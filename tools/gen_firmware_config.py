"""gen_firmware_config.py — WiFi/identidad/provisioning/OTA del nodo desde el .env UNICO.

Uso (SPEC §4, §16.2, §16.9):
  C:\\Python315\\python.exe tools\\gen_firmware_config.py
  C:\\Python315\\python.exe tools\\gen_firmware_config.py --out ruta\\sdkconfig.defaults.local

Flujo (F26-b: saneo de claves fantasma + soporte PROVISION/OTA):
- Lee SOLO el `.env` UNICO de la raiz (SPEC §16.8; `claves/` ya no es fuente:
  valores migrados y archivados en claves/*.bak-f26).
- F24: si `AULASENSE_UPLOAD_TOKEN` está vacío, genera `secrets.token_hex(16)` y lo
  escribe EN EL PROPIO `.env` (reemplazo in situ de la línea, sin imprimir el valor).
  Semántica "última gana" (BUG-GATE-F26-1): el reemplazo toca TODAS las ocurrencias de
  la clave, para que el valor resuelto por `dotenv_values` (y los loaders) coincida con
  el del `sdkconfig.defaults.local`. Si el `.env` trae claves duplicadas se advierte
  por NOMBRE (jamás el valor).
- F26: vierte la pass canónica del AP de provisioning (`AULASENSE_PROVISION_AP_SSID/PASS`
  → `CONFIG_AULASENSE_PROVISION_AP_SSID/_PASS`) y la URL del manifiesto OTA
  (`AULASENSE_OTA_MANIFEST_URL` → `CONFIG_AULASENSE_OTA_MANIFEST_URL`) si están definidas.
- SANEAMIENTO: antes de escribir, CONTRASTA cada símbolo contra los Kconfig reales
  (`firmware/parche_aulasense/Kconfig.projbuild` y el Kconfig nativo de ESPectre).
  Se emiten SOLO líneas `CONFIG_*` cuyo símbolo EXISTE. Los nombres fantasma heredados
  (p. ej. `CONFIG_AULASENSE_WIFI_SSID/PASS/DEVICE_LABEL`) se suprimen: los defaults de
  red del sitio viven en `CONFIG_ESPECTRE_WIFI_SSID/PASSWORD` (vendor) y en la NVS
  puesta por el portal F26 (SPEC §16.2). Si el símbolo F26 aún no está en Kconfig
  (c1 en curso), se deja una NOTA y el emisor queda preparado para cuando c1 lo añada.

Secretos: ni la pass WiFi, ni la pass del AP ni el token se imprimen ni viajan por
argv (solo su longitud, `len=N`); el archivo generado es GITIGNORED
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

# Fuentes de símbolos Kconfig REALES (contrastadas en disco, F26-b):
KCONFIG_PARCHE = ROOT / "firmware" / "parche_aulasense" / "Kconfig.projbuild"
KCONFIG_NATIVE = (
    ROOT / "firmware" / "espectre" / "src" / "cpp" / "frontend" / "native"
    / "app" / "main" / "Kconfig.projbuild",
    # Los simbolos ESPECTRE_WIFI_* viven en el Kconfig del COMPONENTE esectre (no del app):
    ROOT / "firmware" / "espectre" / "src" / "cpp" / "frontend" / "native"
    / "espectre" / "Kconfig.projbuild",
)
_RE_CONFIG = re.compile(r"^\s*config\s+([A-Za-z0-9_]+)", re.MULTILINE)


def _rutas_kconfig() -> list[Path]:
    return [KCONFIG_PARCHE, *KCONFIG_NATIVE]


def simbolos_kconfig() -> set[str]:
    """Nombres `CONFIG_<X>` que REALMENTE existen en los Kconfig del árbol.

    Devuelve el conjunto sin prefijo `CONFIG_` (p. ej. {"AULASENSE_UPLOAD_TOKEN",
    "ESPECTRE_WIFI_SSID", ...}). Archivos ausentes (vendor no clonado todavía) →
    conjunto parcial SOLO del parche: la función NUNCA falla.
    """
    vivos: set[str] = set()
    for ruta in _rutas_kconfig():
        try:
            if ruta.is_file():
                vivos.update(_RE_CONFIG.findall(ruta.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return vivos


def escapar_kconfig(valor: str) -> str:
    """Escapa un valor para una cadena Kconfig: solo comillas dobles y backslash."""
    return valor.replace("\\", "\\\\").replace('"', '\\"')


# nombre Kconfig (sin prefijo CONFIG_) -> (clave del .env, es_string)
# Solo se EMITEN las cuyo símbolo esté en `simbolos_kconfig()`; las demás quedan
# anotadas como "preparadas" (c1 define los símbolos F26 en curso).
ASIGNACIONES: list[tuple[str, str, bool]] = [
    # Red del sitio: símbolo REAL del vendor native (el AULASENSE_WIFI_* era fantasma).
    ("ESPECTRE_WIFI_SSID", "WIFI_SSID", True),
    ("ESPECTRE_WIFI_PASSWORD", "WIFI_PASS", True),
    # DEVICE_LABEL no existe como symbol Kconfig (el id de nodo es la MAC);
    # si algun dia el parche lo define, se emitira automaticamente.
    ("AULASENSE_DEVICE_LABEL", "DEVICE_LABEL", True),
    # F24: receptor HTTP alternativo (existe en el parche).
    ("AULASENSE_UPLOAD_HOST", "AULASENSE_UPLOAD_HOST", True),
    ("AULASENSE_UPLOAD_PORT", "AULASENSE_UPLOAD_PORT", False),
    ("AULASENSE_UPLOAD_TOKEN", "AULASENSE_UPLOAD_TOKEN", True),
    # F26 (SPEC §16.2): AP de provisioning FIJO — símbolos que define c1.
    # PASS vacía = AP abierto con aviso (rama de degradación); si el .env la
    # define, se vierte la canónica del dueño.
    ("AULASENSE_PROVISION_AP_SSID", "AULASENSE_PROVISION_AP_SSID", True),
    ("AULASENSE_PROVISION_AP_PASS", "AULASENSE_PROVISION_AP_PASS", True),
    # F26 (SPEC §16.1/§16.9): manifiesto OTA (URL pública vía túnel pub-aulasense-ota).
    ("AULASENSE_OTA_MANIFEST_URL", "AULASENSE_OTA_MANIFEST_URL", True),
]

NOTA_OMITIDOS = (
    "# NOTA F26 (SPEC 16.2): WIFI_SSID/WIFI_PASS de la red del sitio se proveen por el"
    " portal\n# fijo /cfg -> NVS en campo; los defaults de compilacion son"
    " CONFIG_ESPECTRE_WIFI_SSID/PASSWORD\n# (vendor). CONFIG_AULASENSE_WIFI_SSID/PASS/"
    "DEVICE_LABEL eran nombres FANTASMA y ya no se emiten."
)


def construir_contenido(
    cfg: dict[str, str],
    upload_token: str,
    vivos: set[str],
) -> tuple[str, list[str], list[str]]:
    """Contenido del sdkconfig.defaults.local + (emitidos, omitidos). Sin imprimir secretos."""
    lineas = [
        "# sdkconfig.defaults.local — GENERADO por tools/gen_firmware_config.py desde el .env UNICO",
        "# GITIGNORED: contiene credenciales reales. NO commitear, NO pegar en docs/logs.",
        NOTA_OMITIDOS,
    ]
    emitidos: list[str] = []
    omitidos: list[str] = []
    for simbolo, clave_env, es_string in ASIGNACIONES:
        valor = str(cfg.get(clave_env) or "")
        if clave_env == "AULASENSE_UPLOAD_TOKEN":
            valor = upload_token
        if simbolo not in vivos:
            if valor.strip():
                omitidos.append(f"{simbolo} (valor presente en .env; simbolo ausente en Kconfig)")
            continue
        if not valor.strip():
            continue
        if es_string:
            lineas.append(f'CONFIG_{simbolo}="{escapar_kconfig(valor)}"')
        else:
            lineas.append(f"CONFIG_{simbolo}={int(valor)}")
        emitidos.append(simbolo)
    if omitidos:
        lineas.append("# PREPARADOS (a la espera del simbolo en Kconfig — fase c1/c3):")
        for nota in omitidos:
            lineas.append(f"#   {nota}")
    return "\n".join(lineas) + "\n", emitidos, omitidos


def escribir_env(env_path: Path, clave: str, valor: str) -> None:
    """Reemplaza (o añade) ``clave=valor`` en el `.env` preservando sus saltos de línea.

    Semántica "ÚLTIMA GANA" (BUG-GATE-F26-1): ``dotenv_values`` y el resto de
    loaders se quedan con la ÚLTIMA línea de una clave duplicada. Por eso el
    reemplazo toca TODAS las ocurrencias — tocar solo la primera dejaba el valor
    resuelto en la vieja (vacía), el sdkconfig emitía el token recién generado y
    cada pasada volvía a generar otro (no idempotente). Todas quedan iguales.
    """
    datos = env_path.read_bytes()
    patron = re.compile(rb"^" + re.escape(clave.encode()) + rb"=[^\r\n]*", re.MULTILINE)
    linea = clave.encode() + b"=" + valor.encode()
    if patron.search(datos):
        datos = patron.sub(lambda _m: linea, datos)  # TODAS: coherente con última-gana
    else:
        salto = b"\r\n" if b"\r\n" in datos else b"\n"
        if datos and not datos.endswith(b"\n"):
            datos += salto
        datos += linea + salto
    env_path.write_bytes(datos)


def claves_duplicadas(env_path: Path) -> list[str]:
    """Nombres de clave repetidos DENTRO del `.env` (solo nombres, jamás valores).

    Un duplicado no rompe el archivo (dotenv resuelve "última gana"), pero es una
    trampa silenciosa: la primera línea no la ve ningún loader. Se advierte por
    nombre para que el humano limpie el `.env` (BUG-GATE-F26-1).
    """
    vistas: set[str] = set()
    repetidas: set[str] = set()
    try:
        texto = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for raw in texto.splitlines():
        s = raw.strip().lstrip("\ufeff")  # BOM de un editor Windows: no es parte del nombre
        if not s or s.startswith("#") or "=" not in s:
            continue
        nombre = s.split("=", 1)[0].strip()
        if not nombre:
            continue
        if nombre in vistas:
            repetidas.add(nombre)
        vistas.add(nombre)
    return sorted(repetidas)


def asegurar_token_upload(env_path: Path, cfg: dict) -> tuple[str, bool]:
    """Devuelve ``(token, generado)``; escribe el token nuevo SOLO en el `.env`."""
    token = str(cfg.get("AULASENSE_UPLOAD_TOKEN") or "").strip()
    if token:
        return token, False
    token = secrets.token_hex(16)
    escribir_env(env_path, "AULASENSE_UPLOAD_TOKEN", token)
    return token, True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Genera sdkconfig.defaults.local (WiFi/PROVISION/OTA) desde el .env UNICO de la "
            "raiz, emitiendo SOLO simbolos Kconfig existentes y sin imprimir secretos."
        )
    )
    ap.add_argument("--env", default=str(ENV_DEFECTO), help="ruta del .env UNICO a leer (raiz, SPEC 16.8)")
    ap.add_argument("--out", default=str(OUT_DEFECTO), help="ruta del sdkconfig.defaults.local a escribir")
    args = ap.parse_args(argv)

    env_path = Path(args.env)
    if not env_path.exists():
        print(f"[gen_firmware_config] ERROR: no existe {env_path}")
        print("[gen_firmware_config] Ejecuta primero: tools/consolidar_env.py --unificar (o copia .env.example a .env).")
        return 1

    cfg = {k: (v or "") for k, v in dotenv_values(env_path).items()}

    # BUG-GATE-F26-1: el .env acepta duplicados (dotenv resuelve última-gana), pero
    # son una trampa silenciosa. Se avisa por NOMBRE (nunca el valor).
    repetidas = claves_duplicadas(env_path)
    if repetidas:
        print(
            "[gen_firmware_config] WARN: claves duplicadas en el .env (gana la ULTIMA,"
            f" semantica dotenv): {', '.join(repetidas)} — limpiar a UNA linea por clave."
        )

    ssid = str(cfg.get("WIFI_SSID") or "").strip()
    password = str(cfg.get("WIFI_PASS") or "")
    if not ssid:
        print("[gen_firmware_config] WARN: WIFI_SSID vacio (se omite el default de compilacion; la red del sitio va por portal/NVS, SPEC 16.2).")
    if not password:
        print("[gen_firmware_config] WARN: WIFI_PASS vacia; el default compilado no podra unirse a una red WPA2.")

    # F24: token del receptor de imagen. Si falta, se genera y se escribe en .env
    # (reemplazo in situ); el valor NUNCA se imprime, solo "len=N".
    token_upload, token_generado = asegurar_token_upload(env_path, cfg)

    # F26: la pass canónica del AP es orden explícita del dueño; si el .env no la
    # define, se avisa (c2 requiere AULASENSE_PROVISION_AP_* rellenos).
    if not str(cfg.get("AULASENSE_PROVISION_AP_PASS") or "").strip():
        print("[gen_firmware_config] WARN: AULASENSE_PROVISION_AP_PASS vacia → el AP de provision saldra ABIERTO con aviso (rama SPEC 16.2).")

    vivos = simbolos_kconfig()
    contenido, emitidos, omitidos = construir_contenido(cfg, token_upload, vivos)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(contenido, encoding="utf-8")

    # Resumen SIN secretos: jamás valores, solo nombres/conteos/longitudes.
    print(f"[gen_firmware_config] .env fuente: {env_path} (canonico F26; claves/ NO se lee)")
    print(f"[gen_firmware_config] simbolos Kconfig contrastados: {len(vivos)} (parche+native)")
    print(f"[gen_firmware_config] emitidos: {len(emitidos)} -> {', '.join(emitidos)}")
    if omitidos:
        print(f"[gen_firmware_config] preparados-omitidos (sin simbolo aun): {len(omitidos)}")
        for nota in omitidos:
            print(f"  - {nota}")
    print(
        "[gen_firmware_config] AULASENSE_UPLOAD_TOKEN: "
        f"{'generado' if token_generado else 'presente'} len={len(token_upload)} (oculto)"
    )
    print(f"[gen_firmware_config] escrito: {out_path}")
    print("[gen_firmware_config] GITIGNORED: no commitear este archivo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
