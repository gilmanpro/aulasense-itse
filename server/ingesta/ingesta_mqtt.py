"""Ingesta MQTT de AulaSense: paho-mqtt -> cola -> writer unico -> SQLite (WAL) + CSV.

Contrato: SPEC §6 (topics/payloads), §7 (ingesta) y §8 (esquema).

Flujo:
1. El callback de paho SOLO encola ``(topic, payload)`` (nunca escribe en disco).
2. Un unico hilo writer consume la cola y es el UNICO que escribe SQLite y el CSV.
3. ``IngestaWriter`` es inyectable y no depende de paho: los tests lo usan con la
   cola directa (sin broker).

Reloj (R3, critico): ``timestamp_ms`` del nodo es monotono (ms desde boot). La
ingesta estima ``epoca_boot_ms = now_pc_ms - timestamp_ms`` en cada ``health`` y
calcula ``evaluaciones.ts_ms = epoca_boot_ms + timestamp_ms``. ``hora_sinc=1`` solo
si la estimacion tiene <= 120 s; si nunca hubo ``health``, cae a ``now_pc_ms`` con
``hora_sinc=0``.

Idempotencia: al arrancar se ejecuta ``ESQUEMA_SQL`` (IF NOT EXISTS). Las
evaluaciones repetidas (re-entrega del broker) se aceptan a proposito (id
autoincrement, sin UNIQUE): el analisis agrega por ventanas. Los mensajes marcados
``dup`` por paho en QoS 1 se descartan en el callback.

F24 (SPEC §6.1/§7.1): se suscribe ademas ``+/aulasense_config`` (tabla
``config_eventos``). Si ``AULASENSE_UPLOAD_HOST`` no esta vacio, ``main`` arranca
el receptor HTTP de imagen (:mod:`receptor_imagen`) como hilo y sus jobs validados
entran por la MISMA cola que los mensajes MQTT: el writer sigue siendo unico.

F25 (SPEC §6.2/§7.2, D-F25-1): se suscribe ademas ``+/image`` (QoS 0). El callback
de paho NO parsea ese topic: encola ``(topic, payload CRUDO)`` y el writer unico
decodifica el envelope (limite 64 KB, base64 estricto, magic JPEG, ``tamano``,
``sha256``), aplica dedupe por hash, rate-limit 1/10 s, resuelve el FK
``evento_id`` contra el ``MOVIMIENTO_INICIO`` de la ventana y guarda el archivo +
fila con ``canal='mqtt'`` y ``vision_estado='pendiente'``. Todo fallo es descarte
+ log: JAMAS una fila invalida (el evento motion es la fuente de verdad).

``origen`` (SPEC §6.2, OPCIONAL): el decoder lo HONRA en la fila de ``imagenes``
(``real`` | ``sintetico``); default ``real`` si falta y valor invalido -> ``real``
+ WARNING sin descartar el mensaje. El nodo nunca lo envia (solo los fire-tests
etiquetados) y MQTT 3.1.1 no permite saber por mensaje que usuario publico.

Secretos: MQTT_PASS solo se pasa a paho; jamas se registra en logs. El token del
receptor no se imprime (solo su longitud si hiciera falta diagnosticar).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import json
import logging
import os
import queue
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

try:  # ejecucion directa (server/ingesta en sys.path) o via conftest de tests
    import esquema
except ImportError:  # import como paquete server.ingesta
    from . import esquema  # type: ignore[no-redef]

# F25f (fire-test del receptor :8001): bajo systemd ExecStart este modulo corre
# como ``__main__`` y ``receptor_imagen`` hace ``from ingesta_mqtt import ...``,
# lo que creaba una SEGUNDA copia del modulo: isinstance(item, JobImagen) en
# _procesar_item fallaba entre las dos clases homonimas y el job HTTP moria
# ("writer no responde"). Registrar el alias garantiza que ambos paths del
# import resuelven al MISMO objeto de modulo.
if __name__ == "__main__":  # pragma: no cover - solo efecto colateral del alias
    sys.modules.setdefault("ingesta_mqtt", sys.modules[__name__])

LOG = logging.getLogger("aulasense.ingesta")

# Raiz del repo (server/ingesta/ingesta_mqtt.py -> aulasense/).
_RAIZ_REPO = Path(__file__).resolve().parents[2]
_DOTENV_DEFECTO = _RAIZ_REPO / ".env"
_DB_DEFECTO = _RAIZ_REPO / "server" / "datos" / "aulasense.db"
_CSV_DEFECTO = _RAIZ_REPO / "server" / "datos" / "export_evaluaciones.csv"

# Suffix suscritos (SPEC §7). ``aulasense_config`` es el topic F24 de
# provision/config/captura (SPEC §6.1). ``image`` es el transporte MQTT de la
# foto-evidencia (SPEC §6.2, F25) y se suscribe aparte con QoS 0.
SUFIJOS_SUSCRITOS: tuple[str, ...] = (
    "motion",
    "health",
    "device",
    "sensing",
    "wifi",
    "fault",
    "commands/result",
    "aulasense_temp",
    "aulasense_config",
    "image",
)

# Campos del comando read_diagnostics (metricas edge C12, SPEC §6).
CAMPOS_DIAGNOSTICOS: tuple[str, ...] = (
    "detection_avg_us",
    "detection_max_us",
    "runtime_load_percent",
    "free_memory_kb",
    "minimum_free_memory_kb",
    "cpu_frequency_mhz",
    "csi_accepted_pps",
    "csi_callback_pps",
    "generator_pps",
    "traffic_tx_pps",
    "traffic_rx_pps",
    "wifi_rssi_dbm",
    "wifi_channel",
    "csi_hw_error_total",
    "csi_occupancy",
    "mqtt",
)

# Columna de ``telemetria`` -> tipo esperado (SPEC §8). ``mqtt`` es un objeto
# anidado que no tiene columna: se ignora de forma deliberada.
CAMPOS_TELEMETRIA: dict[str, str] = {
    "detection_avg_us": "real",
    "detection_max_us": "real",
    "runtime_load_percent": "real",
    "free_memory_kb": "real",
    "minimum_free_memory_kb": "real",
    "cpu_frequency_mhz": "int",
    "csi_accepted_pps": "real",
    "csi_callback_pps": "real",
    "generator_pps": "real",
    "traffic_tx_pps": "real",
    "traffic_rx_pps": "real",
    "wifi_rssi_dbm": "int",
    "wifi_channel": "int",
    "csi_hw_error_total": "int",
    "csi_occupancy": "real",
    "detector": "str",
    "threshold": "real",
    "traffic_generator_mode": "str",
    "die_temp_c": "real",
}

CSV_CABECERA: tuple[str, ...] = (
    "device_id",
    "ts_ms",
    "ts_device_ms",
    "hora_sinc",
    "state",
    "score",
    "origen",
)

# Umbral de retroceso del reloj monotono que se interpreta como reinicio del nodo.
REBOOT_UMBRAL_MS = 5_000
# Ventana de frescura de la estimacion de epoca_boot (SPEC §6: hora_sinc=1 si fresca).
SYNC_VENTANA_MS = 120_000
# Tamano maximo de la cola callback -> writer (se descarta y avisa si se llena).
COLA_MAX = 20_000
# Intervalo del sondeo de diagnosticos (SPEC §7).
INTERVALO_DIAGNOSTICOS_S = 30.0

# --- Decoder de imagenes MQTT (SPEC §6.2/§7.2, F25) ------------------------
# Tamano maximo del envelope JSON completo (SPEC §6.2) y del JPEG decodificado
# (mismo tope que el receptor HTTP, SPEC §7.1).
MAX_ENVELOPE_IMAGEN_BYTES = esquema.MAX_ENVELOPE_IMAGEN_BYTES
MAX_IMAGEN_BYTES = 64 * 1024
# Rate-limit de imagenes por device (1 imagen / 10 s, igual que §7.1).
RATE_IMAGEN_MS = 10_000
# Ventana del FK evento_id alrededor del ts_ms de la imagen (SPEC §7.2 paso 6).
VENTANA_IMAGEN_PRE_MS = 15_000
VENTANA_IMAGEN_POST_MS = 5_000
# Magic JPEG que debe seguir al b64 decodificado (SPEC §6.2).
_MAGIC_JPEG = b"\xff\xd8\xff"
# Fix S (causa raíz jpegqc 2026-11-15): validación estructural mínima del JPEG.
_EOI_JPEG = b"\xff\xd9"
# Ventana del segment-walk: el SOF debe aparecer antes del primer SOS y dentro
# de los primeros 2 KB (la cabecera OV3660 estándar ocupa ~623 B).
VENTANA_SOF_JPEG = 2048
# Marcadores sin segmento de longitud (TEM y RSTn) más SOI/EOI.
_MARCADORES_SIN_LONGITUD = frozenset({0x01, 0xD8, 0xD9, *range(0xD0, 0xD8)})


def _jpeg_estructura_ok(datos: bytes) -> tuple[bool, str]:
    """Sintaxis mínima JPEG: SOI@0, EOI en los últimos 4 B y SOF antes de SOS.

    Fix S de la causa raíz jpegqc: el byte-loss en la captura (OV3660→GDMA con
    overflow de FIFO) produce JPEGs que empiezan con magic FFD8FF válido y
    terminan en FFD9, pero con huecos de 14-16 B en offsets múltiplos de 16
    dentro de la cabecera (offsets 336-590). El segment-walk de marcadores
    pierde el sync ahí y delata el patrón. Limitación conocida (aceptada, como
    Fix F2 del firmware): un hueco colocado enteramente DENTRO de la entropy
    data (id30-like) no es detectable barato — el walk se detiene en el primer
    SOS; ese último filtro lo pone el QC externo con PIL.

    Devuelve ``(True, "")`` o ``(False, motivo)``.
    """
    if not datos.startswith(b"\xff\xd8"):
        return False, "sin SOI FFD8 en offset 0"
    if _EOI_JPEG not in datos[-4:]:
        return False, "sin EOI FFD9 en los ultimos 4 bytes"
    n = len(datos)
    sof_visto = False
    i = 2
    while i + 1 < n:
        if datos[i] != 0xFF:
            return False, f"perdida de sync: esperado FF en offset {i}"
        k = i + 1
        while k < n and datos[k] == 0xFF:  # bytes de relleno (FF FF ... marker)
            k += 1
        if k >= n:
            return False, "marker trunco al final de datos"
        codigo = datos[k]
        if codigo == 0x00:
            return False, f"byte 00 donde se esperaba marcador (offset {k})"
        if codigo == 0xDA:  # SOS: fin del walk de cabecera
            if not sof_visto:
                return False, "sin marcador SOF antes de SOS"
            return True, ""
        if 0xC0 <= codigo <= 0xCF and codigo not in (0xC4, 0xC8, 0xCC):
            sof_visto = True
        if codigo in _MARCADORES_SIN_LONGITUD:
            i = k + 1
            continue
        if k + 2 >= n:
            return False, "longitud de segmento fuera de datos"
        largo = int.from_bytes(datos[k + 1 : k + 3], "big")
        if largo < 2:
            return False, f"longitud {largo} invalida en offset {k}"
        # El largo incluye los 2 B del propio campo pero no el marcador:
        # el siguiente FF queda en k + largo + 1 (k es el byte del codigo).
        i = k + largo + 1
        if not sof_visto and i >= VENTANA_SOF_JPEG:
            return False, f"sin SOF en los primeros {VENTANA_SOF_JPEG} B"
    return False, "sin SOS (cabecera incompleta)"


def ahora_ms() -> int:
    """Hora del PC en milisegundos (epoch)."""
    return int(time.time() * 1000)


# --- Configuracion desde .env (nunca se imprime MQTT_PASS) -----------------


@dataclass
class MqttConfig:
    """Parametros de conexion leidos de .env.

    ``user`` es el de la INGESTA: ``MQTT_INGESTA_USER`` (srv-electiva, D-F24-6) con
    fallback a ``MQTT_USER`` y, por ultimo, ``srv-electiva``. El nodo publica con
    ``MQTT_USER``; separar usuarios permite revocar la ingesta sin tocar el nodo.
    """

    host: str = ""
    port: int = 1883
    scheme: str = "mqtt"
    user: str = ""
    password: str = field(default="", repr=False)
    topic_prefix: str = esquema.TOPIC_PREFIX_DEFECTO

    @classmethod
    def from_env(cls, dotenv_path: str | Path | None = None) -> "MqttConfig":
        ruta = Path(dotenv_path) if dotenv_path is not None else _DOTENV_DEFECTO
        # Sin ``override``: las variables reales del entorno mandan sobre el .env.
        load_dotenv(ruta)
        raw_port = os.getenv("MQTT_PORT", "1883").strip() or "1883"
        try:
            port = int(raw_port)
        except ValueError:
            port = 1883
        scheme = (os.getenv("MQTT_SCHEME", "mqtt").strip() or "mqtt").lower()
        if scheme not in ("mqtt", "mqtts"):
            scheme = "mqtt"
        # Usuario de la ingesta: MQTT_INGESTA_USER > MQTT_USER > 'srv-electiva'.
        usuario_ingesta = (
            os.getenv("MQTT_INGESTA_USER", "").strip()
            or os.getenv("MQTT_USER", "").strip()
            or "srv-electiva"
        )
        return cls(
            host=os.getenv("MQTT_HOST", "").strip(),
            port=port,
            scheme=scheme,
            user=usuario_ingesta,
            password=os.getenv("MQTT_PASS", ""),
            topic_prefix=(
                os.getenv("MQTT_TOPIC_PREFIX", "").strip()
                or esquema.TOPIC_PREFIX_DEFECTO
            ),
        )

    def descripcion(self) -> str:
        """Resumen seguro para logs (JAMAS incluye la clave)."""
        return (
            f"host={self.host} port={self.port} scheme={self.scheme} "
            f"user={self.user or '(anonimo)'} prefix={self.topic_prefix}"
        )


# --- Writer unico ----------------------------------------------------------


@dataclass
class JobImagen:
    """Imagen validada por el receptor HTTP, lista para persistir por el writer.

    El receptor (:mod:`receptor_imagen`) escribe el JPEG a disco y encola este
    job en la MISMA cola que los mensajes MQTT; el writer unico resuelve el reloj
    R3 e inserta la fila ``imagenes``. ``resultado`` (Event) permite al receptor
    responder 201 con ``imagen_id`` sin romper la invariante de escritor unico.
    """

    device_id: str
    ts_device_ms: int
    ruta: str
    hash_sha256: str
    tamano_bytes: int
    ancho: int | None = None
    alto: int | None = None
    evento_id: int | None = None
    ia_persona: int | None = None
    ia_score: float | None = None
    origen: str = "real"
    imagen_id: int | None = None
    error: str | None = None
    resultado: threading.Event = field(default_factory=threading.Event)


@dataclass
class EstadoDispositivo:
    """Estado en memoria por device (reloj estimado + bordes de estado)."""

    ultimo_state: str | None = None
    motion_inicio_ts: int | None = None
    online: bool | None = None
    uptime_s: int | None = None
    ultimo_ts_device_ms: int | None = None
    epoca_boot_ms: int | None = None
    epoca_estimada_en_ms: int | None = None
    epoca_desfasada: bool = False
    last_seen_ms: int | None = None


class IngestaWriter:
    """Consumidor unico de la cola: valida, resuelve reloj y escribe SQLite/CSV.

    No conoce paho: se puede instanciar y alimentar con ``procesar(topic, payload)``
    desde los tests.
    """

    def __init__(
        self,
        db_path: str | Path,
        csv_path: str | Path,
        topic_prefix: str = esquema.TOPIC_PREFIX_DEFECTO,
        cola: "queue.Queue[Any] | None" = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.csv_path = Path(csv_path)
        self.topic_prefix = topic_prefix
        # Cola compartida (MQTT + jobs de imagen del receptor): la expone el writer
        # para que el receptor encole sin abrir una segunda ruta de escritura.
        self.cola: "queue.Queue[Any]" = (
            cola if cola is not None else queue.Queue(maxsize=COLA_MAX)
        )
        self._activo = True
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._estados: dict[str, EstadoDispositivo] = {}
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(esquema.ESQUEMA_SQL)
        # SPEC §8.2: la migracion F25 la aplica SOLO la ingesta (writer unico).
        self._migracion_f25 = esquema.migrar_esquema_f25(self._conn)
        self._conn.commit()
        # Rate-limit de imagenes MQTT por device (epoca ms de la ultima aceptada).
        self._ultima_imagen_ms: dict[str, int] = {}
        # Directorio base de datos (perezoso: AULASENSE_DATOS_DIR o padre de la DB).
        self._datos_dir_cache: Path | None = None

        # Cabecera del CSV solo si el archivo es nuevo o esta vacio (idempotente).
        self._csv_es_nuevo = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        self._csv_file = open(self.csv_path, "a", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_file)
        if self._csv_es_nuevo:
            self._csv_writer.writerow(CSV_CABECERA)
            self._csv_file.flush()

    # -- API de consumo -----------------------------------------------------

    def procesar(
        self, topic: str, payload: dict, now_ms: int | None = None
    ) -> dict[str, Any]:
        """Valida y persiste un mensaje. Devuelve el resultado de la validacion + accion."""
        ahora = int(now_ms) if now_ms is not None else ahora_ms()
        resultado = esquema.validar(topic, payload)
        if not resultado["valido"]:
            LOG.warning("mensaje descartado topic=%s error=%s", topic, resultado["error"])
            return {**resultado, "accion": "descartado"}

        kind = resultado["kind"]
        if kind is None:  # p. ej. capabilities: reconocido pero sin tabla destino
            return {**resultado, "accion": "sin_persistencia"}
        if kind == "image":
            # El envelope ``image`` lo decodifica el handler F25 (§7.2), que ademas
            # comprueba b64/magic/tamano/sha256/dedupe/rate.
            return self.procesar_imagen_mqtt(topic, resultado["datos"], ahora)

        partes = esquema.parse_topic(topic, self.topic_prefix)
        if partes is None:
            return {
                "valido": False,
                "error": "topic fuera del prefijo configurado",
                "kind": None,
                "datos": None,
                "accion": "descartado",
            }
        device_id = partes[0]

        with self._lock:
            estado = self._estado(device_id)
            try:
                accion = self._despachar(device_id, kind, resultado["datos"], estado, ahora)
                self._conn.commit()
            except sqlite3.Error as exc:  # nunca se cae el writer por una fila
                self._conn.rollback()
                LOG.exception("error de SQLite topic=%s device=%s: %s", topic, device_id, exc)
                return {
                    "valido": True,
                    "error": None,
                    "kind": kind,
                    "datos": resultado["datos"],
                    "accion": "error_sqlite",
                }
        return {**resultado, "accion": accion}

    def procesar_imagen(self, job: JobImagen, now_ms: int | None = None) -> dict[str, Any]:
        """Persiste un job de imagen encolado por el receptor HTTP (writer unico).

        El archivo YA existe en disco (lo escribio el receptor). Resuelve el reloj
        R3, valida el FK nullable ``evento_id`` e inserta en ``imagenes``. Nunca
        lanza: deja el error en ``job.error`` para que el receptor responda.
        """
        ahora = int(now_ms) if now_ms is not None else ahora_ms()
        try:
            with self._lock:
                estado = self._estado(job.device_id)
                if job.evento_id is not None:
                    fila = self._conn.execute(
                        "SELECT 1 FROM eventos WHERE id = ? LIMIT 1",
                        (int(job.evento_id),),
                    ).fetchone()
                    if fila is None:
                        job.error = "evento_id inexistente"
                        return {"ok": False, "error": job.error, "imagen_id": None}
                ts_ms, _hora_sinc = self._resolver_ts(
                    estado, int(job.ts_device_ms), ahora
                )
                origen = job.origen if job.origen in ("real", "sintetico") else "real"
                cursor = self._conn.execute(
                    "INSERT INTO imagenes "
                    "(device_id, ts_ms, ruta, hash_sha256, tamano_bytes, ancho, alto, "
                    " evento_id, ia_persona, ia_score, imagen_purgada, origen) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                    (
                        job.device_id,
                        ts_ms,
                        job.ruta,
                        job.hash_sha256,
                        int(job.tamano_bytes),
                        job.ancho,
                        job.alto,
                        job.evento_id,
                        job.ia_persona,
                        job.ia_score,
                        origen,
                    ),
                )
                job.imagen_id = int(cursor.lastrowid)
                self._upsert_last_seen(job.device_id, ahora)
                self._conn.commit()
        except sqlite3.Error:
            LOG.exception("error de SQLite al persistir imagen device=%s", job.device_id)
            job.error = "error_sqlite"
            return {"ok": False, "error": job.error, "imagen_id": None}
        finally:
            job.resultado.set()
        return {"ok": True, "error": None, "imagen_id": job.imagen_id}

    def procesar_imagen_mqtt(
        self, topic: str, crudo: Any, now_ms: int | None = None
    ) -> dict[str, Any]:
        """Decoder del topic ``image`` (SPEC §7.2, F25): descarte + log o fila.

        ``crudo`` es el payload TAL CUAL lo entrego paho (``bytes``) o, en tests,
        el dict ya parseado. Pasos: envelope <= 64 KB, JSON, contrato (§6.2),
        allowlist en vivo, base64 estricto, magic JPEG, ``tamano``, ``sha256``,
        dedupe por hash, rate-limit 1/10 s, FK ``evento_id`` por ventana y
        archivo + fila ``canal='mqtt'``/``vision_estado='pendiente'``.

        Nunca lanza por un mensaje invalido: devuelve ``accion`` con el motivo del
        descarte (``descartado``/``imagen_descartada_*``/``imagen_duplicada``).
        """
        ahora = int(now_ms) if now_ms is not None else ahora_ms()
        payload, error = parsear_envelope_imagen(crudo)
        if error is not None:
            LOG.warning("imagen MQTT descartada topic=%s error=%s", topic, error)
            return {
                "valido": False,
                "error": error,
                "kind": "image",
                "datos": None,
                "accion": "descartado",
            }
        resultado = esquema.validar(topic, payload)
        if not resultado["valido"]:
            LOG.warning(
                "imagen MQTT descartada topic=%s error=%s", topic, resultado["error"]
            )
            return {**resultado, "accion": "descartado"}

        partes = esquema.parse_topic(topic, self.topic_prefix)
        if partes is None:
            LOG.warning("imagen MQTT descartada: topic fuera del prefijo %s", topic)
            return {
                "valido": False,
                "error": "topic fuera del prefijo configurado",
                "kind": "image",
                "datos": None,
                "accion": "descartado",
            }
        device_id = partes[0]

        with self._lock:
            estado = self._estado(device_id)
            try:
                accion = self._decodificar_imagen(
                    device_id, resultado["datos"], estado, ahora
                )
                self._conn.commit()
            except sqlite3.Error as exc:  # nunca se cae el writer por una imagen
                self._conn.rollback()
                LOG.exception(
                    "error de SQLite al persistir imagen MQTT device=%s: %s",
                    device_id,
                    exc,
                )
                return {
                    "valido": True,
                    "error": None,
                    "kind": "image",
                    "datos": resultado["datos"],
                    "accion": "error_sqlite",
                }
        return {**resultado, "accion": accion}

    def _decodificar_imagen(
        self,
        device_id: str,
        datos: dict[str, Any],
        estado: EstadoDispositivo,
        ahora: int,
    ) -> str:
        """Pasos 2-7 del decoder (SPEC §7.2). Se ejecuta bajo ``self._lock``.

        Devuelve la accion: ``imagen_mqtt`` si persistio, o
        ``imagen_descartada_*`` (incluida ``imagen_descartada_estructura``, Fix S)
        /``imagen_duplicada`` con log del motivo.
        """
        # 2. Allowlist con refresco EN VIVO (mismo patron que el receptor F24-b1:
        # en una DB nueva el retained ``device`` puede llegar despues).
        if not self._dispositivo_conocido(device_id):
            LOG.warning(
                "imagen MQTT descartada: device desconocido device=%s", device_id
            )
            return "imagen_descartada_device"

        # 3. b64 estricto (sin saltos de linea) -> magic -> tamano -> sha256.
        try:
            contenido = base64.b64decode(datos["b64"], validate=True)
        except (binascii.Error, ValueError):
            LOG.warning("imagen MQTT descartada: base64 invalido device=%s", device_id)
            return "imagen_descartada_b64"
        if not contenido:
            LOG.warning("imagen MQTT descartada: JPEG vacio device=%s", device_id)
            return "imagen_descartada_b64"
        if len(contenido) > MAX_IMAGEN_BYTES:
            LOG.warning(
                "imagen MQTT descartada: %d B supera %d B (device=%s)",
                len(contenido),
                MAX_IMAGEN_BYTES,
                device_id,
            )
            return "imagen_descartada_tamano"
        if not contenido.startswith(_MAGIC_JPEG):
            LOG.warning(
                "imagen MQTT descartada: sin magic JPEG FFD8FF device=%s", device_id
            )
            return "imagen_descartada_magic"
        # Fix S (causa raíz jpegqc): el magic bueno no garantiza estructura —
        # el byte-loss en captura deja FFD8FF@0 y FFD9 final con huecos de
        # 16 B en la cabecera. Se exige la sintaxis mínima antes de persistir.
        estructura_ok, motivo_estructura = _jpeg_estructura_ok(contenido)
        if not estructura_ok:
            LOG.warning(
                "imagen MQTT descartada: estructura JPEG invalida (%s) device=%s "
                "sha=%s bytes=%d",
                motivo_estructura,
                device_id,
                hashlib.sha256(contenido).hexdigest()[:8],
                len(contenido),
            )
            return "imagen_descartada_estructura"
        if len(contenido) != int(datos["tamano"]):
            LOG.warning(
                "imagen MQTT descartada: tamano declarado %s != %d reales device=%s",
                datos["tamano"],
                len(contenido),
                device_id,
            )
            return "imagen_descartada_tamano"
        sha = hashlib.sha256(contenido).hexdigest()
        if sha != datos["sha256"]:
            LOG.warning(
                "imagen MQTT descartada: sha256 no coincide device=%s declarado=%s real=%s",
                device_id,
                str(datos["sha256"])[:8],
                sha[:8],
            )
            return "imagen_descartada_sha256"

        # 4. Dedupe por contenido ANTES del rate-limit: cubre reenvios del nodo y
        # la misma foto por los dos canales (HTTP y MQTT). El unique index
        # ``idx_img_hash_unico`` es la red de seguridad ante la carrera.
        if self._imagen_por_hash(sha) is not None:
            LOG.info(
                "imagen MQTT duplicada (hash ya persistido) device=%s sha=%s",
                device_id,
                sha[:8],
            )
            return "imagen_duplicada"

        # 5. Rate-limit 1 imagen/10 s por device (en MQTT no hay 429: descarte+log).
        ultima = self._ultima_imagen_ms.get(device_id)
        if ultima is not None and (ahora - ultima) < RATE_IMAGEN_MS:
            LOG.warning(
                "imagen MQTT descartada por rate-limit (1/10 s) device=%s", device_id
            )
            return "imagen_descartada_rate"

        # 6. R3 (monotono -> epoca) y FK al MOVIMIENTO_INICIO de la ventana.
        ts_ms, _hora_sinc = self._resolver_ts(estado, int(datos["ts_ms"]), ahora)
        evento_id = self._onset_en_ventana(device_id, ts_ms)

        # 7. Archivo con el MISMO patron que §7.1 + fila del canal MQTT.
        destino, relativa = self._ruta_imagen(device_id, ts_ms, sha)
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(contenido)
        except OSError:
            LOG.exception(
                "no se pudo escribir la imagen MQTT en %s (device=%s)",
                destino,
                device_id,
            )
            return "imagen_descartada_disco"

        ancho, alto = dimensiones_jpeg(contenido)
        # 7.b Campo OPCIONAL ``origen`` del envelope (SPEC §6.2): default 'real',
        # invalido -> 'real' + WARNING (JAMAS descarta, la imagen es accesoria).
        origen = origen_desde_envelope(datos)
        try:
            self._conn.execute(
                "INSERT INTO imagenes "
                "(device_id, ts_ms, ruta, hash_sha256, tamano_bytes, ancho, alto, "
                " evento_id, imagen_purgada, origen, canal, vision_estado) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'mqtt', 'pendiente')",
                (
                    device_id,
                    ts_ms,
                    relativa,
                    sha,
                    len(contenido),
                    ancho,
                    alto,
                    evento_id,
                    origen,
                ),
            )
        except sqlite3.IntegrityError:
            # Carrera con el canal HTTP (dos hilos, dos canales, misma foto).
            _borrar_silencioso(destino)
            LOG.info(
                "imagen MQTT duplicada (conflicto del indice unico de hash) device=%s sha=%s",
                device_id,
                sha[:8],
            )
            return "imagen_duplicada"

        self._ultima_imagen_ms[device_id] = ahora
        self._upsert_last_seen(device_id, ahora)
        LOG.info(
            "imagen MQTT persistida device=%s bytes=%d file=%s evento_id=%s",
            device_id,
            len(contenido),
            relativa,
            evento_id,
        )
        return "imagen_mqtt"

    def _dispositivo_conocido(self, device_id: str) -> bool:
        """Allowlist SIN tomar ``self._lock`` (se llama ya bajo el lock)."""
        fila = self._conn.execute(
            "SELECT 1 FROM dispositivos WHERE device_id = ? LIMIT 1", (device_id,)
        ).fetchone()
        return fila is not None

    def _imagen_por_hash(self, sha256_hex: str) -> int | None:
        """Id de la fila con ese contenido (dedupe cross-canal), si existe."""
        fila = self._conn.execute(
            "SELECT id FROM imagenes WHERE hash_sha256 = ? LIMIT 1", (sha256_hex,)
        ).fetchone()
        return int(fila[0]) if fila is not None else None

    def _onset_en_ventana(self, device_id: str, ts_ms: int) -> int | None:
        """``MOVIMIENTO_INICIO`` mas reciente en ``[ts-15 s, ts+5 s]`` (o ``None``)."""
        fila = self._conn.execute(
            "SELECT id FROM eventos "
            "WHERE device_id = ? AND tipo = 'MOVIMIENTO_INICIO' "
            "AND ts_ms BETWEEN ? AND ? "
            "ORDER BY ts_ms DESC, id DESC LIMIT 1",
            (
                device_id,
                int(ts_ms) - VENTANA_IMAGEN_PRE_MS,
                int(ts_ms) + VENTANA_IMAGEN_POST_MS,
            ),
        ).fetchone()
        return int(fila[0]) if fila is not None else None

    def _ruta_imagen(
        self, device_id: str, ts_ms: int, sha256_hex: str
    ) -> tuple[Path, str]:
        """Ruta absoluta + relativa con el patron compartido de SPEC §7.1."""
        sello = datetime.fromtimestamp(int(ts_ms) / 1000).strftime("%Y%m%d_%H%M%S")
        nombre = f"{sello}_{sha256_hex[:8]}.jpg"
        relativa = f"imagenes/{device_id}/{nombre}"
        return self.datos_dir / "imagenes" / device_id / nombre, relativa

    @property
    def datos_dir(self) -> Path:
        """Directorio base de datos (``AULASENSE_DATOS_DIR`` o el padre de la DB)."""
        if self._datos_dir_cache is None:
            self._datos_dir_cache = resolver_datos_dir(self)
        return self._datos_dir_cache

    def run_forever(self, cola: "queue.Queue[Any]") -> None:
        """Bucle del hilo writer: consume hasta recibir el centinela ``None``."""
        try:
            while True:
                item = cola.get()
                if item is None:
                    cola.task_done()
                    return
                try:
                    self._procesar_item(item)
                except Exception:  # pragma: no cover - red de seguridad del hilo
                    LOG.exception("fallo procesando item de la cola")
                finally:
                    cola.task_done()
        finally:
            self._activo = False

    def drenar(self, cola: "queue.Queue[Any]") -> int:
        """Procesa lo que haya en la cola sin bloquear (modo ``--once``)."""
        procesados = 0
        while True:
            try:
                item = cola.get_nowait()
            except queue.Empty:
                return procesados
            try:
                if item is None:
                    continue
                self._procesar_item(item)
                procesados += 1
            finally:
                cola.task_done()

    def _procesar_item(self, item: Any) -> None:
        """Despacha un item de la cola: mensaje MQTT, job HTTP o imagen cruda.

        F25: los mensajes del topic ``image`` llegan como ``(topic, bytes)`` (el
        callback de paho no los parsea) y van al decoder §7.2, que corre en ESTE
        hilo: la conexion SQLite del writer sigue siendo la unica.
        """
        if isinstance(item, JobImagen):
            self.procesar_imagen(item)
            return
        topic, payload = item
        if isinstance(payload, (bytes, bytearray)):
            self.procesar_imagen_mqtt(topic, payload)
            return
        self.procesar(topic, payload)

    @property
    def activo(self) -> bool:
        """``False`` si el writer se cerro o su hilo termino (lo consulta el receptor)."""
        return self._activo

    def dispositivos_conocidos(self, limite: int = 32) -> list[str]:
        """device_id vistos, para el poller de diagnosticos (tolerante a DB vacia)."""
        with self._lock:
            filas = self._conn.execute(
                "SELECT device_id FROM dispositivos ORDER BY last_seen_ms DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [fila[0] for fila in filas]

    def dispositivo_conocido(self, device_id: str) -> bool:
        """Allowlist del receptor: el device debe existir en ``dispositivos``."""
        with self._lock:
            fila = self._conn.execute(
                "SELECT 1 FROM dispositivos WHERE device_id = ? LIMIT 1", (device_id,)
            ).fetchone()
        return fila is not None

    def evento_existe(self, evento_id: int) -> bool:
        """Comprueba el FK nullable ``imagenes.evento_id`` antes de escribir la foto."""
        with self._lock:
            fila = self._conn.execute(
                "SELECT 1 FROM eventos WHERE id = ? LIMIT 1", (int(evento_id),)
            ).fetchone()
        return fila is not None

    def cerrar(self) -> None:
        self._activo = False
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            finally:
                try:
                    self._csv_file.close()
                except OSError:
                    pass

    # -- Estado y reloj -----------------------------------------------------

    def estimar_epoca_ms(
        self, device_id: str, ts_device_ms: int, now_ms: int | None = None
    ) -> tuple[int, bool]:
        """Estimacion READ-ONLY de la epoca del nodo (R3) para nombrar el archivo.

        No muta el estado del reloj: el definitivo lo fija ``_resolver_ts`` al
        persistir la fila. Sin ``health`` previo cae a la hora del PC (R3).
        """
        ahora = int(now_ms) if now_ms is not None else ahora_ms()
        with self._lock:
            estado = self._estado(device_id)
            if estado.epoca_boot_ms is None:
                return ahora, False
            return int(estado.epoca_boot_ms) + int(ts_device_ms), True

    def _estado(self, device_id: str) -> EstadoDispositivo:
        estado = self._estados.get(device_id)
        if estado is None:
            estado = EstadoDispositivo()
            self._estados[device_id] = estado
        return estado

    def _resolver_ts(
        self, estado: EstadoDispositivo, ts_device_ms: int, ahora: int
    ) -> tuple[int, int]:
        """Mapea el reloj monotono del nodo a epoch del PC (R3).

        Devuelve ``(ts_ms, hora_sinc)``. Un retroceso brusco del reloj monotono se
        interpreta como reinicio: se conserva la ultima epoca conocida, se marca
        ``hora_sinc=0`` y los bordes pendientes se invalidan hasta el proximo health.
        """
        if (
            estado.ultimo_ts_device_ms is not None
            and ts_device_ms + REBOOT_UMBRAL_MS < estado.ultimo_ts_device_ms
        ):
            LOG.warning("retroceso del reloj del nodo: posible reinicio")
            estado.epoca_desfasada = True
            estado.motion_inicio_ts = None
            estado.ultimo_state = None
        estado.ultimo_ts_device_ms = ts_device_ms

        if estado.epoca_boot_ms is None:
            return ahora, 0  # sin health previo: fallback a hora del PC, sin sinc
        ts_ms = estado.epoca_boot_ms + ts_device_ms
        fresca = (
            not estado.epoca_desfasada
            and estado.epoca_estimada_en_ms is not None
            and (ahora - estado.epoca_estimada_en_ms) <= SYNC_VENTANA_MS
        )
        return ts_ms, 1 if fresca else 0

    def _detectar_reboot_health(
        self, estado: EstadoDispositivo, datos: dict[str, Any]
    ) -> bool:
        """Reboot segun uptime que cae o timestamp_ms muy inferior al ultimo."""
        reboot = False
        uptime = int(datos["uptime_s"])
        ts_dev = int(datos["timestamp_ms"])
        if estado.uptime_s is not None and uptime < estado.uptime_s - 1:
            reboot = True
        if (
            estado.ultimo_ts_device_ms is not None
            and ts_dev + REBOOT_UMBRAL_MS < estado.ultimo_ts_device_ms
        ):
            reboot = True
        if reboot:
            LOG.info("reinicio del nodo detectado; se re-estima epoca_boot")
            estado.motion_inicio_ts = None
            estado.ultimo_state = None
            estado.epoca_desfasada = False
        return reboot

    # -- Despacho por tipo --------------------------------------------------

    def _despachar(
        self,
        device_id: str,
        kind: str,
        datos: dict[str, Any],
        estado: EstadoDispositivo,
        ahora: int,
    ) -> str:
        if kind == "motion":
            return self._procesar_motion(device_id, datos, estado, ahora)
        if kind == "health":
            return self._procesar_health(device_id, datos, estado, ahora)
        if kind == "device":
            return self._procesar_device(device_id, datos, estado, ahora)
        if kind == "fault":
            return self._procesar_fault(device_id, datos, estado, ahora)
        if kind == "aulasense_temp":
            return self._procesar_temp(device_id, datos, estado, ahora)
        if kind == "aulasense_config":
            return self._procesar_config(device_id, datos, estado, ahora)
        if kind == "commands_result":
            return self._procesar_commands_result(device_id, datos, estado, ahora)
        # sensing/wifi: se validan y se aceptan, pero no tienen tabla destino.
        return f"{kind}_aceptado"

    def _procesar_motion(
        self,
        device_id: str,
        datos: dict[str, Any],
        estado: EstadoDispositivo,
        ahora: int,
    ) -> str:
        ts_dev = int(datos["timestamp_ms"])
        ts_ms, hora_sinc = self._resolver_ts(estado, ts_dev, ahora)
        state = datos["state"]
        score = float(datos["score"])

        self._conn.execute(
            "INSERT INTO evaluaciones "
            "(device_id, ts_ms, ts_device_ms, hora_sinc, state, score, origen) "
            "VALUES (?, ?, ?, ?, ?, ?, 'real')",
            (device_id, ts_ms, ts_dev, hora_sinc, state, score),
        )
        self._csv_evaluacion(device_id, ts_ms, ts_dev, hora_sinc, state, score)
        self._upsert_last_seen(device_id, ahora)

        previo = estado.ultimo_state
        accion = "evaluacion"
        if previo is None:
            # Primer mensaje del device: linea base, sin borde demostrable.
            estado.ultimo_state = state
            accion = f"evaluacion_baseline_{state}"
        elif previo == "idle" and state == "motion":
            self._insertar_evento(device_id, ts_ms, "MOVIMIENTO_INICIO", score=score)
            estado.motion_inicio_ts = ts_ms
            estado.ultimo_state = state
            accion = "movimiento_inicio"
        elif previo == "motion" and state == "idle":
            duracion = (
                None
                if estado.motion_inicio_ts is None
                else max(0, ts_ms - estado.motion_inicio_ts)
            )
            self._insertar_evento(
                device_id, ts_ms, "MOVIMIENTO_FIN", score=score, duracion_ms=duracion
            )
            estado.motion_inicio_ts = None
            estado.ultimo_state = state
            accion = "movimiento_fin"
        else:
            estado.ultimo_state = state
        estado.last_seen_ms = ahora
        return accion

    def _procesar_health(
        self,
        device_id: str,
        datos: dict[str, Any],
        estado: EstadoDispositivo,
        ahora: int,
    ) -> str:
        self._detectar_reboot_health(estado, datos)
        ts_dev = int(datos["timestamp_ms"])
        # Re-estimacion SIEMPRE que llega un health (SPEC §6, R3).
        estado.epoca_boot_ms = ahora - ts_dev
        estado.epoca_estimada_en_ms = ahora
        estado.epoca_desfasada = False
        estado.ultimo_ts_device_ms = ts_dev
        estado.uptime_s = int(datos["uptime_s"])
        estado.last_seen_ms = ahora

        online = bool(datos["online"])
        previo = estado.online
        estado.online = online
        self._upsert_last_seen(device_id, ahora)
        if previo is None or previo != online:
            tipo = "NODO_ONLINE" if online else "NODO_OFFLINE"
            self._insertar_evento(device_id, ahora, tipo)
            return f"health_{tipo.lower()}"
        return "health_actualizado"

    def _procesar_device(
        self,
        device_id: str,
        datos: dict[str, Any],
        estado: EstadoDispositivo,
        ahora: int,
    ) -> str:
        # El payload ``device`` puede traer uptime_s+timestamp_ms (SPEC §6 R3):
        # si vienen, tambien sirven para estimar la epoca sin esperar al health.
        ts_dev = datos.get("timestamp_ms")
        uptime = datos.get("uptime_s")
        if (
            isinstance(ts_dev, int)
            and not isinstance(ts_dev, bool)
            and isinstance(uptime, int)
            and not isinstance(uptime, bool)
            and ts_dev >= 0
        ):
            estado.epoca_boot_ms = ahora - ts_dev
            estado.epoca_estimada_en_ms = ahora
            estado.epoca_desfasada = False
            estado.ultimo_ts_device_ms = ts_dev
            estado.uptime_s = uptime

        self._conn.execute(
            "INSERT INTO dispositivos "
            "(device_id, label, frontend, firmware, chip, csi_profile, "
            " first_seen_ms, last_seen_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET "
            " label=excluded.label, frontend=excluded.frontend, "
            " firmware=excluded.firmware, chip=excluded.chip, "
            " csi_profile=excluded.csi_profile, last_seen_ms=excluded.last_seen_ms",
            (
                device_id,
                datos["label"],
                datos["frontend"],
                datos["firmware"],
                datos["chip"],
                datos["csi_profile"],
                ahora,
                ahora,
            ),
        )
        estado.last_seen_ms = ahora
        return "device_upsert"

    def _procesar_fault(
        self,
        device_id: str,
        datos: dict[str, Any],
        estado: EstadoDispositivo,
        ahora: int,
    ) -> str:
        ts_ms, _hora_sinc = self._resolver_ts(estado, int(datos["timestamp_ms"]), ahora)
        self._insertar_evento(device_id, ts_ms, "FALLA")
        self._upsert_last_seen(device_id, ahora)
        return "evento_falla"

    def _procesar_temp(
        self,
        device_id: str,
        datos: dict[str, Any],
        estado: EstadoDispositivo,
        ahora: int,
    ) -> str:
        ts_ms, _hora_sinc = self._resolver_ts(estado, int(datos["timestamp_ms"]), ahora)
        self._conn.execute(
            "INSERT INTO telemetria (device_id, ts_ms, die_temp_c) VALUES (?, ?, ?)",
            (device_id, ts_ms, float(datos["die_temp_c"])),
        )
        self._upsert_last_seen(device_id, ahora)
        return "telemetria_temp"

    def _procesar_config(
        self,
        device_id: str,
        datos: dict[str, Any],
        estado: EstadoDispositivo,
        ahora: int,
    ) -> str:
        """Topic ``aulasense_config`` -> fila ``config_eventos`` (SPEC §6.1/§8.1)."""
        ts_ms, _hora_sinc = self._resolver_ts(estado, int(datos["timestamp_ms"]), ahora)
        self._conn.execute(
            "INSERT INTO config_eventos (device_id, ts_ms, tipo, detalle, origen) "
            "VALUES (?, ?, ?, ?, 'real')",
            (device_id, ts_ms, datos["evento"], datos.get("detalle")),
        )
        self._upsert_last_seen(device_id, ahora)
        return "config_evento"

    def _procesar_commands_result(
        self,
        device_id: str,
        datos: dict[str, Any],
        estado: EstadoDispositivo,
        ahora: int,
    ) -> str:
        data = datos.get("data")
        valores: dict[str, Any] = {}
        if isinstance(data, dict):
            for campo, tipo in CAMPOS_TELEMETRIA.items():
                if campo not in data:
                    continue
                valor = _coaccionar(data[campo], tipo)
                if valor is not None:
                    valores[campo] = valor

        if not valores:
            # ``mqtt`` (objeto anidado) no tiene columna: sin metricas no se inserta.
            return "commands_result_sin_telemetria"

        ts_dev = datos.get("timestamp_ms")
        if isinstance(ts_dev, int) and not isinstance(ts_dev, bool) and ts_dev >= 0:
            ts_ms, _hora_sinc = self._resolver_ts(estado, ts_dev, ahora)
        else:
            ts_ms = ahora  # el resultado no trae reloj del nodo

        columnas = ["device_id", "ts_ms", *valores.keys()]
        marcadores = ", ".join("?" for _ in columnas)
        self._conn.execute(
            f"INSERT INTO telemetria ({', '.join(columnas)}) VALUES ({marcadores})",
            (device_id, ts_ms, *valores.values()),
        )
        return "telemetria_diagnostics"

    # -- Helpers de escritura ----------------------------------------------

    def _insertar_evento(
        self,
        device_id: str,
        ts_ms: int,
        tipo: str,
        score: float | None = None,
        duracion_ms: int | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO eventos (device_id, ts_ms, tipo, score, duracion_ms, origen) "
            "VALUES (?, ?, ?, ?, ?, 'real')",
            (device_id, int(ts_ms), tipo, score, duracion_ms),
        )

    def _upsert_last_seen(self, device_id: str, ahora: int) -> None:
        self._conn.execute(
            "INSERT INTO dispositivos (device_id, first_seen_ms, last_seen_ms) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET last_seen_ms=excluded.last_seen_ms",
            (device_id, ahora, ahora),
        )

    def _csv_evaluacion(
        self,
        device_id: str,
        ts_ms: int,
        ts_device_ms: int,
        hora_sinc: int,
        state: str,
        score: float,
    ) -> None:
        self._csv_writer.writerow(
            [device_id, ts_ms, ts_device_ms, hora_sinc, state, score, "real"]
        )
        self._csv_file.flush()


def parsear_envelope_imagen(crudo: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Paso 1 del decoder (SPEC §7.2): tamano del envelope + JSON UTF-8 objeto.

    ``crudo`` es el payload binario que entrego paho o, en tests, el dict ya
    parseado. Devuelve ``(payload, error)``; con ``error`` no se persiste NADA.
    """
    if isinstance(crudo, dict):
        return crudo, None
    if not isinstance(crudo, (bytes, bytearray)):
        return None, "payload de imagen con tipo inesperado (se esperaba JSON)"
    contenido = bytes(crudo)
    if len(contenido) > MAX_ENVELOPE_IMAGEN_BYTES:
        return (
            None,
            f"envelope de {len(contenido)} B supera el limite de 64 KB "
            f"({MAX_ENVELOPE_IMAGEN_BYTES} B)",
        )
    try:
        datos = json.loads(contenido.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "payload de imagen no es JSON UTF-8"
    if not isinstance(datos, dict):
        return None, "payload de imagen no es un objeto JSON"
    return datos, None


# Enum del campo OPCIONAL ``origen`` del envelope de imagen (SPEC §6.2).
ORIGENES_IMAGEN: tuple[str, ...] = ("real", "sintetico")


def origen_desde_envelope(datos: dict[str, Any]) -> str:
    """``origen`` a persistir para una imagen MQTT: ``"real"`` | ``"sintetico"``.

    El campo es **OPCIONAL y accesorio** (SPEC §6.2): ausente (o ``null``) ->
    ``"real"`` sin log; ``"sintetico"`` -> ``"sintetico"``; cualquier otro valor
    -> ``"real"`` + WARNING y el mensaje se persiste igual (nunca se rechaza: la
    imagen no es la fuente de verdad, el evento motion si).

    Procedencia del publish (honestidad del contrato): el nodo publica como
    usuario ``electiva`` y la ingesta suscribe como ``srv-electiva`` (SPEC §6),
    pero MQTT 3.1.1 **no transporta la identidad del publicador por mensaje** y
    el callback de paho 2.x no la expone (``msg`` solo trae topic/payload/qos/
    retain). La ACL del broker se aplica al CONECTAR, no por mensaje, asi que el
    decoder NO puede distinguir hoy que cliente emitio el envelope: el campo se
    honra tal cual venga. El riesgo residual es acotado porque el firmware del
    nodo NUNCA envia ``origen`` (solo lo emiten los fire-tests etiquetados) y el
    canal sintetico "oficial" sigue siendo ``POST /internal/evento`` (C13).
    """
    if "origen" not in datos or datos["origen"] is None:
        return "real"
    valor = datos["origen"]
    if isinstance(valor, str) and valor in ORIGENES_IMAGEN:
        return valor
    mostrado = repr(valor)
    if len(mostrado) > 40:  # un valor absurdo no debe inundar el log
        mostrado = mostrado[:37] + "..."
    LOG.warning(
        "imagen MQTT con 'origen' invalido (%s): se persiste como 'real'", mostrado
    )
    return "real"


def _modulo_receptor() -> Any:
    """Importa ``receptor_imagen`` (modulo plano o paquete); ``None`` si no esta."""
    try:
        import receptor_imagen  # type: ignore[import-not-found]

        return receptor_imagen
    except ImportError:  # pragma: no cover - import como paquete server.ingesta
        try:
            from . import receptor_imagen  # type: ignore[no-redef]

            return receptor_imagen
        except ImportError:
            return None


def resolver_datos_dir(writer: "IngestaWriter") -> Path:
    """DIR base de datos: reusa el criterio del receptor HTTP (SPEC §7.1).

    ``AULASENSE_DATOS_DIR`` si esta definido; si no, el padre de la DB
    (``server/datos``). No se reescribe la logica del receptor: se importa.
    """
    modulo = _modulo_receptor()
    if modulo is not None and hasattr(modulo, "resolver_datos_dir"):
        return modulo.resolver_datos_dir(writer)
    crudo = os.environ.get("AULASENSE_DATOS_DIR", "").strip()
    return Path(crudo).expanduser() if crudo else Path(writer.db_path).parent


def dimensiones_jpeg(contenido: bytes) -> tuple[int | None, int | None]:
    """Ancho/alto del JPEG si Pillow esta disponible (mismo helper que §7.1)."""
    modulo = _modulo_receptor()
    if modulo is not None and hasattr(modulo, "_dimensiones"):
        return modulo._dimensiones(contenido)
    return None, None  # pragma: no cover - sin receptor importable


def _borrar_silencioso(ruta: Path) -> None:
    """Borra un archivo huerfano sin propagar errores (la fila ya se descarto)."""
    try:
        ruta.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - disco ocupado/permisos
        LOG.warning("no se pudo borrar el archivo huerfano: %s", ruta)


def _coaccionar(valor: Any, tipo: str) -> Any:
    """Convierte un valor de diagnostico al tipo de columna o devuelve None."""
    if isinstance(valor, bool):
        return None
    if tipo == "real":
        return float(valor) if isinstance(valor, (int, float)) else None
    if tipo == "int":
        if isinstance(valor, int):
            return valor
        if isinstance(valor, float) and valor.is_integer():
            return int(valor)
        return None
    if tipo == "str":
        return valor if isinstance(valor, str) else None
    return None


# --- Capa MQTT (paho) ------------------------------------------------------


def crear_cliente(
    cfg: MqttConfig, cola: "queue.Queue[Any]"
) -> "mqtt.Client":
    """Construye el cliente paho (sin conectar) con callback -> cola."""
    cliente = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"aulasense-ingesta-{os.getpid()}",
        clean_session=True,
    )
    if cfg.user:
        cliente.username_pw_set(cfg.user, cfg.password)

    if cfg.scheme == "mqtts":
        # TLS con CA publica (SPEC §6 S8): nunca se desactiva la verificacion.
        cliente.tls_set()
        cliente.tls_insecure_set(False)

    def on_connect(client, userdata, flags, reason_code, properties=None):
        # paho 2.x entrega un ReasonCode; se evita int() (no siempre lo soporta).
        valor = getattr(reason_code, "value", reason_code)
        if getattr(reason_code, "is_failure", False) or valor != 0:
            LOG.error("conexion MQTT rechazada: rc=%s", reason_code)
            return
        for sufijo in SUFIJOS_SUSCRITOS:
            # ``image`` (F25) se suscribe aparte con QoS 0: el nodo publica QoS 0
            # y retain=false (SPEC §6.2); QoS 1 no aportaria nada.
            if sufijo == "image":
                continue
            client.subscribe(f"{cfg.topic_prefix}/+/{sufijo}", qos=1)
        client.subscribe(f"{cfg.topic_prefix}/+/image", qos=0)
        LOG.info("MQTT conectado; suscrito a %s/+/{...} (+ image QoS 0)", cfg.topic_prefix)

    def on_disconnect(client, userdata, flags, reason_code, properties=None):
        LOG.warning("MQTT desconectado: rc=%s", reason_code)

    def on_message(client, userdata, msg):
        # Re-entrega QoS 1: se ignora para no duplicar eventos derivados.
        if getattr(msg, "dup", False):
            LOG.debug("mensaje dup ignorado topic=%s", msg.topic)
            return
        # F25 (§7.2): la imagen viaja como JSON+b64 y su decoder (limite de 64 KB,
        # b64 estricto, magic, tamano, sha256) corre en el writer. Aqui SOLO se
        # encola el payload CRUDO: el callback no parsea ni escribe.
        if msg.topic.rsplit("/", 1)[-1] == "image":
            try:
                cola.put_nowait((msg.topic, bytes(msg.payload)))
            except queue.Full:  # pragma: no cover - solo bajo carga extrema
                LOG.warning("cola llena: se descarta imagen topic=%s", msg.topic)
            return
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            LOG.warning("payload no JSON descartado topic=%s", msg.topic)
            return
        if not isinstance(payload, dict):
            LOG.warning("payload no objeto descartado topic=%s", msg.topic)
            return
        try:
            cola.put_nowait((msg.topic, payload))
        except queue.Full:  # pragma: no cover - solo bajo carga extrema
            LOG.warning("cola llena: se descarta mensaje topic=%s", msg.topic)

    cliente.on_connect = on_connect
    cliente.on_disconnect = on_disconnect
    cliente.on_message = on_message
    return cliente


class PollerDiagnosticos(threading.Thread):
    """Publica ``read_diagnostics`` cada 30 s a los devices conocidos (SPEC §7)."""

    def __init__(
        self,
        cliente: "mqtt.Client",
        writer: IngestaWriter,
        topic_prefix: str,
        intervalo_s: float = INTERVALO_DIAGNOSTICOS_S,
    ) -> None:
        super().__init__(daemon=True, name="diagnostics-poller")
        self._cliente = cliente
        self._writer = writer
        self._topic_prefix = topic_prefix.rstrip("/")
        self._intervalo_s = intervalo_s
        self._parar = threading.Event()

    def run(self) -> None:
        while not self._parar.wait(self._intervalo_s):
            self.ciclo()

    def ciclo(self) -> None:
        """Un ciclo de sondeo; nunca lanza (tolera cero devices / broker caido)."""
        try:
            if not self._cliente.is_connected():
                return
            devices = self._writer.dispositivos_conocidos()
        except Exception:  # pragma: no cover - red de seguridad del hilo
            LOG.exception("fallo preparando read_diagnostics")
            return
        if not devices:
            LOG.debug("sin devices conocidos: no se publica read_diagnostics")
            return
        marca = ahora_ms()
        for device_id in devices:
            payload = {
                "command_id": f"diag-{marca}-{device_id[:8]}",
                "command": "read_diagnostics",
                "fields": list(CAMPOS_DIAGNOSTICOS),
            }
            try:
                self._cliente.publish(
                    f"{self._topic_prefix}/{device_id}/commands/request",
                    json.dumps(payload),
                    qos=0,
                )
            except Exception:  # pragma: no cover
                LOG.exception("no se pudo publicar read_diagnostics device=%s", device_id)

    def parar(self) -> None:
        self._parar.set()


# --- CLI -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingesta MQTT de AulaSense (paho -> cola -> SQLite WAL + CSV)"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="ruta de la SQLite (default: env AULASENSE_DB o server/datos/aulasense.db)",
    )
    parser.add_argument(
        "--csv", default=None, help="ruta del CSV acumulativo (default: server/datos/export_evaluaciones.csv)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="drena la cola y sale (sin broker; smoke test del writer)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    db_path = Path(args.db or os.getenv("AULASENSE_DB") or _DB_DEFECTO)
    csv_path = Path(args.csv or _CSV_DEFECTO)
    # La cola existe antes que el writer: el receptor de imagen la comparte.
    cola: "queue.Queue[Any]" = queue.Queue(maxsize=COLA_MAX)
    writer = IngestaWriter(db_path, csv_path, cola=cola)

    if args.once:
        procesados = writer.drenar(cola)
        LOG.info("--once: %d mensajes procesados (db=%s)", procesados, db_path)
        writer.cerrar()
        return 0

    cfg = MqttConfig.from_env()
    if not cfg.host:
        LOG.error("MQTT_HOST vacio: configura .env (ver .env.example)")
        writer.cerrar()
        return 2
    LOG.info("ingesta MQTT -> %s ; db=%s", cfg.descripcion(), db_path)

    cliente = crear_cliente(cfg, cola)
    hilo_writer = threading.Thread(
        target=writer.run_forever, args=(cola,), name="sqlite-writer", daemon=True
    )
    hilo_writer.start()
    poller = PollerDiagnosticos(cliente, writer, cfg.topic_prefix)
    poller.start()

    # F24: receptor de imagen HTTP como hilo DENTRO de este proceso (nunca en import).
    # Sus jobs validados entran por la MISMA cola -> writer unico preservado.
    servidor_imagen = None
    try:
        try:  # ejecucion directa (server/ingesta en sys.path)
            from receptor_imagen import iniciar_receptor_desde_env
        except ImportError:  # import como paquete server.ingesta
            from .receptor_imagen import iniciar_receptor_desde_env

        servidor_imagen = iniciar_receptor_desde_env(writer)
    except Exception:  # el receptor jamas debe tumbar la ingesta MQTT
        LOG.exception("no se pudo arrancar el receptor de imagen")

    try:
        cliente.connect(cfg.host, cfg.port, keepalive=30)
        cliente.loop_forever()
    except OSError as exc:
        LOG.error("no se pudo conectar al broker %s:%s (%s)", cfg.host, cfg.port, exc)
        return 1
    except KeyboardInterrupt:
        LOG.info("parada solicitada (Ctrl+C)")
    finally:
        poller.parar()
        try:
            cliente.disconnect()
        except Exception:  # pragma: no cover
            pass
        if servidor_imagen is not None:
            servidor_imagen.shutdown()
            servidor_imagen.server_close()
        cola.put(None)
        hilo_writer.join(timeout=5)
        writer.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())