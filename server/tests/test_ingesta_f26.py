"""Tests F26 de la ingesta (SPEC §16.4/§16.5/§16.7 — fase F26-a1).

Persistencia de los retains nuevos (``ota``/``imagen_estado``/``heap_interno``),
cacheo del heap interno hacia ``telemetria.internal_*``, heartbeat de
``estado_sistema`` en el ciclo del poller con derivacion de eventos SISTEMA_*,
envelope exacto de ``publicar_comando_ota`` y loader del .env unico raiz.
Sin broker: el writer se ejerce con ``procesar``/``now_ms`` inyectados y el
cliente paho se sustituye por un stub (igual que el patron de test_ingesta.py).
"""

from __future__ import annotations

import json
import re
import sqlite3
import types

import pytest

import esquema
import ingesta_mqtt
from ingesta_mqtt import (
    IngestaWriter,
    MqttConfig,
    PollerDiagnosticos,
    publicar_comando_ota,
)

PREFIJO = "aulasense/v1/devices"
DEV = "2e6f0c909e30673e"
NOW = 1_760_000_000_000

OTA_OK = {
    "estado": "downloading",
    "version_actual": "f25.3",
    "version_objetivo": "f26.1",
    "sha256_ok": False,
    "detalle": "bajando slot b",
    "ts_ms": 1763400000000,
}
IMAGEN_ESTADO_OK = {
    "camara_ok": True,
    "camara_pid": "0x3660",
    "camara_err": "",
    "pipeline_ok": True,
    "captura_habilitada": True,
    "gate_off": False,
    "descartes": 0,
    "imagenes_ok": 57,
    "internal_free_kb": 118,
    "internal_largest_block_kb": 60,
    "build": "f26.1",
    "imagen_habilitado": True,
    "ts_ms": 1763400000000,
}
HEAP_OK = {
    "internal_free_kb": 118,
    "internal_largest_block_kb": 60,
    "ts_ms": 1763400000000,
}


def topic(suffix: str, device_id: str = DEV) -> str:
    return f"{PREFIJO}/{device_id}/{suffix}"


@pytest.fixture
def writer(tmp_path):
    """Writer con DB y CSV temporales (migraciones F25+F26 aplicadas al abrir)."""
    w = IngestaWriter(tmp_path / "aulasense.db", tmp_path / "export.csv")
    yield w
    w.cerrar()


@pytest.fixture(autouse=True)
def disco_controlado(monkeypatch):
    """El disco real de la maquina NO decide los eventos SISTEMA_DISCO_WARN.

    Fijo 50 % de uso: determinista en ambos runtimes (Windows/WSL); el test del
    borde lo re-parcha a 90 % a proposito.
    """
    monkeypatch.setattr(
        ingesta_mqtt.shutil,
        "disk_usage",
        lambda ruta: types.SimpleNamespace(total=100, used=50, free=50),
    )


def consultar(w: IngestaWriter, sql: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(w.db_path)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def heartbeat_tipos(w: IngestaWriter) -> list[str]:
    filas = consultar(w, "SELECT tipo FROM config_eventos WHERE tipo LIKE 'SISTEMA_%' ORDER BY id")
    return [fila[0] for fila in filas]


def registrar_device(w: IngestaWriter, ahora: int = NOW) -> None:
    w.procesar(
        topic("device"),
        {
            "device_id": DEV,
            "frontend": "native",
            "chip": "esp32s3",
            "name": "aula",
            "label": "AULA-1",
            "firmware": "f26.1",
            "csi_profile": "p",
        },
        now_ms=ahora,
    )


def commands_result(data: dict) -> dict:
    return {
        "command_id": "diag-1",
        "command": "read_diagnostics",
        "accepted": True,
        "data": data,
    }


class ClienteStub:
    """Sustituto del cliente paho: registra publish(), responde is_connected()."""

    def __init__(self, conectado: bool = False) -> None:
        self.conectado = conectado
        self.publicados: list[dict] = []

    def is_connected(self) -> bool:
        return self.conectado

    def publish(self, topic: str, payload, qos: int = 0):
        self.publicados.append({"topic": topic, "payload": payload, "qos": qos})


# --- 1. Retains nuevos: validados y persistidos (simulados, topic de prueba) -


def test_ota_retain_se_cachea_en_estado_sistema(writer):
    writer.escribir_heartbeat(True, ahora=NOW)
    res = writer.procesar(topic("ota"), OTA_OK, now_ms=NOW + 1_000)
    assert res["valido"] is True
    assert res["kind"] == "ota"
    assert res["accion"] == "ota_retenido"
    fila = consultar(writer, "SELECT detalle FROM estado_sistema WHERE id = 1")[0]
    detalle = json.loads(fila[0])
    assert detalle["ota"][DEV] == OTA_OK
    # El retain NO falsifica la frescura del heartbeat: ts_ms sigue siendo el
    # del ultimo ciclo del poller (SPEC §16.7 "ingesta activa <= 90 s").
    ts = consultar(writer, "SELECT ts_ms FROM estado_sistema WHERE id = 1")[0][0]
    assert ts == NOW


def test_imagen_estado_retain_se_cachea(writer):
    res = writer.procesar(topic("imagen_estado"), IMAGEN_ESTADO_OK, now_ms=NOW)
    assert res["valido"] is True and res["accion"] == "imagen_estado_retenido"
    detalle = json.loads(
        consultar(writer, "SELECT detalle FROM estado_sistema WHERE id = 1")[0][0]
    )
    assert detalle["imagen_estado"][DEV]["camara_pid"] == "0x3660"


def test_sufijo_ota_rechazaria_sin_migracion_es_ahora_valido(writer):
    """Smoke del contrato: el payload que ANTES se rechazaba pasa y persiste."""
    res = writer.procesar(topic("heap_interno"), HEAP_OK, now_ms=NOW)
    assert res["valido"] is True
    assert res["kind"] == "heap_interno"
    assert res["accion"] == "heap_interno_retenido"


# --- 2. heap_interno -> telemetria.internal_* (D-F26-5) ----------------------


def test_heap_cacheado_rellena_telemetria(writer):
    registrar_device(writer)
    writer.procesar(topic("heap_interno"), HEAP_OK, now_ms=NOW)
    res = writer.procesar(
        topic("commands/result"), commands_result({"free_memory_kb": 8200}), now_ms=NOW
    )
    assert res["accion"] == "telemetria_diagnostics"
    fila = consultar(
        writer,
        "SELECT free_memory_kb, internal_free_kb, internal_largest_block_kb "
        "FROM telemetria ORDER BY id DESC LIMIT 1",
    )[0]
    assert fila == (8200.0, 118, 60)


def test_imagen_estado_tambien_alimenta_el_cache_heap(writer):
    writer.procesar(topic("imagen_estado"), IMAGEN_ESTADO_OK, now_ms=NOW)
    writer.procesar(
        topic("commands/result"),
        commands_result({"runtime_load_percent": 12}),
        now_ms=NOW,
    )
    fila = consultar(
        writer,
        "SELECT internal_free_kb, internal_largest_block_kb FROM telemetria "
        "ORDER BY id DESC LIMIT 1",
    )[0]
    assert fila == (118, 60)


def test_commands_result_sin_cache_internal_nulo(writer):
    """Backwards-compat: flujo viejo (sin heap_interno) -> columna NULL."""
    writer.procesar(
        topic("commands/result"), commands_result({"free_memory_kb": 8200}), now_ms=NOW
    )
    fila = consultar(
        writer,
        "SELECT internal_free_kb, internal_largest_block_kb FROM telemetria "
        "ORDER BY id DESC LIMIT 1",
    )[0]
    assert fila == (None, None)


def test_commands_result_acepta_internal_directos(writer):
    datos = dict(HEAP_OK)
    datos.pop("ts_ms")
    writer.procesar(topic("commands/result"), commands_result(datos), now_ms=NOW)
    fila = consultar(
        writer,
        "SELECT internal_free_kb, internal_largest_block_kb FROM telemetria "
        "ORDER BY id DESC LIMIT 1",
    )[0]
    assert fila == (118, 60)


# --- 3. Eventos nuevos por topic aulasense_config -> config_eventos ---------


@pytest.mark.parametrize(
    "evento",
    [
        "OTA_CHECK",
        "OTA_INICIO",
        "OTA_OK",
        "OTA_ERROR",
        "FIRMWARE",
        "IMAGEN_INIT",
        "CAMARA_ERROR",
        "AP_ERROR",
    ],
)
def test_eventos_placa_f26_persisten(writer, evento):
    """origen=real por topic de prueba: nada se marca sintetico en esta ruta."""
    res = writer.procesar(
        topic("aulasense_config"),
        {"timestamp_ms": 10, "evento": evento, "detalle": "x"},
        now_ms=NOW,
    )
    assert res["valido"] is True and res["accion"] == "config_evento"
    fila = consultar(
        writer,
        "SELECT tipo, origen FROM config_eventos WHERE tipo = ?",
        (evento,),
    )[0]
    assert fila == (evento, "real")


def test_eventos_viejos_siguen_persistiendo(writer):
    for evento in ("IMAGEN_GATE_OFF", "AP_FALLBACK", "IMAGEN_ON"):
        res = writer.procesar(
            topic("aulasense_config"),
            {"timestamp_ms": 20, "evento": evento},
            now_ms=NOW,
        )
        assert res["accion"] == "config_evento", evento
    assert (
        consultar(
            writer,
            "SELECT COUNT(*) FROM config_eventos",
        )[0][0]
        == 3
    )


# --- 4. Heartbeat estado_sistema (D-F26-8) -----------------------------------


def test_heartbeat_upsert_fila_unica(writer):
    writer.escribir_heartbeat(True, ahora=NOW)
    filas = consultar(
        writer, "SELECT id, ts_ms, mqtt_conectado, ingesta_activa FROM estado_sistema"
    )
    assert filas == [(1, NOW, 1, 1)]
    writer.escribir_heartbeat(False, ahora=NOW + 30_000)
    filas = consultar(
        writer, "SELECT id, ts_ms, mqtt_conectado FROM estado_sistema"
    )
    assert filas == [(1, NOW + 30_000, 0)]


def test_borde_mqtt_deriva_eventos_de_cambio(writer):
    writer.escribir_heartbeat(True, ahora=NOW)  # baseline: SIN evento
    assert heartbeat_tipos(writer) == []
    writer.escribir_heartbeat(False, ahora=NOW + 30_000)
    writer.escribir_heartbeat(True, ahora=NOW + 60_000)
    assert heartbeat_tipos(writer) == [
        "SISTEMA_MQTT_CAIDO",
        "SISTEMA_MQTT_RECUPERADO",
    ]
    fila = consultar(
        writer,
        "SELECT device_id FROM config_eventos WHERE tipo = 'SISTEMA_MQTT_CAIDO'",
    )[0]
    assert fila[0] == "sistema"  # sin devices: placeholder honesto
    writer.escribir_heartbeat(True, ahora=NOW + 90_000)  # sin repetirlo
    assert len(heartbeat_tipos(writer)) == 2


def test_stale_telemetria_borde_unico(writer):
    # Fila de telemetria "vieja": temp leido en NOW, heartbeat 10 min + 1 s despues.
    writer.procesar(
        topic("aulasense_temp"), {"timestamp_ms": 1, "die_temp_c": 45.0}, now_ms=NOW
    )
    res = writer.escribir_heartbeat(True, ahora=NOW + 601_000)
    assert "SISTEMA_TELEMETRIA_STALE" in res["derivados"]
    res2 = writer.escribir_heartbeat(True, ahora=NOW + 631_000)
    assert "SISTEMA_TELEMETRIA_STALE" not in res2["derivados"]
    assert heartbeat_tipos(writer).count("SISTEMA_TELEMETRIA_STALE") == 1


def test_stale_suprimido_cuando_ota_descarga(writer):
    """Anti-falsa-alarma (SPEC §16.7): el agujero de OTA es esperado."""
    writer.procesar(
        topic("aulasense_temp"), {"timestamp_ms": 1, "die_temp_c": 45.0}, now_ms=NOW
    )
    writer.procesar(topic("ota"), OTA_OK, now_ms=NOW)  # estado downloading
    res = writer.escribir_heartbeat(True, ahora=NOW + 700_000)
    assert "SISTEMA_TELEMETRIA_STALE" not in res["derivados"]


def test_disco_warn_borde_unico(writer, monkeypatch):
    monkeypatch.setattr(
        ingesta_mqtt.shutil,
        "disk_usage",
        lambda ruta: types.SimpleNamespace(total=100, used=90, free=10),
    )
    res = writer.escribir_heartbeat(True, ahora=NOW)
    assert "SISTEMA_DISCO_WARN" in res["derivados"]
    res2 = writer.escribir_heartbeat(True, ahora=NOW + 30_000)
    assert "SISTEMA_DISCO_WARN" not in res2["derivados"]
    assert heartbeat_tipos(writer).count("SISTEMA_DISCO_WARN") == 1


# --- 5. Integracion con el ciclo del PollerDiagnosticos ----------------------


def test_ciclo_poller_escribe_heartbeat_hasta_desconectado(writer):
    cliente = ClienteStub(conectado=False)
    poller = PollerDiagnosticos(cliente, writer, PREFIJO, intervalo_s=1.0)
    poller.ciclo()
    filas = consultar(
        writer, "SELECT ts_ms, mqtt_conectado FROM estado_sistema WHERE id = 1"
    )
    assert len(filas) == 1 and filas[0][1] == 0
    assert cliente.publicados == []  # desconectado: ni read_diagnostics


def test_ciclo_poller_mantiene_read_diagnostics_y_heartbeat_conectado(writer):
    registrar_device(writer)
    cliente = ClienteStub(conectado=True)
    poller = PollerDiagnosticos(cliente, writer, PREFIJO, intervalo_s=1.0)
    poller.ciclo()
    filas = consultar(writer, "SELECT mqtt_conectado FROM estado_sistema WHERE id = 1")
    assert filas[0][0] == 1
    assert len(cliente.publicados) == 1
    publicado = cliente.publicados[0]
    assert publicado["topic"] == f"{PREFIJO}/{DEV}/commands/request"
    assert json.loads(publicado["payload"])["command"] == "read_diagnostics"


# --- 6. publicar_comando_ota: topic propio + envelope exacto (D-F26-2) -------


def test_publicar_comando_ota_envelope_exacto():
    cliente = ClienteStub()
    registro = publicar_comando_ota(cliente, DEV, "check", ahora=NOW)
    assert len(cliente.publicados) == 1
    publicado = cliente.publicados[0]
    assert publicado["topic"] == f"{PREFIJO}/{DEV}/aulasense_ota"
    assert publicado["qos"] == 0
    envelope = json.loads(publicado["payload"])
    assert envelope == {
        "command_id": f"ota-{NOW}",
        "command": "aulasense_ota",
        "action": "check",
    }
    assert re.fullmatch(r"[\x20-\x7E]{1,64}", envelope["command_id"])
    assert registro["command_id"] == envelope["command_id"]


def test_publicar_comando_ota_apply_y_prefijo_propio():
    cliente = ClienteStub()
    publicar_comando_ota(
        cliente, DEV, "apply", topic_prefix="otro/prefijo", ahora=NOW + 1
    )
    envelope = json.loads(cliente.publicados[0]["payload"])
    assert envelope["action"] == "apply"
    assert cliente.publicados[0]["topic"] == f"otro/prefijo/{DEV}/aulasense_ota"


@pytest.mark.parametrize("action,device", [("reset", DEV), ("check", "ZZZZ")])
def test_publicar_comando_ota_valida_entradas(action, device):
    cliente = ClienteStub()
    with pytest.raises(ValueError):
        publicar_comando_ota(cliente, device, action)
    assert cliente.publicados == []


# --- 7. Loader de config: .env UNICO raiz (D-F26-6) --------------------------


def test_defecto_loader_es_env_raiz():
    """El destino por defecto es ``aulasense/.env`` (raiz), nunca claves/."""
    from pathlib import Path

    raiz_repo = Path(esquema.__file__).resolve().parents[2]
    assert ingesta_mqtt._DOTENV_DEFECTO.name == ".env"
    assert ingesta_mqtt._DOTENV_DEFECTO.parent == raiz_repo
    assert "claves" not in str(ingesta_mqtt._DOTENV_DEFECTO)


def test_from_env_lee_el_env_unico_raiz(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("MQTT_HOST=broker.f26.test\nMQTT_PORT=14769\n", encoding="utf-8")
    for clave in ("MQTT_HOST", "MQTT_PORT", "MQTT_SCHEME", "MQTT_USER", "MQTT_PASS"):
        monkeypatch.delenv(clave, raising=False)
    monkeypatch.setenv("AULASENSE_ENV_RAIZ", str(env))
    cfg = MqttConfig.from_env()
    assert cfg.host == "broker.f26.test"
    assert cfg.port == 14769
    # descripcion(): resumen seguro, jamas la clave.
    assert "pass" not in cfg.descripcion().lower()


def test_from_env_path_explicito_sigue_vivo(tmp_path, monkeypatch):
    """Compatibilidad: el argv/ruta explicita sigue mandando (sin tocar cliente/auth)."""
    env = tmp_path / "otro.env"
    env.write_text("MQTT_HOST=explicito.test\n", encoding="utf-8")
    monkeypatch.delenv("MQTT_HOST", raising=False)
    cfg = MqttConfig.from_env(env)
    assert cfg.host == "explicito.test"
