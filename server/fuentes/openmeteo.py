"""Fetch de Open-Meteo (SPEC §10, C6) — gratis, SIN api key, degradacion elegante (P1).

Uso:
  server\\venv\\Scripts\\python.exe server\\fuentes\\openmeteo.py           # una lectura (default)
  server\\.venv\\Scripts\\python.exe server\\fuentes\\openmeteo.py --loop    # sondeo cada 15 min

Que hace:
- Lee OPENMETEO_LAT/OPENMETEO_LON del `.env` (o del shell, que tiene precedencia).
- Pide a la API publica `current` (temp/humedad/precipitacion/viento) + `hourly`
  (temp/precipitacion) y guarda UNA fila en `fuentes_externas` (SPEC §8).
- DB: `server\\datos\\aulasense.db`, sobrescribible por la env AULASENSE_DB.
- Crea la tabla FUENTES_EXTERNAS con el DDL literal de SPEC §8 (idempotente) antes
  de insertar: no depende de que la ingesta haya arrancado antes.

P1 (degradacion elegante, NUNCA crashea):
- Coords vacias/invalidas -> mensaje claro y exit 0 SIN tocar la red.
- Fallo de red/API -> WARN, un reintento suave y exit 0 (la fuente CSI + die temp
  del S3 cubren C6 igualmente).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]  # aulasense/
ENV_DEFECTO = ROOT / ".env"
DB_DEFECTO = ROOT / "server" / "datos" / "aulasense.db"

API_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 15
INTERVALO_DEFECTO_S = 15 * 60  # --loop: poll cada 15 min
RETRY_DELAY_S = 2.0  # relleno de espera entre el intento 1 y el reintento suave
PAYLOAD_MAX_CHARS = 4000  # el hourly completo ronda 6 KB; se recorta para la fila

MENSAJE_DEGRADADO = (
    "OPENMETEO_LAT/LON no configurados — fuente externa degradada "
    "(C6 cubre die temp del S3)"
)

# DDL literal de SPEC §8 (solo la tabla que escribe esta fuente).
DDL_FUENTES_EXTERNAS = """
CREATE TABLE IF NOT EXISTS fuentes_externas (
  id INTEGER PRIMARY KEY AUTOINCREMENT, fuente TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  lat REAL, lon REAL, temperatura_c REAL, humedad_pct REAL, precipitacion_mm REAL, viento_kmh REAL,
  payload_json TEXT, recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def log(nivel: str, mensaje: str) -> None:
    """Log simple a stdout con marca de tiempo (capturable por tests)."""
    print(f"[openmeteo {datetime.now():%Y-%m-%d %H:%M:%S}] {nivel}: {mensaje}", flush=True)


def ruta_db() -> Path:
    """DB del server: env AULASENSE_DB (convencion compartida) o la ruta por defecto."""
    valor = os.environ.get("AULASENSE_DB", "").strip()
    return Path(valor) if valor else DB_DEFECTO


def leer_coordenadas() -> tuple[float, float] | None:
    """Coords de OPENMETEO_LAT/LON; None si faltan o no son validas (P1)."""
    lat_txt = os.environ.get("OPENMETEO_LAT", "").strip()
    lon_txt = os.environ.get("OPENMETEO_LON", "").strip()
    if not lat_txt or not lon_txt:
        return None
    try:
        lat = float(lat_txt)
        lon = float(lon_txt)
    except ValueError:
        log("WARN", f"OPENMETEO_LAT/LON con formato invalido (lat={lat_txt!r}, lon={lon_txt!r})")
        return None
    if not -90.0 <= lat <= 90.0:
        log("WARN", f"OPENMETEO_LAT fuera de rango [-90,90]: {lat}")
        return None
    if not -180.0 <= lon <= 180.0:
        log("WARN", f"OPENMETEO_LON fuera de rango [-180,180]: {lon}")
        return None
    return lat, lon


def asegurar_tabla(db_path: Path) -> None:
    """Crea directorio + tabla `fuentes_externas` (DDL literal SPEC §8, idempotente)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            # La ingesta ya usa WAL; si esta bloqueada, se ignora (no es critico).
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        conn.executescript(DDL_FUENTES_EXTERNAS)
        conn.commit()
    finally:
        conn.close()


def consultar(coords: tuple[float, float]) -> dict[str, Any] | None:
    """GET a Open-Meteo con timeout=15 y UN reintento suave ante red/API rota."""
    lat, lon = coords
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
        "hourly": "temperature_2m,precipitation",
        "timezone": "auto",
        "wind_speed_unit": "kmh",
    }
    for intento in (1, 2):
        try:
            respuesta = requests.get(API_URL, params=params, timeout=TIMEOUT_S)
            respuesta.raise_for_status()
            datos = respuesta.json()
            if not isinstance(datos, dict) or not isinstance(datos.get("current"), dict):
                raise ValueError("respuesta sin bloque 'current'")
            return datos
        except (requests.RequestException, ValueError) as exc:
            detalle = f"{exc.__class__.__name__}: {exc}"
            if intento == 1:
                log("WARN", f"fallo Open-Meteo ({detalle}); reintento suave en {RETRY_DELAY_S:.1f}s")
                time.sleep(RETRY_DELAY_S)
            else:
                log(
                    "WARN",
                    "Open-Meteo no disponible tras reintento "
                    f"({detalle}). Fuente externa degradada; seguimos con die temp del S3 (C6).",
                )
    return None


def _num(valor: Any) -> float | None:
    """Convierte a float solo numeros reales (bool excluido); None si no aplica."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)


def extraer_medidas(datos: dict[str, Any]) -> dict[str, Any]:
    """Mapea el bloque `current` de la API a las columnas de fuentes_externas."""
    current = datos.get("current") or {}
    return {
        "temperatura_c": _num(current.get("temperature_2m")),
        "humedad_pct": _num(current.get("relative_humidity_2m")),
        "precipitacion_mm": _num(current.get("precipitation")),
        "viento_kmh": _num(current.get("wind_speed_10m")),
        "hora_local": current.get("time"),
    }


def payload_recortado(datos: dict[str, Any]) -> str:
    """JSON compacto de la respuesta, recortado a PAYLOAD_MAX_CHARS (hourly es largo)."""
    texto = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
    return texto[:PAYLOAD_MAX_CHARS]


def guardar_fila(
    db_path: Path,
    lat: float,
    lon: float,
    medidas: dict[str, Any],
    payload_json: str,
    ts_ms: int,
) -> int:
    """INSERT parametrizado en fuentes_externas; devuelve el id de la fila."""
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        cur = conn.execute(
            """
            INSERT INTO fuentes_externas
              (fuente, ts_ms, lat, lon, temperatura_c, humedad_pct,
               precipitacion_mm, viento_kmh, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "openmeteo",
                ts_ms,
                lat,
                lon,
                medidas["temperatura_c"],
                medidas["humedad_pct"],
                medidas["precipitacion_mm"],
                medidas["viento_kmh"],
                payload_json,
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def ejecutar_ciclo() -> int:
    """Un ciclo completo: coords -> tabla -> fetch -> fila. Siempre devuelve 0."""
    coords = leer_coordenadas()
    if coords is None:
        log("WARN", MENSAJE_DEGRADADO)
        return 0

    db_path = ruta_db()
    try:
        asegurar_tabla(db_path)
    except sqlite3.Error as exc:
        log("WARN", f"no se pudo preparar la DB {db_path} ({exc}); se omite esta lectura")
        return 0

    datos = consultar(coords)
    if datos is None:
        return 0

    medidas = extraer_medidas(datos)
    ts_ms = int(time.time() * 1000)
    try:
        fila = guardar_fila(db_path, coords[0], coords[1], medidas, payload_recortado(datos), ts_ms)
    except sqlite3.Error as exc:
        log("WARN", f"INSERT en fuentes_externas fallo ({exc}); se omite la fila")
        return 0

    log(
        "INFO",
        "fila {fila} en {db} -> fuente=openmeteo lat={lat} lon={lon} "
        "t={t}C hum={h}% precip={p}mm viento={v}km/h hora_local={hora}".format(
            fila=fila,
            db=db_path,
            lat=coords[0],
            lon=coords[1],
            t=medidas["temperatura_c"],
            h=medidas["humedad_pct"],
            p=medidas["precipitacion_mm"],
            v=medidas["viento_kmh"],
            hora=medidas["hora_local"],
        ),
    )
    return 0


def _ciclo_seguro() -> int:
    """Envoltorio que NUNCA deja escapar una excepcion (P1)."""
    try:
        return ejecutar_ciclo()
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # red, JSON, sqlite, lo que sea: se degrada, no se cae
        log("WARN", f"ciclo fallido sin abortar ({exc.__class__.__name__}: {exc})")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Fetcher Open-Meteo (gratis, sin key) -> tabla fuentes_externas (SPEC §10)."
    )
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument("--once", action="store_true", help="una lectura y salir (default)")
    modo.add_argument("--loop", action="store_true", help="sondeo continuo cada --interval segundos")
    ap.add_argument(
        "--interval",
        type=int,
        default=INTERVALO_DEFECTO_S,
        help=f"segundos entre lecturas en --loop (default {INTERVALO_DEFECTO_S})",
    )
    ap.add_argument("--env", default=str(ENV_DEFECTO), help="ruta del .env a leer")
    args = ap.parse_args(argv)

    env_path = Path(args.env)
    if env_path.exists():
        # override=False: las variables del shell tienen precedencia sobre el .env
        load_dotenv(env_path, override=False)
    else:
        log("WARN", f"no existe {env_path}; se usan solo variables de entorno del shell")

    if not args.loop:
        return _ciclo_seguro()

    log("INFO", f"modo loop: lectura cada {args.interval}s (Ctrl+C para salir)")
    try:
        while True:
            _ciclo_seguro()
            time.sleep(max(1, args.interval))
    except KeyboardInterrupt:
        log("INFO", "loop detenido por Ctrl+C")
        return 0


if __name__ == "__main__":
    sys.exit(main())