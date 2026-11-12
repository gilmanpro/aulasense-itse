#!/usr/bin/env python
"""Dashboard AulaSense — FastAPI en 127.0.0.1:8000 (SPEC §9).

Reglas de seguridad aplicadas aquí (AGENTS.md, sección "Seguridad de la demo"):
- bind SIEMPRE ``127.0.0.1`` (nunca 0.0.0.0, sin flag para cambiarlo);
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
import sqlite3
import sys
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterator
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# --- Interfaz compartida con la ingesta (DEV A): no reinventar el contrato -----
SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from ingesta.esquema import ESQUEMA_SQL, validar  # noqa: E402

# --- Constantes del contrato ---------------------------------------------------
HOST = "127.0.0.1"  # hard-codeado a propósito: el dashboard jamás sale de loopback
PORT = 8000
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
  FOREIGN KEY(evento_id) REFERENCES eventos(id)
);
CREATE INDEX IF NOT EXISTS idx_img_dev_ts ON imagenes(device_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_img_evento ON imagenes(evento_id);
CREATE INDEX IF NOT EXISTS idx_img_purgada ON imagenes(imagen_purgada);
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
COLS_IMAGEN_METADATA = (
    "id, device_id, ts_ms, tamano_bytes, ancho, alto, evento_id, "
    "ia_persona, ia_score, imagen_purgada, origen, recibido_en"
)

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
    return con


def _filas(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(fila) for fila in con.execute(sql, params)]


def _clamp_limite(valor: int | None, defecto: int = LIMITE_DEFECTO) -> int:
    """Clampa ``limite`` a 1..5000 (SPEC §9)."""
    if valor is None:
        return defecto
    return max(1, min(LIMITE_MAX, int(valor)))


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
    _conectar().close()  # crea/valida el esquema al arrancar
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


@app.get("/api/validacion")
def api_validacion(sesion: str | None = None) -> dict[str, Any]:
    """Matriz de confusión básica sesión(verdad terreno) × evaluaciones predichas.

    ``validacion_sesiones`` no tiene device_id (SPEC §8), así que se cruza solo por
    ventana temporal; con varias placas el resultado es agregado. Puede salir vacía.
    """
    con = _conectar()
    try:
        if sesion:
            sesiones = _filas(
                con,
                "SELECT * FROM validacion_sesiones WHERE sesion = ? ORDER BY inicio_ms",
                (sesion,),
            )
        else:
            sesiones = _filas(
                con, "SELECT * FROM validacion_sesiones ORDER BY inicio_ms"
            )
        detalle: list[dict[str, Any]] = []
        matriz = Counter({"vp": 0, "fp": 0, "vn": 0, "fn": 0})
        for fila in sesiones:
            conteo = {
                r["state"]: int(r["n"])
                for r in con.execute(
                    "SELECT state, COUNT(*) AS n FROM evaluaciones "
                    "WHERE ts_ms BETWEEN ? AND ? GROUP BY state",
                    (int(fila["inicio_ms"]), int(fila["fin_ms"])),
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
        "nota": (
            "Matriz básica sobre sesiones de verdad terreno manual; "
            f"tamaño muestral N={len(detalle)} sesiones. {_NOTA_HONESTA}"
        ),
    }


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
) -> dict[str, Any]:
    """Metadata paginada de imágenes de evidencia (SPEC §9.1).

    Devuelve solo columnas de metadata (``COLS_IMAGEN_METADATA``); la ruta del
    archivo NUNCA sale por la API.
    """
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
            f"SELECT {COLS_IMAGEN_METADATA} FROM imagenes{where} "
            "ORDER BY ts_ms DESC, id DESC LIMIT ?",
            (*params, lim),
        )
    finally:
        con.close()
    return {"limite": lim, "filas": filas}


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
    """
    if not _origen_permitido(request.headers.get("origin")):
        return _respuesta_403("Origin no permitido")
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
    """Push live; rechaza el handshake si el Origin no está permitido (nunca accept)."""
    if not _origen_permitido(ws.headers.get("origin")):
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

    # Bind SIEMPRE loopback y sin flag de override (AGENTS.md, D10).
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")