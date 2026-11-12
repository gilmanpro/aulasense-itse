"""Receptor HTTP de imagenes de evidencia de AulaSense (SPEC §7.1, fase F24-b1).

Por que vive en la INGESTA y no en el dashboard: el dashboard esta atado a
``127.0.0.1`` (regla dura E7) y el nodo no alcanza loopback desde la LAN. Aqui se
levanta un listener HTTP minimo (``ThreadingHTTPServer`` stdlib) que valida el
upload y encola el job en la MISMA cola del writer unico (``IngestaWriter``): la
invariante de escritor unico a SQLite se conserva.

Contrato del endpoint ``POST /internal/imagen``:

- Auth: ``Authorization: Bearer <AULASENSE_UPLOAD_TOKEN>`` (comparacion en tiempo
  constante). Sin token configurado el receptor NO se levanta.
- Bind: ``AULASENSE_UPLOAD_HOST``:``AULASENSE_UPLOAD_PORT`` (default 8001). Host
  vacio = DESACTIVADO; se rechazan comodines (``0.0.0.0``/``::``): el listener es
  LAN explicita opt-in, nunca el default.
- Cuerpo: JPEG binario crudo o ``multipart/form-data`` con el campo ``imagen`` y
  los campos/cabeceras de metadata ``device_id``, ``ts_ms``, ``evento_id``,
  ``ia_persona``, ``ia_score`` (tambien por query string).
- Validaciones: magic ``FFD8FF`` (422), imagen <= 64 KB (413), ``device_id`` 16
  hex en la allowlist de ``dispositivos`` al arrancar (422), rate-limit 1/10 s por
  device en memoria (429). Errores como ``{"ok": false, "error": ...}`` sin secretos.
- Exito (201): ``{"ok": true, "imagen_id": N}``.

Almacenamiento y convencion COMPARTIDA con el dashboard (F24-b2):
``imagenes.ruta`` es RELATIVA al directorio de datos (p. ej.
``imagenes/<device>/<yyyymmdd_HHMMSS>_<sha256_8>.jpg``). El directorio base se
resuelve con ``AULASENSE_DATOS_DIR`` o, por defecto, el padre de la DB real
(``server/datos``). La fecha del nombre es la epoca derivada del reloj R3.

Origen de los datos (honestidad, AGENTS.md regla 10): este canal sirve AL NODO, asi
que las filas se escriben con ``origen='real'``. Para tests/fixtures sinteticos
existe el flag interno ``allow_sintetico`` (``--allow-sintetico`` en el ``main``
standalone), que fuerza ``origen='sintetico'`` en la fila: el binario de prueba
nunca se presenta como captura real.

Ejecucion standalone (modo test, sin broker MQTT)::

    server\\venv\\Scripts\\python.exe server/ingesta/receptor_imagen.py \\
        --db server\\datos\\aulasense.db --datos server\\datos --allow-sintetico
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import logging
import os
import queue
import re
import threading
import time
from datetime import datetime
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from dotenv import load_dotenv

try:  # ejecucion directa (server/ingesta en sys.path) o via conftest de tests
    from ingesta_mqtt import IngestaWriter, JobImagen
except ImportError:  # import como paquete server.ingesta
    from .ingesta_mqtt import IngestaWriter, JobImagen  # type: ignore[no-redef]

LOG = logging.getLogger("aulasense.receptor")

# Raiz del repo (server/ingesta/receptor_imagen.py -> aulasense/).
_RAIZ_REPO = Path(__file__).resolve().parents[2]
_DOTENV_DEFECTO = _RAIZ_REPO / ".env"
_DB_DEFECTO = _RAIZ_REPO / "server" / "datos" / "aulasense.db"

# Limites del contrato (SPEC §7.1).
MAX_IMAGEN_BYTES = 64 * 1024
# Margen para las cabeceras de un multipart chico alrededor del JPEG.
MARGEN_MULTIPART_BYTES = 4096
MAX_CUERPO_BYTES = MAX_IMAGEN_BYTES + MARGEN_MULTIPART_BYTES
# Drenaje acotado de un cuerpo rechazado por tamano (evita resetear la conexion
# a media subida); mas alla de esto se corta sin leer.
MAX_DRENAJE_BYTES = 1024 * 1024
RUTA_ENDPOINT = "/internal/imagen"
RATE_LIMITE_S = 10.0
ESPERA_WRITER_S = 10.0

_RE_HEX16 = re.compile(r"^[0-9a-f]{16}$")

# Metadatos aceptados por campo de multipart, cabecera o query string.
_CABECERAS_META: dict[str, str] = {
    "device_id": "X-Device-Id",
    "ts_ms": "X-Ts-Ms",
    "evento_id": "X-Evento-Id",
    "ia_persona": "X-Ia-Persona",
    "ia_score": "X-Ia-Score",
}


class _ErrorPeticion(Exception):
    """Error de protocolo con codigo HTTP asociado (mensaje sin secretos)."""

    def __init__(self, codigo: int, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


# --- Helpers puros ---------------------------------------------------------


def resolver_datos_dir(writer: IngestaWriter) -> Path:
    """DIR base de datos: env ``AULASENSE_DATOS_DIR`` o el padre de la DB real."""
    crudo = os.environ.get("AULASENSE_DATOS_DIR", "").strip()
    if crudo:
        return Path(crudo).expanduser()
    return Path(writer.db_path).parent


def _puerto_env() -> int:
    crudo = os.getenv("AULASENSE_UPLOAD_PORT", "8001").strip() or "8001"
    try:
        return int(crudo)
    except ValueError:
        return 8001


def _borrar_silencioso(ruta: Path) -> None:
    """Borra un archivo huerfano sin propagar errores (500 ya reportado)."""
    try:
        ruta.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - disco ocupado/permisos
        LOG.warning("no se pudo borrar archivo huerfano: %s", ruta)


def _dimensiones(datos: bytes) -> tuple[int | None, int | None]:
    """Ancho/alto si Pillow esta disponible; ``(None, None)`` es un valor valido."""
    try:
        from PIL import Image  # import perezoso: Pillow es opcional
    except ImportError:  # pragma: no cover - entorno sin Pillow
        return None, None
    try:
        with Image.open(io.BytesIO(datos)) as imagen:
            return int(imagen.width), int(imagen.height)
    except Exception:  # imagenes corruptas: la fila queda con null (permitido)
        return None, None


def _parsear_multipart(
    content_type: str, cuerpo: bytes
) -> tuple[bytes | None, dict[str, str]]:
    """Extrae el campo ``imagen`` y los campos de texto de un multipart/form-data."""
    mensaje = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: "
        + content_type.encode("latin-1", "replace")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + cuerpo
    )
    if not mensaje.is_multipart():
        return None, {}
    imagen: bytes | None = None
    campos: dict[str, str] = {}
    for parte in mensaje.iter_parts():
        nombre = parte.get_param("name", header="content-disposition") or ""
        contenido = parte.get_payload(decode=True) or b""
        if nombre == "imagen" or parte.get_content_maintype() == "image":
            imagen = contenido
        elif nombre:
            campos[nombre] = contenido.decode("utf-8", "replace").strip()
    return imagen, campos


def _fusionar_meta(
    cabeceras: Any, query: str, campos: dict[str, str]
) -> dict[str, str]:
    """Prioridad: campos multipart > cabeceras X-* > query string."""
    meta = dict(campos)
    for clave, cabecera in _CABECERAS_META.items():
        if clave in meta:
            continue
        valor = cabeceras.get(cabecera)
        if valor:
            meta[clave] = valor.strip()
    for clave, valores in parse_qs(query).items():
        if clave not in meta and valores:
            meta[clave] = valores[-1].strip()
    return meta


def _entero(meta: dict[str, str], clave: str, obligatorio: bool) -> int | None:
    crudo = meta.get(clave)
    if crudo is None or crudo == "":
        if obligatorio:
            raise _ErrorPeticion(422, f"falta metadata {clave}")
        return None
    try:
        return int(crudo)
    except ValueError:
        raise _ErrorPeticion(422, f"metadata {clave} no es entero") from None


# --- Receptor --------------------------------------------------------------


class ReceptorImagen:
    """Listener HTTP de uploads de evidencia JPEG con Bearer token (SPEC §7.1).

    Inyectable para tests: ``host='127.0.0.1'`` + ``port=0`` da un puerto efimero.
    El writer debe ser el MISMO ``IngestaWriter`` de la ingesta (o uno con su hilo
    ``run_forever`` activo): el receptor solo ENCOLA jobs, nunca escribe SQLite.
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        writer: IngestaWriter,
        prefix: str,
        datos_dir: str | Path,
        allow_sintetico: bool = False,
    ) -> None:
        self.host = (host or "").strip()
        self.port = int(port)
        self._token = (token or "").strip()
        self.writer = writer
        # ``prefix`` es el prefijo de topics del contrato (contexto/log); el canal
        # HTTP no usa MQTT.
        self.prefix = prefix
        self.datos_dir = Path(datos_dir)
        self.allow_sintetico = bool(allow_sintetico)
        self._lock = threading.Lock()
        self._ultimo_por_device: dict[str, float] = {}
        self._reloj = time.monotonic
        # Allowlist congelada al arrancar (SPEC §7.1: tabla dispositivos).
        self._allowlist: set[str] = set(writer.dispositivos_conocidos(1000))
        self._servidor: ThreadingHTTPServer | None = None

    # -- ciclo de vida ------------------------------------------------------

    def crear_servidor(self) -> ThreadingHTTPServer:
        """Valida configuracion y hace el bind (idempotente por instancia)."""
        if self._servidor is not None:
            return self._servidor
        if not self.host:
            raise ValueError("AULASENSE_UPLOAD_HOST vacio: receptor desactivado")
        if self.host in ("0.0.0.0", "::", "*"):
            raise ValueError("bind comodin prohibido: configura la IP LAN exacta")
        if not self._token:
            raise ValueError("AULASENSE_UPLOAD_TOKEN vacio: receptor desactivado")
        self.datos_dir.mkdir(parents=True, exist_ok=True)
        servidor = ThreadingHTTPServer(
            (self.host, self.port), _crear_handler(self)
        )
        servidor.daemon_threads = True
        self._servidor = servidor
        return servidor

    def iniciar_en_hilo(self) -> ThreadingHTTPServer:
        """Bind + ``serve_forever`` en un hilo daemon; devuelve el servidor."""
        servidor = self.crear_servidor()
        hilo = threading.Thread(
            target=servidor.serve_forever, name="receptor-imagen", daemon=True
        )
        hilo.start()
        return servidor

    def servir(self) -> None:
        """Bind + ``serve_forever`` bloqueante (``main`` standalone)."""
        self.crear_servidor().serve_forever()

    # -- auth / validacion --------------------------------------------------

    def autorizado(self, cabecera: str | None) -> bool:
        """``Authorization: Bearer <token>`` con comparacion en tiempo constante."""
        if not self._token or not cabecera:
            return False
        esquema, _, valor = cabecera.partition(" ")
        if esquema.lower() != "bearer" or not valor:
            return False
        return hmac.compare_digest(valor.strip(), self._token)

    def device_permitido(self, device_id: str) -> bool:
        """16 hex lowercase Y presente en la allowlist de ``dispositivos``.

        Camino rápido: snapshot de arranque. Si no está, se comprueba en vivo
        en la DB y se añade al snapshot: evita la carrera del primer arranque
        (en una DB nueva el `device` retained puede llegar DESPUÉS de construir
        el receptor y el nodo subir su primera imagen antes de reiniciar la
        ingesta → sin esto daría 422 spurio).
        """
        if _RE_HEX16.fullmatch(device_id) is None:
            return False
        if device_id in self._allowlist:
            return True
        if self.writer.dispositivo_conocido(device_id):
            self._allowlist.add(device_id)
            return True
        return False

    def consumir_rate(self, device_id: str) -> bool:
        """Rate-limit 1/10 s por device en memoria; ``False`` = 429."""
        ahora = self._reloj()
        with self._lock:
            ultimo = self._ultimo_por_device.get(device_id)
            if ultimo is not None and (ahora - ultimo) < RATE_LIMITE_S:
                return False
            self._ultimo_por_device[device_id] = ahora
        return True

    # -- persistencia -------------------------------------------------------

    def procesar_upload(
        self, device_id: str, ts_device_ms: int, imagen: bytes, meta: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        """Escribe el JPEG y encola el job; devuelve ``(codigo_http, json)``."""
        if not self.writer.activo:
            return 500, {"ok": False, "error": "writer no disponible"}

        evento_id = _entero(meta, "evento_id", obligatorio=False)
        if evento_id is not None and not self.writer.evento_existe(evento_id):
            return 422, {"ok": False, "error": "evento_id inexistente"}

        ia_persona: int | None = None
        crudo_persona = meta.get("ia_persona")
        if crudo_persona not in (None, ""):
            try:
                ia_persona = int(crudo_persona)
            except ValueError:
                return 422, {"ok": False, "error": "metadata ia_persona invalida"}
            if ia_persona not in (0, 1):
                return 422, {"ok": False, "error": "metadata ia_persona debe ser 0/1"}

        ia_score: float | None = None
        crudo_score = meta.get("ia_score")
        if crudo_score not in (None, ""):
            try:
                ia_score = float(crudo_score)
            except ValueError:
                return 422, {"ok": False, "error": "metadata ia_score invalida"}
            if not 0.0 <= ia_score <= 1.0:
                return 422, {"ok": False, "error": "metadata ia_score fuera de [0,1]"}

        # Rate-limit por device (1/10 s en memoria). Se consume SOLO cuando el
        # upload es valido, para que 401/413/422 no gasten cuota.
        if not self.consumir_rate(device_id):
            return 429, {"ok": False, "error": "rate-limit: 1 imagen/10 s por device"}

        sha = hashlib.sha256(imagen).hexdigest()
        epoca_ms, _sinc = self.writer.estimar_epoca_ms(device_id, ts_device_ms)
        sello = datetime.fromtimestamp(epoca_ms / 1000).strftime("%Y%m%d_%H%M%S")
        nombre = f"{sello}_{sha[:8]}.jpg"
        relativa = f"imagenes/{device_id}/{nombre}"
        destino = self.datos_dir / "imagenes" / device_id / nombre

        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(imagen)
        except OSError:
            LOG.exception("no se pudo escribir la imagen en datos/imagenes")
            return 500, {"ok": False, "error": "no se pudo guardar la imagen"}

        ancho, alto = _dimensiones(imagen)
        job = JobImagen(
            device_id=device_id,
            ts_device_ms=int(ts_device_ms),
            ruta=relativa,
            hash_sha256=sha,
            tamano_bytes=len(imagen),
            ancho=ancho,
            alto=alto,
            evento_id=evento_id,
            ia_persona=ia_persona,
            ia_score=ia_score,
            origen="sintetico" if self.allow_sintetico else "real",
        )
        if self.writer.cola.full():
            _borrar_silencioso(destino)
            return 500, {"ok": False, "error": "cola llena: imagen descartada"}
        try:
            self.writer.cola.put_nowait(job)
        except queue.Full:  # pragma: no cover - carrera con el pre-chequeo
            _borrar_silencioso(destino)
            return 500, {"ok": False, "error": "cola llena: imagen descartada"}

        # Espera acotada: el 201 necesita el id y evita archivos huerfanos si el
        # writer murio entre el pre-chequeo y el encolado.
        if not job.resultado.wait(ESPERA_WRITER_S):
            _borrar_silencioso(destino)
            LOG.error("writer sin respuesta para imagen device=%s", device_id)
            return 500, {"ok": False, "error": "writer no responde"}
        if job.error is not None or job.imagen_id is None:
            _borrar_silencioso(destino)
            if job.error == "evento_id inexistente":
                return 422, {"ok": False, "error": job.error}
            return 500, {"ok": False, "error": "no se pudo persistir la imagen"}
        return 201, {"ok": True, "imagen_id": int(job.imagen_id)}


# --- Handler HTTP ----------------------------------------------------------


def _responder_json(handler: BaseHTTPRequestHandler, codigo: int, datos: dict) -> None:
    """Respuesta JSON minimo (sin secretos) y cierre limpio."""
    cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(codigo)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(cuerpo)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.wfile.write(cuerpo)
    except (BrokenPipeError, ConnectionResetError):  # pragma: no cover - cliente corto
        handler.close_connection = True


def _crear_handler(receptor: ReceptorImagen) -> type[BaseHTTPRequestHandler]:
    """Handler ligado a una instancia concreta del receptor (sin globales)."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "AulaSenseUpload/1.0"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, formato: str, *args: Any) -> None:  # noqa: A003
            LOG.info("receptor %s - %s", self.address_string(), formato % args)

        def do_GET(self) -> None:  # noqa: N802 (API de BaseHTTPRequestHandler)
            _responder_json(self, 405, {"ok": False, "error": "metodo no permitido"})

        def do_PUT(self) -> None:  # noqa: N802
            _responder_json(self, 405, {"ok": False, "error": "metodo no permitido"})

        def do_DELETE(self) -> None:  # noqa: N802
            _responder_json(self, 405, {"ok": False, "error": "metodo no permitido"})

        def do_POST(self) -> None:  # noqa: N802
            partes = urlsplit(self.path)
            if partes.path != RUTA_ENDPOINT:
                _responder_json(self, 404, {"ok": False, "error": "ruta inexistente"})
                return
            if not receptor.autorizado(self.headers.get("Authorization")):
                # Se cierra la conexion: el cuerpo del request queda sin leer y no
                # debe interpretarse como la linea de una peticion siguiente.
                self.close_connection = True
                _responder_json(self, 401, {"ok": False, "error": "token invalido"})
                return
            try:
                cuerpo = self._leer_cuerpo()
                imagen, meta = self._extraer(cuerpo, partes.query)
                self._validar_imagen(imagen)
                device_id, ts_device_ms = self._validar_meta(meta)
            except _ErrorPeticion as exc:
                _responder_json(self, exc.codigo, {"ok": False, "error": exc.mensaje})
                return

            codigo, respuesta = receptor.procesar_upload(
                device_id, ts_device_ms, imagen, meta
            )
            _responder_json(self, codigo, respuesta)

        # -- cuerpo / metadata ---------------------------------------------

        def _leer_cuerpo(self) -> bytes:
            crudo = self.headers.get("Content-Length")
            if crudo is None:
                self.close_connection = True
                raise _ErrorPeticion(411, "falta Content-Length")
            try:
                longitud = int(crudo)
            except ValueError:
                self.close_connection = True
                raise _ErrorPeticion(411, "Content-Length invalido") from None
            if longitud <= 0:
                self.close_connection = True
                raise _ErrorPeticion(422, "cuerpo vacio")
            if longitud > MAX_CUERPO_BYTES:
                # "Leer Content-Length y ademas cortar": se drena un maximo acotado
                # para no dejar la conexion a medias y se rechaza con 413.
                self.close_connection = True
                if longitud <= MAX_DRENAJE_BYTES:
                    self.rfile.read(longitud)
                raise _ErrorPeticion(413, "imagen supera 64 KB")
            return self.rfile.read(longitud)

        def _extraer(self, cuerpo: bytes, query: str) -> tuple[bytes, dict[str, str]]:
            content_type = (self.headers.get("Content-Type") or "").strip()
            if content_type.lower().startswith("multipart/form-data"):
                imagen, campos = _parsear_multipart(content_type, cuerpo)
                if imagen is None:
                    raise _ErrorPeticion(422, "multipart sin campo 'imagen'")
                return imagen, _fusionar_meta(self.headers, query, campos)
            return cuerpo, _fusionar_meta(self.headers, query, {})

        @staticmethod
        def _validar_imagen(imagen: bytes) -> None:
            if not imagen.startswith(b"\xff\xd8\xff"):
                raise _ErrorPeticion(422, "el cuerpo no es un JPEG (magic FFD8FF)")
            if len(imagen) > MAX_IMAGEN_BYTES:
                raise _ErrorPeticion(413, "imagen supera 64 KB")

        @staticmethod
        def _validar_meta(meta: dict[str, str]) -> tuple[str, int]:
            device_id = (meta.get("device_id") or "").strip()
            if not receptor.device_permitido(device_id):
                raise _ErrorPeticion(422, "device_id desconocido o malformado")
            ts_device_ms = _entero(meta, "ts_ms", obligatorio=True)
            assert ts_device_ms is not None
            if ts_device_ms < 0:
                raise _ErrorPeticion(422, "metadata ts_ms no puede ser negativa")
            return device_id, ts_device_ms

    return Handler


def iniciar_receptor_desde_env(writer: IngestaWriter) -> ThreadingHTTPServer | None:
    """Arranca el receptor con la configuracion del entorno; ``None`` si esta off.

    Se invoca desde ``ingesta_mqtt.main`` (hilo dentro del proceso de ingesta) y
    carga el `.env` ya lo habra hecho ``MqttConfig.from_env`` en ese flujo.
    """
    host = os.getenv("AULASENSE_UPLOAD_HOST", "").strip()
    if not host:
        LOG.info("AULASENSE_UPLOAD_HOST vacio: receptor de imagen DESACTIVADO")
        return None
    token = os.getenv("AULASENSE_UPLOAD_TOKEN", "").strip()
    if not token:
        LOG.warning(
            "AULASENSE_UPLOAD_TOKEN vacio: receptor de imagen DESACTIVADO "
            "(genera el token con tools/gen_firmware_config.py)"
        )
        return None
    puerto = _puerto_env()
    try:
        receptor = ReceptorImagen(
            host, puerto, token, writer, writer.topic_prefix, resolver_datos_dir(writer)
        )
        servidor = receptor.iniciar_en_hilo()
    except (OSError, ValueError):
        LOG.exception("no se pudo levantar el receptor de imagen en %s:%s", host, puerto)
        return None
    LOG.info(
        "receptor de imagen escuchando en %s:%s (token presente len=%d, oculto)",
        host,
        puerto,
        len(token),
    )
    return servidor


# --- CLI standalone (modo test / sin broker) ------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Receptor HTTP de imagenes de evidencia (modo test standalone, sin broker)"
    )
    ap.add_argument(
        "--db",
        default=None,
        help="ruta de la SQLite (default: env AULASENSE_DB o server/datos/aulasense.db)",
    )
    ap.add_argument(
        "--datos",
        default=None,
        help="dir base de datos (default: env AULASENSE_DATOS_DIR o el padre de la DB)",
    )
    ap.add_argument("--host", default=None, help="IP LAN de bind (default env AULASENSE_UPLOAD_HOST)")
    ap.add_argument("--port", default=None, help="puerto (default env AULASENSE_UPLOAD_PORT o 8001)")
    ap.add_argument(
        "--token-env",
        default="AULASENSE_UPLOAD_TOKEN",
        help="nombre de la variable de entorno que contiene el Bearer token",
    )
    ap.add_argument("--env", default=str(_DOTENV_DEFECTO), help="ruta del .env a leer")
    ap.add_argument(
        "--allow-sintetico",
        action="store_true",
        help="modo test: fuerza origen='sintetico' en las filas subidas",
    )
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    load_dotenv(Path(args.env))

    host = (args.host or os.getenv("AULASENSE_UPLOAD_HOST", "")).strip()
    if not host:
        LOG.error("AULASENSE_UPLOAD_HOST vacio: receptor DESACTIVADO")
        return 2
    if host in ("0.0.0.0", "::", "*"):
        LOG.error("bind comodin prohibido: configura la IP LAN exacta")
        return 2
    token = os.getenv(args.token_env, "").strip()
    if not token:
        LOG.warning(
            "token vacio en %s: receptor DESACTIVADO (usa tools/gen_firmware_config.py)",
            args.token_env,
        )
        return 2
    try:
        puerto = int(args.port or os.getenv("AULASENSE_UPLOAD_PORT", "8001"))
    except ValueError:
        puerto = 8001

    db_path = Path(args.db or os.getenv("AULASENSE_DB") or _DB_DEFECTO)
    writer = IngestaWriter(db_path, db_path.parent / "export_evaluaciones.csv")
    datos_dir = Path(args.datos) if args.datos else resolver_datos_dir(writer)

    hilo_writer = threading.Thread(
        target=writer.run_forever, args=(writer.cola,), name="sqlite-writer", daemon=True
    )
    hilo_writer.start()

    receptor = ReceptorImagen(
        host,
        puerto,
        token,
        writer,
        writer.topic_prefix,
        datos_dir,
        allow_sintetico=args.allow_sintetico,
    )
    try:
        servidor = receptor.crear_servidor()
    except (OSError, ValueError):
        LOG.exception("no se pudo levantar el receptor en %s:%s", host, puerto)
        writer.cola.put(None)
        hilo_writer.join(timeout=5)
        writer.cerrar()
        return 1

    LOG.info(
        "receptor standalone en %s:%s (token presente len=%d, oculto) datos=%s origen=%s",
        host,
        puerto,
        len(token),
        datos_dir,
        "sintetico" if args.allow_sintetico else "real",
    )
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        LOG.info("parada solicitada (Ctrl+C)")
    finally:
        servidor.shutdown()
        servidor.server_close()
        writer.cola.put(None)
        hilo_writer.join(timeout=5)
        writer.cerrar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())