# PLAN.md — AulaSense: sensing de movimiento/ocupación por Wi-Fi CSI con ML en el ESP32-S3

> Proyecto nuevo IoT2/ITSE (2026-11-12). Reemplaza al abandonado DriveGuard (`dashcam-ia/`, archivable
> por el orquestador a `iot2/archive/`). Plan maestro del `planificador`; lo ejecuta el `@1-orquestador`
> con el roster real. Checklist vivo: `../.tmp/checklist.md` (**v25**; v24 archivada en `checklist_pre_v25.md`). Especificación: `SPEC.md`.
> Evidencia de decisión: `../.tmp/f23_research_tendencias.md` (analista + addendum verificación primaria)
> y `../.tmp/f23_research_viabilidad.md` (analista). Condiciones: `../.tmp/condiciones_curso.md` (C1–C14).
> Restricciones de diseño: `../.tmp/registro_riesgos_nuevo.md` (E1–E10).

## 1. Visión y público

**AulaSense** convierte la placa GOOUUU ESP32-S3-CAM v1.5 (N16R8) en un **sensor de movimiento/ocupación
dinámica** que lee el Channel State Information (CSI) del radio Wi-Fi de 2.4 GHz y ejecuta **en el chip**
un detector neuronal (ESPectre, MLP 529 parámetros) que decide `idle|motion` cada 250 ms. Los eventos JSON
viajan por MQTT al broker `broker.gilman.pro`, una ingesta en el PC los almacena en SQLite (dataset propio),
un dashboard FastAPI en tiempo real los visualiza, y un análisis pandas + sklearn predice la actividad del
aula por franja horaria. Alerta audible local (buzzer GPIO14) + visual (WS2812 GPIO48). Fuentes de datos:
CSI + temperatura interna del S3 + Open-Meteo (gratis, sin key). **Sin micrófono, sin radar.**
**F24 (orden del dueño, 2026-11-12, §11):** se añade **evidencia de imagen local** (1 JPEG QVGA al onset de
motion, cooldown 15 s, subida HTTP al servidor local, galería loopback, retención configurable),
**provisioning AP de fallback con ruta privada**, **IA de visión persona OPCIONAL spike-gated** (la IA
principal YA es el detector CSI), y **recopilación big data académica** (todas las fuentes al consolidado).
La privacidad se re-enmarca a **acotada local-only** (C9 cumple-parcial): imágenes solo al servidor local,
nunca nube/broker público, consentimiento de aula, sin caras-ML.

**Público:** curso IoT2/ITSE (profesor + compañeros), demo en aula. **Costo: $0** (solo placa + router
Wi-Fi existente). **Novedad 2026:** ESPectre v3.2.0 publicado el 2026-11-11 (ayer), recomendado por
Espressif como community project en `esp-csi`; el sensing por Wi-Fi CSI (sin sensores dedicados) es la
narrativa 2026 de edge AI. **F24 matiza el "no-camera":** el detector CSI sigue sin necesitar cámara,
pero el proyecto añade evidencia de imagen QVGA acotada local-only (D-F24-1, §11) — privacidad acotada
por mitigación, no por construcción.

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
| S5 | El tag `3.2.0` de ESPectre existe (SIN prefijo v; `v3.2.0` da 404 — verificado F1a) y el firmware Native compila para esp32s3 con IDF 5.5.6 | releases (analista) + README/CLI verificados | Nuevo → gate F1b; fallback: ajustar tag de release o ruta librería Arduino |
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
- **D9 — Privacidad (C9/E9) por construcción.** *(SUPERSEDED por D-F24-1, §11: el dueño revocó la regla
  "cero imágenes" el 2026-11-12 → privacidad acotada local-only. Se conserva el texto histórico como
  referencia de la decisión original.)* Native NO inicializaba la cámara; el CSI crudo NUNCA sale del chip
  en la ruta crítica. Solo eventos + métricas. Consentimiento, minimización, broker auth, dashboard 127.0.0.1.
- **D10 — Seguridad heredada (E7), código nuevo.** FastAPI bind 127.0.0.1, Origin guard en `/ws` y
  `/internal/*`, `esc()` XSS, SQL parametrizado, **galería de imágenes SOLO loopback + receptor de
  upload :8001 con Bearer token (F24, D-F24-3 — reemplaza la regla v23 "cero endpoints de imágenes")**,
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
│ ESPectre Native 3.2.0 (fork GPLv3) + parche: app_feedback F3 + módulos F24│
│  · CSI capture HT20 2.4 GHz (~100 muestras/s, tráfico ping/dns/wifi_raw)  │
│  · Detector ML EN EL CHIP: High Accuracy MLP 8→24→12→1 (529 params)       │
│    o Lightweight (logística 2 features); decisión cada 250 ms             │
│  · Feedback local: buzzer GPIO14 (LEDC) + WS2812 GPIO48 + die temp        │
│  · BLE NimBLE notify OPCIONAL (default n, gate coexistencia)              │
│  · Cámara OV3660 SOLO bajo AULASENSE_IMAGEN=y (F24): 1 JPEG QVGA al onset │
│  · CSI crudo no sale del chip; sin mic; sin USB device stack              │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ MQTT JSON (mqtt/mqtts) broker.gilman.pro:14769
                │ topic_prefix aulasense/v1/devices/{device_id}/…
                │ + Direct HTTP :62587 (LAN, Monitor/diagnósticos, feedback vivo)
                │ + F24: imagen QVGA → HTTP POST :8001 directo al server local
                │   (Bearer token, SOLO LAN; NUNCA por MQTT ni a la nube)
┌───────────────▼─ CAPA 2 · TRANSPORTE ───────────────────────────────────┐
│ broker.gilman.pro (auth electiva nodo / srv-electiva ingesta, .env)       │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ paho-mqtt (suscriptor srv-electiva) + read_diagnostics cada 30 s
┌───────────────▼─ CAPA 3 · SERVER PC (C:\Python315, sin Docker) ─────────┐
│ ingesta_mqtt.py: valida esquema → mapea monótono→época → deriva bordes   │
│   → SQLite WAL (writer único) + CSV export                               │
│ receptor_imagen.py :8001 (F24, LAN opt-in, Bearer token → cola writer)   │
│ dashboard FastAPI 127.0.0.1:8000: WS live + Chart.js + KPIs + validación │
│   + galería F24 SOLO loopback (Origin guard, esc() XSS, SQL param)       │
│ fuentes/openmeteo.py: pronóstico gratis sin key → fuentes_externas       │
│ tools: start_demo/stop_demo, gen_firmware_config, config_mqtt, lee_serial│
│   + purga_imagenes.py (F24: retención 30 d, imagen_purgada, fila intacta)│
│ ble/receptor_ble.py (opcional, bleak)                                    │
└───────────────┬─────────────────────────────────────────────────────────┘
                │ dataset propio real (SQLite/CSV)
┌───────────────▼─ CAPA 4 · ANÁLISIS + DOCS ──────────────────────────────┐
│ analysis/eda.py (pandas) + modelo_ocupacion.py (sklearn) → assets/*.png  │
│   + export_consolida.py / smoke_consolida.py (F24: consolidado §10.1,    │
│     una fila por evento con joins; heatmap_actividad_hora.png)           │
│ docs: PROPUESTA.md, INFORME.docx, PRESENTACION.pptx (4-documentos)       │
│ Validación manual (C10): verdad terreno binaria por sesión → matriz      │
└─────────────────────────────────────────────────────────────────────────┘
```

Dependencia de capas: 1→2→3→4 (datos fluyen arriba). El CONTRATO (SPEC §6–§10 + F24 §3.2–3.4/6.1/7.1/
8.1/9.1/10.1) se fija antes de implementar, permitiendo F2 (capa 3) en paralelo con F1 (capa 1) y F24-b
(server imagen) en paralelo con la cadena firmware F24 (placa única COM6).

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
  - **F1a** toolchain+vendor: `eim install 5.5.6` + `eim run "idf.py --version" v5.5.6`; clone `git clone --branch 3.2.0 --depth 1 https://github.com/francescopace/espectre.git aulasense/firmware/espectre` (**tag SIN prefijo v** — `v3.2.0` da 404, corrección verificada F1a). → `iot2/.tmp/f1a_idf.log` + `aulasense/firmware/espectre/` (gitignored). **Criterio:** log muestra `v5.5.6`; repo clonado con LICENSE. ~80 steps.
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
| C9 privacidad | **F24: cumple-PARCIAL (local-only)** — imágenes de evidencia SOLO al servidor local (nunca nube/broker público), consentimiento de aula, retención configurable, sin caras-ML; CSI crudo no sale del chip | F1,F3,**F24** | sdkconfig camera bajo AULASENSE_IMAGEN + docs privacidad re-enmarcada + consentimiento_aula.md + politica_retencion_imagenes.md |
| C10 validación manual | Verdad terreno binaria por sesión → matriz confusión | F4 | validacion_sesionN.csv + f4_metricas.md |
| C11 alerta audible/BLE | Buzzer GPIO14 + WS2812 + dashboard beep; BLE opcional | F3 | app_feedback.c + receptor_ble.py (opt) |
| C12 métricas edge | read_diagnostics nativo (ms, CPU%, heap, pps, RSSI) | F2,F3 | telemetria table + f3_metricas.json |
| C13 datos honestos | origen real/sintetico, SIMULADO etiquetado, nunca mezclado | F2,F4,F5 | validador + filtro CSV + docs |
| C14 entregables código | firmware patch + pipeline + dataset + tablero + informe | todas | repo aulasense/ completo |

**Cumplimiento: 14/14** (C6 y C11 son deseables y se cubren; el resto obligatorias). Mayoría exigida: superada.

## 8. Riesgos E1–E10 → mitigaciones (mapeo al diseño AulaSense)

| # | Restricción | Mitigación en AulaSense | Fase que la verifica |
|---|---|---|---|
| E1 | Cero ML de rostros/fisiología | Detector CSI (MLP sobre features de RF), sin cara/ojo; la visión opcional F24 es PERSONA, no caras (D-F24-5). Eliminado por construcción | F1c, F24-e0 |
| E2 | Cero dependencia de encuadre | El detector CSI no depende de encuadre ni visión (RF, no óptica). La cámara F24 es evidencia ACCESORIA: si falla o el heap cae, se descarta/auto-deshabilita y el evento sigue (D-F24-2) | F1b, F24-c (gate memoria + descarte) |
| E3 | Demo sin humanos frágiles | Estímulo trivial: operador camina/mueve objeto; zona quieta→idle. Monitor+WS+serial en vivo | F6b |
| E4 | Hardware HOY, $0 | Solo placa GOOUUU + router Wi-Fi existente; sin compras en ruta crítica | F1 |
| E5 | Windows/python/eim/COM6/sin USB | IDF 5.5.6 vía eim (S4); C:\Python315; consola USB-Serial-JTAG en S3 (COM6, sin TinyUSB); sin USB device stack; sdkconfig verificado pre-flash; recuperación BOOT 3 s | F1a,F1b |
| E6 | Broker 14769/auth/secretos | .env gitignored; config_mqtt.py lee .env nunca imprime; topic_prefix aulasense; scheme verificado | F1d |
| E7 | Heredar PATRÓN no código | Arquitectura 4 capas + controles de seguridad heredados; código nuevo adaptado a CSI; cero firmware de caras copiado | F2,F3 |
| E8 | Entregables en disco + feedback vivo | Cada fase: ruta exacta + criterio objetivo; Monitor/Direct HTTP + dashboard WS + serial en vivo; checklist lo marca el orquestador | todas |
| E9 | Privacidad / sin imágenes | **F24 RE-ENMARCA (D-F24-1):** ya no "sin imágenes" → privacidad acotada local-only (evidencia QVGA al onset, solo servidor local, consentimiento, retención 30 d purgable sin romper dataset, sin caras-ML). CSI crudo no sale del chip. C9 cumple-parcial | F1,F3,**F24-f** |
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

## 11. F24 · AMPLIACIÓN (orden explícita del dueño, 2026-11-12) — decisiones, fases, riesgos

> El dueño del proyecto ordenó 4 funciones + recopilación big data y delegó el diseño ("sé creativo y
> hazlo de la mejor manera, si no se puede no lo hagas"). El orquestador envió un rediseño (imágenes =
> evidencia de evento, provisioning nativo-primero, IA = spike con gate duro, big data). Esta sección
> adopta ese diseño **con mejoras verificadas en terreno** y documenta dónde se mejoró/rechazó con
> evidencia. Contratos implementables: `SPEC.md` §3.2–§3.4, §6.1, §7.1, §8.1, §9.1, §10.1, §14.
> Checklist: `../.tmp/checklist.md` v24 (v23 archivada en `checklist_pre_v24.md`).
> **F1 y F3 NO se reabren ni se tocan.** F24-c/d esperan F1 completada (+F3 por archivos compartidos del
> parche y serialización de la placa COM6). El ejecutor F1 está reanudando F1b AHORA en el vendor clonado:
> F24 solo LEE el vendor (F24-a), nunca ejecuta nada ahí.

### 11.1 Decisiones D-F24-1..8 (con evidencia archivo:linea / fuente)

- **D-F24-1 — Re-enmarque de privacidad (REVOCADA la regla 2 / D9 v23 por orden del usuario).**
  "Privacidad por construcción / cero imágenes" → **"privacidad acotada local-only"**: imágenes SOLO al
  servidor local del proyecto (LAN→ingesta :8001 con token; galería loopback :8000), NUNCA a la nube ni al
  broker MQTT público; consentimiento de aula documentado; retención/borrado configurables (default 30 d,
  purga con `imagen_purgada` que NO rompe el dataset); **sin caras-ML** (no reintroducir detección facial).
  **C9 = cumple-parcial.** *Evidencia:* orden explícita del dueño 2026-11-12 (bitácora entrada F24);
  AGENTS.md regla 2 reescrita + regla 14 nueva; SPEC §13/§14. *Por qué:* el usuario es el dueño y ordenó
  la captura; la mitigación honesta es acotar el flujo (local-only, consentimiento, retención, sin caras)
  en vez de fingir que sigue siendo "cero imágenes".

- **D-F24-2 — Imagen = evidencia de evento, NO streaming.** Captura SOLO en ONSET idle→motion, QVGA
  320×240 JPEG quality 12 fb PSRAM, **cooldown 15 s**, **tarea dedicada de baja prioridad** con backoff
  (reintentos máx 3), **cola corta en PSRAM** (2 slots × 8 KB; si se llena → DESCARTA imagen y el EVENTO
  SIGUE), **gate de memoria** (heap interno < 120 KB o largest block < 64 KB → auto-deshabilita con log
  honesto, CSI sigue). **SXGA descartado** (1600×1200 ~100–300 KB, sin valor analítico para persona a
  224², sube latencia/PSRAM). *Evidencia:* config QVGA probada en este MISMO sensor OV3660:
  `archive/dashcam-ia/firmware/main/app_camara.c:68-71` (FRAMESIZE_QVGA, jpeg_quality 12, fb_count 2,
  CAMERA_FB_IN_PSRAM); esp-dl espdet_pico input 224² (`.tmp/f23_research_viabilidad.md:100`); baseline heap
  `.tmp/f1c_diagnostics.json:4-6` (free 206 KB / min 189.9 / largest 164). *Por qué:* el evento CSI es la
  fuente de verdad; la foto es accesoria y nunca debe bloquear ni degradar la detección.

- **D-F24-3 — Upload por receptor HTTP en la INGESTA (NO en dashboard); galería loopback. MEJORA al diseño
  del orquestador.** El orquestador propuso "endpoint nuevo en dashboard o ingesta". **Se rechaza "en
  dashboard"** porque el dashboard bind **127.0.0.1** es regla dura (E7/AGENTS) y el nodo NO puede alcanzar
  loopback desde la LAN → sería inalcanzable o rompería la regla. **Solución adoptada:** listener HTTP
  mínimo (`server/ingesta/receptor_imagen.py`, hilo `ThreadingHTTPServer`) **dentro del proceso de ingesta**,
  que encola los jobs validados en la **cola existente del writer único** (preserva la invariante). Bind
  `AULASENSE_UPLOAD_HOST`:8001 (vacío=desactivado; NUNCA 0.0.0.0 default), **Bearer token**
  `AULASENSE_UPLOAD_TOKEN` (`secrets.token_hex(16)`, nunca impreso), JPEG magic FFD8FF, ≤64 KB, device
  allowlist 16 hex, rate-limit 1/10 s → 401/413/422/429. Galería servida por el dashboard SOLO loopback
  (`GET /api/imagenes/{id}/foto`, ruta por id de BD validada bajo datos/imagenes/, nosniff, 404 si purgada).
  **Sin thumbnail server-side** (QVGA ya es tamaño thumbnail; el navegador escala → evita riesgo wheel
  Pillow cp315 no verificado para py3.15). *Evidencia:* AGENTS §Seguridad (bind 127.0.0.1);
  `.tmp/f2_backend.md` (writer único, openmeteo ya escribe con WAL/busy_timeout como excepción documentada).
  *Por qué:* mantiene la regla loopback intacta, hace el upload alcanzable desde el nodo, y preserva el
  writer único sin añadir un proceso nuevo.

- **D-F24-4 — Provisioning: NATIVO primero (verificado: NO trae AP) → módulo propio endurecido + ruta
  privada.** *Hallazgo de investigación (vendor clonado tag 3.2.0, solo lectura):* ESPectre **Native** trae
  **Improv Serial** (USB) + recuperación **BOOT 3 s** + Direct HTTP WiFi reconfig (scan/BSSID/erase vía
  portal first-party espectre.dev). **NO trae AP/SoftAP ni captive portal:**
  `runtime/esp_idf/standalone_wifi_service.cpp:176` y `wifi_lifecycle.cpp:378` usan `WIFI_MODE_STA` único
  (grep `WIFI_MODE_` en runtime = solo STA); el captive portal "ESPectre Fallback" es del frontend
  **ESPHome** (`frontend/esphome/README.md:14`), NO Native (y usamos Native, D1). → **módulo propio
  `app_provision.c`**, calco del patrón endurecido DriveGuard (`archive/dashcam-ia/firmware/main/app_portal.c`,
  lectura para inspiración, no copia ciega): AP fallback al agotar un **contador PROPIO del módulo**
  (`AULASENSE_PROVISION_RETRIES`=3, observado por `IP_EVENT_STA_GOT_IP`/`WIFI_EVENT_STA_DISCONNECTED` — NO es un
  estado del vendor, que reintentan con `max_retry=8` + back-off 30 s en bucle infinito; `.tmp/f24a_spec_implementable.md` §1/E2); SSID
  `AULASENSE-CFG-<4 hex MAC>`; **AP abierto CON aviso** (patrón D6, app_portal.c:118-120,387) o WPA2 opcional
  (`AULASENSE_PROVISION_AP_PASS`); **ruta privada** `http://192.168.4.1/cfg/<token>` (token = 8 hex de clave
  efímera NVS `aulasense_prov/cfg_token` generada con `esp_fill_random`, **mostrada UNA vez en serial**,
  ruta incorrecta → 404 sin pista, vía enrutado de parámetro `/cfg/:vtoken` o catch-all + `consttime_memequal` (E5); al guardar credenciales: **AP OFF → STA puro (`WIFI_MODE_STA`) → intentar conexión → re-levantar AP solo si vuelve a fallar** (radio única: AP canal 1 ≠ STA canal 11, NO `APSTA` cross-canal — E3); POST con **Origin/Referer guard 403** (app_portal.c:73-98,260),
  cabeceras seguridad (app_portal.c:104-112), **NVS write solo si cambio real + cooldown 5 s → 429**
  (app_portal.c:255-292), **NUNCA loguea SSID/pass** (solo longitudes, app_portal.c:290-291); AP off 30 s
  tras STA OK; **BOOT 3 s → Improv PRESERVADO**. *Decisión de auth AP:* abierto + ruta token = dos factores
  (proximidad física + conocimiento del token); WPA2 opcional para quien lo quiera (el token de ruta ya es
  el control de acceso; una pass WPA2 añadida también tendría que mostrarse en serial y suma fricción).
  *Por qué:* no reinventar lo que el vendor ya trae (Improv/BOOT/Direct HTTP se preservan); solo se añade
  el AP-fallback que Native no tiene, con el endurecimiento ya certificado en DriveGuard F18-A.

- **D-F24-5 — IA interna = el detector CSI (YA existe); visión persona OPCIONAL, spike-gated, default n.**
  **Respuesta honesta y creativa al pedido:** la "IA interna" del proyecto **YA ES** el detector neuronal
  CSI en-chip (MLP 529 params, edge-first C1) — los docs deben decirlo así. El extra de visión (persona
  sobre la foto) es capa ADICIONAL opcional, **sujeta a SPIKE F24-e0 time-boxed (≤200 steps) con gate duro**:
  (1) `csi_accepted_pps` no cae >10 % vs F1c (≥90 pps); (2) heap interno libre ≥120 KB AND largest block
  ≥64 KB durante captura+inferencia; (3) arena esp-dl cabe en PSRAM (8 MB libre, vendor no usa PSRAM);
  (4) latencia inferencia ≤2 s asíncrona; (5) `detection_avg_us` no se degrada >20 %. **Si CUALQUIERA falla
  → rama ABANDONADA, NO se hace F24-e**, se documenta la limitación ("la capa de visión IA no cabe junto al
  detector CSI en este hardware — verificado en spike") y se entrega sin ella (orden explícita: "si no se
  puede no lo hagas"). Modelo: PERSONA (ESP-WHO pedestrian_detect / esp-dl espdet_pico escala humana),
  **NO caras, NO COCO/YOLO** (inviable en S3 verificado F23). Resultado `ia_imagen:{persona,score}` como
  campo opcional en motion + metadata upload + columnas `ia_persona`/`ia_score` → **DATO ANALIZABLE en el
  consolidado**, no solo galería. *Evidencia:* `.tmp/f23_research_viabilidad.md:94-109` (YOLO11n 320²
  961 ms ~1 FPS / 8 MB flash; YOLO26n exige 16 MB PSRAM; espdet_pico 224² 120 ms ~7.7 FPS), `:104` (ESP-WHO
  trae pedestrian, y ESP-WHO es compatible con IDF **v5.5** según su tabla README 2026-11-12 — no solo 5.4, corrige al F23); tamaño .espdl **RESUELTO en F24-a**: ESPDet-Pico 0.36M×w8a8 ≈ **~0.4 MB** (estimación por conteo de parámetros; flash XIP, arena en PSRAM), y el cuello previsto del gate e0 es **contención de CPU no heap** (veredicto `.tmp/f24a_spec_implementable.md` §4/§6 E6: MARGINAL / probable borderline-fail req 1/5); vendor
  `app/sdkconfig:1300` `# CONFIG_SPIRAM is not set` (PSRAM 8 MB enteramente libre para la arena).
  *Por qué:* el usuario delegó ("si no se puede no lo hagas"); comprometer la integración sin medir
  coexistencia reviviría la clase de fallo de DriveGuard (prometer visión que el S3 no entrega en vivo).
  El spike mide ANTES de comprometer; default n garantiza que el sistema base nunca dependa de la visión.

- **D-F24-6 — MQTT SIN CAMBIOS.** Nodo publica como `electiva` (config_mqtt PATCH /mqtt username desde
  `MQTT_USER`); ingesta suscribe como `srv-electiva` (`MQTT_INGESTA_USER`, fallback `MQTT_USER`).
  Credenciales `.env` vigentes, nunca impresas. Topics nativos intactos + aditivos `aulasense_temp`/
  `aulasense_config`; campo opcional `ia_imagen` en motion. **Imágenes NUNCA por MQTT** (binario al broker
  público viola el re-enmarque de privacidad). *Evidencia:* bitácora 2026-11-12 (verificación srv-electiva
  rc=0, `.env` MQTT_USER); `.env.example`; `server/ingesta/ingesta_mqtt.py:169` (lee MQTT_USER). *Cambio
  mínimo de contrato:* la ingesta lee `MQTT_INGESTA_USER` (F24-b1) para reflejar el usuario receptor; el
  nodo sigue con electiva. Sin cambios de topics/auth/claves.

- **D-F24-7 — Recopilación BIG DATA ACADÉMICA (orden extra del usuario).** TODAS las fuentes aterrizan en
  SQLite + export consolidado CSV analyzable: evaluaciones/eventos (motion/idle/bordes), telemetría health
  (pps/CPU/heap/RSSI/die_temp del poller 30 s), **imágenes F24** (hash/tamaño/device/ts/ruta + **ia_persona/
  ia_score como DATO ANALIZABLE**, no solo galería), openmeteo, **config_eventos** (provision/config, sin
  secretos), validacion_sesiones. **Consolidado = una fila por evento con joins por ventana temporal**
  (telemetría ±30 s, imagen por FK evento_id, openmeteo ±1 h, config en ventana, validación) — esquema
  documentado SPEC §10.1, generado por `analysis/export_consolida.py`. **Postura honesta:** el curso (Big
  Data e IA) pide un **pipeline real ingesta→almacén→análisis con datos propios**, no petabytes; AulaSense
  lo cumple (series temporales CSI ~4 eval/s + catálogo de imágenes + métricas + clima + config). Volumen:
  ~14.4k evaluaciones (3 sesiones × 20 min) + cientos de imágenes (1 por onset, cooldown 15 s). **La
  retención/purga NO rompe el dataset** (borrar imagen deja la fila de evento intacta, flag `imagen_purgada`,
  ia conservada). F24-f2: primera compilación del consolidado con imágenes+ia reales post-F24-c, smoke
  analysis corre sin romperse; F5a consume el esquema extendido. *Evidencia:* orden usuario "se deben
  recopilar datos en el servidor backend con big data"; condiciones curso C3/C5; `.tmp/f2_backend.md`
  (export CSV ya existe, se extiende). *Por qué:* "big data" en el contexto del curso = el pipeline completo
  con datos propios reales, no escala masiva; prometer petabytes sería deshonesto (E10).

- **D-F24-8 — Creatividad barata SIN riesgo: heatmap de actividad por hora.** El heatmap por franja horaria
  YA estaba previsto en Reportes (v23 SPEC §9 / PROPUESTA). F24-f2 produce la figura
  `assets/heatmap_actividad_hora.png` desde el consolidado (los datos CSI+horas lo permiten) como entregable
  visual del big data, sin inflar fases. La galería de evidencia es el entregable visual nuevo. *Por qué:*
  suma valor docente con costo marginal (un groupby hora + imshow), sin riesgo de memoria/latencia en el nodo.

### 11.2 Dónde se MEJORÓ o RECHAZÓ el diseño del orquestador (con justificación)

| Punto del orquestador | Veredicto | Justificación / evidencia |
|---|---|---|
| "endpoint nuevo en dashboard o ingesta: /internal/imagen" | **MEJORADO → receptor en INGESTA, no dashboard** | Dashboard bind 127.0.0.1 es regla dura y el nodo no alcanza loopback desde LAN. Receptor :8001 con token dentro de ingesta preserva writer único y la regla loopback (D-F24-3). |
| "QVGA o SXGA decidir según PSRAM/anchos de upload" | **DECIDIDO QVGA, SXGA descartado** | QVGA probado en este sensor (app_camara.c:68-71); esp-dl input 224² no necesita más; SXGA ~100–300 KB sube latencia/PSRAM sin valor analítico (D-F24-2). |
| "password AP: generada y mostrada en serial? o abierta con aviso — decidir" | **DECIDIDO abierta con aviso + ruta privada token (WPA2 opcional)** | Patrón DriveGuard D6 endurecido; el token de ruta ya es el control de acceso (dos factores con proximidad); WPA2 opcional para quien lo quiera (D-F24-4). |
| "IA interna opcional esp-dl" | **REFINADO → spike F24-e0 con gate duro ANTES de comprometer; si no cabe, ABANDONADA** | Orden del usuario "si no se puede no lo hagas"; evita revivir fallo DriveGuard de prometer visión inviable. La IA principal YA es el CSI (D-F24-5). |
| "galería/thumbnail en dashboard" | **MEJORADO → sin thumbnail server-side** | QVGA ya es tamaño thumbnail; el navegador escala. Evita riesgo wheel Pillow cp315 no verificado (D-F24-3). |
| "heatmap por hora como creatividad opcional" | **ADOPTADO sin inflar fases** | Ya previsto en Reportes v23; F24-f2 produce la figura desde el consolidado (D-F24-8). |

### 11.3 Mapa de fases F24 (agente · sub-despachos · background · deps · entregable · criterio)

> Regla: TODA delegación `background: SI`. Una sola placa COM6 → firmware SERIALIZADO (F3→c→d→e0→e).
> Server (F24-b) y spec (F24-a) en paralelo sin placa. Techos: ejecutor 500, dev 400, analista 300.
> Detalle ejecutable renglón por renglón: `../.tmp/checklist.md` v24 sección F24.

- **F24-a · spec implementable + investigación vendor/esp-dl** — `@3-analista` (solo lectura, NO despacha).
  background SI. **Deps: ninguna** (paralelo a F1/F3; solo LEE el vendor, cero ejecución ahí). Entregable
  `.tmp/f24a_spec_implementable.md`. Criterio: confirma provisioning nativo STA-only (file:line), mapea
  hooks de onset/fallo STA, verifica modelo PERSONA esp-dl + tamaño .espdl + arena PSRAM, config cámara
  QVGA, flujo token. ~150 steps ≤300.
- **F24-b · server imagen + big data** — `@2-ejecutor` → `5-dev` ×2 disjuntos (B1 ingesta/receptor/purga/
  export+tests · B2 dashboard galería+tests). background SI. **Deps: F2 (completa).** Entregables
  `server/ingesta/receptor_imagen.py`, `esquema.py` (extiende), `ingesta_mqtt.py` (suscribe aulasense_config,
  MQTT_INGESTA_USER, arranca receptor), `tools/purga_imagenes.py`, `analysis/export_consolida.py`,
  `server/dashboard/app.py`+static (galería), `server/tests/test_imagen.py`/`test_consolida.py`/
  `test_dashboard.py` (extiende), `.tmp/f24b_server.md`. Criterio: pytest ≥12 tests nuevos verdes; imagen
  sintética POST→archivo+fila+galería <2 s; consolidado esquema §10.1 carga en pandas. ~350 B1 + ~300 B2
  ≤400. **NO choca con F1/F2** (rutas disjuntas; F2 ya terminó).
- **F24-c · firmware captura QVGA + upload** — `@2-ejecutor` → `5-dev`. background SI. **Deps: F1
  completada + F3 completada** (placa + parche compartido) + F24-b (receptor vivo) + F24-a (hooks).
  Entregables `firmware/parche_aulasense/app_imagen.c/.h`, Kconfig/CMake/README (sección F24),
  `.tmp/f24c_build.log`/`f24c_flash.log`/`f24c_evidencia.md`/`f24c_metricas.json`, ≥1 JPEG real en
  `server/datos/imagenes/` + fila BD. Criterio: onset→JPEG→archivo+fila <5 s; cooldown 15 s verificado;
  servidor caído → evento sigue (descarte); gate memoria verificado; pps no cae >10 % vs F1c; SPIRAM=y
  octal pre-flash. ~350 steps ≤400.
- **F24-d · firmware provisioning AP + ruta privada** — `@2-ejecutor` → `5-dev`. background SI. **Deps:
  F1 + F3 + F24-c completadas** (placa serial + Kconfig/CMake/README compartidos) + F24-a. Entregables
  `app_provision.c/.h`, Kconfig/CMake/README, `.tmp/f24d_build.log`/`f24d_flash.log`/`f24d_evidencia.md`,
  filas config_eventos. Criterio: flujo completo credencial falsa→AP sube→ruta privada→config nueva→STA
  reconecta→AP off 30 s; 403/429 verificados; grep logs CERO SSID/pass; BOOT 3 s Improv preservado.
  ~350 steps ≤400.
- **F24-e0 · SPIKE coexistencia esp-dl+CSI (TIME-BOXED ≤200 steps, gate duro)** — `@2-ejecutor` → `5-dev`.
  background SI. **Deps: F24-c completada + F24-a.** Entregable `.tmp/f24e0_spike.md` + `f24e0_metricas.json`
  (veredicto PASS/FAIL). Criterio: gate D-F24-5 (pps ≤10 % caída, heap ≥120 KB/64 KB bloque, arena PSRAM,
  ≤2 s asíncrono, detection_avg_us ≤20 % degradación). **Si FAIL → F24-e ABANDONADA, no se lanza.** ~200 steps.
- **F24-e · IA visión opcional integrada (SOLO si F24-e0 PASS; default n)** — `@2-ejecutor` → `5-dev`.
  background SI. **Deps: F24-e0 PASS + F24-c + F24-b.** Entregables `app_ia_imagen.c/.h`, Kconfig
  (`AULASENSE_IA_IMAGEN` default n), CMake/README, `.tmp/f24e_*`. Criterio: con y, ≥1 evento real con
  ia_imagen en BD+consolidado; con n, idéntico a F24-c; sin faces-ML. ~300 steps ≤400. **Si F24-e0 FAIL:
  marcar ABANDONADO con evidencia, documentar limitación (F24-f), NO ejecutar.**
- **[PARALELO] F24-f2 · datos consolidado + smoke analysis** — `@2-ejecutor` → `5-dev`. background SI.
  **Deps: F24-b + F24-c.** Entregables `analysis/dataset/consolidado.csv` (filas reales con tiene_imagen=1),
  `analysis/smoke_consolida.py`, `assets/heatmap_actividad_hora.png`, `.tmp/f24f2_datos.md`. Criterio: CSV
  real post-F24-c, esquema §10.1, smoke carga+valida joins+heatmap; run_analisis punta a punta si F5a existe.
  ~200 steps. Paralelo a F24-e0/f (rutas disjuntas analysis/+assets/+.tmp/).
- **F24-f · docs + re-enmarque + verificación** — `@4-documentos` (directo) + `@3-analista` (verificación
  solo lectura). background SI. **Deps: F24-b + F24-d + veredicto F24-e0 (+F24-e si corrió) + F24-f2.**
  Entregables `docs/PROPUESTA.md`/`README.md`/`docs/README.md`/`fuentes.md` (actualizados),
  `docs/consentimiento_aula.md`, `docs/politica_retencion_imagenes.md`, `.tmp/f24f_verificacion.md`.
  Criterio: docs coherentes C9 cumple-parcial; grep CERO afirmaciones vigentes "cero imágenes/sin cámara/
  privacidad por construcción"; consentimiento+retención documentados; IA visión honesta (spike verdict).
  ~200 docs + ~100 analista.
- **GATE (actualizado)** — fan-out DIRECTO del orquestador, deps F6 + F24-f + F24-f2. Los 7 especialistas
  con criterios F24 añadidos (seguridad: token/ruta privada/sin secretos en logs/imágenes gitignored/sin
  faces; rendimiento: pps con cámara+upload/gate memoria; bd: tablas imagenes+config_eventos/FK/purga
  intacta; pruebas: casos borde imagen; documentacion: re-enmarque coherente). `devops` OMITIDO (sin
  Docker/CI, E5). **F7 cierre → `.tmp/cierre_v24.md` + commit final del orquestador (manos propias).**

### 11.4 Supuestos nuevos F24 (numerados, visibles)

| # | Supuesto | Fuente/verificación | Estado |
|---|---|---|---|
| S13 | El OV3660 de esta placa captura QVGA JPEG ~4–8 KB con fb PSRAM (config DriveGuard probada en el mismo sensor); el tamaño real se mide en F24-c | archive app_camara.c:68-71; DriveGuard F1–F3 operativo | Nuevo → gate F24-c (medir tamaño) |
| S14 | Habilitar SPIRAM octal 8 MB + camera driver bajo AULASENSE_IMAGEN no degrada csi_accepted_pps >10 % ni el heap interno bajo el umbral (vendor no usa PSRAM hoy; heap baseline 206 KB) | vendor sdkconfig:1300; f1c_diagnostics.json:4-6 | Nuevo → gate F24-c (pps/heap) + F24-e0 (esp-dl) |
| S15 | Existe un modelo de PERSONA esp-dl/ESP-WHO (ESPDet-Pico 0.36M / 224², pedestrian_detect/espdet_pico escala humana) **compatible con IDF v5.5** (tabla README 2026-11-12, no solo 5.4); tamaño .espdl ≈ **~0.4 MB** w8a8 (estimación, arena en PSRAM). La coexistencia con el CSI: cuello previsto = **contención de CPU, no heap** (F24-a: MARGINAL / probable borderline-fail req 1/5) | `.tmp/f24a_spec_implementable.md` §4/§6 (E6); f23_research_viabilidad.md:100,104 | F24-a **resuelve** modelo + tamaño; gate F24-e0 (spike mide CPU/pps); si FAIL → ABANDONADO |

### 11.5 Riesgos nuevos F24 (R9–R16) → mitigación en el checklist

- **R9 — PSRAM/heap con cámara + CSI simultáneos (el OV3660 + detector en el mismo heap).** El vendor NO
  usa PSRAM hoy (sdkconfig:1300); habilitar SPIRAM cambia el mapa de memoria. Heap interno baseline 206 KB
  libre / 189.9 min / 164 largest (f1c_diagnostics:4-6); camera DMA + esp_http_client + esp-dl interno
  deben caber. **Mitigación:** gate de memoria auto-deshabilita captura (heap <120 KB / bloque <64 KB);
  F24-c mide pps/heap vs F1c (no caer >10 %); F24-e0 spike mide coexistencia esp-dl ANTES de comprometer;
  arena esp-dl en PSRAM (MALLOC_CAP_SPIRAM). Checklist: F24-c criterio pps/heap, F24-e0 gate duro.
- **R10 — Upload HTTP desde el loop del detector bloquearía el CSI.** **Mitigación:** tarea dedicada de
  baja prioridad, cola PSRAM, backoff, NUNCA en el loop; si servidor caído → descarte tras reintentos y el
  evento motion sigue por MQTT. Checklist: F24-c prueba servidor-caído→evento-sigue; gate rendimiento.
- **R11 — Tamaño de imagen vs PSRAM fb.** QVGA quality 12 ~4–8 KB (S13), fb_count 2 en PSRAM (16 KB),
  cola 2×8 KB. SXGA descartado (D-F24-2). **Mitigación:** medir tamaño real en F24-c; ≤64 KB en receptor
  (413). Checklist: F24-c tamaño medido, F24-b1 test 413.
- **R12 — AP de provisioning abierto → sniffing del POST de credenciales.** **Mitigación:** ruta privada
  token no adivinable (404 sin pista) + Origin/Referer guard 403 + cooldown NVS 429 + WPA2 opcional +
  aviso en página/log + configurar en lugar privado; NUNCA loguear SSID/pass. Checklist: F24-d flujo
  completo + grep logs cero secretos; gate seguridad.
- **R13 — Regresión de privacidad (imágenes = datos personales).** **Mitigación:** consentimiento de aula
  documentado, retención 30 d purgable (imagen_purgada sin romper dataset), local-only (nunca nube/broker),
  sin caras-ML, imágenes gitignored, token auth, galería loopback. Checklist: F24-f docs consentimiento+
  retención; gate seguridad/documentacion; .gitignore datos/imagenes/.
- **R14 — Modelo persona esp-dl disponibilidad/tamaño (era INCONCLUSO research:215).** **Resuelto en F24-a:** ESPDet-Pico 0.36M/224² ≈ ~0.4 MB w8a8 (estimación, flash + arena PSRAM); ESP-WHO compatible IDF v5.5 (no solo 5.4). **El riesgo real pasa a ser CONTENCIÓN de CPU, no heap** (F24-a: gate e0 MARGINAL / probable borderline-fail req 1/5).
  **Mitigación:** F24-e0 spike mide coexistencia esp-dl+CSI (`csi_accepted_pps`/`detection_avg_us`) ANTES de comprometer; si no cabe →
  ABANDONADO documentado (orden usuario). Checklist: F24-a/e0 gates.
- **R15 — Wheel Pillow cp315 no verificado para thumbnails.** **Mitigación:** sin thumbnail server-side
  (QVGA ya es pequeño, navegador escala). Checklist: F24-b2 galería sin resize server.
- **R16 — Una sola placa COM6 → colisión de firmware si F24-c/d/e corren a la vez.** **Mitigación:**
  serialización explícita F3→F24-c→F24-d→F24-e0→F24-e (deps en checklist); server/spec en paralelo sin
  placa; F24-a solo LEE el vendor (F1 en vuelo ahí). Checklist: deps explícitas + nota placa única.

### 11.6 Qué necesita F24 del orquestador

1. **Aprobar el re-enmarque de privacidad** (regla 2 revocada por orden del dueño; C9 cumple-parcial) y
   commitear los docs F24 (manos propias de git; el planificador NO commitea).
2. **Rellenar `.env`**: `AULASENSE_UPLOAD_HOST` (IP LAN del PC), `MQTT_INGESTA_USER=srv-electiva` (ya en
   example), `AULASENSE_IMG_RETENCION_DIAS`. El `AULASENSE_UPLOAD_TOKEN` lo GENERA por código
   `tools/gen_firmware_config.py` (`secrets.token_hex(16)`) en F24-b/c — NUNCA lo asigna el modelo ni se imprime.
3. **Consentimiento de aula** (P3, ahora OBLIGATORIO por imágenes): coordinar con profesor/operador;
   entregable documental `docs/consentimiento_aula.md` en F24-f.
4. **Esperar F1 completada** antes de lanzar F24-c/d (placa + parche compartido). F24-a (spec, solo lectura)
   y F24-b (server) pueden lanzarse YA en paralelo (no tocan la placa ni el vendor en vuelo).
5. **Decidir la ventana del spike F24-e0** (time-boxed ≤200 steps): si el orquestador/usuario prefieren no
   invertir en la visión opcional, F24-e0/e pueden omitirse y entregarse sin la capa de visión (la IA
   principal CSI ya cumple C1). Si se lanza y FAIL → ABANDONADA documentada, no se hunden más fases.
6. **GATE actualizado**: fan-out directo de los 7 especialistas con criterios F24 (seguridad/rendimiento/
   bd/pruebas/documentacion) tras F6+F24-f+F24-f2. `devops` omitido (sin Docker/CI).
7. **Verificar en disco y marcar** el checklist v24 (solo el orquestador marca `[x]`); reconciliar las
   marcas de F2 (completa según f2_backend.md, 42 tests) y F1 (en vuelo).

## 12. F25 · EXPANSIÓN (orden explícita del dueño, 2026-11-14) — decisiones, fases, riesgos

> El dueño ordenó 8 frentes nuevos ("quiero todo terminado, usa planificador y especialistas, tokens los
> que hagan falta"; bitácora 2026-11-14): (1) imágenes por MQTT, (2) servidor en WSL, (3) descripción IA
> de imágenes + búsqueda, (4) login seguro, (5) diagrama técnico, (6) póster visual, (7) video explicativo,
> (8) cierre TOTAL (F24 restante + F4–F7 + GATE). Esta sección adopta los supuestos declarados por el
> orquestador **refinados con evidencia verificada en terreno y fuentes primarias (2026-11-14)**.
> Contratos nuevos: `SPEC.md` §3.2.1, §6.2, §7.2, §7.3, §8.2, §9.2, §9.3, §12.1, §15. Checklist:
> `../.tmp/checklist.md` **v25** (v24 archivada byte-a-byte en `checklist_pre_v25.md`).
> **RE-ENMARQUE OBLIGATORIO:** la decisión D-F24-1/6 "imágenes NUNCA por MQTT" queda **REVOCADA** por
> orden del dueño (2026-11-14) — ver D-F25-1 con evidencia técnica de viabilidad.
> **F24-c ESTÁ EN VUELO por OTRO ejecutor (fotos HTTP al receptor :8001). F25 NO lo re-hace ni lo
> interrumpe:** las fases server-side de F25 usan rutas disjuntas; el firmware F25-c se serializa DETRÁS
> de F24-c/d/e0/e (placa única COM6 + archivos compartidos del parche); el cutover WSL (F25-b2) espera
> el reporte de F24-c para no romper su E2E en Windows.

### 12.1 Decisiones D-F25-1..13 (con evidencia archivo:linea / fuente primaria)

- **D-F25-1 — IMÁGENES POR MQTT (revoca D-F24-1/6 "nunca por MQTT" por orden del dueño, 2026-11-14).**
  Canal PRIMARIO: el nodo publica cada foto-evidencia como JSON con base64 en
  `aulasense/v1/devices/{device_id}/image` (payload: `v, device_id, ts_ms, motivo="onset", tamano,
  sha256, b64`; contrato SPEC §6.2). HTTP :8001 (F24-b) queda como **canal alternativo** (`canal='http'`
  vs `canal='mqtt'` en BD). **Viabilidad verificada:**
  - *Broker:* Mosquitto `message_size_limit` default = **0 = acepta todo mensaje MQTT válido** (máximo
    del protocolo 268 435 455 B ≈ 256 MB) y `max_packet_size` default = **2 000 000 B** desde 2.1.
    QVGA JPEG ~4–8 KB → b64 ~5,4–10,7 KB + envelope JSON ≈ **≤12 KB ≪ 2 MB**. Fuente:
    mosquitto.org/man/mosquitto-conf-5.html (webfetch del planificador, 2026-11-14). La config REAL de
    broker.gilman.pro no es legible desde aquí → **probe empírico obligatorio F25-a** (publish+subscribe
    de 12 KB y 64 KB en `aulasense/v1/test/image_probe_*` con srv-electiva/electiva; si el broker
    rechazara, fallback documentado: HTTP primario + MQTT solo-notificación, y se reporta al dueño).
  - *Nodo (cuello REAL encontrado):* el transporte vendor tiene **outbox limitado a 8192 B**
    (`firmware/espectre/src/cpp/runtime/esp_idf/mqtt_transport_esp_idf.h:90` `kMqttOutboxLimitBytes=8192U`,
    aplicado en `.cpp:86`) → un payload b64 ~10 KB NO cabe. **Parche documentado de una línea**
    (8192→32768, re-aplicable, registrado en `firmware/parche_aulasense/README.md` §F25, mismo patrón
    que las ediciones vendor de cámara de F24-c). El outbox es un TOPE, no pre-asignación: subirlo no
    reserva memoria; el heap baseline (206 KB libres, f1c_diagnostics.json:4-6) lo absorbe.
  - *QoS/retain:* el vendor publica **QoS 0** fijo (`.cpp:384` `esp_mqtt_client_enqueue(...,0,0,...)`) →
    pérdida posible tolerada (la imagen es accesoria; el EVENTO es la fuente de verdad, D-F24-2).
    **retain=false SIEMPRE** (la foto NO queda persistida en el broker compartido). Vía de publicación:
    `publish(topic_completo, payload, false)` → **NO-reemplazable** (`.cpp:154-161`: replaceable =
    retain || topic `ha/`); NO usar `publish_suffix()` (marca todo reemplazable, `.cpp:163-171`, y el
    high-water de 2048 B `.cpp:379-381` diferiría la imagen). El hook ya existe en el parche en vuelo:
    `app_imagen_set_mqtt_publisher`/`app_imagen_publish_pending` (`firmware/parche_aulasense/app_imagen.h:33-42`).
  - *Tope firmware:* si b64 > `AULASENSE_IMAGEN_MQTT_MAX_B64` (16384) → fallback HTTP (si hay receptor)
    o descarte con log; el evento sigue. Dedupe por `sha256` en ingesta (unique index) cubre duplicados.
  - *Privacidad (C9 sigue cumple-parcial, nueva mitigación):* las imágenes ahora transitan el broker
    compartido AUTENTICADO (electiva publica / srv-electiva suscribe, `.env` gitignored), aisladas por
    `topic_prefix=aulasense/v1/devices`, sin retain, QoS 0, galería loopback+login (D-F25-8), retención/
    purga intactas, consentimiento de aula ACTUALIZADO (F25-g). Nunca a la nube. *Evidencia del cambio:*
    orden del dueño 2026-11-14 (bitácora entrada EXPANSION F25). *Por qué:* el dueño es el dueño y ordenó
    el transporte; la mitigación honesta es acotar el flujo (auth+prefijo+no-retain+login+retención) y
    documentar el re-enmarque, no fingir que sigue vigente la regla vieja.

- **D-F25-2 — SERVIDOR EN WSL: reutilizar la distro `Debian` 13 (trixie) YA EXISTENTE, systemd activo,
  venv linux propio, SIN Docker (regla 4 intacta).** *Evidencia (shell lectura del planificador,
  2026-11-14):* `wsl -l -v` → `Debian Running 2` (también existen deb-hermes1, kali, Ubuntu-24.04,
  docker-desktop, etc. — NO se tocan); `wsl -d Debian cat /etc/os-release` → Debian GNU/Linux 13;
  `ps -p 1` → **systemd**; `/etc/wsl.conf` ya trae `[boot] systemd=true`; `python3` NO instalado →
  `apt install python3 python3-venv` (Debian 13 = Python 3.13, wheels abundantes: argon2-cffi/bcrypt
  sin el riesgo cp315 de Windows). **No se crea distro nueva** (el default del dueño, Debian, ya está).
  Gestión con skill `wsl-manager-cli` (cargada por el planificador; el ejecutor la carga en F25-b):
  `wsl-manager doctor/status/list --json`, `start/stop/restart Debian`, `run Debian <cmd>`,
  `autostart set Debian`; si el binario no está en PATH → fallback documentado `wsl.exe` directo
  (troubleshooting de la skill). **Código:** se ejecuta desde `/mnt/c/Users/Gilman/Desktop/1-Universidad/
  itse/iot2/aulasense` (el repo git sigue en Windows; CERO duplicación de código). **Datos:** en ext4
  nativo `~/aulasense-datos/` (aulasense.db + imagenes/) — SQLite WAL sobre drvfs/9P (/mnt/c) es lento y
  con locking poco fiable; las env `AULASENSE_DB`/`AULASENSE_DATOS_DIR` YA existen (F24-b,
  `.tmp/f24b_server.md` "Convenciones compactadas"). **SQLite, NO MariaDB:** el esquema §8/§8.1 + 90
  tests + writer único ya existen; MariaDB exigiría reescribir todo el SQL y el gate bd sin beneficio a
  esta escala (~14,4k evaluaciones + cientos de imágenes). **Migración = copia con cutover** (F25-b2):
  parar stack Windows → copiar DB+imagenes a ext4 → verificar conteos de filas → arrancar WSL →
  **NUNCA dos stacks vivos a la vez** (split-brain, R-F25-6). El venv Windows `server\.venv` queda como
  **fallback de emergencia documentado** (parar WSL antes de arrancarlo). Análisis F5a: corre en el
  venv WSL (pandas/sklearn instalados ahí) o Windows leyendo el consolidado CSV que el export WSL
  escribe en `analysis/dataset/` (ruta del repo) — el criterio es `run_analisis` punta a punta verde.

- **D-F25-3 — RED WSL: NAT intacto, NO tocar `.wslconfig` global; portproxy LOOPBACK en Windows.**
  *Evidencia:* `.wslconfig` actual = `localhostForwarding=false`, memory=10GB, processors=3, SIN
  networkingMode (NAT); **8 distros comparten la misma VM** → mirrored global afectaría kali/docker/
  ubuntu del usuario. **Mirrored per-distro es IMPOSIBLE:** issue microsoft/WSL#13706, cerrado
  "not planned" el 2025-11-12 por el mantenedor (benhillis): "architecturally this is not possible since
  all distros share a single VM" (webfetch 2026-11-14). Por tanto:
  - Dashboard bind `0.0.0.0` **dentro de la VM WSL** (NAT = VM-local; SIN portproxy LAN es inalcanzable
    desde la LAN) + **`netsh interface portproxy` con `listenaddress=127.0.0.1`** listenport=8000 →
    WSL_IP:8000. La exposición en Windows sigue siendo ESTRICTAMENTE loopback (la regla dura se
    reinterpreta así en AGENTS: "superficie expuesta en Windows = solo 127.0.0.1"). Requiere admin
    una vez; `tools/wsl_aulasense.ps1` refresca la WSL_IP (cambia tras reboot) y re-aplica el portproxy.
  - **Fallback sin admin:** navegador Windows → `http://<WSL_IP>:8000` (host→VM siempre enrutable en
    NAT) + `AULASENSE_ORIGINS_EXTRA=<WSL_IP>:8000` (exacto, opt-in, sin wildcards). El script imprime
    la URL. La LAN NO alcanza la WSL_IP (NAT sin portproxy de escucha LAN).
  - **Salida WSL→broker externo:** NAT permite outbound (verificación obligatoria F25-b1: paho connect
    rc=0 + mensaje real desde WSL). **HTTP :8001 desde el nodo (LAN→WSL):** NAT lo bloquea → portproxy
    LAN OPT-IN documentado (listenaddress=IP_LAN del PC + regla firewall) SOLO si el dueño quiere el
    canal alternativo HTTP activo en WSL; con MQTT primario (D-F25-1) NO es necesario. Origin guard y
    login (D-F25-8) intactos en todos los casos.

- **D-F25-4 — VISIÓN IA: modelo `qwen3.8-flash` (visual understanding) del Token Plan; `qwen3-vl`/
  `qwen-vl-max` NO están en la allowlist TP (supuesto del orquestador REFINADO con fuente primaria).**
  *Evidencia:* docs.qwencloud.com/token-plan/personal/token-plan-personal-overview (webfetch del
  planificador 2026-11-14), tabla "Supported models" = **allowlist exact-string**: los modelos con
  "visual understanding" son `qwen4-max-preview`, `qwen3.8-max`, **`qwen3.8-flash`**, `qwen3.7-plus`,
  `qwen3.6-flash`, `deepseek-v4.1-flash(-1017)`, `auto`. NO existe ningún `qwen*-vl*` en TP.
  **Default `qwen3.8-flash`** (tier flash = el más barato con visión; descuento nocturno 60 %
  22:00–08:00 UTC+8 documentado en la misma página); cadena de fallback `qwen3.6-flash` → `auto`.
  Con key **pay-as-you-go `sk-*`** (opcional, la rellena el operador) sí existen `qwen3-vl-flash`/
  `qwen-vl-max` vía `https://maas.qwencloudapi.com/compatible-mode/v1` (alternativa documentada, NO
  requerida). Base TP verificada: `https://token-plan.maas.qwencloudapi.com/compatible-mode/v1`
  (200 en vivo 2026-11-09, skill qwen-web-search §cadena de bases). **Key resuelta SIEMPRE por código:**
  env `DASHSCOPE_API_KEY` → `claves/.env` → `auth.json` (providers alibaba-token-plan, patrón de las
  skills qwen-*); el modelo NUNCA la escribe ni la imprime. Llamada: chat/completions compatible-mode
  con `image_url` data-URI base64 (QVGA ~10 KB viaja inline; docs TP "Add visual understanding").

- **D-F25-5 — CUMPLIMIENTO ToS DEL TOKEN PLAN (hallazgo crítico): worker autónomo con key `sk-sp-`
  PROHIBIDO → dos modos de operación.** *Evidencia:* misma página TP, "Terms of use → Usage scope":
  "Use is limited to interactive use within programming and agent tools… **It must not be used for
  automation scripts, custom application backends, or any non-interactive batch call scenarios**…
  calls initiated interactively within a tool are considered normal use" (webfetch 2026-11-14).
  Un daemon systemd llamando solo con la key del plan = backend de aplicación → riesgo de suspensión/
  BAN de la key. **Solución adoptada (sin preguntas, supuesto documentado):**
  - **Modo A (daemon autónomo `aulasense-vision.service`):** SOLO se activa si la key resuelta es
    pay-as-you-go `sk-*` (DashScope permite backends). El CLI/systemd unit **rechaza `sk-sp-` con log
    honesto** (guarda verificable por el gate seguridad). Sin key → worker idle (regla 13: el sistema
    completo funciona sin IA).
  - **Modo B (agente-mediado, DEFAULT con key TP `sk-sp-`):** el MISMO CLI `tools/vision_describe.py
    --once --limit N` lo lanza interactivamente un agente en sesión (@2-ejecutor→5-dev; el patrón
    sancionado por los ToS: "models are accessed through the tools' extension mechanisms (Skill,
    Slash Command, or Agent)"). El orquestador agenda pasadas (p.ej. tras cada sesión F4 y antes de
    F6a). Rate limits/dedupe/backoff/presupuesto aplican IGUAL en ambos modos (el CLI los impone, no
    el lanzador). La cola queda en BD (`vision_estado='pendiente'`); ningún modo pierde imágenes.
  *Por qué:* cumple la orden del dueño (descripciones IA + búsqueda) sin violar el contrato del
  proveedor ni arriesgar la key del plan; la limitación se documenta honestamente en AGENTS/PROPUESTA.

- **D-F25-6 — RATE LIMIT DE VISIÓN OBLIGATORIO + PRESUPUESTO HONESTO.** Cola persistente en BD
  (columnas `vision_*` en `imagenes`, §8.2). Topes configurables por env: `AULASENSE_VISION_MAX_DIA=120`
  (default conservador ordenado), `AULASENSE_VISION_MAX_HORA=20`; contadores persistentes (tabla
  `vision_uso`) que sobreviven reinicios. **Dedupe por `hash_sha256`** (unique index; una imagen nunca
  se describe dos veces). **Reintentos con backoff exponencial** (máx 2: 10 s/30 s; 429 → respetar
  Retry-After; error de red → `vision_estado='error'` reintetable, no bucle infinito). **Interruptible:**
  SIGTERM limpio + `--stop` + estado en BD (reanudable). **Presupuesto documentado con medición real:**
  QVGA 320×240 ≈ 300–500 tokens de visión + prompt ~100 + salida ~150 ≈ **~1k tokens/imagen**;
  120 imágenes/día ≈ 120k tokens/día (tier flash = Credits bajos; el descuento nocturno TP aplica).
  **Paso 0 obligatorio (F25-a):** 1 llamada de prueba con una imagen real/sintética → leer `usage` de
  la respuesta y el consumo real en Model usage del TP → registrar en `.tmp/f25a_vision_paso0.json` +
  `.tmp/f25_vision_presupuesto.md`. CERO promesas de coste sin medir (patrón E10). El worker registra
  tokens/imagen en `vision_uso` para auditoría del gate.

- **D-F25-7 — BÚSQUEDA EN GALERÍA: LIKE parametrizado sobre `descripcion`/`etiquetas` (+filtros
  canal/estado/device/fecha); FTS5 solo como evolución documentada.** Volumen esperado: cientos de
  imágenes (1 por onset, cooldown 15 s) → `LIKE '%texto%'` parametrizado (escapando `%`/`_`) responde
  en ms; FTS5 añadiría tabla virtual + triggers sin beneficio a esta escala. Criterio de escalado
  documentado: si >5 000 imágenes o latencia >100 ms → migrar a FTS5 (extensión futura). Endpoint
  `GET /api/imagenes?q=&canal=&vision_estado=` (SPEC §9.3); UI: caja de búsqueda en pestaña Galería,
  `esc()`/textContent, sin innerHTML. La descripción la genera el worker (D-F25-4/5) en español,
  1–2 frases + etiquetas separadas por coma (prompt acotado, salida ≤200 tokens).

- **D-F25-8 — LOGIN SEGURO: admin único, Argon2id (argon2-cffi) con fallback `hashlib.scrypt` stdlib,
  sesiones cookie HttpOnly+SameSite=Lax, lockout progresivo por IP+usuario, logout, expiración.**
  - **Hash:** argon2-cffi (wheels linux py3.13 verificados en el ecosistema; en WSL se instala sin
    fricción). Fallback portable SIN dependencias: `hashlib.scrypt` (stdlib; N=2^14, r=8, p=1, salt
    16 B) — cubre el venv Windows py3.15 si el wheel argon2 no existiera. passlib/bcrypt quedan como
    alternativa documentada, no requerida. **Password generado POR CÓDIGO** (`secrets.token_hex(16)`)
    en F25-e → escrito en `claves/.env` (D-F25-9), NUNCA impreso ni commiteado.
  - **Sesiones:** token `secrets.token_urlsafe(32)`; en BD solo su **SHA-256** (tabla `sesiones`);
    cookie `aulasense_sesion` HttpOnly, SameSite=Lax, Path=/, Max-Age=43200 (12 h absoluta) +
    inactividad 2 h (sliding, verificada en cada request); `Secure` omitido CON JUSTIFICACIÓN (http en
    loopback; el navegador trata localhost como secure context — documentado en SPEC §9.2). Logout
    borra la sesión en BD + cookie. Expiradas se limpian al arrancar (DELETE idempotente).
  - **Rate-limit de intentos:** tabla `intentos_login` (ip, username, ts, exito); ventana 15 min:
    ≥5 fallos → lock 60 s; ≥10 → 15 min; ≥20 → 1 h; respuesta **429 + Retry-After**; el lockout es
    TEMPORAL (auto-expira) → **mitigación del encierro del dueño** (R-F25-3): además
    `tools/reset_admin.py` (CLI local, acceso físico = dueño) regenera la password en `claves/.env`
    y limpia lockouts. Éxito resetea el contador.
  - **Cobertura:** TODAS las rutas del dashboard exigen sesión salvo `GET/POST /login`, `POST /logout`,
    estáticos de la página de login y `/internal/*` (máquina-a-máquina local: Origin guard vigente,
    sin sesión — documentado). `WS /ws` valida la cookie en el handshake (403 sin sesión).
    `POST /api/validacion` (F3) pasa a requerir sesión (el operador usa el navegador logueado;
    session_logger CLI escribe por BD, no afectado). **Origin guard INTACTO** y extendido a
    `POST /login` (anti-CSRF junto a SameSite=Lax). XSS/SQL param intactos. Tests existentes se
    adaptan con fixture de sesión (NO se debilitan).
  - **Modelo de amenaza actualizado (AGENTS):** el dashboard deja de ser "solo loopback sin auth" →
    **loopback + auth** (defensa en profundidad: aunque un proceso local o un SSRF intente pegarle,
    sin sesión no lee datos). Bind/exposición loopback intacto (D-F25-3).

- **D-F25-9 — `claves/.env` vive en `aulasense/claves/.env` (carpeta NUEVA dentro del repo,
  gitignored).** *Decisión:* dentro de `aulasense/` (no en la raíz de iot2) porque el server la lee
  relativa al repo (funciona igual desde WSL vía /mnt/c), el `.gitignore` del repo la cubre y no se
  mezcla con otros proyectos de iot2. Contenido (NOMBRES en `claves/.env.example`, valores vacíos):
  `AULASENSE_ADMIN_USER=admin`, `AULASENSE_ADMIN_PASSWORD=` (generada por código en F25-e),
  `DASHSCOPE_API_KEY=` (opcional; el operador la rellena si tiene pay-as-you-go; si vacío se resuelve
  la TP desde auth.json SOLO para modo B). El `.env` raíz sigue para WiFi/MQTT/upload; el loader lee
  ambos (claves/ tiene prioridad para auth/IA). **Entrega al dueño: canal local** (el archivo en disco;
  el dueño lo abre). NUNCA en logs, reportes, evidencia `.tmp/`, docs ni commits. `.gitignore`:
  `claves/*` + `!claves/.env.example`. Regla nueva en AGENTS (15).

- **D-F25-10 — DIAGRAMA TÉCNICO con skill `graphic-arq-software`: C4 contenedores + flujo de datos.**
  Entregable `assets/diagrama_tecnico.dsl` (Structurizr) o fuente equivalente de la skill +
  `assets/diagrama_tecnico.svg` + **`assets/diagrama_tecnico.png`** (versión png obligatoria).
  Contenido: ESP32-S3 (CSI+detector+cámara) → broker.gilman.pro (MQTT motion+image) → WSL Debian
  (ingesta+decoder → SQLite ext4 → dashboard FastAPI loopback+login → worker visión → QwenCloud) →
  navegador Windows; canal alternativo HTTP :8001; fases del pipeline F1→F25. Ejecuta
  @2-ejecutor→5-dev con la skill cargada (bash: structurizr/plantuml/python-diagrams según skill).
  Rutas disjuntas (assets/) → paralelo total.

- **D-F25-11 — PÓSTER VISUAL con skill `qwen-imagen` (@6-medios).** Concepto CSI: ondas Wi-Fi
  atravesando el aula, persona caminando, chip ESP32-S3, dashboard con galería; estilo explicativo
  limpio, texto en español renderizado por el modelo. **Auto-selección del modelo barato** (la skill
  elige sola entre wan2.7-image / qwen-image-3.0-pro según su tabla; NUNCA pregunta). Coste honesto:
  ~$0,03–0,075 por imagen de referencia equivalente en Credits (skill qwen-imagen); 1 póster + máximo
  1 variante. **Paso 0:** validar que la skill cubre la tarea (script + key); si no cubre → reportar,
  NO improvisar (orden del dueño). Entregable `assets/poster_funcionamiento.png` (+ fuente/prompt en
  `.tmp/f25f2_poster.md`).

- **D-F25-12 — VIDEO EXPLICATIVO COMPLETO: guion primero, escenas HappyHorse 480P, ensamblaje FFmpeg
  por 5-dev (graphic-video NO está en el roster de 6-medios).** Cadena:
  1. `docs/guion_video.md` (@4-documentos): narrativa DriveGuard (inicio) → por qué falló el enfoque
     de caras (E1: nunca detectó una cara real en vivo) → qué es CSI → cómo funciona AulaSense →
     pasos de construcción (F1→F25) → finalidad. 6–8 escenas de 5 s + narración por escena + SRT.
  2. Escenas (@6-medios, skill `qwen-video`): **happyhorse-1.1-t2v** (en allowlist TP, verificado
     2026-11-09 por la skill), **480P** (resolución mínima que cumple; auto-selección), clips 5 s,
     ≤8 clips ≈ 40 s. **Presupuesto por segundos documentado:** ~$0,07/s equivalente (skill) →
     ~$2,8–4,2 en Credits; **Paso 0 OBLIGATORIO: 1 clip de prueba de 3–5 s y leer el consumo REAL**
     antes del lote (la propia doc TP lo recomienda: "use a shorter duration and lower resolution,
     check the actual consumption per call"). Si el consumo real es anómalo → parar y reportar.
  3. Voz TTS OPCIONAL (@6-medios, skill `qwen-audio`): `qwen-audio-3.0-tts-plus`, voces verificadas
     longanlingxin (F) / longanlufeng (M), español verificado empíricamente (skill). Si la key/ToS
     no cubre → video con subtítulos sin voz (degradación honesta documentada).
  4. Ensamblaje + subtítulos (@2-ejecutor→5-dev, skill `graphic-video`/FFmpeg): concat escenas +
     audio TTS + SRT burn-in → **`docs/video_aulasense.mp4`** (H.264+AAC, ≤1080p de salida aunque
     las escenas sean 480P). **Paso 0 por skill:** 6-medios valida qwen-video y qwen-audio por
     separado y reporta si algo NO es cubrible — NO improvisar (orden explícita del dueño).
     Intermedios en `assets/video/` (escenas, audio, srt) gitignored si pesan; el mp4 final se
     trackea si <50 MB (si no, gitignored + nota de reproducción).

- **D-F25-13 — CIERRE TOTAL INTEGRADO (orden 8 del dueño).** Tras F25: cerrar F24-d/e0/e/f2/f (cadena
  placa serializada), F4 (3 sesiones ground truth HOY+MAÑANA, usuario autorizó tiempo; panel F3 +
  session_logger), F5a/F5b, F6a (INFORME.docx + PRESENTACION.pptx ACTUALIZADOS con TODO: WSL,
  MQTT-imágenes, vision-descriptions+búsqueda, login, diagramas, video), F6b (ensayo demo sobre el
  runtime WSL con login+galería+imagen MQTT en vivo), GATE ×7 con criterios nuevos (WSL hardening,
  login rate-limit, presupuesto Credits, ToS, payload MQTT) + `auditor-externo` OPCIONAL (doble ciego
  cmdc; se INCLUYE como pasada externa del mega-cierre si `cmdc` está disponible — skill
  validacion-con-commandcode; si no, validación interna y se anota), F7 commit final + **REPORTE DE
  TODO al usuario**. `devops` sigue OMITIDO (sin Docker/CI; el hardening WSL/systemd lo cubren
  seguridad+arquitectura — se anota el porqué).

### 12.2 Refinamiento de los supuestos del orquestador (evidencia → ajuste)

| Supuesto del orquestador | Veredicto | Evidencia / ajuste |
|---|---|---|
| 1. Mosquitto soporta QVGA b64 ~10 KB | **CONFIRMADO con matiz** | message_size_limit default 0 / max_packet_size 2 MB (man oficial 2026-11-14). El cuello NO es el broker sino el **outbox vendor de 8192 B** (mqtt_transport_esp_idf.h:90) → parche 1 línea a 32768 (D-F25-1). Probe empírico F25-a obligatorio (config real del broker no legible). |
| 2. WSL Debian default, sin Docker, venv linux, SQLite vs MariaDB | **REFINADO** | Debian 13 YA existe con systemd (no crear distro). SQLite SÍ (esquema+90 tests existentes; MariaDB = reescritura sin beneficio). Datos en ext4 (WAL sobre 9P no fiable). Red: NAT+portproxy loopback; mirrored per-distro IMPOSIBLE (WSL#13706) y global afectaría 8 distros → NO tocar .wslconfig (D-F25-2/3). |
| 3. Qwen vision qwen3-vl/qwen-vl-max, key del plan | **REFINADO (crítico)** | qwen*-vl NO está en la allowlist TP exact-string → **qwen3.8-flash** (visual understanding, tier barato). **ToS TP prohíben backends autónomos con sk-sp-** → modo B agente-mediado (default) / modo A daemon solo con sk-* pay-as-you-go (D-F25-4/5). Rate limit 120/día + 20/h, dedupe hash, backoff, interruptible, presupuesto medido en Paso 0 (D-F25-6). |
| 4. Login con credenciales en claves\.env (¿dónde?) | **DECIDIDO** | `aulasense/claves/.env` gitignored (D-F25-9); Argon2id + fallback scrypt stdlib; lockout temporal + reset_admin.py local para no encerrar al dueño (D-F25-8). |
| 5. Diagrama técnico graphic-arq-software | **ADOPTADO** | C4 contenedores + flujo, assets/diagrama_tecnico.* + png (D-F25-10). |
| 6. Póster qwen-imagen, modelo barato | **ADOPTADO** | 6-medios, auto-selección, Paso 0, coste ~$0,03–0,075/imagen (D-F25-11). |
| 7. Video HappyHorse + TTS + FFmpeg | **ADOPTADO con ajuste de roster** | graphic-video NO está en las skills de 6-medios → ensamblaje lo hace 5-dev bajo ejecutor; 6-medios solo escenas (qwen-video) + voz (qwen-audio), Paso 0 por skill, 480P mínimo, presupuesto por segundos medido antes del lote (D-F25-12). |
| 8. Cierre total F24+F4–F7+GATE | **ADOPTADO** | F24-c en vuelo NO se toca; F25-c firmware detrás de la cadena placa; F4 hoy+mañana; F6a con TODO; GATE ×7 + auditor-externo opcional; F7 reporte total (D-F25-13). |

### 12.3 Mapa de fases F25 (agente · sub-despachos · background · deps · entregable · criterio)

> Regla: TODA delegación `background: SI`. Techos: ejecutor 500, dev 400, analista 300, especialistas
> 300. Rutas disjuntas por fase → paralelismo máximo. La placa COM6 serializa F24-c→F24-d→F24-e0→
> (F24-e)→F25-c. El cutover WSL (F25-b2) espera F24-c. Detalle ejecutable: checklist v25.

- **F25-a · PROBES de viabilidad + Paso 0 visión** — `@3-analista` (solo lectura/probes inocuos).
  background SI. **Deps: ninguna.** Entregables `.tmp/f25a_viabilidad.md`, `.tmp/f25a_probe_mqtt.json`,
  `.tmp/f25a_vision_paso0.json`, `.tmp/f25_vision_presupuesto.md`. Criterio: (1) publish+subscribe
  paho de 12 KB y 64 KB b64 en `aulasense/v1/test/image_probe_*` (srv-electiva y electiva) → roundtrip
  OK o límite real documentado; (2) 1 llamada visión qwen3.8-flash con imagen de prueba → `usage`
  registrado + modelo confirmado en allowlist; (3) inventario WSL read-only (distro, systemd, python)
  confirmado. ~120 steps ≤300. NO toca firmware/placa ni código de producto.
- **F25-b1 · WSL montaje (sin cutover)** — `@2-ejecutor` → `5-dev` (skill `wsl-manager-cli` cargada).
  background SI. **Deps: ninguna** (paralelo a F24-c; NO para el stack Windows). Entregables
  `server/deploy/aulasense-ingesta.service`, `aulasense-dashboard.service`, `aulasense-vision.service`
  (unit con guarda sk-sp-), `server/deploy/README_wsl.md`, `tools/wsl_aulasense.ps1`
  (start/stop/status/portproxy-refresh/url), `server/requirements-linux.txt`, `.tmp/f25b1_wsl.md`.
  Criterio: venv linux py3.13 creado + deps instaladas; pytest verde en WSL (suite existente);
  paho WSL→broker rc=0 con mensaje real; units systemd instaladas (enabled, aún sin DB migrada);
  portproxy 127.0.0.1:8000→WSL_IP aplicado (o fallback WSL_IP documentado); dashboard de prueba
  responde desde navegador Windows (curl 200). ~350 steps ≤400.
- **F25-b2 · WSL cutover (migración DB)** — `@2-ejecutor` → `5-dev`. background SI. **Deps: F25-b1 +
  F24-c COMPLETA** (no romper el E2E HTTP en vuelo) + F25-d1 (decoder listo). Entregables
  `~/aulasense-datos/aulasense.db` + `imagenes/` (copiados), `.tmp/f25b2_cutover.md`. Criterio:
  stack Windows parado (puertos 8000/8001 libres); conteos de filas DB origen==destino (por tabla);
  servicios WSL activos (systemctl is-active ×3); nodo real → MQTT → SQLite WSL → dashboard <2 s;
  **un solo writer vivo** (verificado); fallback Windows documentado probado (arranque/parada).
  ~150 steps.
- **F25-c · FIRMWARE imagen por MQTT** — `@2-ejecutor` → `5-dev` (firmware). background SI.
  **Deps: F24-c + F24-d completadas (+F24-e0/e si corrieron) + F25-a + F25-d1 + F25-b2** (placa serial
  + receptor WSL vivo). Entregables `firmware/parche_aulasense/app_imagen.c/.h` (extiende, NO rehace),
  `README.md` §F25 (parche outbox 8192→32768 documentado, re-aplicable), Kconfig
  (`AULASENSE_IMAGEN_MQTT` bool default y, `AULASENSE_IMAGEN_MQTT_MAX_B64` 16384),
  `.tmp/f25c_build.log`/`f25c_flash.log`/`f25c_evidencia.md`/`f25c_metricas.json`. Criterio: ≥3
  imágenes reales canal='mqtt' en BD+galería (onset→galería <5 s medido); sha256/dedupe verificado;
  payload >16 KB → fallback/descarte con log y el EVENTO sigue; retain=false verificado (subscribe
  tardío NO recibe fotos viejas); cooldown 15 s y gate memoria intactos; `csi_accepted_pps` no cae
  >10 % vs F1c; sdkconfig pre-flash (SPIRAM octal, sin USB device). ~350 steps ≤400.
- **F25-d1 · INGESTA decoder MQTT-image + esquema** — `@2-ejecutor` → `5-dev` D1. background SI.
  **Deps: ninguna** (contract-first SPEC §6.2/§7.2/§8.2; paralelo YA). Entregables
  `server/ingesta/esquema.py` (migración idempotente §8.2: columnas canal/descripcion/etiquetas/
  vision_* + tablas usuarios/sesiones/intentos_login/vision_uso), `server/ingesta/ingesta_mqtt.py`
  (suscribe `+/image`, handler decoder), `server/tests/test_imagen_mqtt.py`. Criterio: ≥10 tests
  nuevos verdes (b64 válido→archivo+fila canal='mqtt'; b64 corrupto→descarte+log; sha256 mismatch→
  descarte; tamano incoherente→descarte; >64 KB JSON→descarte; device desconocido→descarte;
  dedupe hash→no duplica; FK evento_id por ventana onset; rate 1/10 s→descarte; writer único
  preservado — todo encola en la cola existente). Ruta disjunta: server/ingesta + test_imagen_mqtt.
  ~300 steps ≤400.
- **F25-d2 · WORKER VISIÓN (CLI + rate limits + modes A/B)** — `@2-ejecutor` → `5-dev` D2.
  background SI. **Deps: ninguna** (contract-first §7.3/§8.2; el esquema lo crea D1 — coordinar por
  contrato, tests con DB temporal propia). Entregables `tools/vision_describe.py`,
  `server/vision/__init__.py`+`cliente.py` (resolución de key por código, cadena de modelos, backoff),
  `server/tests/test_vision.py`, `.tmp/f25d2_vision.md`. Criterio: ≥10 tests verdes (dedupe hash,
  tope día/hora con contador persistente, backoff 429, interruptible SIGTERM, guarda sk-sp-→rechaza
  daemon, key vacía→idle honesto, UPDATE solo columnas vision_* con WAL+busy_timeout); 1 pasada real
  modo B sobre ≥1 imagen (descripción+etiquetas en BD, usage registrado); presupuesto actualizado.
  Ruta disjunta: tools/vision_describe.py + server/vision/ + test_vision. ~350 steps ≤400.
- **F25-e · LOGIN SEGURO dashboard** — `@2-ejecutor` → `5-dev` E. background SI. **Deps: ninguna**
  (contract-first §9.2; paralelo YA). Entregables `server/dashboard/auth.py` (nuevo),
  `server/dashboard/app.py` (middleware+login/logout, edición acotada), `static/login.html`,
  `tools/reset_admin.py`, `claves/.env` (GENERADO por código, gitignored), `server/tests/test_auth.py`.
  Criterio: ≥12 tests verdes (login OK→cookie HttpOnly+SameSite; pass mala→401 sin pista; 5 fallos→
  429+Retry-After; lockout progresivo 60 s/15 min/1 h; expiración 12 h/2 h idle; logout invalida;
  rutas protegidas 401/302 sin sesión; /ws sin cookie→403; Origin guard en POST /login→403;
  reset_admin regenera; hash argon2/scrypt verificado; tests previos adaptados con fixture, suite
  completa verde). Password NUNCA impresa (grep evidencia). Ruta disjunta: auth.py+login.html+
  reset_admin+test_auth (app.py: solo wiring de middleware; d3 espera a e para tocar app.py).
  ~350 steps ≤400.
- **F25-d3 · GALERÍA: búsqueda por texto + filtros** — `@2-ejecutor` → `5-dev` D3. background SI.
  **Deps: F25-e + F25-d1** (app.py compartido con login; columnas vision_* de D1). Entregables
  `server/dashboard/app.py` (extiende /api/imagenes con q/canal/vision_estado), `static/index.html`+
  `app.js` (caja de búsqueda + badges descripción/etiquetas/canal), `server/tests/test_dashboard.py`
  (extiende). Criterio: ≥6 tests verdes (q LIKE parametrizado encuentra por descripción/etiquetas;
  comodines %/_ escapados; filtros combinados; sin sesión→401; XSS escapado; ruta absoluta nunca
  expuesta); búsqueda en vivo con ≥1 imagen descrita devuelve la fila. ~180 steps ≤400.
- **F25-f1 · DIAGRAMA TÉCNICO** — `@2-ejecutor` → `5-dev` (skill `graphic-arq-software`). background
  SI. **Deps: ninguna** (assets/ disjunto; el diagrama refleja el contrato SPEC, no espera código).
  Entregables `assets/diagrama_tecnico.dsl` (o fuente de la skill), `.svg`, **`.png`**,
  `.tmp/f25f1_diagrama.md`. Criterio: los 3 archivos existen; el png abre y muestra C4 contenedores
  ESP32→broker→WSL→dashboard + flujo de imagen MQTT/HTTP + worker visión; coherente con SPEC §15.
  ~150 steps.
- **F25-f2 · PÓSTER VISUAL** — `@6-medios` (skill `qwen-imagen`, despacho directo del orquestador).
  background SI. **Deps: ninguna.** Entregables `assets/poster_funcionamiento.png`,
  `.tmp/f25f2_poster.md` (prompt, modelo, coste). Criterio: Paso 0 skill validado; png existe con el
  concepto CSI (ondas/persona/chip/dashboard); coste registrado; si la skill no cubre → reporte
  honesto, NO improvisación. ~80 steps ≤300.
- **F25-f3 · GUION DEL VIDEO** — `@4-documentos` (despacho directo). background SI. **Deps: ninguna.**
  Entregable `docs/guion_video.md` (+ `docs/video_subtitulos.srt` base). Criterio: narrativa completa
  DriveGuard→fallo caras→CSI→cómo funciona→construcción→finalidad; 6–8 escenas de 5 s con prompt
  visual por escena + narración TTS + tiempos SRT coherentes; honestidad (métricas vendor vs propias,
  motion≠presencia). ~150 steps ≤300.
- **F25-f4 · ESCENAS + TTS** — `@6-medios` (skills `qwen-video` + `qwen-audio`, Paso 0 POR SKILL).
  background SI. **Deps: F25-f3.** Entregables `assets/video/escena_01..08.mp4` (happyhorse-1.1-t2v
  480P 5 s), `assets/video/narracion_*.mp3` (qwen-audio-3.0-tts-plus, español), `.tmp/f25f4_video.md`
  (consumo REAL por clip tras el clip de prueba, presupuesto total). Criterio: Paso 0 por skill
  (si algo no cubrible → reportar y parar esa rama, NO improvisar); 1 clip de prueba medido ANTES del
  lote; ≤8 clips; consumo registrado; TTS opcional degradado a subtítulos si no cubre. ~200 steps
  ≤300 + espera asíncrona de generación (poll de la skill).
- **F25-f5 · ENSAMBLAJE VIDEO** — `@2-ejecutor` → `5-dev` (skill `graphic-video`/FFmpeg). background
  SI. **Deps: F25-f4 (+f3 para SRT).** Entregables **`docs/video_aulasense.mp4`**,
  `docs/video_subtitulos.srt` (final), `.tmp/f25f5_ensamblaje.md`. Criterio: mp4 existe; duración
  ≈40–60 s, H.264+AAC, subtítulos burn-in legibles, narración sincronizada (si hubo TTS), abre en
  reproductor local (ffprobe válido); intermedios ordenados en assets/video/. ~150 steps.
- **F25-g · DOCS F25 + re-enmarque + verificación** — `@4-documentos` (directo) + `@3-analista`
  (verificación solo lectura). background SI. **Deps: F25-b2+c+d1+d2+d3+e+f1+f2+f5.** Entregables
  `README.md`, `docs/README.md` (índice), `docs/PROPUESTA.md`, `docs/fuentes.md` (URLs+fechas F25),
  `docs/consentimiento_aula.md` (ACTUALIZADO: tránsito por broker), `docs/politica_retencion_imagenes.md`,
  `.tmp/f25g_verificacion.md`. Criterio: grep CERO afirmaciones vigentes "imágenes nunca por MQTT"/
  "solo loopback sin auth"; C9 cumple-parcial con mitigaciones nuevas; ToS TP y modos A/B documentados;
  presupuesto Credits real documentado; WSL+login+búsqueda+diagramas+video en el índice; el analista
  verifica que toda ruta citada existe. ~200 docs + ~100 analista.
- **F25-h · CIERRE TOTAL** — paraguas de los renglones existentes ACTUALIZADOS en checklist v25:
  F24-d/e0/e/f2/f (cadena placa), F4 (3 sesiones HOY+MAÑANA), F5a/F5b, F6a (INFORME+PPTX con TODO),
  F6b (ensayo en WSL), GATE ×7 (+auditor-externo opcional), F7 (commit final + REPORTE DE TODO al
  usuario, `.tmp/cierre_v25.md`). Ver checklist v25 para el renglón por renglón.

#### Grupos [PARALELO] (maximizados; todas las delegaciones background SI)

- **[PARALELO G-F25-1] LANZAR YA (8 delegaciones + F24-c ya en vuelo):** F25-a (analista) ∥ F25-b1
  (WSL setup) ∥ F25-d1 (decoder ingesta) ∥ F25-d2 (worker visión) ∥ F25-e (login) ∥ F25-f1 (diagrama)
  ∥ F25-f2 (póster, 6-medios) ∥ F25-f3 (guion, 4-documentos). Rutas 100 % disjuntas; ninguna toca la
  placa ni el stack Windows en vuelo (F24-c). F25-d2 y F25-e no pisan app.py (d2 = tools/+server/vision;
  e = auth.py+wiring acotado).
- **[PARALELO G-F25-2] al cerrar F24-c:** F25-b2 (cutover WSL) ∥ F24-d (placa) ∥ F25-f4 (escenas, si
  f3 listo) ∥ F25-d3 (búsqueda, si e+d1 listos) ∥ F4-sesión-1 (operador, ground truth panel F3).
- **[PARALELO G-F25-3] cadena placa serializada:** F24-d → F24-e0 (spike ≤200) → F24-e (solo si PASS)
  → F25-c (MQTT image). En paralelo sin placa: F25-f5 (ensamblaje, si f4 listo), F24-f2 (consolidado),
  F4-sesión-2/3 (mañana), F5a/F5b (tras F4).
- **[PARALELO G-F25-4] cierre:** F25-g (docs) ∥ F24-f (docs F24, se fusiona en F25-g si coincide) →
  F6a ∥ F6b → GATE ×7 (+auditor-externo) → F7.
- Dependencias que IMPIDEN agrupar más (todas demostrables): F25-b2←F24-c (cutover rompería el E2E
  HTTP en vuelo); F25-c←cadena placa (COM6 única + app_imagen.c compartido); F25-d3←F25-e (app.py
  mismo archivo); F25-f4←f3 (guion es entrada); F25-f5←f4 (escenas son entrada); F25-b2←F25-d1
  (el decoder debe estar en el código que migra); F6a←F5a+F5b+F25-g (contenido completo).

### 12.4 Supuestos nuevos F25 (S16–S22, numerados, visibles)

| # | Supuesto | Fuente/verificación | Estado |
|---|---|---|---|
| S16 | broker.gilman.pro acepta payloads ~12 KB (default Mosquitto message_size_limit=0, max_packet_size 2 MB); la config real se confirma con probe | man mosquitto.conf (webfetch 2026-11-14); probe F25-a | Nuevo → gate F25-a; fallback HTTP primario si rechazara |
| S17 | La distro Debian 13 existente (systemd ON) sirve de runtime; NAT+portproxy loopback da acceso desde Windows sin tocar .wslconfig global | wsl -l -v / os-release / wsl.conf leídos 2026-11-14; WSL#13706 (mirrored per-distro imposible) | Nuevo → gate F25-b1 (curl 200 desde Windows) |
| S18 | qwen3.8-flash (visual understanding) está en la allowlist TP y describe QVGA en español con ~1k tokens/imagen; ToS TP exigen modo agente-mediado para sk-sp- | docs.qwencloud.com TP overview (webfetch 2026-11-14); Paso 0 F25-a mide usage real | Nuevo → gate F25-a (llamada real) |
| S19 | argon2-cffi tiene wheel linux py3.13; fallback hashlib.scrypt stdlib cubre cualquier plataforma | ecosistema wheels Debian 13; scrypt es stdlib | Nuevo → gate F25-e (test de hash) |
| S20 | SQLite WAL en ext4 WSL rinde igual o mejor que en Windows; la copia de migración preserva integridad (conteos por tabla) | patrón documentado SQLite/9P; verificación F25-b2 | Nuevo → gate F25-b2 |
| S21 | HappyHorse 480P 5 s ×8 ≈ 40 s cuesta ~$2,8–4,2 equivalentes en Credits; el consumo REAL se mide con 1 clip antes del lote | skill qwen-video (~$0,07/s); doc TP (recomienda medir primero) | Nuevo → gate F25-f4 (Paso 0) |
| S22 | El usuario ejecuta 3 sesiones ground truth hoy+mañana (autorizado); el panel F3 + session_logger bastan para marcar verdad terreno | bitácora 2026-11-14 (F4 autorizado); f3_e2e.md §5 (panel verificado) | Nuevo → F4 |

### 12.5 Riesgos nuevos R-F25-1..10 → mitigación en el checklist

- **R-F25-1 — Coste Credits (video/visión/póster) se dispara.** Mitigación: Paso 0 obligatorio por
  dominio (1 clip de video medido ANTES del lote; 1 llamada visión con usage registrado; póster 1+1);
  480P mínimo; ≤8 clips; tope visión 120/día+20/h; presupuesto honesto en `.tmp/f25_vision_presupuesto.md`
  y `.tmp/f25f4_video.md`; si el consumo real es anómalo → parar y reportar. Checklist: F25-a/f2/f4.
- **R-F25-2 — Red WSL2 (localhostForwarding=false, NAT, IP dinámica, firewall).** Mitigación:
  portproxy listenaddress=127.0.0.1 (exposición loopback estricta) + script refresh de WSL_IP;
  fallback WSL_IP+ORIGINS_EXTRA sin admin; NO tocar .wslconfig global (8 distros); outbound broker
  verificado en b1; :8001 LAN solo opt-in con portproxy+firewall documentado. Checklist: F25-b1/b2;
  gate seguridad (exposición) y arquitectura.
- **R-F25-3 — Lockout que encierra al dueño.** Mitigación: lockout TEMPORAL auto-expirante (60 s/
  15 min/1 h) + `tools/reset_admin.py` local (acceso físico = dueño) que regenera password en
  claves/.env y limpia lockouts; password entregada en archivo local, nunca impresa. Checklist:
  F25-e (test de reset) ; gate seguridad.
- **R-F25-4 — Payload MQTT rechazado (broker o outbox vendor).** Mitigación: probe F25-a (12/64 KB);
  parche outbox 8192→32768 documentado y re-aplicable; tope b64 16 KB en firmware con fallback HTTP/
  descarte; QoS 0 pérdida tolerada (evento es verdad); dedupe sha256; retain=false. Checklist:
  F25-a/c/d1; gate pruebas (fuzz decoder) y rendimiento (pps con publish b64).
- **R-F25-5 — ToS Token Plan: daemon autónomo con sk-sp- = riesgo de BAN.** Mitigación: guarda en el
  CLI/unit que rechaza sk-sp- en modo daemon (modo A solo sk-*); modo B agente-mediado interactivo
  (patrón sancionado); documentado en AGENTS/PROPUESTA/fuentes. Checklist: F25-d2 (test de la guarda);
  gate seguridad (grep unit/CLI).
- **R-F25-6 — Split-brain de BD (Windows y WSL escribiendo a la vez).** Mitigación: protocolo cutover
  stop→copy→verify→start; criterio "un solo writer vivo" (puertos+procesos); fallback Windows exige
  parar WSL antes; DB canónica en ext4 WSL tras b2. Checklist: F25-b2; gate bd/arquitectura.
- **R-F25-7 — Regresión de privacidad (fotos transitan broker compartido).** Mitigación: broker auth
  (electiva/srv-electiva), prefijo aislado, retain=false (no persisten), QoS 0, galería loopback+login,
  retención/purga intactas, consentimiento ACTUALIZADO (tránsito por broker declarado), imágenes
  gitignored, sin caras-ML. C9 sigue cumple-parcial con el texto nuevo. Checklist: F25-g (docs);
  gate seguridad/documentacion.
- **R-F25-8 — Dependencias py3.13 WSL vs py3.15 Windows (argon2/bleak/etc.).** Mitigación:
  requirements-linux.txt separado; scrypt stdlib fallback; bleak opcional fuera de WSL (BLE default n);
  pytest verde en AMBOS runtimes antes del cutover. Checklist: F25-b1/e; gate pruebas.
- **R-F25-9 — Colisión con F24-c en vuelo (placa, parche, receptor, DB).** Mitigación: F25 server-side
  con rutas disjuntas; firmware F25-c detrás de F24-c/d/e0/e; cutover b2 espera reporte F24-c; nadie
  mata procesos ajenos (patrón f3_e2e.md §8). Checklist: deps explícitas + nota de vuelo.
- **R-F25-10 — Skills multimedia no cubren la tarea (video/TTS/póster).** Mitigación: Paso 0 POR
  SKILL en 6-medios (valida script+key+modelo antes de gastar); si no cubre → reportar y degradar
  honestamente (video con subtítulos sin voz; póster alternativo con graphic-imagen vía 5-dev SOLO
  si el orquestador lo aprueba); ensamblaje FFmpeg por 5-dev (graphic-video fuera del roster de
  6-medios). Checklist: F25-f2/f4/f5; gate documentacion (honestidad).

### 12.6 Qué necesita F25 del orquestador (orden de despacho con paralelismo máximo)

1. **LANZAR YA el grupo [PARALELO G-F25-1] completo (8 delegaciones, background SI):** F25-a
   (analista), F25-b1 (ejecutor, skill wsl-manager-cli), F25-d1+d2+e (UN ejecutor con 3 devs
   disjuntos o ejecutores separados — recomendado: 1 ejecutor server con 3 devs para coordinar el
   contrato §8.2), F25-f1 (ejecutor/5-dev graphic-arq-software), F25-f2 (6-medios qwen-imagen),
   F25-f3 (4-documentos guion). F24-c sigue en vuelo en su propia sesión — NO interferir.
2. **Al llegar el reporte de F24-c:** verificar en disco (JPEG reales+fila BD), marcar checklist,
   lanzar [G-F25-2]: F25-b2 (cutover) ∥ F24-d (placa) ∥ F25-f4 (si f3 listo) ∥ F25-d3 (si e+d1
   listos) ∥ F4-sesión-1 (coordinar con el usuario HOY; ground truth por panel F3).
3. **Cadena placa:** F24-d → F24-e0 (spike ≤200 steps; si FAIL → F24-e ABANDONADO documentado) →
   F24-e (si PASS) → **F25-c** (MQTT image). Serializada por COM6.
4. **F4:** 3 sesiones ≥20 min en ≥2 días (HOY 2026-11-14 + MAÑANA 2026-11-15, autorizado). El
   operador marca verdad terreno en el panel (login F25-e si el cutover ya ocurrió). Sesión 3 ideal
   tras F25-c para tener imágenes canal='mqtt' en el dataset.
5. **Pasadas de visión modo B:** tras cada sesión F4 (y antes de F6a), despachar 5-dev interactivo
   que corra `tools/vision_describe.py --once --limit 30` (key TP, ToS-compliant). Registrar consumo.
6. **F25-f5** (ensamblaje) al llegar f4; **F25-g** (docs+re-enmarque) al cerrar b2/c/d/e/f; **F6a**
   (INFORME+PPTX con TODO) tras F5a/F5b/F25-g; **F6b** ensayo en runtime WSL.
7. **GATE ×7 fan-out DIRECTO** (criterios F25 añadidos) + `auditor-externo` opcional (cmdc; si no
   está, validación interna anotada). `devops` omitido (sin Docker/CI; hardening WSL en
   seguridad+arquitectura).
8. **F7 (manos propias):** verificar todo en disco, commit final, `.tmp/cierre_v25.md` y **REPORTE
   DE TODO al usuario** (entregables, métricas, costes Credits reales, limitaciones honestas).
9. **Credenciales:** el dueño NO rellena nada a mano salvo que quiera key pay-as-you-go (opcional).
   `AULASENSE_ADMIN_PASSWORD` la GENERA el código en F25-e → `claves/.env` (gitignored); el orquestador
   indica al dueño la ruta del archivo por canal local (NUNCA imprime el valor en el reporte).
10. **Admin PowerShell:** el portproxy (F25-b1) requiere elevación una vez; si no hay admin, usar el
    fallback WSL_IP documentado (el plan no se bloquea).

### 12.7 Integración con el cierre natural (F24 restante + F4–F7)

- **F24-c (EN VUELO):** intacto; su reporte habilita b2 y la cadena placa. Su canal HTTP sigue
  válido como alternativo (canal='http').
- **F24-d/e0/e:** sin cambios de contrato (SPEC §3.3/§3.4); se ejecutan en la cadena placa ANTES de
  F25-c. El veredicto del spike e0 se documenta igual (ABANDONADO si FAIL).
- **F24-f2:** el consolidado gana las columnas F25 (`canal`, `descripcion`, `etiquetas` opcionales
  como features de texto; `tiene_imagen` ya existe). Se re-genera tras F25-c para incluir filas mqtt.
- **F24-f:** se FUSIONA en F25-g (un solo pase de docs+verificación para no duplicar trabajo).
- **F4:** 3 sesiones hoy+mañana; el dataset incluye imágenes HTTP (hoy) y MQTT (tras F25-c);
  ground truth por panel F3 (con login si el cutover ocurrió); consolidado SOLO real.
- **F5a/F5b:** consumen el esquema extendido; las features de texto (etiquetas) son opcionales sin
  romperse si null. F5b incluye el guion demo actualizado (WSL+login+galería+búsqueda).
- **F6a:** INFORME.docx + PRESENTACION.pptx regenerados con TODO (WSL, MQTT-imágenes, vision+
  búsqueda, login, diagramas técnico+póster, video embebido/enlazado, métricas F4/F5, costes Credits
  reales, limitaciones honestas: ToS, QoS 0, motion≠presencia, privacidad parcial).
- **F6b:** ensayo completo sobre WSL: wsl_aulasense.ps1 start → login → estímulo → motion+buzzer+LED
  → imagen MQTT en galería <5 s → búsqueda por texto → stop. <5 min de setup.
- **GATE:** los 7 especialistas con criterios F25 (ver checklist v25) + auditor-externo opcional.
- **F7:** commit final (manos propias del orquestador) + cierre_v25.md + REPORTE DE TODO al usuario.