"""Tests del decoder de imagen MQTT (SPEC §6.2/§7.2) y de la migración §8.2 (F25-d1).

Sin broker salvo el roundtrip final: la cola se alimenta con
``IngestaWriter.procesar_imagen_mqtt`` (o con la cola real cuando se prueba la
invariante de writer único) y el reloj del PC se inyecta con ``now_ms`` para que
los tests sean deterministas. NUNCA se toca ``server/datos/*.db``: todo va a
``tmp_path`` y ``AULASENSE_DATOS_DIR`` se apunta al tmp.

El roundtrip con broker real publica en un topic de prueba EXCLUSIVO
``probe-<hex>`` (jamás en ``aulasense/v1/devices/*`` de producción), recibe el
envelope y lo pasa por el despachador real (cola → writer → archivo + fila). Si
el broker no es alcanzable o no devuelve eco, el test se SALTA y se reporta.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import queue
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import esquema
import ingesta_mqtt
from ingesta_mqtt import IngestaWriter

PREFIJO = "aulasense/v1/devices"
DEV = "0123456789abcdef"
DEV2 = "fedcba9876543210"
DEV_NUEVO = "aaaabbbbccccdddd"
# Instante de referencia del PC (epoch ms) inyectado en todos los tests de reloj.
NOW = 1_760_000_000_000

# Columnas F25 que la migración §8.2 añade a ``imagenes``.
COLUMNAS_F25 = {
    "canal",
    "descripcion",
    "etiquetas",
    "vision_estado",
    "vision_modelo",
    "vision_ts_ms",
    "vision_tokens",
}


# --- Helpers ---------------------------------------------------------------


def _jpeg(color: tuple[int, int, int] = (30, 60, 90)) -> bytes:
    """JPEG sintético real con PIL (QVGA, ~1-2 KB)."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (320, 240), color).save(buffer, format="JPEG", quality=70)
    return buffer.getvalue()


def _topic(suffix: str, device_id: str = DEV) -> str:
    return f"{PREFIJO}/{device_id}/{suffix}"


def _envelope(
    imagen: bytes,
    *,
    device_id: str = DEV,
    ts_ms: int = 2000,
    **cambios,
) -> bytes:
    """Envelope §6.2 con los campos coherentes; ``cambios`` rompe lo que interese."""
    datos = {
        "v": 1,
        "device_id": device_id,
        "ts_ms": ts_ms,
        "motivo": "onset",
        "tamano": len(imagen),
        "sha256": hashlib.sha256(imagen).hexdigest(),
        "b64": base64.b64encode(imagen).decode("ascii"),
    }
    datos.update(cambios)
    return json.dumps(datos).encode("utf-8")


def _device_payload(device_id: str, label: str = "Aula-Test") -> dict:
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


def _imagenes(db: Path) -> list[tuple]:
    return _consultar(
        db,
        "SELECT device_id, ruta, hash_sha256, tamano_bytes, evento_id, "
        "canal, vision_estado, origen, imagen_purgada FROM imagenes ORDER BY id",
    )


def _archivos(datos: Path) -> list[Path]:
    raiz = datos / "imagenes"
    if not raiz.exists():
        return []
    return [ruta for ruta in raiz.rglob("*") if ruta.is_file()]


def _esperar(con: sqlite3.Connection, sql: str, params: tuple = (), timeout: float = 5.0):
    """Sondea ``sql`` hasta que devuelva filas (o se agote el timeout)."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        filas = con.execute(sql, params).fetchall()
        if filas:
            return filas
        time.sleep(0.02)
    return con.execute(sql, params).fetchall()


@pytest.fixture
def escenario(tmp_path, monkeypatch):
    """Writer real sobre tmp_path con la allowlist sembrada (device + health)."""
    monkeypatch.setenv("AULASENSE_DATOS_DIR", str(tmp_path / "datos"))
    cola: "queue.Queue" = queue.Queue(maxsize=1000)
    writer = IngestaWriter(tmp_path / "aulasense.db", tmp_path / "export.csv", cola=cola)
    assert writer.procesar(_topic("device"), _device_payload(DEV, "Aula-1"), now_ms=NOW)[
        "accion"
    ] == "device_upsert"
    assert writer.procesar(
        _topic("device", DEV2), _device_payload(DEV2, "Aula-2"), now_ms=NOW
    )["accion"] == "device_upsert"
    # health fija la epoca_boot (R3): ts_ms del nodo 1000 -> epoca NOW.
    assert writer.procesar(
        _topic("health"),
        {"status": "ok", "online": True, "uptime_s": 10, "timestamp_ms": 1000},
        now_ms=NOW,
    )["accion"].startswith("health_")

    yield SimpleNamespace(
        writer=writer,
        db=tmp_path / "aulasense.db",
        datos=tmp_path / "datos",
        cola=cola,
    )
    writer.cerrar()


# --- 1. Camino feliz -------------------------------------------------------


def test_envelope_valido_archivo_y_fila_canal_mqtt(escenario, caplog):
    imagen = _jpeg()
    with caplog.at_level(logging.INFO, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(imagen, ts_ms=2000), now_ms=NOW
        )
    assert resultado["accion"] == "imagen_mqtt", resultado
    assert resultado["kind"] == "image" and resultado["valido"] is True

    (fila,) = _imagenes(escenario.db)
    device_id, ruta, sha, tamano, evento_id, canal, vision, origen, purgada = fila
    assert device_id == DEV
    assert canal == "mqtt" and vision == "pendiente"
    assert origen == "real" and purgada == 0
    assert sha == hashlib.sha256(imagen).hexdigest()
    assert tamano == len(imagen)
    assert evento_id is None  # sin onset en la ventana
    assert ruta.startswith(f"imagenes/{DEV}/") and ruta.endswith(".jpg")

    archivo = escenario.datos / ruta
    assert archivo.is_file()
    assert archivo.read_bytes() == imagen
    assert archivo.name.endswith(f"{sha[:8]}.jpg")

    # R3: epoca_boot = NOW - 1000 (health) => ts_ms = NOW + (2000 - 1000).
    fila_ts = _consultar(escenario.db, "SELECT ts_ms FROM imagenes")[0][0]
    assert fila_ts == NOW + 1000


# --- 2. Descartes de contenido --------------------------------------------


def test_b64_corrupto_descarta_con_log(escenario, caplog):
    imagen = _jpeg()
    roto = base64.b64encode(imagen).decode("ascii")[:-8] + "@@@@AAAA"
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(imagen, b64=roto), now_ms=NOW
        )
    assert resultado["accion"] == "descartado"
    assert "b64" in (resultado["error"] or "")
    assert _imagenes(escenario.db) == []
    assert _archivos(escenario.datos) == []
    assert any("imagen MQTT descartada" in r.message for r in caplog.records)


def test_b64_con_saltos_de_linea_estricto_descarta(escenario):
    """El contrato exige base64 SIN saltos de línea (§6.2)."""
    imagen = _jpeg()
    con_saltos = base64.encodebytes(imagen).decode("ascii")  # 76 cols + \n
    resultado = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(imagen, b64=con_saltos), now_ms=NOW
    )
    assert resultado["accion"] == "descartado"
    assert _imagenes(escenario.db) == []


def test_sha256_mismatch_descarta(escenario, caplog):
    imagen = _jpeg()
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(imagen, sha256="0" * 64), now_ms=NOW
        )
    assert resultado["accion"] == "imagen_descartada_sha256"
    assert _imagenes(escenario.db) == []
    assert _archivos(escenario.datos) == []
    assert any("sha256" in r.message for r in caplog.records)


def test_tamano_incoherente_descarta(escenario, caplog):
    imagen = _jpeg()
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(imagen, tamano=len(imagen) + 10), now_ms=NOW
        )
    assert resultado["accion"] == "imagen_descartada_tamano"
    assert _imagenes(escenario.db) == []
    assert _archivos(escenario.datos) == []
    assert any("tamano" in r.message for r in caplog.records)


def test_magic_jpeg_invalido_descarta(escenario):
    no_jpeg = b"GIF89a" + b"\x00" * 200
    resultado = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(no_jpeg), now_ms=NOW
    )
    assert resultado["accion"] == "imagen_descartada_magic"
    assert _imagenes(escenario.db) == []
    assert _archivos(escenario.datos) == []


# --- 3. Descartes de envelope ---------------------------------------------


def test_envelope_mayor_64kb_descarta(escenario, caplog):
    imagen = _jpeg()
    relleno = _envelope(imagen, relleno="x" * (66 * 1024))
    assert len(relleno) > 64 * 1024
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), relleno, now_ms=NOW
        )
    assert resultado["accion"] == "descartado"
    assert "64 KB" in (resultado["error"] or "")
    assert _imagenes(escenario.db) == []
    assert _archivos(escenario.datos) == []
    assert any("64" in r.message for r in caplog.records)


def test_version_desconocida_descarta(escenario, caplog):
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        resultado = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(_jpeg(), v=2), now_ms=NOW
        )
    assert resultado["accion"] == "descartado"
    assert "v desconocida" in (resultado["error"] or "")
    assert _imagenes(escenario.db) == []


def test_motivo_invalido_descarta(escenario):
    resultado = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg(), motivo="streaming"), now_ms=NOW
    )
    assert resultado["accion"] == "descartado"
    assert "motivo" in (resultado["error"] or "")
    assert _imagenes(escenario.db) == []


def test_device_desconocido_descarta_y_allowlist_en_vivo(escenario, caplog):
    """Allowlist estricta con refresco en vivo (patrón F24-b1)."""
    imagen = _jpeg()
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        primero = escenario.writer.procesar_imagen_mqtt(
            _topic("image", DEV_NUEVO),
            _envelope(imagen, device_id=DEV_NUEVO),
            now_ms=NOW,
        )
    assert primero["accion"] == "imagen_descartada_device"
    assert _imagenes(escenario.db) == []
    assert any("device desconocido" in r.message for r in caplog.records)

    # Llega el retained ``device`` por la vía normal: la allowlist se refresca.
    assert escenario.writer.procesar(
        _topic("device", DEV_NUEVO), _device_payload(DEV_NUEVO, "Aula-Nueva"), now_ms=NOW
    )["accion"] == "device_upsert"
    segundo = escenario.writer.procesar_imagen_mqtt(
        _topic("image", DEV_NUEVO), _envelope(imagen, device_id=DEV_NUEVO), now_ms=NOW
    )
    assert segundo["accion"] == "imagen_mqtt", segundo
    assert len(_imagenes(escenario.db)) == 1


def test_device_id_no_coincide_con_el_topic_descarta(escenario):
    resultado = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg(), device_id=DEV2), now_ms=NOW
    )
    assert resultado["accion"] == "descartado"
    assert "no coincide con el topic" in (resultado["error"] or "")
    assert _imagenes(escenario.db) == []


# --- 4. Dedupe, rate-limit y FK -------------------------------------------


def test_dedupe_hash_no_duplica_reenvio(escenario, caplog):
    imagen = _jpeg()
    envelope = _envelope(imagen, ts_ms=2000)
    primero = escenario.writer.procesar_imagen_mqtt(_topic("image"), envelope, now_ms=NOW)
    assert primero["accion"] == "imagen_mqtt"
    with caplog.at_level(logging.INFO, logger="aulasense.ingesta"):
        segundo = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), envelope, now_ms=NOW + 2000
        )
    assert segundo["accion"] == "imagen_duplicada"
    assert len(_imagenes(escenario.db)) == 1
    assert len(_archivos(escenario.datos)) == 1
    assert any("duplicada" in r.message for r in caplog.records)


def test_dedupe_cubre_el_mismo_hash_por_otro_canal(escenario):
    """Cubre el doble canal HTTP+MQTT: la fila HTTP previa hace skip al MQTT."""
    imagen = _jpeg()
    sha = hashlib.sha256(imagen).hexdigest()
    con = sqlite3.connect(str(escenario.db))
    try:
        con.execute(
            "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes, "
            "origen, canal) VALUES (?, ?, ?, ?, ?, 'real', 'http')",
            (DEV, NOW, f"imagenes/{DEV}/20250101_000000_{sha[:8]}.jpg", sha, len(imagen)),
        )
        con.commit()
    finally:
        con.close()

    resultado = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(imagen), now_ms=NOW
    )
    assert resultado["accion"] == "imagen_duplicada"
    canales = [fila[0] for fila in _consultar(escenario.db, "SELECT canal FROM imagenes")]
    assert canales == ["http"]  # no se duplica la fila ni se reescribe el canal
    assert _archivos(escenario.datos) == []  # no se escribe el JPEG por MQTT


def test_rate_limit_1_imagen_10s_descarta(escenario, caplog):
    assert escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg((10, 10, 10)), ts_ms=2000), now_ms=NOW
    )["accion"] == "imagen_mqtt"
    with caplog.at_level(logging.WARNING, logger="aulasense.ingesta"):
        segundo = escenario.writer.procesar_imagen_mqtt(
            _topic("image"), _envelope(_jpeg((200, 0, 0)), ts_ms=2100), now_ms=NOW + 1000
        )
    assert segundo["accion"] == "imagen_descartada_rate"
    assert any("rate-limit" in r.message for r in caplog.records)
    # Pasados los 10 s la siguiente SÍ entra (el rate es por device).
    tercero = escenario.writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg((0, 200, 0)), ts_ms=2200), now_ms=NOW + 11_000
    )
    assert tercero["accion"] == "imagen_mqtt"
    assert len(_imagenes(escenario.db)) == 2
    # El rate es POR device: otro device no queda bloqueado.
    otro = escenario.writer.procesar_imagen_mqtt(
        _topic("image", DEV2),
        _envelope(_jpeg((0, 0, 200)), device_id=DEV2),
        now_ms=NOW + 12_000,
    )
    assert otro["accion"] == "imagen_mqtt"


def test_fk_evento_id_por_ventana_onset(escenario):
    """§7.2 paso 6: onset más reciente en [ts-15 s, ts+5 s]; null fuera."""
    writer = escenario.writer
    writer.procesar(
        _topic("motion"), {"timestamp_ms": 1000, "state": "idle", "score": 0.05}, now_ms=NOW
    )
    writer.procesar(
        _topic("motion"), {"timestamp_ms": 2000, "state": "motion", "score": 0.9}, now_ms=NOW
    )
    evento_id = _consultar(escenario.db, "SELECT id FROM eventos ORDER BY id DESC LIMIT 1")[0][0]

    # ts_ms 3000 -> epoca NOW+2000: el onset (epoca NOW+1000) está en la ventana.
    dentro = writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg((5, 5, 5)), ts_ms=3000), now_ms=NOW
    )
    assert dentro["accion"] == "imagen_mqtt"
    fila = _consultar(escenario.db, "SELECT evento_id FROM imagenes ORDER BY id DESC LIMIT 1")
    assert fila == [(evento_id,)]

    # Imagen muy posterior: fuera de la ventana -> evento_id null (pero se guarda).
    fuera = writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg((9, 9, 9)), ts_ms=200_000), now_ms=NOW + 11_000
    )
    assert fuera["accion"] == "imagen_mqtt"
    fila = _consultar(escenario.db, "SELECT evento_id FROM imagenes ORDER BY id DESC LIMIT 1")
    assert fila == [(None,)]


def test_fk_ignora_motion_fin_y_otro_device(escenario):
    """Solo MOVIMIENTO_INICIO del MISMO device sirve de ancla."""
    writer = escenario.writer
    writer.procesar(
        _topic("motion", DEV2), {"timestamp_ms": 1000, "state": "idle", "score": 0.05}, now_ms=NOW
    )
    writer.procesar(
        _topic("motion", DEV2), {"timestamp_ms": 2000, "state": "motion", "score": 0.9}, now_ms=NOW
    )
    resultado = writer.procesar_imagen_mqtt(
        _topic("image"), _envelope(_jpeg(), ts_ms=3000), now_ms=NOW
    )
    assert resultado["accion"] == "imagen_mqtt"
    assert _consultar(escenario.db, "SELECT evento_id FROM imagenes") == [(None,)]


# --- 5. Invariante de writer único ---------------------------------------


def test_invariante_writer_unico_solo_la_cola(escenario, monkeypatch):
    """El decoder NO abre otra conexión SQLite: todo pasa por la cola → writer."""
    hilo = threading.Thread(
        target=escenario.writer.run_forever,
        args=(escenario.cola,),
        name="writer-test-f25",
        daemon=True,
    )
    hilo.start()
    lector = sqlite3.connect(str(escenario.db))
    try:

        def _prohibido(*args, **kwargs):
            raise AssertionError("el decoder abrió una segunda conexión SQLite")

        # Cualquier ``sqlite3.connect`` nuevo durante el proceso revienta.
        monkeypatch.setattr(ingesta_mqtt.sqlite3, "connect", _prohibido)
        escenario.cola.put((_topic("image"), _envelope(_jpeg(), ts_ms=2000)))
        filas = _esperar(lector, "SELECT canal, vision_estado FROM imagenes")
        assert filas == [("mqtt", "pendiente")]
        assert len(_archivos(escenario.datos)) == 1
    finally:
        monkeypatch.undo()
        lector.close()
        escenario.cola.put(None)
        hilo.join(timeout=5)


# --- 6. Migración §8.2 ----------------------------------------------------


DDL_F24_IMAGENES = """
CREATE TABLE imagenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  ruta TEXT NOT NULL,
  hash_sha256 TEXT NOT NULL,
  tamano_bytes INTEGER NOT NULL,
  ancho INTEGER, alto INTEGER,
  evento_id INTEGER,
  ia_persona INTEGER, ia_score REAL,
  imagen_purgada INTEGER NOT NULL DEFAULT 0,
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def test_migracion_doble_pasada_idempotente(tmp_path):
    con = sqlite3.connect(str(tmp_path / "migra.db"))
    try:
        con.executescript(esquema.ESQUEMA_SQL)
        primera = esquema.migrar_esquema_f25(con)
        segunda = esquema.migrar_esquema_f25(con)  # segunda pasada sin error

        assert set(primera["columnas_nuevas"]) == COLUMNAS_F25
        assert segunda["columnas_nuevas"] == []
        assert COLUMNAS_F25 <= (esquema.columnas_tabla(con, "imagenes") or set())

        indices = {
            fila[0]
            for fila in con.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert {"idx_img_hash_unico", "idx_img_vision_estado", "idx_img_canal"} <= indices
        tablas = {
            fila[0]
            for fila in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"usuarios", "intentos_login", "vision_uso"} <= tablas

        tabla_login = esquema.nombre_tabla_sesiones_login(con)
        assert tabla_login in ("sesiones", "sesiones_auth")
        assert primera["tabla_sesiones_login"] == segunda["tabla_sesiones_login"]
        columnas_login = esquema.columnas_tabla(con, tabla_login) or set()
        assert {"token_hash", "user_id", "expira_en_ms", "ultimo_uso_ms"} <= columnas_login

        # El unique index de hash está operativo (dedupe transversal de §8.2).
        con.execute(
            "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes) "
            "VALUES ('x', 1, 'r1', 'hash-repetido', 10)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes) "
                "VALUES ('x', 2, 'r2', 'hash-repetido', 10)"
            )
    finally:
        con.rollback()
        con.close()


def test_migracion_filas_f24_quedan_http_y_pendiente(tmp_path):
    """Filas F24 existentes: canal='http' y vision_estado='pendiente' (§8.2)."""
    con = sqlite3.connect(str(tmp_path / "f24.db"))
    try:
        con.executescript(DDL_F24_IMAGENES)
        con.execute(
            "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes) "
            "VALUES (?, ?, ?, ?, ?)",
            (DEV, NOW, f"imagenes/{DEV}/20250101_000000_abcdef01.jpg", "a" * 64, 1234),
        )
        con.commit()
        esquema.migrar_esquema_f25(con)
        esquema.migrar_esquema_f25(con)
        filas = con.execute("SELECT canal, vision_estado, descripcion FROM imagenes").fetchall()
        assert filas == [("http", "pendiente", None)]
    finally:
        con.close()


def test_migracion_sana_hashes_duplicados_previos(tmp_path):
    """Un duplicado F24 no puede impedir el unique index: se conserva el más viejo."""
    con = sqlite3.connect(str(tmp_path / "dup.db"))
    try:
        con.executescript(DDL_F24_IMAGENES)
        for _ in range(2):
            con.execute(
                "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes) "
                "VALUES (?, ?, ?, ?, ?)",
                (DEV, NOW, f"imagenes/{DEV}/20250101_000000_abcdef01.jpg", "b" * 64, 10),
            )
        con.commit()
        resumen = esquema.migrar_esquema_f25(con)
        assert resumen["imagenes_duplicadas_descartadas"] == 1
        assert con.execute("SELECT COUNT(*) FROM imagenes").fetchone()[0] == 1
        assert con.execute("SELECT MIN(id) FROM imagenes").fetchone()[0] == 1
    finally:
        con.close()


def test_writer_aplica_la_migracion_al_arrancar(tmp_path, monkeypatch):
    monkeypatch.setenv("AULASENSE_DATOS_DIR", str(tmp_path / "datos"))
    writer = IngestaWriter(tmp_path / "auto.db", tmp_path / "auto.csv")
    try:
        resumen = writer._migracion_f25
        assert set(resumen["columnas_nuevas"]) == COLUMNAS_F25
        tabla_login = resumen["tabla_sesiones_login"]
        assert tabla_login in ("sesiones", "sesiones_auth")
        assert "token_hash" in (esquema.columnas_tabla(writer._conn, tabla_login) or set())
    finally:
        writer.cerrar()


# --- 7. Roundtrip con BROKER REAL (topic de prueba exclusivo) -------------


def test_roundtrip_broker_real(tmp_path, monkeypatch):
    """Publica el envelope por paho, lo recibe, y lo pasa por el despachador real.

    Topic EXCLUSIVO ``probe-<hex>`` (jamás el prefijo de producción
    ``aulasense/v1/devices/*``, donde la ingesta real puede estar en vuelo). El
    topic canónico se reconstruye en local para el writer, de modo que el decoder
    valida contra el contrato aunque el transporte haya ido por el probe.
    """
    import paho.mqtt.client as mqtt_mod

    # AULASENSE_DATOS_DIR ANTES de leer el .env (load_dotenv no pisa el entorno).
    monkeypatch.setenv("AULASENSE_DATOS_DIR", str(tmp_path / "datos"))
    cfg = ingesta_mqtt.MqttConfig.from_env()
    if not cfg.host:
        pytest.skip("MQTT_HOST vacío: roundtrip con broker omitido")

    topic_probe = f"probe-{secrets.token_hex(6)}"
    conectado = threading.Event()
    recibido: dict[str, bytes] = {}
    mensaje = threading.Event()

    cliente = mqtt_mod.Client(
        mqtt_mod.CallbackAPIVersion.VERSION2,
        client_id=f"aulasense-f25d1-{secrets.token_hex(4)}",
    )
    if cfg.user:
        cliente.username_pw_set(cfg.user, cfg.password)
    if cfg.scheme == "mqtts":
        cliente.tls_set()
        cliente.tls_insecure_set(False)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        conectado.set()
        client.subscribe(topic_probe, qos=0)

    def on_message(client, userdata, msg):
        recibido["payload"] = bytes(msg.payload)
        mensaje.set()

    cliente.on_connect = on_connect
    cliente.on_message = on_message

    try:
        cliente.connect(cfg.host, cfg.port, keepalive=15)
    except OSError as exc:
        pytest.skip(f"broker inalcanzable ({cfg.host}:{cfg.port}): {exc!r}")

    cliente.loop_start()
    try:
        if not conectado.wait(10):
            pytest.skip("broker sin CONNACK en 10 s: roundtrip omitido")
        imagen = _jpeg((120, 30, 200))
        envelope = _envelope(imagen, ts_ms=2000)
        info = cliente.publish(topic_probe, envelope, qos=0, retain=False)
        try:
            info.wait_for_publish(timeout=5)
        except Exception:  # pragma: no cover - paho puede no tener el info listo
            pass
        if not mensaje.wait(10):
            pytest.skip(
                "broker alcanzable pero sin eco del probe (ACL del usuario de "
                "ingesta o límite del broker): roundtrip omitido"
            )
        assert recibido["payload"] == envelope

        # Despachador REAL: cola -> writer -> archivo + fila.
        cola: "queue.Queue" = queue.Queue(maxsize=100)
        writer = IngestaWriter(tmp_path / "broker.db", tmp_path / "broker.csv", cola=cola)
        hilo = threading.Thread(
            target=writer.run_forever, args=(cola,), name="writer-broker-f25", daemon=True
        )
        try:
            assert writer.procesar(
                _topic("device"), _device_payload(DEV, "Aula-Broker"), now_ms=NOW
            )["accion"] == "device_upsert"
            assert writer.procesar(
                _topic("health"),
                {"status": "ok", "online": True, "uptime_s": 10, "timestamp_ms": 1000},
                now_ms=NOW,
            )["accion"].startswith("health_")
            hilo.start()
            cola.put((_topic("image"), recibido["payload"]))
            con = sqlite3.connect(str(tmp_path / "broker.db"))
            try:
                filas = _esperar(
                    con, "SELECT canal, vision_estado, hash_sha256 FROM imagenes"
                )
            finally:
                con.close()
            assert len(filas) == 1
            assert filas[0][0] == "mqtt" and filas[0][1] == "pendiente"
            assert filas[0][2] == hashlib.sha256(imagen).hexdigest()
            (archivo,) = _archivos(tmp_path / "datos")
            assert archivo.read_bytes() == imagen
        finally:
            cola.put(None)
            hilo.join(timeout=5)
            writer.cerrar()
    finally:
        cliente.loop_stop()
        cliente.disconnect()