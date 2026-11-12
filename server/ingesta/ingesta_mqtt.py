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

Secretos: MQTT_PASS solo se pasa a paho; jamas se registra en logs.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import queue
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

try:  # ejecucion directa (server/ingesta en sys.path) o via conftest de tests
    import esquema
except ImportError:  # import como paquete server.ingesta
    from . import esquema  # type: ignore[no-redef]

LOG = logging.getLogger("aulasense.ingesta")

# Raiz del repo (server/ingesta/ingesta_mqtt.py -> aulasense/).
_RAIZ_REPO = Path(__file__).resolve().parents[2]
_DOTENV_DEFECTO = _RAIZ_REPO / ".env"
_DB_DEFECTO = _RAIZ_REPO / "server" / "datos" / "aulasense.db"
_CSV_DEFECTO = _RAIZ_REPO / "server" / "datos" / "export_evaluaciones.csv"

# Suffix suscritos (SPEC §7).
SUFIJOS_SUSCRITOS: tuple[str, ...] = (
    "motion",
    "health",
    "device",
    "sensing",
    "wifi",
    "fault",
    "commands/result",
    "aulasense_temp",
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


def ahora_ms() -> int:
    """Hora del PC en milisegundos (epoch)."""
    return int(time.time() * 1000)


# --- Configuracion desde .env (nunca se imprime MQTT_PASS) -----------------


@dataclass
class MqttConfig:
    """Parametros de conexion leidos de .env."""

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
        return cls(
            host=os.getenv("MQTT_HOST", "").strip(),
            port=port,
            scheme=scheme,
            user=os.getenv("MQTT_USER", "").strip(),
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
    ) -> None:
        self.db_path = Path(db_path)
        self.csv_path = Path(csv_path)
        self.topic_prefix = topic_prefix
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._estados: dict[str, EstadoDispositivo] = {}
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(esquema.ESQUEMA_SQL)
        self._conn.commit()

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

    def run_forever(self, cola: "queue.Queue[Any]") -> None:
        """Bucle del hilo writer: consume hasta recibir el centinela ``None``."""
        while True:
            item = cola.get()
            if item is None:
                cola.task_done()
                return
            topic, payload = item
            try:
                self.procesar(topic, payload)
            except Exception:  # pragma: no cover - red de seguridad del hilo
                LOG.exception("fallo procesando mensaje topic=%s", topic)
            finally:
                cola.task_done()

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
                topic, payload = item
                self.procesar(topic, payload)
                procesados += 1
            finally:
                cola.task_done()

    def dispositivos_conocidos(self, limite: int = 32) -> list[str]:
        """device_id vistos, para el poller de diagnosticos (tolerante a DB vacia)."""
        with self._lock:
            filas = self._conn.execute(
                "SELECT device_id FROM dispositivos ORDER BY last_seen_ms DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [fila[0] for fila in filas]

    def cerrar(self) -> None:
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
            client.subscribe(f"{cfg.topic_prefix}/+/{sufijo}", qos=1)
        LOG.info("MQTT conectado; suscrito a %s/+/{...}", cfg.topic_prefix)

    def on_disconnect(client, userdata, flags, reason_code, properties=None):
        LOG.warning("MQTT desconectado: rc=%s", reason_code)

    def on_message(client, userdata, msg):
        # Re-entrega QoS 1: se ignora para no duplicar eventos derivados.
        if getattr(msg, "dup", False):
            LOG.debug("mensaje dup ignorado topic=%s", msg.topic)
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
    writer = IngestaWriter(db_path, csv_path)
    cola: "queue.Queue[Any]" = queue.Queue(maxsize=COLA_MAX)

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
        cola.put(None)
        hilo_writer.join(timeout=5)
        writer.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())