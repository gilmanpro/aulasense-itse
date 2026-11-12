"""Tests del export consolidado (SPEC §10.1): una fila por evento, SOLO real.

Fixture de DB en ``tmp_path`` construida con el DDL compartido
(``esquema.ESQUEMA_SQL``) e inserciones SQL explícitas: el export se prueba
aislado de la ingesta. Se valida el esquema exacto, la exclusión del sintético,
los joins por ventana, la tolerancia a nulos y la reproducibilidad.
"""

from __future__ import annotations

import csv
import importlib.util
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

import esquema

ROOT = Path(__file__).resolve().parents[2]  # aulasense/
DEV = "0123456789abcdef"
NOW = 1_760_000_000_000


def _cargar_modulo(nombre: str, ruta: Path):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


export_consolida = _cargar_modulo("export_consolida_f24", ROOT / "analysis" / "export_consolida.py")


def _conectar(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db))
    con.executescript(esquema.ESQUEMA_SQL)
    return con


def _db_completa(tmp_path: Path) -> Path:
    """1 evento real con todas las fuentes + 1 sintético + telemetría huérfana."""
    db = tmp_path / "aulasense.db"
    con = _conectar(db)
    try:
        real = con.execute(
            "INSERT INTO eventos (device_id, ts_ms, tipo, score, duracion_ms, origen) "
            "VALUES (?, ?, 'MOVIMIENTO_INICIO', 0.93, NULL, 'real')",
            (DEV, NOW),
        ).lastrowid
        con.execute(
            "INSERT INTO eventos (device_id, ts_ms, tipo, score, duracion_ms, origen) "
            "VALUES (?, ?, 'MOVIMIENTO_INICIO', 0.99, NULL, 'sintetico')",
            (DEV, NOW + 100_000),
        )
        # Telemetria cercana al evento real (la mas proxima manda).
        con.execute(
            "INSERT INTO telemetria (device_id, ts_ms, detection_avg_us, runtime_load_percent, "
            " free_memory_kb, csi_accepted_pps, csi_occupancy, wifi_rssi_dbm, die_temp_c, origen) "
            "VALUES (?, ?, 123.5, 45.0, 180000, 100, 0.9, -55, 52.5, 'real')",
            (DEV, NOW + 2_000),
        )
        # Telemetria del evento sintetico y una huerfana (sin evento).
        con.execute(
            "INSERT INTO telemetria (device_id, ts_ms, detection_avg_us, origen) "
            "VALUES (?, ?, 999.0, 'real')",
            (DEV, NOW + 100_500),
        )
        con.execute(
            "INSERT INTO telemetria (device_id, ts_ms, detection_avg_us, origen) "
            "VALUES (?, ?, 777.0, 'real')",
            (DEV, NOW + 25_000),
        )
        # Imagen del evento real (FK) con resultado de IA.
        con.execute(
            "INSERT INTO imagenes (device_id, ts_ms, ruta, hash_sha256, tamano_bytes, "
            " ancho, alto, evento_id, ia_persona, ia_score, imagen_purgada, origen) "
            "VALUES (?, ?, 'imagenes/dev/x.jpg', ?, 5000, 320, 240, ?, 1, 0.77, 0, 'real')",
            (DEV, NOW, "a" * 64, real),
        )
        # Clima: una lectura cercana (+-1 h) y otra fuera de ventana.
        con.execute(
            "INSERT INTO fuentes_externas (fuente, ts_ms, temperatura_c, humedad_pct, "
            " precipitacion_mm, viento_kmh) VALUES ('openmeteo', ?, 24.5, 61.0, 0.0, 8.0)",
            (NOW + 600_000,),
        )
        con.execute(
            "INSERT INTO fuentes_externas (fuente, ts_ms, temperatura_c, humedad_pct, "
            " precipitacion_mm, viento_kmh) VALUES ('openmeteo', ?, 99.0, 1.0, 9.9, 99.0)",
            (NOW + 7_200_000,),
        )
        # Config: dos eventos en la ventana, uno de provision.
        con.execute(
            "INSERT INTO config_eventos (device_id, ts_ms, tipo, detalle, origen) "
            "VALUES (?, ?, 'AP_FALLBACK', 'reintentos=3', 'real')",
            (DEV, NOW + 30_000),
        )
        con.execute(
            "INSERT INTO config_eventos (device_id, ts_ms, tipo, detalle, origen) "
            "VALUES (?, ?, 'CFG_GUARDADA', 'ssid_len=12 pass_len=0', 'real')",
            (DEV, NOW + 50_000),
        )
        # Verdad terreno que contiene el evento.
        con.execute(
            "INSERT INTO validacion_sesiones (sesion, inicio_ms, fin_ms, verdad_terreno) "
            "VALUES ('s1', ?, ?, 'motion')",
            (NOW - 10_000, NOW + 60_000),
        )
        con.commit()
    finally:
        con.close()
    return db


def _leer_csv(ruta: Path) -> tuple[list[str], list[dict[str, str]]]:
    with open(ruta, newline="", encoding="utf-8") as fh:
        lector = csv.DictReader(fh)
        return list(lector.fieldnames or []), list(lector)


def _exportar(db: Path, out: Path) -> int:
    exportador = export_consolida.ExportadorConsolidado(db)
    try:
        return exportador.exportar(out)
    finally:
        exportador.cerrar()


# --- 1. Esquema exacto + solo real ----------------------------------------


def test_consolidado_esquema_exacto_y_solo_real(tmp_path):
    db = _db_completa(tmp_path)
    out = tmp_path / "consolidado.csv"
    assert _exportar(db, out) == 1

    cabecera, filas = _leer_csv(out)
    assert tuple(cabecera) == export_consolida.COLUMNAS
    assert len(export_consolida.COLUMNAS) == 29
    assert len(filas) == 1  # el sintetico y la telemetria huerfana NO salen

    fila = filas[0]
    assert fila["device_id"] == DEV
    assert fila["tipo"] == "MOVIMIENTO_INICIO"
    assert fila["origen"] == "real"
    assert float(fila["score"]) == pytest.approx(0.93)
    assert fila["ts_ms"] == str(NOW)


def test_consolidado_joins_por_ventana(tmp_path):
    db = _db_completa(tmp_path)
    out = tmp_path / "consolidado.csv"
    _exportar(db, out)
    _, filas = _leer_csv(out)
    fila = filas[0]

    # Telemetria mas cercana (+2 s), no la huerfana ni la del sintetico.
    assert float(fila["detection_avg_us"]) == pytest.approx(123.5)
    assert float(fila["runtime_load_percent"]) == pytest.approx(45.0)
    assert float(fila["free_memory_kb"]) == pytest.approx(180_000.0)
    assert float(fila["csi_accepted_pps"]) == pytest.approx(100.0)
    assert float(fila["csi_occupancy"]) == pytest.approx(0.9)
    assert int(fila["wifi_rssi_dbm"]) == -55
    assert float(fila["die_temp_c"]) == pytest.approx(52.5)
    # Imagen por FK evento_id.
    assert fila["tiene_imagen"] == "1"
    assert int(fila["ia_persona"]) == 1
    assert float(fila["ia_score"]) == pytest.approx(0.77)
    assert fila["imagen_purgada"] == "0"
    # Clima dentro de +-1 h (nunca la lectura de +2 h).
    assert float(fila["temperatura_c"]) == pytest.approx(24.5)
    assert float(fila["humedad_pct"]) == pytest.approx(61.0)
    assert float(fila["precipitacion_mm"]) == pytest.approx(0.0)
    assert float(fila["viento_kmh"]) == pytest.approx(8.0)
    # Config en +-60 s.
    assert fila["n_provision"] == "2"
    assert fila["hora_provision"] == "1"
    # Verdad terreno por intervalo.
    assert fila["verdad_terreno"] == "motion"
    # Derivados temporales.
    momento = datetime.fromtimestamp(NOW / 1000)
    assert int(fila["hora"]) == momento.hour
    assert int(fila["dia_semana"]) == momento.weekday()
    assert fila["franja_horaria"] == export_consolida._franja_horaria(momento.hour)
    # eventos no tiene columna sesion_id hoy -> null.
    assert fila["sesion_id"] == ""


# --- 2. pandas -------------------------------------------------------------


def test_consolidado_carga_en_pandas(tmp_path):
    pd = pytest.importorskip("pandas")
    db = _db_completa(tmp_path)
    out = tmp_path / "consolidado.csv"
    _exportar(db, out)

    df = pd.read_csv(out)
    assert list(df.columns) == list(export_consolida.COLUMNAS)
    assert len(df) == 1
    assert df.loc[0, "origen"] == "real"
    assert df.loc[0, "tiene_imagen"] == 1
    assert df.loc[0, "n_provision"] == 2
    # Columnas numericas realmente parseadas.
    assert df["detection_avg_us"].dtype.kind == "f"


# --- 3. Nulos sin romperse -------------------------------------------------


def _db_solo_evento(tmp_path: Path) -> Path:
    db = tmp_path / "solo_evento.db"
    con = _conectar(db)
    try:
        con.execute(
            "INSERT INTO eventos (device_id, ts_ms, tipo, score, duracion_ms, origen) "
            "VALUES (?, ?, 'FALLA', NULL, NULL, 'real')",
            (DEV, NOW),
        )
        # Fuera de ventana: no deben hacer join.
        con.execute(
            "INSERT INTO telemetria (device_id, ts_ms, detection_avg_us, origen) "
            "VALUES (?, ?, 55.0, 'real')",
            (DEV, NOW + 31_000),
        )
        con.execute(
            "INSERT INTO fuentes_externas (fuente, ts_ms, temperatura_c) "
            "VALUES ('openmeteo', ?, 30.0)",
            (NOW + 3_700_000,),
        )
        con.commit()
    finally:
        con.close()
    return db


def test_consolidado_joins_null_sin_romperse(tmp_path):
    db = _db_solo_evento(tmp_path)
    out = tmp_path / "consolidado.csv"
    assert _exportar(db, out) == 1

    _, filas = _leer_csv(out)
    fila = filas[0]
    assert fila["tipo"] == "FALLA"
    assert fila["score"] == "" and fila["duracion_ms"] == ""
    for columna in (
        "detection_avg_us",
        "runtime_load_percent",
        "free_memory_kb",
        "csi_accepted_pps",
        "csi_occupancy",
        "wifi_rssi_dbm",
        "die_temp_c",
        "temperatura_c",
        "humedad_pct",
        "precipitacion_mm",
        "viento_kmh",
        "verdad_terreno",
        "sesion_id",
    ):
        assert fila[columna] == "", columna
    assert fila["tiene_imagen"] == "0"
    assert fila["imagen_purgada"] == "0"
    assert fila["n_provision"] == "0"
    assert fila["hora_provision"] == "0"
    assert fila["origen"] == "real"


# --- 4. Reproducibilidad y DB parcial -------------------------------------


def test_consolidado_reproducible(tmp_path):
    db = _db_completa(tmp_path)
    out1 = tmp_path / "a.csv"
    out2 = tmp_path / "b.csv"
    _exportar(db, out1)
    _exportar(db, out2)
    assert out1.read_bytes() == out2.read_bytes()


def test_consolidado_tolera_db_sin_tablas_f24(tmp_path):
    """DB antigua (sin imagenes/config_eventos/fuentes): exporta con null/0."""
    db = tmp_path / "vieja.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute(
            "CREATE TABLE eventos (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "device_id TEXT NOT NULL, ts_ms INTEGER NOT NULL, tipo TEXT NOT NULL, "
            "score REAL, duracion_ms INTEGER, origen TEXT NOT NULL DEFAULT 'real')"
        )
        con.execute(
            "INSERT INTO eventos (device_id, ts_ms, tipo, score, duracion_ms, origen) "
            "VALUES (?, ?, 'MOVIMIENTO_FIN', 0.4, 1000, 'real')",
            (DEV, NOW),
        )
        con.commit()
    finally:
        con.close()

    out = tmp_path / "consolidado.csv"
    assert _exportar(db, out) == 1
    _, filas = _leer_csv(out)
    assert filas[0]["tiene_imagen"] == "0"
    assert filas[0]["n_provision"] == "0"
    assert filas[0]["temperatura_c"] == ""