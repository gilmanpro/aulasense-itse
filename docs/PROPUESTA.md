# Propuesta de Proyecto: AulaSense — Sensing de Movimiento/Ocupación por Wi-Fi (CSI) con IA en el Borde

**Materia:** Big Data e Inteligencia Artificial — ITSE (Perú)
**Plataforma de hardware:** ESP32-S3-CAM GOOUUU v1.5 (16 MB Flash, 8 MB PSRAM octal, Wi-Fi 2.4 GHz) + laptop como servidor de ingesta/tablero. **Costo extra: $0** — no se usa la cámara, no hay micrófono, no hay radar, no se compra ningún sensor.
**Caso real:** saber si hay actividad/movimiento en un aula, laboratorio u oficina —sin cámaras ni micrófonos— es valioso para la utilización de espacios, el ahorro energético y la seguridad, pero las soluciones convencionales fallan: el PIR no detecta personas quietas y la cámara invade la privacidad. Este proyecto construye un **sensor de movimiento que preserva la privacidad por construcción**: usa las propias señales Wi-Fi de la sala como "sensor" y ejecuta la IA **en el chip**. No se fotografía ni se graba nada: solo se emiten eventos de movimiento.

**Novedad 2026:** se apoya en [ESPectre](https://github.com/francescopace/espectre) v3.2.0 (publicado el 2026-11-11), SDK/firmware abierto que **Espressif recomienda oficialmente** como community project en su repositorio [`esp-csi`](https://github.com/espressif/esp-csi). El "no-camera privacy sensing" (sensing sin sensores dedicados) es la tendencia 2026 de edge AI privado.

## Objetivo

Implementar un nodo edge que capture el **Channel State Information (CSI)** del radio Wi-Fi y ejecute **100 % en el ESP32-S3** un detector neuronal (MLP de 529 parámetros, sin calibración) que decide `idle | motion` cada 250 ms. Cuando alguien (o algo) se mueve en la zona, el nodo emite una **alerta audible local** (buzzer GPIO14) + visual (LED WS2812 GPIO48) y publica un **evento JSON con marca de tiempo** por MQTT hacia un tablero en vivo en la laptop. El sistema acumula un **dataset propio de eventos de movimiento** (real, no simulado) que alimenta un EDA y un **modelo predictivo simple** de actividad del aula por franja horaria.

El resultado final: demo en vivo (el operador camina por la zona y el sistema alerta en segundos; la zona queda quieta y vuelve a `idle`), tablero en tiempo real con métricas de edge computing, y análisis big data del dataset generado con validación contra verdad terreno manual.

## Arquitectura de Big Data (4 capas, estilo proyecto de referencia)

- **Capa Edge (el ESP32-S3):** el radio Wi-Fi captura CSI (HT20, 2.4 GHz, ~100 muestras/s) usando tráfico de ping/DNS al gateway como estímulo. En el chip corre el **detector neuronal** (perfil High Accuracy: MLP 8→24→12→1, 529 parámetros; alternativa Lightweight de 2 features con calibración de ~10 s). La decisión `idle|motion` se toma cada 250 ms **en el nodo**; el PC nunca procesa la señal cruda. Feedback local sin red: buzzer (beep en onset de movimiento, cooldown 5 s) + LED WS2812 (color por estado). El nodo publica además su **temperatura interna del die** (proxy de carga de cómputo) cada 30 s. Métricas de edge computing medidas en el chip vía diagnóstico nativo: **ms por detección, % CPU, heap libre, CSI paquetes/s, RSSI, canal Wi-Fi**. **Sin cámara y sin micrófono: el CSI crudo nunca sale del chip.**
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

**Preguntas analíticas que el dataset responde:**
1. ¿A qué horas del día se concentra la actividad del aula? ¿Puede un modelo simple predecir la ventana siguiente de 10 min a partir del histórico + clima?
2. ¿Cuál es la latencia real detección→tablero (punta a punta) y el costo de inferencia en el chip (ms/detección, % CPU)?
3. ¿Cuál es el recall y la tasa de falsos positivos LOCALES contra verificación manual binaria, y cómo se comparan con las métricas publicadas del detector?
4. ¿Cómo se correlacionan la carga de cómputo del edge (die temp) y las condiciones externas (Open-Meteo) con los patrones de actividad?

## Calidad del dato y privacidad

- **Validación contra verdad terreno manual:** el operador registra en planilla los intervalos de movimiento/quietud de cada sesión (caminar por la zona = motion; zona quieta = idle) y se mide matriz de confusión, recall, FPR y latencia del detector. Toda métrica se reporta **con su tamaño de muestra**; las cifras del vendor (F1 98,1–99,3 %) son de SU dataset y no se prometen como propias.
- **Privacidad por construcción (no por mitigación):** no hay cámara ni micrófono en el pipeline; el CSI crudo nunca sale del chip; el dataset contiene solo eventos de movimiento y métricas, sin datos personales identificables. Los datos de movimiento pueden revelar rutinas → aviso de consentimiento en el aula, minimización, broker con autenticación y dashboard solo en loopback.
- **Datos honestos:** todo fixture sintético de prueba se etiqueta `origen=sintetico`/`SIMULADO` en docs y dashboard; nunca se mezcla con captura real ni se presenta como real. El dataset consolidado exportable es solo real.

## Diferenciadores frente a un proyecto genérico de sensores

| Aspecto | Proyecto genérico (PIR/cámara) | AulaSense (este proyecto) |
|---|---|---|
| IA | Reglas/umbral en servidor o PIR sin IA | Detector neuronal (MLP 529 params) corriendo **en el ESP32-S3** (edge real, medible) |
| Privacidad | Cámara que graba personas | **Sin imágenes ni audio**: solo eventos; privacidad por construcción |
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
| C9 privacidad | Sin cámara/mic; CSI crudo no sale del chip; solo eventos |
| C10 validación manual | Verdad terreno binaria por sesión → matriz de confusión |
| C11 alerta audible/BLE | Buzzer GPIO14 + WS2812 + beep dashboard; BLE opcional |
| C12 métricas edge | Diagnóstico nativo: ms/detección, CPU %, heap, CSI pps, RSSI |
| C13 datos honestos | origen real/sintetico, SIMULADO etiquetado, nunca mezclado |
| C14 entregables código | firmware patch + pipeline + dataset + tablero + informe |

**Cumplimiento: 14/14** (C6 y C11 son deseables y también se cubren; la mayoría exigida queda superada).

> La especificación técnica detallada (pinout, contrato MQTT, esquema JSON/SQLite, comandos) está en `SPEC.md`; el plan de ejecución por fases y agentes está en `PLAN.md`; las fuentes con URL+fecha están en `docs/fuentes.md`.