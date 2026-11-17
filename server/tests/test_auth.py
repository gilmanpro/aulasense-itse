"""Tests del login seguro del dashboard AulaSense (SPEC §9.2, D-F25-8 — F25-e).

Cobertura (checklist F25-e): login OK con flags exactas de cookie, 401 genérico
(password mala y usuario inexistente indistinguibles), lockout progresivo
5/10/20 → 429 + Retry-After 60 s/15 min/1 h por IP+usuario, éxito que resetea el
contador, expiración absoluta 12 h, inactividad 2 h (sliding), logout que invalida
la sesión en BD y limpia la cookie, rutas protegidas sin sesión, exenciones
(/login, estáticos de login, /internal/* con Origin guard), Origin cruzado en
POST /login y /logout → 403, WS sin cookie rechazado, ``tools/reset_admin.py``
(regenera + limpia, nunca imprime), hash autodescriptivo y bootstrap idempotente.

Las credenciales de prueba las GENERA el código (``secrets.token_hex(16)``) y el
``claves/.env`` real del repo nunca se lee ni se escribe (conftest autousado).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
RAIZ = SERVER_DIR.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from dashboard import auth  # noqa: E402

DEVID = "0123456789abcdef"
MOTION_VALIDO = {
    "kind": "motion",
    "device_id": DEVID,
    "payload": {"timestamp_ms": 1000, "state": "motion", "score": 0.87},
}
IP_PRUEBA = "testclient"  # host del ``request.client`` del TestClient de Starlette


# ------------------------------------------------------------------------------
# Fixtures y ayudas
# ------------------------------------------------------------------------------
@pytest.fixture()
def cliente(tmp_path, monkeypatch, credenciales_tmp):
    """TestClient SIN sesión (para probar login/lockout desde cero)."""
    monkeypatch.setenv("AULASENSE_DB", str(tmp_path / "auth.db"))
    monkeypatch.delenv("AULASENSE_ORIGINS_EXTRA", raising=False)

    from fastapi.testclient import TestClient

    from dashboard.app import app

    with TestClient(app) as test_client:
        yield test_client


def _con() -> sqlite3.Connection:
    """Conexión de inspección a la DB temporal del test."""
    con = sqlite3.connect(os.environ["AULASENSE_DB"])
    con.row_factory = sqlite3.Row
    return con


def _ahora_ms() -> int:
    return int(time.time() * 1000)


def _insertar_fallos(n: int, ip: str = IP_PRUEBA, usuario: str = "admin") -> None:
    """Inserta ``n`` fallos recientes (sin gastar scrypt: lockout puro de BD)."""
    ahora = _ahora_ms()
    con = _con()
    try:
        con.executemany(
            "INSERT INTO intentos_login (ip, username, ts_ms, exito) VALUES (?, ?, ?, 0)",
            [(ip, usuario, ahora - i * 100) for i in range(n)],
        )
        con.commit()
    finally:
        con.close()


def _login(cliente, password: str, usuario: str = "admin"):
    return cliente.post("/login", json={"username": usuario, "password": password})


def _tabla_sesiones() -> str:
    """Nombre real de la tabla de sesiones de auth (``sesiones_auth`` con F3 vivo)."""
    con = _con()
    try:
        return auth.tabla_sesiones(con)
    finally:
        con.close()


def _sesiones() -> list[sqlite3.Row]:
    con = _con()
    try:
        return list(con.execute(f"SELECT * FROM {_tabla_sesiones()}"))
    finally:
        con.close()


# ------------------------------------------------------------------------------
# 1-3. Login correcto, 401 genérico y usuario inexistente
# ------------------------------------------------------------------------------
def test_login_ok_cookie_flags_y_bd_solo_hash(cliente, credenciales_tmp):
    respuesta = _login(cliente, credenciales_tmp["password"])
    assert respuesta.status_code == 200
    assert respuesta.json() == {"ok": True, "usuario": "admin"}

    cabecera = respuesta.headers["set-cookie"]
    minusculas = cabecera.lower()
    assert cabecera.startswith(f"{auth.NOMBRE_COOKIE}=")
    assert "httponly" in minusculas
    assert "samesite=lax" in minusculas
    assert "path=/" in minusculas
    assert "max-age=43200" in minusculas
    assert "secure" not in minusculas  # omitido CON justificación (http loopback)
    # Cabeceras de seguridad de las respuestas de auth (SPEC §9.2).
    assert respuesta.headers["x-content-type-options"] == "nosniff"
    assert respuesta.headers["x-frame-options"] == "DENY"
    assert respuesta.headers["referrer-policy"] == "no-referrer"

    token = respuesta.cookies[auth.NOMBRE_COOKIE]
    assert len(token) >= 32
    filas = _sesiones()
    assert len(filas) == 1
    # En BD solo el SHA-256 del token: el token en claro NUNCA se persiste.
    assert filas[0]["token_hash"] == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert token not in (filas[0]["token_hash"],)

    # La cookie da acceso a la API.
    assert cliente.get("/api/resumen").status_code == 200


def test_password_mala_401_generico_sin_pista(cliente, credenciales_tmp):
    respuesta = _login(cliente, "password-incorrecta")
    assert respuesta.status_code == 401
    assert respuesta.json() == {"detail": "credenciales inválidas"}
    # Sin pista del motivo ni del usuario válido en el cuerpo.
    assert "admin" not in respuesta.text
    assert auth.NOMBRE_COOKIE not in respuesta.headers.get("set-cookie", "")
    assert _sesiones() == []


def test_usuario_inexistente_mismo_401(cliente, credenciales_tmp):
    mala = _login(cliente, "password-incorrecta")
    inexistente = _login(cliente, "password-incorrecta", usuario="fantasma")
    assert inexistente.status_code == 401
    # Misma respuesta exacta ⇒ no se puede enumerar usuarios.
    assert inexistente.json() == mala.json()
    assert inexistente.headers["content-type"] == mala.headers["content-type"]
    assert _sesiones() == []


# ------------------------------------------------------------------------------
# 4-7. Lockout progresivo 5/10/20 y granularidad por IP+usuario
# ------------------------------------------------------------------------------
def test_lockout_5_fallos_429_retry_after_60s(cliente, credenciales_tmp):
    # 5 intentos fallidos REALES (end-to-end por POST /login).
    for _ in range(5):
        assert _login(cliente, "password-incorrecta").status_code == 401
    bloqueado = _login(cliente, credenciales_tmp["password"])
    assert bloqueado.status_code == 429
    assert 0 < int(bloqueado.headers["retry-after"]) <= 60
    # El lockout protege incluso con la password correcta (temporal, auto-expira).
    assert _sesiones() == []


def test_lockout_10_fallos_retry_after_15min(cliente, credenciales_tmp):
    _insertar_fallos(10)
    bloqueado = _login(cliente, credenciales_tmp["password"])
    assert bloqueado.status_code == 429
    assert 890 <= int(bloqueado.headers["retry-after"]) <= 900


def test_lockout_20_fallos_retry_after_1h(cliente, credenciales_tmp):
    _insertar_fallos(20)
    bloqueado = _login(cliente, credenciales_tmp["password"])
    assert bloqueado.status_code == 429
    assert 3590 <= int(bloqueado.headers["retry-after"]) <= 3600


def test_lockout_es_por_ip_y_usuario(cliente, credenciales_tmp):
    # Fallos de OTRA IP y de OTRO usuario no bloquean al par (testclient, admin).
    _insertar_fallos(20, ip="10.1.2.3")
    _insertar_fallos(20, usuario="otro")
    assert _login(cliente, credenciales_tmp["password"]).status_code == 200


def test_lockout_caduca_fuera_de_la_ventana(cliente, credenciales_tmp):
    # Fallos de hace 20 min (fuera de la ventana de 15 min) no bloquean.
    con = _con()
    try:
        con.executemany(
            "INSERT INTO intentos_login (ip, username, ts_ms, exito) VALUES (?, ?, ?, 0)",
            [(IP_PRUEBA, "admin", _ahora_ms() - 20 * 60 * 1000 - i) for i in range(20)],
        )
        con.commit()
    finally:
        con.close()
    assert _login(cliente, credenciales_tmp["password"]).status_code == 200


def test_exito_resetea_contador_de_fallos(cliente, credenciales_tmp):
    for _ in range(4):
        assert _login(cliente, "password-incorrecta").status_code == 401
    con = _con()
    antes = con.execute(
        "SELECT COUNT(*) FROM intentos_login WHERE ip = ? AND username = 'admin' AND exito = 0",
        (IP_PRUEBA,),
    ).fetchone()[0]
    con.close()
    assert antes == 4

    assert _login(cliente, credenciales_tmp["password"]).status_code == 200
    con = _con()
    despues = con.execute(
        "SELECT COUNT(*) FROM intentos_login WHERE ip = ? AND username = 'admin' AND exito = 0",
        (IP_PRUEBA,),
    ).fetchone()[0]
    con.close()
    assert despues == 0  # el éxito limpia el contador del par IP+usuario


# ------------------------------------------------------------------------------
# 8-10. Expiración absoluta, idle sliding y logout
# ------------------------------------------------------------------------------
def test_sesion_expira_absoluta_12h(cliente, credenciales_tmp):
    assert _login(cliente, credenciales_tmp["password"]).status_code == 200
    con = _con()
    con.execute(
        f"UPDATE {_tabla_sesiones()} SET expira_en_ms = ?", (_ahora_ms() - 1,)
    )
    con.commit()
    con.close()

    assert cliente.get("/api/resumen").status_code == 401
    assert _sesiones() == []  # la fila expirada se borra


def test_sesion_expira_por_inactividad_2h(cliente, credenciales_tmp):
    assert _login(cliente, credenciales_tmp["password"]).status_code == 200
    con = _con()
    con.execute(
        f"UPDATE {_tabla_sesiones()} SET ultimo_uso_ms = ?",
        (_ahora_ms() - 3 * 60 * 60 * 1000,),
    )
    con.commit()
    con.close()

    assert cliente.get("/api/resumen").status_code == 401
    assert _sesiones() == []


def test_sliding_idle_actualiza_ultimo_uso(cliente, credenciales_tmp):
    assert _login(cliente, credenciales_tmp["password"]).status_code == 200
    hace_1h = _ahora_ms() - 60 * 60 * 1000
    con = _con()
    con.execute(f"UPDATE {_tabla_sesiones()} SET ultimo_uso_ms = ?", (hace_1h,))
    con.commit()
    con.close()

    assert cliente.get("/api/resumen").status_code == 200  # 1 h < idle 2 h
    filas = _sesiones()
    assert len(filas) == 1
    assert filas[0]["ultimo_uso_ms"] >= _ahora_ms() - 5_000  # sliding aplicado


def test_logout_invalida_sesion_y_limpia_cookie(cliente, credenciales_tmp):
    assert _login(cliente, credenciales_tmp["password"]).status_code == 200
    assert len(_sesiones()) == 1

    salida = cliente.post("/logout")
    assert salida.status_code == 200
    cabecera = salida.headers["set-cookie"].lower()
    assert cabecera.startswith(f"{auth.NOMBRE_COOKIE}=")
    assert "max-age=0" in cabecera  # cookie borrada

    assert _sesiones() == []  # borrada también en BD
    assert cliente.get("/api/resumen").status_code == 401


def test_logout_origin_cruzado_403(cliente, credenciales_tmp):
    assert _login(cliente, credenciales_tmp["password"]).status_code == 200
    cruzado = cliente.post("/logout", headers={"origin": "http://evil.example"})
    assert cruzado.status_code == 403
    assert len(_sesiones()) == 1  # la sesión NO se cerró


# ------------------------------------------------------------------------------
# 11-14. Cobertura del middleware y exenciones
# ------------------------------------------------------------------------------
def test_rutas_protegidas_sin_sesion(cliente):
    portada = cliente.get("/", follow_redirects=False)
    assert portada.status_code == 302
    assert portada.headers["location"] == "/login"

    for ruta in ("/api/resumen", "/api/imagenes", "/static/app.js", "/static/index.html", "/docs"):
        respuesta = cliente.get(ruta)
        assert respuesta.status_code == 401, f"{ruta} debería exigir sesión"


def test_login_y_estaticos_de_login_sin_sesion(cliente):
    pagina = cliente.get("/login")
    assert pagina.status_code == 200
    assert "text/html" in pagina.headers["content-type"]
    assert pagina.headers["x-content-type-options"] == "nosniff"
    assert pagina.headers["x-frame-options"] == "DENY"
    assert pagina.headers["referrer-policy"] == "no-referrer"
    # Página text-only: ni datos del sistema ni CDNs.
    assert "dispositivos" not in pagina.text
    assert "http://" not in pagina.text.replace("http://127.0.0.1", "")

    estatico = cliente.get("/static/login.html")
    assert estatico.status_code == 200

    # Sin sesión la portada sigue redirigiendo al login (exención ≠ acceso).
    assert cliente.get("/", follow_redirects=False).status_code == 302


def test_internal_evento_sin_sesion_ok_y_origin_guard_vigente(cliente):
    # /internal/* exento de sesión (máquina-a-máquina local) pero con Origin guard.
    assert cliente.post("/internal/evento", json=MOTION_VALIDO).status_code == 201
    cruzado = cliente.post(
        "/internal/evento", json=MOTION_VALIDO, headers={"origin": "http://evil.example"}
    )
    assert cruzado.status_code == 403


def test_origin_cruzado_en_login_403(cliente, credenciales_tmp):
    respuesta = cliente.post(
        "/login",
        json={"username": "admin", "password": credenciales_tmp["password"]},
        headers={"origin": "http://evil.example"},
    )
    assert respuesta.status_code == 403
    assert _sesiones() == []
    assert cliente.get("/api/resumen").status_code == 401


# ------------------------------------------------------------------------------
# 15-16. WebSocket y bootstrap
# ------------------------------------------------------------------------------
def test_ws_sin_cookie_rechazado(cliente, credenciales_tmp):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with cliente.websocket_connect(
            "/ws", headers={"origin": "http://127.0.0.1:8000"}
        ) as ws:
            ws.receive_json()

    # Con sesión REAL el handshake se acepta y llega el hello.
    assert _login(cliente, credenciales_tmp["password"]).status_code == 200
    with cliente.websocket_connect(
        "/ws", headers={"origin": "http://127.0.0.1:8000"}
    ) as ws:
        assert ws.receive_json()["type"] == "hello"


def test_bootstrap_primer_arranque_e_idempotente(tmp_path, monkeypatch, credenciales_tmp):
    monkeypatch.setenv("AULASENSE_DB", str(tmp_path / "bootstrap.db"))
    from fastapi.testclient import TestClient

    from dashboard.app import app

    with TestClient(app) as test_client:
        con = _con()
        filas = list(con.execute("SELECT username, pass_hash FROM usuarios"))
        con.close()
        assert len(filas) == 1
        assert filas[0]["username"] == "admin"
        assert filas[0]["pass_hash"].startswith("scrypt$")
        assert auth.verificar_password(credenciales_tmp["password"], filas[0]["pass_hash"])
        assert _login(test_client, credenciales_tmp["password"]).status_code == 200

    hash_inicial = filas[0]["pass_hash"]
    # Segundo arranque sobre la MISMA DB: no duplica ni reescribe el hash.
    with TestClient(app) as test_client:
        con = _con()
        filas = list(con.execute("SELECT username, pass_hash FROM usuarios"))
        con.close()
        assert len(filas) == 1
        assert filas[0]["pass_hash"] == hash_inicial


def test_tablas_auth_no_rompen_sesiones_f3(cliente, credenciales_tmp):
    # F3 ya ocupa ``sesiones`` (session logger): auth usa ``sesiones_auth``
    # (nombre canónico resuelto por ``ingesta/esquema.nombre_tabla_sesiones_login``).
    con = _con()
    tablas = {
        fila[0]
        for fila in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    columnas_f3 = {fila[1] for fila in con.execute("PRAGMA table_info(sesiones)")}
    con.close()
    assert "sesiones_auth" in tablas
    assert "usuarios" in tablas and "intentos_login" in tablas
    assert "token_hash" not in columnas_f3  # F3 intacta

    assert _login(cliente, credenciales_tmp["password"]).status_code == 200
    assert cliente.get("/api/sesiones").status_code == 200


# ------------------------------------------------------------------------------
# 17. Hash autodescriptivo (scrypt default multi-runtime + argon2id opcional)
# ------------------------------------------------------------------------------
def test_hash_scrypt_autodescriptivo_multi_runtime(monkeypatch):
    password = "clave-de-prueba-generada"
    almacenado = auth.hash_password(password)
    partes = almacenado.split("$")
    assert partes[0] == "scrypt"
    assert partes[1:4] == [str(auth.SCRYPT_N), str(auth.SCRYPT_R), str(auth.SCRYPT_P)]
    assert len(partes) == 6  # scrypt$N$r$p$salt$hash
    assert auth.verificar_password(password, almacenado)
    assert not auth.verificar_password("otra-clave", almacenado)
    # Formato desconocido/corrupto ⇒ False (nunca excepción).
    assert not auth.verificar_password(password, "md5$deadbeef")
    assert not auth.verificar_password(password, "scrypt$16414$8$1$no-base64$no-base64")
    assert not auth.verificar_password("", almacenado)
    assert not auth.verificar_password(password, None)


def test_hash_argon2id_si_esta_disponible(monkeypatch):
    if not auth.argon2_disponible():
        pytest.skip("argon2-cffi no instalado en este runtime: se usa scrypt")
    monkeypatch.setenv("AULASENSE_AUTH_HASH", "argon2id")
    assert auth.algoritmo_por_defecto() == "argon2id"
    almacenado = auth.hash_password("clave-de-prueba-generada")
    assert almacenado.startswith("$argon2id$")
    assert auth.verificar_password("clave-de-prueba-generada", almacenado)
    assert not auth.verificar_password("incorrecta", almacenado)


def test_cargar_env_no_exporta_la_password(monkeypatch, credenciales_tmp):
    monkeypatch.delenv("AULASENSE_ADMIN_PASSWORD", raising=False)
    fusion = auth.cargar_env()
    # Se lee del archivo (fusion) pero NO se copia al entorno del proceso.
    assert fusion.get("AULASENSE_ADMIN_PASSWORD") == credenciales_tmp["password"]
    assert os.environ.get("AULASENSE_ADMIN_PASSWORD") is None
    assert auth.valor_env("AULASENSE_ADMIN_PASSWORD") == credenciales_tmp["password"]


# ------------------------------------------------------------------------------
# 18. reset_admin.py (CLI local): regenera + limpia y NUNCA imprime la password
# ------------------------------------------------------------------------------
def test_reset_admin_regenera_limpia_y_no_imprime(cliente, credenciales_tmp, monkeypatch):
    password_vieja = credenciales_tmp["password"]
    assert _login(cliente, password_vieja).status_code == 200
    _insertar_fallos(7)  # lockout pendiente de 60 s
    assert len(_sesiones()) == 1

    entorno = dict(os.environ)
    proceso = subprocess.run(
        [sys.executable, str(RAIZ / "tools" / "reset_admin.py"), "--usuario", "admin"],
        capture_output=True,
        text=True,
        env=entorno,
        timeout=120,
        cwd=str(RAIZ),
    )
    salida = proceso.stdout + proceso.stderr
    assert proceso.returncode == 0, salida
    assert "regenerada en" in salida
    assert "longitud 32" in salida

    # El valor NUNCA se imprime. F26 (SPEC 16.8): reset_admin escribe en el .env
    # UNICO de la raiz (seam aislado por conftest via AULASENSE_ENV_RAIZ).
    from pathlib import Path as _P
    nuevo = _P(os.environ["AULASENSE_ENV_RAIZ"]).read_text(encoding="utf-8")
    assert "AULASENSE_ADMIN_PASSWORD" in nuevo
    assert f"AULASENSE_ADMIN_PASSWORD={password_vieja}" not in nuevo
    password_nueva = nuevo.split("AULASENSE_ADMIN_PASSWORD=", 1)[1].splitlines()[0].strip()
    assert len(password_nueva) == 32
    assert password_nueva != password_vieja
    assert password_nueva not in salida
    assert password_vieja not in salida

    # Limpieza efectiva: lockouts y sesiones del usuario a cero.
    con = _con()
    intentos = con.execute(
        "SELECT COUNT(*) FROM intentos_login WHERE username = 'admin'"
    ).fetchone()[0]
    sesiones = con.execute(f"SELECT COUNT(*) FROM {_tabla_sesiones()}").fetchone()[0]
    con.close()
    assert intentos == 0 and sesiones == 0

    # La password nueva entra, la vieja ya no; sin lockout residual.
    monkeypatch.delenv("AULASENSE_ADMIN_PASSWORD", raising=False)
    assert _login(cliente, password_vieja).status_code == 401
    assert _login(cliente, password_nueva).status_code == 200