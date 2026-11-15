"""Worker de descripcion IA de imagenes de AulaSense (SPEC §7.3, D-F25-4/5/6).

Alcance (fase F25-d2)
---------------------
* Cola persistente en la tabla ``imagenes`` (columnas ``vision_*``, §8.2): toma
  las filas ``vision_estado IN ('pendiente','error')`` con ``imagen_purgada=0``,
  pide la descripcion al modelo de vision y guarda ``descripcion`` (1-2 frases
  en espanol) + ``etiquetas`` (coma-separadas) + ``vision_estado='hecho'`` +
  ``vision_modelo``/``vision_ts_ms``/``vision_tokens``.
* **Claim atomico por fila (BUG-PROD-VISION-LOCK, 2026-11-15):** la transicion
  ``pendiente|error -> procesando`` es UN ``UPDATE ... WHERE id=? AND
  vision_estado IN ('pendiente','error')`` y solo la pasada que ve
  ``rowcount == 1`` procesa la fila (las demas hacen ``continue`` sin llamar a
  la API). Dos pasadas concurrentes sobre la misma BD ya NO re-describen la
  misma imagen (regla 16: cero Credits duplicados). El instante del claim se
  sella en ``vision_ts_ms`` (columna F25 existente: NO se anaden columnas) y es
  el heartbeat que usa ``liberar_procesando`` para liberar solo claims STALE.
* Modelo: cadena con fallback. Default ``qwen3.8-flash`` (allowlist exact-string
  del Token Plan, visual understanding) -> ``qwen3.6-flash`` -> ``auto``.
  Con key pay-as-you-go ``sk-*`` existe ademas ``qwen3-vl-flash``/``qwen-vl-max``
  (alternativa documentada: se pasa con ``--modelo``).
* API: chat/completions *compatible-mode* con ``image_url`` data-URI
  (``data:image/jpeg;base64,...``), ``max_tokens=200`` (salida acotada).
  Base por tipo de key: Token Plan (``sk-sp-``) ->
  ``https://token-plan.maas.qwencloudapi.com/compatible-mode/v1``;
  pay-as-you-go (``sk-*``) -> ``https://maas.qwencloudapi.com/compatible-mode/v1``.

Resolucion de la key (SIEMPRE por codigo; NUNCA se imprime)
-----------------------------------------------------------
1) variable de entorno ``DASHSCOPE_API_KEY`` del proceso;
2) ``claves/.env`` (``AULASENSE_CLAVES_ENV`` la sobreescribe: tests);
3) ``.env`` de la raiz (``AULASENSE_ENV_RAIZ`` la sobreescribe);
4) ``auth.json`` de OpenCode (providers ``alibaba-token-plan`` /
   ``alibaba-token-plan-cn``; en esta maquina se verifico en
   ``%USERPROFILE%\\.local\\share\\opencode\\auth.json``; tambien se prueba
   ``%USERPROFILE%\\.config\\opencode\\auth.json``). ``AULASENSE_VISION_AUTH_JSON``
   fuerza una ruta (tests).
Sin key -> worker **idle honesto** (regla 13: el sistema funciona sin IA).
Los logs solo publican tipo/origen/longitud de la key, nunca su valor.

Modos (D-F25-5, cumplimiento ToS del Token Plan)
------------------------------------------------
* **Modo B (default, agente-mediado):** un agente interactivo lanza
  ``tools/vision_describe.py --once --limit N``. Con key ``sk-sp-`` es el unico
  modo permitido; tambien expone ``vision_pending`` (JSON con la cola para que
  el agente describa con su propia skill) y ``vision_apply`` (escribe los
  resultados que devolvio el agente).
* **Modo A (daemon ``--loop``):** SOLO con key pay-as-you-go ``sk-*``. Si la key
  resuelta empieza por ``sk-sp-`` se RECHAZA con ``ErrorToS`` (el CLI sale con
  codigo != 0): los ToS del Token Plan prohiben backends/daemons autonomos con
  esa key ("must not be used for automation scripts, custom application
  backends, or any non-interactive batch call scenarios").

Rate limits y presupuesto (D-F25-6)
-----------------------------------
* Topes persistentes en la tabla ``vision_uso`` con ventana movil:
  ``AULASENSE_VISION_MAX_DIA`` (default 120) y ``AULASENSE_VISION_MAX_HORA``
  (default 20). Cada pasada cuenta las filas ya consumidas (``hecho``/``error``)
  y, al alcanzar el tope, PARA con log honesto registrando ``resultado='tope'``
  (no quema Credits). Los contadores sobreviven reinicios (viven en la BD).
* Dedupe por ``hash_sha256``: una imagen nunca se describe dos veces (el unique
  index de §8.2 lo impone entre canales; aqui ademas se deduplica dentro de la
  pasada y contra filas ya ``hecho`` por robustez con DB heredadas).
* Validacion LOCAL del JPEG (Pillow, dependencia opcional): un frame corrupto
  del ESP32-S3 (el modelo responde ``HTTP 400 'image format is illegal'``) se
  detecta ANTES de la llamada; la fila queda ``error`` con **0 Credits** y no
  consume cupo de los topes.
* Reintentos: backoff exponencial maximo 2 (10 s / 30 s) POR MODELO; HTTP 429
  respeta ``Retry-After`` (acotado a 120 s). HTTP 5xx/red -> siguiente modelo de
  la cadena; 4xx no recuperable -> siguiente modelo. Si la cadena entera falla,
  la fila queda ``vision_estado='error'`` (reintetable en otra pasada, sin bucle).
* Presupuesto: se registran los ``usage`` REALES de la respuesta
  (``tokens_in``/``tokens_out``) por imagen en ``vision_uso`` -> auditable.
* Orden de la cola: ``AULASENSE_VISION_ORDEN`` (o ``--orden``) = ``asc`` (FIFO,
  default) | ``desc`` (ids MAS NUEVAS primero). Con cupo parcial, ``desc``
  prioriza la actividad reciente de la galeria; el resto queda ``pendiente``.

Interrumpibilidad
-----------------
* SIGTERM/SIGINT: el manejador solo marca una bandera; la imagen EN CURSO termina
  (su resultado se guarda) y la pasada sale limpia (exit 0). El estado vive en la
  BD -> reanudable.
* ``--stop`` escribe la bandera ``.tmp/vision_stop.flag``
  (``AULASENSE_VISION_STOP_FILE`` la sobreescribe); la pasada siguiente la
  consume y sale limpia sin procesar nada mas.

Escritura en BD (excepcion documentada al writer unico, patron openmeteo)
-------------------------------------------------------------------------
Solo UPDATE cortos y parametrizados que tocan ``vision_estado``/``descripcion``/
``etiquetas``/``vision_modelo``/``vision_ts_ms``/``vision_tokens`` (+ INSERT en
``vision_uso``), con ``PRAGMA busy_timeout=5000`` y ``journal_mode=WAL``. NUNCA
toca ``evaluaciones``/``eventos``/``imagenes.ruta`` ni hace DDL canonico: el
esquema F25 lo migra la ingesta (``server/ingesta/esquema.py``). Si la DB aun no
esta migrada se lanza ``ErrorEsquema`` con el mensaje
``DB sin migrar (corre la ingesta F25-d1)``. El claim escribe ``vision_ts_ms``
con el instante del reclamo (heartbeat/stale-claim): la pasada lo sobreescribe
con el ``ts`` del resultado al guardar ``hecho``/``error``.

Codigos de salida del CLI (``tools/vision_describe.py``)
--------------------------------------------------------
0 OK / idle / tope (parada honesta) | 2 uso/config, incluido el rechazo ToS |
3 DB inexistente o sin migrar.
"""

from __future__ import annotations

import base64
import json
import os
import re
import signal
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests
from dotenv import dotenv_values

# --- Rutas del proyecto -------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]  # aulasense/
DB_DEFECTO = ROOT / "server" / "datos" / "aulasense.db"
ENV_DEFECTO = ROOT / ".env"
CLAVES_ENV_DEFECTO = ROOT / "claves" / ".env"
BANDERA_STOP_DEFECTO = ROOT / ".tmp" / "vision_stop.flag"

# --- Contrato SPEC §7.3 -------------------------------------------------------
MODELO_DEFECTO = "qwen3.8-flash"  # allowlist exact-string TP (visual understanding)
FALLBACKS_DEFECTO: tuple[str, ...] = ("qwen3.6-flash", "auto")
MAX_DIA_DEFECTO = 120
MAX_HORA_DEFECTO = 20
LIMITE_DEFECTO = 10
# Orden de la cola: ``asc`` (FIFO, default historico) | ``desc`` (ids MAS NUEVAS
# primero: la actividad reciente es lo que se quiere ver descrito en la galeria
# cuando el cupo de la pasada no alcanza para toda la cola).
ORDEN_DEFECTO = "asc"
ORDENES_VALIDOS: tuple[str, ...] = ("asc", "desc")
MAX_TOKENS_SALIDA = 200  # salida acotada (SPEC §7.3): ~150 tokens reales
TEMPERATURA = 0.2
TIMEOUT_S = 60.0
INTERVALO_LOOP_S = 300.0  # modo A: una pasada cada 5 min

# Claim atomico por fila (BUG-PROD-VISION-LOCK): una fila ``procesando`` solo se
# considera HUERFANA (y vuelve a ``pendiente``) si su claim es mas viejo que N
# minutos. El claim sella ``vision_ts_ms`` con el instante del reclamo (columna
# F25 EXISTENTE; NO se anaden columnas) hasta que la pasada lo sobreescribe con
# el ts del resultado. 30 min cubre con holgura el peor caso de UNA llamada
# (3 modelos x 3 intentos con backoff 10/30 s + timeout 60 s ~ 11 min).
PROCESANDO_STALE_MIN_DEFECTO = 30
MS_POR_MINUTO = 60_000

# Backoff exponencial maximo 2 reintentos por modelo (SPEC §7.3).
ESPERAS_BACKOFF_S: tuple[float, ...] = (10.0, 30.0)
MAX_ESPERA_429_S = 120.0  # Retry-After mayor que esto se sustituye por el backoff

VENTANA_DIA_MS = 24 * 60 * 60 * 1000
VENTANA_HORA_MS = 60 * 60 * 1000
MAX_DESC_CHARS = 600  # descripcion 1-2 frases; recorte defensivo
MAX_ETIQUETAS_CHARS = 300

BASE_TOKEN_PLAN = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1"
BASE_PAYG = "https://maas.qwencloudapi.com/compatible-mode/v1"
ENDPOINT_CHAT = "/chat/completions"

# Proveedores de OpenCode que hospedan la key del Token Plan (patron skills qwen-*).
PROVEEDORES_TP: tuple[str, ...] = ("alibaba-token-plan", "alibaba-token-plan-cn")

# Prompt acotado (español, 1-2 frases + etiquetas coma-separadas).
PROMPT_VISION = (
    "Describe la imagen en 1-2 frases en español. Es la evidencia de un evento de "
    "movimiento en un aula (puede haber personas; no identifiques a nadie). "
    "Después añade una línea final con el formato 'ETIQUETAS: etiqueta1, etiqueta2, ...' "
    "con 3-8 etiquetas cortas en español separadas por coma."
)

# Columnas F25 que este worker necesita (replica de SPEC §8.2; el DDL canonico
# vive en server/ingesta/esquema.py y SOLO la ingesta lo aplica).
COLUMNAS_F25_REQUERIDAS: tuple[str, ...] = (
    "canal",
    "descripcion",
    "etiquetas",
    "vision_estado",
    "vision_modelo",
    "vision_ts_ms",
    "vision_tokens",
)
COLUMNAS_VISION_USO: tuple[str, ...] = (
    "ts_ms",
    "imagen_id",
    "modelo",
    "tokens_in",
    "tokens_out",
    "modo",
    "resultado",
)
MENSAJE_SIN_MIGRAR = "DB sin migrar (corre la ingesta F25-d1)"


# ------------------------------------------------------------------------------
# Excepciones de configuracion (el CLI las traduce a codigos de salida)
# ------------------------------------------------------------------------------
class ErrorVision(RuntimeError):
    """Base de los errores de configuracion del worker (exit code 2)."""


class ErrorConfiguracion(ErrorVision):
    """Uso/configuracion invalida (flag, JSON de apply, etc.)."""


class ErrorToS(ErrorVision):
    """Uso de una key ``sk-sp-`` en modo A (prohibido por los ToS del Token Plan)."""


class ErrorEsquema(ErrorVision):
    """La DB no existe o no tiene el esquema F25 (exit code 3)."""


# ------------------------------------------------------------------------------
# Tipos de datos
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Clave:
    """Key resuelta. ``valor`` NUNCA se imprime ni se serializa."""

    valor: str = ""
    tipo: str = "vacia"  # "token_plan" | "pay_as_you_go" | "vacia"
    origen: str = "ausente"

    @property
    def base_url(self) -> str:
        """Base compatible-mode segun el tipo de key (SPEC D-F25-4)."""
        return BASE_TOKEN_PLAN if self.tipo == "token_plan" else BASE_PAYG

    @property
    def descripcion_segura(self) -> str:
        """Resumen sin secreto: tipo + origen + longitud (para logs/evidencia)."""
        return f"tipo={self.tipo} origen={self.origen} longitud={len(self.valor)}"


@dataclass(frozen=True)
class ConfigVision:
    """Configuracion resuelta (flags del CLI > entorno > .env > defaults)."""

    db_path: Path = DB_DEFECTO
    datos_dir: Path = DB_DEFECTO.parent
    env_path: Path = ENV_DEFECTO
    claves_path: Path = CLAVES_ENV_DEFECTO
    modelo: str = MODELO_DEFECTO
    fallbacks: tuple[str, ...] = FALLBACKS_DEFECTO
    max_dia: int = MAX_DIA_DEFECTO
    max_hora: int = MAX_HORA_DEFECTO
    modo: str = "B"
    limite: int = LIMITE_DEFECTO
    orden: str = ORDEN_DEFECTO  # "asc" (FIFO) | "desc" (ids mas nuevas primero)
    stale_min: int = PROCESANDO_STALE_MIN_DEFECTO  # claim viejo = huerfano
    bandera_stop: Path = BANDERA_STOP_DEFECTO
    timeout: float = TIMEOUT_S
    prompt: str = PROMPT_VISION

    @property
    def cadena(self) -> tuple[str, ...]:
        """Cadena de modelos sin duplicados: default -> fallbacks."""
        vistos: list[str] = []
        for modelo in (self.modelo, *self.fallbacks):
            limpio = str(modelo or "").strip()
            if limpio and limpio not in vistos:
                vistos.append(limpio)
        return tuple(vistos)


@dataclass
class RespuestaVision:
    """Resultado de una llamada al modelo (exito o error)."""

    ok: bool
    modelo: str | None = None
    descripcion: str | None = None
    etiquetas: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None

    @property
    def tokens_total(self) -> int | None:
        """Tokens totales (usage real); ``None`` si la respuesta no traia usage."""
        if self.tokens_in is None and self.tokens_out is None:
            return None
        return int(self.tokens_in or 0) + int(self.tokens_out or 0)


@dataclass
class ResumenPasada:
    """Contadores de una pasada (para logs/CLI)."""

    procesadas: int = 0
    hechas: int = 0
    errores: int = 0
    omitidas: int = 0
    ilegibles: int = 0  # JPEG que no decodifica: 'error' sin llamar a la API
    tope: str | None = None  # "dia" | "hora"
    parada: str | None = None  # "senal" | "bandera"
    idle: bool = False


@dataclass
class ResumenAplicacion:
    """Contadores de ``vision_apply`` (modo B agente-mediado)."""

    aplicadas: int = 0
    invalidas: int = 0
    tope: str | None = None


# ------------------------------------------------------------------------------
# Log (stderr: deja stdout limpio para el JSON de ``vision_pending``)
# ------------------------------------------------------------------------------
def log(nivel: str, mensaje: str) -> None:
    """Log con marca de tiempo a stderr (capturable por los tests)."""
    print(
        f"[vision {datetime.now():%Y-%m-%d %H:%M:%S}] {nivel}: {mensaje}",
        file=sys.stderr,
        flush=True,
    )


def ahora_ms() -> int:
    """Epoca del PC en milisegundos (misma base que ``imagenes.ts_ms``)."""
    return int(time.time() * 1000)


# ------------------------------------------------------------------------------
# Loader de entorno (mismo patron que server/dashboard/auth.py, F25-e)
# ------------------------------------------------------------------------------
def ruta_claves_env() -> Path:
    """``claves/.env``; ``AULASENSE_CLAVES_ENV`` manda (tests)."""
    crudo = os.environ.get("AULASENSE_CLAVES_ENV", "").strip()
    return Path(crudo).expanduser() if crudo else CLAVES_ENV_DEFECTO


def ruta_env_raiz() -> Path:
    """``.env`` de la raiz; ``AULASENSE_ENV_RAIZ`` manda (tests)."""
    crudo = os.environ.get("AULASENSE_ENV_RAIZ", "").strip()
    return Path(crudo).expanduser() if crudo else ENV_DEFECTO


def _leer_archivo(ruta: Path) -> dict[str, str]:
    """Valores de un ``.env`` (vacio si no existe o no se puede leer)."""
    try:
        if not ruta.is_file():
            return {}
        return {k: v for k, v in dotenv_values(ruta).items() if v is not None}
    except OSError:
        return {}


def valor_env(nombre: str, defecto: str = "") -> str:
    """Prioridad: entorno del proceso -> ``claves/.env`` -> ``.env`` de la raiz."""
    del_proceso = os.environ.get(nombre)
    if del_proceso not in (None, ""):
        return str(del_proceso)
    for ruta in (ruta_claves_env(), ruta_env_raiz()):
        valor = _leer_archivo(ruta).get(nombre)
        if valor not in (None, ""):
            return str(valor)
    return defecto


def _entero_env(nombre: str, defecto: int, minimo: int = 0) -> int:
    """Entero de configuracion con WARN si el valor es invalido (nunca crashea)."""
    crudo = valor_env(nombre, "").strip()
    if not crudo:
        return defecto
    try:
        return max(minimo, int(crudo))
    except ValueError:
        log("WARN", f"{nombre}={crudo!r} no es entero; se usa {defecto}")
        return defecto


def candidatos_auth_json() -> list[Path]:
    """Rutas candidatas del ``auth.json`` de OpenCode (verificadas en disco)."""
    crudo = os.environ.get("AULASENSE_VISION_AUTH_JSON", "").strip()
    if crudo:
        return [Path(crudo).expanduser()]
    casa = Path.home()
    return [
        casa / ".local" / "share" / "opencode" / "auth.json",
        casa / ".config" / "opencode" / "auth.json",
    ]


def clasificar_clave(valor: str) -> str:
    """``token_plan`` (sk-sp-) | ``pay_as_you_go`` (sk-*) | ``vacia``."""
    limpio = (valor or "").strip()
    if not limpio:
        return "vacia"
    if limpio.startswith("sk-sp-"):
        return "token_plan"
    # Cualquier otra key no vacia se trata como pay-as-you-go (401 honesto si no
    # vale para esa base); solo sk-sp- tiene contrato ToS restringido.
    return "pay_as_you_go"


def resolver_clave(
    *, env_path: Path | None = None, claves_path: Path | None = None
) -> Clave:
    """Resuelve la key SIN imprimirla: env -> claves/.env -> .env -> auth.json."""
    rutas_env = (
        Path(env_path) if env_path else ruta_env_raiz(),
        Path(claves_path) if claves_path else ruta_claves_env(),
    )

    del_proceso = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if del_proceso:
        return Clave(del_proceso, clasificar_clave(del_proceso), "env")

    for ruta, etiqueta in ((rutas_env[1], "claves/.env"), (rutas_env[0], ".env")):
        valor = str(_leer_archivo(ruta).get("DASHSCOPE_API_KEY") or "").strip()
        if valor:
            return Clave(valor, clasificar_clave(valor), etiqueta)

    for ruta in candidatos_auth_json():
        try:
            if not ruta.is_file():
                continue
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log("WARN", f"auth.json ilegible en {ruta} (se ignora)")
            continue
        if not isinstance(datos, dict):
            continue
        for proveedor in PROVEEDORES_TP:
            entrada = datos.get(proveedor)
            if not isinstance(entrada, dict):
                continue
            valor = str(entrada.get("key") or "").strip()
            if valor:
                return Clave(
                    valor, clasificar_clave(valor), f"auth.json:{proveedor}"
                )

    return Clave("", "vacia", "ausente")


def construir_config(
    *,
    db: str | Path | None = None,
    datos: str | Path | None = None,
    env: str | Path | None = None,
    claves: str | Path | None = None,
    modelo: str | None = None,
    max_dia: int | None = None,
    max_hora: int | None = None,
    modo: str | None = None,
    limite: int | None = None,
    orden: str | None = None,
) -> ConfigVision:
    """Resuelve la configuracion: flags > entorno > ``.env`` > defaults.

    ``AULASENSE_DB`` y ``AULASENSE_DATOS_DIR`` son la convencion compartida del
    proyecto (patron openmeteo/purga). ``datos_dir`` es el directorio base bajo
    el que se resuelven las rutas relativas de ``imagenes.ruta``.
    """
    db_path = Path(db) if db else Path(valor_env("AULASENSE_DB", "") or DB_DEFECTO)
    datos_dir = (
        Path(datos)
        if datos
        else Path(valor_env("AULASENSE_DATOS_DIR", "") or db_path.parent)
    )

    modo_final = (modo or valor_env("AULASENSE_VISION_MODO", "B")).strip().upper()
    if modo_final not in ("A", "B"):
        log("WARN", f"AULASENSE_VISION_MODO={modo_final!r} invalido; se usa B")
        modo_final = "B"

    modelo_final = (modelo or valor_env("AULASENSE_VISION_MODELO", MODELO_DEFECTO)).strip()
    if not modelo_final:
        modelo_final = MODELO_DEFECTO

    max_dia_final = (
        max_dia
        if max_dia is not None
        else _entero_env("AULASENSE_VISION_MAX_DIA", MAX_DIA_DEFECTO)
    )
    max_hora_final = (
        max_hora
        if max_hora is not None
        else _entero_env("AULASENSE_VISION_MAX_HORA", MAX_HORA_DEFECTO)
    )

    bandera = Path(
        os.environ.get("AULASENSE_VISION_STOP_FILE", "").strip()
        or BANDERA_STOP_DEFECTO
    ).expanduser()

    orden_final = (orden or valor_env("AULASENSE_VISION_ORDEN", ORDEN_DEFECTO)).strip().lower()
    if orden_final not in ORDENES_VALIDOS:
        log("WARN", f"AULASENSE_VISION_ORDEN={orden_final!r} invalido; se usa {ORDEN_DEFECTO}")
        orden_final = ORDEN_DEFECTO

    # Claim stale: minutos tras los que una fila 'procesando' se considera
    # huerfana (BUG-PROD-VISION-LOCK). Sin columna nueva: usa vision_ts_ms.
    stale_min_final = _entero_env(
        "AULASENSE_VISION_STALE_MIN", PROCESANDO_STALE_MIN_DEFECTO, minimo=1
    )

    return ConfigVision(
        db_path=db_path.expanduser(),
        datos_dir=datos_dir.expanduser(),
        env_path=Path(env).expanduser() if env else ruta_env_raiz(),
        claves_path=Path(claves).expanduser() if claves else ruta_claves_env(),
        modelo=modelo_final,
        max_dia=max(0, int(max_dia_final)),
        max_hora=max(0, int(max_hora_final)),
        modo=modo_final,
        limite=max(1, int(limite if limite is not None else LIMITE_DEFECTO)),
        orden=orden_final,
        stale_min=max(1, int(stale_min_final)),
        bandera_stop=bandera,
    )


def validar_clave_para_modo(cfg: ConfigVision, clave: Clave) -> None:
    """Guarda ToS (D-F25-5): ``sk-sp-`` jamas en modo A (daemon/autonomo)."""
    if clave.tipo == "token_plan" and cfg.modo == "A":
        raise ErrorToS(
            "RECHAZADO por ToS del Token Plan: una key sk-sp- SOLO vale para uso "
            "interactivo en herramientas de agente (modo B agente-mediado). "
            "Los ToS prohiben 'automation scripts, custom application backends, or "
            "any non-interactive batch call scenarios' -> un daemon/backoff "
            "autonomo con sk-sp- arriesga la suspension de la key. Usa el modo B "
            "(--once/vision_pending/vision_apply lanzado por un agente) o una key "
            "pay-as-you-go sk-* para el modo A."
        )


# ------------------------------------------------------------------------------
# SQLite: apertura corta, WAL + busy_timeout y comprobacion de esquema
# ------------------------------------------------------------------------------
def columnas_tabla(con: sqlite3.Connection, tabla: str) -> set[str] | None:
    """Columnas de ``tabla`` (``None`` si no existe)."""
    filas = con.execute(f"PRAGMA table_info({tabla})").fetchall()
    if not filas:
        return None
    return {str(fila[1]) for fila in filas}


def verificar_esquema(con: sqlite3.Connection) -> None:
    """Exige el esquema F25 (sin DDL propio: lo migra la ingesta, F25-d1)."""
    columnas = columnas_tabla(con, "imagenes")
    if columnas is None:
        raise ErrorEsquema(f"tabla 'imagenes' inexistente -> {MENSAJE_SIN_MIGRAR}")
    faltan = [c for c in COLUMNAS_F25_REQUERIDAS if c not in columnas]
    if faltan:
        raise ErrorEsquema(
            f"faltan columnas F25 en 'imagenes' ({', '.join(faltan)}) -> "
            f"{MENSAJE_SIN_MIGRAR}"
        )
    columnas_uso = columnas_tabla(con, "vision_uso")
    if columnas_uso is None:
        raise ErrorEsquema(f"tabla 'vision_uso' inexistente -> {MENSAJE_SIN_MIGRAR}")
    faltan_uso = [c for c in COLUMNAS_VISION_USO if c not in columnas_uso]
    if faltan_uso:
        raise ErrorEsquema(
            f"faltan columnas en 'vision_uso' ({', '.join(faltan_uso)}) -> "
            f"{MENSAJE_SIN_MIGRAR}"
        )


def abrir_db_verificada(cfg: ConfigVision) -> sqlite3.Connection:
    """Abre la DB (WAL + busy_timeout=5000) y valida el esquema F25.

    Excepcion documentada al writer unico: solo UPDATE cortos de columnas
    ``vision_*``/``descripcion``/``etiquetas`` e INSERT en ``vision_uso``.
    """
    if not cfg.db_path.exists():
        raise ErrorEsquema(f"DB inexistente: {cfg.db_path} -> {MENSAJE_SIN_MIGRAR}")
    con = sqlite3.connect(str(cfg.db_path), timeout=5.0)
    con.execute("PRAGMA busy_timeout=5000")
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error as exc:  # pragma: no cover - DB en solo-lectura
        log("WARN", f"no se pudo activar WAL ({exc}); se continua")
    con.row_factory = sqlite3.Row
    try:
        verificar_esquema(con)
    except ErrorEsquema:
        con.close()
        raise
    return con


# ------------------------------------------------------------------------------
# Rate limits persistentes (tabla vision_uso, ventana movil dia/hora)
# ------------------------------------------------------------------------------
def contar_uso(con: sqlite3.Connection, ahora: int) -> tuple[int, int]:
    """(usadas_dia, usadas_hora) de llamadas que consumieron Credits.

    Cuenta ``resultado IN ('hecho','error')``: un error de API tambien puede
    consumir tokens, asi que se cuenta de forma conservadora (presupuesto
    honesto). Las filas ``'tope'`` NO cuentan (no hubo llamada).
    """
    dia = con.execute(
        "SELECT COUNT(*) FROM vision_uso WHERE ts_ms >= ? AND resultado IN ('hecho','error')",
        (ahora - VENTANA_DIA_MS,),
    ).fetchone()[0]
    hora = con.execute(
        "SELECT COUNT(*) FROM vision_uso WHERE ts_ms >= ? AND resultado IN ('hecho','error')",
        (ahora - VENTANA_HORA_MS,),
    ).fetchone()[0]
    return int(dia), int(hora)


def tope_actual(
    con: sqlite3.Connection, cfg: ConfigVision, ahora: int | None = None
) -> str | None:
    """``'dia'``/``'hora'`` si se alcanzo el tope; ``None`` si hay margen."""
    dia, hora = contar_uso(con, ahora if ahora is not None else ahora_ms())
    if dia >= cfg.max_dia:
        return "dia"
    if hora >= cfg.max_hora:
        return "hora"
    return None


def registrar_uso(
    con: sqlite3.Connection,
    *,
    imagen_id: int | None,
    modelo: str,
    tokens_in: int | None,
    tokens_out: int | None,
    resultado: str,
    modo: str,
) -> None:
    """INSERT en ``vision_uso`` (presupuesto/auditoria persistente)."""
    con.execute(
        "INSERT INTO vision_uso (ts_ms, imagen_id, modelo, tokens_in, tokens_out, modo, resultado) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ahora_ms(), imagen_id, modelo, tokens_in, tokens_out, modo, resultado),
    )
    con.commit()


# ------------------------------------------------------------------------------
# Cola y dedupe
# ------------------------------------------------------------------------------
def ruta_imagen(datos_dir: Path, ruta_relativa: str) -> Path | None:
    """Resuelve ``imagenes.ruta`` bajo ``datos_dir``; ``None`` si escapa (traversal)."""
    try:
        candidata = (datos_dir / str(ruta_relativa or "")).resolve()
        base = datos_dir.resolve()
    except (OSError, RuntimeError):
        return None
    if candidata == base or base in candidata.parents:
        return candidata
    return None


def seleccionar_pendientes(
    con: sqlite3.Connection, limite: int, orden: str = ORDEN_DEFECTO
) -> list[dict[str, Any]]:
    """CANDIDATAS a procesar: ``pendiente``/``error`` (retry) y no purgadas.

    ``orden`` = ``asc`` (FIFO, default) | ``desc`` (ids mas nuevas primero; la
    actividad reciente manda cuando el cupo de la pasada es menor que la cola).

    Este SELECT **solo propone** filas: no las reclama ni reserva (es lo que usa
    ``vision_pending`` para listar la cola). El reclamo real es
    ``reclamar_imagen`` por fila, atomico (BUG-PROD-VISION-LOCK).
    """
    sentido = "DESC" if str(orden).strip().lower() == "desc" else "ASC"
    filas = con.execute(
        "SELECT id, ruta, hash_sha256 FROM imagenes "
        "WHERE vision_estado IN ('pendiente','error') AND imagen_purgada = 0 "
        f"ORDER BY id {sentido} LIMIT ?",
        (int(limite),),
    ).fetchall()
    return [
        {"id": int(f["id"]), "ruta": str(f["ruta"]), "hash_sha256": str(f["hash_sha256"])}
        for f in filas
    ]


def reclamar_imagen(
    con: sqlite3.Connection, imagen_id: int, *, ahora: int | None = None
) -> bool:
    """CLAIM atomico de UNA fila: ``pendiente|error -> procesando`` si aun es suya.

    BUG-PROD-VISION-LOCK: la transicion de estado ES el reclamo. ``UPDATE ...
    WHERE id = ? AND vision_estado IN ('pendiente','error')`` deja que SQLite
    serialice a los competidores: exactamente una pasada ve ``rowcount == 1``;
    las demas reciben 0 y deben hacer ``continue`` SIN llamar a la API (regla 16:
    cero Credits duplicados). No sirve ``executemany``: aqui importa el
    ``rowcount`` POR FILA.

    El instante del claim se sella en ``vision_ts_ms`` (columna F25 existente;
    NO se anaden columnas): es el heartbeat que ``liberar_procesando`` compara
    para no reabrir una fila EN VUELO de otra pasada. La pasada lo sobreescribe
    con el ts del resultado al guardar ``hecho``/``error``.
    """
    sello = int(ahora if ahora is not None else ahora_ms())
    cursor = con.execute(
        "UPDATE imagenes SET vision_estado = 'procesando', vision_ts_ms = ? "
        "WHERE id = ? AND vision_estado IN ('pendiente','error')",
        (sello, int(imagen_id)),
    )
    con.commit()
    return int(cursor.rowcount or 0) > 0


def hash_ya_descripto(
    con: sqlite3.Connection, hash_sha256: str, imagen_id: int
) -> bool:
    """True si OTRA fila con el mismo hash ya tiene descripcion ``hecho``."""
    fila = con.execute(
        "SELECT 1 FROM imagenes WHERE hash_sha256 = ? AND id <> ? "
        "AND vision_estado = 'hecho' LIMIT 1",
        (hash_sha256, int(imagen_id)),
    ).fetchone()
    return fila is not None


def liberar_procesando(
    con: sqlite3.Connection,
    *,
    stale_ms: int = PROCESANDO_STALE_MIN_DEFECTO * MS_POR_MINUTO,
    ahora: int | None = None,
) -> int:
    """Devuelve a ``pendiente`` SOLO las filas ``procesando`` HUERFANAS (stale).

    BUG-PROD-VISION-LOCK: la version anterior reabria TODAS las filas
    ``procesando`` al arrancar cada pasada, incluida la que otra pasada tenia EN
    VUELO -> la volvia a reclamar y la re-describia (doble gasto de Credits,
    regla 16). Ahora el claim se sella en ``vision_ts_ms`` (``reclamar_imagen``)
    y aqui solo se liberan los claims mas viejos que ``stale_ms`` (heartbeat:
    ``AULASENSE_VISION_STALE_MIN``, default 30 min). Las filas heredadas con
    ``vision_ts_ms`` NULL tambien se liberan: claim desconocido = huerfana.

    Solo toca ``vision_estado`` (excepcion documentada al writer unico).
    """
    limite = int(ahora if ahora is not None else ahora_ms()) - max(0, int(stale_ms))
    cursor = con.execute(
        "UPDATE imagenes SET vision_estado = 'pendiente' "
        "WHERE vision_estado = 'procesando' "
        "AND (vision_ts_ms IS NULL OR vision_ts_ms <= ?)",
        (limite,),
    )
    con.commit()
    return int(cursor.rowcount or 0)


# ------------------------------------------------------------------------------
# UPDATE cortos (SOLO columnas vision_*/descripcion/etiquetas)
# ------------------------------------------------------------------------------
def _marcar_estado(con: sqlite3.Connection, imagen_id: int, estado: str) -> None:
    """Cambia solo ``vision_estado`` (``omitido`` en el dedupe por hash).

    La transicion a ``procesando`` ya NO pasa por aqui: es el claim atomico
    ``reclamar_imagen`` (BUG-PROD-VISION-LOCK).
    """
    con.execute(
        "UPDATE imagenes SET vision_estado = ? WHERE id = ?", (estado, int(imagen_id))
    )
    con.commit()


def _guardar_descripcion(
    con: sqlite3.Connection,
    imagen_id: int,
    *,
    descripcion: str,
    etiquetas: str,
    modelo: str,
    tokens: int | None,
    ts_ms: int,
) -> None:
    """UPDATE corto con el resultado de la IA (6 columnas F25)."""
    con.execute(
        "UPDATE imagenes SET descripcion = ?, etiquetas = ?, vision_estado = 'hecho', "
        "vision_modelo = ?, vision_ts_ms = ?, vision_tokens = ? WHERE id = ?",
        (descripcion, etiquetas, modelo, int(ts_ms), tokens, int(imagen_id)),
    )
    con.commit()


def _guardar_error(
    con: sqlite3.Connection, imagen_id: int, *, modelo: str, ts_ms: int
) -> None:
    """UPDATE corto de error (reintetable en otra pasada; sin bucle)."""
    con.execute(
        "UPDATE imagenes SET vision_estado = 'error', vision_modelo = ?, "
        "vision_ts_ms = ?, vision_tokens = NULL WHERE id = ?",
        (modelo, int(ts_ms), int(imagen_id)),
    )
    con.commit()


# ------------------------------------------------------------------------------
# Llamada al modelo de vision
# ------------------------------------------------------------------------------
def _dormir(segundos: float) -> None:
    """Espera (seam de los tests: monkeypatch de ``cliente._dormir``)."""
    time.sleep(max(0.0, float(segundos)))


def data_uri_jpeg(archivo: Path) -> str:
    """data-URI base64 del JPEG (QVGA ~10 KB viaja inline, SPEC D-F25-4)."""
    return data_uri_jpeg_bytes(archivo.read_bytes())


def data_uri_jpeg_bytes(datos: bytes) -> str:
    """data-URI base64 de unos bytes JPEG ya leidos del disco."""
    return "data:image/jpeg;base64," + base64.b64encode(datos).decode("ascii")


def jpeg_utilizable(datos: bytes) -> tuple[bool, str]:
    """Valida LOCALMENTE el JPEG antes de gastar una llamada (presupuesto D-F25-6).

    El ESP32-S3 puede dejar frames a medio escribir (JPEG truncado/corrupto): el
    modelo responde entonces ``HTTP 400 invalid_parameter_error / 'The image
    format is illegal and cannot be opened'``. Detectarlo aqui cuesta 0 Credits y
    evita consumir cupo del tope horario/diario.

    * Sin Pillow instalado -> no se puede verificar: se delega al modelo (True).
    * Dos etapas: decodificacion ESTRICTA y, si falla, segundo intento con
      ``LOAD_TRUNCATED_IMAGES`` (escaneo truncado pero con cabecera valida: el
      modelo y otros decodificadores lo aceptan). Solo se descarta lo que no
      abre en ninguna de las dos.
    * Devuelve ``(True, "")`` o ``(False, motivo)``.
    """
    if not datos:
        return False, "archivo vacio"
    if not datos.startswith(b"\xff\xd8"):
        return False, "no empieza con SOI JPEG (ffd8)"
    if not datos.rstrip(b"\x00").endswith(b"\xff\xd9"):
        return False, "sin EOI JPEG (ffd9): frame truncado"
    try:
        import io

        from PIL import Image, ImageFile  # dependencia opcional (matplotlib la trae)
    except ImportError:  # pragma: no cover - entorno sin Pillow
        return True, ""
    try:
        with Image.open(io.BytesIO(datos)) as imagen:
            imagen.load()
    except Exception:  # noqa: BLE001 - PIL lanza tipos variados
        # Segundo intento TOLERANTE: el ESP32 puede dejar el escaneo entropico
        # truncado conservando cabecera y EOI; muchos decodificadores (y la API)
        # lo aceptan. Solo se descarta si tampoco asi abre.
        anterior = ImageFile.LOAD_TRUNCATED_IMAGES
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        try:
            with Image.open(io.BytesIO(datos)) as imagen:
                imagen.load()
                if imagen.size[0] > 0 and imagen.size[1] > 0:
                    return True, ""
        except Exception:  # noqa: BLE001
            return False, "JPEG corrupto (no decodifica ni en modo tolerante)"
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = anterior
        return False, "JPEG corrupto (no decodifica ni en modo tolerante)"
    return True, ""


def construir_payload(modelo: str, data_uri: str, prompt: str = PROMPT_VISION) -> dict:
    """Body chat/completions compatible-mode con la imagen inline."""
    return {
        "model": modelo,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_uri}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": MAX_TOKENS_SALIDA,
        "temperature": TEMPERATURA,
    }


def _texto_respuesta(cuerpo: dict) -> str:
    """Texto del primer ``choices[].message.content`` (str o lista de partes)."""
    choices = cuerpo.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    contenido = (choices[0].get("message") or {}).get("content")
    if isinstance(contenido, str):
        return contenido.strip()
    if isinstance(contenido, list):
        partes = [
            str(parte.get("text") or "")
            for parte in contenido
            if isinstance(parte, dict)
        ]
        return "".join(partes).strip()
    return ""


def _usage(cuerpo: dict) -> tuple[int | None, int | None]:
    """(tokens_in, tokens_out) REALES de ``usage``; ``None`` si no vienen."""
    usage = cuerpo.get("usage")
    if not isinstance(usage, dict):
        return None, None
    entrada = usage.get("prompt_tokens", usage.get("input_tokens"))
    salida = usage.get("completion_tokens", usage.get("output_tokens"))
    try:
        tokens_in = int(entrada) if entrada is not None else None
    except (TypeError, ValueError):
        tokens_in = None
    try:
        tokens_out = int(salida) if salida is not None else None
    except (TypeError, ValueError):
        tokens_out = None
    return tokens_in, tokens_out


_RE_ETIQUETAS = re.compile(r"^\s*etiquetas?\s*[:\-]\s*(.+)$", re.IGNORECASE)


def _limpiar_etiquetas(crudo: str) -> str:
    """Normaliza a ``a, b, c`` (sin duplicados case-insensitive, recortado)."""
    vistas: list[str] = []
    for parte in re.split(r"[,;]", crudo or ""):
        etiqueta = re.sub(r"\s+", " ", parte).strip(" .")
        if not etiqueta:
            continue
        if etiqueta.casefold() not in {v.casefold() for v in vistas}:
            vistas.append(etiqueta)
    return ", ".join(vistas)[:MAX_ETIQUETAS_CHARS]


def separar_respuesta(texto: str) -> tuple[str, str]:
    """Separa descripcion y etiquetas de la salida del modelo.

    Espera una linea ``ETIQUETAS: ...`` (tolerante a mayusculas y a ``-``); todo
    lo demas es la descripcion (1-2 frases). Sin linea de etiquetas -> ``""``.
    """
    lineas = [ln.strip() for ln in (texto or "").splitlines() if ln.strip()]
    descripcion: list[str] = []
    etiquetas = ""
    for linea in lineas:
        coincidencia = _RE_ETIQUETAS.match(linea)
        if coincidencia and not etiquetas:
            etiquetas = _limpiar_etiquetas(coincidencia.group(1))
        else:
            descripcion.append(linea)
    texto_desc = re.sub(r"\s+", " ", " ".join(descripcion)).strip()
    if not texto_desc:
        texto_desc = re.sub(r"\s+", " ", texto or "").strip()
    return texto_desc[:MAX_DESC_CHARS], etiquetas


def _espera_retry_after(respuesta: Any, defecto: float) -> float:
    """Segundos de ``Retry-After`` (0 < x <= 120); si no vale, ``defecto``."""
    crudo = (getattr(respuesta, "headers", None) or {}).get("Retry-After")
    try:
        valor = float(str(crudo).strip())
    except (TypeError, ValueError):
        return defecto
    return valor if 0 < valor <= MAX_ESPERA_429_S else defecto


def llamar_vision(
    cfg: ConfigVision,
    clave: Clave,
    data_uri: str,
    *,
    dormir: Callable[[float], None] | None = None,
) -> RespuestaVision:
    """Llama a la cadena de modelos con backoff (max 2) y fallback.

    * 200 -> parsea descripcion/etiquetas + usage real.
    * 429 -> espera ``Retry-After`` (o el backoff 10/30 s) y reintenta.
    * 5xx / error de red -> backoff y reintento; agotados -> SIGUIENTE modelo.
    * 4xx no recuperable -> sin reintento, SIGUIENTE modelo.
    """
    esperar = dormir or _dormir
    url = clave.base_url.rstrip("/") + ENDPOINT_CHAT
    cabeceras = {
        "Authorization": f"Bearer {clave.valor}",
        "Content-Type": "application/json",
    }
    ultimo_error = "sin intentos"

    for modelo in cfg.cadena:
        payload = construir_payload(modelo, data_uri, cfg.prompt)
        for intento in range(len(ESPERAS_BACKOFF_S) + 1):
            espera_fija = ESPERAS_BACKOFF_S[min(intento, len(ESPERAS_BACKOFF_S) - 1)]
            try:
                respuesta = requests.post(
                    url, headers=cabeceras, json=payload, timeout=cfg.timeout
                )
            except requests.RequestException as exc:
                ultimo_error = f"red ({exc.__class__.__name__})"
                if intento < len(ESPERAS_BACKOFF_S):
                    log(
                        "WARN",
                        f"{modelo}: {ultimo_error}; reintento {intento + 1}/"
                        f"{len(ESPERAS_BACKOFF_S)} en {espera_fija:.0f}s",
                    )
                    esperar(espera_fija)
                    continue
                break

            estado = int(getattr(respuesta, "status_code", 0) or 0)
            if estado == 200:
                try:
                    cuerpo = respuesta.json()
                except ValueError:
                    ultimo_error = "JSON invalido"
                    break
                texto = _texto_respuesta(cuerpo if isinstance(cuerpo, dict) else {})
                if not texto:
                    ultimo_error = "respuesta sin contenido"
                    break
                descripcion, etiquetas = separar_respuesta(texto)
                tokens_in, tokens_out = _usage(cuerpo if isinstance(cuerpo, dict) else {})
                return RespuestaVision(
                    ok=True,
                    modelo=modelo,
                    descripcion=descripcion,
                    etiquetas=etiquetas,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )

            if estado == 429:
                ultimo_error = "HTTP 429 (rate limit)"
                if intento < len(ESPERAS_BACKOFF_S):
                    espera = _espera_retry_after(respuesta, espera_fija)
                    log(
                        "WARN",
                        f"{modelo}: 429; reintento {intento + 1}/"
                        f"{len(ESPERAS_BACKOFF_S)} en {espera:.0f}s (Retry-After)",
                    )
                    esperar(espera)
                    continue
                break

            if 500 <= estado < 600:
                ultimo_error = f"HTTP {estado}"
                if intento < len(ESPERAS_BACKOFF_S):
                    log(
                        "WARN",
                        f"{modelo}: {ultimo_error}; reintento {intento + 1}/"
                        f"{len(ESPERAS_BACKOFF_S)} en {espera_fija:.0f}s",
                    )
                    esperar(espera_fija)
                    continue
                break

            ultimo_error = f"HTTP {estado}"
            break

        log("WARN", f"modelo {modelo} agotado ({ultimo_error})")

    return RespuestaVision(ok=False, error=ultimo_error)


# ------------------------------------------------------------------------------
# Parada: bandera .tmp/vision_stop.flag + señales
# ------------------------------------------------------------------------------
_PARAR = False


def reiniciar_parada() -> None:
    """Limpia la bandera en memoria (tests / arranque del CLI)."""
    global _PARAR
    _PARAR = False


def _manejar_senal(_signum: int, _frame: Any) -> None:
    """SIGTERM/SIGINT -> solo marca; la imagen en curso termina y se guarda."""
    global _PARAR
    _PARAR = True


def instalar_manejadores() -> None:
    """Instala SIGINT/SIGTERM (falla silenciosamente fuera del hilo principal)."""
    for nombre in ("SIGINT", "SIGTERM"):
        senal = getattr(signal, nombre, None)
        if senal is None:
            continue
        try:
            signal.signal(senal, _manejar_senal)
        except (ValueError, OSError):  # pragma: no cover - hilo no principal
            pass


def escribir_bandera(cfg: ConfigVision) -> Path:
    """Escribe la bandera de parada (``--stop``)."""
    cfg.bandera_stop.parent.mkdir(parents=True, exist_ok=True)
    cfg.bandera_stop.write_text("stop\n", encoding="utf-8")
    return cfg.bandera_stop


def consumir_bandera(cfg: ConfigVision) -> None:
    """Borra la bandera ya consumida por una pasada (idempotente)."""
    try:
        if cfg.bandera_stop.exists():
            cfg.bandera_stop.unlink()
    except OSError:  # pragma: no cover - permisos
        log("WARN", f"no se pudo borrar la bandera {cfg.bandera_stop}")


def debe_parar(cfg: ConfigVision) -> bool:
    """True si se pidio parada por señal o por bandera."""
    return _PARAR or cfg.bandera_stop.exists()


def esperar_interrumpible(
    segundos: float, cfg: ConfigVision, *, dormir: Callable[[float], None] | None = None
) -> bool:
    """Espera en trozos de 0.5 s comprobando la parada; True si hay que parar."""
    esperar = dormir or _dormir
    restante = float(segundos)
    while restante > 0:
        if debe_parar(cfg):
            return True
        trozo = min(0.5, restante)
        esperar(trozo)
        restante -= trozo
    return debe_parar(cfg)


# ------------------------------------------------------------------------------
# Pasada y bucle
# ------------------------------------------------------------------------------
def procesar_pasada(
    con: sqlite3.Connection,
    cfg: ConfigVision,
    clave: Clave,
    *,
    dormir: Callable[[float], None] | None = None,
) -> ResumenPasada:
    """Una pasada sobre la cola (respeta topes, dedupe y parada)."""
    resumen = ResumenPasada()

    if debe_parar(cfg):
        resumen.parada = "bandera" if cfg.bandera_stop.exists() else "senal"
        consumir_bandera(cfg)
        log("INFO", f"parada solicitada ({resumen.parada}); no se procesa ninguna imagen")
        return resumen

    tope = tope_actual(con, cfg)
    if tope is not None:
        registrar_uso(
            con,
            imagen_id=None,
            modelo=cfg.modelo,
            tokens_in=None,
            tokens_out=None,
            resultado="tope",
            modo=cfg.modo,
        )
        resumen.tope = tope
        log(
            "WARN",
            f"TOPE {tope} alcanzado (max_dia={cfg.max_dia}, max_hora={cfg.max_hora}): "
            "pasada detenida sin llamar a la API (presupuesto intacto)",
        )
        return resumen

    retirados = liberar_procesando(con, stale_ms=cfg.stale_min * MS_POR_MINUTO)
    if retirados:
        log(
            "WARN",
            f"{retirados} imagen(es) con claim STALE (>{cfg.stale_min} min) devueltas "
            "a 'pendiente' (pasada anterior caida)",
        )

    pendientes = seleccionar_pendientes(con, cfg.limite, cfg.orden)
    if not pendientes:
        log("INFO", "cola vacia: no hay imagenes pendientes de describir")
        return resumen

    hash_vistos: set[str] = set()
    for fila in pendientes:
        if debe_parar(cfg):
            resumen.parada = "bandera" if cfg.bandera_stop.exists() else "senal"
            consumir_bandera(cfg)
            log(
                "INFO",
                "parada solicitada: se corta la pasada (reanudable, el estado vive en la BD)",
            )
            break

        tope = tope_actual(con, cfg)
        if tope is not None:
            registrar_uso(
                con,
                imagen_id=fila["id"],
                modelo=cfg.modelo,
                tokens_in=None,
                tokens_out=None,
                resultado="tope",
                modo=cfg.modo,
            )
            resumen.tope = tope
            log(
                "WARN",
                f"TOPE {tope} alcanzado a mitad de pasada: se para "
                f"(imagen {fila['id']} queda pendiente)",
            )
            break

        imagen_id = fila["id"]
        hash_imagen = fila["hash_sha256"]

        # CLAIM atomico por fila (BUG-PROD-VISION-LOCK): la primera pasada que
        # llega se queda la fila. Si otra la reclamo antes, rowcount == 0 ->
        # continue SIN llamar a la API (cero Credits duplicados, regla 16).
        if not reclamar_imagen(con, imagen_id):
            log(
                "INFO",
                f"imagen {imagen_id}: reclamada por otra pasada concurrente; "
                "se omite (cero llamadas)",
            )
            continue

        # Dedupe: ninguna imagen se describe dos veces (SPEC §7.3).
        if hash_imagen in hash_vistos or hash_ya_descripto(con, hash_imagen, imagen_id):
            _marcar_estado(con, imagen_id, "omitido")
            resumen.omitidas += 1
            log(
                "INFO",
                f"imagen {imagen_id}: hash duplicado ya descrito; marcada 'omitido' "
                "(cero llamadas extra)",
            )
            continue
        hash_vistos.add(hash_imagen)

        archivo = ruta_imagen(cfg.datos_dir, fila["ruta"])
        if archivo is None or not archivo.is_file():
            # Fallo LOCAL (no hay llamada a la API): no consume presupuesto y no
            # se registra en vision_uso; la fila queda 'error' (reintetable).
            _guardar_error(con, imagen_id, modelo=cfg.modelo, ts_ms=ahora_ms())
            resumen.errores += 1
            log(
                "WARN",
                f"imagen {imagen_id}: archivo no disponible o ruta fuera de datos/ "
                f"({fila['ruta']}); marcada 'error' (reintetable)",
            )
            continue

        # La fila YA esta reclamada ('procesando' + vision_ts_ms = instante del
        # claim) desde el inicio del bucle: no hay transicion intermedia.
        try:
            crudo = archivo.read_bytes()
        except OSError as exc:
            # Fallo LOCAL de lectura: tampoco consume presupuesto.
            _guardar_error(con, imagen_id, modelo=cfg.modelo, ts_ms=ahora_ms())
            resumen.errores += 1
            log("WARN", f"imagen {imagen_id}: no se pudo leer ({exc}); marcada 'error'")
            continue

        # Validacion LOCAL del JPEG: un frame corrupto se rechaza aqui con 0
        # Credits (el modelo devolveria HTTP 400 y consumiria cupo del tope).
        ok_jpeg, motivo_jpeg = jpeg_utilizable(crudo)
        if not ok_jpeg:
            _guardar_error(con, imagen_id, modelo=cfg.modelo, ts_ms=ahora_ms())
            resumen.errores += 1
            resumen.ilegibles += 1
            log(
                "WARN",
                f"imagen {imagen_id}: {motivo_jpeg}; marcada 'error' SIN llamar a la "
                "API (cero Credits)",
            )
            continue

        uri = data_uri_jpeg_bytes(crudo)

        respuesta = llamar_vision(cfg, clave, uri, dormir=dormir)
        ts_ms = ahora_ms()
        resumen.procesadas += 1

        if respuesta.ok:
            _guardar_descripcion(
                con,
                imagen_id,
                descripcion=respuesta.descripcion or "",
                etiquetas=respuesta.etiquetas or "",
                modelo=respuesta.modelo or cfg.modelo,
                tokens=respuesta.tokens_total,
                ts_ms=ts_ms,
            )
            registrar_uso(
                con,
                imagen_id=imagen_id,
                modelo=respuesta.modelo or cfg.modelo,
                tokens_in=respuesta.tokens_in,
                tokens_out=respuesta.tokens_out,
                resultado="hecho",
                modo=cfg.modo,
            )
            resumen.hechas += 1
            log(
                "INFO",
                f"imagen {imagen_id}: descrita con {respuesta.modelo} "
                f"({respuesta.tokens_total if respuesta.tokens_total is not None else '?'} tokens)",
            )
        else:
            _guardar_error(con, imagen_id, modelo=cfg.modelo, ts_ms=ts_ms)
            registrar_uso(
                con,
                imagen_id=imagen_id,
                modelo=cfg.modelo,
                tokens_in=None,
                tokens_out=None,
                resultado="error",
                modo=cfg.modo,
            )
            resumen.errores += 1
            log(
                "WARN",
                f"imagen {imagen_id}: error ({respuesta.error}); queda 'error' "
                "para otra pasada (sin bucle)",
            )

    return resumen


def _log_resumen(resumen: ResumenPasada) -> None:
    log(
        "INFO",
        f"pasada terminada: hechas={resumen.hechas} errores={resumen.errores} "
        f"omitidas={resumen.omitidas} ilegibles={resumen.ilegibles} "
        f"tope={resumen.tope or 'no'} parada={resumen.parada or 'no'}",
    )


def ejecutar_pasada(cfg: ConfigVision, clave: Clave) -> ResumenPasada:
    """Abre la DB, valida esquema/ToS y ejecuta UNA pasada.

    Key vacia -> idle honesto (regla 13) sin tocar la red.
    """
    validar_clave_para_modo(cfg, clave)
    if clave.tipo == "vacia":
        log(
            "INFO",
            "IDLE: sin key de IA (DASHSCOPE_API_KEY vacia y sin auth.json del Token "
            "Plan); el sistema funciona sin IA (regla 13). Cero llamadas.",
        )
        return ResumenPasada(idle=True)

    con = abrir_db_verificada(cfg)
    try:
        resumen = procesar_pasada(con, cfg, clave)
    finally:
        con.close()
    _log_resumen(resumen)
    return resumen


def ejecutar_bucle(
    cfg: ConfigVision,
    clave: Clave,
    *,
    intervalo_s: float = INTERVALO_LOOP_S,
    dormir: Callable[[float], None] | None = None,
) -> int:
    """Modo A daemon (``--loop``): pasadas cada ``intervalo_s`` hasta parada.

    - key ``sk-sp-`` -> ``ErrorToS`` (el CLI sale con codigo != 0).
    - key vacia -> idle honesto y exit 0 (regla 13).
    """
    validar_clave_para_modo(cfg, clave)
    if clave.tipo == "vacia":
        log(
            "INFO",
            "IDLE: modo A sin key pay-as-you-go (DASHSCOPE_API_KEY vacia); "
            "el daemon NO arranca y el sistema sigue funcionando sin IA (regla 13).",
        )
        return 0

    instalar_manejadores()
    log(
        "INFO",
        f"modo A daemon: intervalo={intervalo_s:.0f}s db={cfg.db_path} "
        f"modelo={cfg.modelo} topes={cfg.max_dia}/dia {cfg.max_hora}/hora",
    )
    while not debe_parar(cfg):
        try:
            resumen = ejecutar_pasada(cfg, clave)
        except ErrorEsquema:
            raise
        if resumen.tope:
            log(
                "WARN",
                f"tope {resumen.tope}: el daemon sigue vivo pero no llamara a la API "
                "hasta que la ventana movil libere cupo",
            )
        if debe_parar(cfg):
            break
        if esperar_interrumpible(intervalo_s, cfg, dormir=dormir):
            break
    consumir_bandera(cfg)
    log("INFO", "parada limpia del daemon (estado persistido en la BD)")
    return 0


# ------------------------------------------------------------------------------
# Superficie modo B agente-mediado: vision_pending / vision_apply
# ------------------------------------------------------------------------------
def listar_pendientes(cfg: ConfigVision) -> dict[str, Any]:
    """Cola pendiente en JSON (SIN llamar a la API), para que la describa un agente.

    Aplica ``--limit`` y el cupo restante de los topes (no sugiere mas imagenes de
    las que una pasada podria procesar sin quemar Credits).
    """
    con = abrir_db_verificada(cfg)
    try:
        ahora = ahora_ms()
        dia, hora = contar_uso(con, ahora)
        tope = tope_actual(con, cfg, ahora)
        disponibles = min(cfg.max_dia - dia, cfg.max_hora - hora, cfg.limite)
        imagenes: list[dict[str, Any]] = []
        if tope is None and disponibles > 0:
            for fila in seleccionar_pendientes(con, disponibles, cfg.orden):
                archivo = ruta_imagen(cfg.datos_dir, fila["ruta"])
                imagenes.append(
                    {
                        "id": fila["id"],
                        "ruta": str(archivo) if archivo else fila["ruta"],
                        "ruta_relativa": fila["ruta"],
                        "hash_sha256": fila["hash_sha256"],
                        "modelo": cfg.modelo,
                        "prompt": cfg.prompt,
                    }
                )
        if tope is not None:
            log("WARN", f"TOPE {tope} alcanzado: vision_pending no sugiere imagenes")
        return {
            "modo": "B",
            "modelo": cfg.modelo,
            "cadena": list(cfg.cadena),
            "prompt": cfg.prompt,
            "topes": {
                "max_dia": cfg.max_dia,
                "max_hora": cfg.max_hora,
                "usadas_dia": dia,
                "usadas_hora": hora,
            },
            "limite": cfg.limite,
            "orden": cfg.orden,
            "motivo": "tope" if tope is not None else None,
            "imagenes": imagenes,
        }
    finally:
        con.close()


def aplicar_resultados(cfg: ConfigVision, datos: dict[str, Any]) -> ResumenAplicacion:
    """Escribe resultados producidos por el agente (modo B) y registra usage.

    Formato del JSON::

        {"resultados": [
            {"imagen_id": 12, "descripcion": "1-2 frases", "etiquetas": "a, b",
             "modelo": "qwen3.8-flash", "tokens_in": 380, "tokens_out": 120}, ...
        ]}

    Respeta los topes (uno alcanzado -> registra ``tope`` y para) y usa el MISMO
    UPDATE corto que el modo automatico. ``modo`` de ``vision_uso`` siempre 'B'.
    """
    resultados = datos.get("resultados") if isinstance(datos, dict) else None
    if not isinstance(resultados, list) or not resultados:
        raise ErrorConfiguracion(
            "JSON de resultados invalido: falta la lista no vacia 'resultados'"
        )

    resumen = ResumenAplicacion()
    con = abrir_db_verificada(cfg)
    try:
        for indice, item in enumerate(resultados):
            if not isinstance(item, dict):
                resumen.invalidas += 1
                log("WARN", f"resultado #{indice + 1} invalido (no es objeto); se ignora")
                continue
            try:
                imagen_id = int(item.get("imagen_id"))
            except (TypeError, ValueError):
                resumen.invalidas += 1
                log("WARN", f"resultado #{indice + 1} sin 'imagen_id' entero; se ignora")
                continue
            descripcion = str(item.get("descripcion") or "").strip()
            if not descripcion:
                resumen.invalidas += 1
                log("WARN", f"resultado #{indice + 1} (id={imagen_id}) sin descripcion; se ignora")
                continue

            tope = tope_actual(con, cfg)
            if tope is not None:
                registrar_uso(
                    con,
                    imagen_id=imagen_id,
                    modelo=str(item.get("modelo") or cfg.modelo),
                    tokens_in=None,
                    tokens_out=None,
                    resultado="tope",
                    modo="B",
                )
                resumen.tope = tope
                log(
                    "WARN",
                    f"TOPE {tope} alcanzado en vision_apply: se para "
                    f"(quedan {len(resultados) - indice} resultados sin aplicar)",
                )
                break

            existe = con.execute(
                "SELECT 1 FROM imagenes WHERE id = ?", (imagen_id,)
            ).fetchone()
            if existe is None:
                resumen.invalidas += 1
                log("WARN", f"imagen {imagen_id} inexistente en la BD; se ignora")
                continue

            modelo = str(item.get("modelo") or cfg.modelo).strip() or cfg.modelo
            etiquetas = _limpiar_etiquetas(str(item.get("etiquetas") or ""))
            tokens_in = _tokens(item.get("tokens_in"))
            tokens_out = _tokens(item.get("tokens_out"))
            tokens = None if tokens_in is None and tokens_out is None else int(
                (tokens_in or 0) + (tokens_out or 0)
            )
            _guardar_descripcion(
                con,
                imagen_id,
                descripcion=descripcion[:MAX_DESC_CHARS],
                etiquetas=etiquetas,
                modelo=modelo,
                tokens=tokens,
                ts_ms=ahora_ms(),
            )
            registrar_uso(
                con,
                imagen_id=imagen_id,
                modelo=modelo,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                resultado="hecho",
                modo="B",
            )
            resumen.aplicadas += 1
            log("INFO", f"vision_apply: imagen {imagen_id} descrita ({modelo})")
        return resumen
    finally:
        con.close()


def _tokens(valor: Any) -> int | None:
    """Entero >= 0 o ``None`` (tokens del JSON de apply)."""
    if valor is None or valor == "":
        return None
    try:
        return max(0, int(valor))
    except (TypeError, ValueError):
        return None