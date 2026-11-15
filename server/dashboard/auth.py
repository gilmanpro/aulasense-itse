"""Autenticación del dashboard AulaSense — login, sesiones y lockout (SPEC §9.2, D-F25-8).

Alcance (F25-e)
---------------
* Loader de entorno: ``.env`` de la raíz (WiFi/MQTT/upload) + ``claves/.env``
  (credenciales de auth/IA, **prioridad**). Ver AGENTS regla 15 y D-F25-9.
* Hash de la password admin en formato **autodescriptivo**:
  - escritura por defecto ``scrypt$N$r$p$salt_b64$hash_b64`` (``hashlib.scrypt``,
    stdlib): el MISMO algoritmo en los dos runtimes del proyecto (WSL py3.13 y
    Windows py3.15, AGENTS regla 3), así un hash escrito en un runtime SIEMPRE
    verifica en el otro;
  - verificación de ``$argon2id$...`` cuando ``argon2-cffi`` está instalado, y
    escritura argon2id opt-in con ``AULASENSE_AUTH_HASH=argon2id`` (requiere
    argon2-cffi en AMBOS runtimes; está fijado en ``requirements.txt`` y
    ``requirements-linux.txt``).
* Sesiones (tabla ``sesiones`` o ``sesiones_auth`` según resuelva
  ``esquema.nombre_tabla_sesiones_login``, columnas de §8.2): en BD solo el **SHA-256**
  del token de cookie; expiración absoluta 12 h + inactividad 2 h (sliding).
* Lockout progresivo por par IP+usuario con ventana de 15 min: ≥5 fallos → 60 s,
  ≥10 → 15 min, ≥20 → 1 h. Temporal y auto-expirante (R-F25-3). Éxito resetea.
* ``asegurar_admin``: bootstrap idempotente del usuario admin único.

Nota de esquema (integración F25-d1): SPEC §8.2 llama ``sesiones`` a la tabla
de sesiones de auth, pero ``sesiones`` YA existe desde F3 (session logger de
captura, ``inicio_ms``/``estado``). El DDL canónico vive en
``server/ingesta/esquema.py`` y resuelve el nombre con
``nombre_tabla_sesiones_login``: ``sesiones`` si es libre o ya es la de login, y
**``sesiones_auth``** si F3 ocupa el nombre (caso real). Aquí solo hay un CREATE
IF NOT EXISTS local anti-carrera (patrón F3/F24-b2) con ESE MISMO nombre; se
prefiere ``sesiones_auth`` cuando ``sesiones`` no existe todavía, porque el
dashboard siempre arranca después de crear la tabla F3 (``_conectar``) y crear
``sesiones`` con el esquema de login rompería el session logger.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

# --- Rutas del proyecto -------------------------------------------------------
RAIZ = Path(__file__).resolve().parents[2]  # aulasense/
SERVER_DIR = RAIZ / "server"

# --- Contrato §9.2 ------------------------------------------------------------
NOMBRE_COOKIE = "aulasense_sesion"
MAX_EDAD_S = 43_200  # 12 h: expiración absoluta Y Max-Age de la cookie
EXPIRA_MS = 12 * 60 * 60 * 1000  # 12 h absolutas
IDLE_MS = 2 * 60 * 60 * 1000  # 2 h de inactividad (sliding)
VENTANA_LOCKOUT_MS = 15 * 60 * 1000  # ventana de conteo de fallos
PODA_INTENTOS_MS = 24 * 60 * 60 * 1000  # poda de intentos >24 h
NIVELES_BLOQUEO = ((20, 60 * 60 * 1000), (10, 15 * 60 * 1000), (5, 60 * 1000))

# Cabeceras de seguridad de las respuestas de auth (SPEC §9.2).
CABECERAS_AUTH = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

# Claves que NUNCA se exportan a ``os.environ`` (se leen del archivo al usarlas).
_NO_EXPORTAR = frozenset({"AULASENSE_ADMIN_PASSWORD"})

# --- Parámetros scrypt (stdlib, portables) ------------------------------------
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT_B = 16
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 64 * 1024 * 1024

TABLA_SESIONES_DEFECTO = "sesiones_auth"  # canónico de esquema.TABLA_SESIONES_LOGIN_ALT

# --- DDL local anti-carrera §8.2 (el canónico vive en ingesta/esquema.py) -----
USUARIOS_SQL = """
CREATE TABLE IF NOT EXISTS usuarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  pass_hash TEXT NOT NULL,
  creado_en TEXT NOT NULL DEFAULT (datetime('now')),
  ultimo_login_ms INTEGER
);
"""

INTENTOS_SQL = """
CREATE TABLE IF NOT EXISTS intentos_login (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ip TEXT NOT NULL, username TEXT NOT NULL,
  ts_ms INTEGER NOT NULL, exito INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_intentos_ip_user_ts ON intentos_login(ip, username, ts_ms);
"""


def _sql_sesiones(tabla: str) -> str:
    """DDL de sesiones de auth con el nombre ya resuelto (constante interna)."""
    return f"""
CREATE TABLE IF NOT EXISTS {tabla} (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES usuarios(id),
  creado_en_ms INTEGER NOT NULL,
  ultimo_uso_ms INTEGER NOT NULL,
  expira_en_ms INTEGER NOT NULL,
  ip TEXT, user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_sesiones_expira ON {tabla}(expira_en_ms);
"""


# ------------------------------------------------------------------------------
# Reloj (monkeypatcheable en tests)
# ------------------------------------------------------------------------------
def ahora_ms() -> int:
    """Época del PC en milisegundos (misma convención que el dashboard)."""
    return int(time.time() * 1000)


# ------------------------------------------------------------------------------
# Loader de entorno: .env raíz + claves/.env (prioridad para auth/IA)
# ------------------------------------------------------------------------------
def ruta_claves_env() -> Path:
    """Ruta de ``claves/.env`` (``AULASENSE_CLAVES_ENV`` la sobreescribe en tests)."""
    crudo = os.environ.get("AULASENSE_CLAVES_ENV", "").strip()
    if crudo:
        return Path(crudo).expanduser()
    return RAIZ / "claves" / ".env"


def ruta_env_raiz() -> Path:
    """Ruta del ``.env`` de la raíz (``AULASENSE_ENV_RAIZ`` la sobreescribe)."""
    crudo = os.environ.get("AULASENSE_ENV_RAIZ", "").strip()
    if crudo:
        return Path(crudo).expanduser()
    return RAIZ / ".env"


def _leer_archivo(ruta: Path) -> dict[str, str]:
    try:
        if not ruta.is_file():
            return {}
        return {k: v for k, v in dotenv_values(ruta).items() if v is not None}
    except OSError:  # permisos/formato: el dashboard debe arrancar igual
        return {}


def cargar_env(exportar: bool = True) -> dict[str, str]:
    """Fusiona ``claves/.env`` (gana) sobre el ``.env`` de la raíz.

    Con ``exportar`` rellena ``os.environ`` SOLO con claves ausentes: un valor
    ya presente en el proceso (o inyectado por monkeypatch en tests) manda. La
    password admin NO se exporta al entorno del proceso: solo se lee del archivo
    cuando hace falta (``valor_env``), para no ampliar su superficie.
    """
    fusion: dict[str, str] = dict(_leer_archivo(ruta_env_raiz()))
    fusion.update(_leer_archivo(ruta_claves_env()))
    if exportar:
        for clave, valor in fusion.items():
            if clave in _NO_EXPORTAR:
                continue
            os.environ.setdefault(clave, valor)
    return fusion


def valor_env(clave: str, defecto: str = "") -> str:
    """Prioridad: entorno del proceso → ``claves/.env`` → ``.env`` de la raíz."""
    del_proceso = os.environ.get(clave)
    if del_proceso not in (None, ""):
        return del_proceso
    valor = _leer_archivo(ruta_claves_env()).get(clave)
    if valor not in (None, ""):
        return valor
    valor = _leer_archivo(ruta_env_raiz()).get(clave)
    return valor if valor is not None and valor != "" else defecto


def usuario_admin() -> str:
    """Username del admin único (``AULASENSE_ADMIN_USER``, default ``admin``)."""
    return (valor_env("AULASENSE_ADMIN_USER", "admin") or "admin").strip() or "admin"


def escribir_password(password: str, usuario: str | None = None) -> Path:
    """Escribe la password admin en ``claves/.env`` (NUNCA la imprime).

    Preserva el resto de líneas; si el archivo no existe lo crea. Devuelve la
    ruta. Uso exclusivo del bootstrap por código y de ``tools/reset_admin.py``.
    """
    ruta = ruta_claves_env()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    lineas = ruta.read_text(encoding="utf-8").splitlines() if ruta.is_file() else []
    usuario = (usuario or usuario_admin()).strip() or "admin"
    nuevas: list[str] = []
    visto_user = visto_pass = False
    for linea in lineas:
        if linea.startswith("AULASENSE_ADMIN_USER="):
            nuevas.append(f"AULASENSE_ADMIN_USER={usuario}")
            visto_user = True
        elif linea.startswith("AULASENSE_ADMIN_PASSWORD="):
            nuevas.append(f"AULASENSE_ADMIN_PASSWORD={password}")
            visto_pass = True
        else:
            nuevas.append(linea)
    if not visto_user:
        nuevas.insert(0, f"AULASENSE_ADMIN_USER={usuario}")
    if not visto_pass:
        nuevas.append(f"AULASENSE_ADMIN_PASSWORD={password}")
    ruta.write_text("\n".join(nuevas) + "\n", encoding="utf-8")
    return ruta


# ------------------------------------------------------------------------------
# Hash de password: scrypt (stdlib, default multi-runtime) / argon2id (opcional)
# ------------------------------------------------------------------------------
def _b64(datos: bytes) -> str:
    return base64.b64encode(datos).decode("ascii")


def _de_b64(texto: str) -> bytes:
    return base64.b64decode(texto.encode("ascii"), validate=True)


def argon2_disponible() -> bool:
    """¿Está ``argon2-cffi`` instalado en este runtime?"""
    try:
        import argon2  # noqa: F401
    except Exception:  # ImportError o binario roto: se degrada a scrypt
        return False
    return True


def algoritmo_por_defecto() -> str:
    """Algoritmo de ESCRITURA: ``scrypt`` salvo ``AULASENSE_AUTH_HASH=argon2id``.

    El default scrypt es deliberado (coherencia multi-runtime, AGENTS regla 3):
    un hash ``scrypt$...`` se verifica en WSL py3.13 y en Windows py3.15 sin
    dependencias; ``argon2id`` exige argon2-cffi en ambos runtimes y solo se
    activa explícitamente.
    """
    preferido = (valor_env("AULASENSE_AUTH_HASH", "scrypt") or "scrypt").strip().lower()
    if preferido in ("argon2", "argon2id") and argon2_disponible():
        return "argon2id"
    return "scrypt"


def hash_password(password: str) -> str:
    """Devuelve el hash autodescriptivo de ``password``."""
    if algoritmo_por_defecto() == "argon2id":
        from argon2 import PasswordHasher

        return PasswordHasher().hash(password)
    sal = secrets.token_bytes(SCRYPT_SALT_B)
    derivada = hashlib.scrypt(
        password.encode("utf-8"),
        salt=sal,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
        maxmem=SCRYPT_MAXMEM,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(sal)}${_b64(derivada)}"


def _verificar_scrypt(password: str, almacenado: str) -> bool:
    try:
        etiqueta, n, r, p, sal_b64, hash_b64 = almacenado.split("$")
        if etiqueta != "scrypt":
            return False
        derivada = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_de_b64(sal_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_de_b64(hash_b64)) or SCRYPT_DKLEN,
            maxmem=SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(derivada, _de_b64(hash_b64))
    except (ValueError, TypeError, MemoryError):
        return False


def _verificar_argon2(password: str, almacenado: str) -> bool:
    if not argon2_disponible():
        return False
    from argon2 import PasswordHasher
    from argon2.exceptions import VerificationError, VerifyMismatchError

    try:
        return bool(PasswordHasher().verify(almacenado, password))
    except (VerifyMismatchError, VerificationError, ValueError, TypeError):
        return False


def verificar_password(password: str, almacenado: str | None) -> bool:
    """Verifica ``password`` contra un hash autodescriptivo (sin distinguir motivo)."""
    if not password or not almacenado:
        return False
    if almacenado.startswith("$argon2"):
        return _verificar_argon2(password, almacenado)
    if almacenado.startswith("scrypt$"):
        return _verificar_scrypt(password, almacenado)
    return False


# ------------------------------------------------------------------------------
# Esquema (CREATE IF NOT EXISTS local, patrón F3/F24-b2)
# ------------------------------------------------------------------------------
def tabla_sesiones(con: sqlite3.Connection) -> str:
    """Nombre de la tabla de sesiones de auth (canónico de ``ingesta/esquema.py``).

    Equivale a ``esquema.nombre_tabla_sesiones_login`` salvo cuando ``sesiones``
    NO existe aún: en ese caso se usa ``sesiones_auth`` (crear ``sesiones`` con el
    esquema de login rompería el session logger F3, que el dashboard garantiza
    antes en ``_conectar``).
    """
    fila = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sesiones'"
    ).fetchone()
    if fila is not None:
        columnas = {fila_col[1] for fila_col in con.execute("PRAGMA table_info(sesiones)")}
        if "token_hash" in columnas:
            return "sesiones"
    return TABLA_SESIONES_DEFECTO


def ensure_tablas(con: sqlite3.Connection) -> None:
    """Crea usuarios/intentos_login/sesiones_auth si faltan (idempotente)."""
    con.executescript(USUARIOS_SQL)
    con.executescript(INTENTOS_SQL)
    con.executescript(_sql_sesiones(tabla_sesiones(con)))
    con.commit()


# ------------------------------------------------------------------------------
# Sesiones
# ------------------------------------------------------------------------------
def hash_token(token: str) -> str:
    """SHA-256 hex del token de cookie (lo único que se guarda en BD)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def crear_sesion(
    con: sqlite3.Connection,
    user_id: int,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Crea la fila de sesión y devuelve el token EN CLARO (solo va a la cookie)."""
    token = secrets.token_urlsafe(32)
    ahora = ahora_ms()
    tabla = tabla_sesiones(con)
    con.execute(
        f"INSERT INTO {tabla} (token_hash, user_id, creado_en_ms, ultimo_uso_ms, "
        "expira_en_ms, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            hash_token(token),
            int(user_id),
            ahora,
            ahora,
            ahora + EXPIRA_MS,
            (ip or "")[:64] or None,
            (user_agent or "")[:256] or None,
        ),
    )
    con.commit()
    return token


def validar_sesion(con: sqlite3.Connection, token: str | None) -> dict[str, Any] | None:
    """Valida la sesión (absoluta + idle) y aplica el sliding de ``ultimo_uso_ms``.

    Expirada o inactiva ⇒ DELETE de la fila y ``None``.
    """
    if not token:
        return None
    token_hash = hash_token(token)
    tabla = tabla_sesiones(con)
    fila = con.execute(
        f"SELECT user_id, creado_en_ms, ultimo_uso_ms, expira_en_ms FROM {tabla} "
        "WHERE token_hash = ?",
        (token_hash,),
    ).fetchone()
    if fila is None:
        return None
    user_id, creado, ultimo_uso, expira = int(fila[0]), int(fila[1]), int(fila[2]), int(fila[3])
    ahora = ahora_ms()
    if ahora >= expira or ahora - ultimo_uso > IDLE_MS:
        con.execute(f"DELETE FROM {tabla} WHERE token_hash = ?", (token_hash,))
        con.commit()
        return None
    con.execute(
        f"UPDATE {tabla} SET ultimo_uso_ms = ? WHERE token_hash = ?", (ahora, token_hash)
    )
    con.commit()
    return {
        "token_hash": token_hash,
        "user_id": user_id,
        "creado_en_ms": creado,
        "expira_en_ms": expira,
    }


def borrar_sesion(con: sqlite3.Connection, token: str | None) -> bool:
    """Borra la sesión del token (logout). Devuelve True si existía."""
    if not token:
        return False
    cursor = con.execute(
        f"DELETE FROM {tabla_sesiones(con)} WHERE token_hash = ?", (hash_token(token),)
    )
    con.commit()
    return cursor.rowcount > 0


def borrar_sesiones_usuario(con: sqlite3.Connection, user_id: int) -> int:
    """Invalida todas las sesiones de un usuario (reset_admin)."""
    cursor = con.execute(
        f"DELETE FROM {tabla_sesiones(con)} WHERE user_id = ?", (int(user_id),)
    )
    con.commit()
    return cursor.rowcount


def limpiar_sesiones(con: sqlite3.Connection) -> int:
    """DELETE de sesiones expiradas o inactivas (al arrancar + oportunista)."""
    ahora = ahora_ms()
    cursor = con.execute(
        f"DELETE FROM {tabla_sesiones(con)} WHERE expira_en_ms <= ? OR ultimo_uso_ms < ?",
        (ahora, ahora - IDLE_MS),
    )
    con.commit()
    return cursor.rowcount


# ------------------------------------------------------------------------------
# Lockout por IP+usuario (ventana 15 min, degradación progresiva)
# ------------------------------------------------------------------------------
def _fallos_ventana(con: sqlite3.Connection, ip: str, username: str) -> list[int]:
    desde = ahora_ms() - VENTANA_LOCKOUT_MS
    filas = con.execute(
        "SELECT ts_ms FROM intentos_login WHERE ip = ? AND username = ? AND exito = 0 "
        "AND ts_ms >= ? ORDER BY ts_ms",
        (ip, username, desde),
    ).fetchall()
    return [int(fila[0]) for fila in filas]


def duracion_bloqueo_ms(fallos: list[int]) -> int:
    """Duración del bloqueo según nº de fallos dentro de la ventana."""
    for umbral, duracion in NIVELES_BLOQUEO:
        if len(fallos) >= umbral:
            return duracion
    return 0


def bloqueo_restante_ms(con: sqlite3.Connection, ip: str, username: str) -> int:
    """Milisegundos que faltan para que expire el lockout (0 = sin bloqueo)."""
    fallos = _fallos_ventana(con, ip, username)
    duracion = duracion_bloqueo_ms(fallos)
    if not duracion:
        return 0
    # El bloqueo se cuenta desde el último fallo registrado (temporal y auto-expirante).
    return max(0, fallos[-1] + duracion - ahora_ms())


def segundos_retry_after(restante_ms: int) -> int:
    """``Retry-After`` en segundos (mínimo 1 para no anunciar '0 s')."""
    return max(1, (int(restante_ms) + 999) // 1000)


def registrar_intento(
    con: sqlite3.Connection, ip: str, username: str, exito: bool
) -> None:
    """Registra el intento; un éxito borra los fallos del par (resetea el contador)."""
    ahora = ahora_ms()
    if exito:
        con.execute(
            "DELETE FROM intentos_login WHERE ip = ? AND username = ? AND exito = 0",
            (ip, username),
        )
    con.execute(
        "INSERT INTO intentos_login (ip, username, ts_ms, exito) VALUES (?, ?, ?, ?)",
        (ip, username, ahora, 1 if exito else 0),
    )
    if exito:
        con.execute("UPDATE usuarios SET ultimo_login_ms = ? WHERE username = ?", (ahora, username))
    con.commit()


def podar_intentos(con: sqlite3.Connection) -> int:
    """Poda ``intentos_login`` >24 h (la ventana de lockout es 15 min)."""
    cursor = con.execute("DELETE FROM intentos_login WHERE ts_ms < ?", (ahora_ms() - PODA_INTENTOS_MS,))
    con.commit()
    return cursor.rowcount


def limpiar_intentos_usuario(con: sqlite3.Connection, username: str) -> int:
    """Borra todos los intentos de un usuario (reset_admin)."""
    cursor = con.execute("DELETE FROM intentos_login WHERE username = ?", (username,))
    con.commit()
    return cursor.rowcount


# ------------------------------------------------------------------------------
# Usuarios / bootstrap
# ------------------------------------------------------------------------------
def obtener_usuario(con: sqlite3.Connection, username: str) -> dict[str, Any] | None:
    """Fila de ``usuarios`` por username (o ``None``)."""
    fila = con.execute(
        "SELECT id, username, pass_hash FROM usuarios WHERE username = ?", (username,)
    ).fetchone()
    if fila is None:
        return None
    return {"id": int(fila[0]), "username": fila[1], "pass_hash": fila[2]}


def asegurar_admin(con: sqlite3.Connection) -> None:
    """Bootstrap idempotente del admin único (§9.2).

    - Sin fila en ``usuarios``: crea el usuario con el hash de la password de
      ``claves/.env``; si no hay password, la GENERA (``secrets.token_hex(16)``)
      y la escribe en ``claves/.env``. El valor nunca se imprime.
    - Con fila: NO reescribe nada salvo que la password de ``claves/.env`` ya no
      verifique contra el hash (el dueño la cambió a mano ⇒ se resincroniza).
    """
    username = usuario_admin()
    password = valor_env("AULASENSE_ADMIN_PASSWORD").strip()
    existente = obtener_usuario(con, username)
    if existente is not None:
        if password and not verificar_password(password, existente["pass_hash"]):
            con.execute(
                "UPDATE usuarios SET pass_hash = ? WHERE id = ?",
                (hash_password(password), existente["id"]),
            )
            con.commit()
        return
    if not password:
        password = secrets.token_hex(16)  # generada por código (AGENTS regla 15)
        escribir_password(password, username)
    try:
        con.execute(
            "INSERT INTO usuarios (username, pass_hash) VALUES (?, ?)",
            (username, hash_password(password)),
        )
        con.commit()
    except sqlite3.IntegrityError:  # carrera con otro proceso que ya bootstrapeó
        con.rollback()


def preparar(con: sqlite3.Connection) -> None:
    """Arranque del dashboard: entorno, esquema de auth, limpieza y bootstrap."""
    cargar_env()
    ensure_tablas(con)
    limpiar_sesiones(con)
    podar_intentos(con)
    asegurar_admin(con)