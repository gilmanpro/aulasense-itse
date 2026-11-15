"""Tests del campo OPCIONAL ``origen`` en el decoder de imagen MQTT (SPEC §6.2/§7.2).

Contrato honrado desde 2026-11-15: el envelope ``image`` puede traer
``origen: "real" | "sintetico"``; ausente -> ``"real"``; valor invalido ->
``"real"`` + WARNING **sin descartar** el mensaje (el campo es accesorio: la
verdad la da el evento motion, §6.2).

Autonomo a proposito: NO importa helpers de ``test_imagen_mqtt.py`` (ese fichero
lo edita otro agente en paralelo). Todo va a ``tmp_path``; nunca se toca
``server/datos/*.db`` ni el broker.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import queue
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import ingesta_mqtt
from ingesta_mqtt import IngestaWriter

PREFIJO = "aulasense/v1/devices"
DEV = "0123456789abcdef"
NOW = 1_760_000_000_000


# --- Helpers ---------------------------------------------------------------


def _jpeg(color: tuple[int, int, int] = (30, 60, 90)) -> bytes:
    """JPEG sintetico real con PIL (QVGA)."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (320, 240), color).save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


def _topic(suffix: str) -> str:
    return f"{PREFIJO}/{DEV}/{suffix}"


def _envelope(imagen: bytes, **cambios) -> bytes:
    """Envelope §6.2 coherente; ``cambios`` anade/rompe lo que interese."""
    datos = {
        "v": 1,
        "device_id": DEV,
        "ts_ms": 2000,
        "motivo": "onset",
        "tamano": len(imagen),
        "sha256": hashlib.sha256(imagen).hexdigest(),
        "b64": base64.b64encode(imagen).decode("ascii"),
    }
    datos.update(cambios)
    return json.dumps(datos).encode("utf-8")


def _device_payload() -> dict:
    return {
        "device_id": DEV,
        "name": "aulasense",
        "label": "Aula-Origen",
        "frontend": "native",
        "firmware": "3.2.0",
        "chip": "esp32s3",
        "csi_profile": "ht20",
    }


def _origenes(db: Path) -> list[str]:
    con = sqlite3.connect(str(db))
    try:
        return [fila[0] for fila in con.execute("SELECT origen FROM imagenes ORDER BY id")]
    finally:
        con.close()


def _avisos(caplog, texto: str) -> list[logging.LogRecord]:
    return [
        registro
        for registro in caplog.records
        if registro.levelno == logging.WARNING and texto in registro.getMessage()
    ]


@pytest.fixture
def escenario(tmp_path, monkeypatch):
    """Writer real sobre ``tmp_path`` con la allowlist sembrada (device + health)."""
    monkeypatch.setenv("AULASENSE_DATOS_DIR", str(tmp_path / "datos"))
    cola: "queue.Queue" = queue.Queue(maxsize=1000)
    writer = IngestaWriter(tmp_path / "aulasense.db", tmp_path / "export.csv", cola=cola)
    assert writer.procesar(_topic("device"), _device_payload(), now_ms=NOW)[
        "accion"
    ] == "device_upsert"
    # health fija la epoca_boot (R3): ts_ms del nodo 1000 -> epoca NOW.
    assert writer.procesar(
        _topic("health"),
        {"status": "ok", "online": True, "uptime_s": 10, "timestamp_ms": 1000},
        now_ms=NOW,
    )["accion"].startswith("health_")
    yield SimpleNamespace(writer=writer, db=tmp_path / "aulasense.db")
    writer.cerrar()


# --- 1. Ausente -> real ----------------------------------------------------


def test_origen_ausente_persiste_real_sin_aviso(escenario, caplog):
    imagen = _jpeg()
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(imagen), now_ms=NOW
        )
    assert resultado["accion"] == "imagen_mqtt", resultado
    assert _origenes(escenario.db) == ["real"]
    assert _avisos(caplog, "origen") == []


# --- 2. Sintetico -> sintetico (campo honrado) -----------------------------


def test_origen_sintetico_se_honra(escenario, caplog):
    imagen = _jpeg((10, 120, 40))
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(imagen, origen="sintetico"), now_ms=NOW
        )
    assert resultado["accion"] == "imagen_mqtt", resultado
    assert _origenes(escenario.db) == ["sintetico"]
    assert _avisos(caplog, "origen") == []


# --- 3. Invalido -> real + WARNING, SIN descartar --------------------------


@pytest.mark.parametrize("invalido", ["quiza", "REAL", "", 7, True])
def test_origen_invalido_persiste_real_con_aviso(escenario, caplog, invalido):
    imagen = _jpeg((120, 40, 10))
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(imagen, origen=invalido), now_ms=NOW
        )
    # Accesorio: el mensaje se persiste igual (nunca se rechaza).
    assert resultado["accion"] == "imagen_mqtt", resultado
    assert _origenes(escenario.db) == ["real"]
    assert len(_avisos(caplog, "origen' invalido")) == 1


# --- 4. Extras del contrato ------------------------------------------------


def test_origen_real_explicito_persiste_real(escenario, caplog):
    imagen = _jpeg((90, 90, 200))
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(imagen, origen="real"), now_ms=NOW
        )
    assert resultado["accion"] == "imagen_mqtt", resultado
    assert _origenes(escenario.db) == ["real"]
    assert _avisos(caplog, "origen") == []


def test_origen_null_equivale_a_ausente(escenario, caplog):
    imagen = _jpeg((5, 200, 200))
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(imagen, origen=None), now_ms=NOW
        )
    assert resultado["accion"] == "imagen_mqtt", resultado
    assert _origenes(escenario.db) == ["real"]
    assert _avisos(caplog, "origen") == []


# --- 5. Unidad del helper (sin BD ni broker) -------------------------------


@pytest.mark.parametrize(
    ("datos", "esperado"),
    [
        ({}, "real"),
        ({"origen": None}, "real"),
        ({"origen": "real"}, "real"),
        ({"origen": "sintetico"}, "sintetico"),
        ({"origen": "SINTETICO"}, "real"),
        ({"origen": 1}, "real"),
        ({"origen": ["sintetico"]}, "real"),
    ],
)
def test_helper_origen_desde_envelope(datos, esperado):
    assert ingesta_mqtt.origen_desde_envelope(datos) == esperado