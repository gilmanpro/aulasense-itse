#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F5a — EDA formal de AulaSense (C5, SPEC §10: distribución score, patrones por sesión,
calidad CSI, métricas edge, efecto RSSI, outliers con motivo, cobertura de GT).

Convenciones HEREDADAS del pipeline base (analysis/analizar_dataset.py v f5a-1.1, NO se toca):
  - Autodescubrimiento de sesiones por los mismos globs: dataset/sesion_*/registro_*.jsonl y
    docs/validacion_sesion*.csv (S2/S3 se activan solos al llegar, cero cambio de código).
  - BD canónica WSL en modo SOLO LECTURA (sqlite URI mode=ro) via el mismo patrón de bridge:
    el script viaja por stdin, el payload por argv; prioridad venv WSL, fallback python3.
  - Salidas machine-readable deterministas: hashes de inputs + único campo volátil generado_utc.
  - Politicas honestas AGENTS 10/11: idle ≠ sala vacía; GT pasivo = huecos sin-dato (no idle);
    precision sobre huecos = PISO (no FPR real); die temp es proxy de carga, no temperatura.

Salidas:
  - analysis/informe_eda.md           (ruta fijada por PLAN F5a)
  - docs/analisis/eda.json            (métricas + hashes para F6a/reporte)
  - docs/analisis/graficos/eda_*.png  (5 figuras: score-hora, ACF pps, RSSI, sesiones, GT)

Uso:  C:\\Python315\\python.exe analysis/eda.py [--sin-wsl] [--salida docs/analisis]
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
import analizar_dataset as ad  # noqa: E402  (convenciones de carga de datos del pipeline)

VERSION_EDA = "f5a-2.0"
DISTRO_WSL = ad.DISTRO_WSL
DB_WSL = ad.DB_WSL
PY_VENV_WSL = ad.PY_VENV_WSL
PY_SYS_WSL = ad.PY_SYS_WSL

# Hora local del aula: UTC-5 (fuente: cabecera jsonl F4S1 "2026-11-15T12:14:15-05:00").
OFFSET_HORA_S = -5 * 3600
SCORE_BINS = 20                 # resolución de histogramas: 0.05
PPS_BUCKET_MS = 60_000          # granularidad de la serie pps para ACF
BUCKET5_MS = 300_000            # granularidad del cruce score↔RSSI
ACF_LAGS = 120                  # hasta 2 h de retardos en buckets de 60 s
UMBRAL_OUTLIER_SCORE = 0.5      # idle con score >= esto = sospechoso (motivo se etiqueta)
GAP_HUECO_S = 120               # > 2 min sin evaluaciones = hueco (nodo offline)

r4 = ad.r4
percentiles = ad.percentiles


def sha256_de(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _bins_int(bins):
    """JSON convierte las claves de dict a str: normalizar a int (determinista)."""
    return {int(k): int(v) for k, v in dict(bins).items()}


def mediana_desde_hist(bins: dict[int, int], paso: float = 1.0 / SCORE_BINS) -> float | None:
    """Mediana por interpolación de histograma (bins 0..SCORE_BINS-1). Determinista;
    aproximación a ±paso documentada en el informe."""
    bins = _bins_int(bins)
    total = sum(bins.values())
    if not total:
        return None
    acum = 0
    for b in sorted(bins):
        if acum + bins[b] >= total / 2.0:
            return r4((b + 0.5) * paso)
        acum += bins[b]
    return r4((max(bins) + 0.5) * paso)


def percentil_desde_hist(bins: dict[int, int], q: float,
                         paso: float = 1.0 / SCORE_BINS) -> float | None:
    bins = _bins_int(bins)
    total = sum(bins.values())
    if not total:
        return None
    acum = 0
    for b in sorted(bins):
        if acum + bins[b] >= q * total:
            return (b + 0.5) * paso
        acum += bins[b]
    return (max(bins) + 0.5) * paso


def autocorrelacion(serie: list[float], lags: int) -> list[float | None]:
    """ACCF estimador sesgado (StdLib, determinista): acf(k) = cov(x_t, x_{t+k}) / var(x).
    Sin relleno: la serie ya viene alineada a buckets (huecos = 0 con nota honesta)."""
    n = len(serie)
    if n < lags + 2:
        lags = max(1, n // 2)
    mu = sum(serie) / n if n else 0.0
    desv = [x - mu for x in serie]
    var0 = sum(d * d for d in desv)
    out: list[float | None] = [1.0]
    for k in range(1, lags + 1):
        if var0 == 0:
            out.append(None)
            continue
        cov = sum(desv[t] * desv[t + k] for t in range(n - k)) / (n - k)
        out.append(r4(cov / (var0 / n)))
    return out


def rellenar_pps(filas: list[list], bucket_ms: int = PPS_BUCKET_MS) -> tuple[list, list]:
    """Serie [t0_ms, n] → serie densa en buckets consecutivos (huecos => 0).
    Devuelve (t0s, counts) determinista."""
    if not filas:
        return [], []
    ts = sorted(int(f[0]) for f in filas)
    t0, t1 = ts[0], ts[-1]
    n_by = {int(f[0]): int(f[1]) for f in filas}
    t0s, counts = [], []
    t = t0 - (t0 % bucket_ms)
    while t <= t1:
        t0s.append(t)
        counts.append(n_by.get(t, 0))
        t += bucket_ms
    return t0s, counts


def etiquetar_outliers(huecos: list[dict], idle_altos: list[dict],
                       ts_anomalos: list[dict], n_hueco: int) -> list[dict]:
    """Outliers con MOTIVO (dispatch F5a-1). Clasificación determinista:
      - huecos de series > GAP_HUECO_S  -> motivo NODO_OFFLINE (reenganche R3)
      - idle con score >= UMBRAL        -> motivo BURST_ESPONTANEO (F4S1 probó bursts reales
                                          sin caminata: validacion_sesiones 'manual_operador')
      - ts_ms fuera del rango de evaluaciones -> motivo EPOCA_ANOMALA (fire-test x2 / R3)
    Se agrupan por tipo con conteo y ejemplos (≤5) para el informe."""
    out = []
    if huecos:
        dur = [h["gap_s"] for h in huecos]
        out.append({
            "tipo": "HUECO_SERIE",
            "motivo": "nodo offline/re-enganche (eventos NODO_OFFLINE/ONLINE; época R3 "
                      "monótona del nodo, sin SNTP — SPEC §13)",
            "n": len(huecos), "gap_max_s": r4(max(dur)), "gap_mediana_s": r4(statistics.median(dur)),
            "ejemplos": huecos[:5],
        })
    if idle_altos:
        out.append({
            "tipo": "IDLE_SCORE_ALTO",
            "motivo": "score >= %.2f con state='idle': burst CSI espontáneo (F4 registró "
                      "movimiento real sin caminata dirigida) o borde de histéresis del "
                      "detector ESPectre — no re-entrenable aquí, se reporta" % UMBRAL_OUTLIER_SCORE,
            "n": n_hueco, "ejemplos": idle_altos[:5],
        })
    if ts_anomalos:
        out.append({
            "tipo": "TS_MS_ANOMALO",
            "motivo": "ts_ms fuera del rango de evaluaciones ±1 h: fire-test con época x2 "
                      "(id 56) o fila re-enganchada tras cutover — excluida de correlaciones "
                      "por el pipeline base",
            "n": len(ts_anomalos), "ejemplos": ts_anomalos[:5],
        })
    return out


# ---------------------------------------------------------------- consulta WSL (EDA)
CONSULTA_EDA_PY = r'''# -*- coding: utf-8 -*-
# Sonda EDA SOLO-LECTURA (mode=ro) sobre la BD canonica WSL. Mismo patron del pipeline:
# script por stdin, payload JSON por argv[1]. NO escribe en disco/BD.
import json, sqlite3, sys

payload = json.loads(sys.argv[1])
con = sqlite3.connect("file:" + payload["db"] + "?mode=ro", uri=True)
q = lambda s, p=(): con.execute(s, p).fetchall()
OFF = payload["offset_hora_s"]
SB = payload["score_bins"]
res = {"wsl_ok": True}

def hour_local(col):
    # hora local 0..23 via strftime con modificador de segundos UTC->local (sin %-format
    # en la plantilla: los literales % de strftime romperian el operador %)
    mod = "%+d seconds" % OFF
    return ("CAST(strftime('%H', datetime(" + col + "/1000, 'unixepoch', "
            + repr(mod) + ")) AS INTEGER)")

# 1) histograma score x hora local (todas las evaluaciones reales)
res["hist_hora"] = q("SELECT " + hour_local("ts_ms") + " h, MIN(CAST(score*? AS INTEGER), ?-1) b, "
                     "COUNT(*) FROM evaluaciones WHERE origen='real' AND score IS NOT NULL "
                     "AND ts_ms BETWEEN ? AND ? GROUP BY h, b ORDER BY h, b",
                     (SB, SB, payload["rango"][0], payload["rango"][1]))
res["stats_hora"] = q("SELECT " + hour_local("ts_ms") + " h, COUNT(*), SUM(state='motion') "
                      "FROM evaluaciones WHERE origen='real' AND ts_ms BETWEEN ? AND ? "
                      "GROUP BY h ORDER BY h", (payload["rango"][0], payload["rango"][1]))

# 2) pps por bucket de 60 s (serie para ACF)
bk = payload["pps_bucket_ms"]
res["pps"] = q("SELECT CAST(ts_ms/? AS INTEGER)*? t, COUNT(*) FROM evaluaciones "
               "WHERE origen='real' AND ts_ms BETWEEN ? AND ? GROUP BY t ORDER BY t",
               (bk, bk, payload["rango"][0], payload["rango"][1]))

# 3) RSSI: histograma y cruce con score por bucket de 5 min
res["rssi_hist"] = q("SELECT CAST(wifi_rssi_dbm AS INTEGER) r, COUNT(*) FROM telemetria "
                     "WHERE origen='real' AND wifi_rssi_dbm IS NOT NULL GROUP BY r ORDER BY r")
b5 = payload["bucket5_ms"]
res["eval5"] = q("SELECT CAST(ts_ms/? AS INTEGER)*? t, COUNT(*), SUM(state='motion'), "
                 "AVG(score) FROM evaluaciones WHERE origen='real' AND ts_ms BETWEEN ? AND ? "
                 "GROUP BY t ORDER BY t", (b5, b5, payload["rango"][0], payload["rango"][1]))
res["rssi5"] = q("SELECT CAST(ts_ms/? AS INTEGER)*? t, AVG(wifi_rssi_dbm), AVG(die_temp_c), "
                 "AVG(runtime_load_percent), AVG(csi_accepted_pps) FROM telemetria "
                 "WHERE origen='real' AND ts_ms BETWEEN ? AND ? AND wifi_rssi_dbm IS NOT NULL "
                 "GROUP BY t ORDER BY t", (b5, b5, payload["rango"][0], payload["rango"][1]))

# 4) comparativa por sesion (ventanas descubiertas de los jsonl)
ses_stats = []
for v in payload["ventanas"]:
    if v["inicio_ms"] is None or v["fin_ms"] is None:
        continue
    n_eval, n_motion = q("SELECT COUNT(*), SUM(state='motion') FROM evaluaciones WHERE "
                        "origen='real' AND ts_ms BETWEEN ? AND ?", (v["inicio_ms"], v["fin_ms"]))[0]
    bins = {b: c for b, c, in q("SELECT MIN(CAST(score*? AS INTEGER), ?-1) b, COUNT(*) FROM "
                                "evaluaciones WHERE origen='real' AND state='motion' "
                                "AND score IS NOT NULL AND ts_ms BETWEEN ? AND ? GROUP BY b",
                                (SB, SB, v["inicio_ms"], v["fin_ms"]))}
    tel = q("SELECT AVG(wifi_rssi_dbm), AVG(die_temp_c), AVG(runtime_load_percent), "
            "AVG(csi_accepted_pps) FROM telemetria WHERE origen='real' AND ts_ms BETWEEN ? AND ?",
            (v["inicio_ms"], v["fin_ms"]))[0]
    n_ev, n_fotos = q("SELECT COUNT(*), SUM(state='motion') FROM evaluaciones WHERE origen='real' "
                      "AND ts_ms BETWEEN ? AND ? AND score >= ?",
                      (v["inicio_ms"], v["fin_ms"], payload["umbral_score"]))[0]
    ses_stats.append({"sesion": v["sesion"], "n_eval": n_eval, "n_motion": n_motion,
                      "hist_motion": bins, "duracion_ms": v["fin_ms"] - v["inicio_ms"],
                      "rssi": tel[0], "die_temp": tel[1], "load": tel[2], "pps_csi": tel[3],
                      "n_score_ge_umbral": n_ev})
res["sesiones"] = ses_stats

# 5) outliers con motivo
UA = payload["rango"][1] - payload["rango"][0]
res["idle_altos_n"] = q("SELECT COUNT(*) FROM evaluaciones WHERE origen='real' AND state='idle' "
                        "AND score >= ? AND ts_ms BETWEEN ? AND ?",
                        (payload["umbral_score"], payload["rango"][0], payload["rango"][1]))[0][0]
res["idle_altos"] = [[i, t, round(s, 4) if s is not None else None] for i, t, s in q(
    "SELECT id, ts_ms, score FROM evaluaciones WHERE origen='real' AND state='idle' "
    "AND score >= ? AND ts_ms BETWEEN ? AND ? ORDER BY score DESC LIMIT 10",
    (payload["umbral_score"], payload["rango"][0], payload["rango"][1]))]
res["huecos"] = [{"t_ini_ms": a, "t_fin_ms": b, "gap_s": round((b - a) / 1000.0, 1)}
                 for a, b in q("WITH s AS (SELECT ts_ms, LAG(ts_ms) OVER (ORDER BY ts_ms) p "
                               "FROM evaluaciones WHERE origen='real' AND ts_ms BETWEEN ? AND ?) "
                               "SELECT p, ts_ms FROM s WHERE ts_ms - p > ? ORDER BY ts_ms - p DESC "
                               "LIMIT 40", (payload["rango"][0], payload["rango"][1],
                                            payload["gap_hueco_ms"]))]
res["ts_anomalos"] = [{"tabla": "imagenes", "id": i, "ts_ms": t} for i, t in q(
    "SELECT id, ts_ms FROM imagenes WHERE origen='real' AND (ts_ms < ? OR ts_ms > ?)",
    (payload["rango"][0] - 3600000, payload["rango"][1] + 3600000))]

# 6) fotos y onsets para la cobertura de GT
# GT foto↔onset (auditoría 2026-11-15, MEDIO): SOLO se excluye 'omitido'/FIRE-TEST.
# Las fotos con vision_estado='error' (JPEG irrecoverable) SE MANTIENEN: son evidencia
# física de captura al onset aunque el píxel esté roto; su cuenta se reporta aparte
# (fotos_error) para que el informe diga "evidencia física, no material visión".
res["fotos"] = [[i, t] for i, t in q(
    "SELECT id, ts_ms FROM imagenes WHERE origen='real' AND ts_ms BETWEEN ? AND ? "
    "AND COALESCE(vision_estado,'') != 'omitido' AND UPPER(COALESCE(descripcion,'')) NOT LIKE "
    "'%FIRE-TEST%' ORDER BY ts_ms", (payload["rango"][0], payload["rango"][1]))]
res["fotos_error"] = [[i, t] for i, t in q(
    "SELECT id, ts_ms FROM imagenes WHERE origen='real' AND ts_ms BETWEEN ? AND ? "
    "AND COALESCE(vision_estado,'') = 'error' AND UPPER(COALESCE(descripcion,'')) NOT LIKE "
    "'%FIRE-TEST%' ORDER BY ts_ms", (payload["rango"][0], payload["rango"][1]))]
res["onsets"] = [[i, t, s] for i, t, s in q(
    "SELECT id, ts_ms, score FROM eventos WHERE origen='real' AND tipo='MOVIMIENTO_INICIO' "
    "AND ts_ms BETWEEN ? AND ? ORDER BY ts_ms", (payload["rango"][0], payload["rango"][1]))]
res["config"] = [[t, ts, (d or "")[:60]] for t, ts, d in q(
    "SELECT tipo, ts_ms, detalle FROM config_eventos WHERE origen='real' ORDER BY id")]
res["totals"] = q("SELECT COUNT(*), SUM(state='motion'), MIN(ts_ms), MAX(ts_ms) FROM evaluaciones "
                  "WHERE origen='real'")[0]
print(json.dumps(res, ensure_ascii=False))
'''


def consultar_wsl_eda(payload: dict) -> dict:
    """Puente WSL solo-lectura, mismo patron que ad.consultar_wsl (el script del pipeline
    tiene su consulta fijada; se duplica SOLO el transporte, documentado)."""
    errs = []
    for py in (PY_VENV_WSL, PY_SYS_WSL):
        r = subprocess.run(["wsl", "-d", DISTRO_WSL, "-u", "root", "--", py, "-",
                            json.dumps(payload, ensure_ascii=False)],
                           input=CONSULTA_EDA_PY, capture_output=True, text=True,
                           encoding="utf-8", timeout=300)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
        errs.append(f"{py}: {(r.stderr or r.stdout).strip()[:200]}")
    raise RuntimeError("consulta WSL EDA fallo en ambos interpretes:\n" + "\n".join(errs))


# ---------------------------------------------------------------- calculos EDA
def analizar_horas(wsl: dict) -> dict:
    """Distribucion de score por momento del dia (hora local UTC-5) desde el histograma."""
    por_hora: dict[int, dict] = {}
    for h, b, c in wsl["hist_hora"]:
        d = por_hora.setdefault(int(h), {"n": 0, "hist": {}})
        d["hist"][int(b)] = d["hist"].get(int(b), 0) + int(c)
        d["n"] += int(c)
    stats = {int(h): {"n_eval": int(n), "n_motion": int(m or 0)} for h, n, m in wsl["stats_hora"]}
    out = {}
    for h in sorted(set(por_hora) | set(stats)):
        st = stats.get(h, {"n_eval": 0, "n_motion": 0})
        hh = por_hora.get(h, {"hist": {}})["hist"]
        out[h] = {
            "n_eval": st["n_eval"],
            "n_motion": st["n_motion"],
            "pct_motion": r4(100.0 * st["n_motion"] / st["n_eval"]) if st["n_eval"] else None,
            "score_mediana": mediana_desde_hist(hh),
            "score_p10": r4(percentil_desde_hist(hh, 0.10)),
            "score_p90": r4(percentil_desde_hist(hh, 0.90)),
            "frac_over_05": r4(sum(c for b, c in hh.items() if b >= int(0.5 * SCORE_BINS)) /
                              sum(hh.values())) if hh else None,
        }
    horas = sorted(out, key=lambda h: out[h]["n_motion"], reverse=True)
    return {"por_hora_local": out,
            "hora_pico_motion": horas[0] if horas else None,
            "nota": ("histograma de score a resolucion 0.05 (mediana/p10/p90 aproximadas a "
                     "±0.025); hora local = UTC-5 segun cabecera de las sesiones F4")}


def analizar_pps(wsl: dict) -> dict:
    """Autocorrelacion de la serie pps (buckets 60 s, huecos rellenos con 0)."""
    t0s, counts = rellenar_pps(wsl["pps"])
    acf = autocorrelacion([float(c) for c in counts], ACF_LAGS)
    n = len(counts)
    ic95 = r4(1.96 / math.sqrt(n)) if n else None
    lag_signif = next((k for k, v in enumerate(acf) if k >= 6 and v is not None and abs(v) < ic95),
                      None) if ic95 else None
    return {"n_buckets": n, "pps_medio": r4(sum(counts) / n / (PPS_BUCKET_MS / 1000.0)) if n else None,
            "acf_lag_s": PPS_BUCKET_MS // 1000, "acf": acf,
            "intervalo_confianza_95": ic95,
            "primer_lag_no_significativo_s": (lag_signif * PPS_BUCKET_MS // 1000) if lag_signif else None,
            "nota": ("ACF estimador sesgado cov(k)/var(0) sobre serie densa de buckets de 60 s; "
                     "los huecos de nodo offline entran como 0 => correlacion negativa de larga "
                     "escala atribuible a los huecos (ver outliers HUECO_SERIE), no a ritmo "
                     "humano intrinseco; el GT F4S1B es una unica manana, no hay ciclo diario "
                     "muestreado completo (limitacion S13)")}


def analizar_rssi(wsl: dict) -> dict:
    """Efecto RSSI: histograma y cruce score↔RSSI por bucket de 5 min."""
    hist = {int(r): int(c) for r, c in wsl["rssi_hist"]}
    tot = sum(hist.values())
    vals = sorted(v for v, c in hist.items() for _ in range(c))
    r5 = {int(t): {"rssi": rr, "die_temp": dt, "load": ld, "pps_csi": pc}
          for t, rr, dt, ld, pc in wsl["rssi5"]}
    pares = []
    for t, n_eval, n_mot, avg_s in wsl["eval5"]:
        m = r5.get(int(t))
        if m and m["rssi"] is not None and avg_s is not None and n_eval:
            pares.append((m["rssi"], float(avg_s), int(n_eval)))
    xs = [p[0] for p in pares]
    ys = [p[1] for p in pares]
    def pearson(a, b):
        n = len(a)
        if n < 3:
            return None
        ma, mb = sum(a) / n, sum(b) / n
        sa = math.sqrt(sum((x - ma) ** 2 for x in a)); sb = math.sqrt(sum((y - mb) ** 2 for y in b))
        return None if sa == 0 or sb == 0 else sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)
    por_bin_rssi: dict[int, dict] = {}
    for rssi, s, wgt in pares:
        b = int(rssi)
        d = por_bin_rssi.setdefault(b, {"n": 0, "ws": 0.0, "smax": 0.0})
        d["n"] += wgt; d["ws"] += s * wgt; d["smax"] = max(d["smax"], s)
    bins = {b: {"n_eval": d["n"], "score_medio": r4(d["ws"] / d["n"])} for b, d in sorted(por_bin_rssi.items())}
    return {"n_telemetria": tot,
            "rssi_min": vals[0] if vals else None, "rssi_max": vals[-1] if vals else None,
            "rssi_mediana": vals[len(vals) // 2] if vals else None,
            "corr_bucket_score_vs_rssi": r4(pearson(xs, ys)),
            "n_buckets_cruce": len(pares),
            "score_por_rssi_dbm": bins,
            "nota": ("nodo junto al AP: RSSI en rango estrecho (-31 a -14 dBm) => NO se observa "
                     "efecto de distancia; la correlacion por bucket refleja variabilidad del "
                     "canal/ruido, no un experimento de RSSI. Limitacion declarada SPEC §13 "
                     "(sensible al entorno RF, una placa junto al AP)")}


def analizar_sesiones(wsl: dict) -> dict:
    """Comparativa autodescubierta por globs (F4S1 vs F4S1B; S2/S3 entran solas)."""
    out = []
    for s in wsl["sesiones"]:
        dur_s = s["duracion_ms"] / 1000.0
        out.append({
            "sesion": s["sesion"],
            "duracion_s": r4(dur_s),
            "n_eval": s["n_eval"],
            "pct_motion": r4(100.0 * (s["n_motion"] or 0) / s["n_eval"]) if s["n_eval"] else None,
            "pps_eval": r4(s["n_eval"] / dur_s) if dur_s else None,
            "score_motion_mediana": mediana_desde_hist(s["hist_motion"]),
            "frac_score_ge_umbral": r4(s["n_score_ge_umbral"] / s["n_eval"]) if s["n_eval"] else None,
            "telemetria": {"rssi_dbm": r4(s["rssi"]), "die_temp_c": r4(s["die_temp"]),
                            "runtime_load_pct": r4(s["load"]), "csi_accepted_pps": r4(s["pps_csi"])},
        })
    return {"por_sesion": out,
            "nota": "ventanas desde dataset/sesion_*/registro_*.jsonl (autodescubrimiento por "
                    "glob como el pipeline); F4S1 es la pasada en aula vacia (100 % idle segun "
                    "cabecera), F4S1B la pasada pasiva corroborada por fotos"}


def cobertura_gt(wsl: dict, gt: list[dict]) -> dict:
    """Cobertura temporal del GT: fraccion de segundos de cada tramo motion cubiertos por
    evaluaciones state=motion (densidad), y fotos/onsets dentro.

    Las fotos con vision_estado='error' (JPEG irrecoverable) cuentan como evidencia fisica
    de captura (`n_fotos_dentro`), NO como material de vision: se reportan aparte en
    `n_fotos_vision_error` y la nota lo explicita (auditoria 2026-11-15, MEDIO)."""
    b5 = {int(t): {"n": int(n), "m": int(m or 0)} for t, n, m, _ in wsl["eval5"]}
    fotos = [f[1] for f in wsl["fotos"]]
    n_vision_error = len(wsl.get("fotos_error") or [])
    onsets = [o[1] for o in wsl["onsets"]]
    tramos = []
    for w in gt:
        if w["verdad_terreno"] != "motion":
            continue
        ini, fin = w["inicio_ms"], w["fin_ms"]
        n_eval = n_mot = 0
        t = ini - (ini % BUCKET5_MS)
        while t < fin:
            d = b5.get(t)
            if d:
                ov_ini = max(t, ini); ov_fin = min(t + BUCKET5_MS, fin)
                frac = (ov_fin - ov_ini) / BUCKET5_MS
                n_eval += d["n"] * frac
                n_mot += d["m"] * frac
            t += BUCKET5_MS
        tramos.append({
            "sesion": w["sesion"], "inicio_ms": ini, "fin_ms": fin,
            "duracion_s": r4((fin - ini) / 1000.0),
            "densidad_motion": r4(n_mot / n_eval) if n_eval else None,
            "n_fotos_dentro": sum(1 for f in fotos if ini <= f <= fin),
            "n_onsets_dentro": sum(1 for o in onsets if ini <= o <= fin),
        })
    cubiertos = sum(1 for t in tramos if t["densidad_motion"] and t["densidad_motion"] > 0)
    dens = [t["densidad_motion"] for t in tramos if t["densidad_motion"] is not None]
    return {"n_tramos_motion": len(tramos),
            "tramos_con_motion": cubiertos,
            "pct_tramos_cubiertos": r4(100.0 * cubiertos / len(tramos)) if tramos else None,
            "densidad_motion_mediana": r4(statistics.median(dens)) if dens else None,
            "n_fotos_vision_error": n_vision_error,
            "tramos": tramos,
            "nota": "densidad = fraccion de evaluaciones motion dentro del tramo GT (buckets "
                    "de 5 s pesados por solape); tramo sin evaluaciones = sin-dato, no idle. "
                    + ad.texto_vision_error(n_vision_error)}


# ---------------------------------------------------------------- figuras
FIGS = ["eda_score_por_hora.png", "eda_autocorrelacion_pps.png", "eda_histograma_rssi.png",
        "eda_sesiones_comparativa.png", "eda_cobertura_gt.png"]


def hacer_figuras(eda: dict, wsl: dict, outdir: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt, dates as mdates

    rutas: list[str] = []
    gdir = outdir / "graficos"
    gdir.mkdir(parents=True, exist_ok=True)
    if not wsl.get("eval5"):
        print("[aviso] EDA sin series (eval5 vacio): no se generan figuras", file=sys.stderr)
        return rutas
    clave = lambda epoch_ms: mdates.date2num(datetime.fromtimestamp(epoch_ms / 1000.0, timezone.utc))
    guardado = lambda fig, nombre: rutas.append(str(_save(fig, gdir / nombre)))

    # 4) score vs hora local
    ph = eda["score_por_hora"]["por_hora_local"]
    horas = sorted(ph)
    fig, ax1 = plt.subplots(figsize=(10, 4.8))
    med = [ph[h]["score_mediana"] or 0 for h in horas]
    p10 = [ph[h]["score_p10"] or 0 for h in horas]
    p90 = [ph[h]["score_p90"] or 0 for h in horas]
    ax1.fill_between(horas, p10, p90, alpha=0.25, color="#1f77b4", label="p10–p90 score")
    ax1.plot(horas, med, "o-", color="#1f77b4", label="mediana score")
    ax1.axhline(0.5, color="orange", ls="--", lw=1, label="umbral 0.5")
    ax1.set_xlabel("hora del día (local, UTC−5)"); ax1.set_ylabel("score")
    ax1.set_xticks(list(range(0, 24, 2)))
    ax2 = ax1.twinx()
    ax2.bar(horas, [(ph[h]["pct_motion"] or 0) for h in horas], alpha=0.18, color="#d62728",
            width=0.7, label="% evaluaciones motion")
    ax2.set_ylabel("% motion")
    h1, l1 = ax1.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8)
    ax1.set_title("Distribución de score por momento del día (todas las evaluaciones reales)")
    fig.tight_layout(); guardado(fig, FIGS[0])

    # 5) autocorrelacion pps
    pp = eda["pps_autocorrelacion"]
    lags = list(range(len(pp["acf"])))
    acf = pp["acf"]
    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.bar(lags, [a if a is not None else 0 for a in acf], width=0.8, color="#2ca02c")
    ic = pp["intervalo_confianza_95"] or 0
    ax.axhline(ic, color="gray", ls="--", lw=1, label=f"IC 95 % (±{ic:.3f})")
    ax.axhline(-ic, color="gray", ls="--", lw=1)
    ax.set_xlabel(f"retardo (buckets de {pp['acf_lag_s']} s)")
    ax.set_ylabel("autocorrelación (ACF)")
    ax.set_title(f"Autocorrelación de la tasa de evaluaciones (pps, n={pp['n_buckets']} buckets)")
    ax.legend(); fig.tight_layout(); guardado(fig, FIGS[1])

    # 6) histograma RSSI + score medio por RSSI
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4.5))
    rs = eda["rssi_efecto"]
    bins = sorted(rs["score_por_rssi_dbm"])
    a.hist([v for v, c in wsl["rssi_hist"] for _ in range(int(c))],
           bins=list(range(min(v for v, _ in wsl["rssi_hist"]) - 1,
                           max(v for v, _ in wsl["rssi_hist"]) + 2)),
           color="#7f7f7f")
    a.set_xlabel("RSSI Wi-Fi (dBm)"); a.set_ylabel("muestras telemetría")
    a.set_title("Histograma de RSSI (nodo junto al AP: rango −31…−14 dBm)")
    if bins:
        b.plot(bins, [rs["score_por_rssi_dbm"][x]["score_medio"] for x in bins], "o-",
               color="#9467bd")
    b.axhline(0.5, color="orange", ls="--", lw=1)
    b.set_xlabel("RSSI (dBm, bucket 5 min)"); b.set_ylabel("score medio")
    b.set_title(f"Score ↔ RSSI (ρ={rs['corr_bucket_score_vs_rssi']}, "
                f"{rs['n_buckets_cruce']} buckets)")
    fig.tight_layout(); guardado(fig, FIGS[2])

    # 7) comparativa sesiones
    ses = eda["sesiones_comparativa"]["por_sesion"]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    xs = range(len(ses))
    ax.bar([x - 0.2 for x in xs], [s["pct_motion"] or 0 for s in ses], width=0.4,
           color="#d62728", label="% motion")
    ax.bar([x + 0.2 for x in xs], [(s["score_motion_mediana"] or 0) * 100 for s in ses],
           width=0.4, color="#1f77b4", label="mediana score motion ×100")
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"{s['sesion']}\nn={s['n_eval']} · pps={s['pps_eval']}" for s in ses])
    ax.set_title("Comparativa entre sesiones autodescubiertas (glob dataset/sesion_*)")
    ax.legend()
    fig.tight_layout(); guardado(fig, FIGS[3])

    # 8) cobertura GT (linea de tiempo tramos + fotos)
    fig, ax = plt.subplots(figsize=(13, 4.8))
    b5 = [(int(t), (int(m or 0) / int(n)) if n else 0) for t, n, m, _ in wsl["eval5"]]
    ts = [clave(t) for t, _ in b5]; fm = [v for _, v in b5]
    ax.plot(ts, fm, lw=0.9, color="#d62728")
    ax.fill_between(ts, fm, step="pre", alpha=0.35, color="#d62728")
    for w in eda["_gt"]:
        if w["verdad_terreno"] != "motion":
            continue
        ax.axvspan(clave(w["inicio_ms"]), clave(w["fin_ms"]), alpha=0.15, color="green")
    for f in wsl["fotos"]:
        ax.axvline(clave(f[1]), ymin=0.0, ymax=0.06, color="#1f77b4", lw=0.8)
    if wsl["fotos"]:
        ax.text(clave(wsl["fotos"][0][1]), 1.06,
                "│ = foto de evidencia (real, no fire-test)", fontsize=8, color="#1f77b4")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=timezone.utc))
    ax.set_ylim(0, 1.12)
    # zoom a la ventana del GT (+15 min de margen): con el rango completo de 22 h los tramos
    # quedan ilegiblemente comprimidos (los datos fuera de GT siguen visibles en otras figuras)
    if eda["_gt"]:
        gini = min(w["inicio_ms"] for w in eda["_gt"]) - 900_000
        gfin = max(w["fin_ms"] for w in eda["_gt"]) + 900_000
        ax.set_xlim(clave(gini), clave(gfin))
    ax.set_title("Cobertura del GT: % motion por bucket 5 min · tramos motion sombreados · "
                 "fotos marcadas")
    ax.set_ylabel("fracción motion")
    fig.tight_layout(); guardado(fig, FIGS[4])
    return rutas


def _save(fig, path: Path) -> Path:
    fig.savefig(path, dpi=130)
    plt_close(fig)
    return path


def plt_close(fig):
    import matplotlib.pyplot as plt
    plt.close(fig)


# ---------------------------------------------------------------- main
def construir_eda(wsl: dict, gt: list[dict]) -> dict:
    eda = {
        "rango": {"inicio_ms": wsl["rango"][0], "fin_ms": wsl["rango"][1],
                  "utc_inicio": datetime.fromtimestamp(wsl["rango"][0] / 1000, timezone.utc).isoformat(timespec="seconds"),
                  "utc_fin": datetime.fromtimestamp(wsl["rango"][1] / 1000, timezone.utc).isoformat(timespec="seconds"),
                  "n_eval": wsl["totals"][0], "n_motion": wsl["totals"][1]},
        "score_por_hora": analizar_horas(wsl),
        "pps_autocorrelacion": analizar_pps(wsl),
        "rssi_efecto": analizar_rssi(wsl),
        "sesiones_comparativa": analizar_sesiones(wsl),
        "cobertura_gt": cobertura_gt(wsl, gt),
        "outliers_motivo": etiquetar_outliers(wsl.get("huecos", []), wsl.get("idle_altos", []),
                                              wsl.get("ts_anomalos", []),
                                              wsl.get("idle_altos_n", 0)),
        "config_eventos": wsl.get("config", []),
    }
    return eda


def escribir_informe(eda: dict, figuras: list[str], out: Path) -> None:
    r = eda["rango"]; sh = eda["score_por_hora"]; pp = eda["pps_autocorrelacion"]
    rs = eda["rssi_efecto"]; se = eda["sesiones_comparativa"]; cb = eda["cobertura_gt"]
    lineas = [
        f"# Informe EDA F5a — eda.py {VERSION_EDA}",
        "",
        f"- Rango analizado (origen=real): {r['utc_inicio']} → {r['utc_fin']} UTC · "
        f"{r['n_eval']} evaluaciones ({r['n_motion']} motion)",
        f"- Determinismo: ver docs/analisis/eda.json (hash_inputs; único campo volátil generado_utc)",
        "",
        "## 1. Score por momento del día (hora local UTC−5)",
        "",
        "| hora | n eval | % motion | mediana score | p10 | p90 | frac score≥0.5 |",
        "|---|---|---|---|---|---|---|",
    ]
    for h in sorted(sh["por_hora_local"]):
        d = sh["por_hora_local"][h]
        lineas.append(f"| {h:02d} | {d['n_eval']} | {d['pct_motion']} | {d['score_mediana']} | "
                      f"{d['score_p10']} | {d['score_p90']} | {d['frac_over_05']} |")
    lineas += ["", f"> {sh['nota']}", "", "## 2. Autocorrelación de la tasa de evaluaciones (pps)",
               "", f"- n buckets 60 s: {pp['n_buckets']} · media: {pp['pps_medio']} evaluaciones/s",
               f"- ACF lag1={pp['acf'][1] if len(pp['acf'])>1 else None} · "
               f"lag5={pp['acf'][5] if len(pp['acf'])>5 else None} · "
               f"lag60={pp['acf'][60] if len(pp['acf'])>60 else None}",
               f"- primer lag no significativo: "
               f"{(str(pp['primer_lag_no_significativo_s']) + ' s') if pp['primer_lag_no_significativo_s'] is not None else 'no decae bajo el IC 95 % en 120 lags (memoria larga por los huecos offline)'} "
               f"(IC 95 % = ±{pp['intervalo_confianza_95']})",
               f"> {pp['nota']}", "", "## 3. Efecto del RSSI", "",
               f"- muestras telemetría con RSSI: {rs['n_telemetria']} · rango {rs['rssi_min']}…"
               f"{rs['rssi_max']} dBm · mediana {rs['rssi_mediana']} dBm",
               f"- correlación bucket-score ↔ RSSI: {rs['corr_bucket_score_vs_rssi']} "
               f"(n={rs['n_buckets_cruce']})",
               f"> {rs['nota']}", "", "## 4. Comparativa entre sesiones", "",
               "| sesión | dur s | n eval | % motion | pps | mediana score motion | RSSI | die temp |",
               "|---|---|---|---|---|---|---|---|"]
    for s in se["por_sesion"]:
        t = s["telemetria"]
        lineas.append(f"| {s['sesion']} | {s['duracion_s']} | {s['n_eval']} | {s['pct_motion']} | "
                      f"{s['pps_eval']} | {s['score_motion_mediana']} | {t['rssi_dbm']} | "
                      f"{t['die_temp_c']} |")
    lineas += ["", f"> {se['nota']}", "", "## 5. Cobertura del ground truth", "",
               f"- tramos GT motion: {cb['n_tramos_motion']} · con ≥1 motion: "
               f"{cb['tramos_con_motion']} ({cb['pct_tramos_cubiertos']} %) · densidad motion "
               f"mediana en tramos: {cb['densidad_motion_mediana']}",
               f"- tramos con 0 evaluaciones (sin-dato): "
               f"{sum(1 for t in cb['tramos'] if t['densidad_motion'] is None)}",
               f"> {cb['nota']}", "", "## 6. Outliers con motivo", ""]
    if not eda["outliers_motivo"]:
        lineas.append("- ninguno detectado")
    for o in eda["outliers_motivo"]:
        lineas.append(f"- **{o['tipo']}** (n={o['n']}): {o['motivo']}")
        if "gap_max_s" in o:
            lineas.append(f"  - hueco máx {o['gap_max_s']} s · mediana huecos {o['gap_mediana_s']} s")
    lineas += ["", "## Figuras", ""] + [f"- `{f}`" for f in figuras] + [""]
    out.write_text("\n".join(lineas), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="F5a EDA formal (lea el docstring)")
    ap.add_argument("--dataset", default=str(RAIZ / "dataset"))
    ap.add_argument("--salida", default=str(RAIZ / "docs" / "analisis"))
    ap.add_argument("--informe", default=str(RAIZ / "analysis" / "informe_eda.md"))
    ap.add_argument("--no-figuras", action="store_true")
    ap.add_argument("--sin-wsl", action="store_true", help="smoke local sin BD (fixture minima)")
    ap.add_argument("--db", default=DB_WSL,
                    help="ruta BD dentro de WSL (default canonica; usar snapshot para tests de "
                         "determinismo con el nodo vivo: la BD real crece entre corridas)")
    args = ap.parse_args(argv)

    outdir = Path(args.salida); outdir.mkdir(parents=True, exist_ok=True)
    sesiones = ad.cargar_registros(Path(args.dataset))
    rutas_val = sorted((RAIZ / "docs").glob("validacion_sesion*.csv"))
    gt = ad.construir_ground_truth(sesiones, ad.cargar_validacion(rutas_val))

    if args.sin_wsl:
        print("[aviso] modo --sin-wsl: EDA con wsl vacio (solo valida flujo)", file=sys.stderr)
        wsl = {"hist_hora": [], "stats_hora": [], "pps": [], "rssi_hist": [], "eval5": [],
               "rssi5": [], "sesiones": [], "idle_altos": [], "idle_altos_n": 0, "huecos": [],
               "ts_anomalos": [], "fotos": [], "fotos_error": [], "onsets": [], "config": [],
               "totals": [0, 0, 0, 0],
               "rango": [0, 0]}
        eda = None
    else:
        vals_ms = [w["inicio_ms"] for w in gt] + [s["ventana"]["inicio_ms"] for s in sesiones.values()
                                                  if s["ventana"]["inicio_ms"]]
        fns_ms = [w["fin_ms"] for w in gt] + [s["ventana"]["fin_ms"] for s in sesiones.values()
                                              if s["ventana"]["fin_ms"]]
        # rango EDA = todo el origen=real de la BD (la tabla lo acota por si acaso: ±30 dias)
        rmin = min(vals_ms) - 30 * 86_400_000 if vals_ms else 0
        rmax = max(fns_ms) + 30 * 86_400_000 if fns_ms else 2 ** 62
        ventanas = [{"sesion": nom, **s["ventana"]} for nom, s in sorted(sesiones.items())
                    if s["ventana"]["inicio_ms"] and s["ventana"]["fin_ms"]]
        wsl = consultar_wsl_eda({
            "db": args.db, "rango": [rmin, rmax], "offset_hora_s": OFFSET_HORA_S,
            "score_bins": SCORE_BINS, "pps_bucket_ms": PPS_BUCKET_MS, "bucket5_ms": BUCKET5_MS,
            "umbral_score": UMBRAL_OUTLIER_SCORE, "gap_hueco_ms": GAP_HUECO_S * 1000,
            "ventanas": ventanas,
        })
        wsl["rango"] = [wsl["totals"][2], wsl["totals"][3]]
        eda = construir_eda(wsl, gt)
        eda["_gt"] = gt

    hashes = {"EDA": sha256_de(Path(__file__)), "PIPELINE": sha256_de(RAIZ / "analysis" / "analizar_dataset.py")}
    for arch in sorted(Path(args.dataset).glob("sesion_*/registro_*.jsonl")):
        hashes[str(arch.relative_to(RAIZ)).replace("\\", "/")] = sha256_de(arch)
    for ruta in rutas_val:
        hashes[str(ruta.relative_to(RAIZ)).replace("\\", "/")] = sha256_de(ruta)

    figuras: list[str] = []
    if eda is not None and not args.no_figuras:
        try:
            figuras = hacer_figuras(eda, wsl, outdir)
        except Exception as e:  # las figuras no bloquean las metricas
            print(f"[aviso] figuras EDA fallaron: {e}", file=sys.stderr)
    if eda is not None:
        doc = {
            "eda_version": VERSION_EDA,
            "generado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "hash_inputs": hashes,
            **{k: v for k, v in eda.items() if not k.startswith("_")},
            "figuras": figuras,
        }
        (outdir / "eda.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        escribir_informe(eda, figuras, Path(args.informe))
        print(json.dumps({"salida": str(outdir), "figuras": figuras,
                          "rango_eval": eda["rango"]["n_eval"]}, ensure_ascii=False))
        return 0
    print(json.dumps({"salida": str(outdir), "figuras": [], "modo": "sin-wsl"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
