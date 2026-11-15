#!/usr/bin/env python3
"""F4 sesion 1 (F4S1): grabadora pasiva >=20 min + ground truth fotografico.

Reutilizado TAL CUAL (mecanismo probado en S1) por ``tools/sesion_autonoma.py``
para las sesiones S2/S3 autonomas: el runner lo invoca dentro de WSL con
``--sesion S2|S3`` y rutas /mnt/c de salida. Copia literal de
``iot2\\.tmp\\f4_sesion1_sampler.py`` (2026-11-15).

Corre DENTRO de WSL Debian con el venv del server. Solo LECTURAS HTTP a la API
del dashboard + INSERT acotado de filas validacion_sesiones (fuente honesta
'pasivo_foto_corroborada') y el export CSV. NO toca columnas vision_*/descripcion
ni servicios.

Modo ground truth (documentado en la cabecera del registro): SESION PASIVA CON
CORROBORACION FOTOGRAFICA — cada foto MQTT/HTTP real (origen='real') dentro de la
ventana es presencia confirmada de MOVIMIENTO en su ts_ms (el firmware captura 1
JPEG al ONSET, cooldown 15 s, AGENTS regla 14). Las marcas motion se derivan de
fotos; los huecos SIN foto quedan sin marcar (sin-dato): NO se etiquetan idle
porque eso seria una etiqueta inventada (regla 10).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DB = "/home/aulasense/aulasense-datos/aulasense.db"
CLAVES_ENV = "/home/aulasense/aulasense/claves/.env"

# Ventana de marca motion derivada de una foto: inicio -5 s, fin +20 s (el onset
# fotografico cubre el burst de movimiento; el cooldown de captura es 15 s).
GT_PRE_MS = 5_000
GT_POST_MS = 20_000
# Tolerancia de solape foto<->evento (mapeo R3 monotono->epoca): +-20 s.
SOLAPE_TOL_MS = 20_000


def ms_ahora() -> int:
    return int(time.time() * 1000)


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def leer_credenciales() -> tuple[str, str]:
    txt = Path(CLAVES_ENV).read_text(encoding="utf-8", errors="replace")
    user = (re.search(r"^AULASENSE_ADMIN_USER=(.*)$", txt, re.M) or [None, "admin"])[1].strip() or "admin"
    m = re.search(r"^AULASENSE_ADMIN_PASSWORD=(.*)$", txt, re.M)
    if not m:
        raise SystemExit("no hay AULASENSE_ADMIN_PASSWORD en claves/.env")
    return user, m.group(1).strip()


class Api:
    def __init__(self, base: str) -> None:
        self.base = base
        self.cookie: str | None = None

    def _req(self, ruta: str, payload: dict | None = None, raw: bool = False):
        data = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(self.base + ruta, data=data)
        if data is not None:
            r.add_header("Content-Type", "application/json")
        if self.cookie:
            r.add_header("Cookie", self.cookie)
        resp = urllib.request.urlopen(r, timeout=15)
        cuerpo = resp.read()
        sc = resp.headers.get("Set-Cookie")
        if sc:
            self.cookie = sc.split(";")[0]
        if raw:
            return resp, cuerpo
        return json.loads(cuerpo.decode("utf-8"))

    def login(self, user: str, pwd: str) -> None:
        self._req("/login", {"username": user, "password": pwd})

    def get(self, ruta: str):
        return self._req(ruta)

    def get_raw(self, ruta: str):
        return self._req(ruta, raw=True)[1]


class Registro:
    """JSONL de la sesion (parseable linea a linea, flush inmediato)."""

    def __init__(self, ruta: Path) -> None:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(ruta, "w", encoding="utf-8")

    def linea(self, obj: dict) -> None:
        obj.setdefault("escrito_en", iso(ms_ahora()))
        self.f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.f.flush()

    def cerrar(self) -> None:
        self.f.close()


def percentiles(vals: list[float]) -> dict:
    if not vals:
        return {}
    v = sorted(vals)
    def p(q: float) -> float:
        i = max(0, min(len(v) - 1, int(round(q * (len(v) - 1)))))
        return v[i]
    return {
        "n": len(v), "min": round(v[0], 4), "p25": round(p(.25), 4),
        "mediana": round(p(.5), 4), "p75": round(p(.75), 4),
        "p90": round(p(.9), 4), "p95": round(p(.95), 4), "max": round(v[-1], 4),
        "media": round(sum(v) / len(v), 4),
    }


def marcas_desde_fotos(fotos: list[dict], t0: int, t1: int) -> list[tuple[int, int, list[int]]]:
    """Intervalos motion [ini,fin] uniendo ventanas fotograficas, clamps a sesion."""
    tramos = sorted(
        (max(t0, f["ts_ms"] - GT_PRE_MS), min(t1, f["ts_ms"] + GT_POST_MS), f["id"])
        for f in fotos
    )
    unidos: list[list] = []
    for ini, fin, fid in tramos:
        if ini >= fin:
            continue
        if unidos and ini <= unidos[-1][1]:
            unidos[-1][1] = max(unidos[-1][1], fin)
            unidos[-1][2].append(fid)
        else:
            unidos.append([ini, fin, [fid]])
    return [(a, b, ids) for a, b, ids in unidos]


def insertar_marcas(marcas: list[tuple[int, int, list[int]]], sesion: str) -> int:
    """INSERT honesto en validacion_sesiones (solo filas NUEVAS de esta sesion)."""
    con = sqlite3.connect(DB, timeout=15.0)
    con.execute("PRAGMA busy_timeout=15000")
    n = 0
    try:
        with con:
            for ini, fin, ids in marcas:
                nota = (
                    "PASIVO: onset corroborado por foto(s) id=" + ",".join(map(str, ids[:12]))
                    + f" (total {len(ids)}); ventana = foto +/-("
                    + f"{GT_PRE_MS // 1000}s/{GT_POST_MS // 1000}s); sin marca idle (huecos = sin-dato)"
                )[:199]
                con.execute(
                    "INSERT INTO validacion_sesiones "
                    "(sesion, inicio_ms, fin_ms, verdad_terreno, fuente, notas) "
                    "VALUES (?, ?, ?, 'motion', 'pasivo_foto_corroborada', ?)",
                    (sesion, ini, fin, nota),
                )
                n += 1
    finally:
        con.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sesion", default="F4S1")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--sse-base", default="", help="base para sondear SSE (p.ej. :8100)")
    ap.add_argument("--duracion-s", type=int, default=1320)
    ap.add_argument("--intervalo-s", type=int, default=20)
    ap.add_argument("--salida", required=True, help="ruta del registro JSONL")
    ap.add_argument("--csv-gt", required=True, help="ruta destino del export validacion CSV")
    ap.add_argument("--dry-run", action="store_true", help="solo login + 1 poll")
    args = ap.parse_args()

    api = Api(args.base)
    user, pwd = leer_credenciales()
    api.login(user, pwd)
    print(f"[f4] login OK contra {args.base} (usuario {user!r} sin imprimir pass)", flush=True)

    # Sondeo SSE (si existe un backend SSE, se consume; si no, se degrada a poll
    # REST y se documenta honestamente en la cabecera).
    sse_activo = ""
    if args.sse_base:
        for ruta in ("/sse", "/api/sse", "/events", "/stream", "/api/stream"):
            try:
                r = urllib.request.Request(args.sse_base + ruta)
                if api.cookie:
                    r.add_header("Cookie", api.cookie)
                resp = urllib.request.urlopen(r, timeout=4)
                ct = resp.headers.get("content-type", "")
                if "event-stream" in ct:
                    sse_activo = args.sse_base + ruta
                    resp.close()
                    break
                resp.close()
            except Exception:
                continue
    print(f"[f4] SSE: {sse_activo or 'ningun endpoint encontrado -> muestreo por poll REST'}", flush=True)

    reg = Registro(Path(args.salida))
    t0 = ms_ahora()
    dur = 130 if args.dry_run else args.duracion_s
    t1 = t0 + dur * 1000
    reg.linea({
        "tipo": "cabecera",
        "sesion": args.sesion,
        "proyecto": "aulasense F4 sesion 1 de 3",
        "inicio_ms": t0, "inicio_iso": iso(t0),
        "duracion_objetivo_s": dur,
        "modo": "PASIVA CON CORROBORACION FOTOGRAFICA",
        "metodo_gt": (
            "cada foto real (canal mqtt/http, origen=real) dentro de la ventana es "
            "movimiento confirmado en su ts_ms (captura de evidencia al onset, cooldown 15 s). "
            "Marcas motion = union de ventanas foto +/-(" + f"{GT_PRE_MS//1000}s/{GT_POST_MS//1000}s). "
            "Los huecos sin foto quedan sin-dato (NO se etiquetan idle: no hay evidencia fisica que "
            "lo respalde en modo pasivo). Fuente de las marcas: 'pasivo_foto_corroborada'. "
            "Cero etiquetas inventadas; cero sintético."
        ),
        "muestreo": f"poll REST {args.intervalo_s}s a /api/evaluaciones|eventos|imagenes"
            + (f" + SSE {sse_activo}" if sse_activo else " (SSE no disponible en este entorno al inicio)"),
        "base_api": args.base,
    })

    evals_max_id = 0
    vistos_ev: set[int] = set()
    vistos_fo: set[int] = set()
    scores: list[float] = []
    n_motion = n_idle = 0
    eventos: list[dict] = []
    fotos: list[dict] = []

    def sondeo(fin_sondeo: int, tipo_linea: str = "muestreo") -> None:
        nonlocal evals_max_id, scores, n_motion, n_idle, eventos, fotos
        # evaluaciones incremental por id (LIMIT por defecto del clamp no hace falta:
        # pedimos limite y filtramos ids nuevos)
        ev = api.get(f"/api/evaluaciones?desde={t0}&hasta={fin_sondeo}&limite=5000")
        filas = [f for f in ev["filas"] if f["id"] > evals_max_id]
        if filas:
            evals_max_id = max(f["id"] for f in filas)
        for f in filas:
            scores.append(float(f["score"]))
            if f["state"] == "motion":
                n_motion += 1
            else:
                n_idle += 1
        # eventos: snapshot y dedupe por id, dentro de ventana
        evs = api.get("/api/eventos?limite=2000")
        for f in evs["filas"]:
            if f["id"] not in vistos_ev and t0 <= f["ts_ms"] <= fin_sondeo:
                vistos_ev.add(f["id"])
                eventos.append(f)
                reg.linea({"tipo": "evento", **f})
        # fotos: incremental
        ims = api.get(f"/api/imagenes?desde={t0}&hasta={fin_sondeo}&limite=2000")
        for f in ims["filas"]:
            if f["id"] not in vistos_fo:
                vistos_fo.add(f["id"])
                fotos.append(f)
                reg.linea({"tipo": "foto", **{k: f.get(k) for k in ("id", "device_id", "ts_ms", "canal", "evento_id", "origen", "tamano_bytes", "imagen_purgada")}})
        reg.linea({
            "tipo": tipo_linea, "t_ms": fin_sondeo, "t_iso": iso(fin_sondeo),
            "eval_nuevas": len(filas),
            "motion_acum": n_motion, "idle_acum": n_idle, "scores_n": len(scores),
            "eventos_ventana": len(eventos), "fotos_ventana": len(fotos),
        })

    fin = min(t1, t0 + dur * 1000)
    sondeo(ms_ahora())
    if args.dry_run:
        reg.cerrar()
        print("[f4] DRY-RUN ok", flush=True)
        return 0

    while ms_ahora() < t1:
        time.sleep(args.intervalo_s)
        sondeo(min(ms_ahora(), t1))
    t_end = ms_ahora()

    # GT pasivo: marcas motion desde fotos + export CSV via la propia API.
    marcas = marcas_desde_fotos(fotos, t0, t_end)
    n_insert = insertar_marcas(marcas, args.sesion)
    for ini, finm, ids in marcas:
        reg.linea({"tipo": "gt", "inicio_ms": ini, "fin_ms": finm, "fotos": ids,
                   "duracion_ms": finm - ini})

    csv_bytes = api.get_raw(f"/api/validacion/export?sesion={args.sesion}")
    Path(args.csv_gt).parent.mkdir(parents=True, exist_ok=True)
    Path(args.csv_gt).write_bytes(csv_bytes)

    # Metricas de solape foto<->evento onset.
    onsets = [e for e in eventos if e["tipo"] == "MOVIMIENTO_INICIO"]
    def onset_cubierto(e: dict) -> bool:
        return any(abs(f["ts_ms"] - e["ts_ms"]) <= SOLAPE_TOL_MS for f in fotos)
    onsets_con_foto = sum(1 for e in onsets if onset_cubierto(e))
    fotos_con_onset = sum(1 for f in fotos if any(abs(e["ts_ms"] - f["ts_ms"]) <= SOLAPE_TOL_MS for e in onsets))
    ventana_s = max(1, (t_end - t0) / 1000)
    resumen = {
        "tipo": "resumen",
        "sesion": args.sesion,
        "inicio_ms": t0, "fin_ms": t_end,
        "duracion_s": round(ventana_s, 1),
        "eval_total": len(scores),
        "motion": n_motion, "idle": n_idle,
        "pps_medio": round(len(scores) / ventana_s, 2),
        "score_distribution": percentiles(scores),
        "pct_motion": round(100.0 * n_motion / max(1, len(scores)), 2),
        "eventos": len(eventos),
        "eventos_por_tipo": {t: sum(1 for e in eventos if e["tipo"] == t) for t in set(e["tipo"] for e in eventos)},
        "onsets": len(onsets),
        "onsets_con_foto_20s": onsets_con_foto,
        "fotos_en_ventana": len(fotos),
        "fotos_con_onset_20s": fotos_con_onset,
        "fotos_canal_mqtt": sum(1 for f in fotos if f.get("canal") == "mqtt"),
        "fotos_canal_http": sum(1 for f in fotos if f.get("canal") == "http"),
        "marcas_gt_motion": n_insert,
        "gt_ms_cubiertos": sum(f - i for i, f, _ in marcas),
        "csv_gt": args.csv_gt,
        "modo_gt": "pasivo_foto_corroborada (huecos = sin-dato, NO idle inventado)",
    }
    reg.linea(resumen)
    reg.cerrar()
    print(json.dumps(resumen, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
