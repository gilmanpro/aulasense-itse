# PLAN.md — AulaSense: sensing de movimiento/ocupación por Wi-Fi CSI con ML en el ESP32-S3

> Proyecto nuevo IoT2/ITSE (2026-11-12). Reemplaza al abandonado DriveGuard (`dashcam-ia/`, archivable
> por el orquestador a `iot2/archive/`). Plan maestro del `planificador`; lo ejecuta el `@1-orquestador`
> con el roster real. Checklist vivo: `../.tmp/checklist.md` (v23). Especificación: `SPEC.md`.
> Evidencia de decisión: `../.tmp/f23_research_tendencias.md` (analista + addendum verificación primaria)
> y `../.tmp/f23_research_viabilidad.md` (analista). Condiciones: `../.tmp/condiciones_curso.md` (C1–C14).
> Restricciones de diseño: `../.tmp/registro_riesgos_nuevo.md` (E1–E10).

## 1. Visión y público

**AulaSense** convierte la placa GOOUUU ESP32-S3-CAM v1.5 (N16R8) en un **sensor de movimiento/ocupación
dinámica sin cámara, sin micrófono y sin radar**: lee el Channel State Information (CSI) del radio Wi-Fi
de 2.4 GHz y ejecuta **en el chip** un detector neuronal (ESPectre, MLP 529 parámetros) que decide
`idle|motion` cada 250 ms. Los eventos JSON viajan por MQTT al broker `broker.gilman.pro`, una ingesta
en el PC los almacena en SQLite (dataset propio), un dashboard FastAPI en tiempo real los visualiza, y
un análisis pandas + sklearn predice la actividad del aula por franja horaria. Alerta audible local
(buzzer GPIO14) + visual (WS2812 GPIO48). Tres fuentes de datos: CSI + temperatura interna del S3 +
Open-Meteo (gratis, sin key).

**Público:** curso IoT2/ITSE (profesor + compañeros), demo en aula. **Costo: $0** (solo placa + router
Wi-Fi existente). **Novedad 2026:** ESPectre v3.2.0 publicado el 2026-11-11 (ayer), recomendado por
Espressif como community project en `esp-csi`; el sensing sin sensores ("no-camera privacy sensing") es
la narrativa 2026 de edge AI privado.

## 2. Por qué este proyecto y no otro (matriz de decisión verificada)

Matriz del analista de tendencias (fórmula declarada en su informe), re-verificada por el planificador
en fuente primaria. Puntaje mayor = mejor.

| Candidato | Novedad | Cond. curso | Viable HOY | Demo s/E1-E3 | Costo | Riesgo | **Total** |
|---|---|---|---|---|---|---|---|
| **(f) ESPectre Wi-Fi CSI ← ELEGIDO** | **5** | **11–12/12** | **SÍ ($0)** | 4 | **$0** | 2,5 | **32,0** |
| (b) Visión esp-dl objetos (finalista) | 2,5 | 9/12 | SÍ | 3 | $0 | 4 | 27,3 |
| (a) Audio TinyML INMP441 | 3 | 10/12 | NO (compra mic) | 4 | $1,5 | 2 | 26,0 |
| (d) Gemelo ambiental | 1 | 8/12 | PARCIAL | 4 | $3 | 1 | 25,0 |
| (c) Radar mmWave LD2410/50 | 2 | 8/12 | NO (compra) | 5 | $5–8 | 1,5 | 24,0 |
| (e) Edge-LLM/voz/Matter | 5 | 6/12 | NO | 2 | $6–12 | 5 | 17,5 |

**Desempate cualitativo declarado:** (b) queda segundo en bruto pero **revive la clase exacta de fallo
de DriveGuard** (visión que promete más de lo que el S3 entrega en vivo). Evidencia oficial verificada:
YOLO11n en S3 = 961 ms/frame (320², ~1 FPS) a 3906 ms (640², ~0,26 FPS); YOLO26n exige 16 MB PSRAM (la
placa tiene 8); el taller Espressif documenta 6–26 s/frame y pregunta retóricamente si tiene sentido con
cámara en vivo. **COCO/YOLO en tiempo real es INVIABLE en esta placa.** (b) solo sobreviviría como visión
ligera (espdet_pico ~7,7 FPS, hand_gesture ~13 FPS, color HSV), de menor novedad y riesgo residual de
cámara → queda como **fallback documentado**, no como núcleo. (a) audio es el finalista natural pero
**no viable HOY** (la placa no tiene micrófono; E4 exige fase 1 sin compras) → extensión futura.

**Ganador (f):** único novedoso que funciona HOY con $0, mata E1/E2/E9 por construcción (no hay cámara
ni fisiología ni encuadre), neutraliza E10 (pesos preentrenados y validados públicamente, F1 98,1–99,3 %;
el dataset propio es de EVENTOS para el análisis del curso, no de entrenamiento del detector).

## 3. Supuestos heredados y nuevos (numerados, visibles)

| # | Supuesto | Fuente/verificación | Estado |
|---|---|---|---|
| S1 | Hardware GOOUUU ESP32-S3-CAM v1.5 N16R8: 16 MB flash, 8 MB PSRAM octal, OV3660, buzzer GPIO14, WS2812 GPIO48, LED flash GPIO3, BLE 5.0, WiFi 2.4 GHz; SIN mic/IMU/sensores ambientales | dashcam-ia SPEC §1-2 verificado F0a/F1; analista viabilidad §1 | Heredado, verificado en disco |
| S2 | broker.gilman.pro TCP 14769 y WSS 443 alcanzables con auth (usuario `electiva`); 1883/1884/8083 filtrados; clave NUNCA impresa, en `.env` gitignored | E6; dashcam-ia F18 evidencia radio real | Heredado |
| S3 | PC Windows 11: `python` del PATH roto → `C:\Python315\python.exe` (3.15.2); sin Docker; `eim` instalado (v5.4 presente); flasheo COM6 USB-JTAG; PROHIBIDO USB device stack | E5; dashcam-ia AGENTS reglas 3-4 | Heredado |
| S4 | `eim` puede instalar ESP-IDF **v5.5.6** en esta máquina (desviación documentada de E5 v5.4; el SDK ESPectre requiere ≥5.5.3 y docs/CLI.md recomienda 5.5.6) | docs/CLI.md verificado 2026-11-12 | Nuevo → gate F1a; fallback: offline installer oficial |
| S5 | El tag `v3.2.0` de ESPectre existe y el firmware Native compila para esp32s3 con IDF 5.5.6 | releases (analista) + README/CLI verificados | Nuevo → gate F1b; fallback: ajustar tag de release o ruta librería Arduino |
| S6 | El módulo GOOUUU N16R8 exacto es compatible con ESPectre Native (soporte por familia de chip documentado; compatibilidad exacta del módulo NO verificada empíricamente) | README "Supported hardware: ESP32-S3" | Nuevo → gate F1c (prueba de vida) |
| S7 | La red Wi-Fi 2.4 GHz del aula/escritorio permite el tráfico del generador CSI (ping al gateway por defecto; si ICMP bloqueado → modos dns/dns_tcp/wifi_raw/external) | docs/SETUP.md + Native README verificados | Nuevo → gate F1c con swaps |
| S8 | El esquema MQTT en 14769 es TCP plano (`mqtt`) o TLS con CA pública (`mqtts`) — ambos soportados nativamente por Native; NO WebSocket ni CA privada | Native README + API.md verificados; dashcam-ia F18 conectó a 14769 | Nuevo → gate F1d; si CA privada → fallback puente local (decide orquestador) |
| S9 | Uso académico GPLv3 OK: se publica el fuente del derivado (parche trackeado + tag de vendor referenciado + LICENSE preservado); repo se entrega al profesor | README ESPectre "License: GPLv3" | Nuevo, declarado |
| S10 | ESPectre detecta **MOVIMIENTO**, no presencia estacionaria ni sala vacía ni conteo de personas (limitación honesta del vendor) | README "does not identify people, count them, prove that a room is empty" | Nuevo → framing honesto en docs/dashboard |
| S11 | La temperatura interna del S3 (die temp) es proxy de carga de cómputo, NO temperatura ambiente | analista tendencias (d); products.espressif.com | Nuevo → honesto en C6 |
| S12 | Los dos informes analista llegaron completos (notificaciones tardías); 0 llamadas pagadas usadas; follow-up por sessionID imposible (harness trunca a `ses`) — sin brecha de investigación | conversación 2026-11-12 | Heredado, resuelto |

## 4. Decisiones de arquitectura (D1–D12, con justificación y fuente)

- **D1 — Firmware base: ESPectre Native (fork académico GPLv3 vendored), no SDK-desde-cero.**
  Native ya trae MQTT JSON + HA Discovery + Direct HTTP + diagnósticos + provisioning Improv Serial +
  recuperación BOOT 3 s + OTA. Escribir desde cero con el SDK costaría fases y riesgo. *Por qué no
  ESPHome:* exige HA/YAML, menos control para el contrato del curso. *Por qué no Matter:* no expone el
  contrato MQTT JSON del curso y depende de un controller. *Por qué no librería Arduino:* la ruta IDF
  encaja con el patrón `eim` de E5; Arduino-ESP32 ≥3.3.7 queda como fallback documentado.
  Fuente: README + Native README verificados 2026-11-12.
- **D2 — ESP-IDF v5.5.6 vía `eim` (desviación documentada de E5 v5.4).** docs/CLI.md recomienda
  explícitamente 5.5.6; el SDK requiere ≥5.5.3. v5.4 queda instalado (no se toca el archive). Fallback:
  offline installer oficial. Fuente: docs/CLI.md verificado.
- **D3 — Contrato MQTT = topics nativos ESPectre con `topic_prefix=aulasense/v1/devices`.** La ingesta
  se adapta (mapeo reloj monótono→época, derivación de bordes). *Por qué no topics propios en firmware:*
  el contrato nativo está verificado, documentado y es estable (protocolo 1.0); cero riesgo de firmware.
  El prefijo propio aísla de otros dispositivos ESPectre en el broker compartido. Fuente: docs/API.md.
- **D4 — Métricas edge (C12) vía `read_diagnostics` nativo, sondeado cada 30 s desde la ingesta.**
  Campos nativos cubren toda C12: `detection_avg_us/max_us` (ms/inferencia), `runtime_load_percent`
  (CPU %), `free_memory_kb`/`minimum_free_memory_kb` (heap), `csi_accepted_pps`/`generator_pps` (pps CSI),
  `wifi_rssi_dbm`/`wifi_channel`. Sin parche de firmware para métricas. Fuente: docs/API.md §diagnostics.
- **D5 — Feedback local = parche mínimo GPLv3 (`app_feedback.c`): buzzer GPIO14 (LEDC) + WS2812 GPIO48
  + publicación de die temp.** C11 (alerta audible) en el dispositivo sin red; die temp cubre C6 fuente 2
  con $0. Guardas de compilación: nunca GPIO 19/20 (USB), 43/44 (UART0); `#error` si se configura un pin
  prohibido. **BLE opcional** (sub-hito, default n): NimBLE notify solo si las métricas CSI no se degradan
  (coexistencia WiFi+BLE puede afectar el capture). Fuente: analista viabilidad §1 (GPIO) + Native README.
- **D6 — Demo sin humanos frágiles (E3): estímulo trivial reproducible.** Operador camina por la zona o
  mueve un objeto → `motion`; zona quieta → `idle`. Sin cara, sin encuadre, sin fisiología. Monitor /
  Direct HTTP + dashboard WS + log serial = feedback EN VIVO (E8). Honestidad: `idle` ≠ sala vacía (S10).
- **D7 — Multi-fuente (C6) = CSI + die temp S3 + Open-Meteo.** Open-Meteo gratis sin key (uso no
  comercial), fetcher en el PC (hourly/current). 3 fuentes, $0. Die temp ≠ ambiente (S11, honesto).
  Fuente: open-meteo.com/en/docs (analista, verificado).
- **D8 — Análisis (C5) = EDA pandas + clasificador sklearn simple** (predice `motion` en la ventana
  siguiente de 10 min desde hora/día/actividad móvil + features Open-Meteo), entrenado SOLO con dataset
  propio real. *Por qué no deep learning:* tamaño muestral honesto; el curso pide "modelo predictivo
  simple". Sin promesas de accuracy (E10); métricas reportadas con tamaño de muestra.
- **D9 — Privacidad (C9/E9) por construcción.** Native NO inicializa la cámara (verificar sdkconfig:
  sin driver camera); no hay mic; el CSI crudo NUNCA sale del chip en la ruta crítica (el modo
  `GET /csi` collection es opcional, solo investigación, y pausa los eventos derivados — NO se usa).
  Solo salen eventos de movimiento + métricas. Los datos de movimiento pueden revelar rutinas →
  aviso de consentimiento en el aula, minimización (sin identidades), broker con auth, dashboard
  127.0.0.1. Fuente: README ESPectre §Security + API.md §CSI collection.
- **D10 — Seguridad heredada (E7), código nuevo.** FastAPI bind 127.0.0.1, Origin guard en `/ws` y
  `/internal/*`, `esc()` XSS, SQL parametrizado, cero endpoints de imágenes (no existen imágenes),
  MQTT auth desde `.env` (gitignored), secretos nunca impresos, credenciales WiFi por NVS/Kconfig
  generado (gitignored) o `ESPECTRE_WIFI_PASSWORD` por env (nunca argv). Fuente: dashcam-ia AGENTS
  §Seguridad (controles certificados en su gate F10) + CLI.md §provision.
- **D11 — Backend contract-first en paralelo (F2 ∥ F1).** SPEC fija el contrato MQTT antes de que el
  firmware exista; F2 construye ingesta/dashboard/tools con fixtures sintéticos etiquetados
  `origen=sintetico` (C13). Maximiza paralelismo, parte la ruta crítica. F3 verifica con nodo real.
- **D12 — Gate de prueba de vida temprana (F1c) con regla de 2 intentos + Plan B.** Lección DriveGuard:
  ~20 fases antes de la primera prueba física. Aquí el fenómeno físico (CSI→motion) se demuestra en
  placa ANTES de construir dashboard/análisis/docs. Si F1c falla 2 intentos documentados (con swaps:
  traffic ping→dns→wifi_raw, detector lightweight↔high_accuracy, colocación 3–8 m, canal/BSSID) →
  **Plan B: el orquestador decide** swap al finalista (b) visión ligera; F1 se detiene, no se hunden más
  fases. Fuente: registro_riesgos_nuevo.md §Regla de verificación temprana.

## 5. Arquitectura (4 capas, dependencias)

```
┌─ CAPA 1 · EDGE (ESP32-S3 GOOUUU, $0) ─────────────────────────────────────┐
│ ESPectre Native v3.2.0 (fork GPLv3 vendored) + parche app_feedback.c      │
│  · CSI capture HT20 2.4 GHz (~100 muestras/s, tráfico ping/dns/wifi_raw)  │
│  · Detector ML EN EL CHIP: High Accuracy MLP 8→24→12→1 (529 params)       │
│    o Lightweight (logística 2 features); decisión cada 250 ms             │
│  · Feedback local: buzzer GPIO14 (LEDC) + WS2812 GPIO48 + die temp        │
│  · BLE NimBLE notify OPCIONAL (default n, gate coexistencia)              │
│  · SIN cámara, SIN mic, SIN USB device stack; CSI crudo no sale del chip  │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ MQTT JSON (mqtt/mqtts) broker.gilman.pro:14769
                │ topic_prefix aulasense/v1/devices/{device_id}/…
                │ + Direct HTTP :62587 (LAN, Monitor/diagnósticos, feedback vivo)
┌───────────────▼─ CAPA 2 · TRANSPORTE ───────────────────────────────────┐
│ broker.gilman.pro (auth electiva, .env gitignored) · WiFi 2.4 GHz        │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ paho-mqtt (suscriptor) + read_diagnostics cada 30 s
┌───────────────▼─ CAPA 3 · SERVER PC (C:\Python315, sin Docker) ─────────┐
│ ingesta_mqtt.py: valida esquema → mapea monótono→época → deriva bordes   │
│   → SQLite WAL (writer único) + CSV export                               │
│ dashboard FastAPI 127.0.0.1:8000: WS live + Chart.js + KPIs + validación │
│   (Origin guard, esc() XSS, SQL param, cero endpoints de imágenes)       │
│ fuentes/openmeteo.py: pronóstico gratis sin key → fuentes_externas       │
│ tools: start_demo/stop_demo, gen_firmware_config, config_mqtt, lee_serial│
│ ble/receptor_ble.py (opcional, bleak)                                    │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ dataset propio real (SQLite/CSV)
┌───────────────▼─ CAPA 4 · ANÁLISIS + DOCS ──────────────────────────────┐
│ analysis/eda.py (pandas) + modelo_ocupacion.py (sklearn) → assets/*.png  │
│ docs: PROPUESTA.md, INFORME.docx, PRESENTACION.pptx (4-documentos)       │
│ Validación manual (C10): verdad terreno binaria por sesión → matriz      │
└─────────────────────────────────────────────────────────────────────────┘
```

Dependencia de capas: 1→2→3→4 (datos fluyen arriba). El CONTRATO (SPEC §6-§8) se fija antes de
implementar, permitiendo F2 (capa 3) en paralelo con F1 (capa 1).

## 6. MAPA DE FASES (agente · sub-despachos · herramientas · entregable · criterio · deps · background)

> Regla: TODA delegación va `background: SI` (sin excepción). Las dependencias explícitas deciden
> CUÁNDO se lanza la siguiente (al llegar el reporte), nunca que se espere en foreground. Los grupos
> `[PARALELO]` se lanzan en un solo bloque. Techos steps: ejecutor 500, dev 400, especialistas 300.
> Fases que exceden el techo se trocean en hitos reanudables desde el checklist. Commits = manos
> propias del ORQUESTADOR (el ejecutor NO commitea). El checklist lo marca SOLO el orquestador.

### F0 · INICIO repo — `@1-orquestador` (manos propias, NO delegación)
- **Objetivo:** archivar el proyecto abandonado e inicializar el repo nuevo.
- **Herramientas:** git, shell (manos propias del orquestador).
- **Entregable:** `iot2/archive/dashcam-ia/` (movido, historia preservada) + `aulasense/.git/` + commit inicial del andamiaje.
- **Criterio:** `Test-Path iot2/archive/dashcam-ia/PLAN.md` = True; `git -C aulasense log --oneline` ≥ 1; `git -C aulasense status` limpio.
- **Deps:** ninguna (andamiaje ya en disco). **background:** — (manos propias).
- **Nota:** el planificador NO mueve dashcam-ia ni hace git (límite duro); lo hace el orquestador al aprobar.

### F1 · PILOTO CSI — gate de prueba de vida temprana — `@2-ejecutor` (instancia A)
- **Objetivo:** demostrar el fenómeno físico (CSI→motion) en la placa HOY, antes de construir el resto.
- **Sub-despachos:** `5-dev` × 4 hitos secuenciales (F1a→F1b→F1c→F1d); `3-analista` solo si F1c falla (survey RF del aula, 1 ronda).
- **Herramientas:** edit/bash (eim, idf.py, esptool, git clone), read, webfetch.
- **Hitos y entregables (ruta exacta):**
  - **F1a** toolchain+vendor: `eim install 5.5.6` + `eim run "idf.py --version" v5.5.6`; clone `git clone --branch v3.2.0 --depth 1 https://github.com/francescopace/espectre.git aulasense/firmware/espectre`. → `iot2/.tmp/f1a_idf.log` + `aulasense/firmware/espectre/` (gitignored). **Criterio:** log muestra `v5.5.6`; repo clonado con LICENSE. ~80 steps.
  - **F1b** build+flash: build Native app esp32s3 (`eim run "idf.py set-target esp32s3/build" v5.5.6` en `src/cpp/frontend/native/app`; fallback `espectre.cmd native build --chip s3 --backend local`); verificar sdkconfig (SIN USB device, SIN camera, PSRAM octal 8 MB, flash 16 MB); flash COM6 JTAG. → `iot2/.tmp/f1b_build.log`, `f1b_flash.log`, `f1b_boot.log`. **Criterio:** "Project build complete"; flash OK; boot serial sin pánico; sdkconfig verificado. ~150 steps.
  - **F1c** provisioning+CSI+primera detección: WiFi por Kconfig defaults generados desde `.env` (`tools/gen_firmware_config.py`) o Improv Serial; verificar `csi_accepted_pps>0`, occupancy ≥85 % (Direct HTTP diagnostics); ≥10 ciclos caminar→motion / quieto→idle. → `iot2/.tmp/f1c_piloto.md`, `f1c_serial.log`, `f1c_diagnostics.json`. **Criterio:** ≥8/10 ciclos correctos (umbral piloto honesto, NO promesa vendor); perfil detector elegido; colocación documentada. ~150 steps. **GATE D12:** 2 intentos fallidos con swaps → Plan B (orquestador decide).
  - **F1d** MQTT broker: `tools/config_mqtt.py` (lee `.env`, `PATCH /mqtt` con Origin header, nunca imprime clave) → scheme mqtt/mqtts, host broker.gilman.pro, port 14769, user electiva, topic_prefix aulasense/v1/devices; subscriptor paho externo autenticado captura motion+health. → `iot2/.tmp/f1d_mqtt.log`, `f1d_eventos.json`. **Criterio:** JSON con eventos motion reales del nodo vía broker; `configured:true`. ~80 steps.
- **Deps:** F0. **background:** SI. **Total ~460 steps** (≤500 ejecutor; hitos reanudables, cada uno ≤400 dev).

### F2 · BACKEND PC (contract-first) — `@2-ejecutor` (instancia B) — **[PARALELO con F1]**
- **Objetivo:** construir capa 3 completa contra el contrato SPEC (sin firmware; fixtures sintéticos).
- **Sub-despachos:** `5-dev` instancia A (ingesta+SQLite+época+bordes+diagnostics poller+CSV), `5-dev` instancia B (dashboard FastAPI+WS+Chart.js+Origin guard+static), `5-dev` instancia C (tools start/stop_demo+gen_firmware_config+config_mqtt+lee_serial + openmeteo fetcher). **Rutas disjuntas** (server/ingesta, server/dashboard, tools/+server/fuentes) → sin solapamiento.
- **Herramientas:** edit/bash (`C:\Python315\python.exe`, venv, pytest), read.
- **Entregable:** `server/ingesta/ingesta_mqtt.py` + `esquema.py`; `server/datos/aulasense.db` (esquema §8) + `export_evaluaciones.csv`; `server/dashboard/app.py` + `static/index.html` + `static/app.js`; `server/fuentes/openmeteo.py`; `tools/start_demo.ps1`, `stop_demo.ps1`, `gen_firmware_config.py`, `config_mqtt.py`, `lee_serial.py`; `server/tests/test_ingesta.py`, `test_dashboard.py`, `test_openmeteo.py`; `server/requirements.txt`; `iot2/.tmp/f2_backend.md`.
- **Criterio:** pytest verde (≥15 tests: validador 201/422, mapeo época, derivación bordes, Origin guard 403, SQL param, export CSV); dashboard HTTP 200 en 127.0.0.1:8000; evento sintético (`origen=sintetico`) vía `POST /internal/evento` aparece en WS <2 s; openmeteo fetcher guarda fila real (API gratis). **Sin nodo real** (integra en F3).
- **Deps:** F0 únicamente (contrato fijado en SPEC). **background:** SI. ~350/350/250 steps por dev (≤400); ejecutor coordina ~100.

### F3 · INTEGRACIÓN E2E + FEEDBACK LOCAL — `@2-ejecutor`
- **Objetivo:** unir nodo real ↔ backend; añadir feedback local (buzzer/WS2812/die temp) y panel de validación.
- **Sub-despachos:** `5-dev` firmware (parche `app_feedback.c` + Kconfig + guardas, aplicar a vendor, rebuild+flash) + `5-dev` server (panel validación + session logger + receptor BLE opcional).
- **Herramientas:** edit/bash (eim, idf.py, pytest), read.
- **Entregable:** `firmware/parche_aulasense/app_feedback.c`, `.h`, `Kconfig.projbuild`, `CMakeLists.txt`, `README.md`; `iot2/.tmp/f3_build.log`, `f3_flash.log`, `f3_e2e.md`, `f3_metricas.json`; panel validación en dashboard (`/api/validacion` + static); `server/ble/receptor_ble.py` (opcional).
- **Criterio:** evento motion del nodo → SQLite+dashboard <2 s (latencia medida serial↔WS); buzzer audible en onset (confirmado por operador o log LEDC); WS2812 cambia de color (log); filas `die_temp` en DB; filas Open-Meteo en DB; sub-hito BLE solo si `csi_accepted_pps` no cae vs F1c (comparar f3_metricas.json).
- **Deps:** F1 + F2. **background:** SI. ~300 firmware + ~200 server.

### F4 · DATASET PROPIO + VALIDACIÓN MANUAL (C3/C10) — `@2-ejecutor`
- **Objetivo:** acumular sesiones reales y medir calidad contra verdad terreno binaria.
- **Sub-despachos:** `3-analista` (protocolo de validación + cálculo de métricas desde DB+CSV manual) + `4-documentos` (plantilla de registro de sesión). Operador ejecuta las sesiones físicas (orquestador coordina con usuario).
- **Herramientas:** read/bash (python, pandas), edit (docs).
- **Entregable:** `server/datos/aulasense.db` con ≥3 sesiones reales (≥20 min c/u, ≥2000 evaluaciones reales — objetivo honesto ajustable); `analysis/dataset/consolidado.csv` (SOLO `origen=real`); `docs/validacion_sesion1.csv`…`sesionN.csv` (intervalos motion/id manual con timestamps); `docs/plantilla_validacion.md`; `iot2/.tmp/f4_metricas.md`.
- **Criterio:** CSV existe con filas reales ≥ objetivo; matriz de confusión + recall + FPR + latencia calculadas del join evaluaciones↔intervalos manuales; nota de tamaño muestral; CERO sintético mezclado; sesiones en ≥2 días distintos (diversidad horaria para el modelo).
- **Deps:** F3. **background:** SI. ~200 steps + tiempo físico del operador (las sesiones son a ritmo humano).

### F5 · [PARALELO] ANÁLISIS + DOCS BASE — deps F4
- **F5a · EDA + modelo predictivo (C5)** — `@2-ejecutor` → `5-dev`.
  - Entregable: `analysis/eda.py`, `analysis/modelo_ocupacion.py`, `analysis/run_analisis.ps1`, `analysis/informe_eda.md`, `assets/*.png` (≥6 figuras).
  - Criterio: scripts corren con venv `C:\Python315`; ≥6 figuras existen; modelo entrenado SOLO con datos reales; target = motion en ventana siguiente 10 min; features hora/día/actividad móvil + Open-Meteo; métricas (accuracy/precision/AUC) reportadas CON tamaño de muestra y nota honesta; importancia de features graficada.
  - background: SI. ~300 steps.
- **F5b · Docs base + guion demo** — `@4-documentos` (despacho directo del orquestador, acorta ruta crítica).
  - Entregable: `docs/PROPUESTA.md` (final, estilo profesor), `docs/guion_demo.md`, `docs/README.md` (índice), `docs/fuentes.md` (actualizado con evidencia de ejecución).
  - Criterio: archivos existen, coherentes con SPEC/PLAN; tabla C1–C14 completa; guion con pasos cronometrados + fallbacks; fuentes con URL+fecha.
  - background: SI. ~200 steps.

### F6 · [PARALELO] DOCS FINALES + ENSAYO DEMO — deps: F6a←F5a+F5b+F4; F6b←F3+F4
- **F6a · INFORME.docx + PRESENTACION.pptx (C7)** — `@4-documentos` (skill `ofimatica`).
  - Entregable: `docs/INFORME.docx`, `docs/PRESENTACION.pptx`, `tools/generadores/gen_informe.py`, `gen_presentacion.py` (reproducibles).
  - Criterio: archivos abren (validados con python-docx/python-pptx); contienen figuras reales de `assets/`, métricas reales de F4/F5, sección de limitaciones honestas (S10/S11), SIMULADO etiquetado donde aplique; generadores reproducen el entregable.
  - background: SI. ~250 steps.
- **F6b · Ventana de demo en vivo (ensayo completo, C8/E8)** — `@2-ejecutor` → `5-dev` (soporte) + operador.
  - Entregable: `iot2/.tmp/f6_demo_ensayo.md` (log cronometrado: start_demo → estímulo → dashboard+buzzer+LED → stop_demo; latencia medida; feedback en vivo confirmado); `tools/start_demo.ps1` verificado end-to-end.
  - Criterio:** demo completa en <5 min de setup; motion detectado en vivo con estímulo del operador; dashboard WS live; buzzer audible; stop_demo limpia PIDs (`.tmp/demo_pids.json`); ≥1 ensayo exitoso registrado.
  - background: SI. ~150 steps.

### GATE · ESPECIALISTAS — `@1-orquestador` fan-out DIRECTO (NO bajo ejecutor: subagent_depth=3)
- **[PARALELO]** 7 especialistas, cada uno `background: SI`, ≤300 steps, PASS/FAIL con evidencia `archivo:linea` + fix en su dominio:
  - `seguridad`: SAST, escaneo de secretos (git grep), Origin guard, MQTT auth, exposición del broker, política Origin de Direct HTTP, `.env` gitignored.
  - `rendimiento`: estabilidad CSI pps en sesión, `detection_avg_us` vs claim vendor, latencia dashboard, throughput SQLite WAL, fugas de memoria (heap low-water).
  - `arquitectura`: fronteras de 4 capas, edge-first (sin ML en PC salvo análisis), adherencia contrato SPEC↔código, higiene del fork vendor.
  - `documentacion`: coherencia Word/PPTX/README/SPEC/PLAN, citas URL+fecha, trazabilidad C1–C14.
  - `limpieza`: código muerto, higiene `.tmp`, cobertura gitignore, archivos stray, clone vendor gitignored.
  - `pruebas`: cobertura pytest, test e2e replay, fuzz del validador, casos borde (broker caído, nodo offline, deriva de época).
  - `bd`: normalización esquema SQLite, índices, integridad export CSV, checkpoint WAL, idempotencia de migración.
  - **`devops` OMITIDO** — sin Docker/CI por restricción dura E5 (se anota el porqué; no aplica).
- **Entregable:** `iot2/.tmp/gate_<especialista>.md` (×7) con veredicto + evidencia + fixes.
- **Criterio:** los 7 PASS (o FAIL corregido y re-verificado en su dominio).
- **Deps:** F6. **background:** SI (cada especialista).

### F7 · CIERRE — `@1-orquestador` (manos propias)
- **Objetivo:** validar todo contra disco y cerrar el repo.
- **Herramientas:** git, shell, read (manos propias).
- **Entregable:** `iot2/.tmp/cierre_v23.md` (reporte: cada entregable verificado en disco, gate PASS, commit final).
- **Criterio:** `git -C aulasense log` muestra commit final; `git status` limpio; checklist v23 todo `[x]`; reporte lista cada ruta verificada.
- **Deps:** GATE. **background:** — (manos propias).

### Extensiones futuras (POST-curso, NO en ruta crítica, documentadas)
- (a) Audio TinyML INMP441 (~$1,5) como segunda fuente de sensing no visual (finalista de la matriz).
- (c) Radar LD2410 (~$3–6) como telemetría de presencia complementaria.
- Grabación de dataset CSI crudo (`espectre collect`) para investigación/reentrenamiento del detector.
- BLE en el nodo (si no se activó en F3) + segunda placa ESPectre para sensing pareado (geometría elegible).
- Integración Matter / Home Assistant (el firmware ya la soporta).

## 7. Trazabilidad condiciones del curso (C1–C14 → fase → entregable)

| Cond | Cómo se cumple | Fase | Entregable verificable |
|---|---|---|---|
| C1 edge-first | Detector MLP 529 params EN el S3; PC solo ingesta/almacena/visualiza/analiza | F1,F3 | firmware + f1c_diagnostics.json (detection_avg_us) |
| C2 MQTT JSON | Topics nativos motion/health/sensing, esquema validado | F1d,F2 | f1d_eventos.json + ingesta/esquema.py |
| C3 dataset propio | Evaluaciones+eventos reales en SQLite, export CSV | F4 | analysis/dataset/consolidado.csv (origen=real) |
| C4 dashboard realtime | FastAPI WS + Chart.js, KPIs, feed vivo | F2,F3 | dashboard/app.py + static/index.html |
| C5 EDA+modelo | pandas EDA + sklearn clasificador actividad | F5a | analysis/eda.py, modelo_ocupacion.py, assets/*.png |
| C6 multi-fuente | CSI + die temp S3 + Open-Meteo (3 fuentes, $0) | F3,F5a | fuentes_externas table + openmeteo.py + die_temp |
| C7 Word+PPTX | 4-documentos con skill ofimatica | F6a | docs/INFORME.docx, PRESENTACION.pptx |
| C8 demo en vivo | Guion cronometrado + ensayo completo | F6b | f6_demo_ensayo.md + guion_demo.md |
| C9 privacidad | Sin cámara/mic; CSI crudo no sale del chip; solo eventos | F1,F3 | sdkconfig sin camera + docs privacidad |
| C10 validación manual | Verdad terreno binaria por sesión → matriz confusión | F4 | validacion_sesionN.csv + f4_metricas.md |
| C11 alerta audible/BLE | Buzzer GPIO14 + WS2812 + dashboard beep; BLE opcional | F3 | app_feedback.c + receptor_ble.py (opt) |
| C12 métricas edge | read_diagnostics nativo (ms, CPU%, heap, pps, RSSI) | F2,F3 | telemetria table + f3_metricas.json |
| C13 datos honestos | origen real/sintetico, SIMULADO etiquetado, nunca mezclado | F2,F4,F5 | validador + filtro CSV + docs |
| C14 entregables código | firmware patch + pipeline + dataset + tablero + informe | todas | repo aulasense/ completo |

**Cumplimiento: 14/14** (C6 y C11 son deseables y se cubren; el resto obligatorias). Mayoría exigida: superada.

## 8. Riesgos E1–E10 → mitigaciones (mapeo al diseño AulaSense)

| # | Restricción | Mitigación en AulaSense | Fase que la verifica |
|---|---|---|---|
| E1 | Cero ML de rostros/fisiología | Detector CSI (MLP sobre features de RF), sin cara/ojo. Eliminado por construcción | F1c |
| E2 | Cero dependencia de encuadre | Sin cámara en el pipeline. Eliminado por construcción | F1b (sdkconfig sin camera) |
| E3 | Demo sin humanos frágiles | Estímulo trivial: operador camina/mueve objeto; zona quieta→idle. Monitor+WS+serial en vivo | F6b |
| E4 | Hardware HOY, $0 | Solo placa GOOUUU + router Wi-Fi existente; sin compras en ruta crítica | F1 |
| E5 | Windows/python/eim/COM6/sin USB | IDF 5.5.6 vía eim (S4); C:\Python315; consola UART en S3 (sin TinyUSB); sin USB device stack; sdkconfig verificado pre-flash; recuperación BOOT 3 s | F1a,F1b |
| E6 | Broker 14769/auth/secretos | .env gitignored; config_mqtt.py lee .env nunca imprime; topic_prefix aulasense; scheme verificado | F1d |
| E7 | Heredar PATRÓN no código | Arquitectura 4 capas + controles de seguridad heredados; código nuevo adaptado a CSI; cero firmware de caras copiado | F2,F3 |
| E8 | Entregables en disco + feedback vivo | Cada fase: ruta exacta + criterio objetivo; Monitor/Direct HTTP + dashboard WS + serial en vivo; checklist lo marca el orquestador | todas |
| E9 | Privacidad / sin imágenes | Sin cámara/mic; CSI crudo no sale del chip (collection opcional NO usada); solo eventos+metricas; consentimiento+minimización | F1,F3 |
| E10 | Sin promesas de accuracy con dataset largo | Pesos vendor preentrenados (F1 98,1–99,3 % publicado); dataset propio es de EVENTOS (no entrena detector); piloto F1c mide recall local honesto; modelo con nota de tamaño muestral | F1c,F4,F5a |

### Riesgos nuevos del dominio CSI
- **R1 sensibilidad RF/colocación** (aula concurrida, canal saturado) → piloto F1c temprano; swaps traffic ping→dns→dns_tcp→wifi_raw→external; colocación 3–8 m del AP fuera de metal; BSSID pin; occupancy ≥85 %; regla 2 intentos → Plan B (D12).
- **R2 fricción IDF 5.5.6/build** → hitos F1a/F1b; fallback offline installer / ajustar tag / ruta Arduino.
- **R3 reloj monótono→época / reboot** → ingesta re-estima `epoca_boot` al resetear uptime (patrón heredado del fix de reloj DriveGuard); flag `hora_sinc`; fallback `recibido_en`.
- **R4 framing honesto motion≠presencia** → docs/dashboard etiquetan "movimiento/ocupación dinámica", `idle`≠sala vacía explícito (S10).
- **R5 cumplimiento GPLv3** → parche trackeado + tag vendor referenciado + LICENSE preservado; fuente disponible al profesor (S9).
- **R6 colisión en broker compartido** → `topic_prefix=aulasense/v1/devices` aísla.
- **R7 coexistencia BLE degrada CSI** → BLE opcional default n; activar solo si métricas CSI no caen (gate F3).
- **R8 notificaciones perdidas (harness)** → entregables en disco + orquestador verifica cada paso (E8); no depender del reporte del worker.

## 9. Preguntas abiertas (NO bloqueantes)
- **P1:** coordenadas exactas del aula para Open-Meteo (`OPENMETEO_LAT/LON` en `.env`, placeholder vacío). El fetcher degrada con elegancia si vacío (C6 igual cubierta por die temp). El operador/usuario las rellena cuando las conozca.
- **P2:** esquema MQTT en 14769 (plano vs TLS) — se resuelve empíricamente en F1d; ambos soportados nativamente. Si CA privada (no soportada) → fallback puente local, decide orquestador.
- **P3:** aviso de consentimiento para la demo en aula (coordinación profesor/operador) — tarea documental en F5b, no bloquea.

## 10. Qué necesita el planificador del orquestador
1. **Archivar** `dashcam-ia/` → `iot2/archive/dashcam-ia/` (F0, manos propias del orquestador; el planificador NO lo mueve).
2. **git init** en `aulasense/` + commit del andamiaje (F0, manos propias; el planificador NO hace git).
3. **Delegar F1** (piloto CSI, gate de prueba de vida) y **F2** (backend contract-first) en paralelo, background SI.
4. Confirmar supuestos S4–S8 en los gates tempranos; si F1c falla 2 intentos → decidir Plan B (finalista b).
5. Rellenar `.env` real (WiFi + clave broker) desde `.env.example` — NUNCA en el repo ni impreso.