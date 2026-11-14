# docs/ — Índice de lectura de AulaSense

> Orden recomendado para el profesor/evaluador y para los agentes que ejecutan el plan.
> Estado: ✅ = existe en disco (andamiaje del planificador, 2026-11-12); ⏳ = lo crea la fase indicada
> del mapa `PLAN.md` §6/§11/§12 (checklist vivo: `iot2/.tmp/checklist.md` **v25**).
> **F25 (2026-11-14):** imágenes por MQTT + servidor WSL + visión IA con búsqueda + login +
> diagramas + video — decisiones D-F25-1..13 en `PLAN.md` §12, contratos en `SPEC.md`
> §3.2.1/§6.2/§7.2/§7.3/§8.2/§9.2/§9.3/§12.1/§15.

## Lectura rápida (qué es y cómo se usa)

| # | Documento | Estado | Contenido |
|---|---|---|---|
| 1 | [`../README.md`](../README.md) | ✅ | Qué es AulaSense, instalación, arranque de demo, estructura, privacidad |
| 2 | [`PROPUESTA.md`](PROPUESTA.md) | ✅ | Propuesta para el profesor (estilo del curso): objetivo, arquitectura 4 capas, datos, preguntas analíticas, diferenciadores, alcance, limitaciones honestas, cobertura C1–C14 |
| 3 | [`../PLAN.md`](../PLAN.md) | ✅ | Plan maestro: matriz de decisión, supuestos S1–S22, decisiones D1–D12 + D-F24-1..8 + D-F25-1..13, mapa de fases F0–F7+GATE+F24+F25 con agentes/entregables/criterios, trazabilidad C1–C14, riesgos E1–E10+R1–R16+R-F25 |
| 4 | [`../SPEC.md`](../SPEC.md) | ✅ | Especificación técnica: hardware, pinout corregido, firmware ESPectre, contrato MQTT (+image F25), esquema SQLite (+auth/visión F25), dashboard (+login/búsqueda), WSL §15, comandos exactos |
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

## Entregables F25 (expansión 2026-11-14, orden del dueño)

| Documento | Estado | Fase | Contenido |
|---|---|---|---|
| `../assets/diagrama_tecnico.dsl/.svg/.png` | ⏳ | F25-f1 | Diagrama técnico C4 contenedores + flujo de datos ESP32→broker→WSL→dashboard (skill graphic-arq-software) |
| `../assets/poster_funcionamiento.png` | ⏳ | F25-f2 | Póster visual del concepto CSI (ondas/persona/chip/dashboard), skill qwen-imagen (@6-medios) |
| `guion_video.md` | ⏳ | F25-f3 | Guion del video explicativo: DriveGuard→fallo caras→CSI→cómo funciona→construcción→finalidad |
| `video_subtitulos.srt` | ⏳ | F25-f3/f5 | Subtítulos del video (burn-in en el ensamblaje) |
| `video_aulasense.mp4` | ⏳ | F25-f5 | Video explicativo completo (escenas HappyHorse 480P + TTS opcional + FFmpeg); presupuesto Credits medido |
| `../server/deploy/README_wsl.md` | ⏳ | F25-b1 | Despliegue WSL Debian: venv, systemd, portproxy loopback, cutover, fallback Windows |
| `../claves/.env.example` | ✅ | andamiaje F25 | Nombres de variables de auth/IA (valores vacíos; la password la genera el código en F25-e) |
| `consentimiento_aula.md` | ⏳ | F24-f→F25-g | Consentimiento ACTUALIZADO: las imágenes transitan el broker autenticado (retain=false) |
| `politica_retencion_imagenes.md` | ⏳ | F24-f→F25-g | Retención 30 d purgable (imagen_purgada conserva fila+descripción; dataset intacto) |
| `INFORME.docx` / `PRESENTACION.pptx` | ⏳ | F6a (actualizado F25) | Regenerados con TODO: WSL, MQTT-imágenes, visión+búsqueda, login, diagramas, video, costes Credits reales |

## Contexto del proyecto anterior (archivado)

DriveGuard (`dashcam-ia/`, dashcam de microsueño) fue **abandonado por decisión del usuario**: el
detector de caras en vivo nunca detectó una cara real pese a encuadre perfecto (fracaso terminal
documentado en `PLAN_DETECCION.md` E1–E3). En F0 el orquestador lo archiva en `iot2/archive/dashcam-ia/`
(historia preservada). AulaSense hereda su infraestructura como **patrón** (E7) y sus lecciones como
**restricciones de diseño** (E1–E10, `iot2/.tmp/registro_riesgos_nuevo.md`), pero es un proyecto nuevo:
sensing por Wi-Fi CSI con IA en-chip (el detector no necesita cámara), evidencia de imagen QVGA acotada
local-only (F24: solo al servidor local, consentimiento + retención purgable) y prueba de vida temprana
(gate F1c).