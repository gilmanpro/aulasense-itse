"""config_mqtt.py — configura el broker MQTT del nodo vía Direct HTTP (SPEC §5, F1d).

Hace un PATCH a `http://{host}:62587/espectre/v1/mqtt` con el cuerpo
`{scheme,host,port,username,password,topic_prefix}` leído del `.env` (MQTT_*).
Headers obligatorios: `Origin: https://test.espectre.dev` + `Content-Type: application/json`
(el firmware acepta solo orígenes listados, igual que el CLI; SPEC §5).

Uso:
  C:\\Python315\\python.exe tools\\config_mqtt.py --check          # solo valida el .env
  C:\\Python315\\python.exe tools\\config_mqtt.py --host 192.168.1.42
  C:\\Python315\\python.exe tools\\config_mqtt.py                  # 127.0.0.1 (placeholder)

Host: --host > env AULASENSE_NODE_IP > 127.0.0.1. El default 127.0.0.1 es solo para
probar el contrato en local; en F1d real será la IP del nodo en la LAN.

Secretos: la clave MQTT NUNCA se imprime ni se re-eco el cuerpo enviado; la respuesta
del nodo se muestra con los campos sensibles redactados.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]  # aulasense/
ENV_DEFECTO = ROOT / ".env"

ORIGIN = "https://test.espectre.dev"
PUERTO_NODO = 62587
RUTA_MQTT = "/espectre/v1/mqtt"
TIMEOUT_S = 10
HOST_DEFECTO = "127.0.0.1"
MAX_CUERPO_B = 2048  # límite del firmware para el cuerpo (SPEC §5)

CAMPOS_ENV = ("MQTT_SCHEME", "MQTT_HOST", "MQTT_PORT", "MQTT_USER", "MQTT_PASS", "MQTT_TOPIC_PREFIX")
_CLAVES_SENSIBLES = ("password", "pass", "secret", "token", "credential", "key")


def leer_cfg(env_path: Path) -> dict[str, str]:
    """Lee MQTT_* del .env. MQTT_PASS se conserva tal cual (sin strip)."""
    if not env_path.exists():
        return {clave: "" for clave in CAMPOS_ENV}
    bruto = dotenv_values(env_path)
    cfg = {clave: str(bruto.get(clave) or "") for clave in CAMPOS_ENV}
    for clave in CAMPOS_ENV:
        if clave != "MQTT_PASS":
            cfg[clave] = cfg[clave].strip()
    return cfg


def validar(cfg: dict[str, str]) -> list[str]:
    """Devuelve la lista de fallos de configuración (vacía = válida)."""
    fallos: list[str] = []
    if cfg["MQTT_SCHEME"] not in ("mqtt", "mqtts"):
        fallos.append("MQTT_SCHEME debe ser 'mqtt' (TCP plano) o 'mqtts' (TLS CA pública)")
    if not cfg["MQTT_HOST"]:
        fallos.append("MQTT_HOST vacío")
    try:
        puerto = int(cfg["MQTT_PORT"])
    except (TypeError, ValueError):
        fallos.append(f"MQTT_PORT no es entero: {cfg['MQTT_PORT']!r}")
    else:
        if not 1 <= puerto <= 65535:
            fallos.append(f"MQTT_PORT fuera de rango 1-65535: {puerto}")
    if not cfg["MQTT_USER"]:
        fallos.append("MQTT_USER vacío (el broker compartido exige auth)")
    if not cfg["MQTT_TOPIC_PREFIX"]:
        fallos.append("MQTT_TOPIC_PREFIX vacío (aísla del broker compartido)")
    return fallos


def construir_body(cfg: dict[str, str]) -> dict[str, Any]:
    """Cuerpo del PATCH /mqtt con los tipos del contrato."""
    return {
        "scheme": cfg["MQTT_SCHEME"],
        "host": cfg["MQTT_HOST"],
        "port": int(cfg["MQTT_PORT"]),
        "username": cfg["MQTT_USER"],
        "password": cfg["MQTT_PASS"],
        "topic_prefix": cfg["MQTT_TOPIC_PREFIX"],
    }


def resumen(cfg: dict[str, str]) -> str:
    """Resumen imprimible SIN la clave."""
    pass_estado = "presente (oculta)" if cfg["MQTT_PASS"] else "vacía (el broker puede rechazar)"
    return (
        f"scheme={cfg['MQTT_SCHEME']} host={cfg['MQTT_HOST']} port={cfg['MQTT_PORT']} "
        f"user={cfg['MQTT_USER']} pass={pass_estado} topic_prefix={cfg['MQTT_TOPIC_PREFIX']}"
    )


def normalizar_host(valor: str) -> str:
    """Acepta '1.2.3.4', 'http://1.2.3.4', '1.2.3.4:62587' -> '1.2.3.4'."""
    host = valor.strip().rstrip("/")
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]
    if host.count(":") == 1:  # IPv4[:puerto] (IPv6 no aplica en esta LAN)
        host = host.split(":", 1)[0]
    return host


def sanitizar(obj: Any) -> Any:
    """Redacta recursivamente valores de claves sensibles antes de imprimir."""
    if isinstance(obj, dict):
        limpio: dict[str, Any] = {}
        for clave, valor in obj.items():
            if isinstance(valor, (str, int, float)) and any(
                s in str(clave).lower() for s in _CLAVES_SENSIBLES
            ):
                limpio[clave] = "***"
            else:
                limpio[clave] = sanitizar(valor)
        return limpio
    if isinstance(obj, list):
        return [sanitizar(v) for v in obj]
    return obj


def enviar(host: str, cfg: dict[str, str]) -> int:
    """PATCH al nodo; traduce 4xx/5xx/timeout a mensajes útiles sin secretos."""
    url = f"http://{host}:{PUERTO_NODO}{RUTA_MQTT}"
    body = construir_body(cfg)
    tam = len(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    if tam > MAX_CUERPO_B:
        print(f"[config_mqtt] ERROR: cuerpo de {tam} B supera el límite de {MAX_CUERPO_B} B del firmware.")
        return 1

    cabeceras = {"Origin": ORIGIN, "Content-Type": "application/json"}
    print(f"[config_mqtt] PATCH {url} ({resumen(cfg)})")
    try:
        respuesta = requests.patch(url, json=body, headers=cabeceras, timeout=TIMEOUT_S)
    except requests.Timeout:
        print(f"[config_mqtt] ERROR: timeout de {TIMEOUT_S}s contra {url}.")
        print("[config_mqtt] ¿Está el nodo encendido, en la misma LAN y con Wi-Fi conectado?")
        return 1
    except requests.RequestException as exc:
        print(f"[config_mqtt] ERROR: no se pudo contactar {url} ({exc.__class__.__name__}).")
        print("[config_mqtt] Verifica --host/IP del nodo y que el puerto 62587 esté accesible.")
        return 1

    codigo = respuesta.status_code
    if codigo >= 500:
        print(f"[config_mqtt] ERROR: el nodo respondió HTTP {codigo} (fallo interno del firmware).")
        print("[config_mqtt] Revisa la consola serie del nodo y reintenta.")
        return 1
    if codigo >= 400:
        pista = {
            400: "cuerpo inválido: revisa scheme/port/topic_prefix en el .env",
            401: "el nodo exige preflight/origen autorizado; el header Origin ya va incluido",
            403: "origen rechazado por el firmware (Origin no listado)",
            404: "recurso /espectre/v1/mqtt no existe en esta versión del firmware",
            409: "el nodo está ocupado o en estado incompatible; reintenta en unos segundos",
            422: "payload rechazado por validación del firmware",
            429: "rate limit del nodo; espera unos segundos",
        }.get(codigo, "consulta el diagnóstico del nodo")
        print(f"[config_mqtt] ERROR: el nodo respondió HTTP {codigo} ({pista}).")
        return 1

    print(f"[config_mqtt] OK: HTTP {codigo}.")
    texto = (respuesta.text or "").strip()
    if texto:
        try:
            datos = respuesta.json()
        except ValueError:
            # No se imprime el bruto: podría reflejar configuración interna.
            print(f"[config_mqtt] respuesta no-JSON ({len(texto)} B, no se imprime para no arriesgar secretos).")
        else:
            print("[config_mqtt] respuesta del nodo: " + json.dumps(sanitizar(datos), ensure_ascii=False, separators=(",", ":")))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="PATCH /espectre/v1/mqtt del nodo (SPEC §5) leyendo MQTT_* del .env."
    )
    ap.add_argument("--host", default="", help="IP/host del nodo (default: AULASENSE_NODE_IP o 127.0.0.1)")
    ap.add_argument("--check", action="store_true", help="valida el .env sin enviar nada (no requiere --host)")
    ap.add_argument("--env", default=str(ENV_DEFECTO), help="ruta del .env a leer")
    args = ap.parse_args(argv)

    env_path = Path(args.env)
    if not env_path.exists():
        print(f"[config_mqtt] ERROR: no existe {env_path}; copia .env.example a .env y rellénalo.")
        return 1

    cfg = leer_cfg(env_path)
    fallos = validar(cfg)

    if args.check:
        print(f"[config_mqtt] --check: validando {env_path} (no se envía nada)")
        print(f"[config_mqtt] {resumen(cfg)}")
        if fallos:
            for fallo in fallos:
                print(f"[config_mqtt] FALLO: {fallo}")
            return 1
        print("[config_mqtt] OK: configuración válida para PATCH /espectre/v1/mqtt.")
        return 0

    if fallos:
        for fallo in fallos:
            print(f"[config_mqtt] ERROR: {fallo}")
        return 1

    host = normalizar_host(args.host or os.environ.get("AULASENSE_NODE_IP", "") or HOST_DEFECTO)
    if host == HOST_DEFECTO:
        print("[config_mqtt] AVISO: host 127.0.0.1 (placeholder); en F1d real usa --host <IP del nodo>.")
    return enviar(host, cfg)


if __name__ == "__main__":
    sys.exit(main())