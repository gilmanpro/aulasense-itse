"""Tests de las tools de PC (`tools/`): gen_firmware_config, config_mqtt y lee_serial.

Regla de secretos: ningún test ni el código bajo prueba deben imprimir la clave de
prueba; los tests lo afirman capturando stdout/stderr con capsys.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]  # aulasense/

CLAVE_FALSA = "clave-secreta-de-prueba-42"


def _cargar(nombre: str, ruta: Path):
    """Carga un módulo por ruta (tools/ no es paquete instalable)."""
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


gen_firmware_config = _cargar("gen_firmware_config_aula", ROOT / "tools" / "gen_firmware_config.py")
config_mqtt = _cargar("config_mqtt_aula", ROOT / "tools" / "config_mqtt.py")
lee_serial = _cargar("lee_serial_aula", ROOT / "tools" / "lee_serial.py")


def _env_falso(tmp_path: Path, texto: str) -> Path:
    ruta = tmp_path / ".env"
    ruta.write_text(texto, encoding="utf-8")
    return ruta


# --- gen_firmware_config ---------------------------------------------------
# F26-b: la tool lee el .env UNICO y emite SOLO simbolos que existen en Kconfig
# (saneo de los nombres fantasma AULASENSE_WIFI_*/DEVICE_LABEL). Los tests fijan
# el conjunto de simbolos "vivos" por monkeypatch para ser deterministas sin
# depender del clone vendor en disco.

VIVOS_PRUEBA = {
    "ESPECTRE_WIFI_SSID", "ESPECTRE_WIFI_PASSWORD",
    "AULASENSE_UPLOAD_HOST", "AULASENSE_UPLOAD_PORT", "AULASENSE_UPLOAD_TOKEN",
    "AULASENSE_PROVISION_AP_SSID", "AULASENSE_PROVISION_AP_PASS",
}


def test_gen_firmware_config_escribe_y_oculta_clave(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(gen_firmware_config, "simbolos_kconfig", lambda: set(VIVOS_PRUEBA))
    env = _env_falso(
        tmp_path,
        "WIFI_SSID=AulaDemo-2G\n"
        f"WIFI_PASS={CLAVE_FALSA}\n"
        "DEVICE_LABEL=AulaSense-Test\n",
    )
    salida = tmp_path / "sdkconfig.defaults.local"

    codigo = gen_firmware_config.main(["--env", str(env), "--out", str(salida)])
    capturado = capsys.readouterr()

    assert codigo == 0
    contenido = salida.read_text(encoding="utf-8")
    # simbolos REALES del vendor native:
    assert 'CONFIG_ESPECTRE_WIFI_SSID="AulaDemo-2G"' in contenido
    assert f'CONFIG_ESPECTRE_WIFI_PASSWORD="{CLAVE_FALSA}"' in contenido
    # nombres FANTASMA: suprimidos (el archivo no los ASIGNA; la nota explicativa
    # puede mencionarlos), y DEVICE_LABEL no tiene simbolo Kconfig → queda anotado
    # como omitido, NUNCA emitido.
    assert 'CONFIG_AULASENSE_WIFI_SSID="' not in contenido
    assert 'CONFIG_AULASENSE_WIFI_PASS="' not in contenido
    assert 'CONFIG_AULASENSE_DEVICE_LABEL="AulaSense-Test"' not in contenido
    assert "FANTASMA" in contenido  # nota explicativa del saneamiento

    # La clave NUNCA sale por consola (solo vive en el sdkconfig gitignored)
    assert CLAVE_FALSA not in capturado.out
    assert CLAVE_FALSA not in capturado.err
    assert "AULASENSE_UPLOAD_TOKEN" in capturado.out


def test_gen_firmware_config_ssid_vacio_degrada_sin_error(tmp_path, capsys, monkeypatch):
    """F26 (SPEC 16.2): la red del sitio se provisiona por portal/NVS; WIFI_SSID vacio
    es WARN no-bloqueante (se omite el default de compilacion), rc 0."""
    monkeypatch.setattr(gen_firmware_config, "simbolos_kconfig", lambda: set(VIVOS_PRUEBA))
    env = _env_falso(tmp_path, f"WIFI_SSID=\nWIFI_PASS={CLAVE_FALSA}\n")
    salida = tmp_path / "sdkconfig.defaults.local"

    codigo = gen_firmware_config.main(["--env", str(env), "--out", str(salida)])
    capturado = capsys.readouterr()

    assert codigo == 0
    assert "WIFI_SSID vacio" in capturado.out
    assert CLAVE_FALSA not in capturado.out
    contenido = salida.read_text(encoding="utf-8")
    assert 'CONFIG_ESPECTRE_WIFI_SSID="' not in contenido  # no se emite linea vacia


def test_gen_firmware_config_vierte_provision_y_anota_no_queima(tmp_path, capsys, monkeypatch):
    """PROVISION del .env UNICO → sdkconfig; simbolo aun no definido en Kconfig
    (c1 en curso) → NO se emite, queda anotado como preparado."""
    monkeypatch.setattr(gen_firmware_config, "simbolos_kconfig",
                        lambda: {"AULASENSE_UPLOAD_HOST", "AULASENSE_UPLOAD_PORT",
                                 "AULASENSE_UPLOAD_TOKEN", "AULASENSE_PROVISION_AP_PASS"})
    env = _env_falso(
        tmp_path,
        "WIFI_SSID=AulaDemo-2G\nWIFI_PASS=pass-demo-prueba\n"
        "AULASENSE_PROVISION_AP_SSID=AULASENSE-CFG\n"
        "AULASENSE_PROVISION_AP_PASS=pass-canonica-de-prueba\n",
    )
    salida = tmp_path / "sdkconfig.defaults.local"

    codigo = gen_firmware_config.main(["--env", str(env), "--out", str(salida)])
    capturado = capsys.readouterr()

    assert codigo == 0
    contenido = salida.read_text(encoding="utf-8")
    assert 'CONFIG_AULASENSE_PROVISION_AP_PASS="pass-canonica-de-prueba"' in contenido
    assert "CONFIG_AULASENSE_PROVISION_AP_SSID" not in contenido  # sin simbolo aun
    assert "PREPARADOS" in contenido
    assert "pass-canonica-de-prueba" not in capturado.out  # nunca impresa


# --- config_mqtt -----------------------------------------------------------


def test_config_mqtt_check_valida_sin_enviar(tmp_path, capsys, monkeypatch):
    env = _env_falso(
        tmp_path,
        "MQTT_SCHEME=mqtt\n"
        "MQTT_HOST=broker.example.test\n"
        "MQTT_PORT=14769\n"
        "MQTT_USER=usuario-demo\n"
        f"MQTT_PASS={CLAVE_FALSA}\n"
        "MQTT_TOPIC_PREFIX=aulasense/v1/devices\n",
    )

    def _no_debe_enviar(*_args, **_kwargs):
        raise AssertionError("--check no debe enviar nada al nodo")

    monkeypatch.setattr(requests, "patch", _no_debe_enviar)

    codigo = config_mqtt.main(["--check", "--env", str(env)])
    capturado = capsys.readouterr()

    assert codigo == 0
    assert "broker.example.test" in capturado.out
    assert "presente (oculta)" in capturado.out
    assert CLAVE_FALSA not in capturado.out
    assert CLAVE_FALSA not in capturado.err


def test_config_mqtt_patch_contrato_y_redaccion(tmp_path, capsys, monkeypatch):
    env = _env_falso(
        tmp_path,
        "MQTT_SCHEME=mqtts\n"
        "MQTT_HOST=broker.example.test\n"
        "MQTT_PORT=14769\n"
        "MQTT_USER=usuario-demo\n"
        f"MQTT_PASS={CLAVE_FALSA}\n"
        "MQTT_TOPIC_PREFIX=aulasense/v1/devices\n",
    )

    visto: dict = {}

    class _RespuestaNodo:
        status_code = 200
        text = '{"ok":true,"configured":true,"password":"eco-interno-99"}'

        def json(self) -> dict:
            return {"ok": True, "configured": True, "password": "eco-interno-99"}

    def _patch(url, json=None, headers=None, timeout=None):
        visto["url"] = url
        visto["body"] = json
        visto["headers"] = headers
        visto["timeout"] = timeout
        return _RespuestaNodo()

    monkeypatch.setattr(requests, "patch", _patch)

    codigo = config_mqtt.main(["--host", "192.0.2.10", "--env", str(env)])
    capturado = capsys.readouterr()

    assert codigo == 0
    assert visto["url"] == "http://192.0.2.10:62587/espectre/v1/mqtt"
    assert visto["timeout"] == 10
    assert visto["headers"]["Origin"] == "https://test.espectre.dev"
    assert visto["headers"]["Content-Type"] == "application/json"
    assert visto["body"] == {
        "scheme": "mqtts",
        "host": "broker.example.test",
        "port": 14769,
        "username": "usuario-demo",
        "password": CLAVE_FALSA,
        "topic_prefix": "aulasense/v1/devices",
    }
    # Ni la clave del .env ni el eco del nodo salen por consola
    assert CLAVE_FALSA not in capturado.out
    assert CLAVE_FALSA not in capturado.err
    assert "eco-interno-99" not in capturado.out
    assert "***" in capturado.out


# --- lee_serial (mecánica pura, sin abrir hardware) ------------------------


def test_lee_serial_resuelve_ruta_com():
    assert lee_serial.resolver_puerto("COM6") == "\\\\.\\COM6"
    assert lee_serial.resolver_puerto(r"\\.\COM12") == r"\\.\COM12"
    with pytest.raises(ValueError):
        lee_serial.resolver_puerto("ttyUSB0")
    with pytest.raises(ValueError):
        lee_serial.resolver_puerto("")