"""Configuracion de pytest para el server de AulaSense.

Anade ``server/ingesta`` a ``sys.path`` para que los tests importen los modulos
de la ingesta como modulos planos (``esquema``, ``ingesta_mqtt``), igual que
cuando se ejecuta ``python server/ingesta/ingesta_mqtt.py``.

F25-e: añade fixtures compartidos para el login del dashboard (SPEC §9.2) y un
aislamiento AUTOUSADO de los ficheros de entorno: ningun test lee ni escribe el
``claves/.env`` real del repo (ni el ``.env`` de la raiz), y las passwords de
prueba se GENERAN por codigo (``secrets.token_hex(16)``), nunca literales.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest

_SERVER = Path(__file__).resolve().parents[1]

for _sub in ("ingesta",):
    _ruta = _SERVER / _sub
    if _ruta.is_dir() and str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))

_CLAVES_AUTH = ("AULASENSE_ADMIN_USER", "AULASENSE_ADMIN_PASSWORD", "AULASENSE_AUTH_HASH")


@pytest.fixture(autouse=True)
def aislar_entorno_auth(tmp_path, monkeypatch):
    """Aísla ``claves/.env`` y el ``.env`` raíz: los tests NUNCA usan los reales.

    Las rutas apuntan a ``tmp_path`` (que no existen salvo que el test las cree),
    de modo que un test jamas puede leer ni sobrescribir una credencial real.
    """
    monkeypatch.setenv("AULASENSE_CLAVES_ENV", str(tmp_path / "claves_aisladas" / ".env"))
    monkeypatch.setenv("AULASENSE_ENV_RAIZ", str(tmp_path / "env_raiz_aislado"))
    for clave in _CLAVES_AUTH:
        monkeypatch.delenv(clave, raising=False)
    yield


@pytest.fixture()
def credenciales_tmp(tmp_path, monkeypatch):
    """``claves/.env`` temporal con la password admin GENERADA POR CÓDIGO.

    Devuelve ``{"ruta", "usuario", "password"}``; la password es aleatoria por
    test (nunca un literal del repo ni la credencial real del dueño).
    """
    ruta = tmp_path / "claves" / ".env"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    password = secrets.token_hex(16)
    ruta.write_text(
        "AULASENSE_ADMIN_USER=admin\n"
        f"AULASENSE_ADMIN_PASSWORD={password}\n"
        "DASHSCOPE_API_KEY=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AULASENSE_CLAVES_ENV", str(ruta))
    monkeypatch.setenv("AULASENSE_ADMIN_USER", "admin")
    monkeypatch.setenv("AULASENSE_ADMIN_PASSWORD", password)
    return {"ruta": ruta, "usuario": "admin", "password": password}


@pytest.fixture()
def iniciar_sesion():
    """Factory: ``POST /login`` con credenciales válidas y exige 200.

    La cookie de sesión queda en el cookie jar del ``TestClient`` (fixture de
    sesión REAL: la suite previa NO se debilita desactivando auth).
    """

    def _iniciar(cliente, password: str, usuario: str = "admin"):
        respuesta = cliente.post(
            "/login", json={"username": usuario, "password": password}
        )
        assert respuesta.status_code == 200, respuesta.text
        return respuesta

    return _iniciar