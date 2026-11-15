"""Casos BORDE del decoder de imagen MQTT (SPEC §6.2/§7.2, F25) que faltaban en
``test_imagen_mqtt.py`` (revisa el 2026-11-15):

- envelope con campos AUSENTES o con TIPO ERRADO en cada campo obligatorio
  (el contrato no debe lanzar NUNCA: siempre ``descartado`` sin fila ni archivo);
- ``b64`` vacio / fuera del tope contractual; ``sha256`` en mayusculas;
  ``tamano`` 0/negativo; ``ts_ms`` negativo/float/string;
- payload crudo no-JSON, JSON no-objeto, UTF-8 invalido y tipos inesperados;
- campos EXTRA desconocidos: tolerados (el contrato solo fija los obligatorios);
- Fix S (jpegqc 2026-11-15) variantes nuevas: SOF DESPUES del SOS y borde exacto
  de la ventana de 2 KB (``VENTANA_SOF_JPEG``: dentro persiste, fuera descarta);
- "mismo id con b64 distinto": device_id+ts_ms iguales y contenido distinto NO es
  dedupe (el dedupe es por hash de contenido); y reenvio con el sha256 declarado
  SIN recomputar -> descarte por sha;
- bordes INCLUSIVOS de la ventana del FK ``evento_id`` [-15 s, +5 s] (paso 6).

Reusa el ``escenario`` y los helpers de ``test_imagen_mqtt`` (convencion del
frente: mismos nombres, sin duplicar setup).
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from test_imagen_mqtt import (  # fixtures y helpers compartidos
    DEV,
    NOW,
    _archivos,
    _consultar,
    _envelope,
    _imagenes,
    _jpeg,
    _topic,
    _craft_jpeg,
    escenario,  # noqa: F401  (fixture reexportada: tmp DB + allowlist sembrada)
)

CAMPOS_OBLIGATORIOS: tuple[str, ...] = (
    "v",
    "device_id",
    "ts_ms",
    "motivo",
    "tamano",
    "sha256",
    "b64",
)


def _base_envelope(imagen: bytes, *, device_id: str = DEV, ts_ms: int = 2000) -> dict:
    return {
        "v": 1,
        "device_id": device_id,
        "ts_ms": ts_ms,
        "motivo": "onset",
        "tamano": len(imagen),
        "sha256": hashlib.sha256(imagen).hexdigest(),
        "b64": base64.b64encode(imagen).decode("ascii"),
    }


def _nada_persistido(escenario) -> None:
    """Invariante de descarte: ni fila ni archivo en disco."""
    assert _imagenes(escenario.db) == []
    assert _archivos(escenario.datos) == []


# --- 1. Campos ausentes ------------------------------------------------------


@pytest.mark.parametrize("campo", CAMPOS_OBLIGATORIOS)
def test_envelope_campo_ausente_descarta_sin_excepcion(escenario, campo):
    """Cualquier campo obligatorio ausente -> ``descartado`` (nunca KeyError/500)."""
    datos = _base_envelope(_jpeg())
    datos.pop(campo)
    resultado = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), json.dumps(datos).encode("utf-8"), now_ms=NOW
    )
    assert resultado["accion"] == "descartado", (campo, resultado)
    assert resultado["valido"] is False
    _nada_persistido(escenario)


# --- 2. Tipos errados --------------------------------------------------------


@pytest.mark.parametrize(
    "cambio",
    [
        pytest.param({"v": "1"}, id="v-string"),
        pytest.param({"v": None}, id="v-null"),
        pytest.param({"ts_ms": "2000"}, id="ts-string"),
        pytest.param({"ts_ms": 20.5}, id="ts-float"),
        pytest.param({"ts_ms": True}, id="ts-bool"),
        pytest.param({"ts_ms": -1}, id="ts-negativo"),
        pytest.param({"tamano": "12"}, id="tamano-string"),
        pytest.param({"tamano": 0}, id="tamano-cero"),
        pytest.param({"tamano": -5}, id="tamano-negativo"),
        pytest.param({"sha256": 12345}, id="sha-entero"),
        pytest.param({"sha256": "abc"}, id="sha-corto"),
        pytest.param({"b64": 512}, id="b64-entero"),
        pytest.param({"b64": None}, id="b64-null"),
        pytest.param({"b64": ""}, id="b64-vacio"),
        pytest.param({"device_id": DEV.upper()}, id="device-mayusculas"),
        pytest.param({"device_id": DEV + "0"}, id="device-largo-17"),
        pytest.param({"motivo": ["onset"]}, id="motivo-lista"),
        pytest.param({"motivo": None}, id="motivo-null"),
    ],
)
def test_envelope_tipo_erroneo_descarta_sin_excepcion(escenario, cambio):
    imagen = _jpeg()
    resultado = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(imagen, **cambio), now_ms=NOW
    )
    assert resultado["accion"] == "descartado", (cambio, resultado)
    assert resultado["valido"] is False
    _nada_persistido(escenario)


def test_sha256_mayusculas_descarta_por_contrato(escenario):
    """El contrato fija 64 hex MINUSCULAS: el mismo hash en mayusculas se descarta
    (no se normaliza — coherencia con el unique index de BD)."""
    imagen = _jpeg()
    sha_mayus = hashlib.sha256(imagen).hexdigest().upper()
    resultado = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(imagen, sha256=sha_mayus), now_ms=NOW
    )
    assert resultado["accion"] == "descartado"
    _nada_persistido(escenario)


def test_b64_fuera_del_tope_contractual_descarta(escenario):
    """b64 de mas de 16384 caracteres (tope del firmware, SPEC §3.2.1) se corta en
    el contrato, ANTES de decodificar nada."""
    resultado = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg(), b64="A" * 16388), now_ms=NOW
    )
    assert resultado["accion"] == "descartado"
    assert "16384" in (resultado["error"] or "")
    _nada_persistido(escenario)


# --- 3. Payload crudo no-JSON / tipos inesperados ------------------------------


@pytest.mark.parametrize(
    "crudo",
    [
        pytest.param(b"esto no es json", id="no-json"),
        pytest.param(b"\xff\xfe\x00\x01", id="utf8-invalido"),
        pytest.param(b"[1, 2, 3]", id="json-lista"),
        pytest.param(b'"cadena"', id="json-cadena"),
        pytest.param(b"null", id="json-null"),
        pytest.param(12345, id="tipo-entero"),
    ],
)
def test_crudo_invalido_descarta_sin_excepcion(escenario, crudo):
    resultado = escenario.writer.procesar_imagen_mqtt(_topic("image"), crudo, now_ms=NOW)
    assert resultado["accion"] == "descartado", (crudo, resultado)
    _nada_persistido(escenario)


def test_campos_extra_desconocidos_son_tolerados(escenario):
    """El contrato valida los campos obligatorios y TOLERA extras (p. ej. un
    ``firmware`` futuro del nodo no debe romper la ingesta actual)."""
    imagen = _jpeg()
    resultado = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(imagen, firmware="3.2.1", lote=7), now_ms=NOW
    )
    assert resultado["accion"] == "imagen_mqtt", resultado
    assert len(_imagenes(escenario.db)) == 1


def test_procesar_enruta_el_topic_image_al_decoder(escenario):
    """``procesar()`` (la via generica, payload dict ya parseado) delega en el
    decoder F25: mismo resultado que llamar al decoder directamente."""
    imagen = _jpeg()
    resultado = escenario.writer.procesar(
        _topic("image"), _base_envelope(imagen), now_ms=NOW
    )
    assert resultado["accion"] == "imagen_mqtt", resultado
    assert resultado["kind"] == "image"
    assert len(_imagenes(escenario.db)) == 1


# --- 4. Fix S: variantes nuevas del walk estructural ---------------------------

_SOF0 = (
    b"\xff\xc0"
    + (17).to_bytes(2, "big")
    + b"\x08\x00\x1e\x00\x28\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
)


def _craft_sof_tras_sos() -> bytes:
    """JPEG con el marcador SOF0 colocado DESPUES del segmento SOS (walk debe
    detenerse en SOS sin haber visto nunca un SOF)."""
    base = _craft_jpeg(con_sof=False)
    sos = base.find(b"\xff\xda")
    largo_sos = int.from_bytes(base[sos + 2 : sos + 4], "big")
    corte = sos + 2 + largo_sos  # primer byte tras el segmento SOS
    roto = base[:corte] + _SOF0 + base[corte:]
    # guarda de la construccion: el SOS precede al UNICO SOF (JPEG ilegal).
    assert roto.find(b"\xff\xda") + 3 < roto.find(b"\xff\xc0")
    return roto


def _craft_app0(largo_app0: int) -> bytes:
    """JPEG determinista: SOI + APP0 de ``largo_app0`` + SOF0 + DHT + SOS +
    entropy + EOI. El walk aterriza en el SOF en offset ``3 + largo_app0 + 1``
    (k=3 del codigo E0; el siguiente FF queda en k + largo + 1)."""
    return (
        b"\xff\xd8"
        + b"\xff\xe0"
        + largo_app0.to_bytes(2, "big")
        + b"\x00" * (largo_app0 - 2)
        + _SOF0
        + b"\xff\xc4"
        + (4).to_bytes(2, "big")
        + b"\x00\x00"
        + b"\xff\xda"
        + (8).to_bytes(2, "big")
        + b"\x01\x01\x00\x00\x3f\x00"
        + b"\x7f\xf7\x9b\xa0" * 4
        + b"\xff\xd9"
    )


def test_sof_despues_de_sos_descarta_estructura(escenario, caplog):
    """Variante Fix S: SOF existe pero DETRAS del SOS => JPEG ILEGAL, se descarta.

    Semantica exacta del guard (``_jpeg_estructura_ok``, ingesta_mqtt.py:190): el
    segment-walk de cabecera TERMINA en el primer SOS, asi que un SOF posterior
    nunca se inspecciona y la imagen se rechaza con ``imagen_descartada_estructura``
    y el motivo literal (ingesta_mqtt.py:224) interpolado en el WARNING:
    ``sin marcador SOF antes de SOS``. No confundir con el motivo del borde de la
    ventana de 2 KB (``sin SOF en los primeros 2048 B``): aqui el SOS esta en el
    offset 326 (< 2048), de modo que la regla que dispara es la del SOS y no la
    de la ventana.
    """
    import logging

    roto = _craft_sof_tras_sos()
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(roto), now_ms=NOW
        )
    assert resultado["accion"] == "imagen_descartada_estructura", resultado
    assert any("sin marcador SOF antes de SOS" in r.message for r in caplog.records)
    _nada_persistido(escenario)


def test_ventana_2kb_sof_justo_dentro_persiste(escenario):
    """Borde inferior de la ventana: el walk aterriza en offset 2047 (< 2048) y
    ve el SOF -> JPEG valido persiste (no hay falso positivo en el limite)."""
    datos = _craft_app0(2043)  # 3 + 2043 + 1 = 2047
    assert 3 + 2043 + 1 == 2047  # guarda de la aritmetica del walk
    resultado = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(datos), now_ms=NOW
    )
    assert resultado["accion"] == "imagen_mqtt", resultado
    assert len(_imagenes(escenario.db)) == 1


def test_ventana_2kb_sof_justo_fuera_descarta(escenario, caplog):
    """Borde superior: el walk alcanza offset 2048 SIN SOF visto -> descarte con
    el motivo de la ventana (``VENTANA_SOF_JPEG``)."""
    import logging

    datos = _craft_app0(2044)  # 3 + 2044 + 1 = 2048 == umbral
    assert 3 + 2044 + 1 == 2048
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(datos), now_ms=NOW
        )
    assert resultado["accion"] == "imagen_descartada_estructura", resultado
    assert any("sin SOF en los primeros 2048 B" in r.message for r in caplog.records)
    _nada_persistido(escenario)


# --- 5. Dedupe por contenido, no por identidad --------------------------------


def test_mismo_device_y_ts_contenido_distinto_no_es_dedupe(escenario):
    """REGLA del contrato: el dedupe es por hash de CONTENIDO. Dos imagenes
    distintas del mismo device con el mismo ``ts_ms`` (mismo "id" logico) son dos
    filas validas: no se descartan ni se fusionan."""
    primera = _jpeg((10, 10, 10))
    segunda = _jpeg((200, 50, 50))
    r1 = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(primera, ts_ms=2000), now_ms=NOW
    )
    assert r1["accion"] == "imagen_mqtt", r1
    # El rate-limit (1/10 s) es lo unico que separa ambas: +11 s.
    r2 = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(segunda, ts_ms=2000), now_ms=NOW + 11_000
    )
    assert r2["accion"] == "imagen_mqtt", r2
    filas = _consultar(escenario.db, "SELECT hash_sha256, ts_ms FROM imagenes ORDER BY id")
    assert len(filas) == 2
    assert filas[0][0] != filas[1][0]  # hashes distintos
    assert filas[0][1] == filas[1][1]  # mismo ts_ms resuelto por R3
    assert len(_archivos(escenario.datos)) == 2


def test_mismo_sha_declarado_b64_distinto_descarta_sha(escenario):
    """Reenvio con el sha256 NO recomputado (el nodo cambia la foto y reusa el
    hash viejo) -> descarte por sha, NUNca persiste el contenido nuevo."""
    imagen_a = _jpeg((5, 5, 5))
    imagen_b = _jpeg((90, 90, 90))
    env = _envelope(
        imagen_a, b64=base64.b64encode(imagen_b).decode("ascii"), tamano=len(imagen_b)
    )
    resultado = escenario.writer.procesar_imagen_mqtt(_topic("image"), env, now_ms=NOW)
    assert resultado["accion"] == "imagen_descartada_sha256", resultado
    _nada_persistido(escenario)


# --- 6. Bordes inclusivos de la ventana del FK --------------------------------


def _escenario_onset(escenario):
    """Siembra el reloj (health ts_dev=6000 -> epoca NOW-6000) y un onset en
    ts_dev=7000 => onset_ts = NOW+1000. Devuelve el writer."""
    writer = escenario.writer
    assert writer.procesar(
        _topic("health"),
        {"status": "ok", "online": True, "uptime_s": 6, "timestamp_ms": 6000},
        now_ms=NOW,
    )["accion"].startswith("health_")
    writer.procesar(
        _topic("motion"), {"timestamp_ms": 6000, "state": "idle", "score": 0.05}, now_ms=NOW
    )
    writer.procesar(
        _topic("motion"), {"timestamp_ms": 7000, "state": "motion", "score": 0.9}, now_ms=NOW
    )
    evento_id = _consultar(
        escenario.db,
        "SELECT id FROM eventos WHERE tipo='MOVIMIENTO_INICIO' ORDER BY id DESC LIMIT 1",
    )[0][0]
    return writer, evento_id


def test_fk_ventana_borde_menos_15s_incluida_y_15s1_excluida(escenario):
    """[ts-15 s, ts+5 s] es CERRADO por la izquierda: onset exactamente 15 000 ms
    antes enlaza; 15 001 ms antes queda fuera (evento_id NULL)."""
    writer, evento_id = _escenario_onset(escenario)
    # img_ts = NOW+16000 -> onset NOW+1000 == img_ts - 15000 (dentro, inclusivo).
    dentro = writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg((11, 12, 13)), ts_ms=22000), now_ms=NOW
    )
    assert dentro["accion"] == "imagen_mqtt"
    assert _consultar(
        escenario.db, "SELECT evento_id FROM imagenes ORDER BY id DESC LIMIT 1"
    ) == [(evento_id,)]
    # img_ts = NOW+16001 -> onset == img_ts - 15001 (fuera).
    fuera = writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg((21, 22, 23)), ts_ms=22001), now_ms=NOW + 11_000
    )
    assert fuera["accion"] == "imagen_mqtt"
    assert _consultar(
        escenario.db, "SELECT evento_id FROM imagenes ORDER BY id DESC LIMIT 1"
    ) == [(None,)]


def test_fk_ventana_borde_mas_5s_incluido_y_5s1_excluido(escenario):
    """Cerrado por la derecha: onset exactamente 5 000 ms DESPUES de la imagen
    enlaza (foto tomada justo antes de que el nodo declarara el onset); a 5 001 ms
    queda fuera."""
    writer, evento_id = _escenario_onset(escenario)
    # img_ts = NOW-4000 (ts_dev 2000) -> onset NOW+1000 == img_ts + 5000 (dentro).
    dentro = writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg((31, 32, 33)), ts_ms=2000), now_ms=NOW
    )
    assert dentro["accion"] == "imagen_mqtt"
    assert _consultar(
        escenario.db, "SELECT evento_id FROM imagenes ORDER BY id DESC LIMIT 1"
    ) == [(evento_id,)]
    # img_ts = NOW-4001 (ts_dev 1999) -> onset == img_ts + 5001 (fuera).
    fuera = writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg((41, 42, 43)), ts_ms=1999), now_ms=NOW + 11_000
    )
    assert fuera["accion"] == "imagen_mqtt"
    assert _consultar(
        escenario.db, "SELECT evento_id FROM imagenes ORDER BY id DESC LIMIT 1"
    ) == [(None,)]
