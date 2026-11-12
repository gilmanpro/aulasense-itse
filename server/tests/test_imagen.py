"""Tests del receptor HTTP de imagen (SPEC §7.1) y de la purga de retención (F24).

Todo corre en loopback con puerto efímero (``127.0.0.1:0``) y un token FIJO DE
TEST: jamás se usa el token real de ``.env``. El writer es el de verdad
(``IngestaWriter``) con su hilo ``run_forever``: se prueba de punta a punta que el
receptor encola en la MISMA cola (writer único preservado).

Cobertura: 201+archivo+fila, 401, 413, 422 (magic/device), 429, FK evento_id,
magic+hash, purga con ``imagen_purgada`` sin borrar la fila + idempotencia,
validador ``ia_imagen`` y topic ``aulasense_config`` (enum/detalle/secretos).
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import queue
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import esquema
from ingesta_mqtt import IngestaWriter

ROOT = Path(__file__).resolve().parents[2]  # aulasense/
PREFIJO = "aulasense/v1/devices"
DEV = "0123456789abcdef"
DEV2 = "fedcba9876543210"
NOW = 1_760_000_000_000
TOKEN_TEST = "token-f24b1-solo-para-tests"  # NUNCA el token real del .env


def _cargar_modulo(nombre: str, ruta: Path):
    """Carga un módulo por ruta (tools/ no es paquete instalable)."""
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


purga_imagenes = _cargar_modulo("purga_imagenes_f24", ROOT / "tools" / "purga_imagenes.py")


def _jpeg(ancho: int = 320, alto: int = 240) -> bytes:
    """JPEG sintético real con PIL (QVGA por defecto)."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (ancho, alto), (30, 60, 90)).save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


def _topic(suffix: str, device_id: str = DEV) -> str:
    return f"{PREFIJO}/{device_id}/{suffix}"


def _device_payload(device_id: str, label: str) -> dict:
    return {
        "device_id": device_id,
        "name": "aulasense",
        "label": label,
        "frontend": "native",
        "firmware": "3.2.0",
        "chip": "esp32s3",
        "csi_profile": "ht20",
    }


def _consultar(db: Path, sql: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


@pytest.fixture
def entorno(tmp_path):
    """Writer real + receptor en puerto efímero con token de test."""
    from receptor_imagen import ReceptorImagen

    cola: "queue.Queue" = queue.Queue(maxsize=1000)
    writer = IngestaWriter(tmp_path / "aulasense.db", tmp_path / "export.csv", cola=cola)
    # Allowlist del receptor: los devices deben existir en ``dispositivos``.
    assert writer.procesar(_topic("device"), _device_payload(DEV, "Aula-Test-1"), now_ms=NOW)[
        "accion"
    ] == "device_upsert"
    assert writer.procesar(
        _topic("device", DEV2), _device_payload(DEV2, "Aula-Test-2"), now_ms=NOW
    )["accion"] == "device_upsert"

    hilo_writer = threading.Thread(
        target=writer.run_forever, args=(cola,), name="writer-test", daemon=True
    )
    hilo_writer.start()

    datos_dir = tmp_path / "datos"
    receptor = ReceptorImagen("127.0.0.1", 0, TOKEN_TEST, writer, PREFIJO, datos_dir)
    servidor = receptor.iniciar_en_hilo()
    puerto = int(servidor.server_address[1])

    yield SimpleNamespace(
        receptor=receptor,
        writer=writer,
        servidor=servidor,
        puerto=puerto,
        datos=datos_dir,
        db=tmp_path / "aulasense.db",
        cola=cola,
    )

    servidor.shutdown()
    servidor.server_close()
    cola.put(None)
    hilo_writer.join(timeout=5)
    writer.cerrar()


def _url(entorno, ruta: str = "/internal/imagen") -> str:
    return f"http://127.0.0.1:{entorno.puerto}{ruta}"


def _post(
    entorno,
    imagen: bytes,
    *,
    device: str = DEV,
    ts_ms: int = 1000,
    token: str | None = TOKEN_TEST,
    headers_extra: dict | None = None,
):
    headers = {
        "Content-Type": "image/jpeg",
        "X-Device-Id": device,
        "X-Ts-Ms": str(ts_ms),
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if headers_extra:
        headers.update(headers_extra)
    return requests.post(_url(entorno), data=imagen, headers=headers, timeout=10)


def _archivos(datos: Path) -> list[Path]:
    raiz = datos / "imagenes"
    if not raiz.exists():
        return []
    return [ruta for ruta in raiz.rglob("*") if ruta.is_file()]


# --- 1. Receptor: casos felices --------------------------------------------


def test_upload_valido_201_archivo_y_fila(entorno):
    imagen = _jpeg()
    respuesta = _post(entorno, imagen, ts_ms=1500)

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["ok"] is True and isinstance(cuerpo["imagen_id"], int)

    filas = _consultar(
        entorno.db,
        "SELECT id, device_id, ruta, hash_sha256, tamano_bytes, ancho, alto, "
        "evento_id, ia_persona, ia_score, imagen_purgada, origen FROM imagenes",
    )
    assert len(filas) == 1
    fila = filas[0]
    assert fila[0] == cuerpo["imagen_id"]
    assert fila[1] == DEV
    ruta_relativa = fila[2]
    assert ruta_relativa.startswith(f"imagenes/{DEV}/")
    assert ruta_relativa.endswith(".jpg")
    archivo = entorno.datos / ruta_relativa
    assert archivo.is_file()
    assert archivo.read_bytes() == imagen
    assert fila[3] == hashlib.sha256(imagen).hexdigest()
    assert len(fila[3]) == 64
    assert fila[4] == len(imagen)
    assert (fila[5], fila[6]) == (320, 240)
    assert fila[7] is None and fila[8] is None and fila[9] is None
    assert fila[10] == 0 and fila[11] == "real"


def test_upload_multipart_metadata_ia(entorno):
    respuesta = requests.post(
        _url(entorno),
        files={"imagen": ("evidencia.jpg", _jpeg(), "image/jpeg")},
        data={
            "device_id": DEV,
            "ts_ms": "1700",
            "ia_persona": "1",
            "ia_score": "0.83",
        },
        headers={"Authorization": f"Bearer {TOKEN_TEST}"},
        timeout=10,
    )
    assert respuesta.status_code == 201, respuesta.text
    filas = _consultar(
        entorno.db, "SELECT ia_persona, ia_score FROM imagenes ORDER BY id DESC LIMIT 1"
    )
    assert filas == [(1, pytest.approx(0.83))]


def test_magic_y_hash_coherentes_en_disco(entorno):
    imagen = _jpeg()
    assert _post(entorno, imagen).status_code == 201
    (archivo,) = _archivos(entorno.datos)
    contenido = archivo.read_bytes()
    assert contenido.startswith(b"\xff\xd8\xff")
    assert hashlib.sha256(contenido).hexdigest().startswith(archivo.stem.split("_")[-1])


# --- 2. Receptor: rechazos -------------------------------------------------


def test_sin_token_401(entorno):
    assert _post(entorno, _jpeg(), token=None).status_code == 401
    assert _archivos(entorno.datos) == []
    assert _consultar(entorno.db, "SELECT COUNT(*) FROM imagenes")[0][0] == 0


def test_token_incorrecto_401(entorno):
    assert _post(entorno, _jpeg(), token="token-equivocado").status_code == 401
    assert _archivos(entorno.datos) == []


def test_imagen_mayor_64kb_413(entorno):
    grande = b"\xff\xd8\xff" + b"\x00" * (70 * 1024)
    respuesta = _post(entorno, grande)
    assert respuesta.status_code == 413, respuesta.text
    assert respuesta.json()["ok"] is False
    assert _archivos(entorno.datos) == []
    assert _consultar(entorno.db, "SELECT COUNT(*) FROM imagenes")[0][0] == 0


def test_no_jpeg_422(entorno):
    respuesta = _post(entorno, b"GIF89a" + b"\x00" * 100)
    assert respuesta.status_code == 422
    assert "JPEG" in respuesta.json()["error"]
    assert _archivos(entorno.datos) == []


def test_device_desconocido_o_malformado_422(entorno):
    desconocido = _post(entorno, _jpeg(), device="ffffffffffffffff")
    malformado = _post(entorno, _jpeg(), device="NO-ES-HEX")
    assert desconocido.status_code == 422
    assert malformado.status_code == 422
    assert _archivos(entorno.datos) == []


def test_rate_limit_429_segundo_upload(entorno):
    primero = _post(entorno, _jpeg(), ts_ms=1000)
    segundo = _post(entorno, _jpeg(), ts_ms=2000)
    assert primero.status_code == 201, primero.text
    assert segundo.status_code == 429, segundo.text
    assert _consultar(entorno.db, "SELECT COUNT(*) FROM imagenes")[0][0] == 1


def test_writer_muerto_500_sin_archivo_huerfano(tmp_path):
    """Writer cerrado -> 500 y ningun archivo en datos/imagenes."""
    from receptor_imagen import ReceptorImagen

    writer = IngestaWriter(tmp_path / "muerto.db", tmp_path / "muerto.csv")
    writer.procesar(_topic("device"), _device_payload(DEV, "Aula-Muerta"), now_ms=NOW)
    receptor = ReceptorImagen("127.0.0.1", 0, TOKEN_TEST, writer, PREFIJO, tmp_path / "datos")
    servidor = receptor.iniciar_en_hilo()
    try:
        puerto = int(servidor.server_address[1])
        writer.cerrar()  # simula writer caido
        respuesta = requests.post(
            f"http://127.0.0.1:{puerto}/internal/imagen",
            data=_jpeg(),
            headers={
                "Content-Type": "image/jpeg",
                "X-Device-Id": DEV,
                "X-Ts-Ms": "1000",
                "Authorization": f"Bearer {TOKEN_TEST}",
            },
            timeout=10,
        )
        assert respuesta.status_code == 500
        assert _archivos(tmp_path / "datos") == []
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_receptor_desactivado_por_env(tmp_path, monkeypatch):
    """HOST vacio o token vacio -> ``iniciar_receptor_desde_env`` devuelve None."""
    from receptor_imagen import ReceptorImagen, iniciar_receptor_desde_env

    writer = IngestaWriter(tmp_path / "off.db", tmp_path / "off.csv")
    try:
        monkeypatch.setenv("AULASENSE_UPLOAD_HOST", "")
        assert iniciar_receptor_desde_env(writer) is None
        monkeypatch.setenv("AULASENSE_UPLOAD_HOST", "127.0.0.1")
        monkeypatch.setenv("AULASENSE_UPLOAD_TOKEN", "")
        assert iniciar_receptor_desde_env(writer) is None
        # Comodin de bind: prohibido explicitamente.
        with pytest.raises(ValueError):
            ReceptorImagen("0.0.0.0", 0, TOKEN_TEST, writer, PREFIJO, tmp_path).crear_servidor()
        # Host vacio: tampoco se levanta.
        with pytest.raises(ValueError):
            ReceptorImagen("", 0, TOKEN_TEST, writer, PREFIJO, tmp_path).crear_servidor()
    finally:
        writer.cerrar()


# --- 3. FK evento_id -------------------------------------------------------


def test_evento_id_fk_resuelto_y_sin_evento_null(entorno):
    writer = entorno.writer
    # Evento real derivado (baseline idle + motion = MOVIMIENTO_INICIO).
    writer.procesar(
        _topic("motion"), {"timestamp_ms": 1000, "state": "idle", "score": 0.05}, now_ms=NOW
    )
    writer.procesar(
        _topic("motion"), {"timestamp_ms": 1250, "state": "motion", "score": 0.9}, now_ms=NOW + 250
    )
    evento_id = _consultar(entorno.db, "SELECT id FROM eventos ORDER BY id DESC LIMIT 1")[0][0]

    con_evento = _post(entorno, _jpeg(), ts_ms=1500, headers_extra={"X-Evento-Id": str(evento_id)})
    assert con_evento.status_code == 201, con_evento.text
    fila = _consultar(
        entorno.db, "SELECT evento_id FROM imagenes WHERE id = ?", (con_evento.json()["imagen_id"],)
    )
    assert fila == [(evento_id,)]

    # Segundo device (sin FK): la fila sale con evento_id null.
    sin_evento = _post(entorno, _jpeg(), device=DEV2, ts_ms=1500)
    assert sin_evento.status_code == 201, sin_evento.text
    fila = _consultar(
        entorno.db, "SELECT evento_id FROM imagenes WHERE id = ?", (sin_evento.json()["imagen_id"],)
    )
    assert fila == [(None,)]


def test_evento_id_inexistente_422(entorno):
    respuesta = _post(entorno, _jpeg(), headers_extra={"X-Evento-Id": "999999"})
    assert respuesta.status_code == 422
    assert _archivos(entorno.datos) == []


# --- 4. Purga --------------------------------------------------------------


def _envejecer(entorno, dias: int = 40) -> None:
    corte = NOW + 1_000_000 - dias * 24 * 60 * 60 * 1000
    con = sqlite3.connect(str(entorno.db))
    try:
        con.execute("UPDATE imagenes SET ts_ms = ?", (corte,))
        con.commit()
    finally:
        con.close()


def test_purga_marca_purgada_y_conserva_fila(entorno, capsys):
    assert _post(entorno, _jpeg()).status_code == 201
    (archivo,) = _archivos(entorno.datos)
    _envejecer(entorno)

    codigo = purga_imagenes.main(
        [
            "--db",
            str(entorno.db),
            "--datos",
            str(entorno.datos),
            "--dias",
            "30",
        ]
    )
    salida = capsys.readouterr().out
    assert codigo == 0
    assert "1 imagenes purgadas" in salida
    assert not archivo.exists()
    filas = _consultar(entorno.db, "SELECT COUNT(*), imagen_purgada FROM imagenes")
    assert filas == [(1, 1)]  # fila CONSERVADA y marcada (nunca DELETE)


def test_purga_idempotente_segunda_corrida_cero(entorno, capsys):
    assert _post(entorno, _jpeg()).status_code == 201
    _envejecer(entorno)
    args = [
        "--db",
        str(entorno.db),
        "--datos",
        str(entorno.datos),
        "--dias",
        "30",
    ]
    assert purga_imagenes.main(args) == 0
    capsys.readouterr()
    assert purga_imagenes.main(args) == 0
    salida = capsys.readouterr().out
    assert "0 imagenes purgadas" in salida
    assert _consultar(entorno.db, "SELECT COUNT(*) FROM imagenes")[0][0] == 1


def test_purga_dry_run_no_toca_nada(entorno, capsys):
    assert _post(entorno, _jpeg()).status_code == 201
    (archivo,) = _archivos(entorno.datos)
    _envejecer(entorno)
    codigo = purga_imagenes.main(
        ["--db", str(entorno.db), "--datos", str(entorno.datos), "--dias", "30", "--dry-run"]
    )
    assert codigo == 0
    assert "dry-run: 1 filas pendientes" in capsys.readouterr().out
    assert archivo.exists()
    assert _consultar(entorno.db, "SELECT imagen_purgada FROM imagenes")[0][0] == 0


# --- 5. Validadores F24 (esquema.py) --------------------------------------


def test_validar_ia_imagen_ok_y_tipo_errado():
    base = {"timestamp_ms": 1000, "state": "motion", "score": 0.7}
    ausente = esquema.validar(_topic("motion"), base)
    con_ia = esquema.validar(
        _topic("motion"), {**base, "ia_imagen": {"persona": True, "score": 0.91}}
    )
    persona_mala = esquema.validar(
        _topic("motion"), {**base, "ia_imagen": {"persona": "si", "score": 0.5}}
    )
    score_fuera = esquema.validar(
        _topic("motion"), {**base, "ia_imagen": {"persona": False, "score": 1.5}}
    )
    no_objeto = esquema.validar(_topic("motion"), {**base, "ia_imagen": "persona"})
    nulo = esquema.validar(_topic("motion"), {**base, "ia_imagen": None})

    assert ausente["valido"] is True and ausente["kind"] == "motion"
    assert con_ia["valido"] is True
    assert nulo["valido"] is True
    assert persona_mala["valido"] is False and "ia_imagen" in persona_mala["error"]
    assert score_fuera["valido"] is False and "ia_imagen" in score_fuera["error"]
    assert no_objeto["valido"] is False and "ia_imagen" in no_objeto["error"]


def test_aulasense_config_valida_y_persiste(entorno):
    writer = entorno.writer
    writer.procesar(
        _topic("health"),
        {"status": "ok", "online": True, "uptime_s": 5, "timestamp_ms": 5000},
        now_ms=NOW,
    )
    payload = {
        "timestamp_ms": 6000,
        "evento": "CFG_GUARDADA",
        "detalle": "ssid_len=12 pass_len=0",
    }
    resultado = writer.procesar(_topic("aulasense_config"), payload, now_ms=NOW + 1000)
    assert resultado["valido"] is True and resultado["kind"] == "aulasense_config"
    assert resultado["accion"] == "config_evento"

    filas = _consultar(entorno.db, "SELECT tipo, detalle, ts_ms FROM config_eventos")
    assert len(filas) == 1
    assert filas[0][0] == "CFG_GUARDADA"
    assert filas[0][1] == "ssid_len=12 pass_len=0"
    assert abs(filas[0][2] - (NOW + 1000)) <= 1  # R3: epoca_boot + timestamp_ms


def test_aulasense_config_rechaza_enum_detalle_y_secretos():
    base = {"timestamp_ms": 10, "evento": "AP_FALLBACK", "detalle": "reintentos=3"}
    assert esquema.validar(_topic("aulasense_config"), base)["valido"] is True

    enum_malo = esquema.validar(_topic("aulasense_config"), {**base, "evento": "OTRO"})
    detalle_largo = esquema.validar(
        _topic("aulasense_config"), {**base, "detalle": "x" * 121}
    )
    detalle_tipo = esquema.validar(_topic("aulasense_config"), {**base, "detalle": 42})
    ssid = esquema.validar(
        _topic("aulasense_config"), {**base, "ssid": "aula-2g"}
    )
    pass_vacia = esquema.validar(
        _topic("aulasense_config"), {**base, "password": ""}
    )
    solo_longitudes = esquema.validar(
        _topic("aulasense_config"), {**base, "ssid_len": 12, "token_num": 32}
    )
    anidado = esquema.validar(
        _topic("aulasense_config"), {**base, "config": {"token": "abc123"}}
    )

    assert enum_malo["valido"] is False and "evento" in enum_malo["error"]
    assert detalle_largo["valido"] is False and "120" in detalle_largo["error"]
    assert detalle_tipo["valido"] is False and "detalle" in detalle_tipo["error"]
    assert ssid["valido"] is False and "secreto" in ssid["error"]
    assert pass_vacia["valido"] is True  # valor vacio = metadato, no secreto
    assert solo_longitudes["valido"] is True
    assert anidado["valido"] is False and "secreto" in anidado["error"]
    # El detalle nunca repite el valor del secreto en el mensaje de error.
    assert "aula-2g" not in (ssid["error"] or "")


# --- regresión fix coordinador: allowlist consultada EN VIVO (anti-carrera arranque) ---

DEV3 = "d3adbeef12345678"


def test_allowlist_se_refresca_en_vivo_sin_reiniciar_receptor(entorno):
    """F24-c real: en una DB nueva el retained `device` llega DESPUÉS de construir
    el receptor; el upload no debe dar 422 permanente tras registrar el device."""
    # 1) device aún desconocido -> 422 estricto
    r = _post(entorno, _jpeg(), device=DEV3, ts_ms=2000)
    assert r.status_code == 422, r.text
    # 2) llega el retained device por la via normal de la ingesta (procesar)
    assert entorno.writer.procesar(
        _topic("device", DEV3), _device_payload(DEV3, "Aula-Race"), now_ms=NOW
    )["accion"] == "device_upsert"
    # 3) upload sin reiniciar el receptor -> 201 y la allowlist en memoria se actualiza
    r2 = _post(entorno, _jpeg(), device=DEV3, ts_ms=2500)
    assert r2.status_code == 201, r2.text
    assert DEV3 in entorno.receptor._allowlist