# -*- coding: utf-8 -*-
"""Casos BORDE del pipeline F5a (analysis/analizar_dataset.py), revisa 2026-11-15:

- parser JSONL SIN linea de cabecera: el nombre de sesion cae al del archivo
  (``registro_<x>.jsonl`` -> ``x``), la ventana se reconstruye del ``resumen``
  (y es ``None`` si tampoco hay resumen); lineas en blanco toleradas;
- parser JSONL con linea CORRUPTA: comportamiento actual = revienta con
  ``JSONDecodeError`` y tumba todo el pipeline. Se fija aqui como contrato
  vigente; reportado como [MEDIO] BUG-PROD-F5A-2 (deberia aislar la linea mala
  con log honesto, los dumps pueden venir truncados de la API);
- CSV de validacion con FILAS IDLE HUERFANAS y malformadas: sesion vacia viaja
  tal cual (no cae al stem del archivo), filas con inicio/fin ilegibles se
  ignoran, mayusculas se normalizan, y el tramo idle SIN ninguna evaluacion
  (n_eval=0) cuenta como TN (diferente criterio que motion, que se excluye como
  "sin-eval"); se fija el comportamiento vigente y se reporta como [MENOR];
- DETERMINISMO del run (2 corridas de ``main()`` en modo ``--sin-wsl`` +
  ``--no-graficos`` sobre los mismos inputs): ``analisis.json`` identico salvo
  ``generado_utc``;
- [CRITICO] BUG-PROD-F5A-1 (ARREGLADO 2026-11-15): ``main()`` con ``--sin-wsl``
  lanzaba ``KeyError`` al escribir el informe (``escribir_informe`` pide
  ``latencias['eventos']['nota']`` y ``estadisticas_latencia`` no devolvia ``nota``
  cuando n=0 — SIEMPRE el caso en modo offline). Fix: el dict n=0 de
  ``estadisticas_latencia`` lleva ``nota`` honesta ("sin datos de latencia (n=0)").
  El xfail estricto se retiro: ``test_run_analisis_sin_wsl_completa_sin_excepcion``
  ahora es un test normal de regresion.

Fixtures SOLO bajo ``aulasense/.tmp/`` (regla 8) y BAJO RAIZ: el pipeline
resuelve las rutas de entrada con ``relative_to(RAIZ)`` y estallaria fuera de el.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]          # aulasense/
sys.path.insert(0, str(RAIZ / "analysis"))

import analizar_dataset as ad                        # noqa: E402

TMP = RAIZ / ".tmp" / "test_f5a_bordes"


def _dir_libre(nombre: str) -> Path:
    """Carpeta de prueba limpia bajo .tmp/ (regla 8; idempotente entre corridas)."""
    ruta = TMP / nombre
    if ruta.exists():
        shutil.rmtree(ruta)
    ruta.mkdir(parents=True)
    return ruta


def _jsonl(cabecera: bool, con_resumen: bool, extra: str = "") -> str:
    lineas = []
    if cabecera:
        lineas.append(json.dumps({"tipo": "cabecera", "sesion": "D1",
                                  "inicio_ms": 1000, "fin_ms": 9000}))
    lineas.append(json.dumps({"tipo": "gt", "inicio_ms": 1800, "fin_ms": 2200,
                              "fotos": [5]}))
    lineas.append(json.dumps({"tipo": "foto", "id": 5, "ts_ms": 2000}))
    if con_resumen:
        lineas.append(json.dumps({"tipo": "resumen", "inicio_ms": 900,
                                  "fin_ms": 2100}))
    return "\n".join(lineas) + "\n" + extra


def _montar_dataset(nombre: str, *, contenido: str | None = None,
                    sesion: str = "sesion_d1", archivo: str = "registro_d1") -> Path:
    base = _dir_libre(nombre)
    ds = base / "dataset"
    (ds / sesion).mkdir(parents=True)
    (ds / sesion / f"{archivo}.jsonl").write_text(
        contenido if contenido is not None else _jsonl(cabecera=True, con_resumen=True),
        encoding="utf-8")
    return base


def _csv_validacion(base: Path) -> Path:
    val = base / "validacion_d1.csv"
    val.write_text(
        "sesion,inicio_ms,fin_ms,verdad_terreno,hubo_movimiento_real,fuente,notas\n"
        "D1,1000,2000,motion,si,prueba,\n",
        encoding="utf-8")
    return val


# ------------------------------------------------------- parser JSONL sin cabecera
def test_jsonl_sin_cabecera_nombre_por_archivo_y_ventana_del_resumen():
    """Sin linea ``cabecera`` la sesion se nombra por el archivo y la ventana la
    aporta el ``resumen`` (ruta historica de los dumps reconstruidos)."""
    base = _dir_libre("sin_cabecera")
    ds = base / "dataset"
    (ds / "sesion_s9").mkdir(parents=True)
    (ds / "sesion_s9" / "registro_turno.jsonl").write_text(
        "\n"  # linea en blanco: tolerada
        + json.dumps({"tipo": "gt", "inicio_ms": 1000, "fin_ms": 2000, "fotos": [1]})
        + "\n"
        + json.dumps({"tipo": "foto", "id": 1, "ts_ms": 1500})
        + "\n"
        + json.dumps({"tipo": "resumen", "inicio_ms": 900, "fin_ms": 2100})
        + "\n",
        encoding="utf-8")
    sesiones = ad.cargar_registros(ds)
    assert list(sesiones) == ["turno"], "el stem 'registro_turno' debe dar 'turno'"
    s = sesiones["turno"]
    assert s["cabeceras"] == []
    assert s["ventana"] == {"inicio_ms": 900, "fin_ms": 2100}
    assert len(s["gt"]) == 1 and len(s["fotos"]) == 1


def test_jsonl_sin_cabecera_ni_resumen_ventana_nula():
    """Sin cabecera Y sin resumen la ventana queda ``None``: main() la filtra y no
    consulta esa sesion (las metricas salen solo del GT global)."""
    base = _dir_libre("sin_ventana")
    ds = base / "dataset"
    (ds / "sesion_s8").mkdir(parents=True)
    (ds / "sesion_s8" / "registro_sueltas.jsonl").write_text(
        json.dumps({"tipo": "foto", "id": 3, "ts_ms": 1500}) + "\n", encoding="utf-8")
    sesiones = ad.cargar_registros(ds)
    assert sesiones["sueltas"]["ventana"] == {"inicio_ms": None, "fin_ms": None}
    assert sesiones["sueltas"]["eventos"] == []


def test_jsonl_linea_corrupta_lanza_json_decode_error_contrato_vigente():
    """[MEDIO] BUG-PROD-F5A-2 — un dump con UNA linea rota revienta el pipeline
    ENTERO (json.loads sin proteccion en leer_jsonl). Se fija el contrato vigente;
    al endurecerse el parser, este test deberia invertirse a 'la linea mala se
    ignora con log y el resto carga'."""
    base = _dir_libre("linea_corrupta")
    ds = base / "dataset"
    (ds / "sesion_s7").mkdir(parents=True)
    (ds / "sesion_s7" / "registro_roto.jsonl").write_text(
        json.dumps({"tipo": "cabecera", "sesion": "S7"}) + "\n" + "{truncado\n",
        encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        ad.cargar_registros(ds)


# ------------------------------------------------- CSV de validacion: idle huerfana
def test_cargar_validacion_filas_malformadas_e_idle_huerfanas():
    """Filas con inicio/fin ilegibles se IGNORAN; ``MOTION`` mayusculo se
    normaliza; el idle de una sesion SIN registro jsonl (huerfana) es GT valido
    igual (el CSV es canonico); la columna sesion VACIA viaja como "" (NO cae al
    stem del archivo: el default de DictReader es la clave presente con '')."""
    base = _dir_libre("validacion_huerfana")
    csv = base / "validacion_bordes.csv"
    csv.write_text(
        "sesion,inicio_ms,fin_ms,verdad_terreno,hubo_movimiento_real,fuente,notas\n"
        "B1,1000,2000,MOTION,si,prueba,normaliza-mayusculas\n"
        "B1,3000,4000,idle,no,prueba,marca idle\n"
        "B1,abc,4000,motion,si,prueba,inicio no numerico se ignora\n"
        "B1,5000,,motion,si,prueba,fin ausente se ignora\n"
        "B1,7000,6000,motion,si,prueba,inicio>fin NO se filtra (vigente)\n"
        ",8000,9000,motion,si,prueba,sin sesion\n"
        "H2,10000,11000,idle,no,prueba,idle huerfana sin registro\n"
        "H2,12000,13000,,sin-dato,prueba,verdad vacio no es GT\n",
        encoding="utf-8")
    filas = ad.cargar_validacion([csv])
    by = {(f["sesion"], f["inicio_ms"], f["fin_ms"]): f["verdad_terreno"] for f in filas}
    assert by == {
        ("B1", 1000, 2000): "motion",   # normalizado a minusculas
        ("B1", 3000, 4000): "idle",
        ("B1", 7000, 6000): "motion",   # tramo invertido: vigente se conserva
        ("", 8000, 9000): "motion",     # sesion vacia viaja literal
        ("H2", 10000, 11000): "idle",   # huerfana sin jsonl: GT valido
    }
    # Orden determinista por (sesion, inicio_ms, fin_ms).
    assert [(f["sesion"], f["inicio_ms"]) for f in filas] == sorted(
        (f["sesion"], f["inicio_ms"]) for f in filas)


def test_confusion_idle_sin_evaluaciones_cuenta_tn_vigente():
    """[MENOR] Criterio vigente asimetrico: un tramo MOTION sin evaluaciones se
    excluye como 'sin_eval' (no penaliza), pero un tramo IDLE sin evaluaciones
    (n_eval=0) cuenta como TN (le suma al bueno). Con huecos sin cobertura esto
    INFLA las metricas: la FPR contaria como bueno un idle donde el sensor nunca
    emitio nada. Test fija el comportamiento actual para que el cambio de
    criterio sea explicito (reportado al orquestador)."""
    gt = [
        {"sesion": "X", "inicio_ms": 1000, "fin_ms": 2000, "verdad_terreno": "idle"},
        {"sesion": "X", "inicio_ms": 3000, "fin_ms": 4000, "verdad_terreno": "idle"},
    ]
    stats = [
        {"n_eval": 0, "n_motion_dentro": 0},   # idle SIN datos -> vigente: TN
        {"n_eval": 5, "n_motion_dentro": 1},   # idle con motion -> FP
    ]
    c = ad.matriz_confusion(gt, stats)
    assert c["tn"] == 1 and c["fp"] == 1
    assert c["fpr"] == 0.5 and c["recall_motion"] is None  # sin tramos motion
    assert "nota_honest" not in c


# --------------------------------------------------------------- determinismo run
def _correr_sin_wsl(ds: Path, val: Path, out: Path) -> dict:
    """main() offline: escribe analisis.json + informe (BUG-PROD-F5A-1 arreglado)."""
    assert ad.main(["--dataset", str(ds), "--salida", str(out),
                    "--validacion", str(val), "--sin-wsl", "--no-graficos"]) == 0
    return json.loads((out / "analisis.json").read_text(encoding="utf-8"))


def test_run_analisis_determinismo_solo_generado_utc():
    """REGLA del orquestador: dos corridas sobre los MISMOS inputs dan
    ``analisis.json`` identico salvo ``generado_utc`` (hash_inputs estables:
    version del script + sha256 de dataset y validacion)."""
    base = _montar_dataset("determinismo")
    ds, val = base / "dataset", _csv_validacion(base)
    a = _correr_sin_wsl(ds, val, base / "out_a")
    b = _correr_sin_wsl(ds, val, base / "out_b")

    assert a.get("generado_utc") and b.get("generado_utc")
    assert a["hash_inputs"] == b["hash_inputs"]
    assert a["pipeline_version"] == b["pipeline_version"]
    a.pop("generado_utc")
    b.pop("generado_utc")
    assert a == b, "el analisis no es determinista fuera de generado_utc"


def test_run_analisis_sin_wsl_completa_sin_excepcion():
    """Regresion BUG-PROD-F5A-1: el run offline completo (JSON + informe) no revienta."""
    base = _montar_dataset("sin_wsl_crash")
    ds, val = base / "dataset", _csv_validacion(base)
    out = base / "salida"
    assert ad.main(["--dataset", str(ds), "--salida", str(out),
                    "--validacion", str(val), "--sin-wsl", "--no-graficos"]) == 0
    assert (out / "informe_metricas.md").is_file()
