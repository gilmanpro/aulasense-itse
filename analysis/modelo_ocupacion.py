#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F5a — Modelo/indicador de ocupación (C5, SPEC §10, PLAN F5a).

Definiciones (SPEC §10 fija la métrica concreta — no se sobre-ingeniería):
  * INDICADOR de ocupación dinámica (el más simple, alineado al método de confusión del
    pipeline base): ventana de 10 MINUTOS = OCUPADA si contiene >= 1 evaluacion
    state='motion' (GT pasivo corroborado por foto; idle != sala vacia — AGENTS 11).
  * MODELO predictivo (D8): features hora/dia/actividad movil + die temp + Open-Meteo
    + F24 opcionales (tiene_imagen, ia_persona, hora_provision) SIN ROMPERSE si null
    -> target = motion en la VENTANA SIGUIENTE de 10 min. LogisticRegression +
    DecisionTreeClassifier, split TEMPORAL 80/20, SOLO origen='real'. Metricas CON
    tamano muestral y nota honesta (E10: dataset corto, 1 manana con actividad).
  * Validacion contra ground truth F4S1B (docs/validacion_sesion1.csv): cobertura del
    indicador sobre tramos GT + precision/recall por umbral de score a nivel onset.
    S2/S3: autodescubrimiento por los mismos globs del pipeline (dataset/sesion_*,
    docs/validacion_sesion*.csv) — al llegar, entran solos, cero cambio de codigo.

Convenciones heredadas de analizar_dataset.py (NO se modifica): puente WSL solo-lectura
(script por stdin, payload por argv, mode=ro, venv->python3), r4/percentiles, GT union.

Salidas:
  - docs/analisis/modelo_ocupacion.json  (determinista: hashes + unico generado_utc)
  - docs/analisis/graficos/ocupacion_pr_umbral.png
  - docs/analisis/graficos/ocupacion_modelo_vs_gt.png
  - docs/analisis/graficos/ocupacion_feature_importance.png

Uso:  C:\\Python315\\python.exe analysis/modelo_ocupacion.py [--no-figuras]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import analizar_dataset as ad  # noqa: E402

VERSION_MODELO = "f5a-2.0"
DISTRO_WSL = ad.DISTRO_WSL
DB_WSL = ad.DB_WSL
PY_VENV_WSL = ad.PY_VENV_WSL
PY_SYS_WSL = ad.PY_SYS_WSL

VENTANA_MS = 600_000            # 10 min (target: motion en ventana siguiente)
OFFSET_HORA_S = -5 * 3600       # hora local del aula (cabecera jsonl -05:00)
UMBRALES_PR = [round(0.05 * k, 2) for k in range(1, 20)]  # 0.05..0.95
r4 = ad.r4


def sha256_de(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


# ---------------------------------------------------------------- consulta WSL
CONSULTA_MODELO_PY = r'''# -*- coding: utf-8 -*-
# Sonda SOLO-LECTURA (mode=ro) para el modelo de ocupacion: ventanas de 10 min con
# evaluaciones/telemetria/imagenes/clima/onsets. Mismo patron del pipeline base.
import json, sqlite3, sys

payload = json.loads(sys.argv[1])
con = sqlite3.connect("file:" + payload["db"] + "?mode=ro", uri=True)
q = lambda s, p=(): con.execute(s, p).fetchall()
W = payload["ventana_ms"]
res = {"wsl_ok": True}

res["eval_w"] = q("SELECT CAST(ts_ms/? AS INTEGER)*? t, COUNT(*), SUM(state='motion'), "
                  "AVG(score), SUM(score >= 0.5) FROM evaluaciones WHERE origen='real' "
                  "GROUP BY t ORDER BY t", (W, W))
res["tel_w"] = q("SELECT CAST(ts_ms/? AS INTEGER)*? t, AVG(die_temp_c), "
                 "AVG(runtime_load_percent), AVG(wifi_rssi_dbm), AVG(csi_accepted_pps) "
                 "FROM telemetria WHERE origen='real' GROUP BY t ORDER BY t", (W, W))
res["img_w"] = q("SELECT CAST(ts_ms/? AS INTEGER)*? t, COUNT(*), "
                 "SUM(ia_persona IS NOT NULL), SUM(ia_persona=1) FROM imagenes "
                 "WHERE origen='real' AND COALESCE(vision_estado,'')!='omitido' "
                 "AND UPPER(COALESCE(descripcion,'')) NOT LIKE '%FIRE-TEST%' "
                 "GROUP BY t ORDER BY t", (W, W))
res["clima"] = q("SELECT ts_ms, temperatura_c, humedad_pct, precipitacion_mm, viento_kmh "
                 "FROM fuentes_externas ORDER BY ts_ms")
res["prov"] = q("SELECT ts_ms, tipo FROM config_eventos WHERE origen='real' "
                "AND tipo IN ('CFG_GUARDADA','AP_FALLBACK') ORDER BY ts_ms")
res["onsets"] = q("SELECT id, ts_ms, score FROM eventos WHERE origen='real' "
                  "AND tipo='MOVIMIENTO_INICIO' ORDER BY ts_ms")
res["totals"] = q("SELECT COUNT(*), MIN(ts_ms), MAX(ts_ms) FROM evaluaciones WHERE origen='real'")[0]
print(json.dumps(res, ensure_ascii=False))
'''


def consultar_wsl_modelo(payload: dict) -> dict:
    errs = []
    for py in (PY_VENV_WSL, PY_SYS_WSL):
        r = subprocess.run(["wsl", "-d", DISTRO_WSL, "-u", "root", "--", py, "-",
                            json.dumps(payload, ensure_ascii=False)],
                           input=CONSULTA_MODELO_PY, capture_output=True, text=True,
                           encoding="utf-8", timeout=300)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
        errs.append(f"{py}: {(r.stderr or r.stdout).strip()[:200]}")
    raise RuntimeError("consulta WSL modelo fallo en ambos interpretes:\n" + "\n".join(errs))


# ---------------------------------------------------------------- construccion de dataset
def construir_ventanas(wsl: dict) -> dict[int, dict]:
    """Ventanas de 10 min desde las evaluaciones reales (fuente primaria del indicador)."""
    v: dict[int, dict] = {}
    for t, n, m, avg_s, n_umb in wsl["eval_w"]:
        t = int(t)
        v[t] = {"n_eval": int(n), "n_motion": int(m or 0), "score_medio": avg_s,
                "frac_score_umb": (int(n_umb or 0) / n) if n else None}
    for row in wsl["tel_w"]:
        t = int(row[0])
        if t in v:
            v[t].update(die_temp=row[1], runtime_load=row[2], rssi=row[3], pps_csi=row[4])
    for t, n, n_ia, n_persona in wsl["img_w"]:
        t = int(t)
        if t in v:
            v[t]["n_fotos"] = int(n)
            v[t]["n_ia"] = int(n_ia or 0)
            v[t]["n_persona"] = int(n_persona or 0)
    for t in v:
        v[t].setdefault("n_fotos", 0)
        v[t].setdefault("n_ia", 0)
        v[t].setdefault("n_persona", 0)
    return v


def features_ventana(t: int, v: dict, clima: list, prov: list) -> dict:
    """Fila de features de la ventana t (para predecir t+VENTANA_MS).
    Features F24/OPCIONALES: tiene_imagen, ia_persona, hora_provision — si no hay datos
    van 0/None con imputacion documentada (no rompen el entrenamiento)."""
    d = v[t]
    hora_local = ((t // 1000 + OFFSET_HORA_S) // 3600) % 24
    dia = ((t // 1000 + OFFSET_HORA_S) // 86400 + 3) % 7   # lunes=0 (epoch 1970-01-01=jueves)
    ang = 2 * math.pi * hora_local / 24.0
    # clima: fila mas cercana dentro de ±1 h (fuentes_externas; default si no hay)
    cl = None
    best = 3600_001
    for row in clima:
        dd = abs(int(row[0]) - t)
        if dd < best:
            best, cl = dd, row
    n_ia = d.get("n_ia", 0)
    return {
        "hora_sin": r4(math.sin(ang)), "hora_cos": r4(math.cos(ang)),
        "dia_semana": dia,
        "actividad_movil": r4(d["n_motion"] / d["n_eval"]) if d["n_eval"] else 0.0,
        "frac_score_umb": r4(d["frac_score_umb"]),
        "score_medio": r4(d["score_medio"]),
        "pps_eval": r4(d["n_eval"] / (VENTANA_MS / 1000.0)),
        "die_temp_c": r4(d.get("die_temp")),
        "runtime_load_pct": r4(d.get("runtime_load")),
        "rssi_dbm": r4(d.get("rssi")),
        "csi_pps": r4(d.get("pps_csi")),
        "tiene_imagen": 1 if d.get("n_fotos", 0) > 0 else 0,
        "n_fotos": d.get("n_fotos", 0),
        "frac_ia_persona": (r4(d.get("n_persona", 0) / n_ia) if n_ia else None),  # null-safe
        "hora_provision": 1 if any(t <= int(p[0]) < t + VENTANA_MS for p in prov) else 0,
        "clima_temp_c": r4(cl[1]) if cl and best <= 3600_000 and cl[1] is not None else None,
        "clima_humedad_pct": r4(cl[2]) if cl and best <= 3600_000 and cl[2] is not None else None,
        "clima_viento_kmh": r4(cl[4]) if cl and best <= 3600_000 and cl[4] is not None else None,
        "n_eval": d["n_eval"],
        "ocupada": 1 if d["n_motion"] >= 1 else 0,          # INDICADOR (>=1 motion)
        "n_motion": d["n_motion"],
    }


def construir_dataset(wsl: dict) -> tuple[list[dict], dict]:
    v = construir_ventanas(wsl)
    ts = sorted(v)
    filas, omitidas = [], []
    for t in ts:
        if (t + VENTANA_MS) not in v:
            omitidas.append({"t_ms": t, "motivo": "no hay ventana consecutiva t+10min (hueco)"})
            continue
        f = features_ventana(t, v, wsl["clima"], wsl["prov"])
        f["t_ms"] = t
        f["y_motion_siguiente"] = 1 if v[t + VENTANA_MS]["n_motion"] >= 1 else 0
        filas.append(f)
    meta = {"n_ventanas_eval": len(ts), "n_filas_entrenables": len(filas),
            "n_filas_omitidas": len(omitidas), "omitidas_ejemplos": omitidas[:10],
            "nota": ("ventanas consecutivas (sin huecos de nodo offline); target = "
                     ">=1 evaluacion motion en la ventana SIGUIENTE (misma regla del "
                     "indicador, metodo del pipeline base)")}
    return filas, meta


# ---------------------------------------------------------------- entrenamiento (D8)
FEATURES = ["hora_sin", "hora_cos", "dia_semana", "actividad_movil", "frac_score_umb",
            "score_medio", "pps_eval", "die_temp_c", "runtime_load_pct", "rssi_dbm",
            "csi_pps", "tiene_imagen", "n_fotos", "frac_ia_persona", "hora_provision",
            "clima_temp_c", "clima_humedad_pct", "clima_viento_kmh"]


def entrenar(filas: list[dict]) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

    filas = sorted(filas, key=lambda f: f["t_ms"])
    n = len(filas)
    corte = int(n * 0.8)
    train, test = filas[:corte], filas[corte:]

    def matriz(rs, relleno=None):
        cols = []
        for k in FEATURES:
            vals = [r.get(k) for r in rs]
            if relleno is None:      # mediana del TRAIN (sin fuga del test a la imputacion)
                nums = [x for x in vals if x is not None]
                med = statistics.median(nums) if nums else 0.0
            else:
                med = relleno[k]
            cols.append([med if x is None else float(x) for x in vals])
        return list(zip(*cols))

    relleno = {}
    for k in FEATURES:
        nums = [x for x in (r.get(k) for r in train) if x is not None]
        relleno[k] = statistics.median(nums) if nums else 0.0
    Xtr, Xte = matriz(train), matriz(test, relleno=relleno)
    ytr = [r["y_motion_siguiente"] for r in train]
    yte = [r["y_motion_siguiente"] for r in test]
    mayoria = max(set(ytr), key=ytr.count) if ytr else 0
    base = {"modelo": "mayoria-clase-train",
            "accuracy": r4(sum(1 for y in yte if y == mayoria) / len(yte)) if yte else None,
            "nota": "baseline sin features: clase mayoritaria del train"}

    def metricas(y_pred):
        out = {"n_train": len(train), "n_test": len(test),
               "positivos_test": sum(yte), "positivos_train": sum(ytr),
               "accuracy": r4(accuracy_score(yte, y_pred)) if yte else None}
        for name, fn in (("precision", precision_score), ("recall", recall_score),
                         ("f1", f1_score)):
            try:
                out[name] = r4(fn(yte, y_pred, zero_division=0))
            except Exception:
                out[name] = None
        try:
            out["roc_auc"] = r4(roc_auc_score(yte, y_pred)) if len(set(yte)) > 1 else None
        except Exception:
            out["roc_auc"] = None
        return out

    res = {"split": "temporal 80/20 (orden cronologico por t_ms, sin shuffle)",
           "baseline": base, "modelos": {}}
    lr = Pipeline([("esc", StandardScaler()),
                   ("clf", LogisticRegression(max_iter=2000))])
    lr.fit(Xtr, ytr)
    res["modelos"]["logistic_regression"] = {**metricas(lr.predict(Xte)),
        "coef_abs": {k: r4(abs(c)) for k, c in zip(FEATURES, lr.named_steps["clf"].coef_[0])}}
    dt = DecisionTreeClassifier(max_depth=4, random_state=0)
    dt.fit(Xtr, ytr)
    res["modelos"]["decision_tree"] = {**metricas(dt.predict(Xte)),
        "feature_importances": {k: r4(i) for k, i in zip(FEATURES, dt.feature_importances_)}}
    res["predicciones_test"] = [{"t_ms": r["t_ms"], "real": y, "pred_lr": p1, "pred_dt": p2}
                                for r, y, p1, p2 in zip(test, yte, lr.predict(Xte), dt.predict(Xte))]
    res["nota_honest_e10"] = ("dataset corto (una manana de actividad + huecos offline); las "
                              "metricas se reportan CON tamano muestral y NO se promete "
                              "accuracy dependiente de dataset largo (leccion E10). El target "
                              "es movimiento futuro (ocupacion dinamica), no presencia.")
    return res


# ---------------------------------------------------------------- validacion contra GT
def validar_gt(filas: list[dict], gt: list[dict], onsets: list) -> dict:
    """(a) indicador vs tramos GT motion; (b) P/R por umbral a nivel onset."""
    ocupadas = [(f["t_ms"], f["t_ms"] + VENTANA_MS) for f in filas if f["ocupada"]]
    tramos = [w for w in gt if w["verdad_terreno"] == "motion"]
    cubiertos = 0
    detalle = []
    for w in tramos:
        solape = any(a < w["fin_ms"] and b > w["inicio_ms"] for a, b in ocupadas)
        cubiertos += 1 if solape else 0
        if not solape:
            detalle.append({"sesion": w["sesion"], "inicio_ms": w["inicio_ms"],
                            "fin_ms": w["fin_ms"], "motivo": "sin ventana ocupada encima"})
    n_s = sum(1 for w in gt if w["verdad_terreno"] == "idle")
    curvas = []
    tramos_idle = [w for w in gt if w["verdad_terreno"] == "idle"]
    for umbral in UMBRALES_PR:
        tp_seg = fn = 0
        dentro = [o for o in onsets if o[2] is not None and o[2] >= umbral]
        cubre = set()
        for i, w in enumerate(tramos):
            if any(w["inicio_ms"] - 20_000 <= o[1] <= w["fin_ms"] + 20_000 for o in dentro):
                cubre.add(i)
        tp_seg = len(cubre)
        fn = len(tramos) - tp_seg
        # FP PISO: onsets fuera de tramos motion (incluye zonas sin-dato => precision piso).
        # FP REAL: onsets dentro de tramos idle confirmados (existe solo con marcas idle S2/S3).
        fp_piso = sum(1 for o in dentro
                      if not any(w["inicio_ms"] <= o[1] <= w["fin_ms"] for w in tramos))
        fp_real = sum(1 for o in dentro
                      if any(w["inicio_ms"] <= o[1] <= w["fin_ms"] for w in tramos_idle))
        prec_piso = tp_seg / (tp_seg + fp_piso) if (tp_seg + fp_piso) else None
        prec = (tp_seg / (tp_seg + fp_real)) if (tramos_idle and (tp_seg + fp_real)) else None
        rec = tp_seg / len(tramos) if tramos else None
        p_for_f1 = prec if prec is not None else prec_piso
        f1 = (2 * p_for_f1 * rec / (p_for_f1 + rec)) if p_for_f1 and rec else (
            0.0 if p_for_f1 is not None and rec is not None else None)
        curvas.append({"umbral": umbral, "tramos_cubiertos": tp_seg, "tramos_perdidos": fn,
                       "onsets_fuera_gt": fp_piso, "onsets_en_idle_gt": fp_real,
                       "precision": r4(prec), "precision_piso": r4(prec_piso),
                       "recall": r4(rec), "f1": r4(f1)})
    mejor = max((c for c in curvas if c["f1"] is not None), key=lambda c: c["f1"], default=None)
    return {"n_tramos_gt_motion": len(tramos), "n_tramos_gt_idle": len(tramos_idle),
            "indicador_cobertura_pct": r4(100.0 * cubiertos / len(tramos)) if tramos else None,
            "tramos_gt_no_cubiertos": detalle[:10],
            "curva_pr_por_umbral": curvas,
            "mejor_umbral_f1": mejor,
            "nota": ("P/R a nivel TRAMOS GT con margen ±20 s (cooldown de evidencia F24). "
                     "precision_piso es un PISO honesto: los onsets fuera de tramos GT caen en "
                     "zonas sin-dato (GT pasivo no marca idle), no en idle confirmado. La "
                     "precision REAL se computa sola en cuanto el CSV tenga tramos idle "
                     "(sesiones activas S2/S3, autodescubiertas por glob): el codigo ya cuenta "
                     "onsets_dentro de tramos idle como FP real (hook activo, sin cambio de "
                     "codigo al llegar S2/S3).")}


# ---------------------------------------------------------------- figuras
FIGS = ["ocupacion_pr_umbral.png", "ocupacion_modelo_vs_gt.png", "ocupacion_feature_importance.png"]


def _save(fig, path: Path):
    import matplotlib.pyplot as plt
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def hacer_figuras(filas: list[dict], gt: list[dict], val: dict, ent: dict, outdir: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt, dates as mdates

    gdir = outdir / "graficos"
    gdir.mkdir(parents=True, exist_ok=True)
    rutas: list[str] = []
    clave = lambda ms: mdates.date2num(datetime.fromtimestamp(ms / 1000.0, timezone.utc))

    # 1) precision-recall-F1 por umbral de score
    cv = val["curva_pr_por_umbral"]
    fig, ax = plt.subplots(figsize=(9, 4.8))
    us = [c["umbral"] for c in cv]
    ax.plot(us, [c["recall"] or 0 for c in cv], "o-", color="#d62728", label="recall (tramos GT)")
    ax.plot(us, [c["precision_piso"] or 0 for c in cv], "s-", color="#1f77b4",
            label="precision (piso: huecos = sin-dato)")
    if any(c["precision"] is not None for c in cv):
        ax.plot(us, [(c["precision"] or 0) for c in cv], "^-", color="#17becf",
                label="precision real (GT idle S2/S3)")
    ax.plot(us, [c["f1"] or 0 for c in cv], "--", color="#2ca02c", label="F1")
    if val["mejor_umbral_f1"]:
        ax.axvline(val["mejor_umbral_f1"]["umbral"], color="gray", ls=":",
                   label=f"mejor F1 en θ={val['mejor_umbral_f1']['umbral']}")
    ax.set_xlabel("umbral de score (onset ≥ θ cuenta como detección)")
    ax.set_ylabel("métrica")
    ax.set_title("Precisión / recall por umbral de score vs tramos GT (F4S1B)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    rutas.append(str(_save(fig, gdir / FIGS[0]).relative_to(RAIZ)).replace("\\", "/"))

    # 2) ocupación (indicador + modelo) vs GT — zoom a la ventana GT
    tramos = [w for w in gt if w["verdad_terreno"] == "motion"]
    fig, ax = plt.subplots(figsize=(13, 4.8))
    ts = [clave(f["t_ms"]) for f in filas]
    fm = [f["actividad_movil"] for f in filas]
    ax.bar(ts, fm, width=VENTANA_MS / 86_400_000.0 * 0.9, color="#d62728", alpha=0.55,
           label="fracción motion de la ventana (indicador)")
    pred = {p["t_ms"]: p for p in val["_predicciones"]}
    px = [clave(t) for t in pred if pred[t]["pred_dt"]]
    ax.plot(px, [0.98] * len(px), "x", color="#1f77b4", markersize=7,
            label="DecisionTree predice motion en la ventana siguiente")
    for w in tramos:
        ax.axvspan(clave(w["inicio_ms"]), clave(w["fin_ms"]), alpha=0.15, color="green")
    if tramos:
        ax.set_xlim(clave(min(w["inicio_ms"] for w in tramos) - 900_000),
                    clave(max(w["fin_ms"] for w in tramos) + 900_000))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone.utc))
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("fracción motion")
    ax.set_title("Ocupación dinámica: indicador por ventana de 10 min vs tramos GT "
                 "(sombreado) y predicción del modelo")
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    rutas.append(str(_save(fig, gdir / FIGS[1]).relative_to(RAIZ)).replace("\\", "/"))

    # 3) importancia de features (PLAN F5a: "importancia de features graficada")
    imp = ent["modelos"]["decision_tree"]["feature_importances"]
    orden = sorted(imp, key=imp.get, reverse=True)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.barh(list(reversed(orden)), [imp[k] for k in reversed(orden)], color="#9467bd")
    ax.set_xlabel("importance (DecisionTree, gini)")
    ax.set_title("Importancia de features — modelo de ocupación (D8)")
    fig.tight_layout()
    rutas.append(str(_save(fig, gdir / FIGS[2]).relative_to(RAIZ)).replace("\\", "/"))
    return rutas


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="F5a modelo/indicador de ocupacion (lea el docstring)")
    ap.add_argument("--dataset", default=str(RAIZ / "dataset"))
    ap.add_argument("--salida", default=str(RAIZ / "docs" / "analisis"))
    ap.add_argument("--no-figuras", action="store_true")
    ap.add_argument("--db", default=DB_WSL,
                    help="ruta BD dentro de WSL (default canonica; snapshot para tests de "
                         "determinismo: la BD real crece mientras el nodo ingesta)")
    args = ap.parse_args(argv)

    outdir = Path(args.salida); outdir.mkdir(parents=True, exist_ok=True)
    sesiones = ad.cargar_registros(Path(args.dataset))
    rutas_val = sorted((RAIZ / "docs").glob("validacion_sesion*.csv"))
    gt = ad.construir_ground_truth(sesiones, ad.cargar_validacion(rutas_val))

    wsl = consultar_wsl_modelo({"db": args.db, "ventana_ms": VENTANA_MS})
    filas, meta_ds = construir_dataset(wsl)
    ent = entrenar(filas)
    val = validar_gt(filas, gt, [[o[0], o[1], o[2]] for o in wsl["onsets"]])
    val["_predicciones"] = ent.pop("predicciones_test", [])

    figuras: list[str] = []
    if not args.no_figuras:
        try:
            figuras = hacer_figuras(filas, gt, val, ent, outdir)
        except Exception as e:
            print(f"[aviso] figuras del modelo fallaron: {e}", file=sys.stderr)
    val.pop("_predicciones", None)

    hashes = {"MODELO": sha256_de(Path(__file__)),
              "PIPELINE": sha256_de(RAIZ / "analysis" / "analizar_dataset.py")}
    for arch in sorted(Path(args.dataset).glob("sesion_*/registro_*.jsonl")):
        hashes[str(arch.relative_to(RAIZ)).replace("\\", "/")] = sha256_de(arch)
    for ruta in rutas_val:
        hashes[str(ruta.relative_to(RAIZ)).replace("\\", "/")] = sha256_de(ruta)

    doc = {
        "modelo_version": VERSION_MODELO,
        "generado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hash_inputs": hashes,
        "definiciones": {
            "indicador": "ventana 10 min OCUPADA si >=1 evaluacion state='motion' (metodo del "
                         "pipeline base; idle != sala vacia — AGENTS 11)",
            "target_modelo": "motion (>=1 evaluacion) en la ventana SIGUIENTE de 10 min "
                             "(SPEC §10 / PLAN F5a)",
            "features": FEATURES,
            "origen": "SOLO origen='real' (AGENTS 10)",
        },
        "dataset": meta_ds,
        "entrenamiento": ent,
        "validacion_gt": val,
        "figuras": figuras,
    }
    (outdir / "modelo_ocupacion.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"salida": str(outdir), "figuras": figuras,
                      "n_filas": meta_ds["n_filas_entrenables"],
                      "arbol_test": ent["modelos"]["decision_tree"],
                      "regresion_test": ent["modelos"]["logistic_regression"],
                      "cobertura_indicador_pct": val["indicador_cobertura_pct"]},
                     ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
