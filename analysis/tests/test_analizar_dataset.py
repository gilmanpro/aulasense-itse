# -*- coding: utf-8 -*-
"""F5a — tests minimos del pipeline analizar_dataset.py (parser GT, confusion, latencia,
apareo foto↔onset). Fixiros pequenos SOLO en aulasense/.tmp/test_f5a/ (regla 8), nunca BD."""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]          # aulasense/
sys.path.insert(0, str(RAIZ / "analysis"))

import analizar_dataset as ad                        # noqa: E402

TMP = RAIZ / ".tmp" / "test_f5a"


# ---------------------------------------------------------------- confusion
def test_confusion_motion_only_recall():
    gt = [
        {"sesion": "S", "inicio_ms": 1000, "fin_ms": 2000, "verdad_terreno": "motion"},
        {"sesion": "S", "inicio_ms": 3000, "fin_ms": 4000, "verdad_terreno": "motion"},
        {"sesion": "S", "inicio_ms": 5000, "fin_ms": 6000, "verdad_terreno": "motion"},
    ]
    stats = [
        {"n_eval": 10, "n_motion_dentro": 4},   # TP
        {"n_eval": 10, "n_motion_dentro": 0},   # FN
        {"n_eval": 0, "n_motion_dentro": 0},    # sin datos -> excluido del denominador
    ]
    c = ad.matriz_confusion(gt, stats)
    assert c["tp"] == 1 and c["fn"] == 1
    assert c["tramos_gt_sin_evaluaciones"] == 1
    assert c["recall_motion"] == 0.5
    assert c["fp"] is None and c["fpr"] is None          # pasivo: idle no observable
    assert "RECALL-ONLY" in c["nota_honest"]


def test_confusion_con_marcas_idle_calcula_fpr_y_precision():
    gt = [
        {"sesion": "S", "inicio_ms": 1000, "fin_ms": 2000, "verdad_terreno": "motion"},
        {"sesion": "S", "inicio_ms": 3000, "fin_ms": 4000, "verdad_terreno": "idle"},
        {"sesion": "S", "inicio_ms": 5000, "fin_ms": 6000, "verdad_terreno": "idle"},
        {"sesion": "S", "inicio_ms": 7000, "fin_ms": 8000, "verdad_terreno": "motion"},
    ]
    stats = [
        {"n_eval": 10, "n_motion_dentro": 5},   # TP
        {"n_eval": 10, "n_motion_dentro": 2},   # FP
        {"n_eval": 10, "n_motion_dentro": 0},   # TN
        {"n_eval": 10, "n_motion_dentro": 0},   # FN
    ]
    c = ad.matriz_confusion(gt, stats)
    assert c["tp"] == 1 and c["fp"] == 1 and c["tn"] == 1 and c["fn"] == 1
    assert c["fpr"] == 0.5 and c["precision"] == 0.5 and c["recall_motion"] == 0.5
    assert "nota_honest" not in c              # con idle real desaparece la nota recall-only


# ---------------------------------------------------------------- latencia
def test_latencia_truncacion_y_anomalos():
    pares = [(1, -500.0), (2, -300.0), (3, -700.0), (4, -1.79e12)]  # id4 = fire-test ts x2
    r = ad.estadisticas_latencia(pares, "imagenes")
    assert r["n"] == 3                                    # anomalo excluido
    assert r["mediana_ms"] == -500.0
    assert r["mediana_corr_truncacion_ms"] == 0.0         # +500 ms de correccion
    assert [a["id"] for a in r["anomalos_excluidos"]] == [4]


# ---------------------------------------------------------------- apareo
def test_aparear_fotos_onsets_ventana_20s():
    onsets = [{"id": 10, "ts_ms": 0}, {"id": 11, "ts_ms": 100_000}]
    fotos = [{"id": "a", "ts_ms": 2_000},        # match con 10 (lag +2000)
             {"id": "b", "ts_ms": 119_000},      # match con 11 (lag -1000)
             {"id": "c", "ts_ms": 130_001}]      # fuera de ±20 s
    r = ad.aparear_fotos_onsets(fotos, onsets)
    assert r["emparejadas"] == 2
    assert r["fotos_sin_match"] == ["c"]
    assert r["onsets_con_foto_pct"] == 100.0
    assert r["lags_ms"]["n"] == 2 and r["lags_ms"]["p0"] == 2000.0 and r["lags_ms"]["p100"] == 19000.0


# ---------------------------------------------------------------- parser validacion csv
def test_cargar_validacion_y_ground_truth_union(tmp_path=None):
    TMP.mkdir(parents=True, exist_ok=True)
    csv_path = TMP / "validacion_fixture.csv"
    csv_path.write_text(
        "sesion,inicio_ms,fin_ms,verdad_terreno,hubo_movimiento_real,fuente,notas,recibido_en\n"
        'FX,1000,2000,motion,si,pasivo_foto_corroborada,"x",2026-01-01 00:00:00\n'
        "FX,9000,10000,idle,no,activa_s2,marca directa,2026-01-01 00:00:00\n"
        "FX,11000,12000,sin-dato,,otras,filas no GT se ignoran,\n",
        encoding="utf-8")
    filas = ad.cargar_validacion([csv_path])
    assert [f["verdad_terreno"] for f in filas] == ["motion", "idle"]  # sin-dato descartado
    # union con registros: sesion sin filas CSV se toma del jsonl gt; sesion con CSV NO se duplica
    sesiones = {"FX": {"gt": [{"inicio_ms": 1000, "fin_ms": 2000, "fotos": [1]}]},
                "FY": {"gt": [{"inicio_ms": 5000, "fin_ms": 6000, "fotos": [7]}]}}
    for s in sesiones.values():
        s.setdefault("archivos", [])
    gt = ad.construir_ground_truth({"FX": {**sesiones["FX"], "fotos": [], "eventos": [],
                                            "resumen": [], "cabeceras": []},
                                    "FY": {**sesiones["FY"], "fotos": [], "eventos": [],
                                            "resumen": [], "cabeceras": []}}, filas)
    tramos = [(g["sesion"], g["inicio_ms"]) for g in gt]
    assert ("FX", 1000) in tramos and ("FY", 5000) in tramos
    assert tramos.count(("FX", 1000)) == 1     # sin duplicado CSV vs jsonl
    fy = [g for g in gt if g["sesion"] == "FY"][0]
    assert fy["verdad_terreno"] == "motion" and fy["fuente"] == "gt_registro_jsonl"
