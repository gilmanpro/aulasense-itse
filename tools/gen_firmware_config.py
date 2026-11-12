"""gen_firmware_config.py — WiFi/identidad del nodo desde .env -> sdkconfig.defaults.local.

Uso (SPEC §4):
  C:\\Python315\\python.exe tools\\gen_firmware_config.py
  C:\\Python315\\python.exe tools\\gen_firmware_config.py --out ruta\\sdkconfig.defaults.local

Flujo:
- Lee WIFI_SSID / WIFI_PASS / DEVICE_LABEL del `.env` (dotenv).
- Escribe líneas estilo Kconfig:
      CONFIG_AULASENSE_WIFI_SSID="..."
      CONFIG_AULASENSE_WIFI_PASS="..."
- Default output: aulasense\\.tmp\\test\\sdkconfig.defaults.local (en F3+ el orquestador
  lo apuntará a firmware\\parche_aulasense\\); esta tool NO escribe dentro de firmware\\.

Secretos: la clave WiFi NUNCA se imprime ni viaja por argv; el archivo generado es
GITIGNORED (`firmware/**/sdkconfig.defaults.local` y `.tmp/`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]  # aulasense/
ENV_DEFECTO = ROOT / ".env"
OUT_DEFECTO = ROOT / ".tmp" / "test" / "sdkconfig.defaults.local"

# Los nombres CONFIG_AULASENSE_* son los del parche propio (SPEC §3.1); hay que
# contrastarlos con los Kconfig nativos de ESPectre al integrar el parche en F3.
NOTA_KCONFIG = "# NOTA: verificar nombres Kconfig nativos ESPectre al integrar F3"


def escapar_kconfig(valor: str) -> str:
    """Escapa un valor para una cadena Kconfig: solo comillas dobles y backslash."""
    return valor.replace("\\", "\\\\").replace('"', '\\"')


def construir_contenido(ssid: str, password: str, label: str) -> str:
    """Contenido completo del sdkconfig.defaults.local (sin imprimir la clave)."""
    lineas = [
        "# sdkconfig.defaults.local — GENERADO por tools/gen_firmware_config.py desde .env",
        "# GITIGNORED: contiene credenciales reales. NO commitear, NO pegar en docs/logs.",
        NOTA_KCONFIG,
        f'CONFIG_AULASENSE_WIFI_SSID="{escapar_kconfig(ssid)}"',
        f'CONFIG_AULASENSE_WIFI_PASS="{escapar_kconfig(password)}"',
    ]
    if label:
        lineas.append(f'CONFIG_AULASENSE_DEVICE_LABEL="{escapar_kconfig(label)}"')
    return "\n".join(lineas) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Genera sdkconfig.defaults.local (WiFi/identidad) desde .env, sin imprimir la clave."
    )
    ap.add_argument("--env", default=str(ENV_DEFECTO), help="ruta del .env a leer")
    ap.add_argument("--out", default=str(OUT_DEFECTO), help="ruta del sdkconfig.defaults.local a escribir")
    args = ap.parse_args(argv)

    env_path = Path(args.env)
    if not env_path.exists():
        print(f"[gen_firmware_config] ERROR: no existe {env_path}")
        print("[gen_firmware_config] Copia .env.example a .env y rellena WIFI_SSID/WIFI_PASS.")
        return 1

    cfg = dotenv_values(env_path)
    ssid = str(cfg.get("WIFI_SSID") or "").strip()
    password = str(cfg.get("WIFI_PASS") or "")
    label = str(cfg.get("DEVICE_LABEL") or "").strip()

    if not ssid:
        print("[gen_firmware_config] ERROR: WIFI_SSID vacío en .env (red 2.4 GHz obligatoria para CSI).")
        return 1
    if not password:
        # Red abierta o WPA sin clave: se avisa pero se genera igual (P1 no aplica aqui:
        # el operador decide; el nodo fallara al conectar a una red con clave).
        print("[gen_firmware_config] WARN: WIFI_PASS vacía; el nodo no podrá unirse a una red WPA2.")

    contenido = construir_contenido(ssid, password, label)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(contenido, encoding="utf-8")

    # Resumen SIN secretos: jamás el valor de WIFI_PASS (ni el SSID completo).
    print(f"[gen_firmware_config] WIFI_SSID: presente (len={len(ssid)})")
    print(f"[gen_firmware_config] WIFI_PASS: {'presente (oculta)' if password else 'vacía'}")
    print(f"[gen_firmware_config] DEVICE_LABEL: {label or '(vacío)'}")
    print(f"[gen_firmware_config] escrito: {out_path}")
    print("[gen_firmware_config] GITIGNORED: no commitear este archivo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())