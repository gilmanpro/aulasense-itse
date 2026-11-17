#!/usr/bin/env python
"""Dashboard AulaSense — FastAPI en 127.0.0.1:8000 (SPEC §9).

Reglas de seguridad aplicadas aquí (AGENTS.md, sección "Seguridad de la demo"):
- bind por defecto ``127.0.0.1`` y configurable SOLO por ``AULASENSE_HOST``/``AULASENSE_PORT``:
  D-F25-3 reinterpreta el bind — en Windows el default sigue siendo 127.0.0.1 (sin cambio de
  comportamiento); en WSL Debian el default se sobreescribe a ``0.0.0.0`` **VM-local** (NAT sin
  portproxy = inalcanzable desde la LAN) y la superficie expuesta en Windows sigue siendo solo
  ``127.0.0.1`` (portproxy con admin) o ``http://<WSL_IP>:8000`` + ``AULASENSE_ORIGINS_EXTRA``
  exacto como fallback sin admin. La allowlist de Origin NO cambia;
- Origin guard en ``/ws`` y ``/internal/evento``: **Origin ausente = permitir**
  (curl/CLI/pytest no mandan Origin). Si el header viene, su ``host[:puerto]`` debe
  coincidir EXACTAMENTE con {``127.0.0.1:8000``, ``localhost:8000``} o con una entrada
  de ``AULASENSE_ORIGINS_EXTRA`` (coma-separada, host[:puerto], sin wildcards). Un
  host sin puerto usa el puerto por defecto de su esquema (80/443). No se aceptan
  coincidencias por prefijo/sufijo ni cabeceras sin protocolo;
- CSI crudo: ningún endpoint (el contrato §6 no lo expone);
- **F24 — galería de imágenes SOLO loopback (SPEC §9.1):** se SIRVEN fotos de
  evidencia por id de BD (``GET /api/imagenes/{id}/foto``); la ruta se resuelve
  bajo la base de datos y se valida con ``is_relative_to`` (cualquier escape →
  404 sin pista), con ``nosniff`` y ``no-store``. El dashboard NUNCA acepta
  uploads (eso es el receptor LAN :8001 de la ingesta); sin Origin guard extra en
  lectura: el bind 127.0.0.1 ES el control (SPEC §9.1);
- **F25 — login seguro (SPEC §9.2, D-F25-8):** todas las rutas exigen cookie de
  sesión (`aulasense_sesion`, HttpOnly+SameSite=Lax) salvo `GET/POST /login`,
  `POST /logout`, estáticos de login y `/internal/*` (Origin guard vigente). El
  handshake de `/ws` valida la cookie. Lockout progresivo por IP+usuario con
  `429 + Retry-After`; 401 genérico sin pistas. El bind loopback NO cambia:
  defensa en profundidad, no sustitución;
- SQL 100 % parametrizado y ``limite`` siempre clampado.

El writer de producción es la ingesta; este proceso solo lee y, para pruebas,
inyecta filas ``origen='sintetico'`` vía ``POST /internal/evento`` (SPEC §7/C13).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import sys
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterator
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# --- Interfaz compartida con la ingesta (DEV A): no reinventar el contrato -----
SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from ingesta.esquema import (  # noqa: E402
    COLUMNAS_F25_IMAGENES,
    COLUMNAS_F26_IMAGENES,
    COLUMNAS_F26_TELEMETRIA,
    ESQUEMA_SQL,
    EVENTOS_CONFIG,
    validar,
)

# F26-a2: el respaldo de tablas nuevas reutiliza el DDL CANONICO de la ingesta
# (a1 ya cerró su frente; importar en vez de copiar hace el drift imposible).
from ingesta import esquema as _esquema_canon  # noqa: E402

# F25-e (SPEC §9.2): login, sesiones, lockout y bootstrap del admin único.
from dashboard import auth  # noqa: E402

# Logger del proceso: la migración de respaldo NO puede tragarse errores en
# silencio (gate F26-bd MEDIO-3); basta el NOMBRE del error, sin datos.
LOG = logging.getLogger("aulasense.dashboard")

# --- Constantes del contrato ---------------------------------------------------
# D-F25-3 reinterpreta el bind: el default de Windows sigue siendo loopback (comportamiento
# idéntico al de antes); en WSL la unit systemd fija AULASENSE_HOST=0.0.0.0 VM-local (NAT sin
# portproxy ⇒ inalcanzable desde la LAN) y AULASENSE_ORIGINS_EXTRA cubre el fallback.
HOST = os.environ.get("AULASENSE_HOST", "127.0.0.1")
PORT = int(os.environ.get("AULASENSE_PORT", "8000"))
PUERTO_POR_ESQUEMA = {"http": 80, "https": 443}
ALLOWLIST_DEFECTO = frozenset({"127.0.0.1:8000", "localhost:8000"})
TIPOS_EVENTO = (
    "MOVIMIENTO_INICIO",
    "MOVIMIENTO_FIN",
    "CALIBRACION",
    "FALLA",
    "NODO_OFFLINE",
    "NODO_ONLINE",
)
TIPOS_SINTETICOS = ("motion", "health", "fault", "aulasense_temp")
PREFIJO_CONTRATO = "aulasense/v1/devices"  # base topic fijado por SPEC §6
TABLAS_EXPORT = ("evaluaciones", "eventos", "telemetria")
RE_DEVICE = re.compile(r"^[0-9a-f]{16}$")
LIMITE_MAX = 5000
LIMITE_DEFECTO = 200
VENTANA_VIVO_MS = 120_000  # última evaluación <120 s => en_vivo (SPEC §9)
VENTANA_MOTION_MS = 24 * 60 * 60 * 1000
POLL_INGESTA_S = 0.4  # latencia objetivo <2 s global (SPEC §9)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# --- DDL literal de SPEC §8.1 (copia local deliberada) ------------------------
# La ingesta es la dueña del esquema (§8.1); el dashboard solo lo replica como
# migración idempotente de respaldo (tests con DB nueva, DB antigua anterior a
# F24). El SQL se mantiene como constante local para no acoplarnos a los símbolos
# que se editan en paralelo en ``server/ingesta/esquema.py`` (condición de
# carrera); la única pieza IMPORTADA es el enum de eventos de config (ver abajo).
# Enum del CHECK de ``config_eventos.tipo``: se DERIVA del canon
# (``ingesta.esquema.EVENTOS_CONFIG``, fuente unica de R8) para que la copia de
# respaldo no vuelva a divergir. Auditoria externa doble-ciego 2026-11-15 (MEDIO):
# el literal local se quedo sin 'IMAGEN_ON' tras R8, y una DB creada dashboard-first
# (rama ``not existe_img``) rechazaba ese evento hasta que la ingesta reconstruia la
# tabla (migracion R8 de ``esquema.py``). Derivarlo convierte el drift en imposible.
CHECK_CONFIG_TIPOS = "tipo TEXT NOT NULL CHECK(tipo IN ({tipos}))".format(
    tipos=",".join(f"'{tipo}'" for tipo in EVENTOS_CONFIG)
)

IMAGENES_SQL = """
CREATE TABLE IF NOT EXISTS imagenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  ruta TEXT NOT NULL,
  hash_sha256 TEXT NOT NULL,
  tamano_bytes INTEGER NOT NULL,
  ancho INTEGER, alto INTEGER,
  evento_id INTEGER,
  ia_persona INTEGER,
  ia_score REAL,
  imagen_purgada INTEGER NOT NULL DEFAULT 0,
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now')),
  -- F25 (SPEC §8.2): canal de transporte y descripción IA para la búsqueda §9.3.
  canal TEXT NOT NULL DEFAULT 'http' CHECK(canal IN ('http','mqtt')),
  descripcion TEXT,
  etiquetas TEXT,
  vision_estado TEXT NOT NULL DEFAULT 'pendiente'
    CHECK(vision_estado IN ('pendiente','procesando','hecho','error','omitido')),
  vision_modelo TEXT,
  vision_ts_ms INTEGER,
  vision_tokens INTEGER,
  FOREIGN KEY(evento_id) REFERENCES eventos(id)
);
CREATE INDEX IF NOT EXISTS idx_img_dev_ts ON imagenes(device_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_img_evento ON imagenes(evento_id);
CREATE INDEX IF NOT EXISTS idx_img_purgada ON imagenes(imagen_purgada);
CREATE INDEX IF NOT EXISTS idx_img_vision_estado ON imagenes(vision_estado);
CREATE INDEX IF NOT EXISTS idx_img_canal ON imagenes(canal);
CREATE TABLE IF NOT EXISTS config_eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  {check_config_tipos},
  detalle TEXT,
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cfg_dev_ts ON config_eventos(device_id, ts_ms);
""".format(check_config_tipos=CHECK_CONFIG_TIPOS)

# Columnas exactas de metadata de la galería: ``ruta`` NUNCA se expone (SPEC §9.1).
# F25-d3 (SPEC §9.3): se añaden descripcion/etiquetas/canal/vision_estado para la
# búsqueda por texto; la ruta absoluta sigue fuera del contrato.
COLS_IMAGEN_METADATA = (
    "id, device_id, ts_ms, tamano_bytes, ancho, alto, evento_id, "
    "ia_persona, ia_score, imagen_purgada, origen, recibido_en, "
    "descripcion, etiquetas, canal, vision_estado"
)

# Valores del CHECK canónico de ``imagenes`` (server/ingesta/esquema.py, §8.2):
# un valor fuera de estos conjuntos se rechaza con 422 (nunca se concatena al SQL).
CANALES_IMAGEN = ("http", "mqtt")
ESTADOS_VISION = ("pendiente", "procesando", "hecho", "error", "omitido")
# Tope del texto de búsqueda: LIKE sobre descripcion/etiquetas; cortar evita
# patrones gigantes y mantiene el coste acotado en la escala actual (SPEC §9.3).
TOPE_BUSQUEDA = 200

# ------------------------------------------------------------------------------
# F26 (SPEC §16.5-16.7): monitoreo "salud del sistema" + columnas de decisión.
#
# MIGRACIÓN DE RESPALDO (mismo patrón deliberado que IMAGENES_SQL/F25): la DUEÑA
# real del esquema es ``server/ingesta/esquema.py`` (writer único, fase F26-a1);
# esta copia idempotente (IF NOT EXISTS + PRAGMA table_info) permite que el
# dashboard arranque y sirva /api/salud sobre una DB anterior a F26 sin esperar
# a la ingesta (tests, Windows fallback). Si a1 ya migró, todo es no-op.
# ------------------------------------------------------------------------------
# Columnas nuevas de ``imagenes`` (SPEC §16.5, DDL canónico de
# ``ingesta.esquema.COLUMNAS_F26_IMAGENES`` — importado arriba, no copiado).
COLUMNAS_DECISION_PANEL: tuple[str, ...] = (
    "decision_estado",
    "decision_etiqueta",
    "decision_confianza",
    "decision_modelo",
    "decision_ts_ms",
    "decision_actividad",
    "decision_cubre",
    "decision_calidad",
)
# Tablas F26 (SPEC §16.5): auditoría Clef (gemela de vision_uso) + heartbeat.
DDL_CLEF_USO = _esquema_canon._DDL_CLEF_USO
DDL_ESTADO_SISTEMA = _esquema_canon._DDL_ESTADO_SISTEMA

# --- F26 (SPEC §16.7): umbrales de los checks de salud -------------------------
SALUD_INGESTA_VERDE_MS = 90_000          # heartbeat estado_sistema fresco
SALUD_INGESTA_CRITICO_MS = 5 * 60_000
SALUD_TELEM_VERDE_MS = 5 * 60_000        # frescura telemetría (placa)
SALUD_TELEM_WARN_MS = 10 * 60_000
# Verde del check heap: alineado al UMBRAL VIVO del gate de captura F26
# (95/24 KB: sdkconfig:622-623 CONFIG_AULASENSE_IMAGEN_HEAP_MIN_KB/_BLOCK_MIN_KB,
# README del parche §14 "umbrales vivos del gate 95/24 conservados"). La barra
# hardcodeada 120 de la regla 2 (Kconfig default) producia 'warn' espurio con la
# placa sana (103-106/56 KB, hallazgo F26-c3). Override por .env único:
# AULASENSE_HEAP_INT_VERDE_KB / AULASENSE_HEAP_BLOQUE_VERDE_KB (SPEC §16.7).
SALUD_HEAP_VERDE_KB_DEFECTO = 95
SALUD_HEAP_BLOQUE_VERDE_KB_DEFECTO = 24
SALUD_HEAP_CRITICO_KB = 64
SALUD_RSSI_WARN_DBM = -80
SALUD_ERROR_CRITICO_FRAC = 0.20          # error > 20 % del lote procesado
SALUD_CONSUMO_WARN_FRAC = 0.80           # ≥ 80 % del tope -> warn
SALUD_DISCO_WARN_PCT = 85.0
SALUD_DISCO_CRITICO_PCT = 95.0
SALUD_TUNEL_STALE_MS = 10 * 60_000       # tuneles_estado.json viejo -> warn
SALUD_CUBRE_REVISION = 0.7               # decision_cubre < 0.7 -> badge REVISIÓN_HUMANA
ARCHIVO_TUNELES = "tuneles_estado.json"  # hook F26-b: lo escribe el verbo salud
# Tipos de evento config (SPEC §16.4) que alimentan la cámara/gate y el OTA.
CFG_TIPOS_CAMARA = (
    "IMAGEN_INIT",
    "IMAGEN_GATE_OFF",
    "IMAGEN_DESCARTE",
    "CAMARA_ERROR",
    "IMAGEN_ON",
)
CFG_TIPOS_OTA = ("OTA_CHECK", "OTA_INICIO", "OTA_OK", "OTA_ERROR")
# Bordes OTA -> estado aproximado cuando aún no hay retain cacheado (a1).
OTA_BORDE_ESTADO = {
    "OTA_INICIO": "downloading",
    "OTA_OK": "idle",
    "OTA_ERROR": "error",
    "OTA_CHECK": "checking",
}

_NOTA_HONESTA_SALUD = (
    "Check de solo lectura (SPEC §16.7): sin secretos en la respuesta. "
    "'sin-dato' = la pieza F26-a1 aún no desplegada o la fuente está vacía. "
    "La telemetría de la placa se pausa durante OTA: 'OTA en curso' no es caída."
)

# Columnas nuevas de ``imagenes`` (F25, SPEC §8.2): se IMPORTAN del canonico
# ``server/ingesta/esquema.py`` (antes había una copia literal aquí; dedupe R1
# de la auditoría de arquitectura 2026-11-15). Se usan SOLO como migracion de
# respaldo para DB anteriores a F25 (tests con DB nueva y arranque del dashboard
# sin la ingesta); la dueña real del esquema sigue siendo ``server/ingesta/esquema.py``.

# Extension F3 (session logger): DDL local deliberado, mismo patrón que
# IMAGENES_SQL. La dueña real es ``server/ingesta/esquema.py``; esta copia
# permite que el dashboard arranque sobre una DB anterior a F3 sin depender del
# orden de arranque de la ingesta.
SESIONES_SQL = """
CREATE TABLE IF NOT EXISTS sesiones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sesion TEXT NOT NULL UNIQUE,
  inicio_ms INTEGER NOT NULL,
  fin_ms INTEGER,
  estado TEXT NOT NULL DEFAULT 'activa' CHECK(estado IN ('activa','cerrada')),
  device_id TEXT,
  nota TEXT,
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sesiones_inicio ON sesiones(inicio_ms);
CREATE INDEX IF NOT EXISTS idx_sesiones_estado ON sesiones(estado);
"""

# Panel de validación (F3): ventana visible y bucket de ground truth manual.
VENTANA_PANEL_MS = 10 * 60 * 1000
VENTANA_BUCKET_DEFECTO_MS = 30_000
VENTANA_BUCKET_MIN_MS = 5_000
VENTANA_BUCKET_MAX_MS = 120_000
VENTANA_MARCA_MAX_MS = 30 * 60 * 1000
NOTA_VALIDACION_MAX = 200
RE_SESION = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
HUBO_A_VERDAD = {"si": "motion", "no": "idle"}
VERDAD_A_HUBO = {"motion": "si", "idle": "no"}

# --- SQL de export por whitelist (nunca se concatena el nombre de tabla) -------
_SQL_EXPORT: dict[tuple[str, bool], str] = {
    ("evaluaciones", True): "SELECT * FROM evaluaciones WHERE origen = ? ORDER BY id",
    ("evaluaciones", False): "SELECT * FROM evaluaciones ORDER BY id",
    ("eventos", True): "SELECT * FROM eventos WHERE origen = ? ORDER BY id",
    ("eventos", False): "SELECT * FROM eventos ORDER BY id",
    ("telemetria", True): "SELECT * FROM telemetria WHERE origen = ? ORDER BY id",
    ("telemetria", False): "SELECT * FROM telemetria ORDER BY id",
}

_NOTA_HONESTA = (
    "ESPectre detecta movimiento/ocupación dinámica; idle NO significa sala vacía "
    "y no cuenta personas (SPEC §13)."
)


# ------------------------------------------------------------------------------
# Utilidades varias
# ------------------------------------------------------------------------------
def _ahora_ms() -> int:
    """Época del PC en milisegundos (la ingesta mapea el reloj monótono del nodo)."""
    return int(time.time() * 1000)


def _db_path() -> Path:
    """DB compartida por convención env ``AULASENSE_DB`` (default server/datos/aulasense.db)."""
    crudo = os.environ.get("AULASENSE_DB", "").strip()
    if crudo:
        return Path(crudo).expanduser()
    return SERVER_DIR / "datos" / "aulasense.db"


def _datos_dir() -> Path:
    """Base de las rutas relativas de ``imagenes.ruta`` (convención F24-B).

    ``AULASENSE_DATOS_DIR`` si está puesto; si no, el directorio de la DB
    (``AULASENSE_DB`` o el default ``server/datos``). Devuelve siempre una ruta
    resuelta: la validación ``is_relative_to`` de la galería compara absolutas.
    """
    crudo = os.environ.get("AULASENSE_DATOS_DIR", "").strip()
    if crudo:
        return Path(crudo).expanduser().resolve()
    return _db_path().expanduser().resolve().parent


# --- Migraciones de respaldo: una vez por BD, no por request -----------------
#
# Marca gate (rendimiento 2026-11-15 sugerencias §1, extendido en F26 porque la
# migracion de respaldo F26 duplico el trabajo pagado por request): viaja EN la
# propia BD via ``PRAGMA user_version`` (estado compartido, test-safe: cada test
# usa su tmp_path y arranca sin marca). Con la marca puesta, ``_conectar()``
# OMITE los DDL idempotentes de respaldo (PRAGMA table_info + CREATE INDEX
# IF NOT EXISTS + executescript + COMMIT: transacciones de ESCRITURA por cada
# lectura, en competencia con el writer unico de la ingesta sobre WAL).
# La DUEÑA del esquema sigue siendo la ingesta: una BD nueva o sin marca que
# pase por aqui se alinea igual (las migraciones son idempotentes) y sella la
# marca SOLO si la migracion F26 de respaldo reporta exito — si la BD estaba
# bloqueada no se sella y el siguiente request reintenta.
USER_VERSION_RESPALDO = 26  # F25(25) + F26(26): migraciones de respaldo selladas


def _migrar_imagenes_f25(con: sqlite3.Connection) -> bool:
    """Añade a ``imagenes`` las columnas F25 si faltan (idempotente, tolerante).

    SQLite no tiene ``ADD COLUMN IF NOT EXISTS``: se consulta ``pragma table_info``
    antes de cada ``ALTER TABLE`` (mismo patrón que el canónico de
    ``server/ingesta/esquema.py``). Un fallo de ALTER (p. ej. DB bloqueada por otro
    proceso) NO tumba el arranque: la ingesta aplicará la migración canónica.
    Devuelve ``False`` solo cuando hubo error (el gate de ``user_version`` no
    sella la marca en ese caso).
    """
    try:
        existentes = {fila[1] for fila in con.execute("PRAGMA table_info(imagenes)")}
        for nombre, ddl in COLUMNAS_F25_IMAGENES:
            if nombre in existentes:
                continue
            con.execute(f"ALTER TABLE imagenes ADD COLUMN {ddl}")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_img_vision_estado ON imagenes(vision_estado)"
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_img_canal ON imagenes(canal)")
        con.commit()
        return True
    except sqlite3.Error:  # tabla ausente/bloqueada: la ingesta migrará después
        con.rollback()
        return False


def _conectar() -> sqlite3.Connection:
    """Abre la DB en WAL y garantiza el esquema (CREATE IF NOT EXISTS es idempotente).

    Las migraciones de respaldo (F25 visión + F26 decision/estado_sistema/clef_uso)
    corren UNA vez por BD: se saltan cuando ``PRAGMA user_version`` ya trae la
    marca ``USER_VERSION_RESPALDO`` (gate rendimiento F26 — ver comentario arriba).
    """
    ruta = _db_path()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta, timeout=5.0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA journal_mode=WAL")
    existe = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evaluaciones'"
    ).fetchone()
    if not existe:
        con.executescript(ESQUEMA_SQL)
    # Migración F24 idempotente de respaldo (la dueña real del esquema es la ingesta).
    existe_img = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='imagenes'"
    ).fetchone()
    if not existe_img:
        con.executescript(IMAGENES_SQL)
    # Extensión F3 (session logger): misma migración de respaldo.
    existe_ses = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sesiones'"
    ).fetchone()
    if not existe_ses:
        con.executescript(SESIONES_SQL)
    # F26 (SPEC §16.5): respaldo idempotente de columnas decision_*, clef_uso y
    # estado_sistema. La dueña real es la ingesta (F26-a1); si ya migró, no-op.
    # GATE rendimiento: solo hasta la primera pasada por BD (user_version), y la
    # marca NO se sella si el respaldo fallo (el proximo request reintenta).
    if con.execute("PRAGMA user_version").fetchone()[0] < USER_VERSION_RESPALDO:
        if existe_img:
            # DB anterior a F25: se alinean las columnas de visión (CANAL/descripcion/…)
            # antes de cualquier lectura de la galería.
            _migrar_imagenes_f25(con)
        if _migrar_f26_respaldo(con):
            try:
                con.execute(f"PRAGMA user_version={USER_VERSION_RESPALDO}")
                con.commit()
            except sqlite3.Error:  # bloqueada: sin marca, el próximo request reintenta
                try:
                    con.rollback()
                except sqlite3.Error:
                    pass
    return con


def _migrar_f26_respaldo(con: sqlite3.Connection) -> bool:
    """Añade columnas ``decision_*`` + tablas F26 si faltan (idempotente).

    Mismo patrón tolerante que ``_migrar_imagenes_f25``: un fallo (DB bloqueada
    o tabla ausente) NO tumba el arranque: la ingesta aplicará la migración
    canónica y /api/salud degrada cada check a 'sin-dato'. Devuelve ``False``
    solo cuando hubo error: el gate de ``user_version`` no sella la marca y el
    siguiente request reintenta.
    """
    try:
        existe_img = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='imagenes'"
        ).fetchone()
        if existe_img:
            existentes = {fila[1] for fila in con.execute("PRAGMA table_info(imagenes)")}
            for nombre, ddl in COLUMNAS_F26_IMAGENES:
                if nombre in existentes:
                    continue
                con.execute(f"ALTER TABLE imagenes ADD COLUMN {ddl}")
        existe_tel = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telemetria'"
        ).fetchone()
        if existe_tel:
            # heap INTERNO (SPEC §16.5): nulas en filas históricas (backwards-
            # compat exigida por a1: NULL jamás se pinta como 0).
            existentes_tel = {fila[1] for fila in con.execute("PRAGMA table_info(telemetria)")}
            for nombre, ddl in COLUMNAS_F26_TELEMETRIA:
                if nombre in existentes_tel:
                    continue
                con.execute(f"ALTER TABLE telemetria ADD COLUMN {ddl}")
        con.executescript(DDL_ESTADO_SISTEMA)
        if existe_img:
            con.executescript(DDL_CLEF_USO)  # FK a imagenes(id)
        con.commit()
        return True
    except sqlite3.Error as exc:  # tabla ausente/bloqueada: la ingesta migrará después
        # Gate F26-bd MEDIO-3: no se traga el fallo en silencio. Solo el NOMBRE
        # de la excepción (sin datos de la fila ni de la conexión).
        LOG.warning(
            "migración F26 de respaldo omitida (%s): la ingesta aplicará el esquema",
            type(exc).__name__,
        )
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        return False


def _filas(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(fila) for fila in con.execute(sql, params)]


def _clamp_limite(valor: int | None, defecto: int = LIMITE_DEFECTO) -> int:
    """Clampa ``limite`` a 1..5000 (SPEC §9)."""
    if valor is None:
        return defecto
    return max(1, min(LIMITE_MAX, int(valor)))


def _escapar_like(texto: str) -> str:
    """Escapa ``\\``, ``%`` y ``_`` para un LIKE con ``ESCAPE '\\'`` (SPEC §9.3).

    Sin esto, un ``q=%`` del usuario se convertiría en "cualquier cosa" y un
    ``q=a_b`` casaría con ``axb``. El resultado SIEMPRE viaja como parámetro
    (nunca se interpola en el SQL).
    """
    return (
        str(texto)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _validar_device(device_id: str | None) -> str | None:
    """``device_id`` debe ser 16 hex minúscula; si no, 422 (SPEC §6)."""
    if device_id is None or device_id == "":
        return None
    if not RE_DEVICE.match(device_id):
        raise HTTPException(status_code=422, detail="device_id debe ser 16 hex minúscula")
    return device_id


def _prefijo_topic() -> str:
    return os.environ.get("MQTT_TOPIC_PREFIX", "").strip() or "aulasense/v1/devices"


# ------------------------------------------------------------------------------
# Origin guard (AGENTS.md: 403 al origen cruzado, defaults intactos)
# ------------------------------------------------------------------------------
def _allowlist() -> tuple[set[str], set[str]]:
    """Devuelve (host:puerto exactos, hosts sin puerto) desde defaults + env extra."""
    exactos: set[str] = set(ALLOWLIST_DEFECTO)
    sin_puerto: set[str] = set()
    crudo = os.environ.get("AULASENSE_ORIGINS_EXTRA", "")
    for pieza in crudo.split(","):
        entrada = pieza.strip().lower()
        if not entrada:
            continue
        # Solo host[:puerto] exactos: nada de schemes, rutas, wildcards ni comodines.
        if any(marca in entrada for marca in ("*", "/", "://", "?", "#")):
            continue
        if ":" in entrada:
            exactos.add(entrada)
        else:
            sin_puerto.add(entrada)
    return exactos, sin_puerto


def _origen_permitido(header: str | None) -> bool:
    """Valida el header ``Origin`` con la regla "Origin ausente = permitir".

    - Header ausente (o vacío) => permitir: curl/CLI/pytest no lo envían y el bind
      ya está restringido a loopback.
    - Header presente => debe traer protocolo http/https, host y puerto efectivo
      (explícito o default del esquema) que coincida EXACTAMENTE con la allowlist.
    """
    if header is None:
        return True
    valor = header.strip()
    if not valor:
        return True
    try:
        partes = urlsplit(valor)
    except ValueError:
        return False
    if partes.scheme not in PUERTO_POR_ESQUEMA:
        return False
    if partes.username or partes.password:
        return False
    host = (partes.hostname or "").lower()
    if not host:
        return False
    try:
        puerto = partes.port
    except ValueError:  # puerto no numérico/fuera de rango
        return False
    if puerto is None:
        puerto = PUERTO_POR_ESQUEMA[partes.scheme]
    exactos, sin_puerto = _allowlist()
    if f"{host}:{puerto}" in exactos:
        return True
    # Entrada sin puerto: equivale al puerto default del esquema del Origin.
    return host in sin_puerto and puerto == PUERTO_POR_ESQUEMA[partes.scheme]


def _respuesta_403(detalle: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": detalle})


# ------------------------------------------------------------------------------
# Guard de FUENTE para /internal/* (auditoria 2026-11-15)
# ------------------------------------------------------------------------------
# El Origin guard es inoperante contra clientes NO-navegador (curl/CLI: sin header
# => permitir). Con el dashboard publicado por tunel (167.114.169.134:10062) eso
# hacia de ``POST /internal/evento`` un endpoint de ESCRITURA alcanzable sin
# autenticacion desde internet (verificado: 422 del handler sin sesion). Cierre:
# la FUENTE directa (``request.client.host``) debe ser loopback (o el host
# sintactico "testclient" de pytest) o estar en ``AULASENSE_INTERNAL_HOSTS``
# (lista exacta separada por comas; si la variable esta DEFINIDA, SUSTITUYE al
# default, minimo privilegio). El tunel SSH+portproxy llega con el gateway NAT
# (172.26.x.x), NO loopback => queda fuera del default y el endpoint publico
# responde 403. La ingesta real entra por MQTT, no por /internal.
FUENTES_INTERNAS_DEFECTO = frozenset(
    {"127.0.0.1", "::1", "::ffff:127.0.0.1", "testclient"}
)


def _fuente_interna_permitida(ip: str | None) -> bool:
    """True si la IP origen puede usar ``/internal/*`` (ver comentario arriba)."""
    crudo = os.environ.get("AULASENSE_INTERNAL_HOSTS")
    if crudo is None:
        permitidas: set[str] = set(FUENTES_INTERNAS_DEFECTO)
    else:
        permitidas = {pieza.strip().lower() for pieza in crudo.split(",") if pieza.strip()}
    return (ip or "").strip().lower() in permitidas


# ------------------------------------------------------------------------------
# Cabeceras de seguridad GLOBALES (auditoria 2026-11-15)
# ------------------------------------------------------------------------------
# ANTES solo las rutas de auth recibian CABECERAS_AUTH; la pagina autenticada y la
# API se servian sin XFO/nosniff/Referrer-Policy/CSP. Se aplican por setdefault
# (no pisan los valores por-route ya fijados). CSP: la UI usa CDNs (Tailwind JIT
# exige 'unsafe-eval'; el <style> y los style-attrs exigen 'unsafe-inline' solo en
# style-src) y WebSocket al propio host; NADA de 'unsafe-inline' en script-src ni
# de origins ajenos en connect-src.
CSP_DASHBOARD = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.tailwindcss.com https://cdn.jsdelivr.net 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
    "img-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "font-src 'self' data:; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
CABECERAS_GLOBALES = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": CSP_DASHBOARD,
}


# ------------------------------------------------------------------------------
# F25-e — Login seguro del dashboard (SPEC §9.2, D-F25-8)
# Defensa en profundidad: bind loopback intacto + sesión obligatoria en todas
# las rutas salvo /login, /logout, estáticos de login y /internal/* (máquina a
# máquina local, Origin guard vigente). Auth vive en ``dashboard/auth.py``.
# ------------------------------------------------------------------------------
# Rutas exentas de sesión (contrato §9.2). ``/internal/*`` se exime porque lo
# consume la máquina local (tools/CLI) y ya está protegido por Origin guard.
def _ruta_libre(metodo: str, ruta: str) -> bool:
    """¿La ruta no exige sesión? (método exacto, sin comodines de prefijo)."""
    if ruta == "/login" and metodo in ("GET", "POST"):
        return True
    if ruta == "/logout" and metodo == "POST":
        return True
    if ruta.startswith("/static/login"):  # login.html y sus recursos
        return True
    if ruta == "/internal" or ruta.startswith("/internal/"):
        return True
    return False


def _es_ruta_auth(ruta: str) -> bool:
    """Respuestas de auth con cabeceras nosniff/DENY/no-referrer (SPEC §9.2)."""
    return ruta in ("/login", "/logout")


def _respuesta_sin_sesion(ruta: str) -> Response:
    """Sin sesión: 302 al login para las páginas, 401 para API/estáticos."""
    if ruta in ("/", "/index.html", "/salud"):
        return RedirectResponse(url="/login", status_code=302, headers=auth.CABECERAS_AUTH)
    return JSONResponse(
        status_code=401,
        content={"detail": "sesión requerida"},
        headers=auth.CABECERAS_AUTH,
    )


def _ip_cliente(request: Request) -> str:
    """IP del cliente para el lockout (loopback: 127.0.0.1 / ::1 / testclient)."""
    return (request.client.host if request.client else "") or "local"


# Hash señuelo: usuario inexistente y password mala cuestan lo mismo (sin pistas
# de temporización sobre qué usuario existe). Se calcula una vez, por pereza.
_HASH_SENUELO: str | None = None


def _hash_senuelo() -> str:
    global _HASH_SENUELO
    if _HASH_SENUELO is None:
        _HASH_SENUELO = auth.hash_password(secrets.token_hex(16))
    return _HASH_SENUELO


def _abrir_sesion(token: str | None) -> dict[str, Any] | None:
    """Valida la cookie contra la BD (expiración absoluta + idle sliding)."""
    if not token:
        return None
    con = _conectar()
    try:
        return auth.validar_sesion(con, token)
    except sqlite3.Error:  # DB bloqueada: se trata como "sin sesión"
        return None
    finally:
        con.close()


def _intento_login(
    ip: str, usuario: str, password: str, user_agent: str | None
) -> dict[str, Any]:
    """Un intento de login completo (sync: se ejecuta en un hilo aparte).

    Orden: lockout vigente → verificación → registro del intento → sesión.
    401 genérico: el llamador no distingue usuario inexistente de password mala.
    """
    con = _conectar()
    try:
        restante = auth.bloqueo_restante_ms(con, ip, usuario)
        if restante > 0:
            return {"estado": "bloqueado", "restante_ms": restante}
        fila = auth.obtener_usuario(con, usuario)
        # Siempre se verifica un hash real (señuelo si el usuario no existe).
        hash_ref = fila["pass_hash"] if fila else _hash_senuelo()
        ok = auth.verificar_password(password, hash_ref) and fila is not None
        auth.registrar_intento(con, ip, usuario, ok)
        if not ok:
            return {"estado": "fallo"}
        # Éxito: limpieza oportunista (sesiones expiradas + intentos >24 h).
        auth.limpiar_sesiones(con)
        auth.podar_intentos(con)
        token = auth.crear_sesion(con, int(fila["id"]), ip, user_agent)
        return {"estado": "ok", "token": token, "usuario": fila["username"]}
    finally:
        con.close()


def _cerrar_sesion(token: str | None) -> None:
    """Borra la sesión de la BD (logout)."""
    if not token:
        return
    con = _conectar()
    try:
        auth.borrar_sesion(con, token)
    finally:
        con.close()


# ------------------------------------------------------------------------------
# Lecturas para los endpoints
# ------------------------------------------------------------------------------
def _snapshot_dispositivos() -> list[dict[str, Any]]:
    """Snapshot por device: estado, última eval, telemetría, motion/hora y en_vivo."""
    ahora = _ahora_ms()
    con = _conectar()
    try:
        ids: set[str] = set()
        for tabla in ("dispositivos", "evaluaciones", "telemetria"):
            ids.update(
                fila["device_id"] for fila in con.execute(f"SELECT DISTINCT device_id FROM {tabla}")
            )
        salida: list[dict[str, Any]] = []
        for device_id in sorted(ids):
            meta = con.execute(
                "SELECT * FROM dispositivos WHERE device_id = ?", (device_id,)
            ).fetchone()
            ultima = con.execute(
                "SELECT ts_ms, state, score, origen, recibido_en FROM evaluaciones "
                "WHERE device_id = ? ORDER BY ts_ms DESC, id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            telemetria = con.execute(
                "SELECT * FROM telemetria WHERE device_id = ? "
                "ORDER BY ts_ms DESC, id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            ultimo_evento = con.execute(
                "SELECT tipo, ts_ms FROM eventos WHERE device_id = ? "
                "ORDER BY ts_ms DESC, id DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            inicios_24h = con.execute(
                "SELECT COUNT(*) FROM eventos WHERE device_id = ? "
                "AND tipo = 'MOVIMIENTO_INICIO' AND ts_ms >= ?",
                (device_id, ahora - VENTANA_MOTION_MS),
            ).fetchone()[0]
            en_vivo = False
            if ultima is not None:
                edad = ahora - int(ultima["ts_ms"])
                en_vivo = -5000 <= edad <= VENTANA_VIVO_MS
            salida.append(
                {
                    "device_id": device_id,
                    "label": (meta["label"] if meta is not None else None),
                    "firmware": (meta["firmware"] if meta is not None else None),
                    "chip": (meta["chip"] if meta is not None else None),
                    "estado": (ultima["state"] if ultima is not None else None),
                    "score": (ultima["score"] if ultima is not None else None),
                    "ultima_evaluacion_ms": (ultima["ts_ms"] if ultima is not None else None),
                    "ultima_evaluacion_origen": (ultima["origen"] if ultima is not None else None),
                    "ultimo_evento": (dict(ultimo_evento) if ultimo_evento is not None else None),
                    "en_vivo": en_vivo,
                    "motion_inicios_24h": int(inicios_24h or 0),
                    "motion_por_hora": round((inicios_24h or 0) / 24.0, 3),
                    "telemetria": dict(telemetria) if telemetria is not None else None,
                }
            )
        return salida
    finally:
        con.close()


def _mensaje_hello() -> dict[str, Any]:
    """Snapshot inicial del WS: último estado por device + últimas 50 evaluaciones."""
    con = _conectar()
    try:
        ultimo_estado: dict[str, dict[str, Any]] = {}
        for fila in con.execute(
            "SELECT device_id, ts_ms, state, score, origen FROM evaluaciones "
            "ORDER BY id DESC LIMIT 2000"
        ):
            clave = fila["device_id"]
            if clave not in ultimo_estado:  # la primera vista es la más reciente
                ultimo_estado[clave] = dict(fila)
        evaluaciones = _filas(
            con,
            "SELECT * FROM evaluaciones ORDER BY id DESC LIMIT 50",
        )
    finally:
        con.close()
    return {
        "type": "hello",
        "generado_ms": _ahora_ms(),
        "ultimo_estado": ultimo_estado,
        "evaluaciones": evaluaciones,
        "nota": _NOTA_HONESTA,
    }


# ------------------------------------------------------------------------------
# WS: gestor de clientes con cola propia por cliente
# ------------------------------------------------------------------------------
class GestorWS:
    """Colas independientes por cliente; nunca deja que un socket roto tumbe el server."""

    def __init__(self) -> None:
        self._clientes: dict[WebSocket, asyncio.Queue[dict[str, Any]]] = {}

    def registrar(self, ws: WebSocket, tamano_cola: int = 256) -> asyncio.Queue[dict[str, Any]]:
        cola: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=tamano_cola)
        self._clientes[ws] = cola
        return cola

    def quitar(self, ws: WebSocket) -> None:
        self._clientes.pop(ws, None)

    def difundir(self, mensaje: dict[str, Any]) -> int:
        """Encola en cada cliente; si su cola está llena se descarta el mensaje."""
        enviados = 0
        for cola in list(self._clientes.values()):
            try:
                cola.put_nowait(mensaje)
                enviados += 1
            except asyncio.QueueFull:
                continue  # cliente lento: se descarta el mensaje, no se bloquea nada
        return enviados


gestor_ws = GestorWS()


# ------------------------------------------------------------------------------
# Polling de filas nacidas en la ingesta (proceso aparte) → push WS
# ------------------------------------------------------------------------------
def _max_id(tabla: str) -> int:
    con = _conectar()
    try:
        fila = con.execute(f"SELECT COALESCE(MAX(id), 0) AS max_id FROM {tabla}").fetchone()
        return int(fila["max_id"] or 0)
    finally:
        con.close()


def _leer_nuevas(tabla: str, ultimo_id: int, limite: int = 200) -> list[dict[str, Any]]:
    """Filas ``origen='real'`` con id > ultimo_id (las sintéticas ya se difunden al insertarse)."""
    con = _conectar()
    try:
        return _filas(
            con,
            f"SELECT * FROM {tabla} WHERE id > ? AND origen = 'real' ORDER BY id LIMIT {int(limite)}",
            (ultimo_id,),
        )
    finally:
        con.close()


async def _bucle_ingesta() -> None:
    """Polling cada 400 ms de evaluaciones/eventos reales para difundirlos por WS."""
    ultimo_eval = _max_id("evaluaciones")
    ultimo_evento = _max_id("eventos")
    while True:
        await asyncio.sleep(POLL_INGESTA_S)
        try:
            nuevas_eval = await asyncio.to_thread(_leer_nuevas, "evaluaciones", ultimo_eval)
            if nuevas_eval:
                ultimo_eval = max(int(fila["id"]) for fila in nuevas_eval)
                gestor_ws.difundir({"type": "evaluaciones", "filas": nuevas_eval})
            nuevas_evento = await asyncio.to_thread(_leer_nuevas, "eventos", ultimo_evento)
            if nuevas_evento:
                ultimo_evento = max(int(fila["id"]) for fila in nuevas_evento)
                gestor_ws.difundir({"type": "eventos", "filas": nuevas_evento})
        except asyncio.CancelledError:
            raise
        except Exception:  # DB bloqueada/cerrada: se reintenta al siguiente tick
            await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    con = _conectar()  # crea/valida el esquema al arrancar
    try:
        # F25-e: entorno (.env + claves/.env), tablas de auth, limpieza de
        # sesiones expiradas y bootstrap idempotente del admin único.
        auth.preparar(con)
    finally:
        con.close()
    tarea = asyncio.create_task(_bucle_ingesta())
    try:
        yield
    finally:
        tarea.cancel()
        try:
            await tarea
        except asyncio.CancelledError:
            pass


app = FastAPI(title="AulaSense dashboard", version="1.0", lifespan=lifespan)


# ------------------------------------------------------------------------------
# F25-e — Middleware de sesión (SPEC §9.2) y endpoints de login/logout
# ------------------------------------------------------------------------------
@app.middleware("http")
async def guardia_sesion(request: Request, call_next):
    """Exige sesión válida en TODAS las rutas salvo las exentas (§9.2)."""
    ruta = request.url.path
    metodo = request.method.upper()
    if _ruta_libre(metodo, ruta):
        respuesta = await call_next(request)
    else:
        sesion = await asyncio.to_thread(_abrir_sesion, request.cookies.get(auth.NOMBRE_COOKIE))
        if sesion is None:
            return _respuesta_sin_sesion(ruta)
        request.state.sesion = sesion
        respuesta = await call_next(request)
    if _es_ruta_auth(ruta):
        for clave, valor in auth.CABECERAS_AUTH.items():
            respuesta.headers.setdefault(clave, valor)
    # Auditoria 2026-11-15: cabeceras de seguridad en TODAS las respuestas
    # (setdefault: no pisan los valores por-route ya fijados, p. ej. no-store).
    for clave, valor in CABECERAS_GLOBALES.items():
        respuesta.headers.setdefault(clave, valor)
    return respuesta


class Credenciales(BaseModel):
    """Cuerpo del POST /login (JSON; límites para no procesar payloads enormes)."""

    username: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=256)


@app.get("/login", include_in_schema=False)
def login_pagina() -> FileResponse:
    """Página de login text-only (sin datos del sistema, SPEC §9.2)."""
    return FileResponse(
        STATIC_DIR / "login.html", media_type="text/html", headers=auth.CABECERAS_AUTH
    )


@app.post("/login")
async def login_credenciales(credenciales: Credenciales, request: Request) -> Response:
    """Login JSON: 200 + cookie, 401 genérico, 429 + Retry-After si hay lockout."""
    if not _origen_permitido(request.headers.get("origin")):
        return _respuesta_403("Origin no permitido")
    ip = _ip_cliente(request)
    usuario = (credenciales.username or "").strip()
    resultado = await asyncio.to_thread(
        _intento_login,
        ip,
        usuario,
        credenciales.password or "",
        request.headers.get("user-agent"),
    )
    estado = resultado.get("estado")
    if estado == "bloqueado":
        cabeceras = dict(auth.CABECERAS_AUTH)
        cabeceras["Retry-After"] = str(auth.segundos_retry_after(int(resultado["restante_ms"])))
        return JSONResponse(
            status_code=429,
            content={"detail": "demasiados intentos fallidos; reintenta más tarde"},
            headers=cabeceras,
        )
    if estado != "ok":
        # 401 GENÉRICO: mismo cuerpo/tiempo para usuario inexistente y password mala.
        return JSONResponse(
            status_code=401,
            content={"detail": "credenciales inválidas"},
            headers=auth.CABECERAS_AUTH,
        )
    respuesta = JSONResponse(
        status_code=200,
        content={"ok": True, "usuario": resultado["usuario"]},
        headers=auth.CABECERAS_AUTH,
    )
    # Flags exactas de §9.2: HttpOnly, SameSite=Lax, Path=/, Max-Age=43200.
    # ``Secure``: OFF por defecto con JUSTIFICACION — http en loopback (localhost
    # es secure context para el navegador). ACTIVAR con
    # ``AULASENSE_COOKIE_SECURE=1`` cuando se sirva por TLS (auditoria
    # 2026-11-15: la publicacion por tunel sin TLS expone la cookie a robo por
    # MITM en el trayecto publico; el flag sin TLS Romperia el acceso, por eso
    # es opt-in y va junto con el certificado del VPS — accion humana pendiente).
    respuesta.set_cookie(
        key=auth.NOMBRE_COOKIE,
        value=str(resultado["token"]),
        max_age=auth.MAX_EDAD_S,
        path="/",
        httponly=True,
        samesite="lax",
        secure=auth.valor_env("AULASENSE_COOKIE_SECURE", "").strip().lower()
        in ("1", "true", "yes", "on"),
    )
    return respuesta


@app.post("/logout")
async def logout(request: Request) -> Response:
    """Cierra la sesión: borra la fila en BD y la cookie (SPEC §9.2)."""
    if not _origen_permitido(request.headers.get("origin")):
        return _respuesta_403("Origin no permitido")
    await asyncio.to_thread(_cerrar_sesion, request.cookies.get(auth.NOMBRE_COOKIE))
    respuesta = JSONResponse(status_code=200, content={"ok": True}, headers=auth.CABECERAS_AUTH)
    respuesta.delete_cookie(key=auth.NOMBRE_COOKIE, path="/")
    return respuesta


# ------------------------------------------------------------------------------
# Estáticos y raíz
# ------------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def raiz() -> FileResponse:
    """Sirve la única página del dashboard (SPEC §9)."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> JSONResponse:
    """Favicon inexistente a propósito (la galería F24 sirve evidencia, no branding)."""
    return JSONResponse(status_code=404, content={"detail": "sin favicon en AulaSense"})


# ------------------------------------------------------------------------------
# API de lectura
# ------------------------------------------------------------------------------
@app.get("/api/dispositivos")
def api_dispositivos() -> dict[str, Any]:
    return {"ahora_ms": _ahora_ms(), "dispositivos": _snapshot_dispositivos()}


@app.get("/api/evaluaciones")
def api_evaluaciones(
    device_id: str | None = None,
    desde: int | None = None,
    hasta: int | None = None,
    limite: int | None = None,
) -> dict[str, Any]:
    device = _validar_device(device_id)
    lim = _clamp_limite(limite)
    condiciones: list[str] = []
    params: list[Any] = []
    if device:
        condiciones.append("device_id = ?")
        params.append(device)
    if desde is not None:
        condiciones.append("ts_ms >= ?")
        params.append(int(desde))
    if hasta is not None:
        condiciones.append("ts_ms <= ?")
        params.append(int(hasta))
    where = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""
    con = _conectar()
    try:
        filas = _filas(
            con,
            f"SELECT * FROM evaluaciones{where} ORDER BY ts_ms DESC, id DESC LIMIT ?",
            (*params, lim),
        )
    finally:
        con.close()
    return {"limite": lim, "filas": filas}


@app.get("/api/eventos")
def api_eventos(
    device_id: str | None = None,
    tipo: str | None = None,
    limite: int | None = None,
) -> dict[str, Any]:
    device = _validar_device(device_id)
    if tipo is not None and tipo not in TIPOS_EVENTO:
        raise HTTPException(status_code=422, detail="tipo no está en el CHECK del esquema")
    lim = _clamp_limite(limite)
    condiciones: list[str] = []
    params: list[Any] = []
    if device:
        condiciones.append("device_id = ?")
        params.append(device)
    if tipo:
        condiciones.append("tipo = ?")
        params.append(tipo)
    where = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""
    con = _conectar()
    try:
        filas = _filas(
            con,
            f"SELECT * FROM eventos{where} ORDER BY ts_ms DESC, id DESC LIMIT ?",
            (*params, lim),
        )
    finally:
        con.close()
    return {"limite": lim, "filas": filas}


@app.get("/api/resumen")
def api_resumen() -> dict[str, Any]:
    ahora = _ahora_ms()
    con = _conectar()
    try:
        total_eval = int(
            con.execute("SELECT COUNT(*) FROM evaluaciones").fetchone()[0] or 0
        )
        por_estado = {
            fila["state"]: int(fila["n"])
            for fila in con.execute(
                "SELECT state, COUNT(*) AS n FROM evaluaciones GROUP BY state"
            )
        }
        por_origen = {
            fila["origen"]: int(fila["n"])
            for fila in con.execute(
                "SELECT origen, COUNT(*) AS n FROM evaluaciones GROUP BY origen"
            )
        }
        eventos_por_tipo = {
            fila["tipo"]: int(fila["n"])
            for fila in con.execute("SELECT tipo, COUNT(*) AS n FROM eventos GROUP BY tipo")
        }
        dispositivos = int(
            con.execute(
                "SELECT COUNT(DISTINCT device_id) FROM ("
                " SELECT device_id FROM evaluaciones UNION SELECT device_id FROM telemetria"
                " UNION SELECT device_id FROM dispositivos)"
            ).fetchone()[0]
            or 0
        )
        ultima = con.execute(
            "SELECT MAX(ts_ms) FROM ("
            " SELECT MAX(ts_ms) AS ts_ms FROM evaluaciones"
            " UNION ALL SELECT MAX(ts_ms) FROM eventos"
            " UNION ALL SELECT MAX(ts_ms) FROM telemetria)"
        ).fetchone()[0]
    finally:
        con.close()
    motion = por_estado.get("motion", 0)
    return {
        "ahora_ms": ahora,
        "total_evaluaciones": total_eval,
        "evaluaciones_por_estado": por_estado,
        "evaluaciones_por_origen": por_origen,
        "pct_motion": round((100.0 * motion / total_eval), 2) if total_eval else 0.0,
        "eventos_por_tipo": eventos_por_tipo,
        "dispositivos": dispositivos,
        "ultima_actividad_ms": (int(ultima) if ultima is not None else None),
        "nota": _NOTA_HONESTA,
    }


@app.get("/api/telemetria")
def api_telemetria(device_id: str | None = None, limite: int | None = None) -> dict[str, Any]:
    device = _validar_device(device_id)
    lim = _clamp_limite(limite)
    con = _conectar()
    try:
        if device:
            filas = _filas(
                con,
                "SELECT * FROM telemetria WHERE device_id = ? "
                "ORDER BY ts_ms DESC, id DESC LIMIT ?",
                (device, lim),
            )
        else:
            filas = _filas(
                con,
                "SELECT * FROM telemetria ORDER BY ts_ms DESC, id DESC LIMIT ?",
                (lim,),
            )
    finally:
        con.close()
    return {"limite": lim, "filas": filas}


# ------------------------------------------------------------------------------
# F26 (SPEC §16.7) — Salud del sistema: GET /api/salud (tras login, SOLO lectura)
# ------------------------------------------------------------------------------
# Ninguna respuesta de este bloque incluye secretos: los valores que salen son
# numéricos, enums, timestamps y textos de ``config_eventos`` (contrato §6.1:
# detalle ≤120 chars SIN secretos). De ``tuneles_estado.json`` SOLO se re-
# serializan los campos ``id``/``state``/``remote`` truncados — NUNCA el objeto
# completo (lección pass VPS, bitácora 2026-11-15 / PLAN D-F26-8).

# Prioridad agregación de semáforos (peor gana; 'info'/'sin-dato' no suben).
_SEVERIDAD_ESTADO = {"verde": 0, "info": 0, "sin-dato": 1, "warn": 2, "critico": 3}
ESTADOS_DECISION = ESTADOS_VISION  # mismo CHECK de 'pendiente..omitido' (§16.5)


def _tabla_existe(con: sqlite3.Connection, nombre: str) -> bool:
    fila = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (nombre,)
    ).fetchone()
    return fila is not None


def _columnas_de(con: sqlite3.Connection, tabla: str) -> set[str]:
    if not _tabla_existe(con, tabla):
        return set()
    return {str(f[1]) for f in con.execute(f"PRAGMA table_info({tabla})")}


def _check(
    identificador: str,
    etiqueta: str,
    estado: str,
    valor: Any = None,
    detalle: str | None = None,
) -> dict[str, Any]:
    """Un check del semáforo (estado: verde|warn|critico|info|sin-dato)."""
    return {
        "id": identificador,
        "etiqueta": etiqueta,
        "estado": estado,
        "valor": valor,
        "detalle": (detalle or "")[:200] or None,
    }


def _mas_severo(actual: str, nuevo: str) -> str:
    return nuevo if _SEVERIDAD_ESTADO.get(
        nuevo, 0
    ) > _SEVERIDAD_ESTADO.get(actual, 0) else actual


def _env_int(nombre: str, defecto: int) -> int:
    """Entero de entorno tolerante (topes de presupuesto; jamás un secreto)."""
    crudo = (auth.valor_env(nombre, "") or "").strip()
    try:
        return max(0, int(crudo)) if crudo else defecto
    except ValueError:
        return defecto


def _leer_retains(con: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """``estado_sistema.detalle`` JSON ``{sufijo: {device: payload}}`` (a1).

    Contrato real F26-a1: los retains ``ota``/``imagen_estado``/``heap_interno``
    cachean su último valor POR DEVICE en la columna ``detalle`` de la fila
    única ``estado_sistema`` (id=1). Tabla/JSON ausente o inválido -> {} (el
    check degrada a 'sin-dato', nunca inventa estado).
    """
    if not _tabla_existe(con, "estado_sistema"):
        return {}
    try:
        fila = con.execute(
            "SELECT detalle FROM estado_sistema WHERE id = 1"
        ).fetchone()
    except sqlite3.Error:
        return {}
    if fila is None or not fila[0]:
        return {}
    try:
        datos = json.loads(str(fila[0]))
    except ValueError:
        return {}
    if not isinstance(datos, dict):
        return {}
    return {
        str(clave): valor
        for clave, valor in datos.items()
        if isinstance(valor, dict)
    }


def _payload_retain_reciente(
    retains: dict[str, dict[str, Any]], sufijo: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Último payload (por ``ts_ms`` interno del retain) y su device."""
    mejor: dict[str, Any] | None = None
    device: str | None = None
    mejor_ts = -(2**63)
    for ident, payload in (retains.get(sufijo) or {}).items():
        if not isinstance(payload, dict):
            continue
        try:
            ts = int(payload.get("ts_ms") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts >= mejor_ts:
            mejor, device, mejor_ts = payload, str(ident), ts
    return mejor, device


def _estado_ota(con: sqlite3.Connection) -> dict[str, Any]:
    """Estado OTA canónico: retain en ``detalle``; fallback bordes config_eventos.

    Devuelve {estado, en_curso, fuente, device_id, ts_ms}. ``en_curso`` usa el
    valor EXACTO 'downloading' (mismo criterio que ingesta ``_ota_en_descarga``
    y SPEC §16.7): durante la descarga la telemetría se pausa a propósito y la
    frescura NO debe alarma (D-F26-8).
    """
    payload, device = _payload_retain_reciente(_leer_retains(con), "ota")
    if payload is not None:
        estado = str(payload.get("estado") or "desconocido")
        return {
            "estado": estado,
            "en_curso": estado == "downloading",
            "fuente": "estado_sistema.detalle.ota",
            "device_id": device,
            "ts_ms": payload.get("ts_ms"),
            "detalle": (str(payload.get("detalle") or ""))[:120] or None,
        }
    # Fallback por bordes (SPEC §16.1/§16.4: los eventos OTA_* viven en
    # config_eventos, NO en eventos): OTA_INICIO sin OTA_OK/OTA_ERROR después.
    if _tabla_existe(con, "config_eventos"):
        try:
            marcados = ",".join("?" for _ in CFG_TIPOS_OTA)
            fila = con.execute(
                f"SELECT tipo, ts_ms, device_id FROM config_eventos "
                f"WHERE tipo IN ({marcados}) ORDER BY ts_ms DESC, id DESC LIMIT 1",
                tuple(CFG_TIPOS_OTA),
            ).fetchone()
        except sqlite3.Error:
            fila = None
        if fila is not None:
            estado = OTA_BORDE_ESTADO.get(str(fila["tipo"]), "desconocido")
            return {
                "estado": estado,
                "en_curso": estado == "downloading",
                "fuente": "config_eventos",
                "device_id": str(fila["device_id"]),
                "ts_ms": int(fila["ts_ms"]),
                "detalle": f"derivado de {fila['tipo']}",
            }
    return {
        "estado": None,
        "en_curso": False,
        "fuente": "sin-dato",
        "device_id": None,
        "ts_ms": None,
        "detalle": None,
    }


def _conteos_cola(
    con: sqlite3.Connection, columna: str
) -> dict[str, int] | None:
    """Conteos por estado de una columna de ``imagenes`` (None si no existe)."""
    permitidas = {"vision_estado", "decision_estado"}
    if columna not in permitidas:  # defensa: jamás se concatena nombre arbitrario
        raise ValueError(columna)
    if columna not in _columnas_de(con, "imagenes"):
        return None
    conteos = {estado: 0 for estado in ESTADOS_VISION}
    for fila in con.execute(
        f"SELECT {columna} AS estado, COUNT(*) AS n FROM imagenes GROUP BY {columna}"
    ):
        clave = str(fila["estado"])
        conteos[clave] = int(fila["n"])
    return conteos


def _check_cola(con: sqlite3.Connection, conteos: dict[str, int] | None) -> tuple[str, str | None]:
    """(estado, detalle) de una cola: pendiente>0 warn; error>20% lote critico."""
    if conteos is None:
        return "sin-dato", "columna F26 aún no migrada (F26-a1)"
    pendientes = conteos.get("pendiente", 0) + conteos.get("procesando", 0)
    errores = conteos.get("error", 0)
    lote = (
        conteos.get("hecho", 0)
        + errores
        + conteos.get("omitido", 0)
    )
    if lote > 0 and errores / lote > SALUD_ERROR_CRITICO_FRAC:
        return "critico", f"error {errores}/{lote} > 20 % del lote"
    if errores > 0:
        return "warn", f"{errores} en error (reintatables)"
    if pendientes > 0:
        return "warn", f"{pendientes} pendientes"
    return "verde", None


def _lectura_tuneles(ahora: int) -> dict[str, Any]:
    """Lee ``datos/tuneles_estado.json`` (hook F26-b; el dashboard NO ejecuta
    wsl-port). Formas aceptadas: {"ts_ms", "tuneles": [...]} o lista directa.
    SOLO se re-serializan id/state/remote (blanqueo de campos, lección VPS)."""
    ruta = _datos_dir() / ARCHIVO_TUNELES
    base: dict[str, Any] = {
        "fuente": f"datos/{ARCHIVO_TUNELES}",
        "estado": "sin-dato",
        "edad_ms": None,
        "tuneles": [],
        "motivo": None,
    }
    if not ruta.is_file():
        base["motivo"] = (
            "el verbo 'salud' de tools/wsl_aulasense.ps1 (F26-b) aún no publicó "
            "el estado de los túneles"
        )
        return base
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        base["motivo"] = "archivo de túneles ilegible o JSON inválido"
        return base
    if isinstance(datos, dict):
        lista = datos.get("tuneles")
        if not isinstance(lista, list):
            lista = [dict(datos, ts_ms=datos.get("ts_ms"))]
        ts_global = datos.get("ts_ms")
    elif isinstance(datos, list):
        lista = datos
        ts_global = None
    else:
        base["motivo"] = "formato inesperado"
        return base
    tuneles: list[dict[str, Any]] = []
    ts_ms: list[int] = []
    for item in lista:
        if not isinstance(item, dict):
            continue
        # BLANQUEO: solo id/state/remote truncados; nada más sale del archivo.
        tuneles.append(
            {
                "id": str(item.get("id") or "")[:64] or None,
                "state": str(item.get("state") or "")[:32] or None,
                "remote": str(item.get("remote") or "")[:64] or None,
            }
        )
        for fuente in (item.get("ts_ms"), ts_global):
            try:
                if fuente is not None:
                    ts_ms.append(int(fuente))
            except (TypeError, ValueError):
                pass
    base["tuneles"] = tuneles
    if not tuneles:
        base["motivo"] = "archivo sin túneles"
        return base
    if ts_ms:
        base["edad_ms"] = max(0, ahora - max(ts_ms))
    caidos = [t["id"] for t in tuneles if t["state"] != "running"]
    if caidos:
        base["estado"] = "critico"
        base["motivo"] = "state != running: " + ", ".join(str(c) for c in caidos)[:120]
    elif base["edad_ms"] is not None and base["edad_ms"] > SALUD_TUNEL_STALE_MS:
        base["estado"] = "warn"
        base["motivo"] = "estado de túneles stale (>10 min)"
    else:
        base["estado"] = "verde"
    return base


def _lectura_disco() -> dict[str, Any]:
    """``shutil.disk_usage`` de la base de datos (SPEC §16.7: warn ≥85 %)."""
    ruta = _datos_dir()
    try:
        uso = shutil.disk_usage(str(ruta))
    except OSError:
        return {"ruta": str(ruta), "error": "no se pudo medir el disco"}
    pct = round(100.0 * (uso.used / uso.total), 2) if uso.total else None
    return {
        "ruta": str(ruta),
        "total_bytes": int(uso.total),
        "libre_bytes": int(uso.free),
        "usado_pct": pct,
    }


def _uso_ventana(con: sqlite3.Connection, ahora: int) -> dict[str, int] | None:
    """(dia, hora) de llamadas que pudieron consumir Credits en ``vision_uso``.

    Mismo criterio conservador que el worker de visión (contar_uso): filas
    ``hecho``/``error`` cuentan; ``tope`` no (no hubo llamada).
    """
    if not _tabla_existe(con, "vision_uso"):
        return None
    fila_dia = con.execute(
        "SELECT COUNT(*) FROM vision_uso WHERE ts_ms >= ? AND resultado IN ('hecho','error')",
        (ahora - VENTANA_DIA_MS_SALUD,),
    ).fetchone()
    fila_hora = con.execute(
        "SELECT COUNT(*) FROM vision_uso WHERE ts_ms >= ? AND resultado IN ('hecho','error')",
        (ahora - VENTANA_HORA_MS_SALUD,),
    ).fetchone()
    return {"dia": int(fila_dia[0]), "hora": int(fila_hora[0])}


def _uso_clef(con: sqlite3.Connection, ahora: int) -> dict[str, int] | None:
    """(dia, hora) de ``clef_uso`` excluyendo local (0 llamadas) y 'tope'."""
    if not _tabla_existe(con, "clef_uso"):
        return None
    base = (
        "SELECT COUNT(*) FROM clef_uso WHERE ts_ms >= ? "
        "AND COALESCE(modelo, '') <> 'local' AND COALESCE(detalle, '') <> 'tope'"
    )
    dia = con.execute(base, (ahora - VENTANA_DIA_MS_SALUD,)).fetchone()[0]
    hora = con.execute(base, (ahora - VENTANA_HORA_MS_SALUD,)).fetchone()[0]
    return {"dia": int(dia), "hora": int(hora)}


VENTANA_DIA_MS_SALUD = 24 * 60 * 60 * 1000
VENTANA_HORA_MS_SALUD = 60 * 60 * 1000


def _check_consumo(
    usado: dict[str, int] | None, tope_dia: int, tope_hora: int
) -> tuple[str, str | None]:
    if usado is None:
        return "sin-dato", "tabla de auditoría ausente (aún no migrada)"
    dia, hora = usado["dia"], usado["hora"]
    if tope_dia <= 0 or dia >= tope_dia:
        return "critico", f"tope diario alcanzado ({dia}/{tope_dia})"
    if tope_hora > 0 and hora >= tope_hora:
        return "critico", f"tope horario alcanzado ({hora}/{tope_hora})"
    if dia >= SALUD_CONSUMO_WARN_FRAC * tope_dia:
        return "warn", f"{dia}/{tope_dia} del día (≥80 %)"
    return "verde", f"{dia}/{tope_dia} del día"


@app.get("/api/salud")
def api_salud() -> dict[str, Any]:
    """Semáforo de salud del sistema (SPEC §16.7) — SOLO lectura, sin secretos.

    Detrás del login D-F25-8 (el middleware ``guardia_sesion`` lo exige; ruta no
    exenta en ``_ruta_libre``). Cada check degrada a 'sin-dato' si su fuente
    F26-a1/F26-b aún no existe, de modo que el endpoint funciona desde hoy sin
    despliegue. Anti-falsa-alarma OTA (D-F26-8): con el retain ``ota`` en
    'downloading' la frescura de telemetría pasa a 'info' ("OTA en curso").
    """
    ahora = _ahora_ms()
    checks: list[dict[str, Any]] = []
    con = _conectar()
    try:
        # --- heartbeat de la ingesta (estado_sistema, fila única id=1) ------
        fila_estado = None
        if _tabla_existe(con, "estado_sistema"):
            try:
                fila_estado = con.execute(
                    "SELECT ts_ms, mqtt_conectado, ingesta_activa, detalle "
                    "FROM estado_sistema WHERE id = 1"
                ).fetchone()
            except sqlite3.Error:
                fila_estado = None
        if fila_estado is None:
            checks.append(_check(
                "ingesta", "Ingesta activa", "sin-dato", None,
                "sin heartbeat estado_sistema (F26-a1) o la ingesta no ha escrito",
            ))
            checks.append(_check("mqtt", "MQTT conectado", "sin-dato", None,
                                 "fuente: estado_sistema.mqtt_conectado"))
        else:
            edad = max(0, ahora - int(fila_estado["ts_ms"]))
            if edad <= SALUD_INGESTA_VERDE_MS and int(fila_estado["ingesta_activa"] or 0) == 1:
                estado_ing, det = "verde", f"heartbeat hace {edad // 1000} s"
            elif edad <= SALUD_INGESTA_CRITICO_MS:
                estado_ing, det = "warn", f"heartbeat viejo: {edad // 1000} s (>90 s)"
            else:
                estado_ing, det = "critico", f"heartbeat viejo: {edad // 1000} s (>5 min)"
            checks.append(_check("ingesta", "Ingesta activa", estado_ing, edad, det))
            mqtt = fila_estado["mqtt_conectado"]
            if mqtt is None:
                checks.append(_check("mqtt", "MQTT conectado", "sin-dato", None))
            elif int(mqtt) == 1:
                checks.append(_check("mqtt", "MQTT conectado", "verde", 1))
            else:
                checks.append(_check(
                    "mqtt", "MQTT conectado", "critico", 0,
                    "is_connected()=0 en el último heartbeat (la ingesta deriva "
                    "SISTEMA_MQTT_CAIDO en config_eventos)",
                ))

        # --- OTA en curso (informativo; manda sobre la frescura) ------------
        ota = _estado_ota(con)

        # --- frescura de telemetría (≤5 min verde; 5-10 warn; >10 crítico) --
        ultima_ts = None
        if _tabla_existe(con, "telemetria"):
            fila = con.execute("SELECT MAX(ts_ms) FROM telemetria").fetchone()
            ultima_ts = int(fila[0]) if fila and fila[0] is not None else None
        if ultima_ts is None:
            checks.append(_check("telemetria", "Frescura telemetría", "sin-dato", None,
                                 "tabla telemetria vacía"))
        elif ota["en_curso"]:
            checks.append(_check(
                "telemetria", "Frescura telemetría", "info",
                max(0, ahora - ultima_ts),
                "OTA en curso: la pausa de telemetría es esperada (SPEC §16.7), "
                "no es caída",
            ))
        else:
            edad_t = max(0, ahora - ultima_ts)
            if edad_t <= SALUD_TELEM_VERDE_MS:
                est, det = "verde", None
            elif edad_t <= SALUD_TELEM_WARN_MS:
                est, det = "warn", "5-10 min sin telemetría"
            else:
                est, det = "critico", ">10 min sin telemetría (posible NODO_OFFLINE)"
            checks.append(_check("telemetria", "Frescura telemetría", est, edad_t, det))

        # --- placa: heap INTERNO (nunca el de PSRAM) y RSSI ------------------
        telem_cols = _columnas_de(con, "telemetria")
        internal = None
        bloque = None
        placa_ts = None
        placa_device = None
        fuente_heap = "telemetria"
        if "internal_free_kb" in telem_cols and ultima_ts is not None:
            fila = con.execute(
                "SELECT ts_ms, device_id, internal_free_kb, internal_largest_block_kb "
                "FROM telemetria WHERE internal_free_kb IS NOT NULL "
                "ORDER BY ts_ms DESC, id DESC LIMIT 1"
            ).fetchone()
            if fila is not None:
                internal = fila["internal_free_kb"]
                bloque = fila["internal_largest_block_kb"]
                placa_ts = int(fila["ts_ms"])
                placa_device = str(fila["device_id"])
        if internal is None:
            # Fallback: retains cacheados (heap_interno -> imagen_estado), o si
            # tampoco hay datos -> 'sin-dato'. NULL NUNCA se muestra como 0.
            retains = _leer_retains(con)
            payload, device = _payload_retain_reciente(retains, "heap_interno")
            fuente = "heap_interno"
            if payload is None:
                payload, device = _payload_retain_reciente(retains, "imagen_estado")
                fuente = "imagen_estado"
            if isinstance(payload, dict) and payload.get("internal_free_kb") is not None:
                internal = payload.get("internal_free_kb")
                bloque = payload.get("internal_largest_block_kb")
                placa_ts = payload.get("ts_ms")
                placa_device = device
                fuente_heap = f"retain:{fuente}"
        if internal is None:
            checks.append(_check(
                "heap_interno", "Heap interno placa", "sin-dato", None,
                "sin telemetria.internal_free_kb ni retain heap_interno (F26-c1/a1)",
            ))
        else:
            try:
                libre = float(internal)
            except (TypeError, ValueError):
                libre = None
            # UMBRAL VIVO del gate F26 (95/24), configurable por .env único —
            # no la barra histórica de 120 (Kconfig default de la regla 2), que
            # marcaba 'warn' espurio con la placa sana (hallazgo F26-c3).
            verde_kb = _env_int("AULASENSE_HEAP_INT_VERDE_KB", SALUD_HEAP_VERDE_KB_DEFECTO)
            bloque_verde_kb = _env_int(
                "AULASENSE_HEAP_BLOQUE_VERDE_KB", SALUD_HEAP_BLOQUE_VERDE_KB_DEFECTO
            )
            try:
                bloque_v = float(bloque) if bloque is not None else None
            except (TypeError, ValueError):
                bloque_v = None
            if libre is None:
                checks.append(_check("heap_interno", "Heap interno placa", "sin-dato"))
            elif libre < SALUD_HEAP_CRITICO_KB:
                checks.append(_check(
                    "heap_interno", "Heap interno placa", "critico", libre,
                    f"<{SALUD_HEAP_CRITICO_KB} KB: gate de captura F26 con margen mínimo",
                ))
            elif libre >= verde_kb and (bloque_v is None or bloque_v >= bloque_verde_kb):
                checks.append(_check(
                    "heap_interno", "Heap interno placa", "verde", libre,
                    f"umbral vivo gate F26: ≥{verde_kb}/{bloque_verde_kb} KB "
                    "(README parche §14; SPEC §16.7)",
                ))
            elif bloque_v is not None and bloque_v < bloque_verde_kb:
                checks.append(_check(
                    "heap_interno", "Heap interno placa", "warn", libre,
                    f"mayor bloque {bloque_v:.0f} < {bloque_verde_kb} KB (gate F26)",
                ))
            else:
                checks.append(_check(
                    "heap_interno", "Heap interno placa", "warn", libre,
                    f"<{verde_kb} KB: el gate de captura puede desactivarse",
                ))
        # RSSI (última telemetría con valor).
        rssi = None
        if ultima_ts is not None:
            fila = con.execute(
                "SELECT wifi_rssi_dbm FROM telemetria WHERE wifi_rssi_dbm IS NOT NULL "
                "ORDER BY ts_ms DESC, id DESC LIMIT 1"
            ).fetchone()
            if fila is not None:
                rssi = fila["wifi_rssi_dbm"]
        if rssi is None:
            checks.append(_check("rssi", "Wi-Fi RSSI", "sin-dato", None))
        elif int(rssi) >= SALUD_RSSI_WARN_DBM:
            checks.append(_check("rssi", "Wi-Fi RSSI", "verde", int(rssi)))
        else:
            checks.append(_check("rssi", "Wi-Fi RSSI", "warn", int(rssi),
                                 f"< {SALUD_RSSI_WARN_DBM} dBm"))

        # --- colas visión + decisión -----------------------------------------
        conteos_vision = _conteos_cola(con, "vision_estado")
        est, det = _check_cola(con, conteos_vision)
        checks.append(_check("cola_vision", "Cola visión", est, conteos_vision, det))
        conteos_decision = _conteos_cola(con, "decision_estado")
        est_d, det_d = _check_cola(con, conteos_decision)
        checks.append(_check("cola_decision", "Cola decisión (Clef)", est_d,
                             conteos_decision, det_d))

        # --- consumo del día (ventanas móviles) ------------------------------
        tope_vision_dia = _env_int("AULASENSE_VISION_MAX_DIA", 120)
        tope_vision_hora = _env_int("AULASENSE_VISION_MAX_HORA", 20)
        usado_vision = _uso_ventana(con, ahora)
        est_c, det_c = _check_consumo(usado_vision, tope_vision_dia, tope_vision_hora)
        checks.append(_check("consumo_vision", "Consumo visión (día)", est_c,
                             usado_vision, det_c))
        tope_clef_dia = _env_int("AULASENSE_CLEF_MAX_DIA", 120)
        tope_clef_hora = _env_int("AULASENSE_CLEF_MAX_HORA", 20)
        usado_clef = _uso_clef(con, ahora)
        est_k, det_k = _check_consumo(usado_clef, tope_clef_dia, tope_clef_hora)
        checks.append(_check("consumo_clef", "Consumo Clef (día)", est_k, usado_clef,
                             det_k))

        # --- disco -------------------------------------------------------------
        disco = _lectura_disco()
        pct = disco.get("usado_pct")
        if pct is None:
            checks.append(_check("disco", "Disco", "sin-dato", disco,
                                 str(disco.get("error") or "sin medición")))
        elif pct >= SALUD_DISCO_CRITICO_PCT:
            checks.append(_check("disco", "Disco", "critico", pct,
                                 f"≥{SALUD_DISCO_CRITICO_PCT:.0f} % usado"))
        elif pct >= SALUD_DISCO_WARN_PCT:
            checks.append(_check("disco", "Disco", "warn", pct,
                                 f"≥{SALUD_DISCO_WARN_PCT:.0f} % usado "
                                 "(la ingesta deriva SISTEMA_DISCO_WARN)"))
        else:
            checks.append(_check("disco", "Disco", "verde", pct))

        # --- túneles (archivo publicado por el verbo salud de F26-b) ----------
        tuneles = _lectura_tuneles(ahora)
        checks.append(_check("tuneles", "Túneles WSL→VPS", tuneles["estado"],
                             tuneles.get("tuneles") or None, tuneles.get("motivo")))

        # --- OTA (informativo; SUPRIME la alarma de frescura si downloading) --
        checks.append(_check(
            "ota", "OTA", "info" if ota["estado"] else "sin-dato", ota["estado"],
            (
                "OTA en curso: agujero de telemetría esperado (§16.7)"
                if ota["en_curso"]
                else f"fuente: {ota['fuente']}"
            ),
        ))

        # --- tarjetas del panel ------------------------------------------------
        retains = _leer_retains(con)
        payload_ie, device_ie = _payload_retain_reciente(retains, "imagen_estado")
        eventos_camara: list[dict[str, Any]] = []
        if _tabla_existe(con, "config_eventos"):
            try:
                marcas = ",".join("?" for _ in CFG_TIPOS_CAMARA)
                eventos_camara = _filas(
                    con,
                    "SELECT ts_ms, device_id, tipo, detalle FROM config_eventos "
                    f"WHERE tipo IN ({marcas}) "
                    "ORDER BY ts_ms DESC, id DESC LIMIT 10",
                    tuple(CFG_TIPOS_CAMARA),
                )
            except sqlite3.Error:
                eventos_camara = []
        camara = {
            # EXPLÍCITAMENTE config_eventos, NUNCA la tabla eventos (errata R-F26-14).
            "fuente": (
                "imagen_estado" if payload_ie is not None
                else ("config_eventos" if eventos_camara else "sin-dato")
            ),
            "device_id": device_ie,
            "estado": payload_ie,
            "eventos_recentes": eventos_camara,
        }

        decision_aula: dict[str, Any] = {"disponible": False}
        if conteos_decision is not None:
            por_etiqueta = {
                str(f["decision_etiqueta"]): int(f["n"])
                for f in con.execute(
                    "SELECT decision_etiqueta, COUNT(*) AS n FROM imagenes "
                    "WHERE decision_estado = 'hecho' AND decision_etiqueta IS NOT NULL "
                    "GROUP BY decision_etiqueta"
                )
            }
            revision = int(
                con.execute(
                    "SELECT COUNT(*) FROM imagenes WHERE decision_estado = 'hecho' "
                    "AND decision_cubre IS NOT NULL AND decision_cubre < ?",
                    (SALUD_CUBRE_REVISION,),
                ).fetchone()[0]
                or 0
            )
            recientes = [
                {
                    "imagen_id": int(f["id"]),
                    "decision_etiqueta": f["decision_etiqueta"],
                    "decision_confianza": f["decision_confianza"],
                    "decision_actividad": f["decision_actividad"],
                    "decision_cubre": f["decision_cubre"],
                    "decision_modelo": f["decision_modelo"],
                    "ts_ms": f["decision_ts_ms"],
                    # badge derivado (SPEC §16.5): cubre < 0.7, NO un estado nuevo
                    "revision": (
                        f["decision_cubre"] is not None
                        and float(f["decision_cubre"]) < SALUD_CUBRE_REVISION
                    ),
                }
                for f in con.execute(
                    "SELECT id, decision_etiqueta, decision_confianza, "
                    "decision_actividad, decision_cubre, decision_modelo, "
                    "decision_ts_ms FROM imagenes WHERE decision_estado = 'hecho' "
                    "ORDER BY decision_ts_ms DESC, id DESC LIMIT 8"
                )
            ]
            decision_aula = {
                "disponible": True,
                "total_imagenes": sum(conteos_decision.values()),
                "por_estado": conteos_decision,
                "por_etiqueta": por_etiqueta,
                "revision_humana": revision,
                "recientes": recientes,
            }

        fila_psram = None
        if ultima_ts is not None:
            fila_psram = con.execute(
                "SELECT device_id, ts_ms, free_memory_kb, minimum_free_memory_kb, "
                "wifi_rssi_dbm FROM telemetria ORDER BY ts_ms DESC, id DESC LIMIT 1"
            ).fetchone()
        placa = {
            "fuente": fuente_heap if internal is not None else "sin-dato",
            "device_id": placa_device or (
                str(fila_psram["device_id"]) if fila_psram is not None else None
            ),
            "ts_ms": placa_ts if placa_ts is not None else (
                int(fila_psram["ts_ms"]) if fila_psram is not None else None
            ),
            "internal_free_kb": internal,
            "internal_largest_block_kb": bloque,
            "free_memory_kb": (
                fila_psram["free_memory_kb"] if fila_psram is not None else None
            ),
            "wifi_rssi_dbm": rssi,
            "free_memory_nota": (
                "free_memory_kb es PSRAM-inclusive (diagnósticos): NO confundir "
                "con internal_free_kb (SPEC §16.3)"
            ),
        }

        ultima_actividad = con.execute(
            "SELECT MAX(ts_ms) FROM ("
            " SELECT MAX(ts_ms) AS ts_ms FROM evaluaciones"
            " UNION ALL SELECT MAX(ts_ms) FROM eventos"
            " UNION ALL SELECT MAX(ts_ms) FROM telemetria)"
        ).fetchone()[0]
    finally:
        con.close()

    estado_global = "verde"
    for chk in checks:
        estado_global = _mas_severo(estado_global, chk["estado"])
    return {
        "ahora_ms": ahora,
        "estado_global": estado_global,
        "checks": checks,
        "ultima_actividad_ms": (
            int(ultima_actividad) if ultima_actividad is not None else None
        ),
        "ota_en_curso": bool(ota["en_curso"]),
        "camara": camara,
        "decision_aula": decision_aula,
        "placa": placa,
        "colas": {"vision": conteos_vision, "decision": conteos_decision},
        "consumo": {
            "vision": usado_vision,
            "clef": usado_clef,
            "topes": {
                "vision_dia": tope_vision_dia,
                "vision_hora": tope_vision_hora,
                "clef_dia": tope_clef_dia,
                "clef_hora": tope_clef_hora,
            },
        },
        "disco": disco,
        "tuneles": tuneles,
        "nota": _NOTA_HONESTA_SALUD,
    }


@app.get("/salud", include_in_schema=False)
def salud_pagina() -> FileResponse:
    """Panel /salud (estático bajo sesión; SPEC §16.7). Lee /api/salud."""
    return FileResponse(STATIC_DIR / "salud.html", media_type="text/html")


# --- Panel de validación F3: sesiones + ventanas de ground truth manual -----


def _registro_sesiones(con: sqlite3.Connection, limite: int = 100) -> list[dict[str, Any]]:
    """Sesiones registradas por ``tools/session_logger.py`` (más recientes primero)."""
    lim = max(1, min(500, int(limite)))
    return _filas(
        con,
        "SELECT * FROM sesiones ORDER BY inicio_ms DESC, id DESC LIMIT ?",
        (lim,),
    )


def _sesion_activa(con: sqlite3.Connection) -> dict[str, Any] | None:
    fila = con.execute(
        "SELECT * FROM sesiones WHERE estado = 'activa' "
        "ORDER BY inicio_ms DESC, id DESC LIMIT 1"
    ).fetchone()
    return dict(fila) if fila is not None else None


def _ventanas_panel(
    con: sqlite3.Connection, bucket_ms: int, sesion: str | None
) -> list[dict[str, Any]]:
    """Buckets de los últimos 10 min con evaluaciones REALES + su marca manual.

    Se ancla en ``recibido_en`` (hora de llegada a la ingesta) y NO en ``ts_ms``:
    el reloj monótono del nodo puede venir desfasado si el ``health`` retenido es
    viejo (R3), y eso dejaría la ventana "en el futuro" e inutilizable para el
    operador. ``idle`` ≠ sala vacía (regla 11): la ventana predice el estado
    dominante y solo la verdad terreno manual decide si hubo movimiento real.
    """
    ahora = _ahora_ms()
    filas = con.execute(
        "SELECT ts_ms, state, score, strftime('%s', recibido_en) * 1000 AS recibido_ms "
        "FROM evaluaciones "
        "WHERE origen = 'real' AND recibido_en >= datetime('now', '-10 minutes')"
    ).fetchall()
    acumulado: dict[int, dict[str, Any]] = {}
    for fila in filas:
        recibido = fila["recibido_ms"]
        if recibido is None:
            continue
        inicio = (int(recibido) // int(bucket_ms)) * int(bucket_ms)
        caja = acumulado.setdefault(
            inicio, {"n": 0, "n_motion": 0, "suma_score": 0.0, "n_score": 0}
        )
        caja["n"] += 1
        if fila["state"] == "motion":
            caja["n_motion"] += 1
        if fila["score"] is not None:
            caja["suma_score"] += float(fila["score"])
            caja["n_score"] += 1

    marcas = con.execute(
        "SELECT * FROM validacion_sesiones "
        "WHERE inicio_ms <= ? AND fin_ms >= ? ORDER BY id DESC",
        (ahora, ahora - VENTANA_PANEL_MS),
    ).fetchall()
    indice: dict[tuple[int, int], dict[str, Any]] = {}
    for marca in marcas:
        if sesion and marca["sesion"] != sesion:
            continue
        clave = (int(marca["inicio_ms"]), int(marca["fin_ms"]))
        indice.setdefault(clave, dict(marca))  # id DESC: la marca más nueva manda

    activa = _sesion_activa(con)
    etiqueta_defecto = activa["sesion"] if activa is not None else "manual"
    salida: list[dict[str, Any]] = []
    for inicio in sorted(acumulado):
        caja = acumulado[inicio]
        fin = inicio + int(bucket_ms)
        n = int(caja["n"])
        n_motion = int(caja["n_motion"])
        n_idle = n - n_motion
        marca = indice.get((inicio, fin))
        verdad = marca["verdad_terreno"] if marca is not None else None
        salida.append(
            {
                "inicio_ms": inicio,
                "fin_ms": fin,
                "n_evaluaciones": n,
                "n_motion": n_motion,
                "n_idle": n_idle,
                "score_medio": (
                    round(caja["suma_score"] / caja["n_score"], 4)
                    if caja["n_score"]
                    else None
                ),
                "predicho": "motion" if n_motion >= n_idle else "idle",
                "sesion": (marca["sesion"] if marca is not None else etiqueta_defecto),
                "hubo_movimiento_real": (
                    VERDAD_A_HUBO.get(verdad, "sin-dato") if verdad else "sin-dato"
                ),
                "nota": (marca["notas"] if marca is not None else None),
                "marca_id": (int(marca["id"]) if marca is not None else None),
            }
        )
    return salida


@app.get("/api/validacion")
def api_validacion(
    sesion: str | None = None, ventana_s: int | None = None
) -> dict[str, Any]:
    """Matriz de confusión + sesiones registradas + ventanas marcables (F3).

    ``validacion_sesiones`` no tiene device_id (SPEC §8), así que la matriz se
    cruza solo por ventana temporal; con varias placas el resultado es agregado.
    Puede salir vacía. El panel añade los buckets de los últimos 10 min con
    evaluaciones ``origen='real'`` y la marca manual de cada ventana.
    """
    bucket_ms = VENTANA_BUCKET_DEFECTO_MS
    if ventana_s is not None:
        bucket_ms = max(
            VENTANA_BUCKET_MIN_MS,
            min(VENTANA_BUCKET_MAX_MS, int(ventana_s) * 1000),
        )
    con = _conectar()
    try:
        if sesion:
            filas_sesiones = _filas(
                con,
                "SELECT * FROM validacion_sesiones WHERE sesion = ? ORDER BY inicio_ms",
                (sesion,),
            )
        else:
            filas_sesiones = _filas(
                con, "SELECT * FROM validacion_sesiones ORDER BY inicio_ms"
            )
        detalle: list[dict[str, Any]] = []
        matriz = Counter({"vp": 0, "fp": 0, "vn": 0, "fn": 0})
        for fila in filas_sesiones:
            # Cuenta por ts del nodo O por llegada a la ingesta: si el health
            # retenido desfasa R3, la marca manual (hora de pared) sigue siendo
            # evaluable. La fila se cuenta una sola vez por el OR del WHERE.
            conteo = {
                r["state"]: int(r["n"])
                for r in con.execute(
                    "SELECT state, COUNT(*) AS n FROM evaluaciones "
                    "WHERE (ts_ms BETWEEN ? AND ?) "
                    "   OR (recibido_en BETWEEN datetime(? / 1000, 'unixepoch') "
                    "                        AND datetime(? / 1000, 'unixepoch')) "
                    "GROUP BY state",
                    (
                        int(fila["inicio_ms"]),
                        int(fila["fin_ms"]),
                        int(fila["inicio_ms"]),
                        int(fila["fin_ms"]),
                    ),
                )
            }
            n_motion = conteo.get("motion", 0)
            n_idle = conteo.get("idle", 0)
            n_total = n_motion + n_idle
            if n_total == 0:
                predicho = None
            elif n_motion >= n_idle:
                predicho = "motion"
            else:
                predicho = "idle"
            verdad = fila["verdad_terreno"]
            if predicho is None:
                celda = None
            elif verdad == "motion" and predicho == "motion":
                celda = "vp"
            elif verdad == "motion" and predicho == "idle":
                celda = "fn"
            elif verdad == "idle" and predicho == "motion":
                celda = "fp"
            else:
                celda = "vn"
            if celda:
                matriz[celda] += 1
            detalle.append(
                {
                    "id": fila["id"],
                    "sesion": fila["sesion"],
                    "inicio_ms": fila["inicio_ms"],
                    "fin_ms": fila["fin_ms"],
                    "verdad_terreno": verdad,
                    "predicho": predicho,
                    "evaluaciones": n_total,
                    "celda": celda,
                    "notas": fila["notas"],
                }
            )
        registro = _registro_sesiones(con)
        activa = _sesion_activa(con)
        ventanas = _ventanas_panel(con, bucket_ms, sesion)
    finally:
        con.close()
    positivos = matriz["vp"] + matriz["fn"]
    negativos = matriz["vn"] + matriz["fp"]
    return {
        "sesiones": detalle,
        "matriz": {
            "verdaderos_positivos": matriz["vp"],
            "falsos_positivos": matriz["fp"],
            "verdaderos_negativos": matriz["vn"],
            "falsos_negativos": matriz["fn"],
            "recall": (round(matriz["vp"] / positivos, 4) if positivos else None),
            "fpr": (round(matriz["fp"] / negativos, 4) if negativos else None),
            "n_sesiones": len(detalle),
        },
        "registro_sesiones": registro,
        "sesion_activa": activa,
        "ventanas": ventanas,
        "ventana_ms": bucket_ms,
        "panel_ms": VENTANA_PANEL_MS,
        "base_tiempo": "recepcion",
        "nota": (
            "Matriz básica sobre sesiones de verdad terreno manual; "
            f"tamaño muestral N={len(detalle)} sesiones. {_NOTA_HONESTA}"
        ),
    }


@app.get("/api/sesiones")
def api_sesiones() -> dict[str, Any]:
    """Sesiones del session logger (tabla ``sesiones``) y cuál está activa."""
    con = _conectar()
    try:
        filas = _registro_sesiones(con)
        activa = _sesion_activa(con)
    finally:
        con.close()
    return {"ahora_ms": _ahora_ms(), "activa": activa, "sesiones": filas}


class MarcaValidacion(BaseModel):
    """Ground truth manual de una ventana (panel Validación, F3)."""

    sesion: str = Field(min_length=1, max_length=32)
    inicio_ms: int
    fin_ms: int
    hubo_movimiento_real: str
    nota: str | None = Field(default=None, max_length=NOTA_VALIDACION_MAX)


def _marcar_validacion(
    sesion: str,
    inicio_ms: int,
    fin_ms: int,
    hubo_movimiento_real: str,
    nota: str | None,
) -> dict[str, Any]:
    """Persiste/reemplaza la marca de una ventana en ``validacion_sesiones`` (§8).

    ``sin-dato`` no es una verdad terreno: borra la marca previa de esa misma
    ventana (la tabla solo admite motion/idle). Nunca se duplica una marca:
    DELETE + INSERT dentro de la misma transacción.
    """
    verdad = HUBO_A_VERDAD.get(hubo_movimiento_real)
    con = _conectar()
    try:
        with con:
            borrados = con.execute(
                "DELETE FROM validacion_sesiones "
                "WHERE sesion = ? AND inicio_ms = ? AND fin_ms = ?",
                (sesion, int(inicio_ms), int(fin_ms)),
            ).rowcount
            if verdad is None:
                return {
                    "persistido": False,
                    "verdad_terreno": None,
                    "marcas_borradas": int(borrados),
                    "marca": None,
                }
            cursor = con.execute(
                "INSERT INTO validacion_sesiones "
                "(sesion, inicio_ms, fin_ms, verdad_terreno, fuente, notas) "
                "VALUES (?, ?, ?, ?, 'manual_operador', ?)",
                (sesion, int(inicio_ms), int(fin_ms), verdad, nota),
            )
            fila = con.execute(
                "SELECT * FROM validacion_sesiones WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return {
            "persistido": True,
            "verdad_terreno": verdad,
            "marcas_borradas": int(borrados),
            "marca": dict(fila) if fila is not None else None,
        }
    finally:
        con.close()


@app.post("/api/validacion")
async def api_validacion_marcar(
    marca: MarcaValidacion, request: Request
) -> JSONResponse:
    """Marca la verdad terreno de una ventana (Origin guard como ``/internal/*``).

    201 si persistió (si/no), 200 si ``sin-dato`` (desmarca). 403 al Origin
    cruzado, 422 a ids/rangos fuera de contrato.
    """
    if not _origen_permitido(request.headers.get("origin")):
        return _respuesta_403("Origin no permitido")
    if RE_SESION.fullmatch(marca.sesion) is None:
        raise HTTPException(
            status_code=422, detail="sesion debe ser 1-32 caracteres [A-Za-z0-9._-]"
        )
    if marca.hubo_movimiento_real not in ("si", "no", "sin-dato"):
        raise HTTPException(
            status_code=422, detail="hubo_movimiento_real debe ser si|no|sin-dato"
        )
    inicio = int(marca.inicio_ms)
    fin = int(marca.fin_ms)
    if inicio < 0 or fin <= inicio:
        raise HTTPException(status_code=422, detail="rango de ventana inválido")
    if fin - inicio > VENTANA_MARCA_MAX_MS:
        raise HTTPException(
            status_code=422, detail="la ventana supera 30 min (marca tramos más cortos)"
        )
    nota = (marca.nota or "").strip() or None
    resultado = await asyncio.to_thread(
        _marcar_validacion,
        marca.sesion,
        inicio,
        fin,
        marca.hubo_movimiento_real,
        nota,
    )
    return JSONResponse(
        status_code=201 if resultado["persistido"] else 200,
        content={
            "ok": True,
            "sesion": marca.sesion,
            "inicio_ms": inicio,
            "fin_ms": fin,
            "hubo_movimiento_real": marca.hubo_movimiento_real,
            **resultado,
        },
    )


def _generar_csv_validacion(sesion: str | None) -> Iterator[str]:
    """CSV de marcas manuales (patrón F4 ``validacion_sesionN.csv``), streaming."""
    con = _conectar()
    try:
        sql = (
            "SELECT sesion, inicio_ms, fin_ms, verdad_terreno, fuente, notas, recibido_en "
            "FROM validacion_sesiones"
        )
        params: tuple[Any, ...] = ()
        if sesion:
            sql += " WHERE sesion = ?"
            params = (sesion,)
        sql += " ORDER BY inicio_ms, id"
        cursor = con.execute(sql, params)
        buffer = io.StringIO()
        escritor = csv.writer(buffer, dialect="excel", lineterminator="\r\n")
        escritor.writerow(
            [
                "sesion",
                "inicio_ms",
                "fin_ms",
                "verdad_terreno",
                "hubo_movimiento_real",
                "fuente",
                "notas",
                "recibido_en",
            ]
        )
        yield buffer.getvalue()
        for fila in cursor:
            buffer.seek(0)
            buffer.truncate(0)
            escritor.writerow(
                [
                    _celda_csv(fila["sesion"]),
                    _celda_csv(fila["inicio_ms"]),
                    _celda_csv(fila["fin_ms"]),
                    _celda_csv(fila["verdad_terreno"]),
                    _celda_csv(VERDAD_A_HUBO.get(fila["verdad_terreno"], "sin-dato")),
                    _celda_csv(fila["fuente"]),
                    _celda_csv(fila["notas"]),
                    _celda_csv(fila["recibido_en"]),
                ]
            )
            yield buffer.getvalue()
    finally:
        con.close()


@app.get("/api/validacion/export")
def api_validacion_export(sesion: str | None = None) -> StreamingResponse:
    """Exporta las marcas de validación a CSV (todas o una sesión concreta)."""
    if sesion is not None and sesion != "":
        if RE_SESION.fullmatch(sesion) is None:
            raise HTTPException(
                status_code=422, detail="sesion debe ser 1-32 caracteres [A-Za-z0-9._-]"
            )
        nombre = f"validacion_{sesion}.csv"
        filtro = sesion
    else:
        nombre = "validacion_todas.csv"
        filtro = None
    return StreamingResponse(
        _generar_csv_validacion(filtro),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/fuentes_externas")
def api_fuentes_externas(fuente: str | None = None, limite: int | None = None) -> dict[str, Any]:
    lim = _clamp_limite(limite)
    filtro = (fuente or "").strip()[:64]
    con = _conectar()
    try:
        if filtro:
            filas = _filas(
                con,
                "SELECT * FROM fuentes_externas WHERE fuente = ? "
                "ORDER BY ts_ms DESC, id DESC LIMIT ?",
                (filtro, lim),
            )
        else:
            filas = _filas(
                con,
                "SELECT * FROM fuentes_externas ORDER BY ts_ms DESC, id DESC LIMIT ?",
                (lim,),
            )
    finally:
        con.close()
    return {"limite": lim, "fuente": filtro or None, "filas": filas}


# ------------------------------------------------------------------------------
# API de galería F24 (SPEC §9.1) — SOLO loopback; el dashboard NUNCA acepta uploads
# ------------------------------------------------------------------------------
@app.get("/api/imagenes")
def api_imagenes(
    device_id: str | None = None,
    desde: int | None = None,
    hasta: int | None = None,
    limite: int | None = None,
    q: str | None = None,
    texto: str | None = None,
    canal: str | None = None,
    vision_estado: str | None = None,
    decision_estado: str | None = None,
    decision: int | None = None,
    con_ia: int | None = None,
) -> dict[str, Any]:
    """Metadata paginada de imágenes de evidencia con búsqueda por descripción.

    Devuelve solo columnas de metadata (``COLS_IMAGEN_METADATA``); la ruta del
    archivo NUNCA sale por la API (SPEC §9.1). Filtros (SPEC §9.3, F25-d3):

    - ``q``: texto libre buscado con LIKE parametrizado sobre ``descripcion`` y
      ``etiquetas`` (case-insensitive ASCII, ver nota de escala), escapando ``%``,
      ``_`` y ``\\`` del input con ``ESCAPE '\\'``;
    - ``texto``: ALIAS de ``q`` (el dispatcher pidió ``texto=`` y SPEC §9.3 fija
      ``q=``): si llegan ambos, gana ``q`` cuando no está vacío. Un único término
      de búsqueda, nunca se combinan;
    - ``canal``: ``http``|``mqtt`` (fuera del CHECK → 422, igual que ``tipo`` en
      ``/api/eventos``; NO se ignora en silencio para no devolver datos falsos);
    - ``vision_estado``: ``pendiente``|``procesando``|``hecho``|``error``|
      ``omitido`` (fuera del CHECK → 422);
    - ``con_ia=1``: solo imágenes ya descritas (``vision_estado='hecho'`` y
      ``descripcion`` no vacía); ``con_ia=0`` equivale a omitirlo.

    ``limite`` sigue clampado 1..5000. Sin sesión la petición la corta el
    middleware (§9.2) con 401: aquí no se reimplementa auth.

    TODO(FTS5, SPEC §9.3): LIKE es suficiente a la escala actual (cientos de
    imágenes, latencia de ms). Solo si se superan ~5000 imágenes o el LIKE pasa
    de 100 ms se migrará a FTS5 (tabla virtual + triggers de sincronización).
    """
    device = _validar_device(device_id)
    lim = _clamp_limite(limite)

    # Alias q/texto: un único término; se recorta y acota antes de escapar.
    busqueda = (q if (q or "").strip() else texto) or ""
    busqueda = busqueda.strip()[:TOPE_BUSQUEDA]

    canal_filtro: str | None = None
    if canal is not None and canal.strip() != "":
        canal_filtro = canal.strip().lower()
        if canal_filtro not in CANALES_IMAGEN:
            raise HTTPException(status_code=422, detail="canal debe ser http o mqtt")
    estado_filtro: str | None = None
    if vision_estado is not None and vision_estado.strip() != "":
        estado_filtro = vision_estado.strip().lower()
        if estado_filtro not in ESTADOS_VISION:
            raise HTTPException(
                status_code=422, detail="vision_estado no está en el CHECK del esquema"
            )
    con_ia_activo = con_ia is not None and int(con_ia) != 0
    # F26 (SPEC §16.6): la decisión de aula acompaña a la metadata de galería
    # de forma OPT-IN (``decision=1``) para no romper el contrato F25/F24 que
    # fijan los tests previos; el filtro ``decision_estado`` también las trae.
    decision_activo = decision is not None and int(decision) != 0
    decision_filtro: str | None = None
    if decision_estado is not None and decision_estado.strip() != "":
        decision_filtro = decision_estado.strip().lower()
        if decision_filtro not in ESTADOS_DECISION:
            raise HTTPException(
                status_code=422, detail="decision_estado no está en el CHECK del esquema"
            )

    condiciones: list[str] = []
    params: list[Any] = []
    if device:
        condiciones.append("device_id = ?")
        params.append(device)
    if desde is not None:
        condiciones.append("ts_ms >= ?")
        params.append(int(desde))
    if hasta is not None:
        condiciones.append("ts_ms <= ?")
        params.append(int(hasta))
    if busqueda:
        patron = f"%{_escapar_like(busqueda)}%"
        condiciones.append(
            "(lower(COALESCE(descripcion, '')) LIKE lower(?) ESCAPE '\\' "
            "OR lower(COALESCE(etiquetas, '')) LIKE lower(?) ESCAPE '\\')"
        )
        params.extend((patron, patron))
    if canal_filtro:
        condiciones.append("canal = ?")
        params.append(canal_filtro)
    if estado_filtro:
        condiciones.append("vision_estado = ?")
        params.append(estado_filtro)
    if con_ia_activo:
        condiciones.append("vision_estado = 'hecho'")
        condiciones.append("descripcion IS NOT NULL")
        condiciones.append("TRIM(descripcion) <> ''")
    where = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""
    con = _conectar()
    try:
        # Columnas decision_* SOLO si la migración F26 existe ya en esta BD
        # (debido respaldo en _conectar y canónica de la ingesta F26-a1).
        columnas = _columnas_de(con, "imagenes")
        extra = (
            [c for c in COLUMNAS_DECISION_PANEL if c in columnas]
            if (decision_activo or decision_filtro)
            else []
        )
        seleccion = COLS_IMAGEN_METADATA + (", " + ", ".join(extra) if extra else "")
        if decision_filtro and "decision_estado" not in columnas:
            raise HTTPException(
                status_code=422,
                detail="decision_estado indisponible (esquema F26 no migrado)",
            )
        if decision_filtro:
            condiciones.append("decision_estado = ?")
            params.append(decision_filtro)
            where = f" WHERE {' AND '.join(condiciones)}"
        filas = _filas(
            con,
            f"SELECT {seleccion} FROM imagenes{where} "
            "ORDER BY ts_ms DESC, id DESC LIMIT ?",
            (*params, lim),
        )
    finally:
        con.close()
    return {
        "limite": lim,
        "q": busqueda or None,
        "canal": canal_filtro,
        "vision_estado": estado_filtro,
        "decision_estado": decision_filtro,
        "decision": decision_activo,
        "con_ia": con_ia_activo,
        "filas": filas,
    }


def _resolver_foto(ruta_relativa: str) -> Path | None:
    """Resuelve ``BASE/ruta`` y devuelve ``None`` si escapa de la base (path traversal)."""
    base = _datos_dir()
    candidata = (base / str(ruta_relativa)).resolve()
    if not candidata.is_relative_to(base):
        return None  # cualquier escape se trata igual que "no existe": 404 sin pista
    return candidata


@app.get("/api/imagenes/{imagen_id}/foto")
def api_imagen_foto(imagen_id: int) -> Response:
    """Sirve el JPEG de evidencia por id de BD (SPEC §9.1).

    - ``imagen_id`` no entero → 422 (validador de FastAPI).
    - Sin Origin guard extra: lectura GET estándar en loopback; el bind 127.0.0.1
      ES el control (mismo criterio que ``/api/eventos``, AGENTS.md "Seguridad").
    - ``imagen_purgada=1``, id inexistente, archivo ausente o ruta que escape de la
      base de datos → 404 (nunca se filtra la ruta ni la causa exacta).
    """
    con = _conectar()
    try:
        fila = con.execute(
            "SELECT ruta, imagen_purgada, origen FROM imagenes WHERE id = ?",
            (imagen_id,),
        ).fetchone()
    finally:
        con.close()
    if fila is None or int(fila["imagen_purgada"] or 0) == 1:
        raise HTTPException(status_code=404, detail="imagen no disponible")
    archivo = _resolver_foto(fila["ruta"])
    if archivo is None or not archivo.is_file():
        raise HTTPException(status_code=404, detail="imagen no disponible")
    return Response(
        content=archivo.read_bytes(),
        media_type="image/jpeg",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


# ------------------------------------------------------------------------------
# Export CSV (streaming, columna origen obligatoria, valores escapados)
# ------------------------------------------------------------------------------
def _celda_csv(valor: Any) -> str:
    """Serializa una celda y neutraliza inyección de fórmulas (=, +, @) en hojas de cálculo."""
    if valor is None:
        return ""
    if isinstance(valor, (int, float)):
        return str(valor)
    texto = str(valor)
    if texto[:1] in ("=", "+", "@"):
        return "'" + texto
    return texto


def _generar_csv(tabla: str, origen: str) -> Iterator[str]:
    """Genera el CSV fila a fila (no carga todo en memoria) con quoting del módulo csv."""
    con = _conectar()
    try:
        con_filtro = origen != "todos"
        sql = _SQL_EXPORT[(tabla, con_filtro)]
        cursor = con.execute(sql, (origen,) if con_filtro else ())
        buffer = io.StringIO()
        escritor = csv.writer(buffer, dialect="excel", lineterminator="\r\n")
        escritor.writerow([col[0] for col in cursor.description])
        yield buffer.getvalue()
        for fila in cursor:
            buffer.seek(0)
            buffer.truncate(0)
            escritor.writerow([_celda_csv(valor) for valor in fila])
            yield buffer.getvalue()
    finally:
        con.close()


@app.get("/api/export")
def api_export(tabla: str, origen: str = "real") -> StreamingResponse:
    if tabla not in TABLAS_EXPORT:
        raise HTTPException(status_code=422, detail="tabla debe ser evaluaciones|eventos|telemetria")
    if origen not in ("real", "sintetico", "todos"):
        raise HTTPException(status_code=422, detail="origen debe ser real|sintetico|todos")
    return StreamingResponse(
        _generar_csv(tabla, origen),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="aulasense_{tabla}_{origen}.csv"',
            "Cache-Control": "no-store",
        },
    )


# ------------------------------------------------------------------------------
# Inyección sintética (SPEC §9/C13) — Origin guard + validador de la ingesta
# ------------------------------------------------------------------------------
class EventoSintetico(BaseModel):
    kind: str
    device_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


def _insertar_evento(con: sqlite3.Connection, device_id: str, tipo: str, ts_ms: int,
                     score: float | None, duracion_ms: int | None) -> dict[str, Any]:
    cursor = con.execute(
        "INSERT INTO eventos (device_id, ts_ms, tipo, score, duracion_ms, origen) "
        "VALUES (?, ?, ?, ?, ?, 'sintetico')",
        (device_id, ts_ms, tipo, score, duracion_ms),
    )
    fila = con.execute("SELECT * FROM eventos WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(fila)


def _upsert_dispositivo(con: sqlite3.Connection, device_id: str, ts_ms: int) -> None:
    con.execute(
        "INSERT INTO dispositivos (device_id, first_seen_ms, last_seen_ms) VALUES (?, ?, ?) "
        "ON CONFLICT(device_id) DO UPDATE SET last_seen_ms = excluded.last_seen_ms",
        (device_id, ts_ms, ts_ms),
    )


def _inyectar(kind: str, device_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Escribe una fila sintética y deriva bordes mínimos (SPEC §7)."""
    ahora = _ahora_ms()
    creados: list[dict[str, Any]] = []
    evaluacion: dict[str, Any] | None = None
    con = _conectar()
    try:
        with con:
            _upsert_dispositivo(con, device_id, ahora)
            if kind == "motion":
                previa = con.execute(
                    "SELECT state, ts_ms FROM evaluaciones WHERE device_id = ? "
                    "ORDER BY ts_ms DESC, id DESC LIMIT 1",
                    (device_id,),
                ).fetchone()
                estado = str(payload.get("state"))
                score = float(payload.get("score", 0.0))
                ts_dispositivo = int(payload.get("timestamp_ms", 0))
                cursor = con.execute(
                    "INSERT INTO evaluaciones "
                    "(device_id, ts_ms, ts_device_ms, hora_sinc, state, score, origen) "
                    "VALUES (?, ?, ?, 0, ?, ?, 'sintetico')",
                    (device_id, ahora, ts_dispositivo, estado, score),
                )
                evaluacion = dict(
                    con.execute(
                        "SELECT * FROM evaluaciones WHERE id = ?", (cursor.lastrowid,)
                    ).fetchone()
                )
                previo = previa["state"] if previa is not None else "idle"
                if estado == "motion" and previo != "motion":
                    creados.append(
                        _insertar_evento(con, device_id, "MOVIMIENTO_INICIO", ahora, score, None)
                    )
                elif estado == "idle" and previo == "motion":
                    # Duración = desde el último MOVIMIENTO_INICIO derivado del mismo device.
                    ultimo_inicio = con.execute(
                        "SELECT ts_ms FROM eventos WHERE device_id = ? "
                        "AND tipo = 'MOVIMIENTO_INICIO' ORDER BY id DESC LIMIT 1",
                        (device_id,),
                    ).fetchone()
                    duracion = None
                    if ultimo_inicio is not None:
                        duracion = max(0, ahora - int(ultimo_inicio["ts_ms"]))
                    creados.append(
                        _insertar_evento(
                            con, device_id, "MOVIMIENTO_FIN", ahora, score, duracion
                        )
                    )
            elif kind == "fault":
                creados.append(
                    _insertar_evento(
                        con, device_id, "FALLA", ahora, None, None
                    )
                )
            elif kind == "health":
                tipo = "NODO_ONLINE" if bool(payload.get("online")) else "NODO_OFFLINE"
                creados.append(_insertar_evento(con, device_id, tipo, ahora, None, None))
            elif kind == "aulasense_temp":
                cursor = con.execute(
                    "INSERT INTO telemetria (device_id, ts_ms, die_temp_c, origen) "
                    "VALUES (?, ?, ?, 'sintetico')",
                    (device_id, ahora, float(payload.get("die_temp_c", 0.0))),
                )
                creados.append(
                    dict(
                        con.execute(
                            "SELECT * FROM telemetria WHERE id = ?", (cursor.lastrowid,)
                        ).fetchone()
                    )
                )
    finally:
        con.close()
    return {"evaluacion": evaluacion, "filas": creados}


@app.post("/internal/evento", status_code=201)
async def internal_evento(evento: EventoSintetico, request: Request) -> JSONResponse:
    """Inyecta un evento sintético validado (origen='sintetico' hard-codeado, C13).

    Origin guard: header ausente = permitir (curl/CLI/pytest); presente y no
    allowlist = 403. El payload se valida con el MISMO validador de la ingesta y el
    topic sintético se construye con el prefijo MQTT configurado.

    Guard de fuente (auditoria 2026-11-15): ademas del Origin, la IP origen debe
    ser local (``_fuente_interna_permitida``); la inyeccion sintetica es una
    capacidad de maquina-a-maquina local, NO un endpoint publico.
    """
    if not _origen_permitido(request.headers.get("origin")):
        return _respuesta_403("Origin no permitido")
    if not _fuente_interna_permitida(_ip_cliente(request)):
        return _respuesta_403("fuente no local")
    if evento.kind not in TIPOS_SINTETICOS:
        raise HTTPException(
            status_code=422,
            detail="kind debe ser motion|health|fault|aulasense_temp",
        )
    if not RE_DEVICE.match(evento.device_id):
        raise HTTPException(status_code=422, detail="device_id debe ser 16 hex minúscula")

    topic = f"{_prefijo_topic()}/{evento.device_id}/{evento.kind}"
    resultado = validar(topic, evento.payload)
    # El validador de la ingesta ancla el parseo al prefijo contractual de SPEC §6;
    # si el prefijo configurado difiere, se revalida con el prefijo canónico.
    if not (isinstance(resultado, dict) and resultado.get("valido")) and (
        _prefijo_topic() != PREFIJO_CONTRATO
    ):
        resultado = validar(f"{PREFIJO_CONTRATO}/{evento.device_id}/{evento.kind}", evento.payload)
    if not isinstance(resultado, dict) or not resultado.get("valido"):
        detalle = (resultado or {}).get("error") if isinstance(resultado, dict) else None
        raise HTTPException(status_code=422, detail=detalle or "payload no válido")
    datos = resultado.get("datos") or evento.payload

    creado = await asyncio.to_thread(_inyectar, evento.kind, evento.device_id, datos)
    if creado["evaluacion"] is not None:
        gestor_ws.difundir(
            {"type": "evaluacion", "origin": "sintetico", "fila": creado["evaluacion"]}
        )
    for fila in creado["filas"]:
        if "tipo" in fila:
            gestor_ws.difundir({"type": "evento", "origin": "sintetico", "fila": fila})
        else:
            gestor_ws.difundir({"type": "telemetria", "origin": "sintetico", "fila": fila})
    return JSONResponse(
        status_code=201,
        content={
            "ok": True,
            "origin": "sintetico",
            "kind": evento.kind,
            "device_id": evento.device_id,
            "evaluacion": creado["evaluacion"],
            "filas": creado["filas"],
        },
    )


# ------------------------------------------------------------------------------
# WebSocket live
# ------------------------------------------------------------------------------
@app.websocket("/ws")
async def ws_live(ws: WebSocket) -> None:
    """Push live; rechaza el handshake sin Origin permitido o sin sesión válida."""
    if not _origen_permitido(ws.headers.get("origin")):
        await ws.close(code=1008)  # antes de accept ⇒ respuesta HTTP 403
        return
    # F25-e (§9.2): el handshake WS también exige la cookie de sesión.
    sesion = await asyncio.to_thread(_abrir_sesion, ws.cookies.get(auth.NOMBRE_COOKIE))
    if sesion is None:
        await ws.close(code=1008)  # antes de accept ⇒ respuesta HTTP 403
        return
    await ws.accept()
    cola = gestor_ws.registrar(ws)
    try:
        await ws.send_json(_mensaje_hello())
        while True:
            try:
                mensaje = await asyncio.wait_for(cola.get(), timeout=15.0)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "ping", "ts_ms": _ahora_ms()})
                continue
            await ws.send_json(mensaje)
    except asyncio.CancelledError:
        raise
    except Exception:  # cliente roto: se cierra sin tumbar el servidor
        pass
    finally:
        gestor_ws.quitar(ws)


if __name__ == "__main__":
    import uvicorn

    # Bind por defecto loopback (D-F25-3: en WSL la unit systemd fija AULASENSE_HOST=0.0.0.0
    # VM-local; en Windows el default 127.0.0.1 se mantiene intacto).
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")