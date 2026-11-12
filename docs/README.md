# docs/ — Índice de lectura de AulaSense

> Orden recomendado para el profesor/evaluador y para los agentes que ejecutan el plan.
> Estado: ✅ = existe en disco (andamiaje del planificador, 2026-11-12); ⏳ = lo crea la fase indicada
> del mapa `PLAN.md` §6 (checklist vivo: `iot2/.tmp/checklist.md` v23).

## Lectura rápida (qué es y cómo se usa)

| # | Documento | Estado | Contenido |
|---|---|---|---|
| 1 | [`../README.md`](../README.md) | ✅ | Qué es AulaSense, instalación, arranque de demo, estructura, privacidad |
| 2 | [`PROPUESTA.md`](PROPUESTA.md) | ✅ | Propuesta para el profesor (estilo del curso): objetivo, arquitectura 4 capas, datos, preguntas analíticas, diferenciadores, alcance, limitaciones honestas, cobertura C1–C14 |
| 3 | [`../PLAN.md`](../PLAN.md) | ✅ | Plan maestro: matriz de decisión, supuestos S1–S12, decisiones D1–D12, mapa de fases F0–F7+GATE con agentes/entregables/criterios, trazabilidad C1–C14, riesgos E1–E10+R1–R8 |
| 4 | [`../SPEC.md`](../SPEC.md) | ✅ | Especificación técnica: hardware, pinout corregido, firmware ESPectre, contrato MQTT, esquema SQLite, dashboard, comandos exactos |
| 5 | [`../AGENTS.md`](../AGENTS.md) | ✅ | Convenciones y reglas duras para todo agente (edge-first, privacidad, python, IDF 5.5.6, secretos, GPIO, seguridad) |

## Evidencia y fuentes

| Documento | Estado | Contenido |
|---|---|---|
| [`fuentes.md`](fuentes.md) | ✅ | Todas las URLs consultadas (2026-11-12) con el claim que sostiene cada una y el verificador (planificador/analista). 0 llamadas pagadas |
| `iot2/.tmp/f23_research_tendencias.md` | ✅ (gitignored) | Informe verbatim del analista de tendencias + addendum de verificación primaria |
| `iot2/.tmp/f23_research_viabilidad.md` | ✅ (gitignored) | Informe verbatim del analista de viabilidad (GPIO corregido, veredictos A–E) |

## Firmware propio

| Documento | Estado | Contenido |
|---|---|---|
| [`../firmware/parche_aulasense/README.md`](../firmware/parche_aulasense/README.md) | ✅ | Spec del módulo `app_feedback` (buzzer/WS2812/die temp/BLE opcional), instrucciones de aplicación al vendor, guardas GPIO, nota GPLv3. El código lo crea F3 |

## Entregables de ejecución (los crean las fases)

| Documento | Estado | Fase | Contenido |
|---|---|---|---|
| `guion_demo.md` | ⏳ | F5b | Guion de demo cronometrado con fallbacks (C8/E3) |
| `plantilla_validacion.md` | ⏳ | F4 | Plantilla de registro manual de verdad terreno por sesión |
| `validacion_sesion1.csv` … `sesionN.csv` | ⏳ | F4 | Intervalos motion/id manuales con timestamps (C10) |
| `INFORME.docx` | ⏳ | F6a | Informe Word explicativo completo (C7), generado por `tools/generadores/gen_informe.py` |
| `PRESENTACION.pptx` | ⏳ | F6a | Presentación para el profesor (C7), generada por `tools/generadores/gen_presentacion.py` |
| `../analysis/informe_eda.md` | ⏳ | F5a | EDA + modelo predictivo con métricas y nota de tamaño muestral (C5) |
| `../analysis/dataset/consolidado.csv` | ⏳ | F4 | Dataset propio real exportable (C3), solo `origen=real` |
| `../assets/*.png` | ⏳ | F5a | Figuras del EDA/modelo (≥6) |

## Contexto del proyecto anterior (archivado)

DriveGuard (`dashcam-ia/`, dashcam de microsueño) fue **abandonado por decisión del usuario**: el
detector de caras en vivo nunca detectó una cara real pese a encuadre perfecto (fracaso terminal
documentado en `PLAN_DETECCION.md` E1–E3). En F0 el orquestador lo archiva en `iot2/archive/dashcam-ia/`
(historia preservada). AulaSense hereda su infraestructura como **patrón** (E7) y sus lecciones como
**restricciones de diseño** (E1–E10, `iot2/.tmp/registro_riesgos_nuevo.md`), pero es un proyecto nuevo:
sensing por Wi-Fi CSI con IA en-chip (el detector no necesita cámara), evidencia de imagen QVGA acotada
local-only (F24: solo al servidor local, consentimiento + retención purgable) y prueba de vida temprana
(gate F1c).