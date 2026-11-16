# -*- coding: utf-8 -*-
"""F5a — tests del EDA formal (eda.py): funciones puras, sin WSL ni BD (regla 8: fixtures
pequenos en aulasense/.tmp/test_f5a/ si hicieran falta; aqui todo es en memoria)."""
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]          # aulasense/
sys.path.insert(0, str(RAIZ / "analysis"))

import eda                                          # noqa: E402


# ---------------------------------------------------------------- histogramas
def test_mediana_desde_hist_con_claves_str_de_json():
    # JSON convierte claves int->str: el helper debe normalizar (bug real encontrado)
    bins = {"0": 10, "1": 10}                       # score en [0,0.1)
    assert eda.mediana_desde_hist(bins) == 0.025    # centro del bin donde cae el 50 %
    assert eda.mediana_desde_hist({}) is None
    assert eda.percentil_desde_hist({"0": 10, "1": 10}, 0.9) == pytest.approx(0.075)
    assert eda.percentil_desde_hist({}, 0.5) is None


# ---------------------------------------------------------------- pps / ACF
def test_rellenar_pps_densifica_huecos_con_cero():
    filas = [[0, 40], [60_000, 40], [240_000, 40]]  # hueco en 120k y 180k
    t0s, counts = eda.rellenar_pps(filas)
    assert t0s == [0, 60_000, 120_000, 180_000, 240_000]
    assert counts == [40, 40, 0, 0, 40]
    assert eda.rellenar_pps([]) == ([], [])


def test_autocorrelacion_alternada_y_constante():
    serie = [1.0, 0.0] * 50
    acf = eda.autocorrelacion(serie, 4)
    assert acf[0] == 1.0
    assert acf[1] < -0.9 and acf[2] > 0.9          # alternancia: lag1 negativa, lag2 positiva
    acf_const = eda.autocorrelacion([5.0] * 30, 3)
    assert acf_const[0] == 1.0 and acf_const[1] is None   # var 0 => sin correlacion


# ---------------------------------------------------------------- outliers con motivo
def test_etiquetar_outliers_motivos():
    huecos = [{"t_ini_ms": 1, "t_fin_ms": 300_001, "gap_s": 300.0}]
    idle_altos = [[10, 20, 0.9], [11, 21, 0.8]]
    ts_ano = [{"tabla": "imagenes", "id": 56, "ts_ms": 3_589_525_831_468}]
    out = eda.etiquetar_outliers(huecos, idle_altos, ts_ano, n_hueco=2)
    tipos = {o["tipo"] for o in out}
    assert tipos == {"HUECO_SERIE", "IDLE_SCORE_ALTO", "TS_MS_ANOMALO"}
    assert all(o["motivo"] for o in out)           # TODO outlier lleva motivo
    assert out[0]["gap_max_s"] == 300.0
    assert eda.etiquetar_outliers([], [], [], 0) == []


# ---------------------------------------------------------------- score por hora
def test_analizar_horas_desde_hist_y_stats():
    wsl = {
        "hist_hora": [[9, 0, 100], [9, 19, 4], [10, 0, 50]],   # hora 9: 4 scores altos
        "stats_hora": [[9, 104, 8], [10, 50, 0]],
    }
    r = eda.analizar_horas(wsl)
    h9 = r["por_hora_local"][9]
    assert h9["n_eval"] == 104 and h9["pct_motion"] == 7.6923
    assert h9["frac_over_05"] == 0.0385            # 4/104
    assert r["hora_pico_motion"] == 9
    assert "UTC-5" in r["nota"]


# ---------------------------------------------------------------- efecto RSSI
def test_analizar_rssi_corr_y_bins():
    wsl = {
        "rssi_hist": [[-30, 10], [-18, 5]],
        "eval5": [[0, 100, 10, 0.2], [300_000, 100, 0, 0.01]],
        "rssi5": [[0, -30.0, 45.0, 20.0, 3.9], [300_000, -18.0, 44.0, 18.0, 3.8]],
    }
    r = eda.analizar_rssi(wsl)
    assert r["n_telemetria"] == 15 and r["rssi_min"] == -30 and r["rssi_max"] == -18
    assert r["n_buckets_cruce"] == 2
    assert r["score_por_rssi_dbm"][-30]["score_medio"] == 0.2
    # con n<3 puntos la correlacion es None (guarda determinista documentada)
    assert r["corr_bucket_score_vs_rssi"] is None
    # con 3 puntos si se computa
    wsl["eval5"].append([600_000, 100, 5, 0.1])
    wsl["rssi5"].append([600_000, -24.0, 44.5, 19.0, 3.85])
    r3 = eda.analizar_rssi(wsl)
    assert r3["corr_bucket_score_vs_rssi"] is not None


# ---------------------------------------------------------------- cobertura GT
def test_cobertura_gt_densidad_y_fotos():
    wsl = {
        "eval5": [[0, 100, 50, 0.5], [300_000, 100, 0, 0.0]],
        "fotos": [[1, 100_000], [2, 700_000]],
        "onsets": [[10, 50_000, 0.9]],
    }
    gt = [{"sesion": "S", "inicio_ms": 0, "fin_ms": 300_000, "verdad_terreno": "motion"},
          {"sesion": "S", "inicio_ms": 300_000, "fin_ms": 600_000, "verdad_terreno": "motion"}]
    r = eda.cobertura_gt(wsl, gt)
    assert r["n_tramos_motion"] == 2
    assert r["tramos"][0]["densidad_motion"] == 0.5
    assert r["tramos"][1]["densidad_motion"] == 0.0
    assert r["tramos"][0]["n_fotos_dentro"] == 1   # foto 1 dentro del tramo 0
    assert r["pct_tramos_cubiertos"] == 50.0
    # tramo sin evaluaciones = sin-dato, no idle (nota honesta)
    r2 = eda.cobertura_gt({"eval5": [], "fotos": [], "onsets": []}, gt)
    assert r2["tramos"][0]["densidad_motion"] is None


def test_cobertura_gt_vision_error_evidencia_fisica_con_nota():
    """[MEDIO auditoria 2026-11-15] Decisión (b): una foto con vision_estado='error'
    (JPEG irrecoverable) SIGUE contando como evidencia física de captura al onset
    (`n_fotos_dentro`), pero la nota del informe la separa del material de visión."""
    wsl = {"eval5": [[0, 100, 50, 0.5]],
           "fotos": [[1, 100_000], [2, 120_000]],   # la 2 está rota (error)
           "fotos_error": [[2, 120_000]],
           "onsets": []}
    gt = [{"sesion": "S", "inicio_ms": 0, "fin_ms": 300_000, "verdad_terreno": "motion"}]
    r = eda.cobertura_gt(wsl, gt)
    assert r["tramos"][0]["n_fotos_dentro"] == 2       # la rota cuenta como evidencia
    assert r["n_fotos_vision_error"] == 1
    assert ("1 con JPEG irrecoverable (error) contadas como evidencia fisica de captura, "
            "no como material vision") in r["nota"]
    # Sin filas 'error' la nota lo dice y el contador es 0 (modo --sin-wsl incluido).
    r0 = eda.cobertura_gt({**wsl, "fotos_error": []}, gt)
    assert r0["n_fotos_vision_error"] == 0
    assert "sin fotos con JPEG irrecoverable (error)" in r0["nota"]
