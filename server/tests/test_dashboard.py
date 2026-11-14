"""Tests del dashboard AulaSense (SPEC §9) — pytest + fastapi.testclient.

Cobertura: raíz HTML, Origin guard (cruzado/ausente/loopback/extra exacta),
clamp de ``limite`` y validación de ``device_id``, inyección sintética con
``origen='sintetico'`` hard-codeado, broadcast WS <2 s, export CSV escapado y
**galería F24** (metadata sin ruta, foto con cabeceras, purga/inexistente 404,
id no entero 422, path traversal rechazado y banner de privacidad en la UI).

La DB va a ``tmp_path`` vía ``AULASENSE_DB``; el esquema lo crea la app con el
DDL compartido de ``server/ingesta/esquema.py`` (import real, sin stubs) más la
migración idempotente local de §8.1. La base de imágenes va a
``AULASENSE_DATOS_DIR`` (también en ``tmp_path``): los fixtures insertan filas
directamente en la DB, sin depender del receptor de ingesta de B1.
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
# JPEG de 20 bytes con magic válido (FF D8 FF E0 … FF D9): basta para servir bytes.
JPEG_MINIMO = b"\xff\xd8\xff\xe0" + b"\x00" * 14 + b"\xff\xd9"


@pytest.fixture()
def cliente(tmp_path, monkeypatch, credenciales_tmp, iniciar_sesion):
    """TestClient con DB temporal, login REAL (§9.2) y sin allowlist heredada."""
    monkeypatch.setenv("AULASENSE_DB", str(tmp_path / "aulasense_test.db"))
    monkeypatch.delenv("AULASENSE_ORIGINS_EXTRA", raising=False)

    from fastapi.testclient import TestClient

    from dashboard.app import app

    with TestClient(app) as test_client:
        # F25-e: la sesión es REAL (POST /login → cookie), no auth desactivada.
        iniciar_sesion(test_client, credenciales_tmp["password"])
        # Primer request autenticado: garantiza esquema creado antes de inserciones.
        assert test_client.get("/api/resumen").status_code == 200
        yield test_client


@pytest.fixture()
def galeria(tmp_path, monkeypatch, credenciales_tmp, iniciar_sesion):
    """TestClient logueado con DB y base de imágenes temporales (``AULASENSE_DATOS_DIR``)."""
    datos = tmp_path / "datos"
    datos.mkdir()
    monkeypatch.setenv("AULASENSE_DB", str(tmp_path / "aulasense_galeria.db"))
    monkeypatch.setenv("AULASENSE_DATOS_DIR", str(datos))
    monkeypatch.delenv("AULASENSE_ORIGINS_EXTRA", raising=False)

    from fastapi.testclient import TestClient

    from dashboard.app import app

    with TestClient(app) as test_client:
        # F25-e: sesión real (cookie) antes de tocar la API.
        iniciar_sesion(test_client, credenciales_tmp["password"])
        # Primer request: la app crea el esquema base y la migración de §8.1.
        assert test_client.get("/api/resumen").status_code == 200
        yield test_client, datos


def _insertar_imagen(
    datos: Path,
    *,
    ruta: str | None = None,
    purgada: int = 0,
    origen: str = "real",
    ts_ms: int = 1_700_000_000_000,
    device: str = DEVID,
    ia_persona: int | None = None,
    ia_score: float | None = None,
    contenido: bytes = JPEG_MINIMO,
) -> int:
    """Inserta una fila en ``imagenes`` directamente en la DB (sin receptor B1).

    Con ``ruta=None`` además escribe el JPEG bajo ``datos/imagenes/<device>/``.
    """
    relativa = ruta
    if relativa is None:
        relativa = f"imagenes/{device}/evidencia_{ts_ms}.jpg"
        archivo = datos / relativa
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_bytes(contenido)
    con = sqlite3.connect(os.environ["AULASENSE_DB"])
    try:
        cursor = con.execute(
            "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes, "
            "ancho, alto, evento_id, ia_persona, ia_score, imagen_purgada, origen) "
            "VALUES (?, ?, ?, ?, ?, 320, 240, NULL, ?, ?, ?, ?)",
            (
                device, ts_ms, relativa, "0" * 64, len(contenido),
                ia_persona, ia_score, purgada, origen,
            ),
        )
        con.commit()
        return int(cursor.lastrowid)
    finally:
        con.close()


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


# ------------------------------------------------------------------------------
# Galería F24 (SPEC §9.1) — metadata, foto, purga, traversal y filtros
# ------------------------------------------------------------------------------
CAMPOS_METADATA = {
    "id", "device_id", "ts_ms", "tamano_bytes", "ancho", "alto", "evento_id",
    "ia_persona", "ia_score", "imagen_purgada", "origen", "recibido_en",
}


def test_galeria_metadata_clamp_sin_ruta_y_filtros(galeria):
    cliente, datos = galeria
    _insertar_imagen(datos, ts_ms=1_700_000_000_000)
    _insertar_imagen(
        datos, ts_ms=1_700_000_100_000, origen="sintetico", ia_persona=1, ia_score=0.82
    )

    respuesta = cliente.get("/api/imagenes", params={"limite": 99999})
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["limite"] == 5000  # clamp superior 1–5000
    assert len(cuerpo["filas"]) == 2
    fila = cuerpo["filas"][0]  # más reciente primero
    assert set(fila) == CAMPOS_METADATA
    assert "ruta" not in fila  # NUNCA se expone la ruta del archivo
    assert fila["origen"] == "sintetico"
    assert fila["ia_persona"] == 1
    assert fila["ia_score"] == pytest.approx(0.82)

    # Clamp inferior y device_id fuera de contrato → 422.
    assert cliente.get("/api/imagenes", params={"limite": -5}).json()["limite"] == 1
    assert cliente.get(
        "/api/imagenes", params={"device_id": "zzzzzzzzzzzzzzzz"}
    ).status_code == 422
    assert cliente.get(
        "/api/imagenes", params={"device_id": "' OR 1=1 --"}
    ).status_code == 422

    # Filtros temporales y por device (SQL parametrizado).
    recientes = cliente.get(
        "/api/imagenes", params={"device_id": DEVID, "desde": 1_700_000_050_000}
    ).json()["filas"]
    assert [fila["ts_ms"] for fila in recientes] == [1_700_000_100_000]
    assert cliente.get(
        "/api/imagenes", params={"hasta": 1_699_999_999_999}
    ).json()["filas"] == []
    assert cliente.get(
        "/api/imagenes", params={"device_id": "ffffffffffffffff"}
    ).json()["filas"] == []


def test_galeria_foto_200_cabeceras_y_bytes(galeria):
    cliente, datos = galeria
    id_img = _insertar_imagen(datos)
    respuesta = cliente.get(f"/api/imagenes/{id_img}/foto")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("image/jpeg")
    assert respuesta.headers["x-content-type-options"] == "nosniff"
    assert respuesta.headers["cache-control"] == "no-store"
    assert respuesta.content == JPEG_MINIMO


def test_galeria_foto_404_si_purgada(galeria):
    cliente, datos = galeria
    id_img = _insertar_imagen(datos, purgada=1)
    respuesta = cliente.get(f"/api/imagenes/{id_img}/foto")
    assert respuesta.status_code == 404
    # Sin pista de la ruta ni del motivo exacto en el cuerpo.
    assert "imagenes/" not in respuesta.text


def test_galeria_foto_404_id_inexistente_y_archivo_ausente(galeria):
    cliente, datos = galeria
    assert cliente.get("/api/imagenes/99999/foto").status_code == 404
    # Fila válida pero archivo borrado del disco (purga manual sin marcar).
    id_huerfana = _insertar_imagen(
        datos, ruta=f"imagenes/{DEVID}/no_existe_en_disco.jpg"
    )
    assert cliente.get(f"/api/imagenes/{id_huerfana}/foto").status_code == 404


def test_galeria_foto_id_no_entero_422(galeria):
    cliente, _datos = galeria
    assert cliente.get("/api/imagenes/abc/foto").status_code == 422
    assert cliente.get("/api/imagenes/1.5/foto").status_code == 422


def test_galeria_path_traversal_rechazado(galeria, tmp_path):
    cliente, datos = galeria
    externo = tmp_path / "evil.jpg"
    externo.write_bytes(b"\xff\xd8\xff\xe0EVIL-FUERA-DE-LA-BASE\xff\xd9")

    # Ruta relativa maliciosa: base=datos → ../../evil.jpg resuelve FUERA de la base.
    id_relativa = _insertar_imagen(datos, ruta="../../evil.jpg")
    respuesta = cliente.get(f"/api/imagenes/{id_relativa}/foto")
    assert respuesta.status_code == 404
    assert b"EVIL" not in respuesta.content

    # Ruta absoluta maliciosa: tampoco se sirve.
    id_absoluta = _insertar_imagen(datos, ruta=str(externo))
    respuesta = cliente.get(f"/api/imagenes/{id_absoluta}/foto")
    assert respuesta.status_code == 404
    assert b"EVIL" not in respuesta.content


def test_galeria_ui_banner_privacidad_y_pestana(cliente):
    html = cliente.get("/").text
    assert 'data-tab="galeria"' in html
    assert 'id="tab-galeria"' in html
    assert "solo servidor local (nunca nube/broker)" in html
    assert "consentimiento de aula requerido" in html
    # La lógica pide la foto por id de BD y no acepta uploads (sin POST a /api/imagenes).
    js = cliente.get("/static/app.js")
    assert js.status_code == 200
    assert "/api/imagenes/" in js.text
    assert 'fetch("/api/imagenes"' not in js.text
    assert "FormData" not in js.text


# ------------------------------------------------------------------------------
# F3 — panel de validación: sesiones, ventanas marcables y export CSV
# ------------------------------------------------------------------------------
BUCKET_MS = 30_000


def _db_temporal() -> str:
    return os.environ["AULASENSE_DB"]


def _insertar_evaluaciones_reales(inicio_ms: int, n_motion: int = 2, n_idle: int = 0) -> None:
    """Filas reales dentro del bucket (el panel ignora origen='sintetico')."""
    con = sqlite3.connect(_db_temporal())
    try:
        for indice in range(n_motion):
            ts = inicio_ms + 1000 + indice
            con.execute(
                "INSERT INTO evaluaciones (device_id, ts_ms, ts_device_ms, hora_sinc, "
                "state, score, origen, recibido_en) VALUES (?, ?, ?, 1, 'motion', ?, 'real', "
                "datetime(?, 'unixepoch'))",
                (DEVID, ts, 1000 + indice, 0.9, ts // 1000),
            )
        for indice in range(n_idle):
            ts = inicio_ms + 5000 + indice
            con.execute(
                "INSERT INTO evaluaciones (device_id, ts_ms, ts_device_ms, hora_sinc, "
                "state, score, origen, recibido_en) VALUES (?, ?, ?, 1, 'idle', ?, 'real', "
                "datetime(?, 'unixepoch'))",
                (DEVID, ts, 2000 + indice, 0.1, ts // 1000),
            )
        con.commit()
    finally:
        con.close()


def _bucket_actual() -> int:
    """Bucket de 30 s ya cerrado (sus filas están en el pasado y dentro de 10 min)."""
    ahora = int(time.time() * 1000)
    return ((ahora // BUCKET_MS) - 1) * BUCKET_MS


def test_validacion_panel_vacio_no_crashea_extendido(cliente):
    cuerpo = cliente.get("/api/validacion").json()
    # Contrato previo intacto (matriz vacía) + extensiones F3.
    assert cuerpo["sesiones"] == []
    assert cuerpo["matriz"]["n_sesiones"] == 0
    assert cuerpo["registro_sesiones"] == []
    assert cuerpo["sesion_activa"] is None
    assert cuerpo["ventanas"] == []
    assert cuerpo["ventana_ms"] == BUCKET_MS
    assert cuerpo["panel_ms"] == 600_000


def test_api_sesiones_lista_y_sesion_activa(cliente):
    ruta = _db_temporal()
    con = sqlite3.connect(ruta)
    try:
        con.execute(
            "INSERT INTO sesiones (sesion, inicio_ms, estado, nota) "
            "VALUES ('S1', ?, 'activa', 'aula con movimiento')",
            (int(time.time() * 1000) - 5000,),
        )
        con.commit()
    finally:
        con.close()

    cuerpo = cliente.get("/api/sesiones").json()
    assert cuerpo["activa"]["sesion"] == "S1"
    assert [fila["sesion"] for fila in cuerpo["sesiones"]] == ["S1"]

    validacion = cliente.get("/api/validacion").json()
    assert validacion["sesion_activa"]["sesion"] == "S1"
    assert validacion["registro_sesiones"][0]["estado"] == "activa"


def test_validacion_post_marca_ventana_y_export_csv(cliente):
    inicio = _bucket_actual()
    _insertar_evaluaciones_reales(inicio, n_motion=3, n_idle=1)

    respuesta = cliente.post(
        "/api/validacion",
        json={
            "sesion": "S1",
            "inicio_ms": inicio,
            "fin_ms": inicio + BUCKET_MS,
            "hubo_movimiento_real": "si",
            "nota": "dos personas caminando",
        },
    )
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["persistido"] is True
    assert cuerpo["verdad_terreno"] == "motion"

    panel = cliente.get("/api/validacion").json()
    ventana = next(fila for fila in panel["ventanas"] if fila["inicio_ms"] == inicio)
    assert ventana["n_evaluaciones"] == 4
    assert ventana["n_motion"] == 3
    assert ventana["predicho"] == "motion"
    assert ventana["hubo_movimiento_real"] == "si"
    assert ventana["nota"] == "dos personas caminando"
    assert ventana["sesion"] == "S1"
    # La marca entra en la matriz de confusión existente (verdad motion vs predicho motion).
    assert panel["matriz"]["n_sesiones"] == 1
    assert panel["matriz"]["verdaderos_positivos"] == 1
    assert panel["matriz"]["recall"] == 1.0

    export = cliente.get("/api/validacion/export", params={"sesion": "S1"})
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert export.headers["content-disposition"] == 'attachment; filename="validacion_S1.csv"'
    lineas = export.text.splitlines()
    assert lineas[0] == (
        "sesion,inicio_ms,fin_ms,verdad_terreno,hubo_movimiento_real,fuente,notas,recibido_en"
    )
    assert "S1" in export.text and "motion" in export.text and ",si," in export.text
    assert "dos personas caminando" in export.text

    # Export sin filtro = todas las sesiones.
    todas = cliente.get("/api/validacion/export")
    assert todas.status_code == 200
    assert 'filename="validacion_todas.csv"' in todas.headers["content-disposition"]


def test_validacion_post_reemplaza_y_sin_dato_desmarca(cliente):
    inicio = _bucket_actual()
    _insertar_evaluaciones_reales(inicio, n_motion=1, n_idle=3)
    payload = {
        "sesion": "S2",
        "inicio_ms": inicio,
        "fin_ms": inicio + BUCKET_MS,
        "hubo_movimiento_real": "si",
        "nota": "primera marca",
    }
    assert cliente.post("/api/validacion", json=payload).status_code == 201
    payload.update({"hubo_movimiento_real": "no", "nota": "corrección: nadie"})
    assert cliente.post("/api/validacion", json=payload).status_code == 201

    con = sqlite3.connect(_db_temporal())
    try:
        filas = con.execute(
            "SELECT verdad_terreno, notas FROM validacion_sesiones WHERE sesion = 'S2'"
        ).fetchall()
    finally:
        con.close()
    assert filas == [("idle", "corrección: nadie")]  # DELETE+INSERT: una sola marca

    desmarcar = cliente.post(
        "/api/validacion",
        json={**payload, "hubo_movimiento_real": "sin-dato", "nota": None},
    )
    assert desmarcar.status_code == 200
    assert desmarcar.json()["persistido"] is False
    assert desmarcar.json()["marcas_borradas"] == 1

    panel = cliente.get("/api/validacion").json()
    ventana = next(fila for fila in panel["ventanas"] if fila["inicio_ms"] == inicio)
    assert ventana["hubo_movimiento_real"] == "sin-dato"
    assert ventana["marca_id"] is None


def test_validacion_post_origin_cruzado_403(cliente):
    inicio = _bucket_actual()
    respuesta = cliente.post(
        "/api/validacion",
        json={
            "sesion": "S1",
            "inicio_ms": inicio,
            "fin_ms": inicio + BUCKET_MS,
            "hubo_movimiento_real": "si",
        },
        headers={"Origin": "http://evil.com"},
    )
    assert respuesta.status_code == 403
    # Sin Origin = permitir (curl/CLI) y loopback explícito = permitir.
    assert cliente.post(
        "/api/validacion",
        json={
            "sesion": "S1",
            "inicio_ms": inicio,
            "fin_ms": inicio + BUCKET_MS,
            "hubo_movimiento_real": "si",
        },
        headers={"Origin": "http://127.0.0.1:8000"},
    ).status_code == 201


def test_validacion_post_payload_invalido_422(cliente):
    inicio = _bucket_actual()
    base = {
        "sesion": "S1",
        "inicio_ms": inicio,
        "fin_ms": inicio + BUCKET_MS,
        "hubo_movimiento_real": "si",
    }
    assert cliente.post(
        "/api/validacion", json={**base, "hubo_movimiento_real": "quizas"}
    ).status_code == 422
    assert cliente.post(
        "/api/validacion", json={**base, "sesion": "../escape"}
    ).status_code == 422
    assert cliente.post(
        "/api/validacion", json={**base, "fin_ms": inicio}
    ).status_code == 422
    assert cliente.post(
        "/api/validacion", json={**base, "fin_ms": inicio + 31 * 60 * 1000}
    ).status_code == 422
    # Export con sesión fuera de contrato (evita inyección en el filename).
    assert cliente.get(
        "/api/validacion/export", params={"sesion": "../evil"}
    ).status_code == 422


def test_esquema_sesiones_idempotente(tmp_path):
    import esquema

    db = tmp_path / "sesiones.db"
    con = sqlite3.connect(db)
    try:
        con.executescript(esquema.ESQUEMA_SQL)
        con.executescript(esquema.ESQUEMA_SQL)  # segunda pasada: no debe fallar
        nombres = {
            fila[0]
            for fila in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
            )
        }
        con.execute(
            "INSERT INTO sesiones (sesion, inicio_ms, estado) VALUES ('S1', 1, 'activa')"
        )
        con.execute(
            "INSERT INTO sesiones (sesion, inicio_ms, estado) VALUES ('S2', 2, 'cerrada')"
        )
        total = con.execute("SELECT COUNT(*) FROM sesiones").fetchone()[0]
    finally:
        con.close()
    assert {"sesiones", "idx_sesiones_inicio", "idx_sesiones_estado"} <= nombres
    assert total == 2


def test_validacion_ui_panel_ventanas_y_export(cliente):
    html = cliente.get("/").text
    assert 'id="tabla-ventanas"' in html
    assert 'id="tabla-registro-sesiones"' in html
    assert 'id="validacion-export"' in html
    assert 'id="validacion-sesion-activa"' in html
    assert "hubo movimiento real" in html
    js = cliente.get("/static/app.js").text
    assert "/api/validacion/export" in js
    assert "pintarVentanas" in js
    assert "guardarMarcaValidacion" in js
    # Cero innerHTML con datos en la lógica del panel (esc()/textContent).
    assert "innerHTML =" not in js
    assert ".innerHTML" not in js
    # La galería F24 sigue intacta tras añadir el panel.
    assert "/api/imagenes/" in js