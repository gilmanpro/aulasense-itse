#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F5a — Pipeline de análisis reproducible de AulaSense.

Entrada (SOLO lectura, nunca escribe BD ni dataset/):
  - dataset/sesion_*/registro_*.jsonl   (logs de sesión F4: cabecera, fotos, gt, resumen...)
  - docs/validacion_sesion*.csv         (ground truth manual, columnas §10 SPEC)
  - BD canónica WSL (debian-aulasense, /home/aulasense/aulasense-datos/aulasense.db)
    leída via `wsl ... python3` en modo sqlite URI mode=ro (el CLI sqlite3 NO existe ahí;
    patrón heredado de .tmp/q_fotos2.py y .tmp/q_estado_fotos.py).
  - JPEG de evidencia validados con PIL ESTRICTO en el venv WSL (Pillow 13.0.0);
    el fire-test HTTP (id 56, vision_estado='omitido', descripcion FIRE-TEST) se EXCLUYE.
    vision_estado='error' (JPEG irrecoverable) NO se excluye del GT foto↔onset: cuenta
    como evidencia fisica de captura y el informe lo anota (auditoría 2026-11-15).

Salida a docs/analisis/:
  - analisis.json         (machine-readable para F6a; determinista: version + sha256 de
                            inputs; unico campo volatil `generado_utc`)
  - informe_metricas.md   (legible, con notas honestas)
  - graficos/*.png        (histograma scores, serie temporal motion, matriz confusion)

Re-ejecucion cuando caigan S2/S3 y la pasada 3 de vision (CERO codigo nuevo):
  C:\\Python315\\python.exe analysis\\analizar_dataset.py

Politicas honestas (AGENTS reglas 10/11):
  - GT pasivo_foto_corroborada: huecos sin foto = SIN-DATO, NO idle. Mientras no existan
    marcas idle reales (sesiones activas S2/S3), la confusion es RECALL-ONLY y FPR se
    reporta null con nota. El codigo ya maneja filas idle del CSV si aparecen.
  - Latencia evento→fila = strftime('%s',recibido_en) (UTC) − ts_ms (epoch UTC R3);
    tiene truncacion a ±1 s por precision de segundo del campo recibido_en.
  - NO se toca server/vision ni se escriben columnas vision_*: solo lectura.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]          # aulasense/
VERSION_PIPELINE = "f5a-1.1"

# --- Configuracion del acceso WSL (solo lectura) --------------------------------
DISTRO_WSL = "debian-aulasense"
DB_WSL = "/home/aulasense/aulasense-datos/aulasense.db"
DATOS_WSL = "/home/aulasense/aulasense-datos"
PY_VENV_WSL = "/home/aulasense/venvs/aulasense/bin/python"   # tiene PIL 13 + sqlite3
PY_SYS_WSL = "python3"                                        # fallback sin PIL

VENTANA_FOTO_ONSET_MS = 20_000   # ±20 s, metodologia F4 (onset→[ts-5s, ts+20s] aprox sim)
MARGEN_EVENTOS_MS = 60_000       # buffer para bordes de ventana en consultas de eventos
BUCKET_SERIE_MS = 10_000         # granularidad de la serie temporal motion

# El fire-test del receptor HTTP (id 56) NO es evidencia de evento: descripcion FIRE-TEST
# y vision_estado='omitido'. Se excluye explicitamente de integridad JPEG y correlacion.
RE_FIRE_TEST = re.compile(r"FIRE-TEST", re.IGNORECASE)

# Tratamiento analitico del estado 'error' (auditoria doble-ciego 2026-11-15, MEDIO):
# las ~20 fotos con JPEG irrecoverable (SPEC §404-407, anteriores a FixF) NO aportan
# material de vision (sin descripcion/etiquetas/vision_tokens), pero SI son evidencia
# fisica de que hubo captura al onset. Regla acordada: se EXCLUYEN de las features de
# vision (modelo_ocupacion: n_fotos/tiene_imagen/frac_ia_persona) y se MANTIENEN en el
# GT foto<->onset (eda/analizar_dataset), anotandolo de forma explicita en los informes.
ESTADOS_SIN_MATERIAL_VISION: tuple[str, ...] = ("omitido", "error")


# ------------------------------------------------------------------ utilidades
def r4(x):
    """Redondeo determinista a 4 decimales (None pasa tal cual)."""
    return None if x is None else round(float(x), 4)


def texto_vision_error(n: int) -> str:
    """Nota canonica de informes: filas 'error' (JPEG irrecoverable) contadas como
    evidencia fisica de captura y NO como material de vision (auditoria 2026-11-15).
    Determinista: mismo texto en analisis.json, informe_metricas.md y eda.json."""
    if not n:
        return ("sin fotos con JPEG irrecoverable (error) en la BD (o modo --sin-wsl): "
                "nada que anotar")
    return (f"{n} con JPEG irrecoverable (error) contadas como evidencia fisica de "
            "captura, no como material vision")


def sha256_de(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def percentiles(vals: list[float], qs=(0.0, 0.25, 0.5, 0.75, 0.95, 1.0)) -> dict:
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    out = {"n": len(s)}
    for q in qs:
        idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
        out[f"p{int(q * 100)}"] = r4(s[idx])
    out["media"] = r4(statistics.fmean(s))
    return out


UMBRAL_ANOMALO_MS = 600_000  # |diff| > 10 min => ts_ms o recibido_en anormalo (R3/re-enganche)


def estadisticas_latencia(pares: list[tuple], etiqueta: str) -> dict:
    """Resumen honesto de (recibido_en_utc_epoch*1000 - ts_ms) sobre pares (id, diff_ms).

    `recibido_en` tiene precision de segundo => truncacion sistematica [-1000, 0) ms;
    la latencia real estimada es diff + 500 ms (punto medio de la truncacion).
    Diffs con |valor| > 10 min se excluyen como ANOMALIAS (ts_ms re-enganchado o
    fire-test con epoca x2) y se listan para auditoria."""
    diffs, anomalos = [], []
    for ident, d in pares:
        if d is None:
            continue
        if abs(d) > UMBRAL_ANOMALO_MS:
            anomalos.append({"id": ident, "diff_ms": r4(d)})
            continue
        diffs.append(d)
    if not diffs:
        return {
            "fuente": etiqueta,
            "n": 0,
            "anomalos_excluidos": anomalos,
            # BUG-PROD-F5A-1 (arreglado 2026-11-15): el dict n==0 tambien lleva
            # 'nota'; escribir_informe la exige SIEMPRE y antes caiamos en KeyError
            # en modo --sin-wsl (sin latencias que resumir).
            "nota": ("sin datos de latencia (n=0): ningun par usable (p. ej. modo "
                     "offline --sin-wsl, sin recibido_en ni ts_ms que comparar). Se "
                     "omiten mediana y percentiles: ausencia de dato NO es latencia 0."),
        }
    d = sorted(diffs)
    n = len(d)
    p = lambda q: d[min(n - 1, max(0, round(q * (n - 1))))]
    return {
        "fuente": etiqueta,
        "n": n,
        "mediana_ms": r4(statistics.median(d)),
        "p95_ms": r4(p(0.95)),
        "min_ms": r4(d[0]),
        "max_ms": r4(d[-1]),
        "mediana_corr_truncacion_ms": r4(statistics.median(d) + 500),
        "pct_abs_bajo_2s": r4(100.0 * sum(1 for x in d if abs(x) < 2000) / n),
        "anomalos_excluidos": sorted(anomalos, key=lambda a: a["id"]),
        "nota": ("recibido_en en BD tiene precision de 1 s (UTC) => sesgo de truncacion "
                 "de hasta -1000 ms; se reporta mediana corregida (+500 ms). ts_ms es "
                 "epoca estimada por R3 (monotono del nodo), no reloj wall-clock exacto. "
                 "|diff|>10 min se excluye como anomalia y se lista."),
    }


def aparear_fotos_onsets(fotos: list[dict], onsets: list[dict],
                         ventana_ms: int = VENTANA_FOTO_ONSET_MS) -> dict:
    """Correlacion foto↔onset: para cada foto, el onset mas cercano dentro de ±ventana_ms.
    fotos/onsets: dicts con {"ts_ms","id"}."""
    if not fotos or not onsets:
        # Misma FORMA que la rama con datos: escribir_informe lee cf["ventana_ms"]
        # (KeyError en modo --sin-wsl hasta el fix BUG-PROD-F5A-1, 2026-11-15).
        return {"fotos": len(fotos), "onsets": len(onsets), "emparejadas": 0,
                "fotos_emparejadas_pct": None, "onsets_con_foto_pct": None,
                "lags_ms": {}, "ventana_ms": ventana_ms,
                "fotos_sin_match": sorted(f["id"] for f in fotos),
                "onsets_sin_foto": sorted(o["id"] for o in onsets)}
    ts_on = sorted(o["ts_ms"] for o in onsets)
    lags = []
    emparejadas = 0
    fotos_sin_match = []
    for f in fotos:
        mejor = None
        for t in ts_on:
            d = abs(t - f["ts_ms"])
            if d <= ventana_ms and (mejor is None or d < abs(mejor - f["ts_ms"])):
                mejor = t
        if mejor is not None:
            emparejadas += 1
            lags.append(f["ts_ms"] - mejor)   # foto despues del onset => positivo
        else:
            fotos_sin_match.append(f["id"])
    ts_f = [f["ts_ms"] for f in fotos]
    onsets_sin_foto = [o["id"] for o in onsets
                       if not any(abs(tf - o["ts_ms"]) <= ventana_ms for tf in ts_f)]
    return {
        "fotos": len(fotos),
        "onsets": len(onsets),
        "emparejadas": emparejadas,
        "fotos_emparejadas_pct": r4(100.0 * emparejadas / len(fotos)),
        "onsets_con_foto_pct": r4(100.0 * (len(onsets) - len(onsets_sin_foto)) / len(onsets)),
        "ventana_ms": ventana_ms,
        "lags_ms": percentiles([float(x) for x in lags]),
        "fotos_sin_match": sorted(fotos_sin_match),
        "onsets_sin_foto": sorted(onsets_sin_foto),
    }


def divergencia_registros_vs_bd(sesiones: dict, eventos_bd: list[dict]) -> dict:
    """Los registros jsonl son dumps historicos via API; la BD canonica es la fuente de
    verdad actual. Se comparan los eventos compartidos por id para HACER VISIBLE cualquier
    re-enganche de ts_ms (cutover WSL / reanudaciones R3) sin reescribir nada."""
    bd = {e["id"]: e for e in eventos_bd}
    reg_ids = {}
    for nom, s in sesiones.items():
        for e in s["eventos"]:
            if isinstance(e.get("id"), int):
                reg_ids.setdefault(e["id"], (nom, e))
    comunes = [i for i in reg_ids if i in bd]
    diffs = [(i, bd[i]["ts_ms"] - reg_ids[i][1]["ts_ms"]) for i in sorted(comunes)]
    diverg = [{"id": i, "delta_ms": r4(d)} for i, d in diffs if abs(d) > 5_000]
    return {
        "evento_ids_en_registros": len(reg_ids),
        "evento_ids_en_bd_rango": len(bd),
        "comunes": len(comunes),
        "solo_en_registro": sorted(set(reg_ids) - set(bd))[:50],
        "solo_en_bd": sorted(set(bd) - set(reg_ids))[:50],
        "n_ts_divergentes_gt5s": len(diverg),
        "ts_divergentes": diverg[:50],
        "nota": ("fuente de verdad = BD canonica actual; los jsonl son el dump historico "
                 "de la sesion. ts_ms divergentes = filas re-enganchadas tras el dump "
                 "(cutover/fallback); no se reescribe nada, se reporta."),
    }


def matriz_confusion(gt_windows: list[dict], gt_stats: list[dict], margen_ms: int = 0) -> dict:
    """TP/FN sobre marcas GT motion (al menos 1 evaluacion state='motion' dentro del
    tramo [inicio-margen, fin+margen] = detectado). FP/TN requieren marcas idle: mientras
    el GT sea pasivo_foto_corroborada (huecos = sin-dato) FPR es None con nota honesta.

    gt_stats: por tramo GT => {"n_motion_dentro": int, ...} alineado por indice."""
    tp = fn = 0
    sin_eval = 0
    detecciones_frac = []
    for w, st in zip(gt_windows, gt_stats):
        if w["verdad_terreno"] != "motion":
            continue
        if st.get("n_eval", 0) == 0:
            sin_eval += 1
            continue
        if st.get("n_motion_dentro", 0) > 0:
            tp += 1
        else:
            fn += 1
        detecciones_frac.append(st.get("n_motion_dentro", 0) / st["n_eval"])
    n_motion = sum(1 for w in gt_windows if w["verdad_terreno"] == "motion")
    n_idle = sum(1 for w in gt_windows if w["verdad_terreno"] == "idle")
    total = tp + fn
    out = {
        "metodo": ("motion detectado = >=1 evaluacion state=motion dentro del tramo GT "
                   f"[inicio-{margen_ms}ms, fin+{margen_ms}ms]"),
        "margen_ms": margen_ms,
        "tramos_motion_gt": n_motion,
        "tramos_idle_gt": n_idle,
        "tp": tp,
        "fn": fn,
        "recall_motion": r4(tp / total) if total else None,
        "fp": None,
        "tn": None,
        "fpr": None,
        "precision": None,
        "cobertura_media_motion_dentro_gt": r4(statistics.fmean(detecciones_frac)) if detecciones_frac else None,
        "tramos_gt_sin_evaluaciones": sin_eval,
    }
    if n_idle == 0:
        out["nota_honest"] = ("RECALL-ONLY: el GT actual es pasivo_foto_corroborada (solo "
                              "motion confirmado por foto; los huecos quedan sin-dato, NO "
                              "son idle). FPR/precision no computables. Al existir marcas "
                              "idle de las sesiones activas S2/S3 el mismo codigo produce "
                              "FPR/TPR/precision reales sin cambio de codigo.")
    else:
        # Marcas idle presentes: FP = tramos idle con >=1 evaluacion motion dentro.
        fp = tn = 0
        for w, st in zip(gt_windows, gt_stats):
            if w["verdad_terreno"] != "idle":
                continue
            if st.get("n_motion_dentro", 0) > 0:
                fp += 1
            else:
                tn += 1
        out["fp"], out["tn"] = fp, tn
        out["fpr"] = r4(fp / (fp + tn)) if (fp + tn) else None
        out["precision"] = r4(tp / (tp + fp)) if (tp + fp) else None
    return out


def integrar_pil_estados(jpeg: list[dict]) -> dict:
    """Tasa de integridad JPEG con PIL estricto. Excluye fire-test (id 56 'omitido') y
    sinteticos. 'purgada' = archivo borrado por retencion con imagen_purgada=1 (fila viva)."""
    cont = {"valida": 0, "rota": 0, "ausente": 0, "purgada": 0, "sin_validar": 0}
    excluidas = 0
    for f in jpeg:
        if f.get("excluida_fire_test"):
            excluidas += 1
            continue
        cont[f["estado"]] = cont.get(f["estado"], 0) + 1
    evaluadas = cont["valida"] + cont["rota"]
    return {
        "validas": cont["valida"],
        "rotas": cont["rota"],
        "ausentes_no_purgadas": cont["ausente"],
        "purgadas_retencion": cont["purgada"],
        "sin_validar": cont["sin_validar"],
        "excluidas_fire_test": excluidas,
        "tasa_integridad": r4(cont["valida"] / evaluadas) if evaluadas else None,
        "metodo": ("PIL estricto en venv WSL: Image.open + verify + load; purga por "
                   "retencion no cuenta como rotas (imagen_purgada=1, fila+dataset intactos)"),
    }


# ------------------------------------------------------------------ parsers entrada
def leer_jsonl(ruta: Path) -> list[dict]:
    filas = []
    with open(ruta, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if linea:
                filas.append(json.loads(linea))
    return filas


def cargar_registros(dataset_dir: Path) -> dict:
    """-> {sesion: {"archivo", "cabecera", "ventana":[inicio_ms,fin_ms], "gt":[...],
        "fotos":[...], "eventos":[...], "resumen":[...]}} — orden determinista por archivo."""
    sesiones: dict[str, dict] = {}
    for arch in sorted(dataset_dir.glob("sesion_*/registro_*.jsonl")):
        filas = leer_jsonl(arch)
        cab = next((x for x in filas if x.get("tipo") == "cabecera"), {})
        nom = cab.get("sesion") or arch.stem.replace("registro_", "")
        s = sesiones.setdefault(nom, {"archivos": [], "gt": [], "fotos": [],
                                      "eventos": [], "resumen": [], "cabeceras": []})
        s["archivos"].append(str(arch.relative_to(RAIZ)).replace("\\", "/"))
        for x in filas:
            t = x.get("tipo")
            if t == "cabecera":
                s["cabeceras"].append(x)
            elif t == "gt":
                s["gt"].append(x)
            elif t == "foto":
                s["fotos"].append(x)
            elif t == "resumen":
                s["resumen"].append(x)
            elif t in ("MOVIMIENTO_INICIO", "MOVIMIENTO_FIN", "NODO_ONLINE",
                       "NODO_OFFLINE", "FALLA", "CALIBRACION"):
                s["eventos"].append(x)
    # ventana de cada sesion: del resumen (reconstruido) o cabecera (vivo)
    for nom, s in sesiones.items():
        ini = fin = None
        for r in s["resumen"] + s["cabeceras"]:
            ini = ini or r.get("inicio_ms") or (r.get("ventana") or {}).get("inicio_ms")
            fin = fin or r.get("fin_ms") or (r.get("ventana") or {}).get("fin_ms")
        s["ventana"] = {"inicio_ms": ini, "fin_ms": fin}
    return sesiones


def cargar_validacion(rutas: list[Path]) -> list[dict]:
    """Filas del CSV de ground truth. Soporta verdad_terreno motion|idle|sin-dato.
    Orden determinista: (sesion, inicio_ms, fin_ms)."""
    filas = []
    for ruta in rutas:
        with open(ruta, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    ini, fin = int(float(row["inicio_ms"])), int(float(row["fin_ms"]))
                except (KeyError, ValueError):
                    continue
                vt = (row.get("verdad_terreno") or "").strip().lower()
                if vt not in ("motion", "idle"):
                    continue
                filas.append({
                    "sesion": row.get("sesion", Path(ruta).stem),
                    "inicio_ms": ini, "fin_ms": fin,
                    "verdad_terreno": vt,
                    "fuente": row.get("fuente", ""),
                    "notas": row.get("notas", ""),
                    "csv": str(ruta.relative_to(RAIZ)).replace("\\", "/"),
                })
    filas.sort(key=lambda x: (x["sesion"], x["inicio_ms"], x["fin_ms"]))
    return filas


def construir_ground_truth(sesiones: dict, validacion: list[dict]) -> list[dict]:
    """Union: filas del CSV (canonico) + tramos gt de los registros jsonl como respaldo
    si una sesion no tiene filas en el CSV. Sin duplicar tramos identicos."""
    vistos = {(w["sesion"], w["inicio_ms"], w["fin_ms"], w["verdad_terreno"]) for w in validacion}
    out = list(validacion)
    for nom, s in sorted(sesiones.items()):
        if any(w["sesion"] == nom for w in out):
            continue  # el CSV ya cubre esta sesion
        for g in s["gt"]:
            clave = (nom, g["inicio_ms"], g["fin_ms"], "motion")
            if clave in vistos:
                continue
            vistos.add(clave)
            out.append({"sesion": nom, "inicio_ms": g["inicio_ms"], "fin_ms": g["fin_ms"],
                        "verdad_terreno": "motion",
                        "fuente": "gt_registro_jsonl",
                        "notas": f"fotos={g.get('fotos')}",
                        "csv": None})
    out.sort(key=lambda x: (x["sesion"], x["inicio_ms"], x["fin_ms"]))
    return out


# ------------------------------------------------------------------ consulta WSL
CONSULTA_WSL_PY = r'''# -*- coding: utf-8 -*-
# Consulta SOLO-LECTURA a la BD canonia WSL + validacion PIL estricta de JPEG.
# Lee el payload JSON por argv[1] (el script mismo viaja por stdin). NO escribe nada en disco/BD.
#   {"db","base_datos","ventanas":[{"sesion","inicio_ms","fin_ms"}],
#    "gt":[{"inicio_ms","fin_ms","verdad_terreno"}],
#    "rango_eventos":[min_ms,max_ms], "bucket_ms":int,
#    "validar_jpeg":bool, "re_fire_test":"FIRE-TEST"}
# Escribe el resultado JSON por stdout. NO escribe nada en disco/BD.
import json, os, re, sqlite3, sys

payload = json.loads(sys.argv[1])
con = sqlite3.connect("file:" + payload["db"] + "?mode=ro", uri=True)
q = lambda s, p=(): con.execute(s, p).fetchall()
r4 = lambda x: None if x is None else round(float(x), 4)

def percentiles(vals, qs=(0.0, 0.25, 0.5, 0.75, 0.95, 1.0)):
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    out = {"n": len(s), "media": r4(sum(s)/len(s))}
    for qq in qs:
        idx = min(len(s)-1, max(0, round(qq*(len(s)-1))))
        out["p" + str(int(qq*100))] = r4(s[idx])
    return out

res = {"wsl_ok": True}

# --- evaluaciones por ventana de sesion (solo origen=real)
vent_stats = []
for v in payload["ventanas"]:
    ini, fin = v["inicio_ms"], v["fin_ms"]
    if ini is None or fin is None:
        vent_stats.append({"sesion": v["sesion"], "error": "ventana sin limites"}); continue
    n_eval, n_motion = q("SELECT COUNT(*), SUM(state='motion') FROM evaluaciones "
                         "WHERE origen='real' AND ts_ms BETWEEN ? AND ?", (ini, fin))[0]
    scores = [r[0] for r in q("SELECT score FROM evaluaciones WHERE origen='real' "
                              "AND ts_ms BETWEEN ? AND ? AND score IS NOT NULL", (ini, fin))]
    scores_motion = [r[0] for r in q("SELECT score FROM evaluaciones WHERE origen='real' "
                                     "AND state='motion' AND ts_ms BETWEEN ? AND ?", (ini, fin))]
    buckets = []
    paso = payload["bucket_ms"]
    t = ini - (ini % paso)
    while t < fin:
        n, m = q("SELECT COUNT(*), COALESCE(SUM(state='motion'),0) FROM evaluaciones "
                 "WHERE origen='real' AND ts_ms >= ? AND ts_ms < ?", (t, t + paso))[0]
        if n:
            buckets.append({"t0_ms": t, "n": n, "frac_motion": r4(m / n)})
        t += paso
    tel = q("SELECT AVG(csi_accepted_pps), AVG(wifi_rssi_dbm), AVG(die_temp_c), "
            "AVG(runtime_load_percent), AVG(free_memory_kb) FROM telemetria "
            "WHERE origen='real' AND ts_ms BETWEEN ? AND ?", (ini, fin))[0]
    dur_s = (fin - ini) / 1000.0
    vent_stats.append({
        "sesion": v["sesion"],
        "duracion_s": r4(dur_s),
        "n_eval": n_eval,
        "n_motion": n_motion,
        "pct_motion": r4(100.0 * (n_motion or 0) / n_eval) if n_eval else None,
        "pps_medio": r4(n_eval / dur_s) if dur_s else None,
        "score_p99": r4(percentiles(scores, (0.99,)).get("p99")),
        "scores_todo": percentiles(scores),
        "scores_motion": percentiles(scores_motion),
        "telemetria": {"pps_media": r4(tel[0]), "rssi_dbm": r4(tel[1]),
                        "die_temp_c": r4(tel[2]), "runtime_load_pct": r4(tel[3]),
                        "free_mem_kb": r4(tel[4])},
        "buckets": buckets,
    })
res["ventanas"] = vent_stats

# --- ground truth por tramo: conteos de evaluaciones motion dentro
gt_stats = []
for w in payload["gt"]:
    n_eval, n_motion = q("SELECT COUNT(*), COALESCE(SUM(state='motion'),0) FROM evaluaciones "
                         "WHERE origen='real' AND ts_ms BETWEEN ? AND ?",
                         (w["inicio_ms"], w["fin_ms"]))[0]
    first = q("SELECT MIN(ts_ms) FROM evaluaciones WHERE origen='real' AND state='motion' "
              "AND ts_ms BETWEEN ? AND ?", (w["inicio_ms"], w["fin_ms"]))[0][0]
    gt_stats.append({"n_eval": n_eval, "n_motion_dentro": n_motion,
                     "primer_motion_ms": first,
                     "offset_primer_motion_ms": (None if first is None else first - w["inicio_ms"])})
res["gt_stats"] = gt_stats

# --- eventos reales del rango (latencia + tipos + onsets)
rmin, rmax = payload["rango_eventos"]
evs = q("SELECT id, device_id, ts_ms, tipo, score, duracion_ms, "
        "CAST(strftime('%s',recibido_en) AS INTEGER) FROM eventos "
        "WHERE origen='real' AND ts_ms BETWEEN ? AND ? ORDER BY id", (rmin, rmax))
res["eventos"] = [{"id": e[0], "device_id": e[1], "ts_ms": e[2], "tipo": e[3],
                   "score": r4(e[4]), "duracion_ms": e[5],
                   "diff_recibido_ms": (None if e[6] is None else e[6]*1000 - e[2])} for e in evs]

# --- imagenes reales (metadatos + latencia) + vision
imgs = q("SELECT id, device_id, ts_ms, ruta, tamano_bytes, evento_id, origen, canal, "
         "vision_estado, imagen_purgada, vision_tokens, vision_modelo, "
         "CAST(strftime('%s',recibido_en) AS INTEGER), COALESCE(descripcion,'') FROM imagenes "
         "ORDER BY id")
res["imagenes"] = [{"id": i[0], "device_id": i[1], "ts_ms": i[2], "ruta": i[3],
                    "tamano_bytes": i[4], "evento_id": i[5], "origen": i[6], "canal": i[7],
                    "vision_estado": i[8], "imagen_purgada": i[9], "vision_tokens": i[10],
                    "vision_modelo": i[11],
                    "diff_recibido_ms": (None if i[12] is None else i[12]*1000 - i[2]),
                    "descripcion_corta": i[13][:120]} for i in imgs]
res["vision_uso"] = [list(r) for r in q(
    "SELECT resultado, COUNT(*), COALESCE(SUM(tokens_in+tokens_out),0), COUNT(tokens_in) "
    "FROM vision_uso GROUP BY resultado ORDER BY resultado")]
res["vision_tokens_totales"] = q("SELECT COALESCE(SUM(tokens_in+tokens_out),0) FROM vision_uso")[0][0]

# --- integridad JPEG PIL estricto (fire-test excluido, sinteticos excluidos)
fire = re.compile(payload["re_fire_test"], re.IGNORECASE)
jpeg = []
hay_pil = False
if payload["validar_jpeg"]:
    try:
        from PIL import Image
        hay_pil = True
    except Exception:
        pass
    for i in res["imagenes"]:
        fila = {"id": i["id"], "ruta": i["ruta"], "estado": None, "excluida_fire_test": False}
        if i["origen"] != "real":
            fila["estado"] = "excluida_no_real"
            jpeg.append(fila); continue
        if fire.search(i["descripcion_corta"]) or i["vision_estado"] == "omitido":
            fila["excluida_fire_test"] = True
            jpeg.append(fila); continue
        if not hay_pil:
            fila["estado"] = "sin_validar"
            jpeg.append(fila); continue
        ruta_abs = os.path.join(payload["base_datos"], i["ruta"])
        if i["imagen_purgada"]:
            fila["estado"] = "purgada"
            jpeg.append(fila); continue
        if not os.path.exists(ruta_abs):
            fila["estado"] = "ausente"
            jpeg.append(fila); continue
        try:
            im = Image.open(ruta_abs); im.verify(); im.close()
            im2 = Image.open(ruta_abs); im2.load(); im2.close()
            fila["estado"] = "valida"
        except Exception:
            fila["estado"] = "rota"
        jpeg.append(fila)
res["jpeg"] = jpeg
res["pil_disponible"] = hay_pil
print(json.dumps(res, ensure_ascii=False))
'''

# ------------------------------------------------------------------ WSL bridge
def consultar_wsl(payload: dict) -> dict:
    """Envia CONSULTA_WSL_PY como script por stdin al interprete WSL (python - <script>);
    el payload viaja por argv. Prioridad venv (PIL), fallback python3 del SO sin PIL."""
    errs = []
    for py in (PY_VENV_WSL, PY_SYS_WSL):
        r = subprocess.run(["wsl", "-d", DISTRO_WSL, "-u", "root", "--", py, "-",
                            json.dumps(payload, ensure_ascii=False)],
                           input=CONSULTA_WSL_PY, capture_output=True, text=True,
                           encoding="utf-8", timeout=300)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
        errs.append(f"{py}: {(r.stderr or r.stdout).strip()[:200]}")
    raise RuntimeError("consulta WSL fallo en ambos interpretes:\n" + "\n".join(errs))


# ------------------------------------------------------------------ construccion metricas
def construir_metricas(sesiones: dict, gt: list[dict], wsl: dict) -> dict:
    evs = wsl["eventos"]
    onsets = [e for e in evs if e["tipo"] == "MOVIMIENTO_INICIO"]
    fins = [e for e in evs if e["tipo"] == "MOVIMIENTO_FIN"]
    imagenes_reales = [i for i in wsl["imagenes"] if i["origen"] == "real"]
    fire = re.compile(RE_FIRE_TEST.pattern, re.IGNORECASE)
    # GT foto<->onset: SOLO se excluye el fire-test (descripcion FIRE-TEST u 'omitido').
    # Las filas con vision_estado='error' (JPEG irrecoverable, sin material de vision)
    # SE MANTIENEN aqui: la foto existe y es evidencia fisica de captura al onset
    # (decision de la auditoria 2026-11-15; el efecto analitico se anota en el informe).
    fotos_usables = [i for i in imagenes_reales
                     if not (fire.search(i["descripcion_corta"]) or i["vision_estado"] == "omitido")]
    fotos_ts = [{"ts_ms": f["ts_ms"], "id": f["id"]} for f in fotos_usables]
    # Filas 'error': sin material de vision. Se cuentan aparte para la nota honesta
    # (aparecen en las features del modelo como excluidas, ver modelo_ocupacion.py).
    n_vision_error = sum(1 for i in imagenes_reales if i["vision_estado"] == "error")

    gt_stats = wsl["gt_stats"]
    confusion = matriz_confusion(gt, gt_stats)

    lat_ev = estadisticas_latencia([(e["id"], e["diff_recibido_ms"]) for e in evs], "eventos")
    lat_im = estadisticas_latencia([(i["id"], i["diff_recibido_ms"]) for i in fotos_usables],
                                   "imagenes")

    vision_estados = {}
    for i in imagenes_reales:
        vision_estados[i["vision_estado"] or "nulo"] = vision_estados.get(i["vision_estado"] or "nulo", 0) + 1

    return {
        "pipeline_version": VERSION_PIPELINE,
        "condiciones": {
            "distro_wsl": DISTRO_WSL,
            "db_wsl": DB_WSL,
            "ventana_foto_onset_ms": VENTANA_FOTO_ONSET_MS,
            "bucket_serie_ms": BUCKET_SERIE_MS,
        },
        "sesiones": {
            nom: {"archivos": s["archivos"], "ventana": s["ventana"],
                  "n_gt_registro": len(s["gt"]), "n_fotos_registro": len(s["fotos"]),
                  "n_eventos_registro": len(s["eventos"])}
            for nom, s in sorted(sesiones.items())
        },
        "metricas_por_sesion": wsl["ventanas"],
        "ground_truth": {
            "n_tramos": len(gt),
            "motion": sum(1 for w in gt if w["verdad_terreno"] == "motion"),
            "idle": sum(1 for w in gt if w["verdad_terreno"] == "idle"),
            "fuentes": sorted({w["fuente"] for w in gt}),
            "sesiones_cubiertas": sorted({w["sesion"] for w in gt}),
        },
        "confusion": confusion,
        "integridad_jpeg": {**integrar_pil_estados(wsl["jpeg"]),
                            "vision_error_n": n_vision_error,
                            "nota_vision_error": texto_vision_error(n_vision_error)},
        "correlacion_foto_onset": {**aparear_fotos_onsets(fotos_ts, onsets),
                                   "vision_error_en_gt_n": n_vision_error,
                                   "nota_vision_error": texto_vision_error(n_vision_error)},
        "correlacion_foto_evento_fk": {
            "fotos": len(fotos_usables),
            "con_evento_id": sum(1 for f in fotos_usables if f["evento_id"] is not None),
            "pct_fk": r4(100.0 * sum(1 for f in fotos_usables if f["evento_id"] is not None)
                         / len(fotos_usables)) if fotos_usables else None,
        },
        "latencias": {"eventos": lat_ev, "imagenes": lat_im},
        "divergencia_registros_vs_bd": divergencia_registros_vs_bd(sesiones, evs),
        "eventos": {
            "total_real_rango": len(evs),
            "por_tipo": {t: sum(1 for e in evs if e["tipo"] == t)
                         for t in sorted({e["tipo"] for e in evs})},
            "onsets": len(onsets),
            "movimiento_fin": len(fins),
            "score_onsets": percentiles([e["score"] for e in onsets if e["score"] is not None]),
        },
        "vision": {
            "estados_imagenes_reales": dict(sorted(vision_estados.items())),
            "vision_uso_por_resultado": [{"resultado": r[0], "n": r[1],
                                          "tokens_sum": r[2], "n_con_usage": r[3]}
                                         for r in wsl["vision_uso"]],
            "tokens_totales": wsl["vision_tokens_totales"],
            "nota": ("estados leidos de la BD en modo SOLO-LECTURA; la pasada 3 de vision "
                     "(pendientes de S2/S3) re-alimentara estas cifras al re-ejecutar el "
                     "pipeline. Presupuesto: iot2/.tmp/f25_vision_presupuesto.md "
                     "(media real medida 484,5 tok/imagen; topes 120/dia 20/h)."),
        },
    }


# ------------------------------------------------------------------ graficos
def hacer_graficos(metricas: dict, gt: list[dict], wsl: dict, outdir: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib import dates as mdates
    from matplotlib.patches import Rectangle

    rutas = []
    clave = lambda epoch_ms: mdates.date2num(
        datetime.fromtimestamp(epoch_ms / 1000.0, timezone.utc))

    # 1) histograma de scores: percentiles de las evaluaciones motion por sesion
    #    (las listas completas no viajan del WSL por peso: se grafican sus percentiles)
    sesiones = [v for v in wsl["ventanas"] if v.get("scores_motion", {}).get("n")]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    qs = ["p0", "p25", "p50", "p75", "p95", "p100"]
    for k, v in enumerate(sesiones):
        sm = v["scores_motion"]
        xs = [k + (j - 2.5) * 0.12 for j in range(6)]
        ax.bar(xs, [sm.get(q, 0) or 0 for q in qs], width=0.1, color="#1f77b4")
        for x, q in zip(xs, qs):
            ax.text(x, (sm.get(q, 0) or 0) + 0.01, q.lstrip("p"), ha="center", fontsize=7)
    ax.axhline(0.5, color="orange", ls="--", lw=1, label="score 0.5")
    ax.set_xticks(range(len(sesiones)))
    ax.set_xticklabels([f"{v['sesion']}\nn={v['scores_motion']['n']}" for v in sesiones])
    ax.set_ylim(0, 1.1)
    ax.set_title("Score en evaluaciones motion: percentiles por sesion")
    ax.set_ylabel("score")
    ax.legend()
    fig.tight_layout()
    p = outdir / "histograma_scores.png"; fig.savefig(p, dpi=120); plt.close(fig)
    rutas.append(str(p.relative_to(RAIZ)).replace("\\", "/"))

    # 2) serie temporal motion (buckets 10 s) con tramos GT sombreados
    buckets = []
    for v in wsl["ventanas"]:
        buckets += v.get("buckets", [])
    buckets.sort(key=lambda b: b["t0_ms"])
    if buckets:
        fig, ax = plt.subplots(figsize=(12, 4))
        ts = [clave(b["t0_ms"]) for b in buckets]
        fm = [b["frac_motion"] for b in buckets]
        ax.fill_between(ts, fm, step="pre", alpha=0.55, color="#d62728")
        ax.plot(ts, fm, lw=0.8, color="#900000")
        for w in gt:
            color = "green" if w["verdad_terreno"] == "motion" else "gray"
            ax.add_patch(Rectangle((clave(w["inicio_ms"]), 0),
                                   clave(w["fin_ms"]) - clave(w["inicio_ms"]), 1.0,
                                   alpha=0.18, color=color))
        ax.set_ylim(0, 1.02)
        ax.set_title("Fraccion motion por bucket de 10 s (tramos GT sombreados)")
        ax.set_ylabel("% motion")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone.utc))
        fig.tight_layout()
        p = outdir / "serie_motion.png"; fig.savefig(p, dpi=120); plt.close(fig)
        rutas.append(str(p.relative_to(RAIZ)).replace("\\", "/"))

    # 3) matriz de confusion (recall-only hoy; FP/TN se activan con marcas idle S2/S3)
    c = metricas["confusion"]
    fig, ax = plt.subplots(figsize=(5.5, 4.5))
    valores = [c["tp"], c["fn"], c["fp"] if c["fp"] is not None else 0,
               c["tn"] if c["tn"] is not None else 0]
    colores = ["#2ca02c", "#d62728", "#ff7f0e", "#1f77b4"]
    etiquetas = ["TP\n(detectado)", "FN\n(perdido)", "FP\n(pendiente S2/S3)", "TN\n(pendiente S2/S3)"]
    ax.bar(range(4), valores, color=colores, width=0.6)
    for i, val in enumerate(valores):
        ax.text(i, val + 0.2, str(val), ha="center", fontweight="bold")
    ax.set_xticks(range(4)); ax.set_xticklabels(etiquetas, fontsize=8)
    ax.set_title("GT motion: recall=%s | idle GT=%d (FPR llega con S2/S3)"
                 % (c["recall_motion"], c["tramos_idle_gt"]))
    ax.set_ylabel("tramos GT")
    fig.tight_layout()
    p = outdir / "matriz_confusion.png"; fig.savefig(p, dpi=120); plt.close(fig)
    rutas.append(str(p.relative_to(RAIZ)).replace("\\", "/"))
    return rutas


# ------------------------------------------------------------------ informe
def escribir_informe(md: dict, rutas_fig: list[str], out: Path) -> None:
    c = md["confusion"]
    ij = md["integridad_jpeg"]
    cf = md["correlacion_foto_onset"]
    dv = md["divergencia_registros_vs_bd"]
    lines = [
        f"# Informe de metricas F5a — pipeline {md['pipeline_version']}",
        "",
        f"- Generado (UTC): {md['generado_utc']}",
        f"- Hash de inputs (determinismo): {md['hash_inputs'].get('PIPELINE', '')} "
        "(script) — ver analisis.json completo",
        "",
        "## Metricas por sesion",
        "",
        "| Sesion | Duracion s | Evals | %motion | pps medio | score motion mediana | media |",
        "|---|---|---|---|---|---|---|",
    ]
    for v in md["metricas_por_sesion"]:
        sm = v.get("scores_motion", {})
        lines.append(f"| {v['sesion']} | {v.get('duracion_s')} | {v.get('n_eval')} | "
                     f"{v.get('pct_motion')} | {v.get('pps_medio')} | {sm.get('p50')} | {sm.get('media')} |")
    lines += [
        "",
        "## Matriz de confusion vs ground truth",
        "",
        f"- Metodo: {c['metodo']}",
        f"- Tramos GT: motion={c['tramos_motion_gt']}  idle={c['tramos_idle_gt']}",
        f"- TP={c['tp']}  FN={c['fn']}  **recall motion = {c['recall_motion']}**",
        f"- FP={c['fp']}  TN={c['tn']}  FPR={c['fpr']}  precision={c['precision']}",
        f"- Cobertura media de motion dentro de tramos GT: {c['cobertura_media_motion_dentro_gt']}",
        "",
        f"> **Nota honesta:** {c.get('nota_honest', '')}",
        "",
        "## Integridad JPEG (PIL estricto)",
        "",
        f"- Validas: {ij['validas']} · Rotas: {ij['rotas']} · Ausentes(no purgadas): "
        f"{ij['ausentes_no_purgadas']} · Purgadas(retencion): {ij['purgadas_retencion']}",
        f"- Excluidas fire-test (id 56 etc.): {ij['excluidas_fire_test']}",
        f"- **Tasa de integridad: {ij['tasa_integridad']}** (validas/(validas+rotas))",
        f"- Nota vision: {ij['nota_vision_error']}",
        "",
        "## Correlacion foto↔onset (±%d s)" % cf["ventana_ms"],
        "",
        f"- Fotos reales (post-exclusion): {cf['fotos']} · Onsets en rango: {cf['onsets']}",
        f"- Fotos emparejadas: {cf['emparejadas']} ({cf['fotos_emparejadas_pct']} %) · "
        f"Onsets con foto: {cf['onsets_con_foto_pct']} %",
        f"- Fotos sin match: {cf.get('fotos_sin_match')} · Onsets sin foto (±20 s): "
        f"{len(cf.get('onsets_sin_foto', []))} {str(cf.get('onsets_sin_foto', []))[:120]}",
        f"- Lag foto−onset (ms): mediana {cf['lags_ms'].get('p50')} · p95 {cf['lags_ms'].get('p95')}",
        f"- FK evento_id en fila imagen: {md['correlacion_foto_evento_fk']['pct_fk']} %",
        f"- Nota vision: {cf['nota_vision_error']}",
        "",
        "## Latencias evento→fila (recibido_en UTC − ts_ms)",
        "",
        "| Fuente | n | mediana ms | mediana corr. truncacion | p95 ms | %|diff|<2 s |",
        "|---|---|---|---|---|---|",
    ]
    for lat in (md["latencias"]["eventos"], md["latencias"]["imagenes"]):
        lines.append(f"| {lat['fuente']} | {lat['n']} | {lat.get('mediana_ms')} | "
                     f"{lat.get('mediana_corr_truncacion_ms')} | {lat.get('p95_ms')} | "
                     f"{lat.get('pct_abs_bajo_2s')} |")
    lines += [
        "",
        f"> {md['latencias']['eventos']['nota']}",
        f"> Anomalos excluidos eventos: {json.dumps(md['latencias']['eventos'].get('anomalos_excluidos', []), ensure_ascii=False)} · imagenes: {json.dumps(md['latencias']['imagenes'].get('anomalos_excluidos', []), ensure_ascii=False)}",
        "",
        "## Divergencia registros jsonl vs BD canonica (fuente de verdad: BD)",
        "",
        f"- ids en registros: {dv['evento_ids_en_registros']} · ids en BD (rango): {dv['evento_ids_en_bd_rango']} · comunes: {dv['comunes']}",
        f"- ts_ms divergentes >5 s entre dump y BD actual: {dv['n_ts_divergentes_gt5s']} (re-enganche R3/cutover; no se reescribe nada)",
        f"- {dv['nota']}",
        "",
        "## Resumen vision (solo lectura)",
        "",
        "- Estados imagenes reales: " + json.dumps(md["vision"]["estados_imagenes_reales"], ensure_ascii=False),
        f"- Tokens vision_uso totales: {md['vision']['tokens_totales']}",
        "- Uso por resultado: " + ", ".join(f"{u['resultado']}={u['n']}({u['tokens_sum']} tok)"
                                             for u in md["vision"]["vision_uso_por_resultado"]),
        f"- {md['vision']['nota']}",
        "",
        "## Graficos",
        "",
    ]
    for r in rutas_fig:
        lines.append(f"- `{r}`")
    lines += [
        "",
        "## Pendientes al llegar S2/S3 y pasada 3 de vision",
        "",
        "- Re-ejecutar el MISMO script: los registros jsonl nuevos y las filas idle del CSV",
        "  activan automaticamente FP/TN/FPR/precision (codigo ya preparado).",
        "- Las imagenes `pendiente`/`error` recuperadas por vision actualizan el resumen.",
        "- Las sesiones S2/S3 generan sus propios registros en dataset/sesion_2|3 y sus filas",
        "  en docs/validacion_sesion2|3.csv: el pipeline los descubre por glob, cero cambios.",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default=str(RAIZ / "dataset"))
    ap.add_argument("--salida", default=str(RAIZ / "docs" / "analisis"))
    ap.add_argument("--validacion", nargs="*", default=None,
                    help="CSVs GT; por defecto docs/validacion_sesion*.csv")
    ap.add_argument("--sin-wsl", action="store_true",
                    help="omitir consulta BD/JPEG (modo offline: parsing+GT+confusion local)")
    ap.add_argument("--no-graficos", action="store_true")
    args = ap.parse_args(argv)

    outdir = Path(args.salida)
    (outdir / "graficos").mkdir(parents=True, exist_ok=True)

    sesiones = cargar_registros(Path(args.dataset))
    rutas_val = ([Path(p) for p in args.validacion] if args.validacion
                 else sorted((RAIZ / "docs").glob("validacion_sesion*.csv")))
    gt = construir_ground_truth(sesiones, cargar_validacion(rutas_val))

    ventanas = [{"sesion": nom, **s["ventana"]} for nom, s in sorted(sesiones.items())
                if s["ventana"]["inicio_ms"] and s["ventana"]["fin_ms"]]
    if not ventanas and gt:
        # sesiones sin ventana registrada: derivar del GT
        mins = sorted({w["inicio_ms"] for w in gt}); maxs = sorted({w["fin_ms"] for w in gt})
        ventanas = [{"sesion": "gt_global", "inicio_ms": mins[0], "fin_ms": maxs[-1]}]

    if args.sin_wsl:
        print("[aviso] modo --sin-wsl: sin BD/JPEG (analisis parcial)", file=sys.stderr)
        wsl = {"ventanas": [], "gt_stats": [{"n_eval": 0, "n_motion_dentro": 0} for _ in gt],
               "eventos": [], "imagenes": [], "vision_uso": [], "vision_tokens_totales": 0,
               "jpeg": [], "pil_disponible": False}
    else:
        rmin = min([v["inicio_ms"] for v in ventanas] + [w["inicio_ms"] for w in gt]) - MARGEN_EVENTOS_MS
        rmax = max([v["fin_ms"] for v in ventanas] + [w["fin_ms"] for w in gt]) + MARGEN_EVENTOS_MS
        wsl = consultar_wsl({
            "db": DB_WSL, "base_datos": DATOS_WSL, "ventanas": ventanas,
            "gt": gt, "rango_eventos": [rmin, rmax], "bucket_ms": BUCKET_SERIE_MS,
            "validar_jpeg": True, "re_fire_test": RE_FIRE_TEST.pattern,
        })

    metricas = construir_metricas(sesiones, gt, wsl)

    # hash de inputs + version (determinismo). El unico campo volatil: generado_utc.
    hashes = {"PIPELINE": sha256_de(Path(__file__))}
    for arch in sorted(Path(args.dataset).glob("sesion_*/registro_*.jsonl")):
        hashes[str(arch.relative_to(RAIZ)).replace("\\", "/")] = sha256_de(arch)
    for ruta in rutas_val:
        hashes[str(ruta.relative_to(RAIZ)).replace("\\", "/")] = sha256_de(ruta)

    analisis = {
        "pipeline_version": metricas["pipeline_version"],
        "generado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hash_inputs": hashes,
        "nota_determinismo": ("analisis.json es byte-estable salvo 'generado_utc'; la "
                              "verificacion de determinismo ignora ese campo."),
        **metricas,
    }
    (outdir / "analisis.json").write_text(
        json.dumps(analisis, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    rutas_fig = []
    if not args.no_graficos:
        try:
            rutas_fig = hacer_graficos(metricas, gt, wsl, outdir / "graficos")
        except Exception as e:  # graficos no bloquean las metricas
            print(f"[aviso] graficos fallaron: {e}", file=sys.stderr)
    else:
        rutas_fig = ["(omitidos por --no-graficos)"]

    escribir_informe(analisis, rutas_fig, outdir / "informe_metricas.md")
    print(json.dumps({"salida": str(outdir), "sesiones": sorted(sesiones),
                      "tramos_gt": len(gt), "figuras": rutas_fig}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
