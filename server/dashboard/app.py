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
import os
import re
import secrets
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
    ESQUEMA_SQL,
    validar,
)

# F25-e (SPEC §9.2): login, sesiones, lockout y bootstrap del admin único.
from dashboard import auth  # noqa: E402

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

# --- DDL literal de SPEC §8.1 (copia local deliberada, NO se importa) ---------
# La ingesta es la dueña del esquema (§8.1); el dashboard solo lo replica como
# migración idempotente de respaldo (tests con DB nueva, DB antigua anterior a
# F24). Se mantiene como constante local para no acoplarnos a los símbolos que
# B1 edita en paralelo en ``server/ingesta/esquema.py`` (condición de carrera).
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
  tipo TEXT NOT NULL CHECK(tipo IN ('AP_FALLBACK','CFG_GUARDADA','STA_RECONECTADO','IMAGEN_GATE_OFF','IMAGEN_DESCARTE')),
  detalle TEXT,
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cfg_dev_ts ON config_eventos(device_id, ts_ms);
"""

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


def _migrar_imagenes_f25(con: sqlite3.Connection) -> None:
    """Añade a ``imagenes`` las columnas F25 si faltan (idempotente, tolerante).

    SQLite no tiene ``ADD COLUMN IF NOT EXISTS``: se consulta ``pragma table_info``
    antes de cada ``ALTER TABLE`` (mismo patrón que el canónico de
    ``server/ingesta/esquema.py``). Un fallo de ALTER (p. ej. DB bloqueada por otro
    proceso) NO tumba el arranque: la ingesta aplicará la migración canónica.
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
    except sqlite3.Error:  # tabla ausente/bloqueada: la ingesta migrará después
        con.rollback()


def _conectar() -> sqlite3.Connection:
    """Abre la DB en WAL y garantiza el esquema (CREATE IF NOT EXISTS es idempotente)."""
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
    else:
        # DB anterior a F25: se alinean las columnas de visión (CANAL/descripcion/…)
        # antes de cualquier lectura de la galería.
        _migrar_imagenes_f25(con)
    # Extensión F3 (session logger): misma migración de respaldo.
    existe_ses = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sesiones'"
    ).fetchone()
    if not existe_ses:
        con.executescript(SESIONES_SQL)
    return con


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
    """Sin sesión: 302 al login para la página, 401 para API/estáticos."""
    if ruta in ("/", "/index.html"):
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
        filas = _filas(
            con,
            f"SELECT {COLS_IMAGEN_METADATA} FROM imagenes{where} "
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