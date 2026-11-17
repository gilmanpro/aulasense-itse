#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F26-h3 — Resumen y figuras de las decisiones Clef (fuente 6 del análisis).

Complementa eda.py/modelo_ocupacion.py (que cubren CSI/telemetría/fotos GT) con la
capa de DECISIÓN DE AULA que el worker Clef (SPEC §16.5) escribió en imagenes:
distribución de taxonomía, confianza, cobertura, calidad perceptual (hallazgo
central F26: decodificable ≠ legible — la media de decision_calidad de las
IMAGEN_DEGRADADA reales es ~0.07 vs ~0.54 de las SIN_PERSONAS), tasa
REVISION_HUMANA (cubre<0.7), modelos usados y coste real auditado (clef_uso +
vision_uso).

Políticas honestas (AGENTS reglas 10/11):
  - SOLO origen='real'; las 2 fotos sintéticas (ids 1-2, decisionadas por el CLI
    al no filtrar origen) quedan EXCLUIDAS del análisis y se reportan aparte.
  - Costes MEDIDOS de clef_uso/vision_uso con su matrícula de subestimación
    (la pata flash de las escaladas no se audita: ~+5-10 %).
  - Sin métricas de precisión de decisión: no hay GT de etiquetas de escena.

Convenciones heredadas del pipeline (analizar_dataset.py): puente WSL
solo-lectura (script por stdin, payload por argv, sqlite URI mode=ro, venv->
python3). NO escribe en BD ni en server/; salida a docs/analisis/.

Salidas:
  - docs/analisis/clef_resumen.json        (machine-readable, determinista:
                                            hashes + único generado_utc)
  - docs/analisis/graficos/clef_distribucion_decisiones.png
  - docs/analisis/graficos/clef_revision_calidad.png

Uso:  C:\\Python315\\python.exe analysis/clef_resumen.py [--no-figuras] [--db ruta]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import analizar_dataset as ad  # noqa: E402  (constantes del puente WSL)

VERSION_CLEF = "f26-h3-1.0"
UMBRAL_REVISION = 0.70        # decision_cubre < esto -> REVISION_HUMANA (SPEC §16.5)
TAXONOMIA = ["IMAGEN_DEGRADADA", "SIN_PERSONAS_VISIBLES", "CIRCULACION",
             "PERSONAS_SENTADAS", "PRESENTACION_AL_FRENTE", "AGLOMERACION_OTRA"]

# ---------------------------------------------------------------- consulta WSL (ro)
CONSULTA_CLEF_PY = r'''# -*- coding: utf-8 -*-
# Sonda SOLO-LECTURA (mode=ro) para el resumen Clef. NO escribe nada.
import json, sqlite3, sys

payload = json.loads(sys.argv[1])
con = sqlite3.connect("file:" + payload["db"] + "?mode=ro", uri=True)
q = lambda s, p=(): con.execute(s, p).fetchall()
res = {"wsl_ok": True}

# taxonomia de las decisiones REALES 'hecho' (sinteticas ids 1-2 excluidas por WHERE)
res["dist"] = q("SELECT decision_etiqueta, COUNT(*), AVG(decision_confianza), "
                "AVG(decision_cubre), AVG(decision_calidad) "
                "FROM imagenes WHERE origen='real' AND decision_estado='hecho' "
                "GROUP BY decision_etiqueta")
res["puntos"] = q("SELECT decision_etiqueta, decision_confianza, decision_cubre, "
                  "decision_calidad FROM imagenes WHERE origen='real' AND "
                  "decision_estado='hecho' ORDER BY id")
res["revision_n"] = q("SELECT COUNT(*) FROM imagenes WHERE origen='real' AND "
                      "decision_estado='hecho' AND decision_cubre < ?",
                      (payload["umbral_revision"],))[0][0]
res["revision_ids"] = [r[0] for r in q("SELECT id FROM imagenes WHERE origen='real' AND "
                                       "decision_estado='hecho' AND decision_cubre < ? "
                                       "ORDER BY id", (payload["umbral_revision"],))]
res["conf_global"] = q("SELECT COUNT(*), AVG(decision_confianza) FROM imagenes "
                       "WHERE origen='real' AND decision_estado='hecho'")[0]
res["modelos"] = dict(q("SELECT decision_modelo, COUNT(*) FROM imagenes WHERE "
                        "origen='real' AND decision_estado='hecho' GROUP BY decision_modelo"))
res["actividad"] = dict(q("SELECT decision_actividad, COUNT(*) FROM imagenes WHERE "
                          "origen='real' AND decision_estado='hecho' GROUP BY decision_actividad"))
res["decision_estado"] = dict(q("SELECT decision_estado, COUNT(*) FROM imagenes WHERE "
                                "origen='real' GROUP BY decision_estado"))
res["vision_estado"] = dict(q("SELECT vision_estado, COUNT(*) FROM imagenes WHERE "
                              "origen='real' GROUP BY vision_estado"))
res["fotos_reales_total"] = q("SELECT COUNT(*) FROM imagenes WHERE origen='real'")[0][0]
res["sinteticas"] = q("SELECT id, decision_etiqueta FROM imagenes WHERE "
                      "origen='sintetico' AND decision_estado='hecho' ORDER BY id")
# coste auditado (regla 16): clef_uso + vision_uso (solo numeros)
res["clef_uso"] = q("SELECT COUNT(*), COALESCE(SUM(tokens),0), COALESCE(SUM(coste_usd),0), "
                    "COALESCE(SUM(exito=0),0) FROM clef_uso")[0]
res["clef_uso_tope"] = q("SELECT COUNT(*) FROM clef_uso WHERE detalle='tope'")[0][0]
res["vision_uso_dia"] = q("SELECT COUNT(*), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0) "
                          "FROM vision_uso WHERE date(ts_ms/1000,'unixepoch')="
                          "date((SELECT MAX(ts_ms) FROM vision_uso)/1000,'unixepoch')")[0]
res["vision_uso_total"] = q("SELECT COUNT(*), COALESCE(SUM(tokens_in),0), "
                            "COALESCE(SUM(tokens_out),0) FROM vision_uso")[0]
print(json.dumps(res, ensure_ascii=False))
'''


def consultar_wsl_clef(payload: dict) -> dict:
    errs = []
    for py in (ad.PY_VENV_WSL, ad.PY_SYS_WSL):
        r = subprocess.run(["wsl", "-d", ad.DISTRO_WSL, "-u", "root", "--", py, "-",
                            json.dumps(payload, ensure_ascii=False)],
                           input=CONSULTA_CLEF_PY, capture_output=True, text=True,
                           encoding="utf-8", timeout=300)
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout)
        errs.append(f"{py}: {(r.stderr or r.stdout).strip()[:200]}")
    raise RuntimeError("consulta WSL clef fallo en ambos interpretes:\n" + "\n".join(errs))


def r4(x):
    return None if x is None else round(float(x), 4)


def construir_resumen(wsl: dict) -> dict:
    n_hechos = int(wsl["conf_global"][0])
    dist = {et: {"n": int(n),
                 "conf_media": r4(c), "cubre_media": r4(cu), "calidad_media": r4(ca),
                 "pct": r4(100.0 * int(n) / n_hechos) if n_hechos else None}
            for et, n, c, cu, ca in wsl["dist"]}
    for et in TAXONOMIA:
        dist.setdefault(et, {"n": 0, "conf_media": None, "cubre_media": None,
                             "calidad_media": None, "pct": 0.0})
    deg = dist.get("IMAGEN_DEGRADADA", {})
    sinp = dist.get("SIN_PERSONAS_VISIBLES", {})
    return {
        "version": VERSION_CLEF,
        "alcance": ("imagenes.decision_* con origen='real' y decision_estado='hecho' "
                    "(las 2 sinteticas ids 1-2 EXCLUIDAS por regla 10 — decisionadas "
                    "en su dia por el CLI, que no filtra origen; sin edicion de BD)"),
        "n_decisiones_reales": n_hechos,
        "distribucion_taxonomia": dist,
        "hallazgo_degradacion": {
            "tasa_degradada_pct": deg.get("pct"),
            "calidad_media_degradada": deg.get("calidad_media"),
            "calidad_media_sin_personas": sinp.get("calidad_media"),
            "lectura_honesta": ("decodificable != legible: la mayoria de las reales las "
                                "ve IMAGEN_DEGRADADA el propio Clef (tambien en las nuevas "
                                "QC-PIL-OK); la calidad perceptual media (~0.07) separa "
                                "claramente de las legibles (~0.54). Cuello: encuadre QVGA "
                                "q12 del firmware. Recomendacion post-entrega: subir calidad/"
                                "resolucion JPEG por OTA (no cambia el sensing CSI)."),
        },
        "revision_humana": {"umbral_cubre": UMBRAL_REVISION,
                            "n": int(wsl["revision_n"]),
                            "pct": r4(100.0 * int(wsl["revision_n"]) / n_hechos) if n_hechos else None,
                            "ids": wsl["revision_ids"][:80]},
        "confianza_media_global": r4(wsl["conf_global"][1]),
        "modelos": wsl["modelos"],
        "actividad": wsl["actividad"],
        "estado_fotos_reales": {
            "total": int(wsl["fotos_reales_total"]),
            "decision_estado": wsl["decision_estado"],
            "vision_estado": wsl["vision_estado"],
            "nota": ("las 20 vision='error' (JPEG irrecoverable anteriores al FixF) NO entran "
                     "a la cola Clef por diseno del CLI: decision='pendiente' = no-expuesta, "
                     "documentada como degradadas QC (no editables, regla 10); id 56 = "
                     "FIRE-TEST 'omitido'"),
        },
        "sinteticas_excluidas": [{"id": i, "decision": d} for i, d in wsl["sinteticas"]],
        "costes_medidos": {
            "clef_uso_filas_api": int(wsl["clef_uso"][0]) - int(wsl["clef_uso_tope"]),
            "clef_uso_filas_tope": int(wsl["clef_uso_tope"]),
            "clef_tokens": int(wsl["clef_uso"][1]),
            "clef_coste_usd": r4(wsl["clef_uso"][2]),
            "clef_fillas_exito0": int(wsl["clef_uso"][3] or 0),
            "vision_uso_dia": {"llamadas": int(wsl["vision_uso_dia"][0]),
                                "tokens_in": int(wsl["vision_uso_dia"][1]),
                                "tokens_out": int(wsl["vision_uso_dia"][2]),
                                "tokens_total": int(wsl["vision_uso_dia"][1] + wsl["vision_uso_dia"][2])},
            "vision_uso_total": {"llamadas": int(wsl["vision_uso_total"][0]),
                                  "tokens_in": int(wsl["vision_uso_total"][1]),
                                  "tokens_out": int(wsl["vision_uso_total"][2])},
            "nota": ("clef_uso audita solo el paso final de cada fila: la pata flash de las "
                     "escaladas (~24+4 filas x ~700 tok) NO se suma => subestimacion ~5-10 % "
                     "(coste real ~= 1.05-1.10x el registrado). Vision factura en Credits del "
                     "Token Plan (sin USD por llamada). Cero promesas de coste sin medir "
                     "(regla 16/E10)."),
        },
        "limitaciones": ("no existe GT de etiquetas de escena: se reporta distribucion y "
                         "confianza, NO precision de la decision. Clef decide sobre la FOTO "
                         "(evidencia de evento), no sobre CSI: no reemplaza al detector."),
    }


# ---------------------------------------------------------------- figuras
FIGS = ["clef_distribucion_decisiones.png", "clef_revision_calidad.png"]


def _save(fig, path: Path):
    import matplotlib.pyplot as plt
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def hacer_figuras(res: dict, wsl: dict, outdir: Path) -> list[str]:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    gdir = outdir / "graficos"
    gdir.mkdir(parents=True, exist_ok=True)
    rutas: list[str] = []
    dist = res["distribucion_taxonomia"]
    n_total = res["n_decisiones_reales"]

    # 1) barras de taxonomia con conf/calidad anotadas (hallazgo 75 %)
    fig, ax = plt.subplots(figsize=(10, 5.2))
    etiquetas = [e for e in TAXONOMIA]
    ns = [dist[e]["n"] for e in etiquetas]
    colors = ["#d62728" if e == "IMAGEN_DEGRADADA" else "#1f77b4" for e in etiquetas]
    barras = ax.bar(range(len(etiquetas)), ns, color=colors)
    for i, e in enumerate(etiquetas):
        d = dist[e]
        if d["n"]:
            ax.text(i, d["n"] + 0.7,
                    f"n={d['n']} ({d['pct']} %)\nconf {d['conf_media']}\ncalidad {d['calidad_media']}",
                    ha="center", fontsize=8)
    ax.set_xticks(range(len(etiquetas)))
    ax.set_xticklabels([e.replace("_", "\n") for e in etiquetas], fontsize=8)
    ax.set_ylabel("imagenes reales decisionadas")
    ax.set_title(f"Distribución de decisiones Clef — {n_total} fotos reales "
                 f"(conf media {res['confianza_media_global']}; "
                 f"REVISION_HUMANA {res['revision_humana']['n']}/{n_total})")
    ax.set_ylim(0, max(ns) * 1.28 + 2)
    fig.tight_layout()
    rutas.append(str(_save(fig, gdir / FIGS[0]).relative_to(RAIZ)).replace("\\", "/"))

    # 2) scatter confianza x cobertura con linea de revision + calidad codificada
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.8))
    colores = {"IMAGEN_DEGRADADA": "#d62728", "SIN_PERSONAS_VISIBLES": "#1f77b4",
               "CIRCULACION": "#2ca02c", "PERSONAS_SENTADAS": "#9467bd",
               "PRESENTACION_AL_FRENTE": "#ff7f0e", "AGLOMERACION_OTRA": "#8c564b"}
    for et, c, cu, cal in wsl["puntos"]:
        a1.scatter(c, cu, s=26, color=colores.get(et, "gray"), alpha=0.75)
    # REVISION_HUMANA = decision_cubre < umbral (eje Y); franja inferior sombreada
    a1.axhline(UMBRAL_REVISION, color="gray", ls="--", lw=1,
               label=f"línea REVISION_HUMANA (cubre < {UMBRAL_REVISION})")
    a1.axhspan(0, UMBRAL_REVISION, alpha=0.07, color="red")
    a1.set_xlabel("confianza (noul_score)")
    a1.set_ylabel("cobertura de escena (cubre)")
    a1.set_title(f"Confianza × cobertura ({n_total} reales) — "
                 f"{res['revision_humana']['n']} a revisión")
    a1.legend(fontsize=8)
    for et, c, cu, cal in wsl["puntos"]:
        a2.scatter(c, cal if cal is not None else 0.0, s=26,
                   color=colores.get(et, "gray"), alpha=0.75)
    a2.axhline(0.25, color="gray", ls=":", lw=1)
    a2.set_xlabel("confianza (noul_score)")
    a2.set_ylabel("calidad perceptual (0–1)")
    a2.set_title("Calidad: DEGRADADA se agrupa bajo ~0.25 (QVGA q12 = cuello)")
    import matplotlib.patches as mp
    a2.legend(handles=[mp.Patch(color=colores[e], label=e) for e in colores],
              fontsize=7, loc="upper right")
    fig.tight_layout()
    rutas.append(str(_save(fig, gdir / FIGS[1]).relative_to(RAIZ)).replace("\\", "/"))
    return rutas


def sha256_de(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="F26-h3 resumen/figuras Clef (lea el docstring)")
    ap.add_argument("--salida", default=str(RAIZ / "docs" / "analisis"))
    ap.add_argument("--no-figuras", action="store_true")
    ap.add_argument("--db", default=ad.DB_WSL,
                    help="ruta BD dentro de WSL (default canonica)")
    args = ap.parse_args(argv)

    outdir = Path(args.salida)
    outdir.mkdir(parents=True, exist_ok=True)
    wsl = consultar_wsl_clef({"db": args.db, "umbral_revision": UMBRAL_REVISION})
    res = construir_resumen(wsl)
    figuras: list[str] = []
    if not args.no_figuras:
        try:
            figuras = hacer_figuras(res, wsl, outdir)
        except Exception as e:
            print(f"[aviso] figuras clef fallaron: {e}", file=sys.stderr)
    doc = {
        "generado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hash_inputs": {"CLEF_RESUMEN": sha256_de(Path(__file__)),
                        "PIPELINE": sha256_de(RAIZ / "analysis" / "analizar_dataset.py")},
        **res,
        "figuras": figuras,
    }
    (outdir / "clef_resumen.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"salida": str(outdir), "figuras": figuras,
                      "n_decisiones": res["n_decisiones_reales"],
                      "degradada_pct": res["hallazgo_degradacion"]["tasa_degradada_pct"],
                      "revision": res["revision_humana"]["n"],
                      "coste_clef_usd": res["costes_medidos"]["clef_coste_usd"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
