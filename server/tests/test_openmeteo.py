"""Tests de `server/fuentes/openmeteo.py` (SPEC §10, C6, degradación P1).

Cubren las tres rutas del contrato:
1. coords vacías -> exit 0 SIN red (el `requests.get` de prueba falla si se llama).
2. coords + respuesta fake -> INSERT correcto en `fuentes_externas` (DB en tmp_path).
3. error de red -> reintento suave, no crashea, exit 0 y tabla creada.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]  # aulasense/


def _cargar(nombre: str, ruta: Path):
    """Carga un módulo por ruta (el proyecto no es paquete instalable)."""
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


openmeteo = _cargar("openmeteo_aulasense", ROOT / "server" / "fuentes" / "openmeteo.py")

PAYLOAD_FAKE = {
    "latitude": -2.1709,
    "longitude": -79.9224,
    "timezone": "America/Guayaquil",
    "current_units": {
        "temperature_2m": "°C",
        "relative_humidity_2m": "%",
        "precipitation": "mm",
        "wind_speed_10m": "km/h",
    },
    "current": {
        "time": "2026-11-12T16:15",
        "interval": 900,
        "temperature_2m": 23.4,
        "relative_humidity_2m": 65,
        "precipitation": 0.0,
        "wind_speed_10m": 12.3,
    },
    "hourly": {
        "time": ["2026-11-12T16:00", "2026-11-12T17:00"],
        "temperature_2m": [23.1, 23.7],
        "precipitation": [0.0, 0.2],
    },
}


class _RespuestaFake:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return PAYLOAD_FAKE


def test_coords_vacias_degrada_sin_red(monkeypatch, capsys, tmp_path):
    """Sin OPENMETEO_LAT/LON: mensaje de degradación y exit 0, cero peticiones."""
    monkeypatch.delenv("OPENMETEO_LAT", raising=False)
    monkeypatch.delenv("OPENMETEO_LON", raising=False)
    monkeypatch.setenv("AULASENSE_DB", str(tmp_path / "no_debe_crearse.db"))

    def _falla(*_args, **_kwargs):
        raise AssertionError("no debe haber red con coords vacías")

    monkeypatch.setattr(requests, "get", _falla)

    codigo = openmeteo.main(["--env", str(tmp_path / "sin.env")])
    salida = capsys.readouterr()

    assert codigo == 0
    assert "degradada" in salida.out
    assert "OPENMETEO_LAT/LON no configurados" in salida.out
    assert not (tmp_path / "no_debe_crearse.db").exists()


def test_respuesta_ok_inserta_fila_en_fuentes_externas(monkeypatch, capsys, tmp_path):
    """Con coords y respuesta válida: pide el endpoint correcto y guarda la fila."""
    db_path = tmp_path / "datos" / "prueba.db"
    monkeypatch.setenv("AULASENSE_DB", str(db_path))
    monkeypatch.setenv("OPENMETEO_LAT", "-2.1709")
    monkeypatch.setenv("OPENMETEO_LON", "-79.9224")

    llamada: dict = {}

    def _get(url, params=None, timeout=None):
        llamada["url"] = url
        llamada["params"] = params
        llamada["timeout"] = timeout
        return _RespuestaFake()

    monkeypatch.setattr(requests, "get", _get)

    codigo = openmeteo.main(["--once", "--env", str(tmp_path / "sin.env")])
    salida = capsys.readouterr()

    assert codigo == 0
    assert llamada["url"] == "https://api.open-meteo.com/v1/forecast"
    assert llamada["timeout"] == 15
    assert llamada["params"]["latitude"] == pytest.approx(-2.1709)
    assert llamada["params"]["longitude"] == pytest.approx(-79.9224)
    assert llamada["params"]["timezone"] == "auto"
    assert "temperature_2m" in llamada["params"]["current"]
    assert "hourly" in llamada["params"]

    with sqlite3.connect(db_path) as conexion:
        filas = conexion.execute(
            "SELECT fuente, ts_ms, lat, lon, temperatura_c, humedad_pct, "
            "precipitacion_mm, viento_kmh, payload_json FROM fuentes_externas"
        ).fetchall()

    assert len(filas) == 1
    fuente, ts_ms, lat, lon, temp, hum, precip, viento, payload = filas[0]
    assert fuente == "openmeteo"
    assert ts_ms > 0
    assert lat == pytest.approx(-2.1709)
    assert lon == pytest.approx(-79.9224)
    assert temp == pytest.approx(23.4)
    assert hum == pytest.approx(65.0)
    assert precip == pytest.approx(0.0)
    assert viento == pytest.approx(12.3)
    assert "current" in payload
    assert len(payload) <= openmeteo.PAYLOAD_MAX_CHARS
    assert "fila 1" in salida.out


def test_error_red_reintenta_sin_crashear(monkeypatch, capsys, tmp_path):
    """Red caída: un reintento suave, WARN, exit 0 y tabla creada (aunque vacía)."""
    db_path = tmp_path / "error.db"
    monkeypatch.setenv("AULASENSE_DB", str(db_path))
    monkeypatch.setenv("OPENMETEO_LAT", "1.5")
    monkeypatch.setenv("OPENMETEO_LON", "-2.5")
    monkeypatch.setattr(openmeteo, "RETRY_DELAY_S", 0.0)  # no esperar en el test

    intentos: list[int] = []

    def _get(*_args, **_kwargs):
        intentos.append(1)
        raise requests.ConnectionError("sin red (test)")

    monkeypatch.setattr(requests, "get", _get)

    codigo = openmeteo.main(["--env", str(tmp_path / "sin.env")])
    salida = capsys.readouterr()

    assert codigo == 0
    assert len(intentos) == 2  # intento + reintento suave
    assert "reintento suave" in salida.out
    assert "degradada" in salida.out

    with sqlite3.connect(db_path) as conexion:
        tablas = {
            fila[0]
            for fila in conexion.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        total = conexion.execute("SELECT COUNT(*) FROM fuentes_externas").fetchone()[0]

    assert "fuentes_externas" in tablas
    assert total == 0