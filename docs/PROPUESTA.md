# Propuesta de Proyecto: AulaSense — Sensing de Movimiento/Ocupación por Wi-Fi (CSI) con IA en el Borde

**Materia:** Big Data e Inteligencia Artificial — ITSE (Perú)
**Plataforma de hardware:** ESP32-S3-CAM GOOUUU v1.5 (16 MB Flash, 8 MB PSRAM octal, Wi-Fi 2.4 GHz) + laptop como servidor de ingesta/tablero. **Costo extra: $0** — la cámara OV3660 ya está onboard (se usa como evidencia de evento, F24), no hay micrófono, no hay radar, no se compra ningún sensor.
**Caso real:** saber si hay actividad/movimiento en un aula, laboratorio u oficina es valioso para la utilización de espacios, el ahorro energético y la seguridad. Este proyecto construye un **sensor de movimiento con IA en el chip** que usa las propias señales Wi-Fi de la sala como "sensor" (CSI). **F24 (orden del dueño):** además captura **1 imagen QVGA de evidencia al onset de movimiento** (cooldown 15 s) que se sube **solo al servidor local del proyecto** (nunca a la nube ni al broker público), con consentimiento de aula, retención configurable y sin detección facial. La privacidad es **acotada local-only**, no "por construcción": el CSI crudo nunca sale del chip y las imágenes son evidencia accesoria y purgable, no vigilancia.

**Novedad 2026:** se apoya en [ESPectre](https://github.com/francescopace/espectre) v3.2.0 (publicado el 2026-11-11), SDK/firmware abierto que **Espressif recomienda oficialmente** como community project en su repositorio [`esp-csi`](https://github.com/espressif/esp-csi). El Wi-Fi sensing (CSI) es la tendencia 2026 de edge AI; **F24 añade evidencia de imagen QVGA acotada local-only** (solo al servidor local del proyecto, consentimiento de aula, retención purgable, sin caras-ML): la privacidad es acotada por mitigación, no "por construcción" (C9 cumple-parcial).

## Objetivo

Implementar un nodo edge que capture el **Channel State Information (CSI)** del radio Wi-Fi y ejecute **100 % en el ESP32-S3** un detector neuronal (MLP de 529 parámetros, sin calibración) que decide `idle | motion` cada 250 ms. Cuando alguien (o algo) se mueve en la zona, el nodo emite una **alerta audible local** (buzzer GPIO14) + visual (LED WS2812 GPIO48) y publica un **evento JSON con marca de tiempo** por MQTT hacia un tablero en vivo en la laptop. El sistema acumula un **dataset propio de eventos de movimiento** (real, no simulado) que alimenta un EDA y un **modelo predictivo simple** de actividad del aula por franja horaria.

El resultado final: demo en vivo (el operador camina por la zona y el sistema alerta en segundos; la zona queda quieta y vuelve a `idle`), tablero en tiempo real con métricas de edge computing **y galería de evidencia de imágenes (F24)**, y análisis big data del dataset generado con validación contra verdad terreno manual. **La IA principal es el detector CSI neuronal en-chip (edge-first); una capa de visión persona (esp-dl) es OPCIONAL default n, sujeta a un spike de coexistencia — si no cabe junto al CSI, se abandona documentado (orden del usuario: "si no se puede no lo hagas").**

## Arquitectura de Big Data (4 capas, estilo proyecto de referencia)

- **Capa Edge (el ESP32-S3):** el radio Wi-Fi captura CSI (HT20, 2.4 GHz, ~100 muestras/s) usando tráfico de ping/DNS al gateway como estímulo. En el chip corre el **detector neuronal** (perfil High Accuracy: MLP 8→24→12→1, 529 parámetros; alternativa Lightweight de 2 features con calibración de ~10 s). La decisión `idle|motion` se toma cada 250 ms **en el nodo**; el PC nunca procesa la señal cruda. Feedback local sin red: buzzer (beep en onset de movimiento, cooldown 5 s) + LED WS2812 (color por estado). El nodo publica además su **temperatura interna del die** (proxy de carga de cómputo) cada 30 s. Métricas de edge computing medidas en el chip vía diagnóstico nativo: **ms por detección, % CPU, heap libre, CSI paquetes/s, RSSI, canal Wi-Fi**. **F24: la cámara OV3660 captura 1 JPEG QVGA al onset de motion (evidencia de evento, cooldown 15 s, tarea de baja prioridad que NUNCA bloquea el detector CSI; gate de memoria que auto-deshabilita la captura si el heap cae). El CSI crudo nunca sale del chip; **F25 (D-F25-1): la foto viaja por MQTT (topic `image`, b64, retain=false) al broker autenticado con prefijo aislado — la misma conexión persistente del nodo (N1) — con fallback HTTP :8001 con token (N2); destino final SOLO el servidor local del proyecto, nunca la nube.** Sin micrófono.**
- **Capa de Ingesta:** cada evaluación del detector genera un JSON (`timestamp_ms`, `state`, `score`) publicado por MQTT al broker `broker.gilman.pro:14769` (con autenticación; prefijo de topics propio `aulasense/v1/devices/{device_id}`). Un servicio Python (paho-mqtt) **valida el esquema**, mapea el reloj monótono del nodo a época de PC, deriva los bordes `MOVIMIENTO_INICIO`/`MOVIMIENTO_FIN` y sondea los diagnósticos del nodo cada 30 s. Canal paralelo: heartbeat/health con Last Will offline.
- **Capa de Almacenamiento:** los mensajes validados se persisten en **SQLite** (WAL, writer único: `server/datos/aulasense.db`) + exportación **CSV** acumulativa. El dataset consolidado exportable contiene **solo datos reales** (`origen=real`); los fixtures sintéticos de prueba quedan etiquetados y separados. SQLite: cero infraestructura, portable, suficiente para decenas de miles de evaluaciones y consultable con pandas.
- **Capa de Análisis y Visualización:** dashboard web en vivo (**FastAPI + WebSocket + Chart.js**, servido desde la laptop en `127.0.0.1`), con **tres pestañas**: **Monitoreo** —tarjeta por nodo con estado grande idle/motion, gauge de score, métricas edge en vivo, feed de eventos, badge `en vivo`/`stale` y aviso visual+sonoro en onset—, **Reportes** —KPIs, serie temporal de score y métricas, eventos de movimiento por hora, heatmap por franja horaria, tabla paginada con export CSV— y **Validación** —matriz de confusión por sesión contra verdad terreno manual (recall, FPR, latencia) con nota de tamaño muestral—. Un proceso ETL/EDA en **pandas** sobre el dataset propio produce patrones de actividad por hora/día, calidad del CSI y correlaciones, y un **modelo predictivo simple** (sklearn: regresión logística + árbol) de probabilidad de movimiento en la ventana siguiente de 10 min — la "IA además del edge" que pide la materia. **Tercera fuente de datos gratis:** pronóstico [Open-Meteo](https://open-meteo.com/en/docs) (sin API key, uso no comercial) integrado al análisis.

## Datos generados y preguntas analíticas

Durante las sesiones de demostración y pruebas (≥3 sesiones reales de ≥20 min en ≥2 días distintos, más la demo final en aula), el sistema produce:

| Conjunto | Origen | Frecuencia | Campos principales |
|---|---|---|---|
| Evaluaciones CSI | Nodo ESP32-S3 (IA en el chip) | Cada 250 ms | timestamp, state idle/motion, score |
| Eventos de movimiento | Derivados en ingesta (bordes) | Por onset/fin | tipo, duración_ms, score |
| Telemetría edge | Diagnóstico nativo del nodo | Cada 30 s | ms/detección, CPU %, heap, CSI pps, RSSI, canal |
| Temperatura del die | Sensor interno del S3 | Cada 30 s | die_temp_c (proxy de carga, NO ambiente) |
| Clima externo | Open-Meteo (API gratis) | Horario | temperatura, humedad, precipitación, viento |
| Verdad terreno | Registro manual del operador | Por sesión | intervalos motion/id con timestamps |
| **Imágenes de evidencia (F24)** | **Cámara OV3660 del nodo (solo al onset)** | **1 por onset, cooldown 15 s** | **ruta, hash sha256, tamaño, device, timestamp, evento_id (FK), ia_persona/ia_score (opcional), imagen_purgada** |
| **IA visión persona (F24, opcional)** | **esp-dl en-chip sobre la imagen (default n, spike-gated)** | **Por imagen inferida** | **persona/no-persona + score (campo ia_imagen, dato analizable)** |
| **Eventos de config/provision (F24)** | **Nodo (topic aulasense_config, sin secretos)** | **Por evento AP/config/captura** | **tipo (AP_FALLBACK/CFG_GUARDADA/STA_RECONECTADO/IMAGEN_GATE_OFF/IMAGEN_DESCARTE), detalle (solo longitudes/flags)** |

**Preguntas analíticas que el dataset responde:**
1. ¿A qué horas del día se concentra la actividad del aula? ¿Puede un modelo simple predecir la ventana siguiente de 10 min a partir del histórico + clima?
2. ¿Cuál es la latencia real detección→tablero (punta a punta) y el costo de inferencia en el chip (ms/detección, % CPU)?
3. ¿Cuál es el recall y la tasa de falsos positivos LOCALES contra verificación manual binaria, y cómo se comparan con las métricas publicadas del detector?
4. ¿Cómo se correlacionan la carga de cómputo del edge (die temp) y las condiciones externas (Open-Meteo) con los patrones de actividad?

## Calidad del dato y privacidad

- **Validación contra verdad terreno manual:** el operador registra en planilla los intervalos de movimiento/quietud de cada sesión (caminar por la zona = motion; zona quieta = idle) y se mide matriz de confusión, recall, FPR y latencia del detector. Toda métrica se reporta **con su tamaño de muestra**; las cifras del vendor (F1 98,1–99,3 %) son de SU dataset y no se prometen como propias.
- **Privacidad ACOTADA local-only (F24 re-enmarcado; transporte actualizado por F25/D-F25-1 — ya NO "por construcción"):** el sistema captura 1 imagen QVGA de evidencia al onset de movimiento. **Acotada:** las imágenes transitan el **broker MQTT autenticado** (topic `image`, b64, **retain=false**, prefijo aislado `aulasense/v1` — el destino final sigue siendo **SOLO el servidor local del proyecto**; HTTP :8001 con Bearer token queda como canal alternativo N2), **NUNCA a la nube**; el CSI crudo nunca sale del chip; **consentimiento de aula documentado obligatorio** (las imágenes pueden capturar personas = datos personales); **retención y borrado configurables** (default 30 días; la purga marca `imagen_purgada` y conserva la fila+ia → el dataset no se rompe); imágenes gitignored; **sin caras-ML** (no detección facial; la visión opcional es persona, no caras). Minimización: QVGA, solo onset, cooldown 15 s, gate de memoria. Los datos de movimiento pueden revelar rutinas → aviso de consentimiento, broker con autenticación, dashboard **con login** (loopback/local + Origin guard; publicación VPS tras autenticación). **C9 = cumple-parcial (local-only + broker autenticado), no privacidad por construcción.**
- **Datos honestos:** todo fixture sintético de prueba se etiqueta `origen=sintetico`/`SIMULADO` en docs y dashboard; nunca se mezcla con captura real ni se presenta como real. El dataset consolidado exportable es solo real.

## Diferenciadores frente a un proyecto genérico de sensores

| Aspecto | Proyecto genérico (PIR/cámara) | AulaSense (este proyecto) |
|---|---|---|
| IA | Reglas/umbral en servidor o PIR sin IA | Detector neuronal (MLP 529 params) corriendo **en el ESP32-S3** (edge real, medible) |
| Privacidad | Cámara que graba personas en streaming continuo | **Evidencia acotada local-only (F24)**: 1 JPEG QVGA por onset (cooldown 15 s), solo al servidor local con token, nunca nube/broker público, consentimiento + retención purgable + sin caras-ML; el CSI crudo nunca sale del chip |
| Novedad | PIR mainstream desde 2023 | Wi-Fi sensing 2026 (ESPectre v3.2.0, recomendado por Espressif en esp-csi) |
| Dataset | Simulado | Propio, real, generado por el nodo, exportable CSV, validado manualmente |
| Análisis | Conteos | EDA + modelo predictivo de actividad por franja horaria + 3 fuentes (CSI, die temp, Open-Meteo) |
| Demo | Gráficas estáticas | En vivo: el operador camina y suena la alerta en segundos; feedback en dashboard + buzzer + LED |
| Costo | Sensor dedicado ($3–10) | **$0**: solo la placa y el router Wi-Fi existentes |

## Alcance y entregables

1. Firmware funcional flasheado y corriendo en la placa GOOUUU ESP32-S3-CAM v1.5 real (ESPectre Native v3.2.0 + parche propio de feedback `app_feedback.c`, GPLv3).
2. Servidor en la laptop: ingesta MQTT + SQLite + dashboard en vivo + fetcher Open-Meteo (+ receptor BLE opcional).
3. Dataset propio real (≥3 sesiones, ≥20 min c/u, ≥2000 evaluaciones) con validación manual y export CSV consolidado.
4. Análisis pandas del dataset con EDA (≥6 figuras) + modelo predictivo simple entrenado solo con datos reales.
5. Documento Word explicativo completo + presentación PPTX para el profesor.
6. Código reproducible: parche de firmware, pipeline de ingesta, dashboard, tools de demo, tests (repo `aulasense/`).

## Limitaciones honestas (declaradas desde el inicio)

- ESPectre detecta **MOVIMIENTO**, no presencia estacionaria: no prueba que una sala esté vacía, no identifica ni cuenta personas. `idle` ≠ sala vacía.
- Una placa cubre una zona (one board per room); colocación a 3–8 m del AP, fuera de cajas metálicas; sensible al entorno RF (canal saturado, tráfico bloqueado → modos de tráfico alternativos).
- La temperatura del die es proxy de carga de cómputo, **no** temperatura ambiente.
- El reloj del nodo es monótono; la época la estima el PC (re-estimación tras reboot).
- El sensing requiere Wi-Fi 2.4 GHz (el S3 no ve 5 GHz).
- **F24 — privacidad PARCIAL (C9 cumple-parcial):** el sistema ya NO es "cero imágenes". Captura evidencia QVGA al onset, acotada a servidor local (nunca nube/broker público), con consentimiento de aula, retención configurable (30 d, purga que no rompe el dataset) y sin caras-ML. Las imágenes son datos personales: minimización + acceso local autenticado + gitignored + purgables. No es privacidad por construcción; es privacidad acotada por mitigación.
- **F24 — la IA de visión puede NO caber:** la capa de visión persona (esp-dl) es opcional default n, sujeta a spike de coexistencia con el detector CSI. Si el spike prueba que no cabe (pps/heap), se ABANDONA y se documenta ("la capa de visión IA no cabe junto al detector CSI en este hardware — verificado"). La IA principal (detector CSI neuronal en-chip) YA cumple edge-first sin ella.
- **F24 — la imagen es accesoria, no fuente de verdad:** si la cola se llena o el heap cae, la imagen se descarta/auto-deshabilita y el evento CSI sigue. La detección de movimiento no depende de la cámara.
- **F24 — big data académica, no masiva:** pipeline real ingesta→almacén→análisis con datos propios (~14.4k evaluaciones + cientos de imágenes), no petabytes. Lo que el curso pide es el pipeline completo con datos reales, y eso se cumple.

## Cobertura de condiciones del curso (C1–C14)

| Cond | Cobertura |
|---|---|
| C1 edge-first | Detector MLP en el S3; PC solo ingesta/almacena/visualiza/analiza |
| C2 MQTT JSON | Topics nativos motion/health/sensing con esquema validado |
| C3 dataset propio | Evaluaciones+eventos reales en SQLite, export CSV (solo real) |
| C4 dashboard realtime | FastAPI WS + Chart.js, KPIs, feed vivo |
| C5 EDA+modelo | pandas EDA + clasificador sklearn de actividad por franja |
| C6 multi-fuente | CSI + die temp S3 + Open-Meteo (3 fuentes, $0) |
| C7 Word+PPTX | Informe y presentación generados reproduciblemente |
| C8 demo en vivo | Guion cronometrado + ensayo completo (caminar→alerta en segundos) |
| C9 privacidad | **F24: cumple-PARCIAL (local-only)** — imágenes de evidencia solo al servidor local (nunca nube/broker público), consentimiento de aula, retención configurable, sin caras-ML; CSI crudo no sale del chip |
| C10 validación manual | Verdad terreno binaria por sesión → matriz de confusión |
| C11 alerta audible/BLE | Buzzer GPIO14 + WS2812 + beep dashboard; BLE opcional |
| C12 métricas edge | Diagnóstico nativo: ms/detección, CPU %, heap, CSI pps, RSSI |
| C13 datos honestos | origen real/sintetico, SIMULADO etiquetado, nunca mezclado |
| C14 entregables código | firmware patch + pipeline + dataset + tablero + informe |

**Cumplimiento: 14/14 con C9 en cumple-PARCIAL (local-only) por el re-enmarque F24** (C6 y C11 son deseables y también se cubren; la mayoría exigida queda superada). La privacidad ya no es "por construcción / cero imágenes" sino acotada local-only con consentimiento, retención y sin caras-ML — declarado honestamente, no adornado.

> La especificación técnica detallada (pinout, contrato MQTT, esquema JSON/SQLite, comandos) está en `SPEC.md`; el plan de ejecución por fases y agentes está en `PLAN.md`; las fuentes con URL+fecha están en `docs/fuentes.md`.