"""Tests de `tools/session_logger.py` (tabla `sesiones`, extension F3 de SPEC §8).

Cubren start/stop/list, la sesion activa consultable por F4/dashboard, los
rechazos de contrato (id invalido, duplicado, segunda sesion activa, nota larga)
y la idempotencia del DDL compartido de `server/ingesta/esquema.py`.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # aulasense/


def _cargar(nombre: str, ruta: Path):
    """Carga un modulo por ruta (el proyecto no es paquete instalable)."""
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


session_logger = _cargar("session_logger_aulasense", ROOT / "tools" / "session_logger.py")

NOW = 1_760_000_000_000


@pytest.fixture()
def db(tmp_path) -> Path:
    return tmp_path / "sesiones.db"


def _filas(db_path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def test_start_list_active_stop(db: Path):
    fila = session_logger.iniciar_sesion(
        "S1", nota="aula con dos personas", device_id="0123456789abcdef",
        db_path=db, now_ms=NOW,
    )
    assert fila["sesion"] == "S1"
    assert fila["estado"] == "activa"
    assert fila["inicio_ms"] == NOW
    assert fila["fin_ms"] is None

    activa = session_logger.sesion_activa(db_path=db)
    assert activa is not None and activa["sesion"] == "S1"

    lista = session_logger.listar_sesiones(db_path=db)
    assert [item["sesion"] for item in lista] == ["S1"]

    cerrada = session_logger.detener_sesion(
        "S1", nota="fin de sesion", db_path=db, now_ms=NOW + 90_000
    )
    assert cerrada["estado"] == "cerrada"
    assert cerrada["fin_ms"] == NOW + 90_000
    assert cerrada["nota"] == "fin de sesion"
    assert session_logger.sesion_activa(db_path=db) is None

    lista = session_logger.listar_sesiones(db_path=db)
    assert lista[0]["estado"] == "cerrada"


def test_lista_mas_recientes_primero(db: Path):
    session_logger.iniciar_sesion("S1", db_path=db, now_ms=NOW)
    session_logger.detener_sesion("S1", db_path=db, now_ms=NOW + 1000)
    session_logger.iniciar_sesion("S2", db_path=db, now_ms=NOW + 10_000)
    lista = session_logger.listar_sesiones(db_path=db)
    assert [item["sesion"] for item in lista] == ["S2", "S1"]


def test_rechaza_duplicado_y_segunda_activa(db: Path):
    session_logger.iniciar_sesion("S1", db_path=db, now_ms=NOW)
    with pytest.raises(session_logger.ErrorSesion, match="ya hay una sesion activa"):
        session_logger.iniciar_sesion("S2", db_path=db, now_ms=NOW + 1)

    session_logger.detener_sesion("S1", db_path=db, now_ms=NOW + 2)
    with pytest.raises(session_logger.ErrorSesion, match="ya existe"):
        session_logger.iniciar_sesion("S1", db_path=db, now_ms=NOW + 3)


def test_stop_desconocida_o_ya_cerrada(db: Path):
    with pytest.raises(session_logger.ErrorSesion, match="no existe"):
        session_logger.detener_sesion("S9", db_path=db)
    session_logger.iniciar_sesion("S1", db_path=db, now_ms=NOW)
    session_logger.detener_sesion("S1", db_path=db, now_ms=NOW + 10)
    with pytest.raises(session_logger.ErrorSesion, match="ya estaba cerrada"):
        session_logger.detener_sesion("S1", db_path=db, now_ms=NOW + 20)


def test_ids_y_notas_fuera_de_contrato(db: Path):
    for malo in ("", "con espacio", "a/b", "x" * 33, "s1;DROP"):
        with pytest.raises(session_logger.ErrorSesion, match="id de sesion invalido"):
            session_logger.iniciar_sesion(malo, db_path=db)
    with pytest.raises(session_logger.ErrorSesion, match="nota supera"):
        session_logger.iniciar_sesion("S1", nota="x" * 201, db_path=db)
    # Nada se creo tras los rechazos (listar_sesiones crea el esquema vacio).
    assert session_logger.listar_sesiones(db_path=db) == []


def test_esquema_compartido_idempotente(db: Path):
    import esquema

    con = sqlite3.connect(db)
    try:
        con.executescript(esquema.ESQUEMA_SQL)
        con.executescript(esquema.ESQUEMA_SQL)
        nombres = {
            fila[0]
            for fila in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
            )
        }
    finally:
        con.close()
    assert {"sesiones", "idx_sesiones_inicio", "idx_sesiones_estado"} <= nombres
    # El logger reutiliza esa DB sin recrear ni duplicar nada.
    session_logger.iniciar_sesion("S1", db_path=db)
    session_logger.detener_sesion("S1", db_path=db, now_ms=NOW)
    session_logger.iniciar_sesion("S2", db_path=db, now_ms=NOW + 1)
    assert _filas(db, "SELECT COUNT(*) FROM sesiones")[0][0] == 2


def test_cli_start_list_stop(db: Path, capsys):
    assert session_logger.main(["--db", str(db), "start", "--id", "S1", "--nota", "demo"]) == 0
    assert session_logger.main(["--db", str(db), "list"]) == 0
    salida = capsys.readouterr().out
    assert "S1" in salida and "activa" in salida

    assert session_logger.main(["--db", str(db), "stop", "--id", "S1"]) == 0
    capsys.readouterr()  # descarta la linea de stop
    assert session_logger.main(["--db", str(db), "list", "--json"]) == 0
    import json

    filas = json.loads(capsys.readouterr().out)
    assert filas[0]["estado"] == "cerrada"

    # Errores de dominio: exit 1 con mensaje en stderr.
    assert session_logger.main(["--db", str(db), "stop", "--id", "S1"]) == 1
    assert "ya estaba cerrada" in capsys.readouterr().err