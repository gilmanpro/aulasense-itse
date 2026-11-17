# -*- coding: utf-8 -*-
"""Consolida los .env del proyecto. Dos modos:

  (legado) sin flags : escribe `claves/.env.master` (referencia histórica F25).
  --unificar        : F26 (SPEC §16.8 / D-F26-6) — construye el **.env ÚNICO de la raiz**
                      con las 11 secciones ordenadas a partir de las fuentes
                      (.env raiz actual, claves/.env, claves/.env-wsl-nuevo y, como 4ª
                      fuente histórica solo-valores-ya-consolidados, claves/.env.master).

Garantías (regla 15 AGENTS + criterio F26-b):
- Los valores se copian BYTE-A-BYTE por este código: el modelo/orquestador NUNCA los ve.
- La salida por stdout son SOLO nombres, secciones y conteos. Jamás un valor.
- Colisiones de nombre: gana la PRIMERA fuente (orden FUENTES_UNIFICAR) y la duplicada
  queda anotada por nombre en un comentario.
- Duplicados DENTRO de un mismo archivo (.env mal editado): ``parsear`` conserva la
  primera, y el modo --unificar los ADVIERTE por nombre (nunca el valor).
- ANTES de reescribir, cada fuente se respalda en `claves/*.bak-f26` (no se pisa un
  respaldo existente).
- Con --archivar (solo tras verificar), las fuentes de claves/ quedan ARCHIVADAS: se
  renuevan sus originales a *.bak-f26 (ya no son fuentes canónicas; el canónico es la raiz).
- --dry-run no escribe nada: imprime secciones/nombres/conteos y la verificación prevista.

Uso:
  C:\\Python315\\python.exe tools\\consolidar_env.py --unificar --dry-run
  C:\\Python315\\python.exe tools\\consolidar_env.py --unificar --archivar
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# BASE relativa al propio archivo (fix pendiente del ciclo previo: estaba absoluta).
BASE = Path(__file__).resolve().parents[1]  # aulasense/

FUENTES = [
    ("RAIZ — WiFi/MQTT/upload (los que lee el firmware y la ingesta)", BASE / ".env"),
    ("CLAVES — admin dashboard + key IA (generadas por codigo, regla 15)", BASE / "claves" / ".env"),
    ("CLAVES — usuario/contrasena de la distro debian-aulasense", BASE / "claves" / ".env-wsl-nuevo"),
]
DESTINO_MASTER = BASE / "claves" / ".env.master"

# 4ª fuente del modo --unificar: el maestro F25 (solo aporta nombres ya consolidados, p. ej.
# el bloque REMOTO de URLs no secretas). Prioridad MINIMA: las 3 fuentes reales ganan.
FUENTE_MAESTRO = BASE / "claves" / ".env.master"

DESTINO_UNICO = BASE / ".env"  # el .env UNICO canónico (SPEC §16.8)

# Que es cada clave (comentario didactico; sin valores) — modo master legado.
DESC = {
    "WIFI_SSID": "SSID del router al que se asocia la placa (gen_firmware_config.py -> sdkconfig.defaults.local/NVS)",
    "WIFI_PASS": "Password del router de la placa (via NVS/Improv; NUNCA va al repo)",
    "ESPECTRE_WIFI_PASSWORD": "La misma pass WiFi, variante que lee el CLI/firmware ESPectre por env (regla 7)",
    "MQTT_HOST": "Broker publico del proyecto (broker.gilman.pro = VPS 167.114.169.134)",
    "MQTT_PORT": "Puerto TCP plano del broker (14769; sin TLS hasta decision del dueno)",
    "MQTT_USER": "Usuario MQTT del NODO (rol publicador 'electiva')",
    "MQTT_PASS": "Password del usuario MQTT (8 chars: el gate seguridad recomendo rotarla + ACLs)",
    "MQTT_USER_SRV": "Usuario MQTT de la INGESTA (rol suscriptor 'srv-electiva')",
    "MQTT_PASS_SRV": "Password de la ingesta para suscribirse al broker",
    "MQTT_TOPIC_PREFIX": "Prefijo que aisla nuestros topics en el broker compartido (aulasense/v1/devices)",
    "AULASENSE_UPLOAD_TOKEN": "Bearer del receptor HTTP N2 (:8001) — 32 hex generado por codigo",
    "AULASENSE_UPLOAD_HOST": "Host al que el firmware POSTEA el fallback N2 (hoy IP NAT WSL; caveat LAN documentado)",
    "AULASENSE_ORIGINS_EXTRA": "Origenes extra permitidos al dashboard (host:puerto exactos, sin wildcards)",
    "AULASENSE_ADMIN_USER": "Usuario admin unico del login del dashboard",
    "AULASENSE_ADMIN_PASSWORD": "Password del dashboard, GENERADA POR CODIGO (reset_admin.py la regenera; Argon2id/scrypt en BD)",
    "DASHSCOPE_API_KEY": "Key Token Plan (sk-sp-) para vision modo B — ToS: SOLO uso agente-mediado, jamas daemon",
    "OPENROUTER_API_KEY": "Key del dueno (sk-or-) para Clef/OpenRouter — existe, NO regenerar; SOLO en el .env unico, jamas impresa",
    "WSL_NEW_USER": "Usuario con sudo dentro de la distro debian-aulasense",
    "WSL_NEW_PASS": "Password de ese usuario, generada con secrets.token_hex (unica copia local)",
    "AULASENSE_VISION_MAX_DIA": "Techo de llamadas vision por dia (120)",
    "AULASENSE_VISION_MAX_HORA": "Techo por hora movil (20)",
    "AULASENSE_VISION_MODELO": "Modelo vision allowlisted TP (qwen3.8-flash)",
    "AULASENSE_VISION_ORDEN": "Orden de reclutamiento de las pasadas (asc/desc)",
    "AULASENSE_VISION_STALE_MIN": "Minutos antes de liberar un claim 'procesando' stale (30)",
    "AULASENSE_IMG_RETENCION_DIAS": "Dias de retencion de fotos antes de purga (30; la fila se conserva, dataset intacto)",
    "AULASENSE_UPLOAD_PORT": "Puerto del receptor HTTP N2 (8001)",
    "DEVICE_LABEL": "Etiqueta legible del nodo (aparece en eventos/dashboard; NO es secreto)",
    "MQTT_INGESTA_USER": "Usuario MQTT con el que la INGESTA se suscribe (rol servidor)",
    "MQTT_SCHEME": "Esquema de conexion del broker (tcp plano; tls pendiente de decision del dueno)",
    "OPENMETEO_LAT": "Latitud para la fuente clima Open-Meteo (gratis, sin key)",
    "OPENMETEO_LON": "Longitud para Open-Meteo",
    "AULASENSE_PROVISION_AP_SSID": "SSID FIJO del AP de provisioning (SPEC 16.2: AULASENSE-CFG, sin sufijo MAC)",
    "AULASENSE_PROVISION_AP_PASS": "Pass WPA2 canonica del AP de provisioning (orden del dueno; >=8; solo aqui + sdkconfig.defaults.local/NVS)",
    "AULASENSE_OTA_MANIFEST_URL": "URL publica del manifiesto OTA (tunel pub-aulasense-ota; firmware app_ota la lee via sdkconfig)",
    "AULASENSE_OTA_CHECK_MIN": "Minutos entre chequeos periodicos de OTA (0 = solo por comando MQTT)",
}

# ---------------------------------------------------------------------------
# Layout del .env UNICO (SPEC 16.8): secciones ORDENADAS. Cada entrada es
# (nombre, literal_default|None). None = SOLO desde fuentes (vacio si no hay).
# Los literales aqui listados son NO-secretos documentados en SPEC/PLAN o
# valores fijados por orden explicita del dueno (PROVISION); los secretos viajan
# byte-a-byte desde las fuentes, nunca desde este codigo.
# ---------------------------------------------------------------------------
SECCIONES: list[tuple[str, list[tuple[str, str | None]]]] = [
    ("BROKER — MQTT (VPS broker.gilman.pro)", [
        ("MQTT_HOST", None), ("MQTT_PORT", None), ("MQTT_SCHEME", None),
        ("MQTT_USER", None), ("MQTT_PASS", None), ("MQTT_INGESTA_USER", None),
        ("MQTT_TOPIC_PREFIX", None),
    ]),
    ("WIFI_NODO — red del sitio + identidad (2.4 GHz obligatorio para CSI)", [
        ("WIFI_SSID", None), ("WIFI_PASS", None),
        ("ESPECTRE_WIFI_PASSWORD", None), ("DEVICE_LABEL", None),
    ]),
    ("PROVISION_AP — portal fijo F26 (SPEC 16.2; pass canonica orden del dueno)", [
        ("AULASENSE_PROVISION_AP_SSID", "AULASENSE-CFG"),
        ("AULASENSE_PROVISION_AP_PASS", "itse-aulasense"),
    ]),
    ("UPLOAD_HTTP — receptor :8001 LAN-only (alternativo; MQTT es primario)", [
        ("AULASENSE_UPLOAD_HOST", None), ("AULASENSE_UPLOAD_PORT", None),
        ("AULASENSE_UPLOAD_TOKEN", None), ("AULASENSE_IMG_RETENCION_DIAS", "30"),
    ]),
    ("ADMIN — login del dashboard (generadas SOLO por codigo; regla 15)", [
        ("AULASENSE_ADMIN_USER", None), ("AULASENSE_ADMIN_PASSWORD", None),
    ]),
    ("IA — keys del dueno/proveedor (SE REFERENCIAN, NO se regeneran; jamas impresas)", [
        ("DASHSCOPE_API_KEY", None), ("OPENROUTER_API_KEY", ""),
    ]),
    ("VISION — worker de descripcion IA (rate limits obligatorios, regla 16)", [
        ("AULASENSE_VISION_MAX_DIA", "120"), ("AULASENSE_VISION_MAX_HORA", "20"),
        ("AULASENSE_VISION_MODELO", "qwen3.8-flash"), ("AULASENSE_VISION_MODO", "B"),
        ("AULASENSE_VISION_ORDEN", "asc"), ("AULASENSE_VISION_STALE_MIN", "30"),
    ]),
    ("CLEF — decisiones OpenRouter (cascada flash->clef; D-F26-7)", [
        ("AULASENSE_CLEF_MAX_DIA", "120"), ("AULASENSE_CLEF_MAX_HORA", "20"),
        ("AULASENSE_CLEF_MODELO_FLASH", "cloudflare/clef-flash"),
        ("AULASENSE_CLEF_MODELO", "cloudflare/clef"),
        ("AULASENSE_CLEF_UMBRAL_ESCALADO", "0.6"), ("AULASENSE_CLEF_NOUL_CUBRE_MIN", "0.7"),
    ]),
    ("MONITOREO — umbrales salud del sistema (SPEC 16.7) + clima C6", [
        ("AULASENSE_SALUD_TELEMETRIA_MAX_MIN", "5"), ("AULASENSE_SALUD_STALE_CRIT_MIN", "10"),
        ("AULASENSE_SALUD_DISCO_WARN_PCT", "85"), ("AULASENSE_SALUD_RSSI_WARN_DBM", "-80"),
        ("AULASENSE_SALUD_HEAP_WARN_KB", "120"),
        ("OPENMETEO_LAT", None), ("OPENMETEO_LON", None),
    ]),
    ("WSL — runtime primario debian-aulasense + seams opcionales (vacio=defectos)", [
        ("AULASENSE_DB", None), ("AULASENSE_DATOS_DIR", None), ("AULASENSE_HOST", None),
        ("AULASENSE_PORT", None), ("AULASENSE_ORIGINS_EXTRA", None),
        ("AULASENSE_INTERNAL_HOSTS", None), ("AULASENSE_COOKIE_SECURE", None),
        ("AULASENSE_AUTH_HASH", None),
        ("AULASENSE_CLAVES_ENV", ""), ("AULASENSE_ENV_RAIZ", ""),
        ("AULASENSE_VISION_AUTH_JSON", ""), ("AULASENSE_VISION_STOP_FILE", ""),
        ("WSL_NEW_USER", None), ("WSL_NEW_PASS", None),
    ]),
    ("REMOTO — URLs publicas y referencias (SIN password del VPS: la gestiona wsl-port)", [
        ("DASHBOARD_URL_PUBLIC", None), ("DASHBOARD_URL_LAN_WSL", None),
        ("DASHBOARD_URL_LAN_WIN", None), ("NODO_DIRECT_HTTP", None),
        ("PROVISION_PORTAL", "http://192.168.4.1/cfg"),
        ("AULASENSE_OTA_MANIFEST_URL", ""), ("AULASENSE_OTA_CHECK_MIN", "0"),
        ("VPS_SSH_HOST", None), ("VPS_SSH_PORT", None), ("VPS_SSH_USER", None),
        ("REMOTE_WIFI_SSID", None), ("REMOTE_WIFI_PASS", None),
    ]),
]


def parsear(ruta: Path) -> dict[str, str]:
    """nombre -> valor CRUDO (byte-a-byte, sin strip del valor)."""
    datos: dict[str, str] = {}
    if not ruta.is_file():
        return datos
    for raw in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        if k and k not in datos:
            datos[k] = v
    return datos


def duplicadas_intra(ruta: Path) -> list[str]:
    """Nombres de clave repetidos DENTRO del mismo archivo (solo nombres).

    ``parsear`` se queda con la PRIMERA ocurrencia intra-archivo y descarta el
    resto en silencio; eso NO es una colisión entre fuentes (esa cuenta la lleva
    ``migrar``), pero sí es un .env mal editado. Se advierte por nombre.
    """
    vistas: set[str] = set()
    repetidas: set[str] = set()
    if not ruta.is_file():
        return []
    for raw in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip().lstrip("\ufeff")  # BOM de un editor Windows: no es parte del nombre
        if not s or s.startswith("#") or "=" not in s:
            continue
        k = s.split("=", 1)[0].strip()
        if not k:
            continue
        if k in vistas:
            repetidas.add(k)
        vistas.add(k)
    return sorted(repetidas)


def migrar() -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Fusiona fuentes por orden (gana la primera). Devuelve (valores, origen_por_nombre, duplicadas)."""
    orden = [(f"{t}  (origen: {r.relative_to(BASE)})", r) for t, r in FUENTES]
    orden.append(("MAESTRO historico F25 (origen: claves/.env.master — prioridad minima)", FUENTE_MAESTRO))
    valores: dict[str, str] = {}
    origen: dict[str, str] = {}
    duplicadas: list[str] = []
    for etiqueta, ruta in orden:
        for k, v in parsear(ruta).items():
            if k in valores:
                duplicadas.append(k)
                continue
            valores[k] = v
            origen[k] = ruta.name
    return valores, origen, duplicadas


def construir_unico(valores: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """Texto del .env UNICO con las 11 secciones. Devuelve (texto, nombres_migrados, nombres_por_literal)."""
    esqueleto = {n for _, nombres in SECCIONES for n, _ in nombres}
    lineas = [
        "# =====================================================================",
        "# AulaSense — .env UNICO canonico (F26, SPEC 16.8 / D-F26-6). GITIGNORED.",
        "# Migrado por tools/consolidar_env.py --unificar: valores byte-a-byte desde",
        "#   .env raiz + claves/.env + claves/.env-wsl-nuevo (+ claves/.env.master",
        "#   historico). Fuentes archivadas en claves/*.bak-f26. NUNCA commitear,",
        "#   NUNCA pegar en chats/logs. Si se expone: rotar todo (reset_admin.py,",
        "#   gen_firmware_config, broker ACLs, pass distro).",
        "# Secretos: generados SOLO por codigo (regla 15); OPENROUTER/DASHSCOPE son",
        "#   del dueno: se referencian, NO se regeneran. La pass del VPS NO vive",
        "#   aqui: la gestiona la app local wsl-port (rotacion pendiente dueno).",
        "# =====================================================================",
    ]
    migrados: list[str] = []
    por_literal: list[str] = []
    for titulo, nombres in SECCIONES:
        lineas.append("")
        lineas.append(f"# ----- {titulo} -----")
        for nombre, literal in nombres:
            if nombre in valores:
                v = valores[nombre]
                migrados.append(nombre)
            elif literal is not None:
                v = literal
                por_literal.append(nombre)
            else:
                v = ""
            if nombre in DESC:
                lineas.append(f"# {DESC[nombre]}")
            lineas.append(f"{nombre}={v}")
    sobrantes = sorted(set(valores) - esqueleto)
    if sobrantes:
        lineas.append("")
        lineas.append("# ----- OTRAS — arrastradas de las fuentes sin seccion asignada (no perder valores) -----")
        for nombre in sobrantes:
            lineas.append(f"{nombre}={valores[nombre]}")
            migrados.append(nombre)
    return "\n".join(lineas) + "\n", migrados, por_literal


def respaldar() -> list[str]:
    """Copia cada fuente a claves/*.bak-f26 ANTES de reescribir. No pisa respaldos existentes."""
    hechas: list[str] = []
    mapa = [
        (BASE / ".env", BASE / "claves" / "env-raiz.bak-f26"),
        (BASE / "claves" / ".env", BASE / "claves" / ".env.bak-f26"),
        (BASE / "claves" / ".env-wsl-nuevo", BASE / "claves" / ".env-wsl-nuevo.bak-f26"),
    ]
    for origen, bak in mapa:
        if not origen.is_file():
            continue
        if bak.exists():
            hechas.append(f"{bak.name} (ya existia)")
            continue
        bak.write_bytes(origen.read_bytes())
        hechas.append(bak.name)
    return hechas


def archivar_fuentes() -> list[str]:
    """Tras verificar: las fuentes de claves/ dejan de ser canonicas — se RENOMBRAN a
    *.bak-f26 (si el respaldo con ese nombre aun no existe). El .env raiz ES el destino,
    no se archiva."""
    movidas: list[str] = []
    for origen, bak in [
        (BASE / "claves" / ".env", BASE / "claves" / ".env.bak-f26"),
        (BASE / "claves" / ".env-wsl-nuevo", BASE / "claves" / ".env-wsl-nuevo.bak-f26"),
    ]:
        if not origen.is_file():
            continue
        if bak.exists():
            origen.unlink()  # el respaldo ya conserva los bytes; la fuente deja de existir
        else:
            shutil.move(str(origen), str(bak))
        movidas.append(bak.name)
    return movidas


def verificar(destino: Path, valores: dict[str, str]) -> list[str]:
    """Diff por NOMBRE: fuentes que NO quedaron presentes en el destino (debe ser [])."""
    dst = set(parsear(destino)) if destino.is_file() else set()
    return sorted(set(valores) - dst)


def modo_unificar(dry: bool, archivar: bool) -> int:
    valores, origen, duplicadas = migrar()
    texto, migrados, por_literal = construir_unico(valores)
    print(f"[unificar] fuentes leidas: {len(FUENTES) + 1} | nombres unicos: {len(valores)} | "
          f"duplicadas (gano 1ª fuente): {len(duplicadas)}")
    print(f"[unificar] migradas byte-a-byte: {len(migrados)} | literales SPEC/dueno: {len(por_literal)}")
    for titulo, nombres in SECCIONES:
        print(f"  seccion {titulo.split('—')[0].strip():<12} variables: {len(nombres)}")
    print("[unificar] nombres (sin valores):")
    for k in sorted(valores):
        print(f"  - {k} (origen: {origen[k]})")
    if duplicadas:
        print("[unificar] duplicadas anotadas: " + ", ".join(sorted(set(duplicadas))))
    # GATE-F26-M2 (MENOR): duplicados DENTRO de un mismo archivo. No son colisiones
    # entre fuentes (cuenta aparte, intacta arriba); se avisa por NOMBRE.
    for ruta in [r for _, r in FUENTES] + [FUENTE_MAESTRO]:
        reps = duplicadas_intra(ruta)
        if reps:
            print(
                f"[unificar] WARN duplicadas intra-archivo en {ruta.name}: "
                f"{', '.join(reps)} — parsear conserva la PRIMERA; limpiar el archivo"
            )
    if dry:
        print(f"[unificar] DRY-RUN: no se escribio nada. Destino previsto: {DESTINO_UNICO}")
        return 0
    respaldos = respaldar()
    DESTINO_UNICO.write_text(texto, encoding="utf-8")
    faltan = verificar(DESTINO_UNICO, valores)
    print(f"[unificar] respaldos: {', '.join(respaldos) if respaldos else '(ninguno)'}")
    print(f"[unificar] escrito: {DESTINO_UNICO} ({len(texto.splitlines())} lineas)")
    print(f"[unificar] VERIFICACION por nombre — fuentes ausentes en destino: {len(faltan)}"
          + ("" if not faltan else " -> " + ", ".join(faltan)))
    if archivar:
        movidas = archivar_fuentes()
        print(f"[unificar] fuentes archivadas (dejan de ser canonicas): {', '.join(movidas)}")
    return 0 if not faltan else 1


# ---------------------------------------------------------------------------
# Modo legado (F25): escribe claves/.env.master. Se conserva tal cual para
# referencia historica (SPEC 16.8: "claves/.env.example queda como referencia").
# ---------------------------------------------------------------------------
def modo_master() -> int:
    lineas = ["# =====================================================================",
              "# AulaSense — MAESTRO de configuracion y claves (modo legado F25)",
              "# Referencia HISTORICA: el canónico es el .env de la raiz (F26, --unificar).",
              "# Generado por tools/consolidar_env.py. GITIGNORED via claves/*.",
              "# ====================================================================="]
    vistos: dict[str, str] = {}
    total = 0
    for titulo, ruta in FUENTES:
        lineas.append("")
        lineas.append(f"# ----- {titulo}  (origen: {ruta.name}) -----")
        if not ruta.is_file():
            lineas.append(f"# (ausente: {ruta})")
            continue
        for k, v in parsear(ruta).items():
            total += 1
            if k in vistos:
                lineas.append(f"# [DUPLICADA — se conserva la de {vistos[k]}] {k}")
                continue
            vistos[k] = ruta.name
            if k in DESC:
                lineas.append(f"# {DESC[k]}")
            lineas.append(f"{k}={v}")
    DESTINO_MASTER.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print(f"OK -> {DESTINO_MASTER}")
    print(f"lineas clave procesadas: {total} | unicas: {len(vistos)}")
    print("claves (sin valores):")
    for k in sorted(vistos):
        print("  -", k)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Consolida .env (legado -> claves/.env.master) o UNIFICA el .env raiz "
                    "canonico (F26, SPEC 16.8). Nunca imprime valores: solo nombres y conteos."
    )
    ap.add_argument("--unificar", action="store_true",
                    help="construye el .env UNICO de la raiz con las 11 secciones de SPEC 16.8")
    ap.add_argument("--dry-run", action="store_true", help="no escribe nada (solo reporte)")
    ap.add_argument("--archivar", action="store_true",
                    help="con --unificar: tras verificar, archiva claves/.env y .env-wsl-nuevo "
                         "a *.bak-f26 (dejan de ser fuentes canonicas)")
    args = ap.parse_args(argv)
    if args.unificar:
        return modo_unificar(dry=args.dry_run, archivar=args.archivar)
    if args.dry_run or args.archivar:
        ap.error("--dry-run/--archivar requieren --unificar")
    return modo_master()


if __name__ == "__main__":
    sys.exit(main())
