# PLAN.md — AulaSense: sensing de movimiento/ocupación por Wi-Fi CSI con ML en el ESP32-S3

> Proyecto nuevo IoT2/ITSE (2026-11-12). Reemplaza al abandonado DriveGuard (`dashcam-ia/`, archivable
> por el orquestador a `iot2/archive/`). Plan maestro del `planificador`; lo ejecuta el `@1-orquestador`
> con el roster real. Checklist vivo: `../.tmp/checklist.md` (**v24**; v23 archivada en `checklist_pre_v24.md`). Especificación: `SPEC.md`.
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
  lectura para inspiración, no copia ciega): AP fallback si STA falla N=3 reintentos; SSID
  `AULASENSE-CFG-<4 hex MAC>`; **AP abierto CON aviso** (patrón D6, app_portal.c:118-120,387) o WPA2 opcional
  (`AULASENSE_PROVISION_AP_PASS`); **ruta privada** `http://192.168.4.1/cfg/<token>` (token = 8 hex de clave
  efímera NVS `aulasense_prov/cfg_token` generada con `esp_fill_random`, **mostrada UNA vez en serial**,
  ruta incorrecta → 404 sin pista); POST con **Origin/Referer guard 403** (app_portal.c:73-98,260),
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
  trae pedestrian), `:215` (tamaño .espdl espdet_pico INCONCLUSO → resolver en F24-a/e0); vendor
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
| S15 | Existe un modelo de PERSONA esp-dl/ESP-WHO (pedestrian_detect/espdet_pico escala humana) que cabe en PSRAM con arena y coexiste con el CSI; tamaño .espdl INCONCLUSO hasta medir | f23_research_viabilidad.md:100,104,215 | Nuevo → gate F24-a (verificar modelo) + F24-e0 (spike coexistencia); si FAIL → ABANDONADO |

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
- **R14 — Modelo persona esp-dl disponibilidad/tamaño no verificado (INCONCLUSO research:215).**
  **Mitigación:** F24-a verifica modelo exacto + tamaño; F24-e0 spike mide coexistencia; si no cabe →
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