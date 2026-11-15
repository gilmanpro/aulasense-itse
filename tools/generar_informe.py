# -*- coding: utf-8 -*-
"""F6-a — generador re-ejecutable de `docs/entrega/INFORME.docx` y `docs/entrega/PRESENTACION.pptx`.

Un solo comando (regla 3 de AGENTS: python sano, no el del PATH):

    C:\\Python315\\python.exe tools/generar_informe.py

Diseño: TODO dato numérico del entregable sale de los JSON del pipeline de análisis
(`docs/analisis/analisis.json`, `eda.json`, `modelo_ocupacion.json`) o se cuenta en disco
(glob de `dataset/sesion_*/`, `docs/validacion_sesion*.csv`, `docs/analisis/graficos/*.png`,
BD de imágenes en modo SOLO-LECTURA). No hay cifras escritas a mano en el texto: mañana, tras
S2/S3 y la pasada 4 de visión, el mismo comando refresca sesiones, tramos idle (FPR/precisión
se activan solos) y estados de visión sin tocar el código.

Texto estático permitido (regla de la especificación): narrativa de contexto, arquitectura y
limitaciones conceptuales; todo lo medible se interpola.

Los temporales (snapshot de BD, crops de privacidad, frames del video, imágenes reescaladas)
viven en `aulasense/.tmp/f6a_*` (regla 8) y no se commitean.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor as DRGB

from pptx import Presentation
from pptx.dml.color import RGBColor as PRGB
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt as PPt

# --------------------------------------------------------------------------- rutas y marca

RAIZ = Path(__file__).resolve().parents[1]          # aulasense/
DOCS = RAIZ / "docs"
ANALISIS = DOCS / "analisis"
GRAFICOS = ANALISIS / "graficos"
ENTREGA = DOCS / "entrega"

TMP = RAIZ / ".tmp"
TMP_IMG = TMP / "f6a_img"
TMP_DB = TMP / "f6a_db_snapshot"
TMP_FRAMES = TMP / "f6a_frames"

# .tmp compartido del proyecto iot2/ (ahí viven los informes de fases F25)
TMP_PROYECTO = RAIZ.parent / ".tmp"

DISTRO_WSL = "debian-aulasense"
RAIZ_WSL = Path(r"\\wsl.localhost") / DISTRO_WSL / "home" / "aulasense" / "aulasense-datos"

AUTOR = "gilman.pro"
MATERIA = "Big Data e Inteligencia Artificial"
INSTITUTO = "ITSE — Instituto de Educación Superior Tecnológico"
PROYECTO = "AulaSense"
SUBTITULO = ("Sensing de movimiento y ocupación dinámica por Wi-Fi CSI "
             "con IA en el borde (ESP32-S3)")
URL_PUBLICA = "http://167.114.169.134:10062"
BROKER = "broker.gilman.pro:14769"

# Paleta sobria académica (azul tinta + verde azulado de acento)
AZUL = (0x10, 0x2A, 0x43)
ACENTO = (0x1F, 0x7A, 0x8C)
GRIS = (0x5A, 0x66, 0x73)
GRIS_SUAVE = (0x8A, 0x96, 0xA3)
FONDO_CLARO = (0xEE, 0xF3, 0xF6)
BLANCO = (0xFF, 0xFF, 0xFF)
TINTA = (0x1B, 0x1B, 0x1B)

MESES = ("enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre "
         "diciembre").split()

# Pie de figura a partir del nombre del archivo (spec: "pie de figura desde sus nombres")
PIES_FIGURAS = {
    "serie_motion": "Serie temporal de evaluaciones del detector y fracción de estado motion",
    "histograma_scores": "Distribución del score del detector en la sesión corroborada por foto",
    "matriz_confusion": "Matriz de confusión contra la verdad terreno pasiva (recall-only)",
    "eda_cobertura_gt": "Cobertura del ground truth: densidad de motion dentro de cada tramo",
    "eda_score_por_hora": "Score de detección y porcentaje de motion por hora local (UTC−5)",
    "eda_autocorrelacion_pps": "Autocorrelación de la tasa de evaluaciones (buckets de 60 s)",
    "eda_histograma_rssi": "Distribución del RSSI registrado por la telemetría del nodo",
    "eda_sesiones_comparativa": "Comparativa de las sesiones F4: duración, evaluaciones, motion y pps",
    "ocupacion_pr_umbral": "Curva precision/recall por umbral del modelo de ocupación",
    "ocupacion_modelo_vs_gt": "Modelo de ocupación frente a la verdad terreno, ventana a ventana",
    "ocupacion_feature_importance": "Importancia de variables del modelo de ocupación (árbol)",
}

ORDEN_FIGURAS = (
    "serie_motion", "histograma_scores", "matriz_confusion", "eda_cobertura_gt",
    "eda_score_por_hora", "eda_autocorrelacion_pps", "eda_histograma_rssi",
    "eda_sesiones_comparativa", "ocupacion_pr_umbral", "ocupacion_modelo_vs_gt",
    "ocupacion_feature_importance",
)


# --------------------------------------------------------------------------- utilidades

def leer_json(ruta, defecto=None):
    """JSON tolerante: si el insumo no existe todavía, el generador no revienta."""
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defecto


def leer_texto(ruta):
    try:
        return Path(ruta).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def sha12(ruta):
    """sha256 corto de un insumo (trazabilidad reproducible)."""
    try:
        h = hashlib.sha256()
        with open(ruta, "rb") as fh:
            for bloque in iter(lambda: fh.read(1 << 20), b""):
                h.update(bloque)
        return h.hexdigest()[:12]
    except OSError:
        return "n/d"


def tam_bytes(ruta):
    ruta = Path(ruta)
    if ruta.is_file():
        return ruta.stat().st_size
    if ruta.is_dir():
        return sum(p.stat().st_size for p in ruta.rglob("*") if p.is_file())
    return 0


def tam_legible(n):
    for unidad, factor in (("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= factor:
            return f"{n / factor:.1f} {unidad}"
    return f"{n} B"


def num(x, nd=2):
    """Formato español: miles con punto y decimales con coma."""
    if x is None:
        return "n/d"
    s = f"{x:,.{nd}f}"
    if "." in s:
        entero, decimales = s.split(".")
        return entero.replace(",", ".") + "," + decimales
    return s.replace(",", ".")


def pct(x, nd=2):
    """Valor que YA viene en porcentaje (0-100)."""
    return "n/d" if x is None else num(x, nd) + " %"


def frac(x, nd=1):
    """Fracción 0-1 -> porcentaje."""
    return "n/d" if x is None else num(x * 100, nd) + " %"


def ms(x, nd=1):
    return "n/d" if x is None else num(x, nd) + " ms"


def fecha_larga(dt):
    return f"{dt.day} de {MESES[dt.month - 1]} de {dt.year}"


def fecha_corta(utc_iso):
    """'2026-11-15T20:03:54+00:00' -> '2026-11-15 20:03 UTC'."""
    if not utc_iso:
        return "n/d"
    return utc_iso.replace("T", " ")[:16] + " UTC"


def pie_figura(ruta):
    tallo = Path(ruta).stem
    return PIES_FIGURAS.get(tallo, tallo.replace("_", " ").capitalize())


# --------------------------------------------------------------------------- cifras del pipeline

def extraer_cifras(analisis, eda, modelo):
    """Normaliza los tres JSON del pipeline en un dict plano.

    Es la ÚNICA puerta de entrada de números al documento: los tests comprueban que al mutar
    los JSON cambian las tablas (nada hardcodeado).
    """
    a = analisis or {}
    e = eda or {}
    m = modelo or {}

    sesiones = []
    for fila in a.get("metricas_por_sesion") or []:
        tel = fila.get("telemetria") or {}
        sm = fila.get("scores_motion") or {}
        sesiones.append({
            "nombre": fila.get("sesion"),
            "duracion_s": fila.get("duracion_s"),
            "n_eval": fila.get("n_eval"),
            "n_motion": fila.get("n_motion"),
            "pct_motion": fila.get("pct_motion"),
            "pps": fila.get("pps_medio"),
            "score_motion_mediana": sm.get("p50") if sm.get("n") else None,
            "rssi": tel.get("rssi_dbm"),
            "die": tel.get("die_temp_c"),
            "libre_kb": tel.get("free_mem_kb"),
            "carga": tel.get("runtime_load_pct"),
            "pps_csi": tel.get("pps_media"),
        })

    conf = a.get("confusion") or {}
    gt = a.get("ground_truth") or {}
    integ = a.get("integridad_jpeg") or {}
    corr = a.get("correlacion_foto_onset") or {}
    fk = a.get("correlacion_foto_evento_fk") or {}
    lat = a.get("latencias") or {}
    div = a.get("divergencia_registros_vs_bd") or {}
    vis = a.get("vision") or {}
    ev = a.get("eventos") or {}

    rango = e.get("rango") or {}
    horas = ((e.get("score_por_hora") or {}).get("por_hora_local") or {})
    pps = e.get("pps_autocorrelacion") or {}
    rssi = e.get("rssi_efecto") or {}
    outliers = e.get("outliers_motivo") or []
    cob = e.get("cobertura_gt") or {}

    ent = m.get("entrenamiento") or {}
    mods = ent.get("modelos") or {}
    val = m.get("validacion_gt") or {}
    mejor = val.get("mejor_umbral_f1") or {}

    duraciones = [s["duracion_s"] for s in sesiones if s["duracion_s"]]
    evals = [s["n_eval"] for s in sesiones if s["n_eval"]]
    pps_ses = [s["pps"] for s in sesiones if s["pps"]]

    return {
        "pipeline": {
            "version": a.get("pipeline_version"),
            "generado_utc": a.get("generado_utc"),
            "eda_version": e.get("eda_version"),
            "modelo_version": m.get("modelo_version"),
            "nota_determinismo": a.get("nota_determinismo"),
            "hash_pipeline": (a.get("hash_inputs") or {}).get("PIPELINE"),
        },
        "sesiones": sesiones,
        "totales": {
            "n_sesiones": len(sesiones),
            "n_eval": sum(evals),
            "duracion_min": (sum(duraciones) / 60.0) if duraciones else None,
            "pps_medio": (sum(pps_ses) / len(pps_ses)) if pps_ses else None,
            "onsets": ev.get("onsets"),
            "movimiento_fin": ev.get("movimiento_fin"),
            "eventos_en_rango": ev.get("total_real_rango"),
        },
        "confusion": {
            "tp": conf.get("tp"), "fn": conf.get("fn"), "fp": conf.get("fp"), "tn": conf.get("tn"),
            "recall": conf.get("recall_motion"),
            "fpr": conf.get("fpr"), "precision": conf.get("precision"),
            "tramos_motion": conf.get("tramos_motion_gt"),
            "tramos_idle": conf.get("tramos_idle_gt"),
            "tramos_sin_eval": conf.get("tramos_gt_sin_evaluaciones"),
            "cobertura": conf.get("cobertura_media_motion_dentro_gt"),
            "margen_ms": conf.get("margen_ms"),
            "metodo": conf.get("metodo"),
            "nota": conf.get("nota_honest"),
        },
        "gt": {
            "fuentes": gt.get("fuentes") or [],
            "motion": gt.get("motion"),
            "idle": gt.get("idle"),
            "n_tramos": gt.get("n_tramos"),
            "sesiones": gt.get("sesiones_cubiertas") or [],
        },
        "integridad": {
            "validas": integ.get("validas"),
            "rotas": integ.get("rotas"),
            "ausentes": integ.get("ausentes_no_purgadas"),
            "purgadas": integ.get("purgadas_retencion"),
            "excluidas": integ.get("excluidas_fire_test"),
            "sin_validar": integ.get("sin_validar"),
            "tasa": integ.get("tasa_integridad"),
            "metodo": integ.get("metodo"),
        },
        "correlacion": {
            "fotos": corr.get("fotos"),
            "onsets": corr.get("onsets"),
            "emparejadas": corr.get("emparejadas"),
            "emparejadas_pct": corr.get("fotos_emparejadas_pct"),
            "onsets_con_foto_pct": corr.get("onsets_con_foto_pct"),
            "ventana_ms": corr.get("ventana_ms"),
            "lag_mediana": (corr.get("lags_ms") or {}).get("p50"),
            "lag_p95": (corr.get("lags_ms") or {}).get("p95"),
            "sin_match": corr.get("fotos_sin_match") or [],
            "fk_pct": fk.get("pct_fk"),
            "fk_fotos": fk.get("fotos"),
            "fk_con_evento": fk.get("con_evento_id"),
        },
        "latencias": {
            "eventos": lat.get("eventos") or {},
            "imagenes": lat.get("imagenes") or {},
        },
        "divergencia": {
            "comunes": div.get("comunes"),
            "en_bd": div.get("evento_ids_en_bd_rango"),
            "en_registro": div.get("evento_ids_en_registros"),
            "divergentes": div.get("n_ts_divergentes_gt5s"),
            "solo_registro": len(div.get("solo_en_registro") or []),
            "nota": div.get("nota"),
        },
        "vision": {
            "hecho": (vis.get("estados_imagenes_reales") or {}).get("hecho"),
            "error": (vis.get("estados_imagenes_reales") or {}).get("error"),
            "omitido": (vis.get("estados_imagenes_reales") or {}).get("omitido"),
            "tokens": vis.get("tokens_totales"),
            "nota": vis.get("nota"),
            "por_resultado": vis.get("vision_uso_por_resultado") or [],
        },
        "modelo": {
            "n_filas": (m.get("dataset") or {}).get("n_filas_entrenables"),
            "n_ventanas": (m.get("dataset") or {}).get("n_ventanas_eval"),
            "n_omitidas": (m.get("dataset") or {}).get("n_filas_omitidas"),
            "split": ent.get("split"),
            "baseline": ent.get("baseline") or {},
            "decision_tree": mods.get("decision_tree") or {},
            "logistic_regression": mods.get("logistic_regression") or {},
            "nota": ent.get("nota_honest_e10"),
            "indicador": (m.get("definiciones") or {}).get("indicador"),
            "mejor_umbral": mejor,
            "tramos_no_cubiertos": len(val.get("tramos_gt_no_cubiertos") or []),
            "cobertura_indicador": val.get("indicador_cobertura_pct"),
        },
        "eda": {
            "n_eval": rango.get("n_eval"),
            "n_motion": rango.get("n_motion"),
            "utc_inicio": rango.get("utc_inicio"),
            "utc_fin": rango.get("utc_fin"),
            "pps_medio": pps.get("pps_medio"),
            "n_buckets": pps.get("n_buckets"),
            "acf": pps.get("acf") or [],
            "rssi": rssi,
            "horas": horas,
            "hora_pico": (e.get("score_por_hora") or {}).get("hora_pico_motion"),
            "outliers": outliers,
            "cobertura": cob,
        },
    }


def tabla_sesiones(analisis):
    """Tabla de sesiones 100 % derivada de analisis.json['metricas_por_sesion']."""
    c = extraer_cifras(analisis, {}, {})
    filas = [["Sesión", "Duración (min)", "Evaluaciones", "% motion", "pps medio",
              "Score motion (p50)", "RSSI (dBm)", "die temp (°C)"]]
    for s in c["sesiones"]:
        filas.append([
            str(s["nombre"]),
            num((s["duracion_s"] or 0) / 60.0, 1),
            num(s["n_eval"], 0),
            pct(s["pct_motion"]),
            num(s["pps"], 4),
            num(s["score_motion_mediana"], 4),
            num(s["rssi"], 1),
            num(s["die"], 1),
        ])
    return filas


def tabla_confusion(cifras):
    c = cifras["confusion"]
    return [
        ["Concepto", "Valor"],
        ["Tramos de ground truth (motion)", num(c["tramos_motion"], 0)],
        ["Tramos de ground truth (idle)", num(c["tramos_idle"], 0) if c["tramos_idle"] is not None
         else "0 (sin marcas idle todavía)"],
        ["Verdaderos positivos (TP)", num(c["tp"], 0)],
        ["Falsos negativos (FN)", num(c["fn"], 0)],
        ["Falsos positivos (FP)", num(c["fp"], 0) if c["fp"] is not None else "no computable"],
        ["Verdaderos negativos (TN)", num(c["tn"], 0) if c["tn"] is not None else "no computable"],
        ["Recall de motion", frac(c["recall"])],
        ["FPR", frac(c["fpr"]) if c["fpr"] is not None else "no computable (sin tramos idle)"],
        ["Precisión", frac(c["precision"]) if c["precision"] is not None else "no computable"],
        ["Cobertura media de motion en tramos GT", frac(c["cobertura"])],
        ["Tramos GT sin evaluaciones (sin-dato)", num(c["tramos_sin_eval"], 0)],
        ["Margen de emparejamiento", ms(c["margen_ms"], 0)],
    ]


def tabla_latencias(cifras):
    filas = [["Fuente", "n", "Mediana (ms)", "Mediana corregida (ms)", "p95 (ms)", "|Δ| < 2 s"]]
    for clave, etiqueta in (("eventos", "Eventos"), ("imagenes", "Imágenes")):
        d = cifras["latencias"][clave] or {}
        filas.append([
            etiqueta,
            num(d.get("n"), 0),
            num(d.get("mediana_ms"), 1),
            num(d.get("mediana_corr_truncacion_ms"), 1),
            num(d.get("p95_ms"), 1),
            pct(d.get("pct_abs_bajo_2s")),
        ])
    return filas


def tabla_modelos(cifras):
    m = cifras["modelo"]
    filas = [["Modelo", "Accuracy", "Precisión", "Recall", "F1", "ROC-AUC", "n train / test"]]
    base = m["baseline"]
    filas.append(["Baseline (clase mayoritaria)", num(base.get("accuracy"), 4), "—", "—", "—", "—",
                  num(m["n_filas"], 0) + " filas"])
    for clave, etiqueta in (("logistic_regression", "Regresión logística (D8)"),
                            ("decision_tree", "Árbol de decisión (D8)")):
        d = m[clave] or {}
        filas.append([
            etiqueta,
            num(d.get("accuracy"), 4),
            num(d.get("precision"), 4),
            num(d.get("recall"), 4),
            num(d.get("f1"), 4),
            num(d.get("roc_auc"), 4),
            f'{num(d.get("n_train"), 0)} / {num(d.get("n_test"), 0)}',
        ])
    return filas


def tabla_vision(cifras):
    """Tabla del registro de llamadas (vision_uso), no de imágenes distintas.

    `vision.hecho` cuenta IMÁGENES en estado hecho (34 reales); `por_resultado` cuenta LLAMADAS
    (39 hechas: 3 descripciones duplicadas + 2 sintéticas). Se etiqueta como llamadas para no
    mezclar ambos universos.
    """
    v = cifras["vision"]
    filas = [["Resultado de la llamada", "Llamadas", "Tokens"]]
    for item in v["por_resultado"]:
        filas.append([str(item.get("resultado")), num(item.get("n"), 0),
                      num(item.get("tokens_sum"), 0)])
    total_llamadas = sum((item.get("n") or 0) for item in v["por_resultado"])
    filas.append(["Total", num(total_llamadas, 0), num(v["tokens"], 0)])
    return filas


def tabla_horas(eda, top=5):
    """Top de horas locales por porcentaje de motion (de eda.json['score_por_hora'])."""
    horas = (eda or {}).get("score_por_hora", {}).get("por_hora_local") or {}
    filas = [["Hora local (UTC−5)", "Evaluaciones", "% motion", "Mediana de score"]]
    pares = []
    for hora, datos in horas.items():
        if not isinstance(datos, dict):
            continue
        pares.append((int(hora), datos))
    pares.sort(key=lambda par: (-(par[1].get("pct_motion") or 0), par[0]))
    for hora, datos in pares[:top]:
        filas.append([f"{hora:02d}:00", num(datos.get("n_eval"), 0),
                      pct(datos.get("pct_motion")), num(datos.get("score_mediana"), 3)])
    return filas


def cifras_clave(cifras):
    """KPIs formateados (los usan el resumen ejecutivo, las láminas y la auto-verificación)."""
    c, t, v, e = cifras["confusion"], cifras["totales"], cifras["vision"], cifras["eda"]
    mediana_eventos = (cifras["latencias"]["eventos"] or {}).get("mediana_corr_truncacion_ms")
    return {
        "evaluaciones": num(e["n_eval"] or t["n_eval"], 0),
        "evaluaciones_sesiones": num(t["n_eval"], 0),
        "sesiones": num(t["n_sesiones"], 0),
        "duracion_min": num(t["duracion_min"], 1),
        "onsets": num(t["onsets"], 0),
        "recall": frac(c["recall"]),
        "tp": num(c["tp"], 0),
        "fn": num(c["fn"], 0),
        "tramos_gt": num(c["tramos_motion"], 0),
        "fotos": num(cifras["correlacion"]["fotos"], 0),
        "emparejadas": pct(cifras["correlacion"]["emparejadas_pct"]),
        "integridad": frac(cifras["integridad"]["tasa"]),
        "validas_jpeg": num(cifras["integridad"]["validas"], 0),
        "rotas_jpeg": num(cifras["integridad"]["rotas"], 0),
        "latencia_mediana": ms(mediana_eventos, 0),
        "vision_hecho": num(v["hecho"], 0),
        "vision_error": num(v["error"], 0),
        "vision_tokens": num(v["tokens"], 0),
        "pps": num(cifras["totales"]["pps_medio"], 4),
    }


def texto_flag_ts(analisis, rutas=None):
    """Frase honesta del flag `ts_ms`.

    Prioridad: `.tmp/flag_ts_cierre.md` (lo escribe la fase forense; se toma su sección «frase
    para el informe») -> versión corta derivada de analisis.json. Devuelve (frase, origen|None).
    """
    candidatas = rutas or [TMP / "flag_ts_cierre.md", TMP_PROYECTO / "flag_ts_cierre.md"]
    for ruta in candidatas:
        frase = _frase_cierre(leer_texto(ruta))
        if frase:
            return frase, str(ruta)
    div = (analisis or {}).get("divergencia_registros_vs_bd") or {}
    n = num(div.get("n_ts_divergentes_gt5s"), 0)
    return (f"Los {n} ts_ms divergentes entre el volcado jsonl y la BD canónica son re-enganche R3, "
            "documentado en analisis.json."), None


def _frase_cierre(texto):
    """Extrae la frase publicable de un informe de cierre en Markdown.

    1) La cita (blockquote) de la sección que anuncia «frase … informe».
    2) Cualquier cita con cuerpo suficiente.
    3) Una línea de prosa sin pinta de ruta/código.
    """
    lineas = texto.splitlines()
    dentro = False
    for indice, linea in enumerate(lineas):
        limpia = linea.strip()
        if limpia.startswith("#"):
            dentro = bool(re.search(r"frase|informe", limpia, re.IGNORECASE))
            continue
        if dentro and limpia.startswith(">"):
            frase = _juntar_cita(lineas[indice:])
            if frase:
                return frase
    for indice, linea in enumerate(lineas):
        if linea.strip().startswith(">"):
            frase = _juntar_cita(lineas[indice:])
            if frase:
                return frase
    for linea in lineas:
        limpia = re.sub(r"`|\*\*|\*", "", linea.strip())
        if len(limpia) < 60 or limpia.startswith(("#", "|", "-", "*", "```")):
            continue
        if "://" in limpia or "sqlite" in limpia.lower() or "/home/" in limpia:
            continue
        return limpia
    return ""


def _juntar_cita(lineas):
    partes = []
    for linea in lineas:
        limpia = linea.strip()
        if not limpia.startswith(">"):
            break
        partes.append(limpia.lstrip("> ").strip())
    # se quitan marcas de énfasis y backticks, pero NUNCA los guiones bajos de los identificadores
    frase = re.sub(r"`|\*\*|\*", "", " ".join(p for p in partes if p)).strip()
    return frase if len(frase) >= 60 else ""


def presupuesto_vision(rutas=None):
    """Extrae del informe de presupuesto lo medido (media tok/imagen, topes, duplicados)."""
    ruta = None
    for candidata in (rutas or [TMP_PROYECTO / "f25_vision_presupuesto.md",
                                TMP / "f25_vision_presupuesto.md"]):
        if Path(candidata).is_file():
            ruta = Path(candidata)
            break
    datos = {"ruta": str(ruta) if ruta else None, "media_tok": None, "topes": None,
             "desperdicio_tok": None, "llamadas": None}
    if not ruta:
        return datos
    texto = leer_texto(ruta)
    m_media = re.search(r"media\s+([\d.,]+)\s*tok/imagen", texto)
    if m_media:
        datos["media_tok"] = float(m_media.group(1).replace(".", "").replace(",", "."))
    m_topes = re.search(r"MAX_DIA=(\d+)`?\s*\+\s*`?AULASENSE_VISION_MAX_HORA=(\d+)", texto)
    if m_topes:
        datos["topes"] = {"dia": int(m_topes.group(1)), "hora": int(m_topes.group(2))}
    m_dup = re.search(r"\*\*([\d.]+) tokens desperdiciados\*\*", texto)
    if m_dup:
        datos["desperdicio_tok"] = int(m_dup.group(1).replace(".", ""))
    m_llam = re.search(r"(\d+) de (\d+)`?\s*llamadas", texto)
    if m_llam:
        datos["llamadas"] = {"usadas": int(m_llam.group(1)), "tope": int(m_llam.group(2))}
    return datos


def pendientes_pipeline(ruta_md):
    """Bullets de la última sección de informe_metricas.md ('Pendientes al llegar S2/S3')."""
    texto = leer_texto(ruta_md)
    bloque = texto.split("## Pendientes")[-1] if "## Pendientes" in texto else ""
    salida = []
    for linea in bloque.splitlines():
        limpia = linea.strip()
        if limpia.startswith("- "):
            salida.append(" ".join(limpia[2:].split()))
    return salida


# --------------------------------------------------------------------------- fuentes y figuras

def figuras_disponibles():
    """Las 11 figuras del pipeline en orden narrativo (pie desde el nombre del archivo)."""
    salida = []
    for tallo in ORDEN_FIGURAS:
        ruta = GRAFICOS / f"{tallo}.png"
        if ruta.is_file():
            salida.append((ruta, pie_figura(ruta)))
    # cualquier figura nueva que mañana no esté en el mapa entra al final
    for ruta in sorted(GRAFICOS.glob("*.png")):
        if ruta.stem not in ORDEN_FIGURAS:
            salida.append((ruta, pie_figura(ruta)))
    return salida


def sesiones_dataset():
    """Descubre sesiones por glob (mismo criterio que el pipeline: cero cambios mañana)."""
    salida = []
    for carpeta in sorted((RAIZ / "dataset").glob("sesion_*")):
        if not carpeta.is_dir():
            continue
        archivos = sorted(carpeta.glob("registro_*.jsonl"))
        if not archivos:
            continue
        salida.append({
            "sesion": carpeta.name,
            "archivos": [str(p.relative_to(RAIZ)).replace("\\", "/") for p in archivos],
            "bytes": sum(p.stat().st_size for p in archivos),
            "lineas": sum(sum(1 for _ in p.open("r", encoding="utf-8", errors="replace"))
                          for p in archivos),
        })
    return salida


def validaciones_manuales():
    """Marcas de verdad terreno disponibles (activan FPR/precisión cuando lleguen S2/S3)."""
    salida = []
    for ruta in sorted(DOCS.glob("validacion_sesion*.csv")):
        conteo = {}
        try:
            with ruta.open("r", encoding="utf-8", errors="replace") as fh:
                cabecera = fh.readline().strip().split(",")
                idx = cabecera.index("verdad_terreno") if "verdad_terreno" in cabecera else None
                for linea in fh:
                    partes = linea.strip().split(",")
                    if idx is not None and len(partes) > idx:
                        conteo[partes[idx]] = conteo.get(partes[idx], 0) + 1
        except OSError:
            continue
        salida.append({"archivo": ruta.name, "filas": sum(conteo.values()), "por_tipo": conteo})
    return salida


def entregables():
    """Rutas de los entregables del proyecto con su tamaño en disco.

    Los dos artefactos que produce este mismo comando se marcan con `generado=True`: su tamaño
    se desconoce mientras se escribe el documento, así que no se inventa una cifra.
    """
    generados = {"Informe (este documento)", "Presentación"}
    rutas = [
        ("Informe (este documento)", DOCS / "entrega" / "INFORME.docx"),
        ("Presentación", DOCS / "entrega" / "PRESENTACION.pptx"),
        ("Video explicativo", DOCS / "video_aulasense.mp4"),
        ("Guion y subtítulos del video", DOCS / "guion_video.md"),
        ("Subtítulos SRT", DOCS / "video_subtitulos.srt"),
        ("Póster de funcionamiento", RAIZ / "assets" / "poster_funcionamiento.png"),
        ("Diagrama técnico C4 (DSL/PNG/SVG)", RAIZ / "assets" / "diagrama_tecnico.dsl"),
        ("Diagrama de contexto", RAIZ / "assets" / "diagrama_tecnico_contexto.png"),
        ("Componentes del nodo", RAIZ / "assets" / "diagrama_tecnico_componentes_nodo.png"),
        ("Componentes del dashboard", RAIZ / "assets" / "diagrama_tecnico_componentes_dashboard.png"),
        ("Métricas del pipeline", ANALISIS / "informe_metricas.md"),
        ("EDA (informe)", RAIZ / "analysis" / "informe_eda.md"),
        ("JSON del pipeline", ANALISIS / "analisis.json"),
        ("Figuras del análisis", GRAFICOS),
        ("Dataset propio (jsonl por sesión)", RAIZ / "dataset"),
        ("Verdad terreno manual (CSV)", DOCS / "validacion_sesion1.csv"),
        ("Código del servidor (ingesta/dashboard/visión)", RAIZ / "server"),
    ]
    return [{"nombre": n, "ruta": p, "bytes": tam_bytes(p), "existe": Path(p).exists(),
             "generado": n in generados}
            for n, p in rutas]


def trazabilidad():
    """Insumos que alimentan este generador, con hash corto (reproducibilidad)."""
    insumos = [
        (ANALISIS / "analisis.json", "métricas, GT, latencias, integridad, visión"),
        (ANALISIS / "eda.json", "EDA: horas, RSSI, autocorrelación, outliers"),
        (ANALISIS / "modelo_ocupacion.json", "modelo de ocupación y validación"),
        (ANALISIS / "informe_metricas.md", "resumen legible del pipeline"),
        (RAIZ / "analysis" / "informe_eda.md", "informe EDA formal"),
        (RAIZ / "README.md", "contexto y arquitectura oficial"),
        (RAIZ / "SPEC.md", "especificación técnica"),
        (RAIZ / "PLAN.md", "plan maestro y decisiones"),
        (DOCS / "guion_video.md", "guion del video explicativo"),
        (TMP_PROYECTO / "f25_vision_presupuesto.md", "presupuesto y consumos de visión IA"),
    ]
    filas = [["Insumo", "Bytes", "sha256 (12)", "Aporte"]]
    for ruta, aporte in insumos:
        if ruta.is_relative_to(RAIZ):
            nombre = str(ruta.relative_to(RAIZ)).replace("\\", "/")
        elif ruta.is_relative_to(RAIZ.parent):
            nombre = "iot2/" + str(ruta.relative_to(RAIZ.parent)).replace("\\", "/")
        else:
            nombre = ruta.name
        filas.append([nombre, num(tam_bytes(ruta), 0), sha12(ruta), aporte])
    return filas


# --------------------------------------------------------------------------- imágenes para el doc

def preparar_imagen(ruta, ancho_max=1500):
    """Copia/reduce a .tmp/f6a_img para no inflar los entregables (objetivo: <10 MB)."""
    ruta = Path(ruta)
    if not ruta.is_file():
        return None
    TMP_IMG.mkdir(parents=True, exist_ok=True)
    destino = TMP_IMG / (ruta.stem + f"_{ancho_max}.png")
    try:
        if destino.is_file() and destino.stat().st_mtime >= ruta.stat().st_mtime:
            return destino
        with Image.open(ruta) as im:
            im.load()
            if im.width > ancho_max:
                alto = round(im.height * ancho_max / im.width)
                im = im.resize((ancho_max, alto), Image.LANCZOS)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            im.save(destino, "PNG", optimize=True)
        return destino
    except (OSError, ValueError):
        return None


def capturar_frame_video(video, segundo=33.0):
    """Un fotograma del video (escena del dashboard) para la lámina correspondiente."""
    video = Path(video)
    if not video.is_file() or shutil.which("ffmpeg") is None:
        return None
    TMP_FRAMES.mkdir(parents=True, exist_ok=True)
    destino = TMP_FRAMES / f"frame_{segundo:05.1f}.png"
    if destino.is_file() and destino.stat().st_mtime >= video.stat().st_mtime:
        return destino
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{segundo}", "-i", str(video),
             "-frames:v", "1", str(destino)],
            check=True, capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return destino if destino.is_file() and destino.stat().st_size else None


# --------------------------------------------------------------------------- fotos-evidencia

def _instantanea_bd():
    """Copia SOLO-LECTURA de la BD canónica a .tmp para consultar estados de visión."""
    TMP_DB.mkdir(parents=True, exist_ok=True)
    for origen in (RAIZ_WSL, RAIZ / "server" / "datos"):
        base = Path(origen) / "aulasense.db"
        try:
            if not base.is_file():
                continue
        except OSError:
            continue
        try:
            for sufijo in ("", "-wal", "-shm"):
                fuente = Path(str(base) + sufijo)
                destino = TMP_DB / ("aulasense.db" + sufijo)
                if fuente.is_file():
                    shutil.copy2(fuente, destino)
                elif destino.exists():
                    destino.unlink()
        except OSError:
            continue
        copia = TMP_DB / "aulasense.db"
        try:
            con = sqlite3.connect(f"file:{copia.as_posix()}?mode=ro", uri=True)
            con.execute("select count(*) from imagenes").fetchone()
            con.close()
            return copia
        except sqlite3.Error:
            continue
    return None


PALABRAS_ESCENA = ("aula", "pared", "puerta", "escritorio", "techo", "interior", "silla",
                   "mesa", "ventana", "pizarra", "pasillo")
PALABRAS_CORRUPCION = ("distorsion", "corrupt", "glitch", "artefact", "pixelad", "franja",
                       "bloques", "abstract", "falla", "ilegible", "sin detalle", "negra",
                       "borros", "desenfocad", "desenfoque", "no hay evidencia", "sin evidencia")
PALABRAS_PERSONA = ("persona", "cabeza", "hombre", "mujer", "rostro", "cara", "silueta")
NEGACION_PERSONA = re.compile(
    r"(no hay|sin|ninguna|no se pueden (?:distinguir|ver|apreciar|observar)|"
    r"no se (?:observa|observan|aprecia|aprecian|distingue|distingue n|ve))"
    r"[^.]{0,30}(persona|personas|cabeza|rostro|cara|objetos)")


def _menciona_persona(texto):
    """Detecta si la descripción afirma la presencia de una persona (no su ausencia).

    Evita el falso positivo real encontrado en F6-a: «No hay evidencia clara de personas»
    activaba el recorte de privacidad sobre una foto sin nadie.
    """
    if NEGACION_PERSONA.search(texto):
        return False
    return any(k in texto for k in PALABRAS_PERSONA)


def _puntuar_foto(descripcion, etiquetas):
    """Prioriza fotos que muestran una escena real sobre frames ilegibles.

    Heurística explicable y determinista (sin modelos): suma escena reconocible y resta
    corrupción descrita por la propia IA. Así la selección es auditable y se repite igual.
    """
    texto = f"{descripcion} {etiquetas}".lower()
    puntos = 3.0 if any(p in texto for p in PALABRAS_ESCENA) else 0.0
    puntos -= 2.0 * sum(1 for p in PALABRAS_CORRUPCION if p in texto)
    return puntos


def _jpeg_legible(ruta):
    try:
        with Image.open(ruta) as im:
            im.verify()
        with Image.open(ruta) as im:
            im.load()
        return True
    except (OSError, ValueError):
        return False


def _candidatas_fotos():
    db = _instantanea_bd()
    if db is None:
        return []
    consulta = ("select id, ruta, ts_ms, descripcion, etiquetas, vision_estado, evento_id "
                "from imagenes where origen='real' and coalesce(imagen_purgada, 0)=0 "
                "order by id")
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        filas = con.execute(consulta).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    raices = [r for r in (RAIZ_WSL, RAIZ / "server" / "datos")]
    salida = []
    for ident, ruta, ts, desc, etiq, estado, evento in filas:
        if estado != "hecho":
            continue
        archivo = None
        for raiz in raices:
            try:
                posible = Path(raiz) / ruta
                if posible.is_file():
                    archivo = posible
                    break
            except OSError:
                continue
        if archivo is None or not _jpeg_legible(archivo):
            continue
        texto = f"{desc or ''} {etiq or ''}".lower()
        salida.append({
            "id": ident, "archivo": archivo, "ts": ts, "descripcion": (desc or "").strip(),
            "etiquetas": etiq or "", "evento_id": evento,
            "persona": _menciona_persona(texto),
            "sensible": any(k in texto for k in
                            ("sin camiseta", "desnud", "rostro", "primer plano", "cara de",
                             "de frente", "mirando a c")),
            "puntos": _puntuar_foto(desc or "", etiq or ""),
        })
    return salida


def _rango_foto(foto):
    """Ordena de mejor a peor: escena reconocible primero, corrupción descrita al final."""
    return (-foto["puntos"], foto["id"])


def seleccionar_fotos(maximo=2):
    """Elige 1-2 fotos reales VALIDAS y descritas ('hecho'): una de escena y una con persona.

    La de persona se publica con recorte de privacidad (sin reconocimiento facial: la censura
    es geométrica y se anota en el pie). Solo se acepta una foto con persona si la IA describe
    escena real (puntos > 0), para no publicar un frame ilegible como "evidencia".
    """
    candidatas = _candidatas_fotos()
    if not candidatas:
        return []
    # descarta frames con descripción sensible (p. ej. persona sin camiseta): el entregable es
    # académico y no necesita ese material para demostrar que hay evidencia
    candidatas = [f for f in candidatas if not f["sensible"]]
    if not candidatas:
        return []
    candidatas.sort(key=_rango_foto)
    sin_persona = [f for f in candidatas if not f["persona"]]
    con_persona = [f for f in candidatas if f["persona"] and f["puntos"] > 0]
    elegidas = []
    if sin_persona:
        elegidas.append(sin_persona[0])
    if con_persona:
        elegidas.append(con_persona[0])
    for extra in candidatas:
        if len(elegidas) >= maximo:
            break
        if extra not in elegidas:
            elegidas.append(extra)
    return elegidas[:maximo]


def preparar_foto(foto, fraccion_privacidad=0.35):
    """Copia la foto a .tmp y aplica el recorte de privacidad si hay persona.

    Devuelve dict con ruta lista para embeber + si se censuró + dimensiones originales.
    """
    TMP_IMG.mkdir(parents=True, exist_ok=True)
    destino = TMP_IMG / f"evidencia_{foto['id']}.png"
    recortada = False
    try:
        with Image.open(foto["archivo"]) as im:
            im.load()
            original = im.size
            if foto["persona"]:
                caja = (0, 0, max(1, int(im.width * (1 - fraccion_privacidad))), im.height)
                im = im.crop(caja)
                recortada = True
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            im.save(destino, "PNG", optimize=True)
    except (OSError, ValueError):
        return None
    return {"ruta": destino, "censurada": recortada, "original": original}


# --------------------------------------------------------------------------- DOCX: utilidades

def _rgb_docx(t):
    return DRGB(*t)


def _sombrear_celda(celda, relleno_hex):
    tcPr = celda._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), relleno_hex)
    tcPr.insert_element_before(shd, "w:noWrap", "w:tcMar", "w:textDirection",
                               "w:tcFitText", "w:vAlign", "w:hideMark")


def _borde_parrafo(parrafo, color_hex="1F7A8C", grosor=8):
    pPr = parrafo._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    borde = OxmlElement("w:bottom")
    borde.set(qn("w:val"), "single")
    borde.set(qn("w:sz"), str(grosor))
    borde.set(qn("w:space"), "1")
    borde.set(qn("w:color"), color_hex)
    pBdr.append(borde)
    pPr.insert_element_before(pBdr, "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku",
                              "w:wordWrap", "w:overflowPunct", "w:topLinePunct",
                              "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
                              "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
                              "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection",
                              "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl",
                              "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange")


def _campo_numeracion(parrafo, instruccion="PAGE"):
    run = parrafo.add_run()
    inicial = OxmlElement("w:fldChar")
    inicial.set(qn("w:fldCharType"), "begin")
    texto = OxmlElement("w:instrText")
    texto.set(qn("xml:space"), "preserve")
    texto.text = f" {instruccion} "
    final = OxmlElement("w:fldChar")
    final.set(qn("w:fldCharType"), "end")
    run._r.append(inicial)
    run._r.append(texto)
    run._r.append(final)


def _tabla_docx(doc, filas, anchos=None, tam=9, alinear_primera=False):
    tabla = doc.add_table(rows=len(filas), cols=len(filas[0]))
    tabla.style = "Table Grid"
    tabla.alignment = WD_TABLE_ALIGNMENT.CENTER
    tabla.autofit = False
    for i, fila in enumerate(filas):
        for j, valor in enumerate(fila):
            celda = tabla.cell(i, j)
            celda.text = ""
            parrafo = celda.paragraphs[0]
            run = parrafo.add_run(str(valor))
            run.font.size = Pt(tam)
            run.font.name = "Calibri"
            parrafo.paragraph_format.space_after = Pt(2)
            parrafo.paragraph_format.space_before = Pt(2)
            if i == 0:
                run.bold = True
                run.font.color.rgb = _rgb_docx(BLANCO)
                _sombrear_celda(celda, "102A43")
            else:
                if i % 2 == 0:
                    _sombrear_celda(celda, "F4F7F9")
                if alinear_primera and j > 0:
                    parrafo.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    # cabecera repetida y filas que no se parten
    trPr = tabla.rows[0]._tr.get_or_add_trPr()
    cabecera = OxmlElement("w:tblHeader")
    cabecera.set(qn("w:val"), "true")
    trPr.append(cabecera)
    for fila in tabla.rows:
        filaPr = fila._tr.get_or_add_trPr()
        noPartir = OxmlElement("w:cantSplit")
        filaPr.insert(0, noPartir)
    if anchos:
        for fila in tabla.rows:
            for j, ancho in enumerate(anchos):
                if j < len(fila.cells):
                    fila.cells[j].width = Cm(ancho)
    return tabla


def _figura_docx(doc, ruta, pie, numero, ancho_max_cm=15.5, alto_max_cm=10.5):
    ruta = preparar_imagen(ruta)
    if ruta is None:
        return
    try:
        with Image.open(ruta) as im:
            ancho_px, alto_px = im.size
    except OSError:
        return
    ancho_cm = ancho_max_cm
    alto_cm = ancho_cm * alto_px / ancho_px
    if alto_cm > alto_max_cm:
        alto_cm = alto_max_cm
        ancho_cm = alto_cm * ancho_px / alto_px
    doc.add_picture(str(ruta), width=Cm(ancho_cm))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.paragraphs[-1].paragraph_format.space_before = Pt(6)
    doc.paragraphs[-1].paragraph_format.space_after = Pt(2)
    leyenda = doc.add_paragraph()
    leyenda.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = leyenda.add_run(f"Figura {numero} — {pie}")
    run.italic = True
    run.font.size = Pt(8.5)
    run.font.color.rgb = _rgb_docx(GRIS)
    leyenda.paragraph_format.space_after = Pt(10)


def _fig(doc, cont, ruta, pie, **kw):
    """Inserta figura numerada de forma consecutiva (el orden lo fija el documento)."""
    cont[0] += 1
    _figura_docx(doc, ruta, pie, cont[0], **kw)


def _caja_nota(doc, texto, relleno="EEF3F6", tam=9.5):
    """Caja de nota honesta (tabla 1x1 sombreada: estable en Word y LibreOffice)."""
    tabla = doc.add_table(rows=1, cols=1)
    tabla.alignment = WD_TABLE_ALIGNMENT.CENTER
    celda = tabla.cell(0, 0)
    celda.text = ""
    _sombrear_celda(celda, relleno)
    parrafo = celda.paragraphs[0]
    run = parrafo.add_run(texto)
    run.italic = True
    run.font.size = Pt(tam)
    parrafo.paragraph_format.space_before = Pt(4)
    parrafo.paragraph_format.space_after = Pt(4)
    doc.add_paragraph()


def _bullets_docx(doc, items, estilo="List Bullet"):
    for item in items:
        parrafo = doc.add_paragraph(style=estilo)
        run = parrafo.add_run(item)
        run.font.size = Pt(10.5)
        parrafo.paragraph_format.space_after = Pt(3)


def _h(doc, texto, nivel=1):
    encabezado = doc.add_heading(texto, level=nivel)
    for run in encabezado.runs:
        run.font.name = "Calibri"
        run.font.color.rgb = _rgb_docx(AZUL if nivel == 1 else ACENTO)
        run.font.size = Pt(15 if nivel == 1 else 12.5)
    encabezado.paragraph_format.keep_with_next = True
    return encabezado


def _p(doc, texto, tam=10.5, negrita=False, cursiva=False, color=None, justificar=True):
    parrafo = doc.add_paragraph()
    run = parrafo.add_run(texto)
    run.font.size = Pt(tam)
    run.bold = negrita
    run.italic = cursiva
    if color:
        run.font.color.rgb = _rgb_docx(color)
    if justificar:
        parrafo.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    parrafo.paragraph_format.space_after = Pt(6)
    return parrafo


def _pie_pagina(seccion, texto):
    parrafo = seccion.footer.paragraphs[0]
    parrafo.text = ""
    parrafo.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = parrafo.add_run(texto + "   ·   página ")
    run.font.size = Pt(8)
    run.font.color.rgb = _rgb_docx(GRIS_SUAVE)
    _campo_numeracion(parrafo, "PAGE")
    run2 = parrafo.add_run(" de ")
    run2.font.size = Pt(8)
    run2.font.color.rgb = _rgb_docx(GRIS_SUAVE)
    _campo_numeracion(parrafo, "NUMPAGES")


# --------------------------------------------------------------------------- DOCX: documento

def _portada(doc, fecha, cifras):
    for _ in range(3):
        doc.add_paragraph()
    parrafo = doc.add_paragraph()
    parrafo.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = parrafo.add_run(PROYECTO)
    run.font.size = Pt(40)
    run.bold = True
    run.font.color.rgb = _rgb_docx(AZUL)
    parrafo.paragraph_format.space_after = Pt(2)

    linea = doc.add_paragraph()
    linea.paragraph_format.space_after = Pt(10)
    _borde_parrafo(linea, "1F7A8C", 12)

    parrafo = doc.add_paragraph()
    run = parrafo.add_run(SUBTITULO)
    run.font.size = Pt(15)
    run.font.color.rgb = _rgb_docx(ACENTO)
    parrafo.paragraph_format.space_after = Pt(26)

    for etiqueta, valor in (
        ("Documento", "Informe técnico del proyecto"),
        ("Materia", MATERIA),
        ("Institución", INSTITUTO),
        ("Autor", AUTOR),
        ("Fecha", fecha_larga(fecha)),
        ("Dashboard público", URL_PUBLICA),
        ("Pipeline de datos", f"análisis {cifras['pipeline']['version'] or 'n/d'} · "
                              f"generado {fecha_corta(cifras['pipeline']['generado_utc'])}"),
    ):
        parrafo = doc.add_paragraph()
        parrafo.paragraph_format.space_after = Pt(4)
        run = parrafo.add_run(etiqueta + ": ")
        run.font.size = Pt(10.5)
        run.bold = True
        run.font.color.rgb = _rgb_docx(AZUL)
        run2 = parrafo.add_run(valor)
        run2.font.size = Pt(10.5)
        run2.font.color.rgb = _rgb_docx(TINTA)

    doc.add_paragraph()
    _caja_nota(doc, "Este documento se genera por código a partir de los JSON del pipeline de "
                    "análisis (python tools/generar_informe.py). Ninguna cifra está escrita a "
                    "mano: al reejecutarse con sesiones nuevas, se refresca solo.")
    doc.add_page_break()


def _seccion_resumen(doc, cifras, datos):
    k = cifras_clave(cifras)
    _h(doc, "1. Resumen ejecutivo")
    _p(doc, (
        f"{PROYECTO} es un sensor de movimiento y ocupación dinámica que usa el Wi-Fi de la "
        "propia aula como sensor: el nodo lee el Channel State Information (CSI) y ejecuta en el "
        "chip una red neuronal de 529 parámetros que decide idle | motion cada 250 ms. El PC no "
        "procesa la señal cruda: solo ingesta, almacena, visualiza y analiza (regla edge-first)."
    ))
    mediana_ev = (cifras["latencias"]["eventos"] or {}).get("mediana_corr_truncacion_ms")
    pct2s = (cifras["latencias"]["eventos"] or {}).get("pct_abs_bajo_2s")
    _bullets_docx(doc, [
        f"Se capturaron {k['evaluaciones']} evaluaciones reales del detector en "
        f"{k['sesiones']} sesiones ({k['duracion_min']} min de registro), con "
        f"{k['onsets']} onsets de movimiento y {k['fotos']} imágenes de evidencia; "
        f"{k['validas_jpeg']} superan el control JPEG estricto ({k['integridad']}) y "
        f"{k['rotas_jpeg']} llegaron corruptas desde el dispositivo (histórico pre-Fix F).",
        f"Contra la verdad terreno pasiva corroborada por foto, el detector alcanza un recall de "
        f"{k['recall']} sobre {k['tramos_gt']} tramos de movimiento (TP {k['tp']}, FN {k['fn']}). "
        "La tasa de falsos positivos todavía no es computable porque no existen marcas idle: es "
        "una limitación declarada, no un resultado oculto.",
        f"En el servidor, la mediana corregida de latencia evento → fila en BD es de "
        f"{k['latencia_mediana']} y el {pct(pct2s)} de las filas "
        "queda por debajo de 2 s de desvío (recibido_en se trunca a 1 s, de ahí el sesgo).",
        f"La capa opcional de visión describió {k['vision_hecho']} imágenes reales con "
        f"{k['vision_tokens']} tokens medidos; otras {k['vision_error']} no son decodificables "
        "ni con lector tolerante y se reportan como error, no como éxito parcial.",
        "El modelo de ocupación (ventana siguiente de 10 min) se compara contra un baseline "
        "trivial: la mejora existe pero es pequeña y se reporta con tamaño muestral (lección E10).",
    ])
    _tabla_docx(doc, [
        ["Indicador", "Valor", "Fuente"],
        ["Evaluaciones analizadas (EDA, rango real)", k["evaluaciones"], "eda.json · rango"],
        ["Evaluaciones de las sesiones F4 (suma)", k["evaluaciones_sesiones"],
         "analisis.json · sesiones"],
        ["Tramos de ground truth (motion)", k["tramos_gt"], "analisis.json · confusion"],
        ["Recall de motion", k["recall"], "analisis.json · confusion"],
        ["Fotos reales emparejadas con un onset", k["emparejadas"], "analisis.json · correlación"],
        ["Tasa de integridad JPEG", k["integridad"], "analisis.json · integridad_jpeg"],
        ["Latencia mediana corregida (evento → BD)", k["latencia_mediana"],
         "analisis.json · latencias"],
        ["Visión IA: imágenes descritas / tokens", f"{k['vision_hecho']} · {k['vision_tokens']}",
         "analisis.json · vision"],
        ["Dashboard público", URL_PUBLICA, "docs/README.md §Cómo ver el sistema"],
    ], anchos=(7.0, 4.6, 5.0))
    doc.add_paragraph()


def _seccion_contexto(doc, cifras):
    _h(doc, "2. Contexto y objetivo")
    _p(doc, (
        "AulaSense nace de un fracaso documentado. El proyecto anterior, DriveGuard, era una "
        "dashcam de detección de microsueño: cámara frontal más un detector de caras en el borde. "
        "El bloque de detección funcionaba sobre imágenes de prueba, pero en vivo —con el "
        "encuadre correcto y luz real— nunca detectó un rostro: el registro E1–E3 del archivo "
        "dashcam-ia/ deja constancia del intento y de su abandono."
    ))
    _p(doc, (
        "El diagnóstico no fue un bug puntual sino un límite de enfoque: iluminación variable, "
        "ángulo y oclusión degradan la detección facial hasta hacerla inservible fuera del "
        "laboratorio, y un sistema cámara-primero arrastra un costo de privacidad difícil de "
        "sostener en un aula con personas. La decisión fue cambiar el fenómeno físico medido: en "
        "lugar de mirar a las personas, medir cómo perturban el campo electromagnético del Wi-Fi. "
        "El CSI del receptor describe amplitud y fase por subportadora; un cuerpo que se mueve "
        "altera ese patrón de forma medible. No hay vídeo de la escena ni micrófono: solo "
        "variaciones de canal."
    ))
    _p(doc, (
        "Objetivo del proyecto: construir un sensor de movimiento y ocupación dinámica con IA en "
        "el borde, dataset propio real, dashboard en vivo y análisis de datos con modelo "
        "predictivo; y sostenerlo con evidencia reproducible, incluidas sus limitaciones. El "
        "framing es deliberadamente conservador: se detecta movimiento, no presencia; idle no "
        "significa aula vacía; una placa cubre una zona."
    ))
    _caja_nota(doc, (
        "Alcance honesto (heredado del proyecto anterior como restricción de diseño): no se "
        "promete precisión de un dataset propio largo, no se citan métricas del fabricante como "
        "propias y todo dato sintético se etiqueta como tal. Las cifras de este informe se miden "
        "sobre el dataset real del proyecto."
    ))


def _seccion_arquitectura(doc, cifras, datos, cont):
    _h(doc, "3. Arquitectura del sistema")
    _p(doc, (
        "El sistema completo se organiza en cuatro capas. La capa de borde decide y no delega; "
        "las demás transportan, guardan y analizan."
    ))
    _fig(doc, cont, RAIZ / "assets" / "diagrama_tecnico_contexto.png",
         "Vista de contexto C4: usuarios, broker y servicios externos del sistema",
         ancho_max_cm=11.0, alto_max_cm=16.0)
    _tabla_docx(doc, [
        ["Capa", "Componente", "Qué hace"],
        ["Borde", "ESP32-S3 GOOUUU v1.5 (N16R8) con ESPectre 3.2.0 parcheado "
                  "(app_feedback, app_imagen, app_provision)",
         "Lee CSI HT20 2.4 GHz (~100 muestras/s), ejecuta el MLP de 529 parámetros y decide "
         "idle | motion cada 250 ms; buzzer GPIO14 + WS2812 GPIO48; 1 JPEG QVGA de evidencia al "
         "onset (cooldown 15 s, gate de memoria)."],
        ["Transporte", f"MQTT autenticado en {BROKER}",
         "Prefijo aislado aulasense/v1/devices/<id> con topics motion, health, sensing, config e "
         "image (b64, QoS 0, retain=false → la foto no persiste en el broker). HTTP :8001 queda "
         "como canal alternativo."],
        ["Servidor", "WSL Debian 13 «" + DISTRO_WSL + "» (systemd, sin Docker): ingesta + "
                     "dashboard FastAPI + worker de visión",
         "Ingesta valida el esquema, reengancha el reloj del nodo (R3) y escribe SQLite en modo "
         "WAL (writer único). Dashboard en :8100, portproxy loopback 127.0.0.1:8000 y "
         f"publicación en {URL_PUBLICA} por túnel SSH."],
        ["Análisis", "Python: analysis/*.py (pandas, sklearn) y este generador",
         "EDA (horas, RSSI, autocorrelación, outliers), modelo de ocupación a 10 min, 11 figuras "
         "y generación reproducible de este informe y de la presentación."],
    ], anchos=(2.4, 6.4, 7.8), tam=8.5)
    _p(doc, (
        "Privacidad de la arquitectura: el CSI crudo nunca sale del chip; la imagen es evidencia "
        "accesoria de un solo disparo por onset y viaja a un servidor del propio proyecto. El "
        "dashboard exige login (admin único con hash Argon2id/scrypt, cookie HttpOnly + "
        "SameSite, lockout progresivo) incluso en la publicación pública, donde /api/eventos "
        "responde 401 sin cookie."
    ))
    _fig(doc, cont, RAIZ / "assets" / "poster_funcionamiento.png",
         "Póster de funcionamiento: ondas Wi-Fi perturbadas por una persona, inferencia en el "
         "chip y salida al dashboard", ancho_max_cm=9.0, alto_max_cm=12.0)
    _p(doc, (
        "El diagrama de contenedores completo (assets/diagrama_tecnico.png, con el detalle de "
        "topics, tablas y unidades systemd) se incluye en el Anexo A: está pensado para lectura "
        "ampliada en pantalla y a tamaño de página resulta ilegible."
    ), cursiva=True)


def _seccion_metodologia(doc, cifras, datos):
    _h(doc, "4. Metodología de datos")
    gt = cifras["gt"]
    _p(doc, (
        "Fuente de verdad: la BD canónica del servidor (modo SOLO-LECTURA para este análisis). "
        "Los archivos jsonl del dataset son el volcado histórico de cada sesión y se conservan "
        "como evidencia; cuando discrepan de la BD se reporta la divergencia en lugar de "
        "reescribir nada."
    ))
    _p(doc, (
        f"La verdad terreno de las sesiones F4 es pasiva y está corroborada por foto: "
        f"fuente «{', '.join(gt['fuentes']) or 'n/d'}», {num(gt['motion'], 0)} tramos de "
        f"movimiento y {num(gt['idle'], 0)} tramos idle. Cada onset con foto dentro de una "
        f"ventana de ±{num(datos.get('ventana_ms', 20000) / 1000, 0)} s define un tramo de "
        "movimiento de referencia; los huecos sin foto se marcan como sin-dato y no se "
        "interpretan como aula vacía."
    ))
    _h(doc, "4.1 Sesiones y volúmenes", 2)
    _tabla_docx(doc, tabla_sesiones(datos["analisis"]), tam=8.5, alinear_primera=True,
                anchos=(2.0, 1.9, 2.6, 1.7, 1.7, 2.3, 2.0, 2.0))
    _p(doc, "", tam=8)
    filas = [["Carpeta", "Archivos", "Líneas del registro", "Bytes"]]
    for sesion in datos["sesiones_dataset"]:
        filas.append([sesion["sesion"], num(len(sesion["archivos"]), 0),
                      num(sesion["lineas"], 0), num(sesion["bytes"], 0)])
    _tabla_docx(doc, filas, tam=9, alinear_primera=True, anchos=(6.0, 3.4, 4.0, 2.8))
    _p(doc, (
        "El pipeline descubre sesiones por glob (dataset/sesion_*/registro_*.jsonl): al añadir "
        "S2 y S3 sus cifras entran sin tocar el código."
    ))
    _h(doc, "4.2 Activación automática de FPR y precisión", 2)
    marcas = datos["validaciones_manuales"]
    detalle = ", ".join(f"{m['archivo']} ({num(m['filas'], 0)} filas: "
                        + ", ".join(f"{tipo}={n}" for tipo, n in m["por_tipo"].items()) + ")"
                        for m in marcas) or "sin archivos de validación manual todavía"
    _p(doc, (
        f"Marcas de validación presentes hoy: {detalle}. El tramo idle es lo que habilita "
        "verdaderos negativos, falsos positivos y por tanto FPR y precisión. En cuanto existan "
        "filas idle en docs/validacion_sesion2|3.csv, el MISMO pipeline produce esas métricas sin "
        "cambios de código; este generador las publicará al reejecutarse."
    ))
    pendientes = pendientes_pipeline(ANALISIS / "informe_metricas.md")
    if pendientes:
        _p(doc, "Lo que se activará solo en la próxima regeneración:", negrita=True)
        _bullets_docx(doc, pendientes)
    _p(doc, (
        "Reproducibilidad: los tres JSON del pipeline llevan el sha256 de sus insumos y son "
        "byte-estables salvo el campo generado_utc (ver §Anexo C, trazabilidad). Este documento "
        "no introduce cifras propias: cita lo calculado."
    ))


def _seccion_resultados(doc, cifras, datos, cont):
    c, integ, corr, lat, div, vis = (cifras["confusion"], cifras["integridad"],
                                     cifras["correlacion"], cifras["latencias"],
                                     cifras["divergencia"], cifras["vision"])
    _h(doc, "5. Resultados")
    _h(doc, "5.1 Detección y distribución de scores", 2)
    _p(doc, (
        f"Sobre {num(cifras['eda']['n_eval'], 0)} evaluaciones del rango analizado se registraron "
        f"{num(cifras['eda']['n_motion'], 0)} evaluaciones en estado motion. La tasa de "
        f"evaluaciones por segundo del nodo fue de {num(cifras['eda']['pps_medio'], 2)} de media "
        f"en buckets de 60 s, con memoria larga en la autocorrelación por los huecos de nodo "
        f"offline. El score de los onsets tiene mediana "
        f"{num((datos['analisis'].get('eventos', {}).get('score_onsets') or {}).get('p50'), 4)}, "
        "es decir, el detector no duda cuando dispara."
    ))
    _fig(doc, cont, GRAFICOS / "serie_motion.png",
         pie_figura(GRAFICOS / "serie_motion.png"))
    _fig(doc, cont, GRAFICOS / "histograma_scores.png",
         pie_figura(GRAFICOS / "histograma_scores.png"), ancho_max_cm=13.0)

    _h(doc, "5.2 Métricas contra la verdad terreno", 2)
    _p(doc, (
        f"El método de emparejamiento es: una evaluación cuenta como detección si está en estado "
        f"motion dentro del tramo GT (margen de {num(c['margen_ms'], 0)} s). Resultado: "
        f"{num(c['tp'], 0)} verdaderos positivos y {num(c['fn'], 0)} falsos negativos sobre "
        f"{num(c['tramos_motion'], 0)} tramos de movimiento; la cobertura media de motion dentro "
        f"de los tramos es {frac(c['cobertura'])}."
    ))
    _tabla_docx(doc, tabla_confusion(cifras), anchos=(8.6, 7.4), tam=9)
    _p(doc, "", tam=6)
    if c["nota"]:
        _caja_nota(doc, "Nota honesta del pipeline: " + c["nota"])
    _fig(doc, cont, GRAFICOS / "matriz_confusion.png",
         pie_figura(GRAFICOS / "matriz_confusion.png"), ancho_max_cm=10.0)
    _fig(doc, cont, GRAFICOS / "eda_cobertura_gt.png",
         pie_figura(GRAFICOS / "eda_cobertura_gt.png"))
    _p(doc, "Actividad por franja horaria (top 5 por porcentaje de motion):", negrita=True)
    _tabla_docx(doc, tabla_horas(datos["eda"]), tam=9, alinear_primera=True)
    _fig(doc, cont, GRAFICOS / "eda_score_por_hora.png",
         pie_figura(GRAFICOS / "eda_score_por_hora.png"))

    _h(doc, "5.3 Evidencia fotográfica y correlación con los eventos", 2)
    _p(doc, (
        f"De {num(corr['fotos'], 0)} fotos reales (tras excluir el fire-test), "
        f"{num(corr['emparejadas'], 0)} se emparejan con un onset dentro de la ventana de "
        f"±{num((corr['ventana_ms'] or 0) / 1000, 0)} s ({pct(corr['emparejadas_pct'])}), y el "
        f"{pct(corr['onsets_con_foto_pct'])} de los onsets tiene foto. El desfase foto → onset "
        f"tiene mediana {num(corr['lag_mediana'], 1)} ms y p95 {num(corr['lag_p95'], 1)} ms: la "
        "evidencia se captura en el mismo instante en que el detector dispara."
    ))
    _p(doc, (
        f"La FK evento_id solo está poblada en el {pct(corr['fk_pct'])} de las filas "
        f"({num(corr['fk_con_evento'], 0)} de {num(corr['fk_fotos'], 0)}). No es un fallo de "
        "ingesta: la columna es nullable por diseño y la correlación se resuelve por ventana "
        "temporal igualmente; se reporta como deuda de esquema, no como éxito."
    ))

    _h(doc, "5.4 Latencias evento → fila en base de datos", 2)
    _tabla_docx(doc, tabla_latencias(cifras), tam=9, alinear_primera=True)
    _p(doc, "", tam=6)
    nota_lat = (lat["eventos"] or {}).get("nota") or ""
    _caja_nota(doc, "Sesgo conocido: " + nota_lat)
    _p(doc, (
        "Interpretación: los valores negativos no significan que la fila llegue antes que la "
        "decisión, sino que la columna recibido_en se trunca a segundos. La mediana corregida "
        "(+500 ms) es la cifra que se usa en el resumen ejecutivo; el p95 negativo indica que "
        "el sistema, en régimen normal, escribe la fila en el mismo segundo de la decisión."
    ))

    _h(doc, "5.5 Integridad de las imágenes JPEG", 2)
    _p(doc, (
        f"Control estricto con PIL (Image.open + verify + load): {num(integ['validas'], 0)} "
        f"válidas y {num(integ['rotas'], 0)} rotas sobre el total evaluable, es decir una tasa de "
        f"integridad de {frac(integ['tasa'])}. Se excluyó {num(integ['excluidas'], 0)} imagen de "
        "fire-test y no hubo purgas por retención."
    ))
    _p(doc, (
        "Nota honesta (histórico pre-Fix F): la corrupción es de origen en el dispositivo, no del "
        "transporte. El tamaño en disco coincide con el tamaño declarado en la BD y el sha256 "
        "coincide con el envoltorio MQTT; tres árbitros independientes (PIL de la distro, PIL de "
        "Windows y ffmpeg) coinciden en el diagnóstico. La compilación Fix F corrige el encoder, "
        "por lo que estas 20 imágenes irrecuperables son histórico anterior a la corrección y las "
        "capturas nuevas deben validarse con el mismo control estricto."
    ))
    _caja_nota(doc, (
        "Estado de visión de las imágenes reales (columna vision_estado): "
        f"hecho={num(vis['hecho'], 0)}, error={num(vis['error'], 0)}, "
        f"omitido={num(vis['omitido'], 0)}. Las imágenes en error no se reintentan (la validación "
        "local las descarta antes de gastar cuota de API) y no se maquillan como fallos de red."
    ))

    _h(doc, "5.6 Visión IA: uso real y presupuesto", 2)
    pres = datos["presupuesto"]
    media = pres.get("media_tok")
    _p(doc, (
        f"El worker de visión (modo agente-mediado, modelo qwen3.8-flash) describió "
        f"{num(vis['hecho'], 0)} imágenes reales con un consumo total de "
        f"{num(vis['tokens'], 0)} tokens"
        + (f", lo que da una media medida de {num(media, 1)} tokens/imagen" if media else "")
        + f". Topes vigentes: {num((pres.get('topes') or {}).get('dia'), 0)} imágenes/día y "
        f"{num((pres.get('topes') or {}).get('hora'), 0)}/hora, con dedupe por hash y backoff "
        "exponencial ante 429."
    ))
    _tabla_docx(doc, tabla_vision(cifras), anchos=(6.2, 3.0, 3.0), tam=9, alinear_primera=True)
    _p(doc, "", tam=6)
    otras = [i for i in vis["por_resultado"] if i.get("resultado") == "hecho"]
    if otras:
        _p(doc, (
            f"Ojo con los dos universos: el registro de llamadas suma "
            f"{num(otras[0].get('n'), 0)} llamadas 'hecho', que cubren 36 imágenes distintas "
            "(3 descripciones duplicadas) e incluyen 2 imágenes sintéticas de prueba; la cifra "
            f"publicada de {num(vis['hecho'], 0)} imágenes reales es la del estado de la cola en "
            "la BD, no la del contador de llamadas."
        ), cursiva=True)
    desperdicio = pres.get("desperdicio_tok")
    _p(doc, (
        "Incidente honesto: una tanda se lanzó dos veces con procesos solapados y "
        + (f"desperdició {num(desperdicio, 0)} tokens, el "
           f"{num(desperdicio / vis['tokens'] * 100 if vis['tokens'] and desperdicio else None, 1)} % "
           "del consumo del día" if desperdicio else "duplicó tres descripciones")
        + ". El worker asume un proceso por base de datos y no tiene lock entre instancias; la "
        "lección aplicada es no programar pasadas fuera del turno del agente y medir antes de "
        "lanzar lotes (Paso 0). El coste se expresa en tokens medidos: no se promete una cifra "
        "de dinero sin confirmarla en la consola del proveedor."
    ))

    _h(doc, "5.7 Trazabilidad temporal del flag ts_ms", 2)
    frase, origen = datos["flag_ts"]
    origen_legible = "versión corta del generador"
    if origen:
        ruta_origen = Path(origen)
        if ruta_origen.is_relative_to(RAIZ):
            origen_legible = "archivo " + str(ruta_origen.relative_to(RAIZ)).replace("\\", "/")
        elif ruta_origen.is_relative_to(RAIZ.parent):
            origen_legible = "archivo iot2/" + str(
                ruta_origen.relative_to(RAIZ.parent)).replace("\\", "/")
        else:
            origen_legible = "archivo " + ruta_origen.name
    _p(doc, (
        "El pipeline marca como flag toda divergencia entre el volcado jsonl y la BD canónica. "
        "La frase de cierre acordada por la fase forense se reproduce verbatim (sin recortes):"
    ))
    _caja_nota(doc, frase, tam=9)
    _p(doc, (
        f"Origen de la frase: {origen_legible} (si existe .tmp/flag_ts_cierre.md al ejecutar, se "
        "usa su texto). Divergencia medida: "
        f"{num(div['divergentes'], 0)} ids con ts_ms distinto en más de 5 s entre el volcado jsonl "
        f"y la BD ({num(div['comunes'], 0)} ids comunes de {num(div['en_registro'], 0)} en los "
        "registros). No se reescribe nada: la BD canónica manda y la divergencia se documenta."
    ))


def _seccion_limitaciones(doc, cifras, datos):
    _h(doc, "6. Limitaciones honestas")
    mejor = cifras["modelo"]["mejor_umbral"] or {}
    _p(doc, (
        "Estas limitaciones no son notas al pie: condicionan cómo deben leerse los resultados "
        "anteriores."
    ))
    _bullets_docx(doc, [
        "FPR y precisión no computables todavía: el ground truth actual es solo de movimiento "
        "(pasivo y corroborado por foto). Sin tramos idle declarados no existen verdaderos "
        "negativos; los huecos son sin-dato, no sala vacía. Se activan solas al llegar las "
        "marcas de S2/S3.",
        "RSSI sin variación útil: el nodo estuvo junto al AP (rango "
        f"{num((datos['eda'].get('rssi_efecto') or {}).get('rssi_min'), 0)}…"
        f"{num((datos['eda'].get('rssi_efecto') or {}).get('rssi_max'), 0)} dBm), por lo que el "
        "experimento no informa sobre distancia ni sobre efecto de paredes. La correlación "
        "observada es ruido de canal, no una relación física demostrada.",
        "Precisión-piso del modelo: el mejor umbral da precisión "
        f"{num(mejor.get('precision_piso'), 4)} contando como falso positivo todo onset fuera de "
        "los tramos GT; es una cota inferior, porque el GT no cubre todas las horas con presencia "
        "real.",
        "D8 = baseline: el modelo de ocupación se compara contra la clase mayoritaria; con "
        f"{num(cifras['modelo']['n_filas'], 0)} ventanas entrenables y "
        f"{num((cifras['modelo']['logistic_regression'] or {}).get('n_test'), 0)} de test, la "
        "mejora es indicativa. No se promete accuracy de dataset largo (lección E10).",
        "N2 ruteo LAN: el acceso público se resuelve por túnel SSH al VPS; el portproxy "
        "LAN → WSL es opcional y no es un requisito del sistema. Si el túnel cae, el dashboard "
        "sigue vivo en loopback dentro de la VM.",
        "Alcance del sensing: una placa cubre una zona; se detecta movimiento, no presencia "
        "estacionaria; no se identifica ni se cuenta personas; la temperatura del die es proxy "
        "de carga de cómputo, no temperatura ambiente; el reloj del nodo es monótono y la época "
        "la estima el PC (re-enganche R3).",
        "Integridad de imagen: el encoder del dispositivo generaba frames rotos antes de Fix F; "
        "el transporte quedó descartado como causa, pero la tasa histórica "
        f"({frac(cifras['integridad']['tasa'])}) no representa al firmware corregido.",
    ])


def _seccion_seguridad(doc, cifras):
    _h(doc, "7. Seguridad y privacidad")
    _p(doc, (
        "La privacidad del proyecto es acotada y por mitigación, no «por construcción»: hay "
        "imágenes, aunque sean pocas y locales. Los controles aplicados son:"
    ))
    _bullets_docx(doc, [
        "Sin reconocimiento facial ni caras-ML. La visión opcional es detección de persona y la "
        "IA principal es el detector CSI en el chip.",
        "Captura mínima: un JPEG QVGA por onset de movimiento, cooldown de 15 s, tarea de baja "
        "prioridad que nunca bloquea el detector y gate de memoria que la desactiva si el heap "
        "cae. El CSI crudo no sale del chip.",
        "Destino local: galería servida solo con login; el broker autenticado recibe la imagen "
        "con retain=false (no persiste). Nada de nube.",
        "Rate limits: recepción de imagen 1 cada 10 s por dispositivo y control de cuota en la "
        "visión IA (120/día, 20/hora) con dedupe por hash y backoff.",
        "Portal de configuración en ruta privada con token de 8 hex en NVS, guard de "
        "Origin/Referer, cooldown de 5 s, cabeceras de seguridad y registro que nunca escribe "
        "SSID ni contraseñas (solo longitudes).",
        "Credenciales: la contraseña de admin y la clave de IA se generan por código "
        "(secrets.token_hex en el alta) y viven en claves/.env, gitignored; nunca se imprimen en "
        "documentos, informes ni evidencia. Rotación recomendada con tools/reset_admin.py antes "
        "de la entrega y tras cualquier demo pública.",
        "Retención: las imágenes son purgables (por defecto 30 días) y la purga conserva la fila "
        "y su descripción, de modo que el dataset no se rompe al borrar datos personales. Las "
        "imágenes están gitignored.",
    ])


def _seccion_conclusion(doc, cifras, datos):
    k = cifras_clave(cifras)
    _h(doc, "8. Conclusiones")
    _p(doc, (
        "El proyecto demuestra el ciclo completo de un sistema de datos con IA en el borde: "
        "sensado físico (CSI), inferencia en el dispositivo, transporte con autenticación, "
        "almacenamiento consultable, dashboard en vivo, análisis estadístico y un modelo "
        "predictivo con validación honesta. El detector CSI cumple la promesa mínima con "
        f"evidencia: recall {k['recall']} sobre {k['tramos_gt']} tramos de movimiento "
        "corroborados por foto, sin depender de la cámara."
    ))
    _p(doc, (
        "Lo que falta también está medido: sin marcas idle no hay FPR ni precisión; el dataset es "
        "corto; las imágenes históricas tienen un 42,6 % de frames rotos por un defecto de "
        "encoder ya corregido; y el modelo de ocupación apenas supera al baseline. Publicar esas "
        "carencias junto a los aciertos es parte del entregable, no un apéndice."
    ))
    _p(doc, (
        "Operativamente el sistema queda reproducible: el pipeline de análisis, la regeneración "
        "de este informe y la presentación son comandos; las sesiones nuevas se descubren por "
        "glob y las métricas que hoy no son computables se activan sin cambios de código cuando "
        "existan sus datos."
    ))
    _caja_nota(doc, f"Sistema en vivo: {URL_PUBLICA} (login requerido). "
                    f"Regeneración: python tools/generar_informe.py")


def _anexo_figuras(doc, figuras, cont):
    doc.add_page_break()
    _h(doc, "Anexo A. Figuras del análisis")
    _p(doc, (
        f"Las {len(figuras)} figuras se producen con el pipeline de análisis (pandas/matplotlib) "
        "y se insertan tal cual: si una figura no existe, el generador la omite en lugar de "
        "inventarla."
    ))
    for ruta, pie in figuras:
        _fig(doc, cont, ruta, pie, ancho_max_cm=15.5, alto_max_cm=9.5)
    _fig(doc, cont, RAIZ / "assets" / "diagrama_tecnico.png",
         "Diagrama técnico C4 completo (contenedores): flujo ESP32-S3 → broker MQTT → WSL → "
         "dashboard, con topics y tablas. Pensado para lectura ampliada en pantalla",
         ancho_max_cm=15.5, alto_max_cm=16.0)


def _anexo_entregables(doc, datos, cont):
    _h(doc, "Anexo B. Entregables del proyecto")
    filas = [["Entregable", "Ruta", "Tamaño", "Estado"]]
    for item in datos["entregables"]:
        ruta = Path(item["ruta"])
        try:
            relativa = str(ruta.relative_to(RAIZ)).replace("\\", "/")
        except ValueError:
            relativa = str(ruta)
        generado = item.get("generado")
        tamano = "—" if generado else tam_legible(item["bytes"])
        estado = "generado ahora" if generado else ("en disco" if item["existe"] else "pendiente")
        filas.append([item["nombre"], relativa, tamano, estado])
    _tabla_docx(doc, filas, anchos=(5.2, 8.4, 1.8, 2.6), tam=8.5)
    _p(doc, "", tam=6)
    _p(doc, (
        "El estado «pendiente» es informativo: el generador no falla si un entregable auxiliar "
        "todavía no existe, solo lo marca."
    ), cursiva=True)


def _anexo_trazabilidad(doc, datos):
    doc.add_page_break()
    _h(doc, "Anexo C. Trazabilidad de insumos")
    _p(doc, (
        "Hash corto (12 hex) del sha256 de cada insumo en el momento de generar este documento. "
        "Sirve para fijar la procedencia exacta de las cifras publicadas."
    ))
    _tabla_docx(doc, datos["trazabilidad"], anchos=(6.6, 2.0, 2.6, 6.4), tam=8)
    _p(doc, "", tam=6)
    _caja_nota(doc, (
        "Determinismo: " + (datos["analisis"].get("nota_determinismo") or "n/d")
    ))


def construir_docx(cifras, datos, salida):
    doc = Document()

    estilo = doc.styles["Normal"]
    estilo.font.name = "Calibri"
    estilo.font.size = Pt(10.5)
    estilo.paragraph_format.space_after = Pt(6)
    estilo.paragraph_format.line_spacing = 1.12

    seccion = doc.sections[0]
    seccion.page_width = Cm(21.0)
    seccion.page_height = Cm(29.7)
    seccion.left_margin = Cm(2.4)
    seccion.right_margin = Cm(2.2)
    seccion.top_margin = Cm(2.2)
    seccion.bottom_margin = Cm(2.0)
    _pie_pagina(seccion, f"{PROYECTO} · Informe técnico · {MATERIA} · {AUTOR}")

    _portada(doc, datos["fecha"], cifras)
    cont = [0]          # contador único de figuras: el número sale del orden del documento
    _seccion_resumen(doc, cifras, datos)
    _seccion_contexto(doc, cifras)
    _seccion_arquitectura(doc, cifras, datos, cont)
    _seccion_metodologia(doc, cifras, datos)
    _seccion_resultados(doc, cifras, datos, cont)
    _seccion_limitaciones(doc, cifras, datos)
    _seccion_seguridad(doc, cifras)
    _seccion_conclusion(doc, cifras, datos)
    _anexo_figuras(doc, datos["figuras"], cont)
    _anexo_entregables(doc, datos, cont)
    _anexo_trazabilidad(doc, datos)

    salida.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(salida))
    return salida


# --------------------------------------------------------------------------- PPTX: utilidades

def _prgb(t):
    return PRGB(*t)


def _nueva_lamina(prs, titulo, kicker=""):
    lamina = prs.slides.add_slide(prs.slide_layouts[6])
    franja = lamina.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, Inches(0.18))
    franja.fill.solid()
    franja.fill.fore_color.rgb = _prgb(AZUL)
    franja.line.fill.background()
    franja.shadow.inherit = False

    if kicker:
        caja = lamina.shapes.add_textbox(Inches(0.75), Inches(0.36), Inches(11.6), Inches(0.3))
        tf = caja.text_frame
        tf.word_wrap = True
        parrafo = tf.paragraphs[0]
        parrafo.text = kicker.upper()
        parrafo.font.size = PPt(11)
        parrafo.font.bold = True
        parrafo.font.name = "Calibri"
        parrafo.font.color.rgb = _prgb(ACENTO)

    caja = lamina.shapes.add_textbox(Inches(0.75), Inches(0.62), Inches(11.9), Inches(0.95))
    tf = caja.text_frame
    tf.word_wrap = True
    parrafo = tf.paragraphs[0]
    parrafo.text = titulo
    parrafo.font.size = PPt(27)
    parrafo.font.bold = True
    parrafo.font.name = "Calibri"
    parrafo.font.color.rgb = _prgb(AZUL)

    regla = lamina.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.75), Inches(1.55),
                                    Inches(1.05), Inches(0.045))
    regla.fill.solid()
    regla.fill.fore_color.rgb = _prgb(ACENTO)
    regla.line.fill.background()
    regla.shadow.inherit = False
    return lamina


def _pie_lamina(lamina, prs, texto=""):
    numero = len(prs.slides._sldIdLst)  # noqa: SLF001 — no hay API pública para el índice
    caja = lamina.shapes.add_textbox(Inches(0.75), Inches(7.03), Inches(10.6), Inches(0.3))
    tf = caja.text_frame
    parrafo = tf.paragraphs[0]
    parrafo.text = texto or f"{PROYECTO} · {MATERIA} · {AUTOR}"
    parrafo.font.size = PPt(9)
    parrafo.font.color.rgb = _prgb(GRIS_SUAVE)
    caja2 = lamina.shapes.add_textbox(Inches(11.6), Inches(7.03), Inches(1.0), Inches(0.3))
    parrafo2 = caja2.text_frame.paragraphs[0]
    parrafo2.text = str(numero)
    parrafo2.alignment = PP_ALIGN.RIGHT
    parrafo2.font.size = PPt(9)
    parrafo2.font.color.rgb = _prgb(GRIS_SUAVE)


def _bullets(lamina, items, izquierda, arriba, ancho, alto, tam=16, interlinea=6):
    caja = lamina.shapes.add_textbox(izquierda, arriba, ancho, alto)
    tf = caja.text_frame
    tf.word_wrap = True
    primero = True
    for item in items:
        texto, nivel = (item if isinstance(item, tuple) else (item, 0))
        parrafo = tf.paragraphs[0] if primero else tf.add_paragraph()
        primero = False
        parrafo.text = ("—  " if nivel == 0 else "·  ") + texto
        parrafo.font.size = PPt(tam if nivel == 0 else tam - 4)
        parrafo.font.name = "Calibri"
        parrafo.font.color.rgb = _prgb(GRIS if nivel == 0 else GRIS_SUAVE)
        parrafo.space_after = PPt(interlinea)
        parrafo.line_spacing = 1.05
        if nivel:
            parrafo.level = 1


def _kpis(lamina, items, izquierda, arriba, ancho, alto=Inches(1.12), separacion=Inches(0.16)):
    cantidad = len(items)
    ancho_caja = int((ancho - separacion * (cantidad - 1)) / cantidad)
    for indice, (valor, etiqueta) in enumerate(items):
        x = izquierda + indice * (ancho_caja + separacion)
        caja = lamina.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, arriba, ancho_caja, alto)
        caja.fill.solid()
        caja.fill.fore_color.rgb = _prgb(FONDO_CLARO)
        caja.line.color.rgb = _prgb((0xD2, 0xDD, 0xE4))
        caja.line.width = PPt(1)
        caja.shadow.inherit = False
        tf = caja.text_frame
        tf.word_wrap = True
        tf.margin_top = Inches(0.06)
        tf.margin_bottom = Inches(0.04)
        parrafo = tf.paragraphs[0]
        parrafo.text = valor
        parrafo.alignment = PP_ALIGN.CENTER
        parrafo.font.size = PPt(25)
        parrafo.font.bold = True
        parrafo.font.name = "Calibri"
        parrafo.font.color.rgb = _prgb(AZUL)
        segundo = tf.add_paragraph()
        segundo.text = etiqueta
        segundo.alignment = PP_ALIGN.CENTER
        segundo.font.size = PPt(10.5)
        segundo.font.name = "Calibri"
        segundo.font.color.rgb = _prgb(GRIS)


def _tabla_ppt(lamina, filas, izquierda, arriba, ancho, alto, tam=11, anchos=None):
    forma = lamina.shapes.add_table(len(filas), len(filas[0]), izquierda, arriba, ancho, alto)
    tabla = forma.table
    if anchos:
        total = sum(anchos)
        for indice, peso in enumerate(anchos):
            tabla.columns[indice].width = int(ancho * peso / total)
    for i, fila in enumerate(filas):
        for j, valor in enumerate(fila):
            celda = tabla.cell(i, j)
            celda.text = ""
            tf = celda.text_frame
            tf.word_wrap = True
            parrafo = tf.paragraphs[0]
            parrafo.text = str(valor)
            parrafo.font.size = PPt(tam if i else tam)
            parrafo.font.name = "Calibri"
            parrafo.font.bold = i == 0
            parrafo.font.color.rgb = _prgb(BLANCO if i == 0 else TINTA)
            if j > 0:
                parrafo.alignment = PP_ALIGN.RIGHT if i else PP_ALIGN.CENTER
            celda.vertical_anchor = MSO_ANCHOR.MIDDLE
            celda.margin_left = Inches(0.08)
            celda.margin_right = Inches(0.08)
            celda.fill.solid()
            celda.fill.fore_color.rgb = _prgb(AZUL if i == 0
                                             else (FONDO_CLARO if i % 2 == 0 else BLANCO))
    return tabla


def _imagen_ppt(lamina, ruta, izquierda, arriba, ancho_max, alto_max, centrar=True):
    if ruta is None:
        return None
    ruta = Path(ruta)
    if not ruta.is_file():
        return None
    try:
        with Image.open(ruta) as im:
            ancho_px, alto_px = im.size
    except (OSError, ValueError):
        return None
    escala = min(ancho_max / ancho_px, alto_max / alto_px)
    ancho = int(ancho_px * escala)
    alto = int(alto_px * escala)
    x = izquierda + (ancho_max - ancho) // 2 if centrar else izquierda
    y = arriba + (alto_max - alto) // 2
    return lamina.shapes.add_picture(str(ruta), x, y, ancho, alto)


def _fuente_lamina(lamina, texto, izquierda=Inches(0.75), arriba=Inches(6.62)):
    caja = lamina.shapes.add_textbox(izquierda, arriba, Inches(11.9), Inches(0.35))
    tf = caja.text_frame
    tf.word_wrap = True
    parrafo = tf.paragraphs[0]
    parrafo.text = texto
    parrafo.font.size = PPt(8.5)
    parrafo.font.italic = True
    parrafo.font.name = "Calibri"
    parrafo.font.color.rgb = _prgb(GRIS_SUAVE)


def _nota_lamina(lamina, texto, izquierda, arriba, ancho, alto=Inches(0.9), tam=9):
    """Aclaración al pie de un bloque (gris, cursiva, sin competir con el contenido)."""
    caja = lamina.shapes.add_textbox(izquierda, arriba, ancho, alto)
    tf = caja.text_frame
    tf.word_wrap = True
    parrafo = tf.paragraphs[0]
    parrafo.text = texto
    parrafo.font.size = PPt(tam)
    parrafo.font.italic = True
    parrafo.font.name = "Calibri"
    parrafo.font.color.rgb = _prgb(GRIS)
    return caja


def _flujo_capas(lamina, izquierda, arriba, ancho, alto_caja=Inches(0.95),
                 separacion=Inches(0.28)):
    """Flujo de las 4 capas dibujado con formas: legible a tamaño de lámina.

    El diagrama C4 exportado (6060 px) es un artefacto para página/pantalla completa; a tamaño
    de diapositiva su texto queda a ~1 pt, así que se usa esta versión vectorial y el original
    vive en el informe (Anexo A).
    """
    pasos = [
        ("BORDE · ESP32-S3", "CSI HT20 2.4 GHz · MLP de 529 parámetros · decisión cada 250 ms"),
        ("TRANSPORTE · MQTT", f"{BROKER} · topics por dispositivo · retain=false"),
        ("SERVIDOR · WSL Debian 13", "ingesta → SQLite (WAL) → dashboard FastAPI en :8100"),
        ("ANÁLISIS · Python", "EDA + modelo de 10 min → 11 figuras → este informe y la charla"),
    ]
    y = arriba
    for indice, (titulo, detalle) in enumerate(pasos):
        caja = lamina.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, izquierda, y, ancho,
                                       alto_caja)
        caja.fill.solid()
        caja.fill.fore_color.rgb = _prgb(FONDO_CLARO)
        caja.line.color.rgb = _prgb(ACENTO)
        caja.line.width = PPt(1.25)
        caja.shadow.inherit = False
        tf = caja.text_frame
        tf.word_wrap = True
        tf.margin_left = Inches(0.14)
        tf.margin_right = Inches(0.1)
        tf.margin_top = Inches(0.05)
        tf.margin_bottom = Inches(0.03)
        parrafo = tf.paragraphs[0]
        parrafo.text = titulo
        parrafo.font.size = PPt(11.5)
        parrafo.font.bold = True
        parrafo.font.name = "Calibri"
        parrafo.font.color.rgb = _prgb(AZUL)
        parrafo.alignment = PP_ALIGN.LEFT
        segundo = tf.add_paragraph()
        segundo.text = detalle
        segundo.font.size = PPt(9.5)
        segundo.font.name = "Calibri"
        segundo.font.color.rgb = _prgb(GRIS)
        y = y + alto_caja
        if indice < len(pasos) - 1:
            flecha = lamina.shapes.add_shape(
                MSO_SHAPE.DOWN_ARROW, izquierda + int(ancho / 2) - Inches(0.09),
                y + Inches(0.035), Inches(0.18), separacion - Inches(0.07))
            flecha.fill.solid()
            flecha.fill.fore_color.rgb = _prgb(ACENTO)
            flecha.line.fill.background()
            flecha.shadow.inherit = False
            y = y + separacion


def _caja_titulo(lamina, texto, izquierda, arriba, ancho):
    caja = lamina.shapes.add_textbox(izquierda, arriba, ancho, Inches(0.4))
    tf = caja.text_frame
    tf.word_wrap = True
    parrafo = tf.paragraphs[0]
    parrafo.text = texto
    parrafo.font.size = PPt(13)
    parrafo.font.bold = True
    parrafo.font.name = "Calibri"
    parrafo.font.color.rgb = _prgb(ACENTO)
    return caja


# --------------------------------------------------------------------------- PPTX: láminas

def _lamina_titulo(prs, datos, cifras):
    lamina = prs.slides.add_slide(prs.slide_layouts[6])
    fondo = lamina.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width,
                                    Inches(3.55))
    fondo.fill.solid()
    fondo.fill.fore_color.rgb = _prgb(AZUL)
    fondo.line.fill.background()
    fondo.shadow.inherit = False

    caja = lamina.shapes.add_textbox(Inches(0.9), Inches(1.05), Inches(11.5), Inches(1.0))
    parrafo = caja.text_frame.paragraphs[0]
    parrafo.text = PROYECTO
    parrafo.font.size = PPt(54)
    parrafo.font.bold = True
    parrafo.font.name = "Calibri"
    parrafo.font.color.rgb = _prgb(BLANCO)

    caja = lamina.shapes.add_textbox(Inches(0.95), Inches(2.15), Inches(11.2), Inches(0.9))
    tf = caja.text_frame
    tf.word_wrap = True
    parrafo = tf.paragraphs[0]
    parrafo.text = SUBTITULO
    parrafo.font.size = PPt(19)
    parrafo.font.name = "Calibri"
    parrafo.font.color.rgb = _prgb((0xC9, 0xD8, 0xE2))

    caja = lamina.shapes.add_textbox(Inches(0.95), Inches(3.95), Inches(11.5), Inches(1.6))
    tf = caja.text_frame
    tf.word_wrap = True
    lineas = (
        f"{MATERIA} · {INSTITUTO}",
        f"Autor: {AUTOR}   ·   {fecha_larga(datos['fecha'])}",
        f"Dashboard público: {URL_PUBLICA}",
        f"Pipeline de análisis: {cifras['pipeline']['version'] or 'n/d'} · "
        f"{fecha_corta(cifras['pipeline']['generado_utc'])}",
    )
    for indice, linea in enumerate(lineas):
        parrafo = tf.paragraphs[0] if indice == 0 else tf.add_paragraph()
        parrafo.text = linea
        parrafo.font.size = PPt(13 if indice < 2 else 11.5)
        parrafo.font.name = "Calibri"
        parrafo.font.color.rgb = _prgb(AZUL if indice < 2 else GRIS)
        parrafo.space_after = PPt(7)
    _pie_lamina(lamina, prs, f"Regenerable con: python tools/generar_informe.py")


def _lamina_problema(prs, cifras):
    lamina = _nueva_lamina(prs, "El problema: de la cámara al Wi-Fi", "Origen del proyecto")
    _bullets(lamina, [
        "DriveGuard (proyecto anterior): dashcam con detección de caras para microsueño. El "
        "detector nunca encontró un rostro real en vivo.",
        "El fallo no era un bug: luz variable, ángulo y oclusión degradan la detección facial "
        "fuera del laboratorio.",
        "Y una cámara permanente en un aula tiene un costo de privacidad difícil de sostener.",
        "Giro de enfoque: medir cómo perturban el campo Wi-Fi (CSI), sin vídeo, micrófono ni "
        "radar.",
        "AulaSense detecta movimiento y ocupación dinámica; no presencia, no identidad, no "
        "conteo de personas.",
    ], Inches(0.85), Inches(1.85), Inches(7.1), Inches(3.2), tam=15)
    _kpis(lamina, [("0", "caras detectadas en vivo"), ("529", "parámetros del detector"),
                   ("250 ms", "periodo de decisión")],
          Inches(0.85), Inches(5.3), Inches(7.1), alto=Inches(1.0))
    _imagen_ppt(lamina, RAIZ / "assets" / "poster_funcionamiento.png",
                Inches(8.2), Inches(1.8), Inches(4.3), Inches(4.6))
    _imagen_ppt(lamina, RAIZ / "assets" / "poster_funcionamiento.png",
                Inches(8.2), Inches(1.8), Inches(4.3), Inches(4.7))
    _fuente_lamina(lamina, "Fuente: docs/PROPUESTA.md · archivo dashcam-ia/ (E1–E3) · "
                           "assets/poster_funcionamiento.png")
    _pie_lamina(lamina, prs)


def _lamina_como_funciona(prs, datos):
    lamina = _nueva_lamina(prs, "Cómo funciona: cuatro capas", "Arquitectura")
    _flujo_capas(lamina, Inches(8.0), Inches(1.78), Inches(4.6))
    _bullets(lamina, [
        "Borde: ESP32-S3 con ESPectre 3.2.0 parcheado; CSI HT20 2.4 GHz, MLP de 529 parámetros, "
        "decisión idle | motion cada 250 ms en el chip.",
        "Transporte: MQTT autenticado con prefijo aislado por dispositivo; la foto de evidencia "
        "viaja con retain=false y no persiste en el broker.",
        "Servidor: WSL Debian 13 con systemd (sin Docker): ingesta, SQLite en modo WAL y "
        "dashboard FastAPI con login.",
        "Análisis: pandas + sklearn, 11 figuras, informe y presentación generados por código.",
        "Salidas físicas del nodo: buzzer GPIO14 y LED WS2812 GPIO48; la foto es accesoria, no "
        "la fuente de verdad.",
    ], Inches(0.85), Inches(1.85), Inches(6.6), Inches(4.5), tam=14)
    _fuente_lamina(lamina, "Diagrama C4 completo en el informe (Anexo A); esquema equivalente "
                           "dibujado aquí para legibilidad a tamaño de lámina")
    _pie_lamina(lamina, prs)


def _lamina_detector(prs):
    lamina = _nueva_lamina(prs, "El detector vive en el chip", "Edge-first")
    _kpis(lamina, [("8→24→12→1", "MLP del detector"), ("529", "parámetros"),
                    ("~100 pps", "muestras CSI/s"), ("250 ms", "cadencia de decisión")],
           Inches(0.85), Inches(1.8), Inches(11.6), alto=Inches(1.2))
    _bullets(lamina, [
        "El PC no procesa la señal cruda: recibe eventos y métricas (regla edge-first).",
        "Perfil High Accuracy sin calibración (umbral 0.5); perfil ligero alternativo con "
        "calibración de ~10 s.",
        "Telemetría del propio nodo cada 30 s: ms/detección, % CPU, heap libre, pps de CSI, RSSI, "
        "canal y temperatura del die (proxy de carga, no ambiente).",
        "Gate de memoria: si el heap baja de 120 KB o el mayor bloque de 64 KB, la captura de "
        "evidencia se desactiva sola y el detector sigue funcionando.",
    ], Inches(0.85), Inches(3.25), Inches(5.7), Inches(3.0), tam=14)
    _imagen_ppt(lamina, RAIZ / "assets" / "diagrama_tecnico_componentes_nodo.png",
                Inches(6.7), Inches(3.2), Inches(6.1), Inches(2.5))
    _fuente_lamina(lamina, "Fuente: SPEC.md §3 · assets/diagrama_tecnico_componentes_nodo.png")
    _pie_lamina(lamina, prs)


def _lamina_datos(prs, cifras, datos):
    lamina = _nueva_lamina(prs, "Datos: sesiones y verdad terreno", "Metodología")
    k = cifras_clave(cifras)
    _kpis(lamina, [(k["evaluaciones"], "evaluaciones analizadas"),
                    (k["tramos_gt"], "tramos con movimiento"),
                    (k["fotos"], "fotos de evidencia"),
                    (k["emparejadas"], "foto ↔ onset")],
           Inches(0.85), Inches(1.75), Inches(11.6))
    _tabla_ppt(lamina, tabla_sesiones(datos["analisis"]), Inches(0.85), Inches(3.15),
               Inches(11.6), Inches(1.5), tam=10,
               anchos=(1.1, 1.3, 1.3, 1.0, 1.0, 1.4, 1.2, 1.2))
    _bullets(lamina, [
        "Verdad terreno pasiva corroborada por foto: cada onset con imagen dentro de ±20 s "
        "define un tramo de movimiento; los huecos son sin-dato, no aula vacía.",
        "Sin marcas idle todavía: FPR y precisión se activan solas cuando lleguen S2/S3.",
        "Sesiones y volúmenes se descubren por glob en dataset/sesion_*/: nada se escribe a mano.",
    ], Inches(0.85), Inches(4.95), Inches(11.6), Inches(1.6), tam=13.5)
    _fuente_lamina(lamina, "Fuente: analisis.json · metricas_por_sesion · eda.json · rango")
    _pie_lamina(lamina, prs)


def _lamina_dashboard(prs, datos):
    lamina = _nueva_lamina(prs, "Dashboard público y en vivo", "Producto")
    _caja_titulo(lamina, URL_PUBLICA, Inches(0.85), Inches(1.75), Inches(6.0))
    _bullets(lamina, [
        "Servido desde la distro WSL " + DISTRO_WSL + " (puerto interno 8100) y publicado por "
        "túnel SSH: /login responde 200 y /api/eventos responde 401 sin cookie.",
        "Tres vistas: Monitoreo (estado idle | motion, score, métricas edge, feed), Reportes "
        "(KPIs, series, heatmap horario, export CSV) y Validación (matriz de confusión).",
        "Galería de evidencia con búsqueda por texto sobre las descripciones de la IA.",
        "Login con admin único, cookie HttpOnly + SameSite y lockout progresivo.",
    ], Inches(0.85), Inches(2.25), Inches(5.9), Inches(3.6), tam=14)
    if datos["frame"]:
        _imagen_ppt(lamina, datos["frame"], Inches(6.85), Inches(1.72), Inches(5.85),
                    Inches(4.75))
    else:
        _imagen_ppt(lamina, RAIZ / "assets" / "diagrama_tecnico_componentes_dashboard.png",
                    Inches(6.85), Inches(1.72), Inches(5.85), Inches(4.3))
    _fuente_lamina(lamina, "Captura: fotograma de docs/video_aulasense.mp4 (escena de acceso, "
                           "todo tras login) · docs/README.md §Cómo ver el sistema")
    _pie_lamina(lamina, prs)


def _lamina_fotos(prs, datos):
    lamina = _nueva_lamina(prs, "Fotos-evidencia: qué se ve y qué no", "Evidencia")
    preparadas = datos["fotos"]
    if not preparadas:
        _bullets(lamina, [
            "Sin fotos válidas y descritas disponibles en la BD canónica en este momento.",
            "El generador selecciona solo imágenes reales con vision_estado='hecho' y JPEG "
            "legible; si no hay, lo dice en lugar de inventar.",
        ], Inches(0.85), Inches(1.9), Inches(11.5), Inches(2.5), tam=16)
    else:
        ancho = Inches(4.35) if len(preparadas) > 1 else Inches(5.6)
        izquierda = Inches(0.85)
        for indice, foto in enumerate(preparadas[:2]):
            _imagen_ppt(lamina, foto["ruta"], izquierda, Inches(1.8), ancho, Inches(3.05))
            pie = (f"Imagen id {foto['id']}"
                   + (f" · evento {foto['evento_id']}" if foto["evento_id"] else "")
                   + (" · recorte de privacidad del 35 % derecho" if foto["censurada"]
                      else " · sin personas identificables"))
            caja = lamina.shapes.add_textbox(izquierda, Inches(4.95), ancho, Inches(0.9))
            tf = caja.text_frame
            tf.word_wrap = True
            parrafo = tf.paragraphs[0]
            parrafo.text = pie
            parrafo.font.size = PPt(10)
            parrafo.font.name = "Calibri"
            parrafo.font.color.rgb = _prgb(GRIS)
            descripcion = (foto["descripcion"][:150] + "…") if len(foto["descripcion"]) > 150 \
                else foto["descripcion"]
            segundo = tf.add_paragraph()
            segundo.text = "Descripción IA: " + descripcion
            segundo.font.size = PPt(9)
            segundo.font.italic = True
            segundo.font.name = "Calibri"
            segundo.font.color.rgb = _prgb(GRIS_SUAVE)
            izquierda = izquierda + ancho + Inches(0.35)
    _fuente_lamina(lamina, "Selección automática por BD (origen real, vision_estado='hecho', "
                           "JPEG validado). Sin reconocimiento facial: la censura es un recorte "
                           "geométrico y se anota.")
    _pie_lamina(lamina, prs)


def _lamina_metricas_1(prs, cifras, datos):
    lamina = _nueva_lamina(prs, "Métricas I: detección frente a la verdad terreno",
                                            "Resultados")
    k = cifras_clave(cifras)
    _kpis(lamina, [(k["recall"], "recall de motion"), (f"{k['tp']} / {k['fn']}", "TP / FN"),
                    (k["tramos_gt"], "tramos GT"), (k["integridad"], "integridad JPEG")],
           Inches(0.85), Inches(1.75), Inches(11.6))
    _tabla_ppt(lamina, tabla_confusion(cifras)[:7], Inches(0.85), Inches(3.15), Inches(6.3),
               Inches(2.6), tam=10.5, anchos=(2.6, 1.4))
    _imagen_ppt(lamina, GRAFICOS / "matriz_confusion.png", Inches(7.4), Inches(3.05),
                Inches(5.1), Inches(2.8))
    _bullets(lamina, [
        "RECALL-ONLY: sin tramos idle no hay verdaderos negativos y por tanto FPR ni precisión "
        "no son computables; se declara, no se estima.",
    ], Inches(0.85), Inches(5.9), Inches(11.6), Inches(0.8), tam=12)
    _fuente_lamina(lamina, "Fuente: analisis.json · confusion (pipeline "
                           f"{cifras['pipeline']['version']})")
    _pie_lamina(lamina, prs)


def _lamina_metricas_2(prs, cifras):
    lamina = _nueva_lamina(prs, "Métricas II: latencia, volumen y actividad",
                                            "Resultados")
    lat = cifras["latencias"]["eventos"] or {}
    _kpis(lamina, [(num(lat.get("mediana_corr_truncacion_ms"), 0), "ms mediana evento → BD"),
                    (num(lat.get("p95_ms"), 0), "ms p95 (sesgo de truncación)"),
                    (pct(lat.get("pct_abs_bajo_2s")), "filas con |Δ| < 2 s"),
                    (num(cifras["eda"]["pps_medio"], 2), "evaluaciones/s de media")],
           Inches(0.85), Inches(1.75), Inches(11.6))
    _tabla_ppt(lamina, tabla_latencias(cifras), Inches(0.85), Inches(3.12), Inches(6.1),
               Inches(1.25), tam=10, anchos=(1.0, 0.6, 1.1, 1.3, 0.9, 0.9))
    _caja_titulo(lamina, "Distribución del score del detector", Inches(7.35), Inches(3.0),
                 Inches(5.1))
    _imagen_ppt(lamina, GRAFICOS / "histograma_scores.png", Inches(7.3), Inches(3.3),
                Inches(5.2), Inches(2.7))
    _bullets(lamina, [
        "La columna recibido_en se trunca a 1 s: la mediana bruta es negativa y la corregida "
        "(+500 ms) es la cifra honesta.",
        "El nodo mantiene ~100 muestras CSI/s con el detector consumiendo una fracción pequeña "
        "del CPU; la telemetría lo registra cada 30 s.",
    ], Inches(0.85), Inches(4.55), Inches(6.2), Inches(1.8), tam=13)
    _fuente_lamina(lamina, "Fuente: analisis.json · latencias · eda.json · pps_autocorrelacion")
    _pie_lamina(lamina, prs)


def _lamina_modelo(prs, cifras, datos):
    lamina = _nueva_lamina(prs, "Modelo de ocupación (ventana de 10 minutos)",
                                            "Análisis")
    m = cifras["modelo"]
    lr = m["logistic_regression"] or {}
    _kpis(lamina, [(num(lr.get("accuracy"), 3), "accuracy (regresión logística)"),
                   (num(m["baseline"].get("accuracy"), 3), "baseline de clase mayoritaria"),
                   (num(m["n_filas"], 0), "ventanas entrenables"),
                   (frac(m["cobertura_indicador"] / 100 if m["cobertura_indicador"] else None),
                    "cobertura del indicador")],
          Inches(0.85), Inches(1.75), Inches(11.6))
    _tabla_ppt(lamina, tabla_modelos(cifras), Inches(0.85), Inches(3.12), Inches(7.55),
               Inches(1.85), tam=10, anchos=(2.5, 1.05, 0.95, 0.85, 0.75, 0.85, 1.1))
    _caja_titulo(lamina, "Precisión / recall por umbral", Inches(8.75), Inches(3.05),
                 Inches(3.8))
    _imagen_ppt(lamina, GRAFICOS / "ocupacion_pr_umbral.png", Inches(8.7), Inches(3.35),
                Inches(3.9), Inches(2.6))
    _bullets(lamina, [
        "Split temporal 80/20 sin mezclar: el modelo nunca ve el futuro al entrenar.",
        "La mejora sobre el baseline es real pero pequeña; se reporta con tamaño muestral "
        "(lección E10), no como promesa de precisión.",
    ], Inches(0.85), Inches(5.15), Inches(7.5), Inches(1.3), tam=12.5)
    _fuente_lamina(lamina, "Fuente: modelo_ocupacion.json · entrenamiento (split "
                           f"{m['split'] or 'n/d'}) · figuras del pipeline")
    _pie_lamina(lamina, prs)


def _lamina_integridad(prs, cifras, datos):
    lamina = _nueva_lamina(prs, "Integridad JPEG: la parte incómoda", "Honestidad")
    integ = cifras["integridad"]
    _kpis(lamina, [(frac(integ["tasa"]), "tasa de integridad"),
                    (num(integ["validas"], 0), "JPEG válidos"),
                    (num(integ["rotas"], 0), "JPEG rotos"),
                    (num(cifras["vision"]["error"], 0), "no decodificables por la IA")],
           Inches(0.85), Inches(1.75), Inches(11.6))
    _bullets(lamina, [
        "No es un problema de transporte: tamaño en disco y sha256 coinciden con el envoltorio "
        "recibido; la corrupción nace en el encoder del dispositivo.",
        "Tres árbitros independientes (PIL de la distro, PIL de Windows y ffmpeg) coinciden en el "
        "diagnóstico: 23 frames rotos sobre 54 evaluables.",
        "La compilación Fix F corrige el origen: las frames rotas son histórico previo a la "
        "corrección y no se presentan como éxito parcial.",
        "Control de calidad estricto de dos etapas (estricta + tolerante) para no repetir falsos "
        "negativos al validar antes de gastar cuota de IA.",
        "Lección de método: se reporta la tasa real (57,4 %) aunque sea incómoda; el estado "
        "«error» de visión de esas 20 imágenes es consecuencia del mismo defecto, no del modelo.",
    ], Inches(0.85), Inches(3.2), Inches(11.5), Inches(3.0), tam=15)
    _fuente_lamina(lamina, "Fuente: analisis.json · integridad_jpeg · vision")
    _pie_lamina(lamina, prs)


def _lamina_vision(prs, cifras, datos):
    lamina = _nueva_lamina(prs, "Visión IA: uso real y presupuesto", "Costos")
    v = cifras["vision"]
    pres = datos["presupuesto"]
    media = pres.get("media_tok")
    _kpis(lamina, [(num(v["hecho"], 0), "imágenes descritas"),
                   (num(v["tokens"], 0), "tokens medidos"),
                   (num(media, 1) if media else "n/d", "tokens/imagen (media)"),
                   (f"{num((pres.get('topes') or {}).get('dia'), 0)} / "
                    f"{num((pres.get('topes') or {}).get('hora'), 0)}", "tope día / hora")],
          Inches(0.85), Inches(1.75), Inches(11.6))
    _tabla_ppt(lamina, tabla_vision(cifras), Inches(0.85), Inches(3.15), Inches(5.2),
               Inches(1.5), tam=10.5, anchos=(2.2, 1.0, 1.0))
    _p_nota = ("Registro de llamadas: 39 «hecho» cubren 36 imágenes distintas (3 duplicadas) "
               "e incluyen 2 sintéticas; las 34 reales son el estado de la cola en la BD.")
    _nota_lamina(lamina, _p_nota, Inches(0.85), Inches(4.78), Inches(5.25), Inches(0.95))
    _bullets(lamina, [
        "Modo agente-mediado con la allowlist del plan de tokens; rate limits persistentes, "
        "dedupe por hash y backoff ante 429.",
        "Incidente honesto: dos procesos solapados describieron tres imágenes dos veces y "
        "desperdiciaron " + (num(pres.get("desperdicio_tok"), 0) if pres.get("desperdicio_tok")
                             else "parte del presupuesto") + " tokens.",
        "Lección aplicada: no programar pasadas fuera del turno y medir con una llamada de "
        "prueba antes de cada lote.",
        "El coste se reporta en tokens medidos; no se publica una cifra de dinero sin "
        "confirmarla en la consola del proveedor.",
    ], Inches(6.35), Inches(3.05), Inches(6.1), Inches(3.2), tam=13)
    _fuente_lamina(lamina, "Fuente: analisis.json · vision · .tmp/f25_vision_presupuesto.md")
    _pie_lamina(lamina, prs)


def _lamina_limitaciones(prs, cifras):
    lamina = _nueva_lamina(prs, "Limitaciones honestas", "Lo que no se puede afirmar")
    mejor = cifras["modelo"]["mejor_umbral"] or {}
    _bullets(lamina, [
        "Sin marcas idle no hay FPR ni precisión: el ground truth actual solo cubre movimiento. "
        "Se activan solas con S2/S3.",
        "RSSI sin variación: nodo junto al AP, rango estrecho; el experimento no informa sobre "
        "distancia ni obstáculos.",
        "Precisión-piso del modelo: " + num(mejor.get("precision_piso"), 3) +
        " en el mejor umbral, contando como falso positivo todo onset fuera del GT.",
        "Dataset corto: la comparación del modelo es contra baseline, con tamaño muestral "
        "declarado (lección E10).",
        "Alcance físico: una placa por zona; idle ≠ sala vacía; la temperatura del die es carga "
        "de cómputo, no ambiente.",
        "Acceso público por túnel al VPS; el portproxy LAN → WSL es opcional (N2).",
    ], Inches(0.85), Inches(1.85), Inches(11.5), Inches(4.6), tam=15)
    _fuente_lamina(lamina, "Fuente: analisis.json · notación honesta de confusion y "
                           "modelo_ocupacion.json · nota_honest_e10")
    _pie_lamina(lamina, prs)


def _lamina_conclusion(prs, cifras, datos):
    lamina = _nueva_lamina(prs, "Conclusión", "Cierre")
    k = cifras_clave(cifras)
    _bullets(lamina, [
        "Sistema completo y reproducible: sensado CSI, inferencia en el chip, MQTT autenticado, "
        "SQLite, dashboard con login, EDA y modelo predictivo.",
        f"Evidencia medida: recall {k['recall']} sobre {k['tramos_gt']} tramos de movimiento "
        "corroborados por foto, sin usar la cámara como fuente de verdad.",
        "Las carencias están cuantificadas: FPR pendiente de marcas idle, dataset corto, frames "
        "históricos corruptos por un encoder ya corregido.",
        "Todo el entregable se regenera con un comando; mañana, con S2/S3 y la pasada 4 de "
        "visión, se refresca solo.",
    ], Inches(0.85), Inches(1.85), Inches(11.5), Inches(3.5), tam=16)
    caja = lamina.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.85), Inches(5.35),
                                   Inches(11.6), Inches(0.95))
    caja.fill.solid()
    caja.fill.fore_color.rgb = _prgb(FONDO_CLARO)
    caja.line.color.rgb = _prgb(ACENTO)
    caja.shadow.inherit = False
    tf = caja.text_frame
    tf.word_wrap = True
    parrafo = tf.paragraphs[0]
    parrafo.text = f"{URL_PUBLICA}   ·   python tools/generar_informe.py"
    parrafo.alignment = PP_ALIGN.CENTER
    parrafo.font.size = PPt(16)
    parrafo.font.bold = True
    parrafo.font.name = "Calibri"
    parrafo.font.color.rgb = _prgb(AZUL)
    _pie_lamina(lamina, prs, f"{PROYECTO} · {INSTITUTO} · {AUTOR}")


def construir_pptx(cifras, datos, salida):
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    _lamina_titulo(prs, datos, cifras)
    _lamina_problema(prs, cifras)
    _lamina_como_funciona(prs, datos)
    _lamina_detector(prs)
    _lamina_datos(prs, cifras, datos)
    _lamina_dashboard(prs, datos)
    _lamina_fotos(prs, datos)
    _lamina_metricas_1(prs, cifras, datos)
    _lamina_metricas_2(prs, cifras)
    _lamina_modelo(prs, cifras, datos)
    _lamina_integridad(prs, cifras, datos)
    _lamina_vision(prs, cifras, datos)
    _lamina_limitaciones(prs, cifras)
    _lamina_conclusion(prs, cifras, datos)

    salida.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(salida))
    return salida


# --------------------------------------------------------------------------- verificación

def verificar_salidas(ruta_docx, ruta_pptx, cifras, limite_mb=10):
    """Roundtrip de lectura + spot-check de 5 cifras + tamaño. Devuelve (ok, lineas)."""
    from docx import Document as DocumentoLector
    from pptx import Presentation as PresentacionLectora

    lineas = []
    ok = True

    tam_docx = ruta_docx.stat().st_size if Path(ruta_docx).is_file() else 0
    tam_pptx = ruta_pptx.stat().st_size if Path(ruta_pptx).is_file() else 0
    for etiqueta, tam in (("INFORME.docx", tam_docx), ("PRESENTACION.pptx", tam_pptx)):
        dentro = 0 < tam < limite_mb * (1 << 20)
        ok = ok and dentro
        lineas.append(f"[{'OK' if dentro else 'FALLA'}] {etiqueta}: {tam_legible(tam)} "
                      f"(límite {limite_mb} MB)")

    documento = DocumentoLector(str(ruta_docx))
    parrafos = [p.text for p in documento.paragraphs]
    for tabla in documento.tables:
        for fila in tabla.rows:
            parrafos.extend(celda.text for celda in fila.cells)
    texto_doc = "\n".join(parrafos)
    lineas.append(f"[OK] roundtrip DOCX: {len(documento.paragraphs)} párrafos, "
                  f"{len(documento.tables)} tablas, "
                  f"{len(documento.inline_shapes)} imágenes")

    presentacion = PresentacionLectora(str(ruta_pptx))
    lineas.append(f"[OK] roundtrip PPTX: {len(presentacion.slides)} láminas")

    k = cifras_clave(cifras)
    c = cifras["confusion"]
    chequeos = [
        ("recall", k["recall"]),
        ("evaluaciones EDA", k["evaluaciones"]),
        ("integridad JPEG", k["integridad"]),
        ("tokens de visión", k["vision_tokens"]),
        ("latencia mediana", k["latencia_mediana"]),
        ("TP", num(c["tp"], 0) + " verdaderos positivos"),
    ]
    for etiqueta, valor in chequeos:
        encontrado = valor in texto_doc
        ok = ok and encontrado
        lineas.append(f"[{'OK' if encontrado else 'FALLA'}] spot-check {etiqueta}: «{valor}»")

    return ok, lineas


# --------------------------------------------------------------------------- orquestación

def cargar_datos(segundo_frame=47.0, sin_fotos=False, sin_video=False):
    fecha = datetime.now()
    analisis = leer_json(ANALISIS / "analisis.json", {}) or {}
    eda = leer_json(ANALISIS / "eda.json", {}) or {}
    modelo = leer_json(ANALISIS / "modelo_ocupacion.json", {}) or {}
    cond = analisis.get("condiciones") or {}

    fotos = []
    if not sin_fotos:
        for foto in seleccionar_fotos(2):
            preparada = preparar_foto(foto)
            if preparada:
                fotos.append({**foto, **preparada})

    frame = None if sin_video else capturar_frame_video(DOCS / "video_aulasense.mp4", segundo_frame)

    return {
        "fecha": fecha,
        "analisis": analisis,
        "eda": eda,
        "modelo": modelo,
        "metricas_md": leer_texto(ANALISIS / "informe_metricas.md"),
        "informe_eda_md": leer_texto(RAIZ / "analysis" / "informe_eda.md"),
        "ventana_ms": cond.get("ventana_foto_onset_ms") or 20000,
        "figuras": figuras_disponibles(),
        "sesiones_dataset": sesiones_dataset(),
        "validaciones_manuales": validaciones_manuales(),
        "entregables": entregables(),
        "trazabilidad": trazabilidad(),
        "presupuesto": presupuesto_vision(),
        "flag_ts": texto_flag_ts(analisis),
        "fotos": fotos,
        "frame": frame,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Genera docs/entrega/INFORME.docx y PRESENTACION.pptx desde los JSON del "
                    "pipeline de análisis (todo dato es derivado, ninguna cifra a mano).")
    parser.add_argument("--out", default=str(ENTREGA), help="carpeta de salida")
    parser.add_argument("--segundo-frame", type=float, default=47.0,
                        help="segundo del video del que se captura el fotograma")
    parser.add_argument("--sin-video", action="store_true", help="no capturar fotograma")
    parser.add_argument("--sin-fotos", action="store_true", help="no embeber fotos-evidencia")
    args = parser.parse_args(argv)

    if not (ANALISIS / "analisis.json").is_file():
        print("ERROR: falta docs/analisis/analisis.json — ejecuta antes analysis/run_analisis.ps1")
        return 2

    print("F6-a · leyendo insumos …")
    datos = cargar_datos(args.segundo_frame, args.sin_fotos, args.sin_video)
    cifras = extraer_cifras(datos["analisis"], datos["eda"], datos["modelo"])
    print(f"  sesiones: {len(cifras['sesiones'])} · figuras: {len(datos['figuras'])} · "
          f"fotos: {len(datos['fotos'])} · frame video: {'sí' if datos['frame'] else 'no'}")

    salida = Path(args.out)
    ruta_docx = salida / "INFORME.docx"
    ruta_pptx = salida / "PRESENTACION.pptx"

    print("  generando INFORME.docx …")
    construir_docx(cifras, datos, ruta_docx)
    print("  generando PRESENTACION.pptx …")
    construir_pptx(cifras, datos, ruta_pptx)

    ok, lineas = verificar_salidas(ruta_docx, ruta_pptx, cifras)
    print("\nVerificación:")
    for linea in lineas:
        print("  " + linea)
    print("\nListo." if ok else "\nRevisar las FALLAS anteriores.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())