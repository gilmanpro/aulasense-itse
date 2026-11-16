# -*- coding: utf-8 -*-
"""F5a — tests del modelo/indicador de ocupacion (modelo_ocupacion.py): funciones puras
y entrenamiento determinista contra filas sinteticas EN MEMORIA (nunca BD)."""
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "analysis"))

import modelo_ocupacion as mo                       # noqa: E402

W = mo.VENTANA_MS


def _wsl_fake():
    """Dos noches de ventanas: quietas (idle) y una manana con motion periodico."""
    eval_w, tel_w, img_w = [], [], []
    for i in range(40):
        t = i * W
        mot = 10 if (i % 4 == 1) else 0             # motion alternado determinista
        eval_w.append([t, 600, mot, 0.02 + 0.5 * (mot > 0), 30 if mot else 1])
        tel_w.append([t, 45.0 + i % 3, 20.0, -25.0, 3.9])
        if mot:
            img_w.append([t, 2, 0, None])
    return {"eval_w": eval_w, "tel_w": tel_w, "img_w": img_w,
            "clima": [[5 * W, 20.0, 60.0, 0.0, 10.0]],
            "prov": [[2 * W, "CFG_GUARDADA"]],
            "onsets": [[1, 1 * W + 60_000, 0.9], [2, 5 * W + 60_000, 0.7],
                       [3, 30 * W + 60_000, 0.2]],
            "totals": [40 * 600, 0, 39 * W]}


# ---------------------------------------------------------------- ventanas/features
def test_construir_ventanas_unen_fuentes():
    v = mo.construir_ventanas(_wsl_fake())
    assert v[W]["n_motion"] == 10 and v[W]["n_eval"] == 600
    assert v[W]["die_temp"] == 46.0 and v[W]["n_fotos"] == 2
    assert v[0]["n_fotos"] == 0 and v[0]["n_ia"] == 0


def test_indicador_ocupacion_ge1_motion():
    filas, meta = mo.construir_dataset(_wsl_fake())
    for f in filas:
        assert f["ocupada"] == (1 if f["n_motion"] >= 1 else 0)
    assert meta["n_filas_omitidas"] == 1            # ultima ventana sin t+1
    assert meta["n_filas_entrenables"] == 39


def test_features_null_safe_sin_romperse():
    v = mo.construir_ventanas(_wsl_fake())
    f = mo.features_ventana(0, v, [], [])            # sin clima ni provision
    assert f["frac_ia_persona"] is None              # ia null -> None (imputable)
    assert f["clima_temp_c"] is None and f["hora_provision"] == 0
    assert f["tiene_imagen"] == 0
    f2 = mo.features_ventana(2 * W, v, _wsl_fake()["clima"], _wsl_fake()["prov"])
    assert f2["hora_provision"] == 1                 # evento de config dentro de la ventana
    assert f2["clima_temp_c"] == 20.0                # ±1 h mas cercano


def test_filtro_material_vision_excluye_error_y_omitido():
    """[MEDIO auditoria 2026-11-15] Decisión (a): las features que dependen de la
    imagen (tiene_imagen/n_fotos/frac_ia_persona) NO cuentan filas sin material de
    visión: 'error' (JPEG irrecoverable) junto a 'omitido' (fire-test) quedan fuera;
    'pendiente' o estado NULL sí cuentan (la foto existe y está por describir)."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE imagenes (ts_ms INTEGER, origen TEXT, vision_estado TEXT, "
                "descripcion TEXT, ia_persona INTEGER)")
    con.executemany("INSERT INTO imagenes VALUES (?,?,?,?,?)", [
        (0, "real", "hecho", "persona", 1),
        (0, "real", "pendiente", None, None),
        (0, "real", "error", None, None),           # JPEG irrecoverable (SPEC §404-407)
        (0, "real", "omitido", "FIRE-TEST", None),  # fire-test
        (0, "real", None, None, None),              # sin estado: cuenta
        (0, "sintetico", "hecho", "", 1),           # sintetico: fuera
    ])
    n = con.execute("SELECT COUNT(*) FROM imagenes WHERE origen='real' AND "
                    + mo.FILTRO_MATERIAL_VISION).fetchone()[0]
    con.close()
    assert n == 3
    assert "'error'" in mo.FILTRO_MATERIAL_VISION
    assert "FIRE-TEST" in mo.FILTRO_MATERIAL_VISION


# ---------------------------------------------------------------- entrenamiento
def test_entrenar_determinista_y_split_temporal():
    filas, _ = mo.construir_dataset(_wsl_fake())
    r1 = mo.entrenar(filas)
    r2 = mo.entrenar(filas)
    r1.pop("predicciones_test"); r2.pop("predicciones_test")
    assert r1 == r2                                  # misma salida byte a byte
    assert r1["split"].startswith("temporal 80/20")
    assert r1["modelos"]["decision_tree"]["n_train"] == 31
    assert "nota_honest_e10" in r1


def test_target_es_ventana_siguiente():
    filas, _ = mo.construir_dataset(_wsl_fake())
    by_t = {f["t_ms"]: f for f in filas}
    for t, f in by_t.items():
        nxt = t + W
        esperado = 1 if (nxt in by_t and by_t[nxt]["n_motion"] >= 1) else 0
        assert f["y_motion_siguiente"] == esperado


# ---------------------------------------------------------------- validacion GT
def test_validar_gt_cobertura_y_curva_pr():
    filas, _ = mo.construir_dataset(_wsl_fake())
    gt = [{"sesion": "FX", "inicio_ms": W, "fin_ms": W + 30_000, "verdad_terreno": "motion"},
          {"sesion": "FX", "inicio_ms": 20 * W, "fin_ms": 20 * W + 30_000,
           "verdad_terreno": "motion"},
          {"sesion": "FX", "inicio_ms": 10 * W, "fin_ms": 10 * W + 30_000,
           "verdad_terreno": "idle"}]
    onsets = [[1, W + 10_000, 0.9], [2, 30 * W + 60_000, 0.2]]
    v = mo.validar_gt(filas, gt, onsets)
    assert v["n_tramos_gt_motion"] == 2 and v["n_tramos_gt_idle"] == 1
    assert v["tramos_gt_no_cubiertos"]               # tramo en 20W no tiene ventana ocupada
    assert v["indicador_cobertura_pct"] == 50.0
    cur = v["curva_pr_por_umbral"]
    assert cur[0]["umbral"] == 0.05 and cur[-1]["umbral"] == 0.95
    assert cur[0]["tramos_cubiertos"] == 1 and cur[0]["onsets_fuera_gt"] == 1
    # hook idle REAL: con tramos idle en el GT la precision deja de ser solo piso
    assert cur[0]["precision"] == 1.0 and cur[0]["precision_piso"] == 0.5
    recalls = [c["recall"] for c in cur]
    assert all(recalls[i] >= recalls[i + 1] for i in range(len(recalls) - 1))  # no creciente
    assert "PISO" in v["nota"]                       # honestidad: precision es piso
