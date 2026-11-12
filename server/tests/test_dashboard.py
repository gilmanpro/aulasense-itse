"""Tests del dashboard AulaSense (SPEC §9) — pytest + fastapi.testclient.

Cobertura: raíz HTML, Origin guard (cruzado/ausente/loopback/extra exacta),
clamp de ``limite`` y validación de ``device_id``, inyección sintética con
``origen='sintetico'`` hard-codeado, broadcast WS <2 s y export CSV escapado.

La DB va a ``tmp_path`` vía ``AULASENSE_DB``; el esquema lo crea la app con el
DDL compartido de ``server/ingesta/esquema.py`` (import real, sin stubs).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

DEVID = "0123456789abcdef"
MOTION_VALIDO = {
    "kind": "motion",
    "device_id": DEVID,
    "payload": {"timestamp_ms": 1000, "state": "motion", "score": 0.87},
}


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    """TestClient con DB temporal y sin allowlist extra heredada del entorno."""
    monkeypatch.setenv("AULASENSE_DB", str(tmp_path / "aulasense_test.db"))
    monkeypatch.delenv("AULASENSE_ORIGINS_EXTRA", raising=False)

    from fastapi.testclient import TestClient

    from dashboard.app import app

    with TestClient(app) as test_client:
        # Primer request: garantiza esquema creado antes de inserciones directas.
        assert test_client.get("/api/resumen").status_code == 200
        yield test_client


def test_raiz_sirve_index(cliente):
    respuesta = cliente.get("/")
    assert respuesta.status_code == 200
    assert "text/html" in respuesta.headers["content-type"]
    assert "AulaSense" in respuesta.text
    # Regla 11 de AGENTS.md: el badge honesto viaja en la página.
    assert "idle NO significa sala vacía" in respuesta.text


def test_origin_cruzado_rechazado(cliente):
    respuesta = cliente.post(
        "/internal/evento", json=MOTION_VALIDO, headers={"Origin": "http://evil.com"}
    )
    assert respuesta.status_code == 403


def test_origin_ausente_y_loopback_permitidos(cliente):
    # Sin Origin = permitir (curl/CLI/pytest).
    assert cliente.post("/internal/evento", json=MOTION_VALIDO).status_code == 201
    # Loopback explícito permitido.
    assert cliente.post(
        "/internal/evento",
        json=MOTION_VALIDO,
        headers={"Origin": "http://127.0.0.1:8000"},
    ).status_code == 201
    assert cliente.post(
        "/internal/evento",
        json=MOTION_VALIDO,
        headers={"Origin": "http://localhost:8000"},
    ).status_code == 201


def test_allowlist_extra_exacta(cliente, monkeypatch):
    monkeypatch.setenv("AULASENSE_ORIGINS_EXTRA", "ejemplo.test:9000")
    assert cliente.post(
        "/internal/evento",
        json=MOTION_VALIDO,
        headers={"Origin": "http://ejemplo.test:9000"},
    ).status_code == 201
    assert cliente.post(
        "/internal/evento",
        json=MOTION_VALIDO,
        headers={"Origin": "http://ejemplo.test:9001"},
    ).status_code == 403
    # Nada de prefijos/sufijos: un subdominio no cuela.
    assert cliente.post(
        "/internal/evento",
        json=MOTION_VALIDO,
        headers={"Origin": "http://sub.ejemplo.test:9000"},
    ).status_code == 403


def test_motion_sintetico_escribe_filas(cliente):
    respuesta = cliente.post("/internal/evento", json=MOTION_VALIDO)
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["origin"] == "sintetico"
    assert cuerpo["evaluacion"]["origen"] == "sintetico"

    evaluaciones = cliente.get("/api/evaluaciones", params={"device_id": DEVID}).json()
    assert evaluaciones["filas"], "debe existir la evaluación sintética"
    assert evaluaciones["filas"][0]["origen"] == "sintetico"

    eventos = cliente.get("/api/eventos", params={"device_id": DEVID}).json()["filas"]
    assert any(fila["tipo"] == "MOVIMIENTO_INICIO" for fila in eventos)
    assert all(fila["origen"] == "sintetico" for fila in eventos)

    dispositivos = cliente.get("/api/dispositivos").json()["dispositivos"]
    tarjeta = next(item for item in dispositivos if item["device_id"] == DEVID)
    assert tarjeta["estado"] == "motion"
    assert tarjeta["en_vivo"] is True


def test_limite_clamp_y_device_invalido(cliente):
    respuesta = cliente.get("/api/evaluaciones", params={"limite": 99999})
    assert respuesta.status_code == 200
    assert respuesta.json()["limite"] == 5000

    respuesta = cliente.get("/api/evaluaciones", params={"limite": -7})
    assert respuesta.json()["limite"] == 1

    # device_id que no es 16 hex minúscula => 422 (nunca se concatena al SQL).
    assert cliente.get("/api/evaluaciones", params={"device_id": "zzzzzzzzzzzzzzzz"}).status_code == 422
    assert cliente.get("/api/evaluaciones", params={"device_id": "' OR 1=1 --"}).status_code == 422

    # Parámetros SQL correctos con device_id válido y sin filas.
    respuesta = cliente.get("/api/evaluaciones", params={"device_id": "ffffffffffffffff"})
    assert respuesta.status_code == 200
    assert respuesta.json()["filas"] == []


def test_eventos_tipo_fuera_del_check(cliente):
    assert cliente.get("/api/eventos", params={"tipo": "HACK"}).status_code == 422
    assert cliente.get("/api/eventos", params={"tipo": "MOVIMIENTO_FIN"}).status_code == 200


def test_ws_broadcast_sintetico_en_menos_de_2s(cliente):
    with cliente.websocket_connect(
        "/ws", headers={"origin": "http://127.0.0.1:8000"}
    ) as ws:
        saludo = ws.receive_json()
        assert saludo["type"] == "hello"
        assert "ultimo_estado" in saludo
        assert "evaluaciones" in saludo

        inicio = time.monotonic()
        assert cliente.post("/internal/evento", json=MOTION_VALIDO).status_code == 201

        recibido = None
        while time.monotonic() - inicio < 2.0:
            mensaje = ws.receive_json()
            if mensaje.get("type") == "evaluacion":
                recibido = mensaje
                break
        assert recibido is not None, "no llegó el broadcast en menos de 2 s"
        assert recibido["origin"] == "sintetico"
        assert recibido["fila"]["origen"] == "sintetico"


def test_ws_rechaza_origin_cruzado(cliente):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with cliente.websocket_connect(
            "/ws", headers={"origin": "http://evil.com"}
        ) as ws:
            ws.receive_json()


def test_export_csv_content_type_y_escapado(cliente):
    ruta = os.environ["AULASENSE_DB"]
    con = sqlite3.connect(ruta)
    try:
        # Valor hostil: comas, comillas y fórmula de hoja de cálculo.
        con.execute(
            "INSERT INTO telemetria (device_id, ts_ms, detector, origen) "
            "VALUES (?, ?, ?, 'sintetico')",
            (DEVID, int(time.time() * 1000), '=cmd|"x",y'),
        )
        con.commit()
    finally:
        con.close()

    respuesta = cliente.get(
        "/api/export", params={"tabla": "telemetria", "origen": "sintetico"}
    )
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/csv")
    assert respuesta.headers["content-disposition"].startswith("attachment;")

    texto = respuesta.text
    cabecera = texto.splitlines()[0]
    assert "origen" in cabecera
    assert "sintetico" in texto
    # Escapado CSV: el campo con comas/comillas queda entrecomillado y duplicado.
    assert "'=cmd" in texto  # fórmula neutralizada con apóstrofo
    assert '""x""' in texto  # comillas escapadas por el módulo csv


def test_validacion_vacia_no_crashea(cliente):
    respuesta = cliente.get("/api/validacion")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["sesiones"] == []
    assert cuerpo["matriz"]["n_sesiones"] == 0
    assert cuerpo["matriz"]["recall"] is None

    # Fuentes externas y telemetría responden vacías sin error.
    assert cliente.get("/api/fuentes_externas").status_code == 200
    assert cliente.get("/api/telemetria").status_code == 200
    assert cliente.get("/api/resumen").json()["total_evaluaciones"] == 0