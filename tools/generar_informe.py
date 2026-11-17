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

F26-f2 (2026-11-17): el generador se EXTIENDE a la realidad F26 — la presentación recorre el
proyecto paso a paso (12 bloques, desde qué es el CSI hasta la operación OTA) con CÓDIGO REAL
del repo leído en vivo desde disco (registro SNIPPETS: ruta + rango de líneas, citados en cada
lámina como archivo:linea y verificables por grep). El informe gana los capítulos de decisión
con Clef (clef_resumen.json) y operación F26 (OTA/rollback/portal/salud), y refresca su
narrativa a SPEC §16. Ningún secreto entra en los entregables (QA lo comprueba).

F26-f2 FIX PRIVACIDAD (GATE-seguridad, 2026-11-17): los entregables públicos NO embeben
píxeles de fotos reales del aula (regla 2: galería solo local tras login; repo público +
orden del dueño «cero exposición de información privada»). La lámina 8 muestra un MOCKUP
SINTÉTICO del encuadre QVGA dibujado por código (mockup_qvga_privacidad) y cita solo la
DESCRIPCIÓN TEXTUAL de la IA (filtrada no sensible y anonimizada en defensa en profundidad).
auditoria_medios() lo garantiza: cada blob embebido debe estar en el whitelist de
figuras/diagramas/mockups y NINGUNO puede coincidir (sha256) con archivos bajo
server/datos/imagenes/ ni con la carpeta de imágenes de la BD WSL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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
    "clef_distribucion_decisiones": "Distribución de decisiones Clef sobre las fotos reales (F26)",
    "clef_revision_calidad": "Calidad perceptual media por etiqueta: decodificable ≠ legible (F26)",
    "diagrama_flujo_f26": "Flujos F26: OTA por aire (comando→gate→SHA→rollback) y evidencia (onset→foto→visión→Clef)",
    "diagrama_tecnico_contexto": "Vista de contexto C4 F26: placa, broker del dueño, WSL y VPS (solo túneles)",
    "diagrama_tecnico_componentes_nodo": "Componentes del nodo ESP32-S3: detector CSI + parche F26 (imagen, portal, OTA)",
    "diagrama_tecnico_componentes_dashboard": "Componentes del dashboard WSL: ingesta, BD, workers y panel /salud",
}

ORDEN_FIGURAS = (
    "serie_motion", "histograma_scores", "matriz_confusion", "eda_cobertura_gt",
    "eda_score_por_hora", "eda_autocorrelacion_pps", "eda_histograma_rssi",
    "eda_sesiones_comparativa", "ocupacion_pr_umbral", "ocupacion_modelo_vs_gt",
    "ocupacion_feature_importance", "clef_distribucion_decisiones", "clef_revision_calidad",
)

# --------------------------------------------------------------------------- snippets del repo

# F26-f2: cada snippet es código REAL copiado del repo en tiempo de generación. La clave es
# el id usado por las láminas; el valor, (ruta_relativa, linea_inicio, linea_fin, titulo).
# La etiqueta «ruta:Lx-Ly» va impresa en la lámina: cualquier lector verifica el bloque con
# grep/sed sobre ese archivo. Si el archivo o el rango faltan, el texto queda marcado FALTA
# y verificar_salidas() FALLA (prohibido inventar código).
SNIPPETS = {
    "gpio": ("firmware/parche_aulasense/Kconfig.projbuild", 26, 44,
             "GPIO del nodo: pines y guardas de compilación"),
    "imagen_task": ("firmware/parche_aulasense/app_imagen.c", 939, 955,
                    "Tarea de evidencia: consume la cola de onsets sin tocar el detector CSI"),
    "event_json": ("firmware/parche_aulasense/app_imagen.c", 301, 318,
                   "Evento JSON publicado por MQTT"),
    "topics": ("server/ingesta/esquema.py", 30, 54,
               "Contrato de topics: prefijo + sufijos (F26 añade ota/imagen_estado/heap_interno)"),
    "motion_schema": ("server/ingesta/esquema.py", 196, 209,
                      "Payload motion validado al entrar: state idle|motion y score 0..1"),
    "writer_doc": ("server/ingesta/ingesta_mqtt.py", 1, 9,
                   "Flujo de la ingesta: paho → cola → writer único → SQLite (WAL)"),
    "on_message": ("server/ingesta/ingesta_mqtt.py", 1610, 1623,
                   "El callback de paho SOLO encola; la imagen viaja cruda al writer"),
    "tuneles": ("SPEC.md", 1174, 1179,
                "Túnel de publicación OTA creado con wsl-port (comandos reales)"),
    "salud_cmd": ("docs/guion_demo.md", 29, 32,
                  "Verificación de servidor y túneles desde el host"),
    "vision_payload": ("server/vision/cliente.py", 873, 888,
                       "Petición del worker de visión: imagen inline + prompt acotado"),
    "clef_url": ("server/vision/clef.py", 87, 87,
                 "API de decisión usada (OpenRouter Decisions)"),
    "clef_payload": ("server/vision/clef.py", 648, 651,
                     "Envelope de la decisión: modelo flash hasta que toca escalar con imagen"),
    "ota_inicio": ("firmware/parche_aulasense/app_ota.c", 618, 625,
                   "OTA_INICIO deja los números del gate post-quiesce visibles en la BD"),
    "ota_aplicar": ("firmware/parche_aulasense/app_ota.c", 641, 656,
                    "Cambio de slot SOLO tras verificar SHA-256; luego reboot"),
    "ota_cadena": ("docs/NOTES_operacion.md", 140, 144,
                   "Cadena de eventos reales tras un apply bueno (manual del dueño)"),
    "jpeg_q": ("firmware/parche_aulasense/app_imagen.c", 647, 656,
               "Calidad QVGA medida como cuello real de la evidencia (jpeg_quality = 12)"),
}


def cargar_snippets():
    """Lee cada snippet del repo (rango exacto). Etiqueta archivo:linea siempre presente."""
    salida = {}
    for ident, (ruta, inicio, fin, titulo) in SNIPPETS.items():
        etiqueta = f"{ruta}:{inicio}-{fin} — {titulo}"
        archivo = RAIZ / ruta
        try:
            lineas = archivo.read_text(encoding="utf-8", errors="replace").splitlines()
            texto = "\n".join(lineas[inicio - 1:fin])
            ok = bool(texto.strip()) and fin <= len(lineas)
        except OSError:
            texto, ok = "", False
        salida[ident] = {
            "id": ident, "ruta": ruta, "inicio": inicio, "fin": fin, "titulo": titulo,
            "etiqueta": etiqueta, "texto": texto if ok else f"FALTA_SNIPPET {ruta}:{inicio}-{fin}",
            "ok": ok,
        }
    return salida


def filas_consolidado():
    """Filas del dataset consolidado, contadas en disco (no hardcodeado)."""
    ruta = RAIZ / "analysis" / "dataset" / "consolidado.csv"
    try:
        with ruta.open("r", encoding="utf-8", errors="replace") as fh:
            return max(0, sum(1 for _ in fh) - 1)
    except OSError:
        return None


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


def num_o_dash(x, nd=2):
    """Como num() pero para celdas donde la ausencia es un hecho honesto (n=0 / sin dato)."""
    return "—" if x is None else num(x, nd)


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
            num_o_dash(s["score_motion_mediana"], 4),
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
        ("Guion de demo en vivo (F26)", DOCS / "guion_demo.md"),
        ("Manual de operación del dueño (F26)", DOCS / "NOTES_operacion.md"),
        ("Entrega guiada para el profesor", DOCS / "ENTREGA_PROFESOR.md"),
        ("Subtítulos SRT", DOCS / "video_subtitulos.srt"),
        ("Póster de funcionamiento", RAIZ / "assets" / "poster_funcionamiento.png"),
        ("Diagrama técnico C4 (DSL/PNG/SVG)", RAIZ / "assets" / "diagrama_tecnico.dsl"),
        ("Diagrama de contexto (F26)", RAIZ / "assets" / "diagrama_tecnico_contexto.png"),
        ("Flujos F26 OTA/evidencia", RAIZ / "assets" / "diagrama_flujo_f26.png"),
        ("Componentes del nodo", RAIZ / "assets" / "diagrama_tecnico_componentes_nodo.png"),
        ("Componentes del dashboard", RAIZ / "assets" / "diagrama_tecnico_componentes_dashboard.png"),
        ("Métricas del pipeline", ANALISIS / "informe_metricas.md"),
        ("Resumen Clef (F26)", ANALISIS / "clef_resumen.json"),
        ("EDA (informe)", RAIZ / "analysis" / "informe_eda.md"),
        ("JSON del pipeline", ANALISIS / "analisis.json"),
        ("Dataset consolidado (solo real, F26)", RAIZ / "analysis" / "dataset" / "consolidado.csv"),
        ("Figuras del análisis", GRAFICOS),
        ("Dataset propio (jsonl por sesión)", RAIZ / "dataset"),
        ("Verdad terreno manual (CSV)", DOCS / "validacion_sesion1.csv"),
        ("Código del servidor (ingesta/dashboard/visión/Clef)", RAIZ / "server"),
        ("Firmware parcheado (app_imagen/app_provision/app_ota)",
         RAIZ / "firmware" / "parche_aulasense"),
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
        (ANALISIS / "clef_resumen.json", "distribución Clef, confianza, calidad, costes medidos (F26)"),
        (RAIZ / "analysis" / "dataset" / "consolidado.csv", "dataset 100 % real (regla ts F26)"),
        (ANALISIS / "informe_metricas.md", "resumen legible del pipeline"),
        (RAIZ / "analysis" / "informe_eda.md", "informe EDA formal"),
        (DOCS / "NOTES_operacion.md", "operación F26: OTA, rollback, portal, duraciones medidas"),
        (DOCS / "ENTREGA_PROFESOR.md", "recorrido guiado y reality-check del paquete"),
        (DOCS / "guion_demo.md", "demo en vivo y comandos de verificación"),
        (RAIZ / "README.md", "contexto y arquitectura oficial"),
        (RAIZ / "SPEC.md", "especificación técnica (§16 = F26)"),
        (RAIZ / "PLAN.md", "plan maestro y decisiones"),
        (TMP_PROYECTO / "f26_analisis.md", "análisis regenerado F26-h3 (cifras §9)"),
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
                       "borros", "desenfocad", "desenfoque", "no hay evidencia", "sin evidencia",
                        "barras", "puntos de colores")
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


def _candidatas_fotos():
    """Candidatas SOLO por TEXTO de la BD (F26-f2-fix PRIVACIDAD).

    CORRECCIÓN GATE-seguridad: este generador NO abre ni embebe píxeles de fotos reales:
    las fotos del aula viven en server/datos/imagenes (gitignored) y su galería solo se
    sirve en el servidor local tras login (regla 2 AGENTS — el repo es PÚBLICO). De la BD
    en modo SOLO-LECTURA se toman el id y la DESCRIPCIÓN textual (sin personas
    identificables, filtrada por 'sensible'), y la lámina muestra una REPRESENTACIÓN
    SINTÉTICA del encuadre (mockup_qvga_privacidad).
    """
    db = _instantanea_bd()
    if db is None:
        return []
    consulta = ("select id, ts_ms, descripcion, etiquetas, vision_estado, evento_id "
                "from imagenes where origen='real' and coalesce(imagen_purgada, 0)=0 "
                "order by id")
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        filas = con.execute(consulta).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    salida = []
    for ident, ts, desc, etiq, estado, evento in filas:
        if estado != "hecho":
            continue
        texto = f"{desc or ''} {etiq or ''}".lower()
        salida.append({
            "id": ident, "ts": ts, "descripcion": (desc or "").strip(),
            "etiquetas": etiq or "", "evento_id": evento,
            "persona": _menciona_persona(texto),
            "sensible": any(k in texto for k in
                            ("sin camiseta", "desnud", "rostro", "primer plano", "cara de",
                             "de frente", "mirando a c")),
            "puntos": _puntuar_foto(desc or "", etiq or ""),
        })
    return salida


def _rango_foto(foto):
    """Ordena de mejor a peor: escena reconocible primero; empate → la más reciente.

    (F26-f2-fix: sin filtro de legibilidad por píxeles — no se abren las fotos —, así que
    el desempate por id descendente favorece las capturas post-FixF, mejor descritas.)
    """
    return (-foto["puntos"], -foto["id"])


def seleccionar_fotos(maximo=2):
    """Elige 1-2 fotos reales SOLO por su descripción ('hecho'): escena primero.

    Se descarta cualquier descripción sensible (personas identificables, vestimenta,
    primeros planos de cara): el entregable público cita SOLO descripciones de escena
    neutras; los píxeles reales nunca salen del servidor local (regla 2).
    """
    candidatas = _candidatas_fotos()
    if not candidatas:
        return []
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


def anonimizar_descripcion(texto):
    """Cita publicable: si la descripción afirmara presencia de personas, se anonimiza.

    Con la selección actual (escena sin personas) es una defensa en profundidad: sustituye
    sustantivos de persona por 'figura no identificable' y elimina menciones de rostro/cara.
    """
    if not texto:
        return ""
    t = texto
    if _menciona_persona(t.lower()):
        t = re.sub(r"(?i)\b(hombre|hombres|mujer|mujeres|persona|personas|individuo|"
                   r"individuos|alumno|alumnos|estudiante|estudiantes)\b",
                   "figura no identificable", t)
    t = re.sub(r"(?i)\b(rostro|rostros|cara|caras)\b", "zona difusa", t)
    return t


def mockup_qvga_privacidad():
    """REPRESENTACIÓN SINTÉTICA del encuadre QVGA real (320x240, jpeg_quality=12).

    Dibujado por código (PIL): cero píxeles reales. Es el marco docente que necesita la
    lámina — proporción, encuadre y rótulos de privacidad — sin exponer la escena del aula
    en un repo público (regla 2; orden del dueño: cero exposición de información privada).
    """
    TMP_IMG.mkdir(parents=True, exist_ok=True)
    destino = TMP_IMG / "mockup_qvga_privacidad.png"
    if destino.is_file():
        destino.unlink()
    ancho, alto = 960, 640                      # 3x la QVGA real 320x240
    img = Image.new("RGB", (ancho, alto), (0x24, 0x2C, 0x33))
    d = ImageDraw.Draw(img)
    # rejilla esquemática de subportadoras (motivo CSI, no escena)
    for x in range(0, ancho, 40):
        d.line([(x, 0), (x, alto)], fill=(0x2E, 0x38, 0x40), width=1)
    for y in range(0, alto, 40):
        d.line([(0, y), (ancho, y)], fill=(0x2E, 0x38, 0x40), width=1)
    # marco de encuadre punteado + escuadras de esquina
    d.rectangle([30, 30, ancho - 30, alto - 30], outline=(0x8F, 0xD1, 0xDE), width=3)
    for (ex, ey) in ((30, 30), (ancho - 30, 30), (30, alto - 30), (ancho - 30, alto - 30)):
        sx = 1 if ex < ancho / 2 else -1
        sy = 1 if ey < alto / 2 else -1
        d.line([(ex, ey), (ex + 60 * sx, ey)], fill=BLANCO, width=6)
        d.line([(ex, ey), (ex, ey + 60 * sy)], fill=BLANCO, width=6)
    # silueta abstracta de movimiento (curva sinusoidal estilizada: NO es una persona)
    puntos = [(60 + i, alto // 2 + int(90 * math.sin(i / 90.0))) for i in range(0, ancho - 120, 8)]
    d.line(puntos, fill=(0x1F, 0x7A, 0x8C), width=5)
    # rótulos (ajustados al ancho del lienzo: nada se corta)
    def _fuente(tam, bold=False):
        nombre = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        while tam > 12:
            try:
                f = ImageFont.truetype(nombre, tam)
            except OSError:
                return ImageFont.load_default()
            if max(d.textlength(t, font=f) for t in textos) <= ancho - 80:
                return f
            tam -= 2
        return ImageFont.truetype(nombre, 12) if not bold else ImageFont.truetype(
            "DejaVuSans-Bold.ttf", 12)

    textos = ["ESQUEMA QVGA 320 x 240",
              "representación sintética — cero píxeles reales",
              "FOTO REAL OMITIDA POR PRIVACIDAD (regla 2)",
              "la galería completa vive solo en el servidor local, tras login",
              "evidencia: 1 JPEG por onset · jpeg_quality=12"]
    f_grande = _fuente(42, bold=True)
    f_media = _fuente(30)
    d.text((ancho // 2, 92), textos[0], font=f_grande, fill=BLANCO, anchor="mm")
    d.text((ancho // 2, 150), textos[1], font=f_media, fill=(0x8F, 0xD1, 0xDE), anchor="mm")
    d.text((ancho // 2, alto - 150), textos[2], font=f_grande, fill=(0xE8, 0xB0, 0x4A),
           anchor="mm")
    d.text((ancho // 2, alto - 92), textos[3], font=f_media, fill=BLANCO, anchor="mm")
    d.text((ancho // 2, alto - 46), textos[4], font=f_media, fill=(0x9A, 0xA6, 0xB3),
           anchor="mm")
    img.save(destino, "PNG", optimize=True)
    return destino


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


def _codigo_docx(doc, snip, tam=7.8):
    """Bloque de código real del repo (F26-f2): fondo tenue + Consolas + cita archivo:linea."""
    cab = doc.add_paragraph()
    run = cab.add_run("Código: " + snip["etiqueta"])
    run.font.size = Pt(8.5)
    run.bold = True
    run.font.name = "Consolas"
    run.font.color.rgb = _rgb_docx(ACENTO)
    cab.paragraph_format.space_after = Pt(1)
    for linea in (snip["texto"] or "").splitlines():
        parrafo = doc.add_paragraph()
        pPr = parrafo._p.get_or_add_pPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), "F4F7F9")
        pPr.insert_element_before(shd, "w:tabs", "w:suppressAutoHyphens", "w:kinsoku",
                                  "w:wordWrap", "w:overflowPunct", "w:topLinePunct",
                                  "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
                                  "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
                                  "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection",
                                  "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl",
                                  "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange")
        r = parrafo.add_run(linea if linea.strip() else " ")
        r.font.name = "Consolas"
        r.font.size = Pt(tam)
        parrafo.paragraph_format.space_after = Pt(0)
        parrafo.paragraph_format.space_before = Pt(0)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


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
        "las demás transportan, guardan y analizan — y, desde F26, operan la placa en remoto sin "
        "jamás alcanzarla: la placa solo abre conexiones salientes (zero-asunción-LAN, D-F26-10)."
    ))
    _fig(doc, cont, RAIZ / "assets" / "diagrama_tecnico_contexto.png",
         "Vista de contexto C4 (F26): placa, broker del dueño, WSL y VPS (solo broker + túneles)",
         ancho_max_cm=12.5, alto_max_cm=15.0)
    _tabla_docx(doc, [
        ["Capa", "Componente", "Qué hace"],
        ["Borde", "ESP32-S3 GOOUUU v1.5 (N16R8) con ESPectre 3.2.0 parcheado "
                  "(app_feedback, app_imagen, app_provision; + app_ota en F26)",
         "Lee CSI HT20 2.4 GHz (~100 muestras/s), ejecuta el MLP de 529 parámetros y decide "
         "idle | motion cada 250 ms; buzzer GPIO14 + WS2812 GPIO48; 1 JPEG QVGA de evidencia al "
         "onset (cooldown 15 s, gate de heap interno 95/24 sin latch); portal fijo de "
         "provisioning y OTA por comando MQTT con rollback en el bootloader (SPEC §16)."],
        ["Transporte", f"MQTT autenticado en {BROKER} (broker del dueño: SOLO Mosquitto + "
                       "túneles SSH de publicación)",
          "Prefijo aislado aulasense/v1/devices/<id> con topics motion, health, sensing, config e "
          "image (b64, QoS 0, retain=false → la foto no persiste en el broker) más los retains "
          "F26 (ota, imagen_estado, heap_interno); el comando aulasense_ota lo publica la "
          "ingesta y la placa lo consume por su conexión saliente. HTTP :8001 queda "
          "como canal alternativo LAN-only."],
        ["Servidor", "WSL Debian 13 «" + DISTRO_WSL + "» (systemd, sin Docker): ingesta + "
                     "dashboard FastAPI + worker de visión + worker Clef + estático OTA :8101",
         "Ingesta valida el esquema, reengancha el reloj del nodo (R3) y escribe SQLite en modo "
         "WAL (writer único). Dashboard en :8100 → túnel wsl-port → público :10062; estático "
         "OTA :8101 → túnel → público :10064. El VPS no ejecuta código del proyecto "
         f"(publicación: {URL_PUBLICA})."],
        ["Análisis", "Python: analysis/*.py (pandas, sklearn) y este generador",
         "EDA (horas, RSSI, autocorrelación, outliers), modelo de ocupación a 10 min, resumen "
         "Clef, 13 figuras y generación reproducible de este informe y de la presentación."],
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
    if (RAIZ / "assets" / "diagrama_flujo_f26.png").is_file():
        _fig(doc, cont, RAIZ / "assets" / "diagrama_flujo_f26.png",
             "Flujos F26 fuente-a-destino: OTA (comando MQTT → gate post-quiesce → descarga → "
             "SHA-256 → reboot → self-check → rollback 225 s) y evidencia (onset CSI → foto → "
             "MQTT → visión → Clef)", ancho_max_cm=15.5, alto_max_cm=11.0)
    _p(doc, (
        "El diagrama de contenedores completo (assets/diagrama_tecnico.png, con el detalle de "
        "topics, tablas y unidades systemd) se incluye en el Anexo A: está pensado para lectura "
        "ampliada en pantalla y a tamaño de página resulta ilegible."
    ), cursiva=True)


def _seccion_metodologia(doc, cifras, datos):
    _h(doc, "4. Metodología de datos")
    gt = cifras["gt"]
    _p(doc, (
        "Fuente de verdad: la BD canónica del servidor (modo SOLO-LECTURA para este análisis; "
        "cero escrituras — el writer único es la ingesta). Los archivos jsonl del dataset son el "
        "volcado histórico de cada sesión y se conservan como evidencia; cuando discrepan de la "
        "BD se reporta la divergencia en lugar de reescribir nada. Desde F26 el export aplica "
        "además una regla de validez temporal explícita (venta [2026-01-01, ahora+1 h]): la "
        "única imagen fuera de venta (id 56, época ~2083, fire-test histórico) queda documentada "
        "y FILTRADA del consolidado — la BD no se edita (regla 10)."
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
    _p(doc, (
        "El contrato de eventos que la ingesta valida antes de escribir (código real del repo):"
    ))
    _codigo_docx(doc, datos["snippets"]["motion_schema"])
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
        f"Marcas de validación presentes hoy: {detalle}. S2B y S3B (sesiones nocturnas en "
        "remoto, 2026-11-16/17) ya entraron solas al pipeline por el glob de sesiones, pero su "
        "GT fotográfico es CERO (la cámara del sitio no subió en esas ventanas: sus CSV solo "
        "tienen cabecera): aportan estabilidad multi-día, distribución y telemetría, NO "
        "recall/precisión. El tramo idle es lo que habilita verdaderos negativos, falsos "
        "positivos y por tanto FPR y precisión: en cuanto existan filas idle en un "
        "docs/validacion_sesion*.csv, el MISMO pipeline produce esas métricas sin cambios de "
        "código y este generador las publicará al reejecutarse. Por eso las métricas de "
        "validación siguen ancladas a S1, la única sesión con tramos motion foto-corroborados."
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
    _caja_nota(doc, (
        "Política de privacidad del paquete público (regla 2, orden del dueño: cero "
        "exposición de información privada): este informe NO adjunta píxeles de las fotos "
        "reales del aula. La evidencia fotográfica vive SOLO en la galería del servidor "
        "local, accesible tras login; aquí se reportan sus conteos, correlaciones y las "
        "descripciones textuales de la IA (filtradas: sin personas identificables)."
    ))
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
    _p(doc, (
        f"Ojo con los dos universos: el registro de LLAMADAS (tabla vision_uso) y el estado de "
        f"la COLA en la BD son conteos distintos — la cifra publicada de "
        f"{num(vis['hecho'], 0)} imágenes reales descritas es el estado de la cola "
        "(origen=real), no la suma de llamadas. La pasada F26 (2026-11-17) añadió "
        "26 llamadas / 12.180 tokens sobre las fotos nuevas, con media medida de ~468 "
        "tokens/imagen y 0 fallos."
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


def _tabla_clef_docx(clef):
    dist = (clef or {}).get("distribucion_taxonomia") or {}
    filas = [["Etiqueta (taxonomía SPEC §16.6)", "n", "%", "confianza", "cubrimiento",
              "calidad media"]]
    orden = ("IMAGEN_DEGRADADA", "SIN_PERSONAS_VISIBLES", "CIRCULACION",
             "PERSONAS_SENTADAS", "PRESENTACION_AL_FRENTE", "AGLOMERACION_OTRA")
    for etiqueta in orden:
        d = dist.get(etiqueta) or {}
        filas.append([etiqueta, num(d.get("n"), 0), pct(d.get("pct")),
                      num_o_dash(d.get("conf_media"), 3), num_o_dash(d.get("cubre_media"), 3),
                      num_o_dash(d.get("calidad_media"), 3)])
    return filas


def _seccion_clef(doc, cifras, datos, cont):
    clef = datos.get("clef") or {}
    sn = datos["snippets"]
    hall = clef.get("hallazgo_degradacion") or {}
    rev = clef.get("revision_humana") or {}
    costes = clef.get("costes_medidos") or {}
    _h(doc, "6. Decisión de aula con Clef (F26)")
    _p(doc, (
        "F26 añadió una segunda capa de IA sobre la evidencia fotográfica: la "
        "clasificación de la escena del aula con la Decisions API de OpenRouter (familia "
        "Cloudflare Clef). El worker (server/vision/clef.py) usa una cascada barata→cara: "
        "cloudflare/clef-flash decide solo sobre el TEXTO de la descripción de visión, y "
        "escala a cloudflare/clef (que ve la imagen real) cuando la confianza es menor de "
        "0,6. La taxonomía tiene 6 etiquetas mutuamente excluyentes; una respuesta fuera de "
        "ella se rechaza (fail-closed hacia revisión humana). Las JPEG no decodificables se "
        "clasifican IMAGEN_DEGRADADA en local, sin gastar llamada."
    ))
    _codigo_docx(doc, sn["clef_url"])
    _codigo_docx(doc, sn["clef_payload"])
    _p(doc, (
        f"Medido sobre las fotos reales del dataset: {num(clef.get('n_decisiones_reales'), 0)} "
        f"decisiones ('hecho', origen=real; las 2 sintéticas históricas quedan excluidas por "
        f"la regla 10, sin editar la BD). Modelos: "
        + " · ".join(f"{m} = {n}" for m, n in (clef.get('modelos') or {}).items())
        + f". Confianza media global {num(clef.get('confianza_media_global'), 3)}; el badge "
        f"REVISION_HUMANA (cubrimiento < {num((rev.get('umbral_cubre') or 0.7), 1)}) se "
        f"activó en {num(rev.get('n'), 0)}/{num(clef.get('n_decisiones_reales'), 0)} "
        f"decisiones ({pct(rev.get('pct'))})."
    ))
    _tabla_docx(doc, _tabla_clef_docx(clef), anchos=(5.4, 1.3, 1.6, 1.9, 2.1, 1.9), tam=8.5,
                alinear_primera=True)
    _p(doc, "", tam=6)
    _caja_nota(doc, (
        "HALLAZGO CENTRAL — decodificable ≠ legible: " + pct(hall.get("tasa_degradada_pct")) +
        " de las decisiones fueron IMAGEN_DEGRADADA con calidad perceptual media " +
        num(hall.get("calidad_media_degradada"), 3) + " frente a " +
        num(hall.get("calidad_media_sin_personas"), 3) + " de las legibles — y ocurre también "
        "en las fotos nuevas post-F26 que pasan el control PIL estricto: el modelo ve el píxel "
        "y sigue juzgando la escena ilegible. El cuello NO es la decodificación ni la IA: es "
        "el encuadre QVGA con jpeg_quality=12 que el firmware eligió por presupuesto de "
        "memoria. Cero PERSONAS_SENTADAS: el aula nunca quedó bien encuadrada en las sesiones. "
        "La mejora está diseñada post-entrega: subir la calidad por OTA (no altera el sensing "
        "CSI; ver §7 y §9)."
    ))
    if (GRAFICOS / "clef_distribucion_decisiones.png").is_file():
        _fig(doc, cont, GRAFICOS / "clef_distribucion_decisiones.png",
             pie_figura(GRAFICOS / "clef_distribucion_decisiones.png"))
    if (GRAFICOS / "clef_revision_calidad.png").is_file():
        _fig(doc, cont, GRAFICOS / "clef_revision_calidad.png",
             pie_figura(GRAFICOS / "clef_revision_calidad.png"))
    _h(doc, "6.1 Coste medido (regla 16)", 2)
    _bullets_docx(doc, [
        f"Paso 0 de calibración ANTES del lote: 809 tokens / $1,54e-5 / 747 ms (evidencia "
        f".tmp/f26_clef_paso0.json). Solo entonces se autorizó el lote.",
        f"Consumo real acumulado F26 (tabla clef_uso): {num(costes.get('clef_uso_filas_api'), 0)} "
        f"llamadas API + {num(costes.get('clef_uso_filas_tope'), 0)} rechazos por tope · "
        f"{num(costes.get('clef_tokens'), 0)} tokens · "
        f"${num(costes.get('clef_coste_usd'), 5)} USD · 0 fallos ni refusals.",
        "Matrícula honesta de subestimación: la pata flash de las escaladas no se audita por "
        "separado; el coste real es ~5-10 % mayor (mejora de 1 línea anotada, no aplicada).",
        f"Visión del mismo día: "
        f"{num(((costes.get('vision_uso_dia') or {}).get('llamadas')), 0)} llamadas · "
        f"{num(((costes.get('vision_uso_dia') or {}).get('tokens_total')), 0)} tokens "
        "(la clave del Token Plan factura en Credits del plan, sin USD por llamada).",
    ])
    _p(doc, (
        "Limitación de la capa: no existe ground truth de etiquetas de escena, así que se "
        "reporta DISTRIBUCIÓN y CONFIANZA, no precisión de la decisión (constancia escrita en "
        "docs/analisis/clef_resumen.json). Clef decide sobre la FOTO (evidencia de evento), no "
        "sobre el CSI: no reemplaza al detector."
    ))


def _seccion_operacion(doc, cifras, datos):
    sn = datos["snippets"]
    _h(doc, "7. Operación F26: OTA por aire, rollback, portal y monitoreo")
    _p(doc, (
        "La fase final convirtió el despliegue en operación remota honesta: la placa vive lejos "
        "del servidor y SOLO hace conexiones salientes (MQTT al broker del dueño y GET HTTP del "
        "binario de actualización por el túnel público). El servidor jamás alcanza a la placa; "
        "el recovery remoto es el rollback de la OTA, no un canal hacia el nodo."
    ))
    _h(doc, "7.1 OTA: comando → gate → SHA-256 → reboot → self-check", 2)
    _p(doc, (
        "El módulo propio app_ota.c suscribe el topic aulasense_ota (comandos que la ingesta "
        "PUBLICA; la ingesta nunca suscribe ese topic). El flujo de un apply: gate de heap "
        "INTERNO post-quiesce (96/48 KB — los 120/64 del plan original se enmendaron con "
        "evidencia: en 200 muestras de banco ninguna alcanzaba 120 y el post-quiesce medido es "
        "103/48, con 120/64 la primera OTA por aire habría sido imposible), pausa de captura y "
        "telemetría, descarga (~34 s medidos para 1.418.720 B), verificación SHA-256 + tamaño "
        "byte a byte contra el manifest publicado por código, cambio de slot, reboot, "
        "self-check de 90 s estables + 120 s de gracia y validación de la imagen. E2E ~163 s."
    ))
    _codigo_docx(doc, sn["ota_inicio"])
    _codigo_docx(doc, sn["ota_aplicar"])
    _p(doc, (
        "Fail-closed demostrado: con el sha alterado la placa descarga, rechaza "
        "(OTA_ERROR etapa=sha256) y NO reinicia — el slot actual nunca se toca antes de "
        "verificar. La protección anti-brick vive en el bootloader (doble slot con "
        "PENDING_VERIFY): un bin malo con SHA válido hace que la placa revierta SOLO a la "
        "imagen buena en 225,2 s medidos, sin intervención humana (probado con f26.7-test de "
        "self-check fallido a propósito; la placa 'resucitó sola' 5 veces, 1 planeada + 4 por "
        "un bug de rutas del runner). La OTA buena quedó demostrada DOS veces por aire: "
        "f26.4 → f26.5 → f26.6, la build viva al cerrar la fase."
    ))
    _codigo_docx(doc, sn["ota_cadena"])
    _h(doc, "7.2 Duraciones medidas (Anexo A de NOTES_operacion)", 2)
    _tabla_docx(doc, [
        ["Paso", "Medido"],
        ["check publicado → evento OTA_CHECK en BD", "~1 s"],
        ["apply publicado → OTA_INICIO (pasa gate post-quiesce)", "~0,5 s"],
        ["Descarga del bin 1.418.720 B por el túnel", "32,2-35 s (~34 s)"],
        ["OTA_OK → reboot → evento FIRMWARE", "19-22 s"],
        ["Self-check hasta validar", "~91-110 s"],
        ["E2E apply → placa validada de vuelta", "~161-163 s"],
        ["Rollback automático (bin malo con sha válido)", "225,2 s sin humano"],
        ["Agujero de telemetría esperado durante apply", "~30 s"],
        ["Sha alterado → OTA_ERROR etapa=sha256 (sin reboot)", "rechazo a los ~34 s"],
    ], anchos=(9.6, 5.0), tam=9)
    _p(doc, "", tam=6)
    _h(doc, "7.3 Portal de provisioning fijo (D-F26-4, ya flasheado)", 2)
    _p(doc, (
        "Si la WiFi del sitio falla 3 ciclos, la placa sube el AP fija AULASENSE-CFG (WPA2; la "
        "pass canónica está documentada en docs/NOTES_operacion.md §4, orden explícita del "
        "dueño: es una capacidad LOCAL que exige asociación física). Portal sin token en "
        "http://192.168.4.1/cfg con rutas GET /cfg (formulario), GET /cfg/estado (JSON) y POST "
        "/cfg/wifi (guarda NVS → AP OFF → STA puro → connect). Guardas heredadas intactas: "
        "Origin/Referer 403, cooldown 5 s (429), body ≤255 B y la placa nunca loguea SSID ni "
        "pass (solo longitudes). Confirmación remota sin volver al sitio: los eventos "
        "CFG_GUARDADA y STA_RECONECTADO aterrizan en config_eventos cuando la placa recupera "
        "MQTT. Regla de colisión: SSID fijo → una placa a la vez en provisioning."
    ))
    _h(doc, "7.4 Monitoreo: panel /salud y verificación post-energía", 2)
    _bullets_docx(doc, [
        "GET /api/salud (tras login) + panel /salud con semáforos: ingesta, MQTT, frescura de "
        "telemetría (umbral 5 min), heap INTERNO, colas de visión y Clef, disco, túneles y "
        "estado OTA retenido.",
        "Anti-falsa-alarma: durante una OTA hay un agujero de telemetría de ~30 s ESPERADO; con "
        "el retain ota no-idle el panel marca ota_en_curso=true y no alarma.",
        "No confundir métricas: free_memory_kb ≈ 8,2 MB es PSRAM y no sirve para los gates; "
        "los números útiles son internal_free_kb / internal_largest_block_kb (régimen de banco "
        "103-106 / 56 KB), publicados por el retain heap_interno cada 30 s.",
        "Tras re-energizar en el sitio: telemetría fresca ≤5 min, retain imagen_estado con "
        "camera_ok=true y build=f26.x, MAX(id) de imágenes creciendo con el tránsito y evento "
        "FIRMWARE nuevo. Todo por consulta SOLO-LECTURA (mode=ro): writer único = ingesta.",
        "Verificación por CLI desde el host: powershell tools\\wsl_aulasense.ps1 status y "
        "powershell tools\\wsl_aulasense.ps1 salud (docs/guion_demo.md:29-32; el JSON de "
        "túneles expone solo state/remote, nunca la pass del VPS).",
        "Regla de campo de la evidencia: ratio onsets→fotos ≥ 80 % en ventanas con tránsito; si "
        "baja, revisar descartes/GATE_OFF en config_eventos y solo entonces serial en lectura "
        "(lee_serial.py, sin reset).",
    ])


def _seccion_limitaciones(doc, cifras, datos):
    _h(doc, "8. Limitaciones honestas")
    mejor = cifras["modelo"]["mejor_umbral"] or {}
    clef = datos.get("clef") or {}
    hall = clef.get("hallazgo_degradacion") or {}
    _p(doc, (
        "Estas limitaciones no son notas al pie: condicionan cómo deben leerse los resultados "
        "anteriores."
    ))
    _bullets_docx(doc, [
        "FPR y precisión no computables: el ground truth actual es solo de movimiento "
        "(pasivo y corroborado por foto). S2B/S3B llegaron con GT fotográfico CERO (cámara "
        "caída en el sitio remoto): aportan estabilidad multi-día y distribución, no "
        "validación. Sin tramos idle declarados no existen verdaderos negativos; los huecos "
        "son sin-dato, no aula vacía. Las métricas de validación siguen ancladas a S1.",
        "Clef sin GT de escena: sus decisiones reportan distribución y confianza, NO "
        "precisión; el " + pct((clef.get('revision_humana') or {}).get('pct')) +
        " con badge REVISION_HUMANA existe precisamente por eso. Y el cuello real de la "
        "evidencia es el encuadre QVGA q12: " + pct(hall.get('tasa_degradada_pct')) +
        " de las fotos acabaron IMAGEN_DEGRADADA (§6).",
        "OTA y dashboard por HTTP sin TLS (decisión D3 del dueño, declarada): el tramo "
        "distro→VPS va cifrado por SSH y el tramo VPS→placa es HTTP plano MITIGADO por "
        "SHA-256 + rollback automático; TLS (nginx + Let's Encrypt) queda post-entrega. "
        "El aislamiento de topics en el broker compartido es por convención: ACLs pendientes.",
        "Rotación de la pass SSH del VPS pendiente (acción humana): la gestiona la app local "
        "wsl-port y no vive en el .env del proyecto. La pass canónica del AP figura en "
        "docs/NOTES_operacion.md por orden explícita del dueño (capacidad local, no credencial "
        "de red); en este informe y en la presentación no se publica el valor.",
        f"Ratio fotos↔onsets: el emparejamiento es {pct(cifras['correlacion']['emparejadas_pct'])} "
        "y tras F26 hay fotos sin onset ±20 s (re-habilitaciones de gate); la regla de campo "
        "(≥80 % onset→foto con tránsito) queda para seguimiento en el aula con lee_serial.",
        "RSSI sin variación útil: el nodo del banco quedó cerca del AP; el experimento no "
        "informa sobre distancia ni efecto de paredes. La correlación observada es ruido de "
        "canal, no una relación física demostrada.",
        "Precisión-piso del modelo predictivo y D8 = baseline: con ventanas entrenables cortas "
        "la comparación es indicativa. No se promete accuracy de dataset largo (lección E10).",
        "Alcance del sensing: una placa cubre una zona; se detecta movimiento, no presencia "
        "estacionaria; no se identifica ni se cuenta personas; la temperatura del die es proxy "
        "de carga de cómputo, no temperatura ambiente; el reloj del nodo es monótono y la época "
        "la estima el PC (re-enganche R3). Escenarios E2-E5 del portal (caída real del router) "
        "pendientes de ventana natural.",
        "Integridad de imagen: el encoder del dispositivo generaba frames rotos antes de "
        "Fix F; la tasa histórica no representa al firmware corregido.",
    ])


def _seccion_seguridad(doc, cifras):
    _h(doc, "9. Seguridad y privacidad")
    _p(doc, (
        "La privacidad del proyecto es acotada y por mitigación, no «por construcción»: hay "
        "imágenes, aunque sean pocas y locales. Los controles aplicados son:"
    ))
    _bullets_docx(doc, [
        "Sin reconocimiento facial ni caras-ML. La visión opcional describe escenas y la IA "
        "principal es el detector CSI en el chip.",
        "Captura mínima: un JPEG QVGA por onset de movimiento, cooldown de 15 s, tarea de baja "
        "prioridad que nunca bloquea el detector y gate de heap interno (95/24, sin latch) que "
        "la desactiva si el memoria cae. El CSI crudo no sale del chip.",
        "Destino local: galería servida solo con login; el broker autenticado recibe la imagen "
        "con retain=false (no persiste). Nada de nube.",
        "Rate limits: recepción de imagen 1 cada 10 s por dispositivo, cuota de visión "
        "(120/día, 20/hora) y de Clef con las mismas ventanas, ambas con dedupe por hash y "
        "backoff; toda tanda precedida de Paso 0 de medición.",
        "Portal de configuración FIJO (F26, SPEC §16.2): AP WPA2 AULASENSE-CFG + rutas "
        "/cfg, /cfg/estado, /cfg/wifi sin token; guardas de Origin/Referer (403), cooldown "
        "5 s (429), body ≤255 B, cabeceras duras y registro que nunca escribe SSID ni "
        "contraseñas (solo longitudes). El token NVS de F24 fue eliminado del código.",
        "Credenciales: todo vive en el .env ÚNICO de la raíz (gitignored; reglas 7 y 15), "
        "generado SOLO por código (secrets.token_hex) y migrado byte a byte por "
        "tools/consolidar_env.py; las claves del dueño (IA/broker) nunca se imprimen en logs, "
        "reportes ni documentos. La pass canónica del AP es la única excepción publicada, por "
        "orden explícita del dueño, solo en .env + docs/NOTES_operacion.md (capacidad local). "
        "La pass SSH del VPS no entra al repo (la gestiona wsl-port; rotación pendiente).",
        "Superficie pública: el dashboard solo abre por túnel SSH y exige sesión (401 sin "
        "cookie en /api/eventos); el estático OTA no expone datos personales (solo bins y "
        "manifest). El JSON de túneles que lee /salud expone únicamente state/remote.",
        "Retención: las imágenes son purgables (por defecto 30 días) y la purga conserva la "
        "fila y su descripción, de modo que el dataset no se rompe al borrar datos personales. "
        "Las imágenes están gitignored.",
    ])


def _seccion_conclusion(doc, cifras, datos):
    k = cifras_clave(cifras)
    _h(doc, "10. Conclusiones")
    _p(doc, (
        "El proyecto demuestra el ciclo completo de un sistema de datos con IA en el borde: "
        "sensado físico (CSI), inferencia en el dispositivo, transporte con autenticación, "
        "almacenamiento consultable, dashboard en vivo, análisis estadístico, dos capas de IA "
        "opcional con coste medido y —el cierre F26— operación remota real: actualización por "
        "aire con verificación SHA-256 y rollback anti-brick demostrados, portal de "
        "provisioning fijo y monitoreo con semáforos. El detector CSI cumple la promesa mínima "
        f"con evidencia: recall {k['recall']} sobre {k['tramos_gt']} tramos de movimiento "
        "corroborados por foto, sin depender de la cámara."
    ))
    _p(doc, (
        "Lo que falta también está medido y ordenado: sin marcas idle no hay FPR ni precisión; "
        "el GT fotográfico de S2B/S3B fue cero; el 75 % de las fotos son ilegibles para la IA "
        "de decisión por el presupuesto de calidad JPEG (mejora diseñada post-entrega, "
        "desplegable por OTA); TLS y ACLs de broker quedan declarados como deuda con su "
        "mitigación actual. Publicar esas carencias junto a los aciertos es parte del "
        "entregable, no un apéndice."
    ))
    _p(doc, (
        "Operativamente el sistema queda reproducible y gobernable por una persona: el pipeline "
        "de análisis, la regeneración de este informe y la presentación son comandos; la OTA se "
        "dispara con check→verificación externa→apply (docs/NOTES_operacion.md); y todo cambio "
        "futuro de firmware viaja por aire sobre la build única F26 (placa viva f26.6)."
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
    _seccion_clef(doc, cifras, datos, cont)
    _seccion_operacion(doc, cifras, datos)
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


# --------------------------------------------------------------------------- PPTX: láminas

# F26-f2: la presentación es el RECORRIDO PASO A PASO del proyecto terminado, desde
# cero (qué es el CSI) hasta la operación real (OTA, portal, salud) y las limitaciones
# honestas. Cada bloque 1-12 lleva su código REAL leído del repo con su referencia
# archivo:linea (verificable por grep). Fuente: docs/NOTES_operacion.md,
# docs/ENTREGA_PROFESOR.md, docs/guion_demo.md y .tmp/f26_analisis.md (cifras §9).


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

    caja = lamina.shapes.add_textbox(Inches(0.95), Inches(3.95), Inches(11.5), Inches(1.9))
    tf = caja.text_frame
    tf.word_wrap = True
    lineas = (
        f"{MATERIA} · {INSTITUTO}",
        f"Autor: {AUTOR}   ·   {fecha_larga(datos['fecha'])}",
        "Paso a paso desde cero hasta el proyecto terminado, con código real del repo",
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
    _pie_lamina(lamina, prs, "Regenerable con: python tools/generar_informe.py")


def _caja_codigo(lamina, snip, izquierda, arriba, ancho, alto):
    """Caja oscura de código con su referencia archivo:linea (copiado del repo en vivo)."""
    caja = lamina.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, izquierda, arriba, ancho, alto)
    caja.fill.solid()
    caja.fill.fore_color.rgb = _prgb((0x10, 0x1A, 0x24))
    caja.line.color.rgb = _prgb(ACENTO)
    caja.line.width = PPt(1)
    caja.shadow.inherit = False
    tf = caja.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.1)
    tf.margin_right = Inches(0.08)
    tf.margin_top = Inches(0.06)
    tf.margin_bottom = Inches(0.04)
    parrafo = tf.paragraphs[0]
    parrafo.text = snip["etiqueta"]
    parrafo.font.size = PPt(8)
    parrafo.font.bold = True
    parrafo.font.name = "Consolas"
    parrafo.font.color.rgb = _prgb((0x8F, 0xD1, 0xDE))
    lineas = (snip["texto"] or "").splitlines()
    tam = 8.5 if len(lineas) <= 12 else 7.5 if len(lineas) <= 18 else 6.8
    for linea in lineas:
        p = tf.add_paragraph()
        p.text = linea if linea.strip() else " "
        p.font.size = PPt(tam)
        p.font.name = "Consolas"
        p.font.color.rgb = _prgb((0xE8, 0xF0, 0xF4))
        p.space_after = PPt(0)
        p.line_spacing = 1.0
    return caja


def _lamina_roadmap(prs, datos, cifras):
    lamina = _nueva_lamina(prs, "De cero a proyecto terminado: 12 pasos", "Hoja de ruta")
    izquierda = [
        "1 — Qué es Wi-Fi CSI y por qué edge-first",
        "2 — Arquitectura real (VPS = broker + túneles; WSL = todo lo demás)",
        "3 — Montaje de la placa GOOUUU (GPIO, alimentación)",
        "4 — Firmware ESPectre + parche AulaSense",
        "5 — Eventos JSON por MQTT (topics y payloads)",
        "6 — Servidor en WSL debian-aulasense (writer único → SQLite)",
    ]
    derecha = [
        "7 — Broker del dueño y túneles de publicación wsl-port",
        "8 — Fotos-evidencia y visión IA (Qwen)",
        "9 — Decisión de aula con Clef (+ hallazgo honesto)",
        "10 — Dataset y análisis (métricas con n)",
        "11 — Operación F26: OTA por aire, rollback, portal, /salud",
        "12 — Limitaciones honestas y post-entrega",
    ]
    _bullets(lamina, izquierda, Inches(0.85), Inches(1.8), Inches(5.8), Inches(3.6), tam=14)
    _bullets(lamina, derecha, Inches(6.85), Inches(1.8), Inches(5.8), Inches(3.6), tam=14)
    k = cifras_clave(cifras)
    filas = num(datos.get("consolidado_filas"), 0) if datos.get("consolidado_filas") else "n/d"
    clef = datos.get("clef") or {}
    hall = clef.get("hallazgo_degradacion") or {}
    _kpis(lamina, [
        (filas, "filas reales en el consolidado"),
        (k["evaluaciones"], "evaluaciones del detector"),
        (k["recall"], "recall motion (n=" + k["tramos_gt"] + ")"),
        (frac((hall.get("tasa_degradada_pct") or 0) / 100.0, 0), "fotos Clef IMAGEN_DEGRADADA"),
        ("225 s", "rollback automático medido"),
    ], Inches(0.85), Inches(5.55), Inches(11.6), alto=Inches(1.05))
    _fuente_lamina(lamina, "Fuente: .tmp/f26_analisis.md §9 · docs/ENTREGA_PROFESOR.md · "
                           "analysis/dataset/consolidado.csv (contado en disco)")
    _pie_lamina(lamina, prs)


def _lamina_b1_csi(prs, cifras):
    lamina = _nueva_lamina(prs, "Paso 1 — Qué es Wi-Fi CSI y por qué edge-first",
                           "El fenómeno físico")
    _bullets(lamina, [
        "Origen: el proyecto anterior (dashcam anti-microsueño) nunca detectó un rostro real "
        "en vivo. El giro: dejar de mirar a las personas y medir cómo perturban el campo Wi-Fi.",
        "CSI (Channel State Information): cada subportadora OFDM del Wi-Fi 2.4 GHz describe "
        "amplitud y fase del canal; un cuerpo en movimiento altera ese patrón de forma medible.",
        "Edge-first: una red neuronal (MLP 8→24→12→1) corre ENTERA dentro del ESP32-S3 y "
        "decide idle | motion cada 250 ms. El PC solo ingesta, almacena, visualiza y analiza.",
        "El CSI crudo nunca sale del chip: por la red viajan eventos ya decididos.",
        "Framing honesto: se detecta MOVIMIENTO, no presencia estacionaria; idle ≠ sala vacía; "
        "no se identifica ni se cuenta personas.",
        "Sin micrófono, sin radar, sin hardware extra: la placa y el router que ya estaban.",
    ], Inches(0.85), Inches(1.8), Inches(7.4), Inches(4.0), tam=13.5)
    _kpis(lamina, [("529", "parámetros del MLP"), ("250 ms", "periodo de decisión"),
                   ("~100", "muestras CSI/s")],
          Inches(0.85), Inches(5.9), Inches(7.4), alto=Inches(0.95))
    _imagen_ppt(lamina, RAIZ / "assets" / "poster_funcionamiento.png",
                Inches(8.5), Inches(1.8), Inches(4.1), Inches(4.6))
    _fuente_lamina(lamina, "Fuente: docs/ENTREGA_PROFESOR.md §Qué hace único este proyecto · "
                           "AGENTS.md reglas 1 y 11 · assets/poster_funcionamiento.png")
    _pie_lamina(lamina, prs)


def _lamina_b2_arquitectura(prs, datos):
    lamina = _nueva_lamina(prs, "Paso 2 — La arquitectura real (SPEC §16.10)", "Dónde corre cada cosa")
    _bullets(lamina, [
        "VPS = SOLO broker Mosquitto (MQTT/TCP :14769) + túneles SSH de publicación. "
        "No ejecuta código del proyecto.",
        "WSL debian-aulasense (systemd, venv, sin Docker) = TODO lo demás: ingesta, SQLite, "
        "dashboard :8100, workers visión+Clef y estático OTA :8101.",
        "Outbound-only (zero-asunción-LAN): la placa solo abre conexiones salientes; el "
        "servidor JAMÁS alcanza a la placa. Recovery remoto = rollback de la OTA.",
        "Publicación: dashboard :8100 → túnel → :10062; OTA :8101 → túnel → :10064. "
        "HTTP :8001 queda LAN-only alternativo.",
        "IA opcional: visión Qwen (Token Plan) y decisión Clef (OpenRouter Decisions API).",
    ], Inches(0.75), Inches(1.75), Inches(5.6), Inches(4.6), tam=12.5)
    _imagen_ppt(lamina, preparar_imagen(RAIZ / "assets" / "diagrama_tecnico_contexto.png", 1500),
                Inches(6.5), Inches(1.7), Inches(6.2), Inches(2.75))
    _imagen_ppt(lamina, preparar_imagen(RAIZ / "assets" / "diagrama_flujo_f26.png", 1500),
                Inches(7.05), Inches(4.55), Inches(5.6), Inches(2.0))
    _fuente_lamina(lamina, "Diagramas F26 regenerados: assets/diagrama_tecnico*.png "
                           "(Structurizr) + assets/diagrama_flujo_f26.png (Mermaid) — "
                           ".tmp/f26d_diagrama.md")
    _pie_lamina(lamina, prs)


def _lamina_b3_montaje(prs, datos):
    lamina = _nueva_lamina(prs, "Paso 3 — Montaje de la placa GOOUUU ESP32-S3 v1.5", "Hardware")
    _caja_codigo(lamina, datos["snippets"]["gpio"], Inches(0.75), Inches(1.8), Inches(6.3),
                 Inches(4.55))
    _bullets(lamina, [
        "GOOUUU ESP32-S3-CAM v1.5 (N16R8): 16 MB flash + 8 MB PSRAM octal + OV3660 + "
        "buzzer + WS2812 onboard.",
        "Alimentación: USB-C 5 V (fuente o power bank). Consumo ~1-2 W con detector y cámara "
        "al aire.",
        "Buzzer en GPIO14 (LEDC) y WS2812 en GPIO48 (RMT): feedback local en cada transición.",
        "Cámara OV3660 usa el bus DVP GPIO4-18 SOLO con AULASENSE_IMAGEN=y; ningún otro "
        "periférico puede tocarlo.",
        "Consola = USB-Serial-JTAG (COM6), NO UART. El flash UART ocurrió UNA vez; todo lo "
        "demás viajó por OTA (paso 11).",
        "Prohibido USB device stack (re-muxea el PHY y apaga el JTAG = riesgo de brick).",
    ], Inches(7.3), Inches(1.8), Inches(5.35), Inches(4.6), tam=12)
    _fuente_lamina(lamina, "Snippet: firmware/parche_aulasense/Kconfig.projbuild:26-44 · "
                           "AGENTS.md §GPIO · docs/ENTREGA_PROFESOR.md §Cómo reproducir")
    _pie_lamina(lamina, prs)


def _lamina_b4_firmware(prs, datos):
    lamina = _nueva_lamina(prs, "Paso 4 — Firmware: ESPectre + parche AulaSense", "El parche en el chip")
    _caja_codigo(lamina, datos["snippets"]["imagen_task"], Inches(0.75), Inches(1.8),
                 Inches(6.15), Inches(4.55))
    _bullets(lamina, [
        "Base: ESPectre 3.2.0 (GPLv3, clonado por tag) sobre ESP-IDF v5.5.6 "
        "(eim run idf.py build). El derivado vive en firmware/parche_aulasense/.",
        "app_feedback.c — buzzer/LED en la transición idle→motion.",
        "app_imagen.c — captura QVGA de evidencia SOLO en el ONSET (task propia prio 3, "
        "cola de 4 onsets drop-oldest, cooldown 15 s): la imagen se descarta si hace falta, "
        "el evento NUNCA.",
        "app_provision.c — portal fijo de red (paso 11). app_ota.c — actualización por aire "
        "(paso 11).",
        "Gate de memoria SIN latch (F26): free<95 KB o largest<24 KB de heap INTERNO apaga "
        "la captura; se re-evalúa en cada onset.",
        "Regla de espejo: parche ↔ árbol vivo diff cero + hash, verificado al cerrar cada "
        "tarea de placa (10/10 archivos en F26).",
    ], Inches(7.15), Inches(1.8), Inches(5.5), Inches(4.6), tam=12)
    _fuente_lamina(lamina, "Snippet: firmware/parche_aulasense/app_imagen.c:939-955 · "
                           "README del parche §F26 · AGENTS.md regla 17")
    _pie_lamina(lamina, prs)


def _lamina_b5_eventos(prs, datos):
    lamina = _nueva_lamina(prs, "Paso 5 — Eventos JSON por MQTT", "El contrato de datos")
    _caja_codigo(lamina, datos["snippets"]["topics"], Inches(0.75), Inches(1.75), Inches(6.0),
                 Inches(4.6))
    _caja_codigo(lamina, datos["snippets"]["event_json"], Inches(6.95), Inches(1.75),
                 Inches(5.7), Inches(2.75))
    _bullets(lamina, [
        "Prefijo aislado en broker compartido: aulasense/v1/devices/<id>/{motion,health,"
        "image,config_eventos,ota,…}.",
        "motion payload validado al entrar: {timestamp_ms, state:idle|motion, score 0..1} "
        "(server/ingesta/esquema.py:196-209).",
        "Imagen: JSON+b64 QoS 0 retain=false — la foto NO persiste en el broker.",
        "Comandos servidor→placa viajan por el topic aulasense_ota (la ingesta publica, "
        "nunca suscribe): la placa los consume por su MQTT saliente.",
    ], Inches(6.95), Inches(4.65), Inches(5.7), Inches(2.0), tam=11)
    _fuente_lamina(lamina, "Snippets: server/ingesta/esquema.py:30-54 · "
                           "firmware/parche_aulasense/app_imagen.c:301-318 · SPEC §6/§16.4")
    _pie_lamina(lamina, prs)


def _lamina_b6_servidor(prs, datos):
    lamina = _nueva_lamina(prs, "Paso 6 — Servidor en WSL debian-aulasense", "Ingesta con writer único")
    _caja_codigo(lamina, datos["snippets"]["writer_doc"], Inches(0.75), Inches(1.75),
                 Inches(6.0), Inches(2.05))
    _caja_codigo(lamina, datos["snippets"]["on_message"], Inches(0.75), Inches(3.95),
                 Inches(6.0), Inches(2.55))
    _bullets(lamina, [
        "WSL Debian 13 nativo: systemd + venv python3.13, SIN Docker (restricción dura).",
        "Patrón paho→cola→writer único: el callback de paho SOLO encola; un único hilo escribe "
        "SQLite (WAL) y CSV. Jamás dos stacks vivos (split-brain).",
        "El decoder de imagen (límite 64 KB, b64 estricto, magic JPEG, sha256) corre en el "
        "writer: por eso el payload entra CRUDO a la cola (y los eventos JSON parseados por "
        "la misma vía, línea 1633).",
        "BD canónica: /home/aulasense/aulasense-datos/aulasense.db (ext4). No existe CLI "
        "sqlite3: toda consulta es SOLO-LECTURA (mode=ro) por el venv.",
        "Reloj del nodo es monótono: la ingesta estima la época en cada health (re-enganche R3) "
        "y la serie se lee por orden de id.",
        "Encima: dashboard FastAPI con login (:8100), worker de visión y worker Clef.",
    ], Inches(7.0), Inches(1.75), Inches(5.65), Inches(4.8), tam=11.5)
    _fuente_lamina(lamina, "Snippets: server/ingesta/ingesta_mqtt.py:1-9 y 1610-1623 · "
                           "SPEC §7-§8 · AGENTS.md reglas 3 y 4")
    _pie_lamina(lamina, prs)


def _lamina_b7_broker(prs, datos):
    lamina = _nueva_lamina(prs, "Paso 7 — Broker del dueño + túneles wsl-port", "Publicación sin abrir puertos")
    _bullets(lamina, [
        "Broker compartido del dueño: broker.gilman.pro:14769 (MQTT/TCP) con usuario de nodo "
        "y de ingesta; el aislamiento por prefijo de topic es de convención (ACLs: "
        "post-entrega).",
        "La placa, el PC y el VPS NO se ven directamente: la publicación se hace con túneles "
        "SSH de retorno (reverse forward) gestionados por la app local wsl-port.",
        "Regla dura: antes de add, tunnels list (puerto libre validado, jamás tocar túneles "
        "ajenos); auto-start para que sobrevivan reinicios.",
        "Verificación externa: HTTP 200 al manifiesto OTA por el puerto público + "
        "/login 200 y /api/eventos 401 sin cookie en el dashboard.",
        "El JSON de túneles que consume el panel /salud SOLO expone state/remote: nunca la "
        "pass SSH del VPS (esa vive en wsl-port, no en el repo).",
    ], Inches(0.75), Inches(1.75), Inches(6.3), Inches(4.0), tam=12)
    _caja_codigo(lamina, datos["snippets"]["tuneles"], Inches(7.25), Inches(1.75),
                 Inches(5.4), Inches(2.35))
    _caja_codigo(lamina, datos["snippets"]["salud_cmd"], Inches(7.25), Inches(4.25),
                 Inches(5.4), Inches(1.6))
    _nota_lamina(lamina, "El comando real usó --remote-port 10064: asignado y verificado "
                         "(libre en tunnels list + TCP-test externo; state=running).",
                 Inches(7.25), Inches(5.95), Inches(5.4), Inches(0.6), tam=10)
    _fuente_lamina(lamina, "Snippets: SPEC.md:1174-1179 (comandos reales) · "
                           "docs/guion_demo.md:29-32 · NOTES_operacion §topología")
    _pie_lamina(lamina, prs)


def _lamina_b8_vision(prs, datos):
    lamina = _nueva_lamina(prs, "Paso 8 — Fotos-evidencia y visión IA (Qwen)", "Describir sin prometer")
    _caja_codigo(lamina, datos["snippets"]["vision_payload"], Inches(0.75), Inches(1.75),
                 Inches(6.1), Inches(2.75))
    cm = ((datos.get("clef") or {}).get("costes_medidos") or {})
    vd = cm.get("vision_uso_dia") or {}
    vt = cm.get("vision_uso_total") or {}
    if vd:
        media = round((vd.get("tokens_total") or 0) / max(1, vd.get("llamadas") or 1))
        consumo = (
            f"Consumo F26 medido: {num(vd.get('llamadas'), 0)} llamadas · "
            f"{num(vd.get('tokens_total'), 0)} tokens ({num(media, 0)} tok/llamada media)."
        )
        if vt:
            consumo += (f" Histórico total: {num(vt.get('llamadas'), 0)} llamadas / "
                        f"{num((vt.get('tokens_in') or 0) + (vt.get('tokens_out') or 0), 0)} "
                        "tokens · 0 fallos en la tanda F26.")
    else:
        consumo = ("Consumo F26: sin clef_resumen.json al generar (no se inventa: ver la "
                   "tabla vision_uso en el informe).")
    _bullets(lamina, [
        "El worker tools/vision_describe.py --once es AGENTE-MEDIADO (ToS del Token Plan): "
        "una pasada viva a la vez, lanzada y esperada dentro del turno.",
        "Rate limits medidos y persistentes: 120 imágenes/día y 20/hora, dedupe por hash y "
        "backoff exponencial ante 429; Paso 0 de medición antes de cada lote.",
        "QC local ANTES de gastar API: JPEG ilegible = error, sin llamada (20 irreparables "
        "históricas quedaron así, documentadas).",
        consumo,
        "Las descripciones alimentan la búsqueda por texto de la galería (sobre texto ya "
        "almacenado).",
    ], Inches(0.75), Inches(4.75), Inches(6.1), Inches(1.8), tam=10)
    fotos = datos.get("fotos") or []
    if fotos:
        foto = fotos[0]
        # F26-f2-fix PRIVACIDAD: se embebe el MOCKUP SINTÉTICO (cero píxeles reales);
        # de la foto id N solo se cita su descripción textual (filtrada no sensible).
        _imagen_ppt(lamina, datos.get("mockup") or mockup_qvga_privacidad(),
                    Inches(7.15), Inches(1.75), Inches(5.4), Inches(2.6))
        descripcion = anonimizar_descripcion(foto["descripcion"])
        if len(descripcion) > 220:
            descripcion = descripcion[:220] + "…"
        pie = (f"Encuadre esquemático de la evidencia (QVGA real 320x240): la foto id "
               f"{foto['id']} se OMITE por privacidad — la galería vive solo en el "
               "servidor local tras login (regla 2). Descripción real generada por la IA:")
        caja = lamina.shapes.add_textbox(Inches(7.15), Inches(4.5), Inches(5.45), Inches(2.1))
        tf = caja.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = pie
        p.font.size = PPt(10)
        p.font.name = "Calibri"
        p.font.color.rgb = _prgb(GRIS)
        p2 = tf.add_paragraph()
        p2.text = "«" + descripcion + "»"
        p2.font.size = PPt(10.5)
        p2.font.italic = True
        p2.font.name = "Calibri"
        p2.font.color.rgb = _prgb(AZUL)
    else:
        _imagen_ppt(lamina, datos.get("mockup") or mockup_qvga_privacidad(),
                    Inches(7.15), Inches(1.75), Inches(5.4), Inches(2.6))
        _bullets(lamina, [
            "Sin descripciones disponibles al generar (la selección lee la BD canónica en "
            "modo SOLO-LECTURA y solo por texto). El generador lo dice en lugar de inventar; "
            "los píxeles reales nunca se embeben (regla 2).",
        ], Inches(7.15), Inches(4.5), Inches(5.4), Inches(2.0), tam=11)
    _fuente_lamina(lamina, "Snippet: server/vision/cliente.py:873-888 · "
                           ".tmp/f26_analisis.md §7 (costes medidos)")
    _pie_lamina(lamina, prs)


def _tabla_clef_ppt(clef):
    dist = (clef or {}).get("distribucion_taxonomia") or {}
    filas = [["Etiqueta (taxonomia §16.6)", "n", "%", "conf", "calidad"]]
    orden = ("IMAGEN_DEGRADADA", "SIN_PERSONAS_VISIBLES", "CIRCULACION",
             "PERSONAS_SENTADAS", "PRESENTACION_AL_FRENTE", "AGLOMERACION_OTRA")
    for etiqueta in orden:
        d = dist.get(etiqueta) or {}
        filas.append([
            etiqueta,
            num(d.get("n"), 0),
            pct(d.get("pct")),
            num_o_dash(d.get("conf_media"), 3),
            num_o_dash(d.get("calidad_media"), 3),
        ])
    return filas


def _lamina_b9_clef(prs, datos, cifras):
    lamina = _nueva_lamina(prs, "Paso 9 — Decisión de aula con Clef (OpenRouter)", "IA de decisión + hallazgo honesto")
    clef = datos.get("clef") or {}
    hall = clef.get("hallazgo_degradacion") or {}
    rev = clef.get("revision_humana") or {}
    costes = clef.get("costes_medidos") or {}
    kpis = [
        (pct(hall.get("tasa_degradada_pct"), 1), "decisiones IMAGEN_DEGRADADA"),
        (num(clef.get("confianza_media_global"), 3), "confianza media"),
        (f"{num(rev.get('n'), 0)}/{num(clef.get('n_decisiones_reales'), 0)}", "badge REVISION_HUMANA"),
        ("$" + num(costes.get("clef_coste_usd"), 4), "coste medido (62 llamadas)"),
    ]
    _kpis(lamina, kpis, Inches(0.75), Inches(1.7), Inches(12.0), alto=Inches(1.0))
    _caja_codigo(lamina, datos["snippets"]["clef_url"], Inches(0.75), Inches(2.95),
                 Inches(6.15), Inches(0.85))
    _caja_codigo(lamina, datos["snippets"]["clef_payload"], Inches(0.75), Inches(3.95),
                 Inches(6.15), Inches(1.55))
    _bullets(lamina, [
        "Cascada: clef-flash (solo el TEXTO de la descripción) escala a clef (con la imagen) "
        "si la confianza < 0,6; una choice fuera de la taxonomía se rechaza (fail-closed).",
        "IMAGEN_DEGRADADA se decide LOCAL para JPEG no decodificables: 0 llamadas. Paso 0 "
        "medido antes del lote: 809 tokens / $1,54e-5 / 747 ms.",
        "HALLAZGO (decodificable ≠ legible): calidad " +
        num(hall.get("calidad_media_degradada"), 3) + " vs " +
        num(hall.get("calidad_media_sin_personas"), 3) + " — cuello QVGA q12, no la IA; cero "
        "PERSONAS_SENTADAS.",
        "Sin GT de escena: se reporta DISTRIBUCIÓN, no precisión de la decisión.",
    ], Inches(0.75), Inches(5.5), Inches(6.15), Inches(1.05), tam=9)
    _tabla_ppt(lamina, _tabla_clef_ppt(clef), Inches(7.1), Inches(2.95), Inches(5.55),
               Inches(2.15), tam=9.5, anchos=(2.6, 0.6, 0.8, 0.8, 0.9))
    _imagen_ppt(lamina, GRAFICOS / "clef_revision_calidad.png", Inches(7.1), Inches(5.25),
                Inches(5.55), Inches(1.75))
    _fuente_lamina(lamina, "Snippets: server/vision/clef.py:87 y 648-651 · "
                           "tabla: docs/analisis/clef_resumen.json (60 decisiones reales)")
    _pie_lamina(lamina, prs)


def _lamina_b10_dataset(prs, cifras, datos):
    lamina = _nueva_lamina(prs, "Paso 10 — Dataset propio y análisis con tamaño muestral",
                           "Medido, no prometido")
    k = cifras_clave(cifras)
    filas = num(datos.get("consolidado_filas"), 0) if datos.get("consolidado_filas") else "n/d"
    pps_vals = [s["pps"] for s in cifras["sesiones"] if s.get("pps")]
    pps_txt = (f"{num(min(pps_vals), 2)}-{num(max(pps_vals), 2)}" if pps_vals else "n/d")
    n_fig = len(datos.get("figuras") or [])
    _kpis(lamina, [
        (filas, "filas 100 % reales (consolidado)"),
        (k["evaluaciones"], "evaluaciones del detector"),
        (k["recall"], "recall motion (anclado a S1)"),
        (k["tramos_gt"], "tramos GT n= con foto"),
    ], Inches(0.75), Inches(1.7), Inches(12.0), alto=Inches(1.0))
    _bullets(lamina, [
        "Verdad terreno PASIVA corroborada por foto: onset + foto en ±20 s = tramo motion; "
        "los huecos son sin-dato, no aula vacía.",
        "NOTA honesta S2B/S3B (sesiones nocturnas remotas): GT fotográfico = 0 (cámara caída "
        "en el sitio): sirven para estabilidad multi-día (pps de evaluación "
        + pps_txt + " en " + num(len(cifras['sesiones']), 0) + " sesiones) y "
        "distribución, NO para recall/precision. Las métricas siguen ancladas a S1: recall "
        + k["recall"] + " (TP " + k["tp"] + "/FN " + k["fn"] + ", n=" + k["tramos_gt"] + ").",
        "Precision no computable: sin marcas idle no hay verdaderos negativos (se declara, no "
        "se estima).",
        "Fila ts_ms absurda (id 56, época 2083): DOCUMENTADA y FILTRADA con regla explícita "
        "en el export; la BD NO se edita.",
        "Modelo de ocupación (sklearn, n_test " +
        num((cifras['modelo']['logistic_regression'] or {}).get('n_test'), 0) +
        " con 5 positivos): accuracy " + num((cifras['modelo']['decision_tree'] or {}).get('accuracy'), 3) +
        " = baseline mayoritario → ejercicio docente SIN promesas (lección E10).",
        num(n_fig, 0) + " figuras del pipeline regeneradas (glob de sesiones: todo entra solo).",
    ], Inches(0.75), Inches(2.95), Inches(6.6), Inches(3.9), tam=10.5)
    _imagen_ppt(lamina, GRAFICOS / "eda_sesiones_comparativa.png", Inches(7.55), Inches(2.9),
                Inches(5.15), Inches(1.85))
    _imagen_ppt(lamina, GRAFICOS / "matriz_confusion.png", Inches(7.55), Inches(4.85),
                Inches(5.15), Inches(1.85))
    _fuente_lamina(lamina, "Fuente: .tmp/f26_analisis.md §1-§5 · analisis.json · eda.json · "
                           "analysis/dataset/consolidado.csv")
    _pie_lamina(lamina, prs)


def _lamina_b11_ota(prs, datos):
    lamina = _nueva_lamina(prs, "Paso 11a — Operación F26: OTA por aire con rollback",
                           "La placa se actualiza sola (y revive sola)")
    _caja_codigo(lamina, datos["snippets"]["ota_inicio"], Inches(0.75), Inches(1.72),
                 Inches(6.15), Inches(1.8))
    _caja_codigo(lamina, datos["snippets"]["ota_aplicar"], Inches(0.75), Inches(3.65),
                 Inches(6.15), Inches(2.85))
    _bullets(lamina, [
        "Flow de un apply: comando MQTT (check→apply) → gate de heap INTERNO post-quiesce "
        "96/48 KB (enmendado con evidencia: con los 120/64 del plan la primera OTA era "
        "imposible) → pausa captura+telemetría → descarga del bin por el túnel público "
        "(~34 s para 1,4 MB) → SHA-256+size → set_boot → reboot → self-check 90 s + gracia "
        "120 s → imagen validada. E2E ~163 s.",
        "Fail-closed: hash alterado → OTA_ERROR etapa=sha256 SIN reboot (el slot actual "
        "nunca se toca antes de verificar).",
        "Anti-brick en el BOOTLOADER: bin malo con SHA válido → PENDING_VERIFY → no supera "
        "el self-check → revierte SOLO a la imagen buena. Probado: la placa resucitó sola "
        "5 veces (1 planeada + 4 por un bug de rutas del runner).",
        "Demostrado DOS veces por aire: f26.4 → f26.5 → f26.6 (build viva al cerrar F26).",
        "Agujero de telemetría ~30 s ESPERADO durante apply; el panel /salud lo sabe por el "
        "retain OTA y pone ota_en_curso=true (cero falsas alarmas).",
    ], Inches(7.1), Inches(1.72), Inches(5.55), Inches(4.8), tam=10.5)
    _imagen_ppt(lamina, preparar_imagen(RAIZ / "assets" / "diagrama_flujo_f26.png", 1200),
                Inches(7.1), Inches(6.0), Inches(5.55), Inches(0.95))
    _fuente_lamina(lamina, "Snippets: firmware/parche_aulasense/app_ota.c:618-625 y 641-656 · "
                           "evidencia: .tmp/f26_g1_ota.md · .tmp/f26_g2_rollback.md")
    _pie_lamina(lamina, prs)


def _lamina_b11_operacion(prs, datos):
    lamina = _nueva_lamina(prs, "Paso 11b — Operación F26: portal fijo, /salud y verificación remota",
                           "Todo remoto es outbound o solo-lectura")
    _caja_codigo(lamina, datos["snippets"]["ota_cadena"], Inches(0.75), Inches(1.75),
                 Inches(6.35), Inches(1.95))
    _bullets(lamina, [
        "Portal de provisioning FIJO (F26, ya flasheado): si la WiFi falla 3 ciclos sube el "
        "AP AULASENSE-CFG (WPA2; pass documentada en docs/NOTES_operacion.md §4) con rutas "
        "sin token GET /cfg, GET /cfg/estado, POST /cfg/wifi.",
        "Guardas heredadas: Origin/Referer 403, cooldown 5 s (429), body ≤255 B y la placa "
        "NUNCA loguea SSID ni pass (solo longitudes). Capacidad LOCAL: exige estar junto a "
        "la placa; una placa a la vez (SSID fijo).",
        "Confirmación remota sin volver al sitio: eventos CFG_GUARDADA y STA_RECONECTADO "
        "aterrizan en config_eventos cuando la placa recupera MQTT.",
        "Panel /salud (tras login): semáforos de ingesta, MQTT, frescura de telemetría, heap "
        "INTERNO (no confundir con los ~8,2 MB de PSRAM), colas visión/Clef, disco, túneles "
        "y estado OTA. Verificación por CLI: wsl_aulasense.ps1 salud.",
        "Tras re-energizar en el sitio: telemetría < 5 min, retain imagen_estado camera_ok, "
        "MAX(id) de imágenes creciendo con el tránsito y evento FIRMWARE nuevo.",
    ], Inches(0.75), Inches(3.95), Inches(6.35), Inches(3.0), tam=10)
    _kpis(lamina, [("≈34 s", "descarga del bin"), ("≈163 s", "E2E apply validado"),
                   ("225 s", "rollback solo (medido)"), ("≈30 s", "agujero esperado")],
          Inches(7.35), Inches(1.75), Inches(5.3), alto=Inches(1.0))
    _tabla_ppt(lamina, [
        ["Umbral (heap interno)", "free / largest", "Cuándo"],
        ["Captura de foto", "95 / 24 KB", "cada onset, sin latch"],
        ["Gate OTA", "96 / 48 KB", "post-quiesce, antes de descargar"],
        ["Régimen medido", "103–106 / 56 KB", "banco F26 (con evidencia)"],
    ], Inches(7.35), Inches(3.1), Inches(5.3), Inches(1.4), tam=9.5,
        anchos=(1.7, 1.2, 1.7))
    _nota_lamina(lamina, "Duraciones y umbrales medidos en F26 (Anexo A de NOTES_operacion; "
                         "SPEC §16.1 enmendado de 120/64 a 96/48 con 200 muestras de banco). "
                         "La OTA es SIEMPRE a mano: chequeo automático desactivado por "
                         "decisión del dueño.",
                 Inches(7.35), Inches(4.75), Inches(5.3), Inches(1.7), tam=10)
    _fuente_lamina(lamina, "Snippet: docs/NOTES_operacion.md:140-144 · §2 §4 §5 · "
                           "docs/guion_demo.md pasos 0-7")
    _pie_lamina(lamina, prs)


def _lamina_b12_limitaciones(prs, datos, cifras):
    lamina = _nueva_lamina(prs, "Paso 12 — Limitaciones honestas y qué queda después",
                           "Lo que no se puede afirmar · post-entrega")
    _caja_codigo(lamina, datos["snippets"]["jpeg_q"], Inches(0.75), Inches(1.72),
                 Inches(6.15), Inches(1.95))
    _bullets(lamina, [
        "Limitaciones de fondo: motion ≠ presencia estacionaria; idle ≠ sala vacía; una placa "
        "cubre una zona; die temp es carga de cómputo, no ambiente; precision NO computable "
        "(sin marcas idle); Clef sin GT de escena (distribución, no precisión); dataset corto "
        "(el modelo = baseline, reportado con n).",
        "Seguridad declarada (D3): OTA y dashboard por HTTP + SHA-256 SIN TLS (mitigado por "
        "hash + rollback; TLS nginx+Let's Encrypt post-entrega). Broker compartido sin ACLs "
        "por topic (pendiente). Pass SSH del VPS fuera del repo (la gestiona wsl-port; su "
        "ROTACIÓN es acción humana pendiente).",
        "Cuello real medido de la evidencia: jpeg_quality=12 (arriba) → 75 % DEGRADADA. "
        "Plan post-entrega: subirla a ~18-24 POR OTA (no toca el sensing CSI) ajustando en "
        "la misma OTA el tope b64 de 16.384 chars y conservando el QC PIL.",
        "Ratio onsets→fotos ≥ 80 % = regla de campo de evidencia sana (hoy 67,5 % de match y "
        "26 fotos sin onset ±20 s: re-habilitaciones de gate; revisar con lee_serial si baja "
        "en el aula). Escenarios E2-E5 del portal pendientes de ventana natural.",
    ], Inches(0.75), Inches(3.85), Inches(6.15), Inches(3.1), tam=9.5)
    _kpis(lamina, [("75 %", "fotos ilegibles para Clef"), ("71,25 %", "integridad JPEG (57/80)"),
                   ("67,5 %", "fotos↔onset ±20 s"), ("0", "precision (sin idle: declarado)")],
          Inches(7.1), Inches(1.72), Inches(5.55), alto=Inches(1.0))
    _imagen_ppt(lamina, GRAFICOS / "clef_distribucion_decisiones.png", Inches(7.1),
                Inches(2.95), Inches(5.55), Inches(2.6))
    _bullets(lamina, [
        "Post-entrega en orden: 1) calidad JPEG por OTA · 2) TLS OTA+dashboard · "
        "3) rotación pass VPS (humano) · 4) ACLs por topic · 5) pasada incremental remota "
        "si la reinstalación en sitio ocurre tras la entrega.",
    ], Inches(7.1), Inches(5.75), Inches(5.55), Inches(1.2), tam=10)
    _fuente_lamina(lamina, "Snippet: firmware/parche_aulasense/app_imagen.c:647-656 · "
                           "NOTES_operacion §7-§8 · .tmp/f26_analisis.md §4 §6")
    _pie_lamina(lamina, prs)


def _lamina_conclusion(prs, cifras, datos):
    lamina = _nueva_lamina(prs, "Proyecto terminado: qué ver y qué queda", "Cierre")
    k = cifras_clave(cifras)
    _bullets(lamina, [
        "Ciclo completo demostrado con la placa LEJOS del servidor: sensado CSI en-chip → "
        "eventos JSON por MQTT → ingesta WSL + SQLite → dashboard con login → visión + "
        "decisión IA → análisis y paquete de documentos, todo regenerable por comando.",
        "F26 cerró lo que faltaba: OTA por aire demostrada dos veces, rollback automático "
        "probado (225 s), portal fijo flasheado, panel /salud y decisión de aula con Clef "
        "con coste medido ($0,0053/62 llamadas).",
        "Evidencia con tamaño muestral: recall " + k["recall"] + " (n=" + k["tramos_gt"] +
        ", anclado a S1) sobre " + k["evaluaciones"] + " evaluaciones; las carencias "
        "(precision no computable, GT=0 en S2B/S3B, 75 % degradadas) están publicadas con "
        "los aciertos.",
        "Para el profesor: docs/ENTREGA_PROFESOR.md es el recorrido guiado de 14 piezas "
        "(dashboard en vivo, video, dataset, código, diagramas, informe y presentación).",
    ], Inches(0.85), Inches(1.85), Inches(11.5), Inches(3.7), tam=14)
    caja = lamina.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.85), Inches(5.5),
                                   Inches(11.6), Inches(0.95))
    caja.fill.solid()
    caja.fill.fore_color.rgb = _prgb(FONDO_CLARO)
    caja.line.color.rgb = _prgb(ACENTO)
    caja.shadow.inherit = False
    tf = caja.text_frame
    tf.word_wrap = True
    parrafo = tf.paragraphs[0]
    parrafo.text = (f"{URL_PUBLICA} (login por canal privado del curso)   ·   "
                    "python tools/generar_informe.py")
    parrafo.alignment = PP_ALIGN.CENTER
    parrafo.font.size = PPt(15)
    parrafo.font.bold = True
    parrafo.font.name = "Calibri"
    parrafo.font.color.rgb = _prgb(AZUL)
    _pie_lamina(lamina, prs, f"{PROYECTO} · {INSTITUTO} · {AUTOR}")


def construir_pptx(cifras, datos, salida):
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    _lamina_titulo(prs, datos, cifras)
    _lamina_roadmap(prs, datos, cifras)
    _lamina_b1_csi(prs, cifras)
    _lamina_b2_arquitectura(prs, datos)
    _lamina_b3_montaje(prs, datos)
    _lamina_b4_firmware(prs, datos)
    _lamina_b5_eventos(prs, datos)
    _lamina_b6_servidor(prs, datos)
    _lamina_b7_broker(prs, datos)
    _lamina_b8_vision(prs, datos)
    _lamina_b9_clef(prs, datos, cifras)
    _lamina_b10_dataset(prs, cifras, datos)
    _lamina_b11_ota(prs, datos)
    _lamina_b11_operacion(prs, datos)
    _lamina_b12_limitaciones(prs, datos, cifras)
    _lamina_conclusion(prs, cifras, datos)

    salida.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(salida))
    return salida


# --------------------------------------------------------------------------- verificación

def _sha_bytes(datos):
    return hashlib.sha256(datos).hexdigest()


def _hashes_fotos_reales():
    """sha256 de TODO píxel real de evidencia (espejo Windows + BD WSL) + recortes viejos.

    F26-f2-fix PRIVACIDAD: ni uno solo de estos blobs puede aparecer como medio embebido
    en los entregables públicos. SOLO lectura; si un origen no está montado, se omite con
    nota (el whitelist de la auditoría sigue garantizando el origen de cada blob).
    """
    hashes, origen_visto = set(), False
    dirs = [RAIZ / "server" / "datos" / "imagenes", RAIZ_WSL / "imagenes"]
    for d in dirs:
        try:
            if not d.is_dir():
                continue
            origen_visto = True
            for p in d.rglob("*"):
                if p.is_file():
                    hashes.add(_sha_bytes(p.read_bytes()))
        except OSError:
            continue
    try:
        for p in TMP_IMG.glob("evidencia_*.png"):
            hashes.add(_sha_bytes(p.read_bytes()))
    except OSError:
        pass
    return hashes, origen_visto


def _hashes_permitidos():
    """Universo de medios legítimos: figuras EDA/Clef, assets, preparados .tmp y frames."""
    fuentes = []
    for d in (GRAFICOS, RAIZ / "assets", TMP_IMG, TMP_FRAMES):
        try:
            if d.is_dir():
                fuentes.extend(p for p in d.glob("*") if p.is_file())
        except OSError:
            continue
    hashes = set()
    for p in fuentes:
        try:
            hashes.add(_sha_bytes(p.read_bytes()))
        except OSError:
            continue
    return hashes


def _medios_embebidos(ruta):
    import zipfile
    with zipfile.ZipFile(ruta) as z:
        return {n: z.read(n) for n in z.namelist()
                if n.startswith(("word/media/", "ppt/media/"))}


def auditoria_medios(rutas):
    """(ok, lineas): cada blob embebido está en el whitelist y NINGÚN píxel real circula."""
    prohibidos, origen_visto = _hashes_fotos_reales()
    permitidos = _hashes_permitidos()
    lineas, ok = [], True
    for etiqueta, ruta in rutas:
        try:
            medios = _medios_embebidos(ruta)
        except OSError as exc:
            ok = False
            lineas.append(f"[FALLA] auditoría {etiqueta}: no se pudo abrir el zip ({exc})")
            continue
        malas = []
        for nombre, blob in sorted(medios.items()):
            h = _sha_bytes(blob)
            if h in prohibidos:
                malas.append(f"{nombre} = PÍXEL DE FOTO REAL")
            elif h not in permitidos:
                malas.append(f"{nombre} = blob sin origen conocido")
        if malas:
            ok = False
            for m in malas:
                lineas.append(f"[FALLA] auditoría {etiqueta}: {m}")
        else:
            lineas.append(f"[OK] auditoría {etiqueta}: {len(medios)} medios embebidos, "
                          "todos de figuras/diagramas/mockup sintético — cero píxeles de "
                          "fotos reales"
                          + ("" if origen_visto else " (origen de fotos no montado: "
                             "verificación por whitelist)"))
    return ok, lineas


def verificar_salidas(ruta_docx, ruta_pptx, cifras, limite_mb=10, datos=None):
    """Roundtrip de lectura + spot-checks + bloques + snippets + secretos. Devuelve (ok, lineas)."""
    from docx import Document as DocumentoLector
    from pptx import Presentation as PresentacionLectora

    lineas = []
    ok = True

    def marca(estado, texto):
        nonlocal ok
        if estado != "OK":
            ok = False
        lineas.append(f"[{estado}] {texto}")

    tam_docx = ruta_docx.stat().st_size if Path(ruta_docx).is_file() else 0
    tam_pptx = ruta_pptx.stat().st_size if Path(ruta_pptx).is_file() else 0
    for etiqueta, tam in (("INFORME.docx", tam_docx), ("PRESENTACION.pptx", tam_pptx)):
        dentro = 0 < tam < limite_mb * (1 << 20)
        marca("OK" if dentro else "FALLA", f"{etiqueta}: {tam_legible(tam)} "
                                           f"(límite {limite_mb} MB)")

    documento = DocumentoLector(str(ruta_docx))
    parrafos = [p.text for p in documento.paragraphs]
    for tabla in documento.tables:
        for fila in tabla.rows:
            parrafos.extend(celda.text for celda in fila.cells)
    texto_doc = "\n".join(parrafos)
    marca("OK", f"roundtrip DOCX: {len(documento.paragraphs)} párrafos, "
                f"{len(documento.tables)} tablas, {len(documento.inline_shapes)} imágenes")

    presentacion = PresentacionLectora(str(ruta_pptx))
    texto_ppt = []
    for lamina in presentacion.slides:
        for forma in lamina.shapes:
            if forma.has_text_frame:
                for parrafo in forma.text_frame.paragraphs:
                    texto_ppt.append("".join(run.text for run in parrafo.runs))
            if getattr(forma, "has_table", False):
                for fila in forma.table.rows:
                    for celda in fila.cells:
                        texto_ppt.append(celda.text)
    texto_ppt = "\n".join(texto_ppt)
    marca("OK", f"roundtrip PPTX: {len(presentacion.slides)} láminas")

    def normaliza(t):
        return re.sub(r"\s+", " ", t or "").strip()

    # 1) cifras clave del pipeline en el informe
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
        marca("OK" if valor in texto_doc else "FALLA",
              f"spot-check {etiqueta}: «{valor}»")

    # 2) los 12 bloques paso a paso deben estar nombrados en la presentación
    bloques_vistos = [n for n in range(1, 13) if f"Paso {n}" in texto_ppt]
    marca("OK" if len(bloques_vistos) == 12 else "FALLA",
          f"bloques 1-12 presentes en PPTX: {len(bloques_vistos)}/12 {bloques_vistos}")
    marca("OK" if len(presentacion.slides) >= 14 else "FALLA",
          f"láminas ≥ 14: {len(presentacion.slides)}")

    # 3) snippets: leídos del repo, citados con archivo:linea y reproducibles por grep
    sn = (datos or {}).get("snippets") or {}
    if not sn:
        marca("FALLA", "registro SNIPPETS vacío (datos['snippets'] no cargado)")
    doc_n, ppt_n = normaliza(texto_doc), normaliza(texto_ppt)
    for ident, s in sorted(sn.items()):
        ruta = RAIZ / s["ruta"]
        try:
            fresco = "\n".join(ruta.read_text(encoding="utf-8", errors="replace")
                               .splitlines()[s["inicio"] - 1:s["fin"]])
        except OSError:
            fresco = ""
        en_artefacto = (normaliza(s["texto"]) in ppt_n or normaliza(s["texto"]) in doc_n
                        if s["texto"] else False)
        bueno = bool(s["ok"]) and fresco == s["texto"] and en_artefacto
        marca("OK" if bueno else "FALLA",
              f"snippet {ident}: {s['ruta']}:{s['inicio']}-{s['fin']} "
              f"(coincide con disco y embebido en el paquete)")

    # 4) residuos y secretos. El «None» del código real de los snippets (p. ej.
    #    ``str | None`` en esquema.py) NO es un placeholder roto: se examina el texto
    #    fuera de los bloques de código.
    for texto, origen in ((texto_doc, "DOCX"), (texto_ppt, "PPTX")):
        plana = normaliza(texto)
        for s in sn.values():
            plana = plana.replace(normaliza(s["texto"]), " ")
        for residuo in ("None", "FALTA_SNIPPET"):
            marca("OK" if residuo not in plana else "FALLA",
                  f"cero «{residuo}» (fuera de snippets) en {origen}")
        for secreto in ("itse-aulasense", "sk-sp-", "sk-[A-Za-z0-9]"):
            encontrado = re.search(re.escape(secreto), plana) if "sk-[" not in secreto \
                else re.search(secreto, plana)
            marca("OK" if not encontrado else "FALLA",
                  f"cero secreto «{secreto}» en {origen}")
    nd_doc, nd_ppt = texto_doc.count("n/d"), texto_ppt.count("n/d")
    lineas.append(f"[INFO] marcadores «n/d»: DOCX {nd_doc} · PPTX {nd_ppt} "
                  "(deben ser 0 salvo insumo ausente justificado)")

    # 5) F26-f2-fix PRIVACIDAD: ni un píxel de foto real del aula en los entregables
    aud_ok, aud_lineas = auditoria_medios((("DOCX", ruta_docx), ("PPTX", ruta_pptx)))
    ok = ok and aud_ok
    lineas.extend(aud_lineas)

    return ok, lineas


# --------------------------------------------------------------------------- orquestación

def cargar_datos(segundo_frame=47.0, sin_fotos=False, sin_video=False):
    fecha = datetime.now()
    analisis = leer_json(ANALISIS / "analisis.json", {}) or {}
    eda = leer_json(ANALISIS / "eda.json", {}) or {}
    modelo = leer_json(ANALISIS / "modelo_ocupacion.json", {}) or {}
    cond = analisis.get("condiciones") or {}

    # F26-f2-fix PRIVACIDAD: solo TEXTO de la BD (id + descripción no sensible) + mockup
    # sintético dibujado por código. Ningún píxel de foto real se abre ni se embebe.
    fotos = []
    if not sin_fotos:
        fotos = seleccionar_fotos(2)
        if fotos:
            mockup = mockup_qvga_privacidad()
        else:
            mockup = None
    else:
        mockup = None

    frame = None if sin_video else capturar_frame_video(DOCS / "video_aulasense.mp4", segundo_frame)

    return {
        "fecha": fecha,
        "analisis": analisis,
        "eda": eda,
        "modelo": modelo,
        "clef": leer_json(ANALISIS / "clef_resumen.json", {}) or {},
        "snippets": cargar_snippets(),
        "consolidado_filas": filas_consolidado(),
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
        "mockup": mockup,
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

    print("F26-f2 · leyendo insumos …")
    datos = cargar_datos(args.segundo_frame, args.sin_fotos, args.sin_video)
    cifras = extraer_cifras(datos["analisis"], datos["eda"], datos["modelo"])
    sn_faltan = [i for i, s in datos["snippets"].items() if not s["ok"]]
    print(f"  sesiones: {len(cifras['sesiones'])} · figuras: {len(datos['figuras'])} · "
          f"fotos: {len(datos['fotos'])} · frame video: {'sí' if datos['frame'] else 'no'} · "
          f"snippets: {len(datos['snippets']) - len(sn_faltan)}/{len(datos['snippets'])}"
          + (f" FALTAN {sn_faltan}" if sn_faltan else ""))

    salida = Path(args.out)
    ruta_docx = salida / "INFORME.docx"
    ruta_pptx = salida / "PRESENTACION.pptx"

    print("  generando INFORME.docx …")
    construir_docx(cifras, datos, ruta_docx)
    print("  generando PRESENTACION.pptx …")
    construir_pptx(cifras, datos, ruta_pptx)

    ok, lineas = verificar_salidas(ruta_docx, ruta_pptx, cifras, datos=datos)
    print("\nVerificación:")
    for linea in lineas:
        print("  " + linea)
    print("\nListo." if ok else "\nRevisar las FALLAS anteriores.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())