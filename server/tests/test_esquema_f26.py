"""Tests F26 del contrato (SPEC §16.4/§16.5 — fase F26-a1).

Cubren: sufijos retain nuevos ``ota``/``imagen_estado``/``heap_interno`` (que
ANTES eran rechazados por ``parse_topic``), eventos OTA_*/FIRMWARE/IMAGEN_INIT/
CAMARA_ERROR/AP_ERROR/SISTEMA_*, y la migracion idempotente ``migrar_esquema_f26``
(decision_* en imagenes, clef_uso, estado_sistema, telemetria.internal_*).
Sin writer ni broker: aqui solo se ejerce el modulo ``esquema``.
"""

from __future__ import annotations

import sqlite3

import pytest

import esquema

PREFIJO = "aulasense/v1/devices"
DEV = "2e6f0c909e30673e"

# Payloads de ejemplo calcados de SPEC §16.3/§16.4 (simulados, para prueba).
OTA_OK = {
    "estado": "downloading",
    "version_actual": "f25.3",
    "version_objetivo": "f26.1",
    "sha256_ok": False,
    "detalle": "bajando slot b",
    "ts_ms": 1763400000000,
}
IMAGEN_ESTADO_OK = {
    "camara_ok": True,
    "camara_pid": "0x3660",
    "camara_err": "",
    "pipeline_ok": True,
    "captura_habilitada": True,
    "gate_off": False,
    "descartes": 0,
    "imagenes_ok": 57,
    "internal_free_kb": 118,
    "internal_largest_block_kb": 60,
    "build": "f26.1",
    "imagen_habilitado": True,
    "ts_ms": 1763400000000,
}
HEAP_OK = {
    "internal_free_kb": 118,
    "internal_largest_block_kb": 60,
    "ts_ms": 1763400000000,
}

EVENTOS_PLACA_F26 = (
    "OTA_CHECK",
    "OTA_INICIO",
    "OTA_OK",
    "OTA_ERROR",
    "FIRMWARE",
    "IMAGEN_INIT",
    "CAMARA_ERROR",
    "AP_ERROR",
)
EVENTOS_SISTEMA_F26 = (
    "SISTEMA_MQTT_CAIDO",
    "SISTEMA_MQTT_RECUPERADO",
    "SISTEMA_TELEMETRIA_STALE",
    "SISTEMA_DISCO_WARN",
    "SISTEMA_TUNEL_CAIDO",
)
EVENTOS_VIEJOS = (
    "AP_FALLBACK",
    "CFG_GUARDADA",
    "STA_RECONECTADO",
    "IMAGEN_ON",
    "IMAGEN_GATE_OFF",
    "IMAGEN_DESCARTE",
)


def topic(suffix: str, device_id: str = DEV) -> str:
    return f"{PREFIJO}/{device_id}/{suffix}"


@pytest.fixture
def conn(tmp_path):
    """BD en memoria con el DDL base (tablas SPEC §8 + config generada)."""
    con = sqlite3.connect(str(tmp_path / "f26.db"))
    con.executescript(esquema.ESQUEMA_SQL)
    yield con
    con.close()


# --- 1. Sufijos F26 dados de alta en el contrato ----------------------------


def test_sufijos_nuevos_en_tabla():
    for sufijo in ("ota", "imagen_estado", "heap_interno"):
        assert sufijo in esquema.SUFFIX_VALIDOS
        assert sufijo in esquema.KINDS_VALIDOS
    # El topic de COMANDO no se suscribe (la ingesta publica, D-F26-2).
    assert "aulasense_ota" not in esquema.SUFFIX_VALIDOS


def test_parse_topic_acepta_sufijos_nuevos():
    for sufijo in ("ota", "imagen_estado", "heap_interno"):
        partes = esquema.parse_topic(topic(sufijo))
        assert partes == (DEV, sufijo)


def test_validar_ota_aceptado():
    res = esquema.validar(topic("ota"), OTA_OK)
    assert res["valido"] is True
    assert res["kind"] == "ota"
    assert res["datos"] == OTA_OK


@pytest.mark.parametrize(
    "mutado,motivo",
    [
        ({"estado": "instalando"}, "estado"),
        ({"ts_ms": "1763400000000"}, "ts_ms"),
        ({"ts_ms": -1}, "ts_ms"),
        ({"sha256_ok": "si"}, "sha256_ok"),
        ({"detalle": "x" * 121}, "detalle"),
        ({"version_actual": 7}, "version_actual"),
    ],
)
def test_validar_ota_rechazos(mutado, motivo):
    payload = dict(OTA_OK)
    payload.update(mutado)
    res = esquema.validar(topic("ota"), payload)
    assert res["valido"] is False
    assert motivo in res["error"]


def test_validar_ota_rechaza_secreto():
    payload = dict(OTA_OK)
    payload["pass"] = "no-debe-viajar"
    res = esquema.validar(topic("ota"), payload)
    assert res["valido"] is False
    assert "secreto" in res["error"]


def test_validar_imagen_estado_aceptado():
    res = esquema.validar(topic("imagen_estado"), IMAGEN_ESTADO_OK)
    assert res["valido"] is True
    assert res["kind"] == "imagen_estado"


def test_validar_imagen_estado_rechazos():
    sin_captura = {k: v for k, v in IMAGEN_ESTADO_OK.items() if k != "captura_habilitada"}
    res = esquema.validar(topic("imagen_estado"), sin_captura)
    assert res["valido"] is False
    assert "captura_habilitada" in res["error"]

    mal_tipo = dict(IMAGEN_ESTADO_OK, camara_ok="si")
    res = esquema.validar(topic("imagen_estado"), mal_tipo)
    assert res["valido"] is False
    assert "camara_ok" in res["error"]

    heap_malo = dict(IMAGEN_ESTADO_OK, internal_free_kb="118")
    res = esquema.validar(topic("imagen_estado"), heap_malo)
    assert res["valido"] is False
    assert "internal_free_kb" in res["error"]


def test_validar_heap_interno_aceptado():
    res = esquema.validar(topic("heap_interno"), HEAP_OK)
    assert res["valido"] is True
    assert res["kind"] == "heap_interno"


def test_validar_heap_interno_rechazos():
    sin_libre = dict(HEAP_OK)
    sin_libre.pop("internal_free_kb")
    res = esquema.validar(topic("heap_interno"), sin_libre)
    assert res["valido"] is False
    assert "internal_free_kb" in res["error"]

    negativo = dict(HEAP_OK, internal_largest_block_kb=-5)
    res = esquema.validar(topic("heap_interno"), negativo)
    assert res["valido"] is False
    assert "internal_largest_block_kb" in res["error"]

    sin_ts = {k: v for k, v in HEAP_OK.items() if k != "ts_ms"}
    res = esquema.validar(topic("heap_interno"), sin_ts)
    assert res["valido"] is False
    assert "ts_ms" in res["error"]


# --- 2. EVENTOS_CONFIG: nuevos aceptados, viejos intactos -------------------


@pytest.mark.parametrize("evento", EVENTOS_PLACA_F26 + EVENTOS_SISTEMA_F26)
def test_eventos_f26_aceptados_por_validar(evento):
    res = esquema.validar(
        topic("aulasense_config"),
        {"timestamp_ms": 5, "evento": evento, "detalle": "demo"},
    )
    assert res["valido"] is True, res["error"]
    assert res["kind"] == "aulasense_config"


@pytest.mark.parametrize("evento", EVENTOS_VIEJOS)
def test_eventos_viejos_intactos(evento):
    res = esquema.validar(
        topic("aulasense_config"), {"timestamp_ms": 5, "evento": evento}
    )
    assert res["valido"] is True, res["error"]


def test_evento_desconocido_sigue_rechazado():
    res = esquema.validar(
        topic("aulasense_config"), {"timestamp_ms": 5, "evento": "PING"}
    )
    assert res["valido"] is False
    assert "evento" in res["error"]


# --- 3. Migracion F26: tablas y columnas nuevas ------------------------------


DECISION_COLS = (
    "decision_estado",
    "decision_etiqueta",
    "decision_confianza",
    "decision_modelo",
    "decision_ts_ms",
    "decision_tokens",
    "decision_cubre",
    "decision_calidad",
    "decision_actividad",
)


def test_migracion_anade_columnas_y_tablas(conn):
    resumen = esquema.migrar_esquema_f26(conn)
    columnas = esquema.columnas_tabla(conn, "imagenes")
    assert columnas is not None
    for col in DECISION_COLS:
        assert col in columnas
        assert col in resumen["columnas_nuevas"]
    telem = esquema.columnas_tabla(conn, "telemetria")
    assert "internal_free_kb" in telem
    assert "internal_largest_block_kb" in telem
    assert esquema.columnas_tabla(conn, "clef_uso") is not None
    estado = esquema.columnas_tabla(conn, "estado_sistema")
    assert {"ts_ms", "mqtt_conectado", "ingesta_activa", "detalle"} <= estado
    assert set(resumen["tablas_nuevas"]) == {"clef_uso", "estado_sistema"}


def test_migracion_idempotente_doble_pasada(conn):
    primera = esquema.migrar_esquema_f26(conn)
    assert primera["columnas_nuevas"], "la primera pasada debe anadir algo"
    segunda = esquema.migrar_esquema_f26(conn)
    assert segunda["columnas_nuevas"] == []
    assert segunda["tablas_nuevas"] == []
    assert segunda["config_eventos_reconstruida"] is False


def test_decision_estado_default_y_check(conn):
    esquema.migrar_esquema_f26(conn)
    conn.execute(
        "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes) "
        "VALUES (?, 1, 'imagenes/x/a.jpg', ?, 10)",
        (DEV, "a" * 64),
    )
    fila = conn.execute(
        "SELECT decision_estado FROM imagenes WHERE hash_sha256 = ?", ("a" * 64,)
    ).fetchone()
    assert fila[0] == "pendiente"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, "
            "tamano_bytes, decision_estado) VALUES (?, 2, 'imagenes/x/b.jpg', ?, 10, 'nope')",
            (DEV, "b" * 64),
        )


def test_telemetria_internal_acepta_nulo_backwards_compat(conn):
    esquema.migrar_esquema_f26(conn)
    conn.execute(
        "INSERT INTO telemetria (device_id, ts_ms) VALUES (?, 1)", (DEV,)
    )
    fila = conn.execute(
        "SELECT internal_free_kb, internal_largest_block_kb FROM telemetria"
    ).fetchone()
    assert fila == (None, None)


def test_clef_uso_gemela_de_vision_uso(conn):
    esquema.migrar_esquema_f26(conn)
    conn.execute(
        "INSERT INTO clef_uso (ts_ms, device_id, imagen_id, modelo, tokens, "
        "coste_usd, exito, detalle) VALUES (1, ?, NULL, 'cloudflare/clef-flash', "
        "260, 0.00001, 1, 'hecho')",
        (DEV,),
    )
    fila = conn.execute("SELECT modelo, tokens, exito FROM clef_uso").fetchone()
    assert fila == ("cloudflare/clef-flash", 260, 1)


def test_estado_sistema_fila_unica(conn):
    esquema.migrar_esquema_f26(conn)
    conn.execute(
        "INSERT INTO estado_sistema (id, ts_ms, mqtt_conectado, ingesta_activa, detalle) "
        "VALUES (1, 10, 1, 1, '{}')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO estado_sistema (id, ts_ms) VALUES (2, 20)"
        )


# --- 4. config_eventos: enum F26 en BD nueva y reconstruccion en BD vieja ---


def test_bd_nueva_config_eventos_acepta_eventos_f26(conn):
    """ESQUEMA_SQL genera el CHECK desde EVENTOS_CONFIG (fuente unica)."""
    conn.execute(
        "INSERT INTO config_eventos (device_id, ts_ms, tipo) VALUES (?, 1, 'OTA_CHECK')",
        (DEV,),
    )
    conn.execute(
        "INSERT INTO config_eventos (device_id, ts_ms, tipo) VALUES (?, 2, 'SISTEMA_MQTT_CAIDO')",
        (DEV,),
    )
    assert conn.execute("SELECT COUNT(*) FROM config_eventos").fetchone()[0] == 2


DDL_CONFIG_VIEJA = """
CREATE TABLE config_eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  tipo TEXT NOT NULL CHECK(tipo IN ('AP_FALLBACK','CFG_GUARDADA','STA_RECONECTADO','IMAGEN_ON','IMAGEN_GATE_OFF','IMAGEN_DESCARTE')),
  detalle TEXT,
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def test_reconstruccion_conserva_filas_y_es_idempotente(tmp_path):
    """BD F24/F25 con el enum viejo: la migracion la reconstruye sin perder filas."""
    con = sqlite3.connect(str(tmp_path / "vieja.db"))
    con.execute(DDL_CONFIG_VIEJA)
    con.execute(
        "INSERT INTO config_eventos (id, device_id, ts_ms, tipo, detalle) "
        "VALUES (7, ?, 100, 'IMAGEN_GATE_OFF', 'heap=90KB')",
        (DEV,),
    )
    con.commit()

    primera = esquema.migrar_esquema_f26(con)
    assert primera["config_eventos_reconstruida"] is True
    fila = con.execute(
        "SELECT id, tipo, detalle FROM config_eventos WHERE id = 7"
    ).fetchone()
    assert fila == (7, "IMAGEN_GATE_OFF", "heap=90KB")
    con.execute(
        "INSERT INTO config_eventos (device_id, ts_ms, tipo) VALUES (?, 200, 'OTA_OK')",
        (DEV,),
    )

    segunda = esquema.migrar_esquema_f26(con)
    assert segunda["config_eventos_reconstruida"] is False
    con.close()


class _ConexionQueFallaAlDescartar:
    """Proxy de ``sqlite3.Connection`` que revienta justo en el ``DROP`` de la copia."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    @property
    def in_transaction(self) -> bool:
        return self._real.in_transaction

    def execute(self, sql: str, *args: object):
        if "DROP TABLE config_eventos_legacy" in sql:
            raise RuntimeError("corte simulado antes del DROP")
        return self._real.execute(sql, *args)

    def commit(self) -> None:
        self._real.commit()

    def rollback(self) -> None:
        self._real.rollback()


def _simular_corte_r8(con: sqlite3.Connection, *, volcar_filas: bool) -> None:
    """Deja la DB en el estado intermedio que la version SIN transaccion podia crear.

    ``volcar_filas=True`` = corte entre el ``INSERT…SELECT`` y el ``DROP`` (la
    tabla vigente ya tiene enum y filas, sobra la copia); ``False`` = corte entre
    el ``CREATE`` y el ``INSERT…SELECT`` (vigente vacia, filas solo en la copia).
    """
    con.execute("ALTER TABLE config_eventos RENAME TO config_eventos_legacy")
    con.execute(esquema._DDL_CONFIG_EVENTOS)
    if volcar_filas:
        columnas = ", ".join(esquema._COLUMNAS_CONFIG_EVENTOS)
        con.execute(
            f"INSERT INTO config_eventos ({columnas}) "
            f"SELECT {columnas} FROM config_eventos_legacy"
        )
    con.commit()


def test_legacy_huerfana_se_descarta_sin_perder_filas(tmp_path):
    """Corte entre INSERT…SELECT y DROP: la 2a corrida limpia la copia (MEDIO-1)."""
    con = sqlite3.connect(str(tmp_path / "huerfana.db"))
    con.execute(DDL_CONFIG_VIEJA)
    con.execute(
        "INSERT INTO config_eventos (id, device_id, ts_ms, tipo) "
        "VALUES (7, ?, 100, 'IMAGEN_GATE_OFF')",
        (DEV,),
    )
    con.commit()
    assert esquema.migrar_esquema_f26(con)["config_eventos_reconstruida"] is True
    _simular_corte_r8(con, volcar_filas=True)
    assert esquema.columnas_tabla(con, "config_eventos_legacy") is not None

    resumen = esquema.migrar_esquema_f26(con)
    assert resumen["config_eventos_reconstruida"] is False  # no re-reconstruye
    assert esquema.columnas_tabla(con, "config_eventos_legacy") is None  # limpiada
    assert con.execute(
        "SELECT id, tipo FROM config_eventos ORDER BY id"
    ).fetchall() == [(7, "IMAGEN_GATE_OFF")]
    con.close()


def test_legacy_huerfana_completa_las_filas_que_faltan(tmp_path):
    """Corte entre CREATE y INSERT…SELECT: la copia repone las filas y se descarta."""
    con = sqlite3.connect(str(tmp_path / "huerfana_vacia.db"))
    con.execute(DDL_CONFIG_VIEJA)
    con.execute(
        "INSERT INTO config_eventos (id, device_id, ts_ms, tipo) "
        "VALUES (7, ?, 100, 'IMAGEN_GATE_OFF')",
        (DEV,),
    )
    con.commit()
    assert esquema.migrar_esquema_f26(con)["config_eventos_reconstruida"] is True
    _simular_corte_r8(con, volcar_filas=False)
    assert con.execute("SELECT COUNT(*) FROM config_eventos").fetchone()[0] == 0

    esquema.migrar_esquema_f26(con)
    assert esquema.columnas_tabla(con, "config_eventos_legacy") is None
    assert con.execute(
        "SELECT id, tipo FROM config_eventos ORDER BY id"
    ).fetchall() == [(7, "IMAGEN_GATE_OFF")]
    con.execute(
        "INSERT INTO config_eventos (device_id, ts_ms, tipo) VALUES (?, 200, 'OTA_OK')",
        (DEV,),
    )  # el enum vigente quedo aplicado
    con.close()


def test_corte_entre_rename_y_create_se_restaura(tmp_path):
    """Estado 'solo legacy' (RENAME hecho, CREATE no): se restaura y se reconstruye."""
    con = sqlite3.connect(str(tmp_path / "solo_legacy.db"))
    con.execute(DDL_CONFIG_VIEJA)
    con.execute(
        "INSERT INTO config_eventos (id, device_id, ts_ms, tipo) "
        "VALUES (7, ?, 100, 'IMAGEN_GATE_OFF')",
        (DEV,),
    )
    con.commit()
    con.execute("ALTER TABLE config_eventos RENAME TO config_eventos_legacy")
    con.commit()
    assert esquema.columnas_tabla(con, "config_eventos") is None

    resumen = esquema.migrar_esquema_f26(con)
    assert resumen["config_eventos_reconstruida"] is True
    assert esquema.columnas_tabla(con, "config_eventos_legacy") is None
    assert con.execute(
        "SELECT id, tipo FROM config_eventos"
    ).fetchall() == [(7, "IMAGEN_GATE_OFF")]
    con.close()


def test_reconstruccion_es_atomica_si_falla_el_drop(tmp_path):
    """MEDIO-1: si algo revienta a mitad, el ROLLBACK deja la tabla vieja intacta."""
    con = sqlite3.connect(str(tmp_path / "atomica.db"))
    con.execute(DDL_CONFIG_VIEJA)
    con.execute(
        "INSERT INTO config_eventos (id, device_id, ts_ms, tipo) "
        "VALUES (7, ?, 100, 'IMAGEN_GATE_OFF')",
        (DEV,),
    )
    con.commit()

    with pytest.raises(RuntimeError):
        esquema._migrar_config_eventos_enum(_ConexionQueFallaAlDescartar(con))

    ddl = con.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'config_eventos'"
    ).fetchone()[0]
    assert "OTA_CHECK" not in ddl  # sigue el enum viejo (RENAME/CREATE deshechos)
    assert esquema.columnas_tabla(con, "config_eventos_legacy") is None  # sin huerfana
    assert con.execute("SELECT id, tipo FROM config_eventos").fetchall() == [
        (7, "IMAGEN_GATE_OFF")
    ]
    # La pasada siguiente (sin el fallo) reconstruye con normalidad.
    assert esquema.migrar_esquema_f26(con)["config_eventos_reconstruida"] is True
    assert "OTA_CHECK" in con.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'config_eventos'"
    ).fetchone()[0]
    con.close()


def test_migracion_tolerante_bd_parcial(tmp_path):
    """Sin imagenes/telemetria (BD parcial de otros tests): no lanza, omite."""
    con = sqlite3.connect(str(tmp_path / "parcial.db"))
    resumen = esquema.migrar_esquema_f26(con)
    # Nadie las columnas porque las tablas base no existen (solo WARNING).
    assert resumen["columnas_nuevas"] == []
    # Las tablas nuevas SI se crean (no dependen de las anteriores).
    assert esquema.columnas_tabla(con, "clef_uso") is not None
    assert esquema.columnas_tabla(con, "estado_sistema") is not None
    con.close()
