"""Validador del contrato MQTT (SPEC §6) y esquema SQLite (SPEC §8) de AulaSense.

Interfaz publica (la consumen la ingesta, el dashboard y los tests):

- ``validar(topic, payload)`` -> dict con ``valido``/``error``/``kind``/``datos``.
- ``parse_topic(topic, topic_prefix)`` -> ``(device_id, suffix)`` o ``None``.
- ``ESQUEMA_SQL`` -> DDL literal de SPEC §8 (tablas + indices, idempotente).
- ``migrar_esquema_f25(conn)`` -> migracion idempotente de SPEC §8.2 (F25).
- ``migrar_esquema_f26(conn)`` -> migracion idempotente de SPEC §16.5 (F26:
  decision_*, clef_uso, estado_sistema, telemetria.internal_*, enum config).
- ``nombre_tabla_sesiones_login(conn)`` -> nombre real de la tabla de sesiones de
  login (``sesiones`` o ``sesiones_auth`` si la F3 ya ocupa el nombre).

Reglas de diseno:
- Nunca se lanza una excepcion por payload malformado: tipo errado o rango fuera
  de contrato devuelve ``valido=False`` con un mensaje en espanol.
- ``datos`` devuelve el payload original (ya validado) para que el writer no
  tenga que volver a leerlo.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Callable, Iterator

LOG = logging.getLogger("aulasense.esquema")

# Prefijo contractual del broker compartido (SPEC §6).
TOPIC_PREFIX_DEFECTO = "aulasense/v1/devices"

# Suffix de la tabla SPEC §6. ``capabilities`` se reconoce (esta en la tabla)
# pero no tiene tabla destino: ``validar`` lo acepta con ``kind=None``.
SUFFIX_VALIDOS: tuple[str, ...] = (
    "health",
    "device",
    "capabilities",
    "sensing",
    "wifi",
    "motion",
    "fault",
    "commands/result",
    "aulasense_temp",
    "aulasense_config",
    "image",
    # F26 (SPEC §16.4): retains nuevos del parche. El vendor tambien publica un
    # retain ``ota`` pero su servicio nunca se invoca -> sin colision (PLAN
    # D-F26-1). ``aulasense_ota`` es topic de COMANDO servidor->placa: la ingesta
    # PUBLICA ahi, nunca suscribe (D-F26-2), por eso no figura en esta lista.
    "ota",
    "imagen_estado",
    "heap_interno",
)

# Tipos que se persisten (interfaz fijada por el coordinador).
KINDS_VALIDOS: tuple[str, ...] = (
    "motion",
    "health",
    "device",
    "sensing",
    "wifi",
    "fault",
    "commands_result",
    "aulasense_temp",
    "aulasense_config",
    "image",
    "ota",
    "imagen_estado",
    "heap_interno",
)

# Mapa suffix del topic -> kind logico devuelto por ``validar``.
_SUFFIX_A_KIND: dict[str, str] = {
    "motion": "motion",
    "health": "health",
    "device": "device",
    "sensing": "sensing",
    "wifi": "wifi",
    "fault": "fault",
    "commands/result": "commands_result",
    "aulasense_temp": "aulasense_temp",
    "aulasense_config": "aulasense_config",
    "image": "image",
    "ota": "ota",
    "imagen_estado": "imagen_estado",
    "heap_interno": "heap_interno",
}

# Eventos de provision/config/captura del parche F24 (SPEC §6.1/§8.1), los
# nuevos F26 de placa (SPEC §16.4: OTA_*/FIRMWARE/IMAGEN_INIT/CAMARA_ERROR/
# AP_ERROR) y los derivados de monitoreo SISTEMA_* (D-F26-8: los inserta la
# PROPIA ingesta en el borde de cambio -- patron NODO_ONLINE/OFFLINE --, no la
# placa; se listan aqui porque VIVEN en la misma tabla ``config_eventos`` y el
# CHECK del tipo debe aceptarlos).
EVENTOS_CONFIG: tuple[str, ...] = (
    "AP_FALLBACK",
    "CFG_GUARDADA",
    "STA_RECONECTADO",
    "IMAGEN_ON",
    "IMAGEN_GATE_OFF",
    "IMAGEN_DESCARTE",
    # F26 eventos de placa (SPEC §16.4).
    "OTA_CHECK",
    "OTA_INICIO",
    "OTA_OK",
    "OTA_ERROR",
    "FIRMWARE",
    "IMAGEN_INIT",
    "CAMARA_ERROR",
    "AP_ERROR",
    # F26 derivados de monitoreo por la ingesta (SPEC §16.7).
    "SISTEMA_MQTT_CAIDO",
    "SISTEMA_MQTT_RECUPERADO",
    "SISTEMA_TELEMETRIA_STALE",
    "SISTEMA_DISCO_WARN",
    "SISTEMA_TUNEL_CAIDO",
)

_ID_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_COMMAND_ID = re.compile(r"^[\x20-\x7E]{1,64}$")

# --- Contrato del topic ``image`` (SPEC §6.2, F25) --------------------------

# Version del envelope que entiende esta ingesta (desconocida -> descarte).
VERSION_ENVELOPE_IMAGEN = 1
# Motivos aceptados hoy: solo la foto del onset.
MOTIVOS_IMAGEN: tuple[str, ...] = ("onset",)
# Tamano MAXIMO del envelope JSON completo (SPEC §6.2).
MAX_ENVELOPE_IMAGEN_BYTES = 64 * 1024
# Tope del base64 segun el firmware (SPEC §3.2.1 / AULASENSE_IMAGEN_MQTT_MAX_B64).
MAX_B64_IMAGEN_CHARS = 16384
# Base64 estandar ESTRICTO: alfabeto + hasta 2 '=' finales (sin saltos de linea).
_RE_B64_ESTRICTO = re.compile(r"[A-Za-z0-9+/]*={0,2}")
_RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Campos que NUNCA deben viajar por MQTT (privacidad del contrato, SPEC §6 wifi).
_CAMPOS_PRIVADOS = ("ssid", "bssid", "ip", "ip_address", "sta_ip")


def _error(mensaje: str) -> dict[str, Any]:
    return {"valido": False, "error": mensaje, "kind": None, "datos": None}


def _ok(kind: str | None, datos: dict[str, Any]) -> dict[str, Any]:
    return {"valido": True, "error": None, "kind": kind, "datos": datos}


# --- Helpers de tipos estrictos (bool NO es int/float en este contrato) -----


def _es_bool(valor: Any) -> bool:
    return isinstance(valor, bool)


def _es_int(valor: Any) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)


def _es_num(valor: Any) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _es_str(valor: Any) -> bool:
    return isinstance(valor, str)


def _es_hex16(valor: Any) -> bool:
    return isinstance(valor, str) and _ID_HEX16.fullmatch(valor) is not None


# --- Validadores por tipo (devuelven None si el payload es correcto) --------


def _validar_ia_imagen(p: dict[str, Any]) -> str | None:
    """Campo OPCIONAL ``ia_imagen`` (SPEC §6.1): resultado de la vision persona.

    Ausente o ``null`` es valido (la ingesta lo tolera -> null en BD). Si viene,
    debe ser ``{"persona": bool, "score": float 0..1}``; un tipo errado es invalido.
    """
    ia = p.get("ia_imagen")
    if ia is None:
        return None
    if not isinstance(ia, dict):
        return "motion.ia_imagen debe ser objeto o null"
    if not _es_bool(ia.get("persona")):
        return "motion.ia_imagen.persona debe ser booleano"
    score = ia.get("score")
    if not _es_num(score):
        return "motion.ia_imagen.score debe ser numerico"
    if not 0.0 <= float(score) <= 1.0:
        return "motion.ia_imagen.score fuera de rango [0,1]"
    return None


def _validar_motion(p: dict[str, Any]) -> str | None:
    ts = p.get("timestamp_ms")
    if not _es_int(ts):
        return "motion.timestamp_ms debe ser entero"
    if ts < 0:
        return "motion.timestamp_ms no puede ser negativo"
    if p.get("state") not in ("idle", "motion"):
        return "motion.state debe ser 'idle' o 'motion'"
    score = p.get("score")
    if not _es_num(score):
        return "motion.score debe ser numerico"
    if not 0.0 <= float(score) <= 1.0:
        return "motion.score fuera de rango [0,1]"
    return _validar_ia_imagen(p)


def _validar_health(p: dict[str, Any]) -> str | None:
    if p.get("status") not in ("ok", "offline"):
        return "health.status debe ser 'ok' u 'offline'"
    if not _es_bool(p.get("online")):
        return "health.online debe ser booleano"
    uptime = p.get("uptime_s")
    if not _es_int(uptime):
        return "health.uptime_s debe ser entero"
    if uptime < 0:
        return "health.uptime_s no puede ser negativo"
    ts = p.get("timestamp_ms")
    if not _es_int(ts):
        return "health.timestamp_ms debe ser entero"
    if ts < 0:
        return "health.timestamp_ms no puede ser negativo"
    return None


def _validar_device(p: dict[str, Any], topic: str) -> str | None:
    device_id = p.get("device_id")
    if not _es_hex16(device_id):
        return "device.device_id debe ser 16 hex lowercase"
    if p.get("frontend") != "native":
        return "device.frontend debe ser 'native'"
    if p.get("chip") != "esp32s3":
        return "device.chip debe ser 'esp32s3'"
    for campo in ("name", "label", "firmware", "csi_profile"):
        if not _es_str(p.get(campo)):
            return f"device.{campo} debe ser texto"
    # Coherencia opcional topic <-> payload (si el topic es de nuestro prefijo).
    partes = parse_topic(topic)
    if partes is not None and partes[0] != device_id:
        return "device.device_id no coincide con el topic"
    return None


# Claves reconocidas de ``sensing`` con su comprobador de tipo (SPEC §6).
_REGLAS_SENSING: dict[str, Callable[[Any], bool]] = {
    "enabled": _es_bool,
    "ready": _es_bool,
    "calibrating": _es_bool,
    "mode": _es_str,
    "detector": _es_str,
    "traffic_generator_mode": _es_str,
    "threshold": _es_num,
    "motion_on_hits": _es_num,
    "motion_off_hits": _es_num,
    "csi_target_pps": _es_num,
}


def _validar_sensing(p: dict[str, Any]) -> str | None:
    presentes = [clave for clave in _REGLAS_SENSING if clave in p]
    if not presentes:
        return "sensing sin claves reconocidas"
    for clave in presentes:
        if not _REGLAS_SENSING[clave](p[clave]):
            return f"sensing.{clave} tiene tipo invalido"
    return None


def _es_campo_privado(clave: str) -> bool:
    nombre = clave.lower()
    return (
        nombre in _CAMPOS_PRIVADOS
        or "ssid" in nombre
        or "bssid" in nombre
        or nombre.endswith("_ip")
    )


def _validar_wifi(p: dict[str, Any]) -> str | None:
    privados = [clave for clave in p if _es_campo_privado(clave)]
    if privados:
        # Nunca se repite el valor: solo se informa el nombre del campo prohibido.
        return "wifi incluye campos privados no permitidos (SSID/BSSID/IP)"
    if not _es_bool(p.get("configured")):
        return "wifi.configured debe ser booleano"
    if not _es_bool(p.get("connected")):
        return "wifi.connected debe ser booleano"
    if "band" in p and not (_es_str(p["band"]) or _es_int(p["band"])):
        return "wifi.band debe ser texto o entero"
    if "channel" in p and not _es_int(p["channel"]):
        return "wifi.channel debe ser entero"
    if "rssi_dbm" in p and not _es_int(p["rssi_dbm"]):
        return "wifi.rssi_dbm debe ser entero"
    return None


def _validar_fault(p: dict[str, Any]) -> str | None:
    ts = p.get("timestamp_ms")
    if not _es_int(ts):
        return "fault.timestamp_ms debe ser entero"
    if ts < 0:
        return "fault.timestamp_ms no puede ser negativo"
    if not _es_str(p.get("message")):
        return "fault.message debe ser texto"
    return None


def _validar_commands_result(p: dict[str, Any]) -> str | None:
    command_id = p.get("command_id")
    if not isinstance(command_id, str) or _COMMAND_ID.fullmatch(command_id) is None:
        return "commands_result.command_id debe ser 1-64 caracteres ASCII imprimibles"
    if not _es_str(p.get("command")) or not p["command"]:
        return "commands_result.command debe ser texto no vacio"
    if not _es_bool(p.get("accepted")):
        return "commands_result.accepted debe ser booleano"
    for campo in ("code", "message"):
        if p.get(campo) is not None and not _es_str(p[campo]):
            return f"commands_result.{campo} debe ser texto o null"
    if p.get("data") is not None and not isinstance(p["data"], dict):
        return "commands_result.data debe ser objeto o null"
    return None


def _validar_aulasense_temp(p: dict[str, Any]) -> str | None:
    ts = p.get("timestamp_ms")
    if not _es_int(ts):
        return "aulasense_temp.timestamp_ms debe ser entero"
    if ts < 0:
        return "aulasense_temp.timestamp_ms no puede ser negativo"
    temp = p.get("die_temp_c")
    if not _es_num(temp):
        return "aulasense_temp.die_temp_c debe ser numerico"
    if not -40.0 <= float(temp) <= 150.0:
        return "aulasense_temp.die_temp_c fuera de rango [-40,150]"
    return None


def _validar_aulasense_config(p: dict[str, Any]) -> str | None:
    """Topic ``aulasense_config`` (SPEC §6.1): provision/config/captura SIN secretos."""
    ts = p.get("timestamp_ms")
    if not _es_int(ts):
        return "aulasense_config.timestamp_ms debe ser entero"
    if ts < 0:
        return "aulasense_config.timestamp_ms no puede ser negativo"
    if p.get("evento") not in EVENTOS_CONFIG:
        return (
            "aulasense_config.evento debe ser uno de "
            + "|".join(EVENTOS_CONFIG)
        )
    detalle = p.get("detalle")
    if detalle is not None:
        if not _es_str(detalle):
            return "aulasense_config.detalle debe ser texto o null"
        if len(detalle) > 120:
            return "aulasense_config.detalle supera 120 caracteres"
    if _claves_secreto(p):
        # Nunca se repite el valor: solo se informa que hay un campo de secreto.
        return "aulasense_config incluye campos de secreto no permitidos (SSID/pass/token)"
    return None


def _validar_image(p: dict[str, Any], topic: str) -> str | None:
    """Envelope del topic ``image`` (SPEC §6.2): estructura + base64 estricto.

    Solo valida el CONTRATO del envelope (tipos, rangos, alfabeto b64). La
    decodificacion real, el magic JPEG, el ``tamano`` y el ``sha256`` los
    comprueba el decoder de la ingesta (SPEC §7.2, pasos 3-7), que es el unico
    que tiene el contenido decodificado.
    """
    version = p.get("v")
    if not _es_int(version):
        return "image.v debe ser entero"
    if version != VERSION_ENVELOPE_IMAGEN:
        return (
            f"image.v desconocida ({version}); esta ingesta entiende "
            f"v={VERSION_ENVELOPE_IMAGEN}"
        )
    device_id = p.get("device_id")
    if not _es_hex16(device_id):
        return "image.device_id debe ser 16 hex lowercase"
    partes = parse_topic(topic)
    if partes is not None and partes[0] != device_id:
        return "image.device_id no coincide con el topic"
    ts = p.get("ts_ms")
    if not _es_int(ts):
        return "image.ts_ms debe ser entero"
    if ts < 0:
        return "image.ts_ms no puede ser negativo"
    if p.get("motivo") not in MOTIVOS_IMAGEN:
        return "image.motivo debe ser " + "|".join(MOTIVOS_IMAGEN)
    tamano = p.get("tamano")
    if not _es_int(tamano):
        return "image.tamano debe ser entero"
    if tamano <= 0:
        return "image.tamano debe ser positivo"
    sha = p.get("sha256")
    if not _es_str(sha) or _RE_SHA256.fullmatch(sha) is None:
        return "image.sha256 debe ser 64 hex lowercase"
    b64 = p.get("b64")
    if not _es_str(b64) or not b64:
        return "image.b64 debe ser texto no vacio"
    if len(b64) > MAX_B64_IMAGEN_CHARS:
        return f"image.b64 supera {MAX_B64_IMAGEN_CHARS} caracteres"
    if len(b64) % 4 != 0 or _RE_B64_ESTRICTO.fullmatch(b64) is None:
        return "image.b64 no es base64 estandar estricto (sin saltos de linea)"
    return None


# --- Validadores F26 (SPEC §16.3/§16.4) ------------------------------------

# Estado machines del retain ``ota`` (SPEC §16.4, coherente con app_ota.c F26-c1).
_ESTADOS_OTA: tuple[str, ...] = (
    "idle",
    "checking",
    "downloading",
    "verifying",
    "rebooting",
    "error",
)


def _validar_ota(p: dict[str, Any]) -> str | None:
    """Retain ``ota``: estado de la OTA del modulo propio (SPEC §16.4)."""
    if p.get("estado") not in _ESTADOS_OTA:
        return "ota.estado debe ser uno de " + "|".join(_ESTADOS_OTA)
    ts = p.get("ts_ms")
    if not _es_int(ts):
        return "ota.ts_ms debe ser entero"
    if ts < 0:
        return "ota.ts_ms no puede ser negativo"
    for campo in ("version_actual", "version_objetivo", "detalle"):
        if p.get(campo) is not None and not _es_str(p[campo]):
            return f"ota.{campo} debe ser texto o null"
    if _es_str(p.get("detalle")) and len(p["detalle"]) > 120:
        return "ota.detalle supera 120 caracteres"
    if p.get("sha256_ok") is not None and not _es_bool(p["sha256_ok"]):
        return "ota.sha256_ok debe ser booleano o null"
    if _claves_secreto(p):
        return "ota incluye campos de secreto no permitidos"
    return None


def _validar_imagen_estado(p: dict[str, Any]) -> str | None:
    """Retain ``imagen_estado`` (SPEC §16.3): observabilidad de camara/gate.

    Solo se exige lo minimo accionable (``camara_ok``, ``captura_habilitada``,
    ``ts_ms``); el resto de campos, si vienen, debe respetar su tipo.
    """
    if not _es_bool(p.get("camara_ok")):
        return "imagen_estado.camara_ok debe ser booleano"
    if not _es_bool(p.get("captura_habilitada")):
        return "imagen_estado.captura_habilitada debe ser booleano"
    ts = p.get("ts_ms")
    if not _es_int(ts):
        return "imagen_estado.ts_ms debe ser entero"
    if ts < 0:
        return "imagen_estado.ts_ms no puede ser negativo"
    for campo in ("camara_pid", "camara_err", "build"):
        if p.get(campo) is not None and not _es_str(p[campo]):
            return f"imagen_estado.{campo} debe ser texto o null"
    for campo in ("pipeline_ok", "gate_off", "imagen_habilitado"):
        if p.get(campo) is not None and not _es_bool(p[campo]):
            return f"imagen_estado.{campo} debe ser booleano o null"
    for campo in ("descartes", "imagenes_ok"):
        if p.get(campo) is not None and not _es_int(p[campo]):
            return f"imagen_estado.{campo} debe ser entero o null"
    for campo in ("internal_free_kb", "internal_largest_block_kb"):
        if p.get(campo) is not None and not _es_num(p[campo]):
            return f"imagen_estado.{campo} debe ser numerico o null"
    if _claves_secreto(p):
        return "imagen_estado incluye campos de secreto no permitidos"
    return None


def _validar_heap_interno(p: dict[str, Any]) -> str | None:
    """Retain ``heap_interno`` (SPEC §16.3): heap INTERNO, nunca PSRAM."""
    ts = p.get("ts_ms")
    if not _es_int(ts):
        return "heap_interno.ts_ms debe ser entero"
    if ts < 0:
        return "heap_interno.ts_ms no puede ser negativo"
    for campo in ("internal_free_kb", "internal_largest_block_kb"):
        if campo not in p:
            return f"heap_interno.{campo} es obligatorio"
        if not _es_num(p[campo]):
            return f"heap_interno.{campo} debe ser numerico"
        if p[campo] < 0:
            return f"heap_interno.{campo} no puede ser negativo"
    if _claves_secreto(p):
        return "heap_interno incluye campos de secreto no permitidos"
    return None


# Marcadores de nombre que delatan un secreto dentro de ``aulasense_config``.
_MARCAS_SECRETO = ("ssid", "pass", "password", "token")
# Sufijos que SI se permiten: solo longitudes/flags, nunca el valor.
_SUFIJOS_METADATO = ("_len", "_num")


def _valor_no_vacio(valor: Any) -> bool:
    """``True`` si el valor de un campo sensible tiene contenido real."""
    if valor is None:
        return False
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return valor != 0
    if isinstance(valor, str):
        return bool(valor.strip())
    if isinstance(valor, (list, tuple, dict, set)):
        return len(valor) > 0
    return True


def _claves_secreto(datos: Any) -> list[str]:
    """Claves tipo SSID/pass/token con valor no vacio (recursivo, sin mirar valores).

    ``*_len``/``*_num`` quedan permitidas porque solo llevan longitudes/flags
    (p. ej. ``ssid_len=12``), no el secreto en si (SPEC §6.1/§12).
    """
    encontradas: list[str] = []
    if isinstance(datos, dict):
        for clave, valor in datos.items():
            if isinstance(clave, str):
                nombre = clave.lower()
                es_metadato = nombre.endswith(_SUFIJOS_METADATO)
                if not es_metadato and any(m in nombre for m in _MARCAS_SECRETO):
                    if _valor_no_vacio(valor):
                        encontradas.append(clave)
            encontradas.extend(_claves_secreto(valor))
    elif isinstance(datos, (list, tuple)):
        for elemento in datos:
            encontradas.extend(_claves_secreto(elemento))
    return encontradas


# --- API publica -----------------------------------------------------------


def parse_topic(
    topic: str, topic_prefix: str = TOPIC_PREFIX_DEFECTO
) -> tuple[str, str] | None:
    """Separa ``{prefijo}/{device_id}/{suffix}``.

    Devuelve ``None`` si el topic no cuelga del prefijo, si el ``device_id`` no
    es 16 hex lowercase o si el suffix no figura en la tabla de SPEC §6.
    """
    if not isinstance(topic, str) or not isinstance(topic_prefix, str):
        return None
    prefijo = topic_prefix.rstrip("/")
    if not prefijo or not topic.startswith(prefijo + "/"):
        return None
    resto = topic[len(prefijo) + 1 :]
    if "/" not in resto:
        return None
    device_id, suffix = resto.split("/", 1)
    if _ID_HEX16.fullmatch(device_id) is None:
        return None
    if suffix not in SUFFIX_VALIDOS:
        return None
    return device_id, suffix


def validar(topic: str, payload: dict) -> dict[str, Any]:
    """Valida un mensaje MQTT contra el contrato SPEC §6.

    Devuelve siempre un dict:
    ``{"valido": bool, "error": str|None, "kind": str|None, "datos": dict|None}``.
    """
    partes = parse_topic(topic)
    if partes is None:
        return _error("topic fuera del prefijo o con sufijo/device_id invalido")
    _device_id, suffix = partes
    kind = _SUFFIX_A_KIND.get(suffix)
    if kind is None:
        # ``capabilities`` es valido en el contrato pero no se persiste.
        if suffix == "capabilities" and isinstance(payload, dict):
            return _ok(None, payload)
        return _error(f"sufijo sin validador: {suffix}")
    if not isinstance(payload, dict):
        return _error(f"{kind}: el payload no es un objeto JSON")

    if kind == "motion":
        fallo = _validar_motion(payload)
    elif kind == "health":
        fallo = _validar_health(payload)
    elif kind == "device":
        fallo = _validar_device(payload, topic)
    elif kind == "sensing":
        fallo = _validar_sensing(payload)
    elif kind == "wifi":
        fallo = _validar_wifi(payload)
    elif kind == "fault":
        fallo = _validar_fault(payload)
    elif kind == "commands_result":
        fallo = _validar_commands_result(payload)
    elif kind == "aulasense_config":
        fallo = _validar_aulasense_config(payload)
    elif kind == "image":
        fallo = _validar_image(payload, topic)
    elif kind == "ota":
        fallo = _validar_ota(payload)
    elif kind == "imagen_estado":
        fallo = _validar_imagen_estado(payload)
    elif kind == "heap_interno":
        fallo = _validar_heap_interno(payload)
    else:  # aulasense_temp
        fallo = _validar_aulasense_temp(payload)

    if fallo is not None:
        return _error(fallo)
    return _ok(kind, payload)


# DDL de ``config_eventos`` con FUENTE UNICA: el CHECK de ``tipo`` se genera
# desde ``EVENTOS_CONFIG`` tanto para BD nuevas (dentro de ``ESQUEMA_SQL``)
# como para la reconstruccion R8/F26 (SQLite no permite ALTER de un CHECK).
def _ddl_config_eventos(idempotente: bool) -> str:
    existe = "IF NOT EXISTS " if idempotente else ""
    tipos = ",".join(f"'{tipo}'" for tipo in EVENTOS_CONFIG)
    return (
        "CREATE TABLE "
        + existe
        + "config_eventos (\n"
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "  device_id TEXT NOT NULL, ts_ms INTEGER NOT NULL,\n"
        f"  tipo TEXT NOT NULL CHECK(tipo IN ({tipos})),\n"
        "  detalle TEXT,\n"
        "  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),\n"
        "  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))\n"
        ");"
    )


# DDL literal de SPEC §8 (idempotente: IF NOT EXISTS en tablas e indices).
# El bloque final ``config_eventos`` se GENERA (ver ``_ddl_config_eventos``).
_ESQUEMA_SQL_BASE: str = """
CREATE TABLE IF NOT EXISTS dispositivos (
  device_id TEXT PRIMARY KEY, label TEXT, frontend TEXT, firmware TEXT, chip TEXT, csi_profile TEXT,
  first_seen_ms INTEGER, last_seen_ms INTEGER,
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evaluaciones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL,
  ts_ms INTEGER NOT NULL, ts_device_ms INTEGER NOT NULL, hora_sinc INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('idle','motion')), score REAL NOT NULL,
  sesion_id INTEGER, origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_eval_ts ON evaluaciones(ts_ms);
CREATE INDEX IF NOT EXISTS idx_eval_dev_ts ON evaluaciones(device_id, ts_ms);
CREATE TABLE IF NOT EXISTS eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  tipo TEXT NOT NULL CHECK(tipo IN ('MOVIMIENTO_INICIO','MOVIMIENTO_FIN','CALIBRACION','FALLA','NODO_OFFLINE','NODO_ONLINE')),
  score REAL, duracion_ms INTEGER,
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_eventos_ts ON eventos(ts_ms);
-- Rendimiento (revision 2026-11-15): las consultas por dispositivo mas frecuentes
-- (onset-en-ventana del decoder §7.2, ultimo evento y conteo 24 h del snapshot
-- /api/dispositivos, /api/eventos?device_id=) filtran device_id + rango ts_ms +
-- ORDER BY ts_ms DESC. Con solo idx_eventos_ts, SQLite escanea el indice temporal
-- y descarta filas de otros devices (O(N_eventos)); este cubre las cuatro con un
-- seek compuesto. IF NOT EXISTS -> idempotente (doble pasada OK).
CREATE INDEX IF NOT EXISTS idx_eventos_dev_ts ON eventos(device_id, ts_ms);
CREATE TABLE IF NOT EXISTS telemetria (
  id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  detection_avg_us REAL, detection_max_us REAL, runtime_load_percent REAL,
  free_memory_kb REAL, minimum_free_memory_kb REAL, cpu_frequency_mhz INTEGER,
  csi_accepted_pps REAL, csi_callback_pps REAL, generator_pps REAL, traffic_tx_pps REAL, traffic_rx_pps REAL,
  wifi_rssi_dbm INTEGER, wifi_channel INTEGER, csi_hw_error_total INTEGER, csi_occupancy REAL,
  detector TEXT, threshold REAL, traffic_generator_mode TEXT, die_temp_c REAL,
  origen TEXT NOT NULL DEFAULT 'real', recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Rendimiento (revision 2026-11-15): telemetria NO tenia ningun indice. El
-- snapshot /api/dispositivos (ultima fila por device) y /api/telemetria?device_id=
-- ordenan (device_id, ts_ms DESC); el MAX(ts_ms) de /api/resumen escanea toda la
-- tabla sin idx_telem_ts. Con el sondeo de diagnosticos cada 30 s la tabla crece
-- ~2.9k filas/dia: estas dos consultas pasarian de O(N) a O(log N + k).
CREATE INDEX IF NOT EXISTS idx_telem_dev_ts ON telemetria(device_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_telem_ts ON telemetria(ts_ms);
CREATE TABLE IF NOT EXISTS validacion_sesiones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sesion TEXT NOT NULL,
  inicio_ms INTEGER NOT NULL, fin_ms INTEGER NOT NULL,
  verdad_terreno TEXT NOT NULL CHECK(verdad_terreno IN ('motion','idle')),
  fuente TEXT NOT NULL DEFAULT 'manual_operador', notas TEXT,
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Extension F3 (session logger, tools/session_logger.py): registro de sesiones
-- de captura. Aditiva e idempotente; NO altera las tablas de SPEC §8. El
-- dashboard la consume para etiquetar ventanas de validacion por sesion y F4
-- puede cruzar evaluaciones.sesion_id por ventana temporal.
CREATE TABLE IF NOT EXISTS sesiones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sesion TEXT NOT NULL UNIQUE,
  inicio_ms INTEGER NOT NULL,
  fin_ms INTEGER,
  estado TEXT NOT NULL DEFAULT 'activa' CHECK(estado IN ('activa','cerrada')),
  device_id TEXT,
  nota TEXT,
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sesiones_inicio ON sesiones(inicio_ms);
CREATE INDEX IF NOT EXISTS idx_sesiones_estado ON sesiones(estado);
CREATE TABLE IF NOT EXISTS fuentes_externas (
  id INTEGER PRIMARY KEY AUTOINCREMENT, fuente TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  lat REAL, lon REAL, temperatura_c REAL, humedad_pct REAL, precipitacion_mm REAL, viento_kmh REAL,
  payload_json TEXT, recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Evidencia de imagen local (D-F24-2/3/7). La foto es accesoria; el evento es la fuente de verdad.
CREATE TABLE IF NOT EXISTS imagenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,                 -- época (mapeo R3 del monótono del nodo)
  ruta TEXT NOT NULL,                     -- relativa bajo server/datos/imagenes/<device>/<archivo>.jpg
  hash_sha256 TEXT NOT NULL,              -- sha256 completo del contenido
  tamano_bytes INTEGER NOT NULL,
  ancho INTEGER, alto INTEGER,            -- 320x240 QVGA esperado
  evento_id INTEGER,                      -- FK nullable → eventos.id (el onset MOVIMIENTO_INICIO que la disparó)
  ia_persona INTEGER,                     -- F24-e opcional: 1/0/null (null si IA off o no inferida)
  ia_score REAL,                          -- F24-e opcional: score persona/null
  imagen_purgada INTEGER NOT NULL DEFAULT 0,  -- 1 si la purga borró el archivo (fila+ia CONSERVADAS → dataset intacto)
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(evento_id) REFERENCES eventos(id)
);
CREATE INDEX IF NOT EXISTS idx_img_dev_ts ON imagenes(device_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_img_evento ON imagenes(evento_id);
CREATE INDEX IF NOT EXISTS idx_img_purgada ON imagenes(imagen_purgada);
"""

# ``config_eventos`` se anexa generada desde EVENTOS_CONFIG (fuente unica; F26
# dio de alta OTA_*/FIRMWARE/IMAGEN_INIT/CAMARA_ERROR/AP_ERROR/SISTEMA_* en el
# CHECK de BD nuevas sin duplicar la lista literal).
ESQUEMA_SQL: str = (
    _ESQUEMA_SQL_BASE
    + "\n-- Eventos de provision/config/captura (D-F24-7 + F26, sin secretos).\n"
    + _ddl_config_eventos(True)
    + "\nCREATE INDEX IF NOT EXISTS idx_cfg_dev_ts ON config_eventos(device_id, ts_ms);\n"
)


# --- Migracion F25 (SPEC §8.2) ---------------------------------------------
#
# SOLO la ingesta aplica este DDL (writer unico). Todo es idempotente:
# - columnas de ``imagenes``: se comprueba ``pragma table_info`` antes de cada
#   ``ALTER TABLE`` (SQLite no tiene ``ADD COLUMN IF NOT EXISTS``);
# - indices y tablas: ``IF NOT EXISTS``.
# Las filas F24 existentes quedan con ``canal='http'`` y ``vision_estado='pendiente'``
# (los DEFAULT de las columnas nuevas): el worker de vision las describe en su
# primera pasada (SPEC §8.2, notas de integridad).

# Columnas nuevas de ``imagenes``: (nombre, DDL para ``ALTER TABLE``).
COLUMNAS_F25_IMAGENES: tuple[tuple[str, str], ...] = (
    ("canal", "canal TEXT NOT NULL DEFAULT 'http' CHECK(canal IN ('http','mqtt'))"),
    ("descripcion", "descripcion TEXT"),
    ("etiquetas", "etiquetas TEXT"),
    (
        "vision_estado",
        "vision_estado TEXT NOT NULL DEFAULT 'pendiente' "
        "CHECK(vision_estado IN ('pendiente','procesando','hecho','error','omitido'))",
    ),
    ("vision_modelo", "vision_modelo TEXT"),
    ("vision_ts_ms", "vision_ts_ms INTEGER"),
    ("vision_tokens", "vision_tokens INTEGER"),
)

# Nombre canonico de la tabla de sesiones de login (SPEC §8.2/§9.2).
TABLA_SESIONES_LOGIN = "sesiones"
# Alternativa cuando la F3 (tools/session_logger.py) YA ocupa ``sesiones`` con
# otro esquema (tabla de log de sesiones de captura): sin esto el
# ``CREATE TABLE IF NOT EXISTS sesiones`` seria un no-op silencioso y el login
# del dashboard no tendria donde guardar el SHA-256 del token.
TABLA_SESIONES_LOGIN_ALT = "sesiones_auth"

_DDL_SESIONES_LOGIN = """
CREATE TABLE IF NOT EXISTS {tabla} (
  token_hash TEXT PRIMARY KEY,                 -- SHA-256 del token de cookie (el token NO se guarda)
  user_id INTEGER NOT NULL REFERENCES usuarios(id),
  creado_en_ms INTEGER NOT NULL,
  ultimo_uso_ms INTEGER NOT NULL,              -- sliding: inactividad 2 h
  expira_en_ms INTEGER NOT NULL,               -- absoluta: 12 h
  ip TEXT, user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_sesiones_expira ON {tabla}(expira_en_ms);
"""

# Login del dashboard (D-F25-8) y presupuesto/auditoria de vision (D-F25-6).
_DDL_F25_AUTH = """
CREATE TABLE IF NOT EXISTS usuarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  pass_hash TEXT NOT NULL,                     -- argon2id (o scrypt fallback), formato autodescriptivo
  creado_en TEXT NOT NULL DEFAULT (datetime('now')),
  ultimo_login_ms INTEGER
);
CREATE TABLE IF NOT EXISTS intentos_login (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ip TEXT NOT NULL, username TEXT NOT NULL,
  ts_ms INTEGER NOT NULL, exito INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_intentos_ip_user_ts ON intentos_login(ip, username, ts_ms);
CREATE TABLE IF NOT EXISTS vision_uso (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms INTEGER NOT NULL, imagen_id INTEGER REFERENCES imagenes(id),
  modelo TEXT NOT NULL, tokens_in INTEGER, tokens_out INTEGER,
  modo TEXT NOT NULL CHECK(modo IN ('A','B')),  -- A=daemon sk-*, B=agente-mediado TP
  resultado TEXT NOT NULL CHECK(resultado IN ('hecho','error','tope'))
);
CREATE INDEX IF NOT EXISTS idx_vision_uso_ts ON vision_uso(ts_ms);
"""


def columnas_tabla(conn: sqlite3.Connection, tabla: str) -> set[str] | None:
    """Columnas actuales de ``tabla`` (``None`` si la tabla no existe).

    El nombre llega por parámetro porque ``PRAGMA`` no admite placeholders; se
    valida contra un identificador estricto antes de interpolarlo.
    """
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tabla) is None:
        raise ValueError(f"nombre de tabla invalido: {tabla!r}")
    try:
        filas = conn.execute(f"PRAGMA table_info({tabla})").fetchall()
    except sqlite3.Error:  # pragma: no cover - DB corrupta/cerrada
        return None
    if not filas:
        return None
    return {str(fila[1]) for fila in filas}


def nombre_tabla_sesiones_login(conn: sqlite3.Connection) -> str:
    """Nombre real de la tabla de sesiones de login en esta DB (SPEC §8.2).

    Devuelve ``sesiones`` si es libre (o ya es la de login) y ``sesiones_auth``
    cuando la F3 ya creo ``sesiones`` con el esquema del session logger. El
    dashboard (F25-e) debe resolverlo con esta funcion, no asumir el nombre.
    """
    columnas = columnas_tabla(conn, TABLA_SESIONES_LOGIN)
    if columnas is None or "token_hash" in columnas:
        return TABLA_SESIONES_LOGIN
    return TABLA_SESIONES_LOGIN_ALT


def _asegurar_indice_hash_unico(conn: sqlite3.Connection) -> int:
    """Crea ``idx_img_hash_unico`` (dedupe cross-canal) saneando duplicados F24.

    El indice es UNIQUE sobre ``hash_sha256``: si una DB F24 ya tuviera el mismo
    contenido dos veces (reenvio HTTP manual), el ``CREATE`` fallaria. Se
    conserva la fila MAS ANTIGUA de cada hash y se borran las copias exactas
    (misma foto), dejando el dataset sin filas repetidas. Devuelve cuantas filas
    se descartaron por esta causa (0 en el caso normal).
    """
    duplicados = conn.execute(
        "SELECT COUNT(*) FROM ("
        " SELECT hash_sha256 FROM imagenes GROUP BY hash_sha256 HAVING COUNT(*) > 1"
        ")"
    ).fetchone()
    descartadas = 0
    if duplicados and int(duplicados[0]) > 0:
        LOG.warning(
            "migracion F25: %s hashes duplicados en imagenes; se conserva la fila "
            "mas antigua de cada uno",
            int(duplicados[0]),
        )
        cursor = conn.execute(
            "DELETE FROM imagenes WHERE id NOT IN ("
            " SELECT MIN(id) FROM imagenes GROUP BY hash_sha256"
            ")"
        )
        descartadas = int(cursor.rowcount or 0)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_img_hash_unico ON imagenes(hash_sha256)"
    )
    return descartadas


# DDL de ``config_eventos`` para la reconstruccion R8/F26: el CHECK se genera
# desde ``EVENTOS_CONFIG`` con la MISMA fuente que las BD nuevas (F26 elimino
# la duplication de la lista literal). SQLite no permite ALTER de un CHECK: las
# BD ya creadas en produccion se reconstruyen con ESTE DDL.
_DDL_CONFIG_EVENTOS = _ddl_config_eventos(False)

# Columnas copiadas en la reconstruccion R8 (orden = DDL vigente).
_COLUMNAS_CONFIG_EVENTOS: tuple[str, ...] = (
    "id",
    "device_id",
    "ts_ms",
    "tipo",
    "detalle",
    "origen",
    "recibido_en",
)


def _existe_tabla(conn: sqlite3.Connection, tabla: str) -> bool:
    """True si ``tabla`` existe (``sqlite_master``); el nombre se valida antes."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tabla) is None:
        raise ValueError(f"nombre de tabla invalido: {tabla!r}")
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tabla,)
        ).fetchone()
        is not None
    )


@contextmanager
def _transaccion_migracion(conn: sqlite3.Connection, nombre: str) -> Iterator[None]:
    """Transaccion explicita de una migracion: ``BEGIN IMMEDIATE`` … ``COMMIT``.

    ``BEGIN IMMEDIATE`` toma el lock de escritura al entrar: la migracion corre
    en el writer unico (AGENTS.md regla 3), asi ningun otro proceso puede colarse
    entre el ``INSERT…SELECT`` y el ``DROP`` de una reconstruccion. Si el llamador
    YA trae una transaccion abierta (la implicita de un DML previo de la misma
    migracion) se usa un ``SAVEPOINT``: no se commitea trabajo ajeno ni se aborta
    su transaccion. En error: ``ROLLBACK`` (o ``ROLLBACK TO``) y la excepcion sube;
    ``nombre`` debe ser un identificador literal de este modulo.
    """
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", nombre) is None:
        raise ValueError(f"nombre de savepoint invalido: {nombre!r}")
    propia = not conn.in_transaction
    savepoint = f"migracion_{nombre}"
    if propia:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except BaseException:
        try:
            if propia:
                conn.rollback()
            else:
                conn.execute(f"ROLLBACK TO {savepoint}")
                conn.execute(f"RELEASE {savepoint}")
        except sqlite3.Error:  # pragma: no cover - conexion ya rota
            pass
        raise
    else:
        if propia:
            conn.commit()
        else:
            conn.execute(f"RELEASE {savepoint}")


def _enum_config_eventos_vigente(ddl: str | None) -> bool:
    """True si el DDL de ``config_eventos`` ya lista TODOS los ``EVENTOS_CONFIG``."""
    return bool(ddl) and all(f"'{tipo}'" in ddl for tipo in EVENTOS_CONFIG)


def _legacy_volcada_en_config_eventos(conn: sqlite3.Connection) -> bool:
    """True si TODA fila de ``config_eventos_legacy`` ya vive en la tabla nueva."""
    fila = conn.execute(
        "SELECT COUNT(*) FROM config_eventos_legacy AS legacy "
        "WHERE NOT EXISTS (SELECT 1 FROM config_eventos AS nuevo "
        "WHERE nuevo.id = legacy.id)"
    ).fetchone()
    return int(fila[0] if fila is not None else 0) == 0


def _descartar_config_eventos_legacy(conn: sqlite3.Connection) -> bool:
    """Completa y descarta una ``config_eventos_legacy`` huerfana (crash R8).

    Estado legitimo de entrada: la tabla vigente ya tiene el enum completo. Aun
    asi puede faltarle alguna fila si el proceso murio entre el ``CREATE`` y el
    ``INSERT…SELECT`` de la version SIN transaccion (gate F26-bd MEDIO-1): se
    completan por id (``INSERT OR IGNORE``) y solo cuando NINGUNA fila de la copia
    queda sin volcar se hace ``DROP``. Devuelve ``True`` si la copia desaparecio.
    """
    columnas = ", ".join(_COLUMNAS_CONFIG_EVENTOS)
    with _transaccion_migracion(conn, "r8_legacy"):
        conn.execute(
            f"INSERT OR IGNORE INTO config_eventos ({columnas}) "
            f"SELECT {columnas} FROM config_eventos_legacy"
        )
        if not _legacy_volcada_en_config_eventos(conn):
            LOG.warning(
                "migracion R8: config_eventos_legacy conserva filas sin volcar; "
                "NO se descarta (se reintenta en la proxima pasada)"
            )
            return False
        conn.execute("DROP TABLE config_eventos_legacy")
    LOG.info(
        "migracion R8: se descarta config_eventos_legacy huerfana (enum ya vigente)"
    )
    return True


def _migrar_config_eventos_enum(conn: sqlite3.Connection) -> bool:
    """R8: pone al dia el CHECK de ``config_eventos.tipo`` (``IMAGEN_ON`` incluido).

    SQLite no permite modificar un CHECK: se copia la tabla tal cual, se recrea
    con el enum vigente de ``EVENTOS_CONFIG`` (fuente unica) y se restauran las
    filas con sus ids. Idempotente: si el CHECK ya lista todos los eventos, no
    toca nada (solo limpia una ``config_eventos_legacy`` huerfana). Devuelve
    ``True`` solo cuando reconstruyo la tabla.

    Crash-safe (gate F26-bd MEDIO-1): ``RENAME`` + ``CREATE`` + ``INSERT…SELECT``
    + índice + ``DROP`` corren en UNA transaccion (``BEGIN IMMEDIATE``…``COMMIT``
    con rollback en error), asi que un corte deja o la tabla vieja intacta o la
    nueva completa y sin copia. La huerfana que hubiera dejado una corrida de la
    version anterior se resuelve al entrar (volcado por id + ``DROP``), y la
    ventana ``RENAME``→``CREATE`` se repara restaurando la copia.
    """
    fila = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'config_eventos'"
    ).fetchone()
    ddl_actual = fila[0] if fila is not None else None
    hay_legacy = _existe_tabla(conn, "config_eventos_legacy")

    if ddl_actual is None and hay_legacy:
        # Ventana RENAME->CREATE de la version sin transaccion: la tabla se
        # quedo solo en la copia. Se restaura y se sigue con la reconstruccion.
        with _transaccion_migracion(conn, "r8_restaura"):
            conn.execute("ALTER TABLE config_eventos_legacy RENAME TO config_eventos")
        LOG.warning(
            "migracion R8: config_eventos ausente; se restaura desde la copia legacy"
        )
        fila = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'config_eventos'"
        ).fetchone()
        ddl_actual = fila[0] if fila is not None else None
        hay_legacy = False

    if ddl_actual is None:
        return False

    if _enum_config_eventos_vigente(ddl_actual):
        if hay_legacy:
            _descartar_config_eventos_legacy(conn)
        return False

    LOG.info("migracion R8: se reconstruye config_eventos con el enum vigente")
    columnas = ", ".join(_COLUMNAS_CONFIG_EVENTOS)
    with _transaccion_migracion(conn, "r8_reconstruye"):
        if hay_legacy:
            # Copia sin resolver junto a una tabla con enum viejo (estado que la
            # version anterior no produce): se vuelca lo que falte por id para
            # poder renombrar sin colision; si choca con el CHECK viejo, el
            # ROLLBACK deja ambas tablas como estaban y el error sube.
            conn.execute(
                f"INSERT OR IGNORE INTO config_eventos ({columnas}) "
                f"SELECT {columnas} FROM config_eventos_legacy"
            )
            conn.execute("DROP TABLE config_eventos_legacy")
        conn.execute("ALTER TABLE config_eventos RENAME TO config_eventos_legacy")
        conn.execute(_DDL_CONFIG_EVENTOS)
        conn.execute(
            f"INSERT INTO config_eventos ({columnas}) "
            f"SELECT {columnas} FROM config_eventos_legacy"
        )
        conn.execute("DROP TABLE config_eventos_legacy")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_cfg_dev_ts ON config_eventos(device_id, ts_ms)"
        )
    return True


def migrar_esquema_f25(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aplica el esquema extendido F25 de SPEC §8.2 (idempotente, doble pasada OK).

    Devuelve un resumen utilizable por logs/tests: columnas anadidas en esta
    pasada, nombre de la tabla de sesiones de login y filas de imagen descartadas
    por dedupe previo al indice unico.
    """
    resumen: dict[str, Any] = {
        "columnas_nuevas": [],
        "tabla_sesiones_login": TABLA_SESIONES_LOGIN,
        "imagenes_duplicadas_descartadas": 0,
        "config_eventos_reconstruida": False,
    }

    columnas = columnas_tabla(conn, "imagenes")
    if columnas is None:
        # Sin tabla ``imagenes`` no hay nada que migrar: ``ESQUEMA_SQL`` la crea.
        LOG.warning("migracion F25: tabla imagenes inexistente; se omite")
        columnas = set()

    for nombre, ddl in COLUMNAS_F25_IMAGENES:
        if nombre in columnas:
            continue
        conn.execute(f"ALTER TABLE imagenes ADD COLUMN {ddl}")
        resumen["columnas_nuevas"].append(nombre)

    if columnas_tabla(conn, "imagenes") is not None:
        resumen["imagenes_duplicadas_descartadas"] = _asegurar_indice_hash_unico(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_img_vision_estado ON imagenes(vision_estado)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_img_canal ON imagenes(canal)")

    # Indices de consultas frecuentes (revision de rendimiento 2026-11-15):
    # cubren el decoder §7.2 (onset-en-ventana), el snapshot /api/dispositivos,
    # /api/eventos?device_id=, /api/telemetria?device_id= y el MAX(ts_ms) de
    # /api/resumen. Guardados por existencia de tabla como el resto de la
    # migracion (los tests F24 pueden invocar esta funcion con BD parcial donde
    # eventos/telemetria no existen; CREATE INDEX sobre tabla inexistente fallaria
    # incluso con IF NOT EXISTS). Idempotente: doble pasada OK.
    if columnas_tabla(conn, "eventos") is not None:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_eventos_dev_ts ON eventos(device_id, ts_ms)"
        )
    if columnas_tabla(conn, "telemetria") is not None:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_telem_dev_ts ON telemetria(device_id, ts_ms)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_telem_ts ON telemetria(ts_ms)")

    # --- R8: enum de ``config_eventos.tipo`` al dia (IMAGEN_ON) -------------
    resumen["config_eventos_reconstruida"] = _migrar_config_eventos_enum(conn)

    # --- Login del dashboard y presupuesto de vision (D-F25-8/D-F25-6) ------
    conn.executescript(_DDL_F25_AUTH)

    columnas_sesiones = columnas_tabla(conn, TABLA_SESIONES_LOGIN)
    if columnas_sesiones is not None and "token_hash" not in columnas_sesiones:
        LOG.warning(
            "migracion F25: la tabla 'sesiones' ya existe con otro esquema "
            "(session logger F3); las sesiones de login viven en '%s'",
            TABLA_SESIONES_LOGIN_ALT,
        )
        tabla_sesiones = TABLA_SESIONES_LOGIN_ALT
    else:
        tabla_sesiones = TABLA_SESIONES_LOGIN
    conn.executescript(_DDL_SESIONES_LOGIN.format(tabla=tabla_sesiones))
    resumen["tabla_sesiones_login"] = tabla_sesiones

    conn.commit()
    return resumen


# --- Migracion F26 (SPEC §16.5) --------------------------------------------
#
# Mismo patron que ``migrar_esquema_f25`` (esquema.py:783 historico): SOLO la
# ingesta la aplica (writer unico), columnas via ``pragma table_info`` antes de
# cada ``ALTER`` (SQLite no tiene ADD COLUMN IF NOT EXISTS) y tablas/indices via
# ``IF NOT EXISTS``. Doble pasada = no-op. Verificacion posterior: SOLO-LECTURA
# por python con URI ``file:...?mode=ro`` (regla 3; no existe CLI sqlite3).

# Columnas Clef de ``imagenes`` (D-F26-7; lista completa de SPEC §16.5,
# incluidas cubre/calidad/actividad que el dashboard muestra como badge
# REVISIÓN_HUMANA cuando decision_cubre < 0.7 -- derivado, NO un estado nuevo).
COLUMNAS_F26_IMAGENES: tuple[tuple[str, str], ...] = (
    (
        "decision_estado",
        "decision_estado TEXT NOT NULL DEFAULT 'pendiente' "
        "CHECK(decision_estado IN ('pendiente','procesando','hecho','error','omitido'))",
    ),
    ("decision_etiqueta", "decision_etiqueta TEXT"),
    ("decision_confianza", "decision_confianza REAL"),
    ("decision_modelo", "decision_modelo TEXT"),
    ("decision_ts_ms", "decision_ts_ms INTEGER"),
    ("decision_tokens", "decision_tokens INTEGER"),
    ("decision_cubre", "decision_cubre REAL"),
    ("decision_calidad", "decision_calidad REAL"),
    ("decision_actividad", "decision_actividad TEXT"),
)

# Columnas de heap INTERNO en ``telemetria`` (D-F26-5; rellenas desde el retain
# ``heap_interno`` cacheado por device o desde campos directos del resultado de
# diagnosticos). Nulas en filas historicas: backwards-compat exigida.
COLUMNAS_F26_TELEMETRIA: tuple[tuple[str, str], ...] = (
    ("internal_free_kb", "internal_free_kb INTEGER"),
    ("internal_largest_block_kb", "internal_largest_block_kb INTEGER"),
)

# Auditoria Clef GEMELA de ``vision_uso`` (esquema.py:662-669 patron): una fila
# por decision, base de los topes moviles AULASENSE_CLEF_MAX_DIA/MAX_HORA (regla 16).
_DDL_CLEF_USO = """
CREATE TABLE IF NOT EXISTS clef_uso (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms INTEGER NOT NULL,
  device_id TEXT,
  imagen_id INTEGER REFERENCES imagenes(id),
  modelo TEXT NOT NULL,
  tokens INTEGER,
  coste_usd REAL,
  exito INTEGER NOT NULL DEFAULT 1,
  detalle TEXT
);
CREATE INDEX IF NOT EXISTS idx_clef_uso_ts ON clef_uso(ts_ms);
"""

# Heartbeat de la ingesta (D-F26-8, writer unico = ingesta): FILA UNICA id=1
# UPSERT cada ciclo del PollerDiagnosticos (~30 s) con is_connected(). El JSON
# ``detalle`` lleva los ultimos retains {ota, imagen_estado, heap_interno} por
# device para que /api/salud (F26-a2) lea el estado OTA sin tocar el broker.
_DDL_ESTADO_SISTEMA = """
CREATE TABLE IF NOT EXISTS estado_sistema (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  ts_ms INTEGER NOT NULL,
  mqtt_conectado INTEGER NOT NULL DEFAULT 0,
  ingesta_activa INTEGER NOT NULL DEFAULT 1,
  detalle TEXT
);
"""


def migrar_esquema_f26(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aplica el esquema F26 de SPEC §16.5 (idempotente, doble pasada OK).

    Devuelve resumen para logs/tests: columnas anadidas, tablas creadas en esta
    pasada y si ``config_eventos`` requigio reconstruccion del enum (los eventos
    nuevos F26 viven en esa tabla -- patron R8, SOLO-LECTURA para verificar).
    """
    resumen: dict[str, Any] = {
        "columnas_nuevas": [],
        "tablas_nuevas": [],
        "config_eventos_reconstruida": False,
    }

    columnas = columnas_tabla(conn, "imagenes")
    if columnas is not None:
        for nombre, ddl in COLUMNAS_F26_IMAGENES:
            if nombre in columnas:
                continue
            conn.execute(f"ALTER TABLE imagenes ADD COLUMN {ddl}")
            resumen["columnas_nuevas"].append(nombre)
    else:
        LOG.warning("migracion F26: tabla imagenes inexistente; se omite")
        columnas = set()

    if columnas_tabla(conn, "imagenes") is not None:
        # Cola de Clef: vision_estado='hecho' AND decision_estado='pendiente'
        # (SPEC §16.6) -- el indice acompana al idx_img_vision_estado de F25.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_img_decision_estado "
            "ON imagenes(decision_estado)"
        )
        # /api/salud 'decision_recientes' (app.py): WHERE decision_estado='hecho'
        # ORDER BY decision_ts_ms DESC, id DESC LIMIT 8. Con el indice anterior
        # SQLite usa temp B-tree sobre todas las 'hecho' (bench synth 5k: p50
        # 4.0 ms / p95 11.5 ms por request, el unico query del set >1 ms a
        # escala). El compuesto ordena gratis: las claves son
        # (decision_estado, decision_ts_ms, rowid) -> backwards-scan O(log n + 8).
        # NO se anade indice sobre hash_sha256: idx_img_hash_unico (F25,
        # esquema.py:877) ya cubre el dedupe de Clef -- verificado con EXPLAIN
        # en BD viva y a 5k (.tmp/gate_f26_rendimiento_aplicado.md). Additivo,
        # idempotente.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_img_decision_ts "
            "ON imagenes(decision_estado, decision_ts_ms)"
        )

    telem = columnas_tabla(conn, "telemetria")
    if telem is None:
        LOG.warning("migracion F26: tabla telemetria inexistente; se omite")
    else:
        for nombre, ddl in COLUMNAS_F26_TELEMETRIA:
            if nombre in telem:
                continue
            conn.execute(f"ALTER TABLE telemetria ADD COLUMN {ddl}")
            resumen["columnas_nuevas"].append(nombre)

    for tabla, ddl in (("clef_uso", _DDL_CLEF_USO), ("estado_sistema", _DDL_ESTADO_SISTEMA)):
        if columnas_tabla(conn, tabla) is None:
            conn.executescript(ddl)
            resumen["tablas_nuevas"].append(tabla)

    # Enum de config_eventos: mismos tipos que F25 (fuente unica EVENTOS_CONFIG).
    resumen["config_eventos_reconstruida"] = _migrar_config_eventos_enum(conn)

    conn.commit()
    return resumen