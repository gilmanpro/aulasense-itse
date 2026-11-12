"""Validador del contrato MQTT (SPEC §6) y esquema SQLite (SPEC §8) de AulaSense.

Interfaz publica (la consumen la ingesta, el dashboard y los tests):

- ``validar(topic, payload)`` -> dict con ``valido``/``error``/``kind``/``datos``.
- ``parse_topic(topic, topic_prefix)`` -> ``(device_id, suffix)`` o ``None``.
- ``ESQUEMA_SQL`` -> DDL literal de SPEC §8 (tablas + indices, idempotente).

Reglas de diseno:
- Nunca se lanza una excepcion por payload malformado: tipo errado o rango fuera
  de contrato devuelve ``valido=False`` con un mensaje en espanol.
- ``datos`` devuelve el payload original (ya validado) para que el writer no
  tenga que volver a leerlo.
"""

from __future__ import annotations

import re
from typing import Any, Callable

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
}

# Eventos de provision/config/captura del parche F24 (SPEC §6.1/§8.1).
EVENTOS_CONFIG: tuple[str, ...] = (
    "AP_FALLBACK",
    "CFG_GUARDADA",
    "STA_RECONECTADO",
    "IMAGEN_GATE_OFF",
    "IMAGEN_DESCARTE",
)

_ID_HEX16 = re.compile(r"^[0-9a-f]{16}$")
_COMMAND_ID = re.compile(r"^[\x20-\x7E]{1,64}$")

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
    else:  # aulasense_temp
        fallo = _validar_aulasense_temp(payload)

    if fallo is not None:
        return _error(fallo)
    return _ok(kind, payload)


# DDL literal de SPEC §8 (idempotente: IF NOT EXISTS en tablas e indices).
ESQUEMA_SQL: str = """
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
CREATE TABLE IF NOT EXISTS telemetria (
  id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  detection_avg_us REAL, detection_max_us REAL, runtime_load_percent REAL,
  free_memory_kb REAL, minimum_free_memory_kb REAL, cpu_frequency_mhz INTEGER,
  csi_accepted_pps REAL, csi_callback_pps REAL, generator_pps REAL, traffic_tx_pps REAL, traffic_rx_pps REAL,
  wifi_rssi_dbm INTEGER, wifi_channel INTEGER, csi_hw_error_total INTEGER, csi_occupancy REAL,
  detector TEXT, threshold REAL, traffic_generator_mode TEXT, die_temp_c REAL,
  origen TEXT NOT NULL DEFAULT 'real', recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS validacion_sesiones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sesion TEXT NOT NULL,
  inicio_ms INTEGER NOT NULL, fin_ms INTEGER NOT NULL,
  verdad_terreno TEXT NOT NULL CHECK(verdad_terreno IN ('motion','idle')),
  fuente TEXT NOT NULL DEFAULT 'manual_operador', notas TEXT,
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
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

-- Eventos de provision/config/captura (D-F24-7, sin secretos). Topic aulasense_config (§6.1).
CREATE TABLE IF NOT EXISTS config_eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  tipo TEXT NOT NULL CHECK(tipo IN ('AP_FALLBACK','CFG_GUARDADA','STA_RECONECTADO','IMAGEN_GATE_OFF','IMAGEN_DESCARTE')),
  detalle TEXT,                           -- ≤120 chars, SIN secretos (solo longitudes/flags)
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cfg_dev_ts ON config_eventos(device_id, ts_ms);
"""