"""Tests del GATE-rendimiento F26 (aplicación 2026-11-17).

Dos frentes:

1) ``dashboard.app._conectar()`` — gate de ``PRAGMA user_version`` para que las
   migraciones de respaldo (F25 visión + F26 decision/estado_sistema/clef_uso)
   corran UNA vez por BD y no por request (evidencia de coste:
   ``.tmp/perf_f26_userver.json`` — mediana 1.878 → 1.367 ms y COMMIT por
   request eliminado; plan redactado en ``.tmp/perf/sugerencias.md`` §1):
   - sella la marca tras la primera pasada;
   - en estado estable NO vuelve a invocar los respaldos (spy);
   - si el respaldo falla NO sella y el siguiente request reintenta;
   - una BD real pre-F25 se migra COMPLETA aunque el gate exista (el gate no
     rompe la migración: columnas y tablas F25/F26 aparecen igual).

2) ``ingesta.esquema.migrar_esquema_f26()`` — índice compuesto
   ``idx_img_decision_ts(decision_estado, decision_ts_ms)`` (delta medido a
   3 000 decisiones: p50 3.1 → 0.027 ms, p95 13.3 → 0.09 ms,
   ``.tmp/perf_f26_dts.json``):
   - idempotente (doble pasada, un solo índice);
   - ``decision_recientes`` de /api/salud lo usa y ya NO ordena por temp B-tree;
   - FIX 1 DESCARTADO con evidencia: el dedupe Clef (``clef.hash_ya_decidido``)
     usa ``idx_img_hash_unico`` (creado por F25, esquema.py:877) — el índice
     redundante ``idx_img_hash`` NO debe existir (coste de escritura sin
     ganancia: el plan ya es O(log n) con el único canónico).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

DEVID = "0123456789abcdef"

# SQL exactos del dashboard (app.py) y del worker Clef (clef.py:452-461).
SQL_DECISION_RECIENTES = (
    "SELECT id, decision_etiqueta, decision_confianza, decision_actividad, "
    "decision_cubre, decision_modelo, decision_ts_ms FROM imagenes "
    "WHERE decision_estado = 'hecho' ORDER BY decision_ts_ms DESC, id DESC LIMIT 8"
)
SQL_HASH_YA_DECIDIDO = (
    "SELECT 1 FROM imagenes WHERE hash_sha256 = ? AND id <> ? "
    "AND decision_estado = 'hecho' LIMIT 1"
)


def _planes(con: sqlite3.Connection, sql: str, params: tuple = ()) -> str:
    return "\n".join(str(fila) for fila in con.execute("EXPLAIN QUERY PLAN " + sql, params))


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    """Módulo ``dashboard.app`` con DB temporal virgen (user_version=0)."""
    db = tmp_path / "aulasense_gate.db"
    monkeypatch.setenv("AULASENSE_DB", str(db))
    import dashboard.app as modulo  # import perezoso: requiere fastapi
    return modulo, db


# --- 1) gate user_version de _conectar ----------------------------------------


def test_conectar_sella_user_version(app_db):
    modulo, db = app_db
    con = modulo._conectar()
    try:
        marca = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()
    assert marca == modulo.USER_VERSION_RESPALDO == 26


def test_conectar_estable_no_reinvoca_respaldo(app_db):
    """Segunda conexión (marca puesta): los respaldos NO se vuelven a ejecutar."""
    modulo, db = app_db
    con = modulo._conectar()
    con.close()

    llamadas = {"f25": 0, "f26": 0}
    real_f25, real_f26 = modulo._migrar_imagenes_f25, modulo._migrar_f26_respaldo

    def spy_f25(c):
        llamadas["f25"] += 1
        return real_f25(c)

    def spy_f26(c):
        llamadas["f26"] += 1
        return real_f26(c)

    modulo._migrar_imagenes_f25, modulo._migrar_f26_respaldo = spy_f25, spy_f26
    try:
        for _ in range(3):
            con = modulo._conectar()
            con.close()
    finally:
        modulo._migrar_imagenes_f25, modulo._migrar_f26_respaldo = real_f25, real_f26
    assert llamadas == {"f25": 0, "f26": 0}


def test_respaldo_fallido_no_sella_y_reintenta(app_db):
    """Si el respaldo F26 falla (p. ej. BD bloqueada) la marca NO se sella y el
    siguiente request reintenta hasta lograrlo."""
    modulo, db = app_db
    real_f26 = modulo._migrar_f26_respaldo
    modulo._migrar_f26_respaldo = lambda con: False
    try:
        con = modulo._conectar()
        con.close()
    finally:
        modulo._migrar_f26_respaldo = real_f26
    c = sqlite3.connect(db)
    try:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        c.close()
    # reintento: la conexión siguiente completa el respaldo y sella
    con = modulo._conectar()
    con.close()
    c = sqlite3.connect(db)
    try:
        assert c.execute("PRAGMA user_version").fetchone()[0] == modulo.USER_VERSION_RESPALDO
    finally:
        c.close()


def test_gate_no_rompe_migracion_real_desde_f24(app_db):
    """BD con el esquema base (pre-F25: sin columnas de visión ni F26) migra
    COMPLETA por la vía del dashboard pese al gate, y luego queda marcada."""
    from ingesta.esquema import ESQUEMA_SQL

    modulo, db = app_db
    c = sqlite3.connect(db)
    c.executescript(ESQUEMA_SQL)  # imagenes SIN canal/descripcion/decision_*
    cols = {f[1] for f in c.execute("PRAGMA table_info(imagenes)")}
    assert "descripcion" not in cols and "decision_estado" not in cols
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    c.close()

    con = modulo._conectar()
    con.close()

    c = sqlite3.connect(db)
    try:
        cols = {f[1] for f in c.execute("PRAGMA table_info(imagenes)")}
        assert {"canal", "descripcion", "etiquetas", "vision_estado",
                "decision_estado", "decision_ts_ms", "decision_modelo"} <= cols
        cols_tel = {f[1] for f in c.execute("PRAGMA table_info(telemetria)")}
        assert {"internal_free_kb", "internal_largest_block_kb"} <= cols_tel
        tablas = {f[0] for f in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"estado_sistema", "clef_uso"} <= tablas
        assert c.execute("PRAGMA user_version").fetchone()[0] == 26
    finally:
        c.close()


# --- 2) migrar_esquema_f26: idx_img_decision_ts y dedupe por hash -------------


@pytest.fixture()
def bd_canonica(tmp_path):
    """BD fresca con el DDL canónico + migraciones F25 y F26 aplicadas dos veces
    (la doble pasada ES el test de idempotencia)."""
    from ingesta import esquema

    db = tmp_path / "canonica.db"
    con = sqlite3.connect(db)
    con.executescript(esquema.ESQUEMA_SQL)
    esquema.migrar_esquema_f25(con)
    esquema.migrar_esquema_f26(con)
    esquema.migrar_esquema_f26(con)  # doble pasada OK (idempotente)
    con.commit()
    yield con
    con.close()


def _insertar(bd, n_hecho: int) -> None:
    for i in range(n_hecho):
        bd.execute(
            "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes,"
            " vision_estado, decision_estado, decision_ts_ms, descripcion)"
            " VALUES (?, ?, ?, ?, 40000, 'hecho', 'hecho', ?, 'descripcion de prueba')",
            (DEVID, 1_700_000_000_000 + i, f"imagenes/{DEVID}/x{i}.jpg",
             f"{i:064x}", 1_700_000_100_000 + i * 60_000),
        )
    bd.commit()


def test_idx_decision_ts_presente_y_unico_indice_hash(bd_canonica):
    nombres = [f[1] for f in bd_canonica.execute("PRAGMA index_list(imagenes)")]
    assert "idx_img_decision_ts" in nombres          # fix aplicado (delta medido)
    assert "idx_img_hash_unico" in nombres           # cobertura CANONICA del dedupe
    assert "idx_img_hash" not in nombres             # FIX 1 descartado: redundante
    assert nombres.count("idx_img_decision_ts") == 1  # idempotencia de la doble pasada


def test_decision_recientes_usa_idx_decision_ts(bd_canonica):
    _insertar(bd_canonica, 60)
    plan = _planes(bd_canonica, SQL_DECISION_RECIENTES)
    assert "idx_img_decision_ts" in plan
    assert "TEMP B-TREE" not in plan  # el orden sale del propio índice (backwards-scan)


def test_hash_ya_decidido_usa_idx_hash_unico(bd_canonica):
    """EXPLAIN de la query real de clef.hash_ya_decidido: usa el UNIQUE de F25 →
    el SCAN O(n) que postuló el gate NO existe y añadir idx_img_hash sería
    deudor de escritura sin ganancia (FIX 1 descartado con evidencia)."""
    _insertar(bd_canonica, 5)
    plan = _planes(bd_canonica, SQL_HASH_YA_DECIDIDO, ("0" * 64, 1))
    assert "idx_img_hash_unico" in plan
    assert "SCAN imagenes" not in plan
