# -*- coding: utf-8 -*-
"""F6-a — tests del generador de entregables (tools/generar_informe.py).

Solo funciones puras: no tocan WSL, ni la BD de imágenes, ni generan documentos. El objetivo es
blindar lo que la especificación pide: (a) las tablas salen del JSON y no de constantes, (b) la
frase del flag ts_ms prefiere el archivo de cierre, (c) los pies de figura salen del nombre y
(d) la selección de fotos no censura lo que no tiene personas ni publica material sensible.
"""
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]          # aulasense/
sys.path.insert(0, str(RAIZ / "tools"))

import generar_informe as gen                       # noqa: E402


@pytest.fixture(scope="module")
def analisis_real():
    return gen.leer_json(RAIZ / "docs" / "analisis" / "analisis.json", {})


@pytest.fixture(scope="module")
def eda_real():
    return gen.leer_json(RAIZ / "docs" / "analisis" / "eda.json", {})


# ------------------------------------------------------------------ tablas desde el JSON
def test_tabla_sesiones_lee_las_cifras_del_json(analisis_real):
    filas = gen.tabla_sesiones(analisis_real)
    assert filas[0][0] == "Sesión"
    assert len(filas) == 1 + len(analisis_real["metricas_por_sesion"])
    por_nombre = {fila[0]: fila for fila in filas[1:]}
    f4s1b = por_nombre["F4S1B"]
    assert f4s1b[2] == gen.num(16604, 0) == "16.604"
    assert f4s1b[3] == gen.pct(6.1311) == "6,13 %"
    assert f4s1b[4] == gen.num(3.5253, 4) == "3,5253"
    # F4S1 no tiene evaluaciones motion: la mediana debe quedar "n/d", no 0
    assert por_nombre["F4S1"][5] == "n/d"


def test_cifras_clave_sigue_al_json_y_no_esta_hardcodeada():
    analisis = {
        "pipeline_version": "prueba-0",
        "metricas_por_sesion": [{
            "sesion": "X", "duracion_s": 600.0, "n_eval": 100, "n_motion": 5,
            "pct_motion": 5.0, "pps_medio": 4.0, "scores_motion": {"n": 5, "p50": 0.9},
            "scores_todo": {}, "telemetria": {},
        }],
        "confusion": {"recall_motion": 0.5, "tp": 7, "fn": 7, "tramos_motion_gt": 14,
                      "cobertura_media_motion_dentro_gt": 0.2},
        "ground_truth": {"fuentes": ["f"], "motion": 14, "idle": 0, "n_tramos": 14},
        "integridad_jpeg": {"validas": 3, "rotas": 1, "tasa_integridad": 0.75},
        "correlacion_foto_onset": {"fotos": 4, "emparejadas_pct": 100.0},
        "latencias": {"eventos": {"mediana_corr_truncacion_ms": 42.0}},
        "vision": {"estados_imagenes_reales": {"hecho": 3, "error": 1},
                   "tokens_totales": 999, "vision_uso_por_resultado": []},
    }
    cifras = gen.extraer_cifras(analisis, {}, {})
    clave = gen.cifras_clave(cifras)
    assert clave["recall"] == "50,0 %"
    assert clave["tp"] == "7"
    assert clave["tramos_gt"] == "14"
    assert clave["integridad"] == "75,0 %"
    assert clave["vision_tokens"] == "999"
    assert clave["latencia_mediana"] == "42 ms"
    # y al cambiar el JSON cambia el texto (prueba de que no hay constante escondida)
    analisis["vision"]["tokens_totales"] = 1234
    assert gen.cifras_clave(gen.extraer_cifras(analisis, {}, {}))["vision_tokens"] == "1.234"


def test_tabla_vision_etiqueta_llamadas_no_imagenes(analisis_real):
    cifras = gen.extraer_cifras(analisis_real, {}, {})
    filas = gen.tabla_vision(cifras)
    assert filas[0][:2] == ["Resultado de la llamada", "Llamadas"]
    total = filas[-1]
    suma = sum(item["n"] for item in cifras["vision"]["por_resultado"])
    assert total[1] == gen.num(suma, 0)


# ------------------------------------------------------------------ frase del flag ts_ms
def test_texto_flag_ts_prefiere_el_archivo_de_cierre(tmp_path):
    cierre = tmp_path / "flag_ts_cierre.md"
    cierre.write_text(
        "# Cierre\n\n> nota\n\n"
        "Los ids 726-761 se re-engancharon por multiples re-anchors R3 del reloj del nodo, "
        "sin daño material en el dataset ni en el recall.\n",
        encoding="utf-8")
    analisis = {"divergencia_registros_vs_bd": {"n_ts_divergentes_gt5s": 36}}
    frase, origen = gen.texto_flag_ts(analisis, rutas=[cierre])
    assert origen == str(cierre)
    assert frase.startswith("Los ids 726-761")


def test_texto_flag_ts_sin_archivo_usa_version_corta():
    analisis = {"divergencia_registros_vs_bd": {"n_ts_divergentes_gt5s": 36}}
    frase, origen = gen.texto_flag_ts(analisis, rutas=[Path("no-existe-flag.md")])
    assert origen is None
    assert "36" in frase and "re-enganche R3" in frase


def test_texto_flag_ts_usa_la_cita_de_la_seccion_frase(tmp_path):
    # caso real de iot2/.tmp/flag_ts_cierre.md: la frase publicable va como blockquote
    cierre = tmp_path / "flag_ts_cierre.md"
    cierre.write_text(
        "# flag_ts_cierre.md — cierre\n\n"
        "- Fuentes: `debian-aulasense:/home/aulasense/aulasense-datos/aulasense.db`\n\n"
        "## 4) Frase honesta DEFINITIVA para el informe F6a (un parrafo)\n\n"
        "> La divergencia entre el dump historico y la BD canonica no es corrupcion: es el\n"
        "> diseno R3 de re-enganche de epoca del ingesta, con 36 ids re-anclados.\n\n"
        "## 5) Otro apartado\n",
        encoding="utf-8")
    frase, origen = gen.texto_flag_ts({}, rutas=[cierre])
    assert origen == str(cierre)
    assert frase.startswith("La divergencia")
    assert "R3" in frase and "/home/" not in frase


# ------------------------------------------------------------------ figuras y pies
def test_figuras_con_pie_desde_el_nombre():
    figuras = gen.figuras_disponibles()
    assert len(figuras) == 11, "el pipeline publica 11 figuras en docs/analisis/graficos"
    pies = [pie for _, pie in figuras]
    assert all(pie and len(pie) > 15 for pie in pies)
    assert len(set(pies)) == len(pies)
    assert figuras[0][0].name == "serie_motion.png"


# ------------------------------------------------------------------ privacidad de las fotos
def test_mencion_de_persona_ignora_negaciones():
    assert gen._menciona_persona("se observa parcialmente la cabeza de una persona")
    assert not gen._menciona_persona(
        "fondo morado; no hay evidencia clara de personas en la escena")
    assert not gen._menciona_persona("un aula vacía, sin personas ni objetos")


def test_puntuacion_prefiere_escena_sobre_glitch():
    escena = gen._puntuar_foto("una pared blanca y una puerta de un aula", "aula, interior")
    glitch = gen._puntuar_foto("fotograma corrupto con bloques y franjas", "glitch, distorsion")
    assert escena > glitch


def test_pie_figura_usa_mapa_y_fallback():
    assert "motion" in gen.pie_figura(RAIZ / "docs/analisis/graficos/serie_motion.png").lower()
    assert gen.pie_figura(Path("grafico_nuevo.png")) == "Grafico nuevo"