"""gen_firmware_config.py — WiFi/identidad del nodo desde .env -> sdkconfig.defaults.local.

Uso (SPEC §4):
  C:\\Python315\\python.exe tools\\gen_firmware_config.py
  C:\\Python315\\python.exe tools\\gen_firmware_config.py --out ruta\\sdkconfig.defaults.local

Flujo:
- Lee WIFI_SSID / WIFI_PASS / DEVICE_LABEL del `.env` (dotenv).
- F24: si `AULASENSE_UPLOAD_TOKEN` está vacío, genera `secrets.token_hex(16)` y lo
  escribe EN EL PROPIO `.env` (reemplazo in situ de la línea, sin imprimir el valor).
  El token del receptor HTTP de imagen (SPEC §7.1) viaja al sdkconfig generado.
- Escribe líneas estilo Kconfig:
      CONFIG_AULASENSE_WIFI_SSID="..."
      CONFIG_AULASENSE_WIFI_PASS="..."
      CONFIG_AULASENSE_UPLOAD_HOST/PORT/TOKEN="..."
- Default output: aulasense\\.tmp\\test\\sdkconfig.defaults.local (en F3+ el orquestador
  lo apuntará a firmware\\parche_aulasense\\); esta tool NO escribe dentro de firmware\\.

Secretos: ni la clave WiFi ni el token se imprimen ni viajan por argv (solo su
longitud, `len=N`); el archivo generado es GITIGNORED
(`firmware/**/sdkconfig.defaults.local` y `.tmp/`).
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]  # aulasense/
ENV_DEFECTO = ROOT / ".env"
OUT_DEFECTO = ROOT / ".tmp" / "test" / "sdkconfig.defaults.local"

# Los nombres CONFIG_AULASENSE_* son los del parche propio (SPEC §3.1); hay que
# contrastarlos con los Kconfig nativos de ESPectre al integrar el parche en F3.
NOTA_KCONFIG = "# NOTA: verificar nombres Kconfig nativos ESPectre al integrar F3"
# F24-c (app_imagen.c/app_provision.c) aún no fija sus Kconfig en firmware/.
NOTA_KCONFIG_F24 = (
    "# NOTA F24-c: verificar nombres Kconfig de upload en firmware/parche_aulasense "
    "antes del build (app_imagen.c)"
)
PUERTO_UPLOAD_DEFECTO = 8001


def escapar_kconfig(valor: str) -> str:
    """Escapa un valor para una cadena Kconfig: solo comillas dobles y backslash."""
    return valor.replace("\\", "\\\\").replace('"', '\\"')


def construir_contenido(
    ssid: str,
    password: str,
    label: str,
    upload_host: str = "",
    upload_port: int = PUERTO_UPLOAD_DEFECTO,
    upload_token: str = "",
) -> str:
    """Contenido completo del sdkconfig.defaults.local (sin imprimir secretos)."""
    lineas = [
        "# sdkconfig.defaults.local — GENERADO por tools/gen_firmware_config.py desde .env",
        "# GITIGNORED: contiene credenciales reales. NO commitear, NO pegar en docs/logs.",
        NOTA_KCONFIG,
        f'CONFIG_AULASENSE_WIFI_SSID="{escapar_kconfig(ssid)}"',
        f'CONFIG_AULASENSE_WIFI_PASS="{escapar_kconfig(password)}"',
    ]
    if label:
        lineas.append(f'CONFIG_AULASENSE_DEVICE_LABEL="{escapar_kconfig(label)}"')
    lineas.append(NOTA_KCONFIG_F24)
    lineas.append(f'CONFIG_AULASENSE_UPLOAD_HOST="{escapar_kconfig(upload_host)}"')
    lineas.append(f"CONFIG_AULASENSE_UPLOAD_PORT={int(upload_port)}")
    lineas.append(f'CONFIG_AULASENSE_UPLOAD_TOKEN="{escapar_kconfig(upload_token)}"')
    return "\n".join(lineas) + "\n"


def escribir_env(env_path: Path, clave: str, valor: str) -> None:
    """Reemplaza (o añade) ``clave=valor`` en el `.env` preservando sus saltos de línea."""
    datos = env_path.read_bytes()
    patron = re.compile(rb"^" + re.escape(clave.encode()) + rb"=[^\r\n]*", re.MULTILINE)
    linea = clave.encode() + b"=" + valor.encode()
    if patron.search(datos):
        datos = patron.sub(lambda _m: linea, datos, count=1)
    else:
        salto = b"\r\n" if b"\r\n" in datos else b"\n"
        if datos and not datos.endswith(b"\n"):
            datos += salto
        datos += linea + salto
    env_path.write_bytes(datos)


def asegurar_token_upload(env_path: Path, cfg: dict) -> tuple[str, bool]:
    """Devuelve ``(token, generado)``; escribe el token nuevo SOLO en el `.env`."""
    token = str(cfg.get("AULASENSE_UPLOAD_TOKEN") or "").strip()
    if token:
        return token, False
    token = secrets.token_hex(16)
    escribir_env(env_path, "AULASENSE_UPLOAD_TOKEN", token)
    return token, True


def _puerto_upload(cfg: dict) -> int:
    crudo = str(cfg.get("AULASENSE_UPLOAD_PORT") or "").strip()
    try:
        return int(crudo) if crudo else PUERTO_UPLOAD_DEFECTO
    except ValueError:
        return PUERTO_UPLOAD_DEFECTO


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

    # F24: token del receptor de imagen. Si falta, se genera y se escribe en .env
    # (reemplazo in situ); el valor NUNCA se imprime, solo "len=N".
    token_upload, token_generado = asegurar_token_upload(env_path, cfg)
    upload_host = str(cfg.get("AULASENSE_UPLOAD_HOST") or "").strip()

    contenido = construir_contenido(
        ssid,
        password,
        label,
        upload_host=upload_host,
        upload_port=_puerto_upload(cfg),
        upload_token=token_upload,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(contenido, encoding="utf-8")

    # Resumen SIN secretos: jamás el valor de WIFI_PASS ni el del token.
    print(f"[gen_firmware_config] WIFI_SSID: presente (len={len(ssid)})")
    print(f"[gen_firmware_config] WIFI_PASS: {'presente (oculta)' if password else 'vacía'}")
    print(f"[gen_firmware_config] DEVICE_LABEL: {label or '(vacío)'}")
    print(
        "[gen_firmware_config] AULASENSE_UPLOAD_TOKEN: "
        f"{'generado' if token_generado else 'presente'} len={len(token_upload)} (oculto)"
    )
    if not upload_host:
        print(
            "[gen_firmware_config] AULASENSE_UPLOAD_HOST: (vacío) → receptor de imagen DESACTIVADO"
        )
    print(f"[gen_firmware_config] escrito: {out_path}")
    print("[gen_firmware_config] GITIGNORED: no commitear este archivo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())