"""Tests de la ingesta de AulaSense (esquema SPEC §6 y writer SQLite/CSV).

No usan paho ni broker: la cola se alimenta llamando a ``IngestaWriter.procesar``
directamente y el reloj del PC se inyecta con ``now_ms`` para que sea determinista.
"""

from __future__ import annotations

import csv
import sqlite3

import pytest

import esquema
from ingesta_mqtt import IngestaWriter

PREFIJO = "aulasense/v1/devices"
DEV = "0123456789abcdef"
DEV2 = "fedcba9876543210"

# Instante de referencia del PC (epoch ms) inyectado en todos los tests de reloj.
NOW = 1_760_000_000_000


def topic(suffix: str, device_id: str = DEV, prefix: str = PREFIJO) -> str:
    return f"{prefix}/{device_id}/{suffix}"


@pytest.fixture
def writer(tmp_path):
    """Writer con DB y CSV temporales (sin broker, sin paho)."""
    w = IngestaWriter(tmp_path / "aulasense.db", tmp_path / "export.csv")
    yield w
    w.cerrar()


def consultar(w: IngestaWriter, sql: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(w.db_path)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def eventos(w: IngestaWriter, tipo: str | None = None) -> list[tuple]:
    if tipo is None:
        sql = "SELECT tipo, score, duracion_ms FROM eventos ORDER BY id"
        return consultar(w, sql)
    sql = "SELECT score, duracion_ms FROM eventos WHERE tipo = ? ORDER BY id"
    return consultar(w, sql, (tipo,))


# --- 1. Validacion del contrato (esquema.py) -------------------------------


def test_validar_motion_valido():
    res = esquema.validar(
        topic("motion"), {"timestamp_ms": 1234, "state": "motion", "score": 0.87}
    )
    assert res["valido"] is True
    assert res["error"] is None
    assert res["kind"] == "motion"
    assert res["datos"]["score"] == 0.87


def test_rechaza_motion_state_bogus():
    res = esquema.validar(
        topic("motion"), {"timestamp_ms": 1234, "state": "presence", "score": 0.5}
    )
    assert res["valido"] is False
    assert res["kind"] is None
    assert res["datos"] is None
    assert "state" in res["error"]


def test_rechaza_motion_score_fuera_de_rango():
    alto = esquema.validar(
        topic("motion"), {"timestamp_ms": 1, "state": "idle", "score": 1.5}
    )
    bajo = esquema.validar(
        topic("motion"), {"timestamp_ms": 1, "state": "idle", "score": -0.1}
    )
    texto = esquema.validar(
        topic("motion"), {"timestamp_ms": 1, "state": "idle", "score": "alta"}
    )
    assert alto["valido"] is False and "score" in alto["error"]
    assert bajo["valido"] is False and "score" in bajo["error"]
    assert texto["valido"] is False and "score" in texto["error"]


def test_rechaza_bool_donde_se_espera_entero():
    # ``True`` es int en Python, pero el contrato exige entero real.
    res = esquema.validar(
        topic("health"),
        {"status": "ok", "online": True, "uptime_s": True, "timestamp_ms": 10},
    )
    assert res["valido"] is False
    assert "uptime_s" in res["error"]


def test_wifi_rechaza_campos_privados():
    con_ssid = esquema.validar(
        topic("wifi"),
        {"configured": True, "connected": True, "ssid": "aula-2g", "rssi_dbm": -60},
    )
    con_ip = esquema.validar(
        topic("wifi"), {"configured": True, "connected": True, "sta_ip": "10.0.0.5"}
    )
    valido = esquema.validar(
        topic("wifi"),
        {"configured": True, "connected": True, "band": "2.4", "channel": 6, "rssi_dbm": -60},
    )
    assert con_ssid["valido"] is False and "privados" in con_ssid["error"]
    assert con_ip["valido"] is False and "privados" in con_ip["error"]
    assert valido["valido"] is True and valido["kind"] == "wifi"


def test_aulasense_temp_rango_y_tipo():
    ok = esquema.validar(topic("aulasense_temp"), {"timestamp_ms": 10, "die_temp_c": 48.5})
    fuera = esquema.validar(topic("aulasense_temp"), {"timestamp_ms": 10, "die_temp_c": 200})
    assert ok["valido"] is True and ok["kind"] == "aulasense_temp"
    assert fuera["valido"] is False and "die_temp_c" in fuera["error"]


def test_validar_device_y_commands_result():
    device = {
        "device_id": DEV,
        "name": "aulasense",
        "label": "AulaSense-01",
        "frontend": "native",
        "firmware": "3.2.0",
        "chip": "esp32s3",
        "csi_profile": "ht20",
    }
    res_device = esquema.validar(topic("device"), device)
    res_device_ajeno = esquema.validar(topic("device", DEV2), device)
    malo = dict(device, chip="esp32c6")
    res_malo = esquema.validar(topic("device"), malo)

    assert res_device["valido"] is True and res_device["kind"] == "device"
    assert res_device_ajeno["valido"] is False  # device_id no coincide con el topic
    assert res_malo["valido"] is False

    resultado = {
        "command_id": "cmd-42",
        "command": "read_diagnostics",
        "accepted": True,
        "code": None,
        "message": None,
        "data": {"detection_avg_us": 120.5},
    }
    res_cmd = esquema.validar(topic("commands/result"), resultado)
    id_largo = dict(resultado, command_id="x" * 65)
    assert res_cmd["valido"] is True and res_cmd["kind"] == "commands_result"
    assert esquema.validar(topic("commands/result"), id_largo)["valido"] is False


# --- 2. parse_topic --------------------------------------------------------


def test_parse_topic_prefix_ajeno():
    assert esquema.parse_topic("aulasense/v2/devices/0123456789abcdef/motion") is None
    assert esquema.parse_topic("otro/prefijo/0123456789abcdef/motion") is None
    assert esquema.parse_topic("aulasense/v1/devices/motion") is None


def test_parse_topic_device_id_malformado():
    assert esquema.parse_topic(topic("motion", "0123456789ABCDEF")) is None  # mayusculas
    assert esquema.parse_topic(topic("motion", "0123456789abcde")) is None  # 15 hex
    assert esquema.parse_topic(topic("motion", "zzzzzzzzzzzzzzzz")) is None  # no hex
    assert esquema.parse_topic(topic("sufijo_desconocido")) is None


def test_parse_topic_valido_con_sufijo_compuesto():
    assert esquema.parse_topic(topic("motion")) == (DEV, "motion")
    assert esquema.parse_topic(topic("commands/result")) == (DEV, "commands/result")
    assert esquema.parse_topic(topic("aulasense_temp", DEV2)) == (DEV2, "aulasense_temp")
    # prefijo alternativo explicito
    assert esquema.parse_topic("mi/prefijo/" + DEV + "/wifi", "mi/prefijo") == (DEV, "wifi")


# --- 3. Esquema SQLite -----------------------------------------------------


def test_esquema_crea_tablas_e_indices(tmp_path):
    w = IngestaWriter(tmp_path / "x.db", tmp_path / "x.csv")
    try:
        nombres = {
            fila[0]
            for fila in consultar(
                w, "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
            )
        }
    finally:
        w.cerrar()
    esperadas = {
        "dispositivos",
        "evaluaciones",
        "eventos",
        "telemetria",
        "validacion_sesiones",
        "fuentes_externas",
        "idx_eval_ts",
        "idx_eval_dev_ts",
        "idx_eventos_ts",
    }
    assert esperadas <= nombres


def test_writer_es_idempotente_y_csv_sin_cabecera_duplicada(tmp_path):
    db = tmp_path / "aulasense.db"
    csv_path = tmp_path / "export.csv"
    payload = {"timestamp_ms": 100, "state": "idle", "score": 0.1}

    w1 = IngestaWriter(db, csv_path)
    w1.procesar(topic("motion"), payload, now_ms=NOW)
    w1.cerrar()

    # Segunda ejecucion del esquema sobre la misma DB/CSV: no debe fallar.
    w2 = IngestaWriter(db, csv_path)
    resultado = w2.procesar(topic("motion"), payload, now_ms=NOW + 250)
    w2.cerrar()

    assert resultado["valido"] is True
    assert consultar(w2, "SELECT COUNT(*) FROM evaluaciones")[0][0] == 2

    with open(csv_path, newline="", encoding="utf-8") as fh:
        filas = list(csv.reader(fh))
    assert filas[0] == list(
        ("device_id", "ts_ms", "ts_device_ms", "hora_sinc", "state", "score", "origen")
    )
    assert filas.count(filas[0]) == 1  # cabecera una sola vez
    assert len(filas) == 3  # cabecera + 2 evaluaciones


# --- 4. Eventos derivados y reloj (writer) ---------------------------------


def test_health_deriva_nodo_online_y_offline(writer: IngestaWriter):
    writer.procesar(
        topic("health"),
        {"status": "ok", "online": True, "uptime_s": 5, "timestamp_ms": 5000},
        now_ms=NOW,
    )
    # Mismo estado: no se duplica el evento.
    writer.procesar(
        topic("health"),
        {"status": "ok", "online": True, "uptime_s": 10, "timestamp_ms": 10000},
        now_ms=NOW + 5000,
    )
    writer.procesar(
        topic("health"),
        {"status": "offline", "online": False, "uptime_s": 11, "timestamp_ms": 11000},
        now_ms=NOW + 11000,
    )

    assert len(eventos(writer, "NODO_ONLINE")) == 1
    assert len(eventos(writer, "NODO_OFFLINE")) == 1
    last_seen = consultar(writer, "SELECT last_seen_ms FROM dispositivos WHERE device_id=?", (DEV,))
    assert last_seen == [(NOW + 11000,)]


def test_deriva_movimiento_inicio_y_fin_con_duracion(writer: IngestaWriter):
    writer.procesar(
        topic("health"),
        {"status": "ok", "online": True, "uptime_s": 1, "timestamp_ms": 1000},
        now_ms=NOW,
    )
    # Linea base idle (sin evento), luego idle->motion y motion->idle.
    writer.procesar(
        topic("motion"), {"timestamp_ms": 1250, "state": "idle", "score": 0.05}, now_ms=NOW + 250
    )
    writer.procesar(
        topic("motion"), {"timestamp_ms": 1500, "state": "motion", "score": 0.92}, now_ms=NOW + 500
    )
    writer.procesar(
        topic("motion"), {"timestamp_ms": 2500, "state": "idle", "score": 0.02}, now_ms=NOW + 1500
    )

    movimientos = consultar(
        writer,
        "SELECT tipo, score, duracion_ms FROM eventos "
        "WHERE tipo IN ('MOVIMIENTO_INICIO','MOVIMIENTO_FIN') ORDER BY id",
    )
    assert movimientos == [
        ("MOVIMIENTO_INICIO", pytest.approx(0.92), None),
        ("MOVIMIENTO_FIN", pytest.approx(0.02), 1000),
    ]
    filas = consultar(writer, "SELECT state, score FROM evaluaciones ORDER BY id")
    assert filas == [("idle", pytest.approx(0.05)), ("motion", pytest.approx(0.92)), ("idle", pytest.approx(0.02))]


def test_epoca_boot_estima_ts_y_hora_sinc(writer: IngestaWriter):
    writer.procesar(
        topic("health"),
        {"status": "ok", "online": True, "uptime_s": 5, "timestamp_ms": 5000},
        now_ms=NOW,
    )
    writer.procesar(
        topic("motion"), {"timestamp_ms": 5200, "state": "motion", "score": 0.8}, now_ms=NOW + 250
    )

    ts_ms, ts_device_ms, hora_sinc = consultar(
        writer, "SELECT ts_ms, ts_device_ms, hora_sinc FROM evaluaciones ORDER BY id DESC LIMIT 1"
    )[0]
    assert ts_device_ms == 5200
    assert abs(ts_ms - (NOW + 200)) <= 1  # epoca = NOW - 5000, + timestamp_ms
    assert hora_sinc == 1


def test_sin_health_cae_a_hora_del_pc_y_hora_sinc_cero(writer: IngestaWriter):
    writer.procesar(
        topic("motion"), {"timestamp_ms": 1234, "state": "idle", "score": 0.1}, now_ms=NOW
    )
    ts_ms, hora_sinc = consultar(writer, "SELECT ts_ms, hora_sinc FROM evaluaciones")[0]
    assert ts_ms == NOW
    assert hora_sinc == 0


def test_health_tardia_marca_hora_sinc_cero(writer: IngestaWriter):
    writer.procesar(
        topic("health"),
        {"status": "ok", "online": True, "uptime_s": 5, "timestamp_ms": 5000},
        now_ms=NOW,
    )
    # 200 s despues el nodo sigue enviando sin health fresco: estimacion caducada.
    writer.procesar(
        topic("motion"), {"timestamp_ms": 205000, "state": "motion", "score": 0.7}, now_ms=NOW + 200_000
    )
    hora_sinc = consultar(writer, "SELECT hora_sinc FROM evaluaciones")[0][0]
    assert hora_sinc == 0


def test_reboot_reestima_epoca(writer: IngestaWriter):
    writer.procesar(
        topic("health"),
        {"status": "ok", "online": True, "uptime_s": 100, "timestamp_ms": 100_000},
        now_ms=NOW,
    )
    writer.procesar(
        topic("motion"), {"timestamp_ms": 100_500, "state": "idle", "score": 0.1}, now_ms=NOW + 500
    )
    # Reinicio: uptime y timestamp_ms vuelven a valores pequenos.
    writer.procesar(
        topic("health"),
        {"status": "ok", "online": True, "uptime_s": 2, "timestamp_ms": 2_000},
        now_ms=NOW + 60_000,
    )
    writer.procesar(
        topic("motion"), {"timestamp_ms": 2_400, "state": "idle", "score": 0.1}, now_ms=NOW + 60_400
    )

    filas = consultar(writer, "SELECT ts_ms, hora_sinc FROM evaluaciones ORDER BY id")
    assert abs(filas[0][0] - (NOW + 500)) <= 1
    assert abs(filas[1][0] - (NOW + 60_400)) <= 1  # epoca re-estimada con el health nuevo
    assert [fila[1] for fila in filas] == [1, 1]


def test_retroceso_de_reloj_marca_hora_sinc_cero(writer: IngestaWriter):
    writer.procesar(
        topic("health"),
        {"status": "ok", "online": True, "uptime_s": 100, "timestamp_ms": 100_000},
        now_ms=NOW,
    )
    # Salto atras de ~97 s sin health previo: se sospecha reinicio.
    resultado = writer.procesar(
        topic("motion"), {"timestamp_ms": 3_000, "state": "motion", "score": 0.6}, now_ms=NOW + 60_000
    )
    fila = consultar(writer, "SELECT hora_sinc FROM evaluaciones ORDER BY id DESC LIMIT 1")[0]
    assert resultado["accion"] == "evaluacion_baseline_motion"
    assert fila[0] == 0  # no se confia en la epoca vieja hasta el proximo health


# --- 5. Telemetria, fault y dispositivos -----------------------------------


def test_telemetria_desde_commands_result_y_temp(writer: IngestaWriter):
    writer.procesar(
        topic("health"),
        {"status": "ok", "online": True, "uptime_s": 5, "timestamp_ms": 5000},
        now_ms=NOW,
    )
    resultado = {
        "command_id": "diag-1-01234567",
        "command": "read_diagnostics",
        "accepted": True,
        "code": "ok",
        "message": None,
        "data": {
            "detection_avg_us": 123.5,
            "cpu_frequency_mhz": 240,
            "wifi_rssi_dbm": -55,
            "detector": "mlp",
            "threshold": 0.5,
            "traffic_generator_mode": "ping",
            "csi_occupancy": 0.93,
            "mqtt": {"connected": True},  # objeto anidado: se ignora
        },
        "timestamp_ms": 5500,
    }
    writer.procesar(topic("commands/result"), resultado, now_ms=NOW + 500)
    writer.procesar(
        topic("aulasense_temp"), {"timestamp_ms": 6000, "die_temp_c": 47.25}, now_ms=NOW + 1000
    )

    filas = consultar(
        writer,
        "SELECT ts_ms, detection_avg_us, cpu_frequency_mhz, wifi_rssi_dbm, detector, die_temp_c "
        "FROM telemetria ORDER BY id",
    )
    assert len(filas) == 2
    ts_diag, avg, cpu, rssi, detector, temp = filas[0]
    assert abs(ts_diag - (NOW + 500)) <= 1
    assert avg == pytest.approx(123.5)
    assert cpu == 240 and rssi == -55 and detector == "mlp"
    assert temp is None
    assert filas[1][5] == pytest.approx(47.25)


def test_fault_crea_evento_falla(writer: IngestaWriter):
    writer.procesar(
        topic("fault"), {"timestamp_ms": 700, "message": "CSI timeout"}, now_ms=NOW
    )
    assert len(eventos(writer, "FALLA")) == 1
    filas = consultar(writer, "SELECT device_id, ts_ms FROM eventos")
    assert filas == [(DEV, NOW)]  # sin health: fallback a hora del PC


def test_device_upsert_y_sensing_wifi_aceptados(writer: IngestaWriter):
    device = {
        "device_id": DEV,
        "name": "aulasense",
        "label": "AulaSense-01",
        "frontend": "native",
        "firmware": "3.2.0",
        "chip": "esp32s3",
        "csi_profile": "ht20",
    }
    assert writer.procesar(topic("device"), device, now_ms=NOW)["accion"] == "device_upsert"
    assert writer.procesar(topic("device"), device, now_ms=NOW + 1000)["accion"] == "device_upsert"

    filas = consultar(
        writer,
        "SELECT device_id, label, first_seen_ms, last_seen_ms FROM dispositivos",
    )
    assert filas == [(DEV, "AulaSense-01", NOW, NOW + 1000)]

    sensing = {
        "enabled": True,
        "ready": True,
        "calibrating": False,
        "mode": "high_accuracy",
        "detector": "mlp",
        "threshold": 0.55,
        "motion_on_hits": 3,
        "csi_target_pps": 100,
    }
    assert writer.procesar(topic("sensing"), sensing)["accion"] == "sensing_aceptado"
    wifi = {"configured": True, "connected": True, "channel": 6, "rssi_dbm": -58}
    assert writer.procesar(topic("wifi"), wifi)["accion"] == "wifi_aceptado"


def test_motion_invalido_no_escribe(writer: IngestaWriter):
    res = writer.procesar(
        topic("motion"), {"timestamp_ms": 1, "state": "idle", "score": 3.0}, now_ms=NOW
    )
    assert res["valido"] is False and res["accion"] == "descartado"
    assert consultar(writer, "SELECT COUNT(*) FROM evaluaciones")[0][0] == 0
    assert consultar(writer, "SELECT COUNT(*) FROM eventos")[0][0] == 0