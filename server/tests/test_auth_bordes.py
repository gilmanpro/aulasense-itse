"""Casos BORDE de login/sesiones/lockout (SPEC §9.2) que faltaban en
``test_auth.py`` (revisa el 2026-11-15):

- username con ESPACIOS (``strip()`` del handler: la normalización no permite
  evadir el lockout añadiendo espacios) y homógrafos UNICODE (``admın`` con i
  sin punto, ``аdmin`` con a cirílica): 401 genérico, jamás acceso;
- payload invalido por tipos/largos (username null, 65 chars; password null,
  257 chars) -> 422 de FastAPI ANTES del handler (nunca 500);
- umbrales exactos del lockout progresivo a nivel unidad (4/5/9/10/19/20);
- expiración TEMPORAL del bloqueo dentro de la ventana de 15 min (el bloqueo
  cuenta desde el último fallo y auto-expira — R-F25-3);
- ``limpiar_sesiones`` a nivel unidad (absolutas + inactivas fuera, viva queda);
- logout por SESIÓN: cerrar una cookie no invalida las otras sesiones activas
  del mismo usuario (y la cookie cerrada reutilizada a mano muere: 401).

Reutiliza el fixture ``cliente`` y los helpers de ``test_auth`` (misma DB tmp,
mismas credenciales generadas por código; el repo real nunca se toca).
"""

from __future__ import annotations

import sqlite3

import pytest

from dashboard import auth
from test_auth import (  # fixture e helpers compartidos
    _login,
    _sesiones,
    cliente,  # noqa: F401  (fixture reexportada)
)

# Reloj fijo para las pruebas unitarias de auth (determinismo total).
T0 = 1_760_000_000_000


@pytest.fixture()
def con_auth(monkeypatch):
    """BD en memoria con las tablas de auth y el reloj de ``auth`` congelado."""
    monkeypatch.setattr(auth, "ahora_ms", lambda: T0)
    con = sqlite3.connect(":memory:")
    auth.ensure_tablas(con)
    con.execute("INSERT INTO usuarios (username, pass_hash) VALUES ('u', 'x')")
    con.commit()
    yield con
    con.close()


def _insertar_fallos_con(con, n: int, ts: int) -> None:
    con.executemany(
        "INSERT INTO intentos_login (ip, username, ts_ms, exito) VALUES ('ip', 'u', ?, 0)",
        [(ts,) for _ in range(n)],
    )
    con.commit()


# --- 1. Username: espacios, unicode y largos -----------------------------------


def test_username_con_espacios_se_normaliza_y_entra(cliente, credenciales_tmp):
    """El handler hace ``strip()``: " admin " es el admin legitimo (respuesta
    además reporta el username CANONICO de BD, no el enviado)."""
    respuesta = _login(cliente, credenciales_tmp["password"], usuario="  admin ")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"ok": True, "usuario": "admin"}


def test_fallos_con_espacios_cuentan_contra_el_mismo_lockout(cliente, credenciales_tmp):
    """Los espacios no evaden el lockout: 5 fallos con " admin " bloquean al par
    (IP, "admin") — la cuenta se hace sobre el username YA normalizado."""
    for _ in range(5):
        assert _login(cliente, "incorrecta", usuario=" admin ").status_code == 401
    bloqueado = _login(cliente, credenciales_tmp["password"], usuario="admin")
    assert bloqueado.status_code == 429
    assert _sesiones() == []


def test_username_homografo_unicode_401_generico(cliente, credenciales_tmp):
    """Homógrafos unicode (i sin punto turca, a cirílica) NO son el admin:
    401 idéntico, sin sesión."""
    for usuario in ("adm\u0131n", "\u0430dmin", "ADMIN"):
        respuesta = _login(cliente, credenciales_tmp["password"], usuario=usuario)
        assert respuesta.status_code == 401, usuario
        assert respuesta.json() == {"detail": "credenciales inválidas"}
    assert _sesiones() == []


def test_username_vacio_401_y_payloads_invalidos_422(cliente, credenciales_tmp):
    """La validación de tipos/largos de FastAPI corta ANTES del handler: nunca
    500 ni 401 por payloads mal formados."""
    # Username vacío: login generico fallido (401), no error de validación.
    assert _login(cliente, "x", usuario="").status_code == 401
    # Null donde va str -> 422 (pydantic); 65 chars -> 422 (max_length=64).
    assert cliente.post("/login", json={"username": None, "password": "x"}).status_code == 422
    assert cliente.post(
        "/login", json={"username": "a" * 65, "password": "x"}
    ).status_code == 422
    # Password null -> 422; 257 chars -> 422 (max_length=256). Body sin username
    # usa el default "" -> 401 (no es un payload inválido).
    assert cliente.post("/login", json={"username": "admin", "password": None}).status_code == 422
    assert cliente.post(
        "/login", json={"username": "admin", "password": "x" * 257}
    ).status_code == 422
    assert _sesiones() == []


# --- 2. Lockout: umbrales exactos y expiración temporal (unidad) ---------------


def test_duracion_bloqueo_umbrales_exactos_5_10_20():
    """Escalera 5/10/20 -> 60 s / 15 min / 1 h con los bordes por debajo exactos."""
    assert auth.duracion_bloqueo_ms([]) == 0
    assert auth.duracion_bloqueo_ms([T0 - i for i in range(4)]) == 0  # 4: libre
    assert auth.duracion_bloqueo_ms([T0 - i for i in range(5)]) == 60_000  # 5
    assert auth.duracion_bloqueo_ms([T0 - i for i in range(9)]) == 60_000  # 9
    assert auth.duracion_bloqueo_ms([T0 - i for i in range(10)]) == 900_000  # 10
    assert auth.duracion_bloqueo_ms([T0 - i for i in range(19)]) == 900_000  # 19
    assert auth.duracion_bloqueo_ms([T0 - i for i in range(20)]) == 3_600_000  # 20


def test_bloqueo_restante_cuenta_desde_el_ultimo_fallo(con_auth):
    """5 fallos hace 30 s -> quedan 30 s de los 60 s de bloqueo."""
    _insertar_fallos_con(con_auth, 5, T0 - 30_000)
    assert auth.bloqueo_restante_ms(con_auth, "ip", "u") == 30_000


def test_bloqueo_expira_antes_que_la_ventana_de_conteo(con_auth):
    """R-F25-3: el bloqueo es TEMPORAL y auto-expirante. Con 5 fallos hace 90 s
    siguen DENTRO de la ventana de conteo (15 min) pero el bloqueo de 60 s ya
    caducó: restante 0 = se puede volver a intentar."""
    _insertar_fallos_con(con_auth, 5, T0 - 90_000)
    assert auth.bloqueo_restante_ms(con_auth, "ip", "u") == 0


def test_bloqueo_nivel_20_resta_de_la_hora(con_auth):
    """20 fallos hace 10 min -> quedan 50 min de bloqueo de 1 h."""
    _insertar_fallos_con(con_auth, 20, T0 - 10 * 60_000)
    assert auth.bloqueo_restante_ms(con_auth, "ip", "u") == 50 * 60_000


def test_registrar_exito_limpia_fallos_y_bloqueo_vuelve_a_cero(con_auth):
    """El éxito borra los fallos del par y deja solo la fila de éxito; el
    contador de bloqueo vuelve a cero inmediatamente."""
    _insertar_fallos_con(con_auth, 19, T0 - 1_000)
    # El bloqueo cuenta desde el ÚLTIMO fallo: quedan 900_000 - 1_000 ms.
    assert auth.bloqueo_restante_ms(con_auth, "ip", "u") == 899_000
    auth.registrar_intento(con_auth, "ip", "u", exito=True)
    assert auth.bloqueo_restante_ms(con_auth, "ip", "u") == 0
    restante = con_auth.execute(
        "SELECT COUNT(*) FROM intentos_login WHERE ip='ip' AND username='u' AND exito=0"
    ).fetchone()[0]
    assert restante == 0


# --- 3. Limpieza de sesiones (unidad) ------------------------------------------


def test_limpiar_sesiones_borra_absolutas_e_inactivas_y_conserva_vivas(con_auth):
    """``limpiar_sesiones`` (arranque del dashboard) expulsa por las dos vías:
    absoluta (``expira_en_ms <= ahora``) e inactiva (idle > 2 h); la viva queda
    y sigue validando."""
    token_viva = auth.crear_sesion(con_auth, 1)
    token_absoluta = auth.crear_sesion(con_auth, 1)
    token_inactiva = auth.crear_sesion(con_auth, 1)
    tabla = auth.tabla_sesiones(con_auth)
    con_auth.execute(
        f"UPDATE {tabla} SET expira_en_ms = ? WHERE token_hash = ?",
        (T0 - 1, auth.hash_token(token_absoluta)),
    )
    con_auth.execute(
        f"UPDATE {tabla} SET ultimo_uso_ms = ? WHERE token_hash = ?",
        (T0 - auth.IDLE_MS - 1, auth.hash_token(token_inactiva)),
    )
    con_auth.commit()

    assert auth.limpiar_sesiones(con_auth) == 2
    assert auth.validar_sesion(con_auth, token_viva) is not None
    assert auth.validar_sesion(con_auth, token_absoluta) is None
    assert auth.validar_sesion(con_auth, token_inactiva) is None


def test_sesion_bordes_absoluta_e_idle_exactos(con_auth):
    """Bordes: ``ahora >= expira`` caduca (incluso con expira == ahora);
    idle EXACTO (``ahora - ultimo_uso == IDLE_MS``) sobrevive (el comparador es
    ``>``), un ms más muere."""
    tabla = auth.tabla_sesiones(con_auth)

    token_borde = auth.crear_sesion(con_auth, 1)
    con_auth.execute(
        f"UPDATE {tabla} SET expira_en_ms = ? WHERE token_hash = ?",
        (T0, auth.hash_token(token_borde)),
    )  # expira EXACTAMENTE ahora -> validar_sesion usa ahora >= expira -> muere
    con_auth.commit()
    assert auth.validar_sesion(con_auth, token_borde) is None

    token_idle = auth.crear_sesion(con_auth, 1)
    con_auth.execute(
        f"UPDATE {tabla} SET ultimo_uso_ms = ? WHERE token_hash = ?",
        (T0 - auth.IDLE_MS, auth.hash_token(token_idle)),
    )
    con_auth.commit()
    assert auth.validar_sesion(con_auth, token_idle) is not None


# --- 4. Logout por sesión (HTTP): otras cookies del usuario siguen vivas --------


def _extraer_token(respuesta) -> str:
    trozo = respuesta.headers["set-cookie"].split(f"{auth.NOMBRE_COOKIE}=", 1)[1]
    return trozo.split(";", 1)[0]


def test_logout_invalida_solo_la_sesion_usada(cliente, credenciales_tmp):
    """Dos logins = dos sesiones. El logout (cookie del jar) mata SOLO la suya:
    la primera sesión del mismo usuario conserva el acceso hasta expirar."""
    r1 = _login(cliente, credenciales_tmp["password"])
    token1 = _extraer_token(r1)
    r2 = _login(cliente, credenciales_tmp["password"])
    token2 = _extraer_token(r2)
    assert token1 != token2
    assert len(_sesiones()) == 2

    assert cliente.post("/logout").status_code == 200  # jar lleva token2
    assert len(_sesiones()) == 1

    cliente.cookies.clear()
    reuso_muerto = cliente.get(
        "/api/resumen", headers={"Cookie": f"{auth.NOMBRE_COOKIE}={token2}"}
    )
    assert reuso_muerto.status_code == 401  # la sesión cerrada no revive
    viva = cliente.get(
        "/api/resumen", headers={"Cookie": f"{auth.NOMBRE_COOKIE}={token1}"}
    )
    assert viva.status_code == 200  # la otra sesión sigue válida
