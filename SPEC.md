# SPEC.md — Especificación técnica AulaSense (ESP32-S3 GOOUUU v1.5 + ESPectre Wi-Fi CSI)

> Companion de `PLAN.md`. Todo dato de hardware/contrato cita fuente. Lo que se mida en los gates
> F1a–F1d se anota en `../.tmp/f1*_*.md` y actualiza esta SPEC. Fuentes primarias verificadas por el
> planificador el 2026-11-12 (webfetch gratuito): README, Native README, docs/API.md, docs/CLI.md,
> docs/SETUP.md de `github.com/francescopace/espectre`; `espectre.dev/sdk/detectors`;
> `github.com/espressif/esp-csi`. Pinout heredado corregido del analista de viabilidad.

## 1. Hardware objetivo

| Componente | Valor | Fuente/verificación |
|---|---|---|
| Placa | GOOUUU ESP32-S3-CAM **v1.5** (clon compatible ESP32-S3-EYE) | dashcam-ia SPEC §1 (verificado F0a/F1) |
| SoC | ESP32-S3 Xtensa LX7 dual-core 240 MHz, SIMD | dashcam-ia SPEC; ESPectre Native CPU 240 MHz S3 |
| Flash | 16 MB (N16R8) | dashcam-ia `.tmp/env_ok.md` (flash_id 16 MB) |
| PSRAM | 8 MB octal embebida (AP_3v3, `SPIRAM_MODE_OCT`) | dashcam-ia `.tmp/env_ok.md` |
| Cámara | OV3660 presente **pero NUNCA inicializada** (Native no usa cámara; verificar sdkconfig sin driver camera) | D9; Native README |
| WiFi | 2.4 GHz Wi-Fi 4 (802.11n) — obligatorio para CSI (S3 no ve 5 GHz) | ESPectre README "Supported hardware" |
| BLE | BLE 5.0 (opcional en el nodo, default n; gate coexistencia CSI) | dashcam-ia SPEC; D5 |
| USB-serial | CH340K (COMx autodetectado) **o** USB-C nativo USB-Serial-JTAG (hoy COM6) | dashcam-ia SPEC §1 (F18 flasheó por COM6 JTAG) |
| Sensor temp | **Temperatura interna del die S3** (proxy de carga de cómputo, NO ambiente — S11) | analista tendencias; products.espressif.com |
| Costo extra | **$0** (solo placa + router Wi-Fi existente) | D1, E4 |

## 2. Pinout FIJADO (corregido por el analista de viabilidad, 2026-11-12)

> Corrección clave: el UART0 real es **43/44** (la SPEC vieja de DriveGuard decía "41–44 UART0", mal).
> **41/42 están LIBRES**. Fuente: `github.com/bbhpy/docs/blob/main/GOOUUU_ESP32-S3-CAM.md` (mapa 2×20
> de esta placa exacta) + `github.com/profharris/GOOUUU-Tech-ESP32-S3-CAM-Expansion-Board`.

| GPIO | Estado | Función | Uso en AulaSense |
|---:|---|---|---|
| 0 | Reservado | BOOT (strapping) | Recuperación ESPectre: mantener BOOT 3 s borra WiFi → Improv Serial |
| 1 | Casi libre | SDA QWIIC I2C (ADC1_CH0) | Libre si no se usa QWIIC (no se usa) |
| 2 | Casi libre | SCL QWIIC I2C (ADC1_CH1) | Libre si no se usa QWIIC (no se usa) |
| 3 | Ocupado | LED flash | No usado por AulaSense |
| 4–18 | Ocupado | Bus DVP cámara | **Nunca inicializado** (sin cámara) |
| **14** | Ocupado | **Buzzer piezo (LEDC PWM)** | **Feedback audible: beep en onset de motion (D5)** |
| 19/20 | **PROHIBIDO** | USB nativo D-/D+ | **NUNCA tocar** (USB device stack = riesgo brick, E5) |
| 21 | Libre | Header (SPICLK SoC) | Disponible (no usado en ruta crítica) |
| 22–25 | NO EXISTEN | — | — |
| 26–32 | Ocupado | QSPI flash interna | — |
| 33–34 | NO/FALTA | — | — |
| 35–37 | Ocupado | PSRAM octal | — |
| 38/39/40 | Libre si no SD | microSD | No se usa SD |
| 41 | Libre | Header (MOSI SPI-LCD no conectado) | Disponible |
| 42 | Libre | Header (SCLK SPI-LCD no conectado) | Disponible |
| **43/44** | Reservado | **UART0 real TX0/RX0** (consola/flasheo) | **NUNCA usar para periféricos** |
| 45/46 | Reservado | strapping | — |
| 47 | Libre | SPICLK_N SoC | Disponible |
| **48** | Ocupado | **WS2812 RGB NeoPixel onboard** | **Feedback visual: color por estado idle/motion (D5)** |

**Pool libre utilizable** (sin SD ni QWIIC): 1, 2, 21, 41, 42, 47 (+38/39/40). El feedback local
(buzzer 14 + WS2812 48) **no necesita ningún GPIO libre extra** (ya son periféricos onboard).
**Guardas de compilación** en `app_feedback.c`: `#error` si se configura un pin prohibido (19/20/43/44
o cualquier pin de cámara 4–18). Fuente: analista viabilidad §1 + dashcam-ia `app_audio.c:142-146`.

## 3. Firmware: ESPectre Native v3.2.0 (fork académico GPLv3)

- **Vendor:** `git clone --branch v3.2.0 --depth 1 https://github.com/francescopace/espectre.git firmware/espectre` (gitignored, reproducible por tag). LICENSE GPLv3 preservado. El derivado (parche) se trackea en `firmware/parche_aulasense/` (S9: fuente publicado al profesor).
- **App Native:** `firmware/espectre/src/cpp/frontend/native/app` (proyecto ESP-IDF standalone).
- **ESP-IDF requerido: v5.5.6** (docs/CLI.md lo recomienda; SDK ≥5.5.3). Desviación documentada de E5 (v5.4 queda instalado para el archive). Instalación: `eim install 5.5.6` (verificar sintaxis eim en F1a).
- **Detector (elige en runtime, conmutable por MQTT/Direct):**
  - **High Accuracy** (default elegido): MLP `8→24→12→1`, **529 parámetros**, estado 5.068 B, SIN calibración (threshold 0.5), necesita segundos de CSI válido. Métricas vendor (espectre.dev/sdk/detectors, 20 grabaciones reales): recall 99,6 %, FPR 0,5 %, F1 99,3 %, 3,02 ms/detección, CPU 11,83 %.
  - **Lightweight** (alternativa si CPU/memoria aprieta): 2 features, combinación logística fija, estado 2.016 B, calibración ~10 s (hasta ~30 s con burst). Métricas vendor: recall 98,5 %, FPR 1,1 %, F1 98,1 %, 0,783 ms, CPU 9,09 %.
  - **Honestidad (E10/S10):** las métricas vendor son de SU dataset; el piloto F1c mide recall/FPR LOCALES contra verdad terreno manual. No se prometen las cifras vendor como propias. Detecta **MOVIMIENTO**, no presencia estacionaria, no sala vacía, no conteo de personas.
- **CSI:** capture HT20 2.4 GHz, ~100 muestras/s, decisión cada 250 ms. Tráfico por defecto = ping al gateway; modos `ping|dns|dns_tcp|wifi_raw|external` (swaps si el aula bloquea ICMP, S7).
- **Consola:** UART en S3 (TinyUSB CDC solo S2 → **sin riesgo E5 de USB device stack**). `ESPECTRE_EMULATOR_CSI_REPLAY` activo por defecto en S3 pero solo arranca con marcador de MAC de emulador (inocuo en chip real).
- **Recuperación:** BOOT 3 s (GPIO0) borra WiFi → Improv Serial. Reflasheo USB full factory image = recovery de OTA.
- **Cámara:** Native NO inicializa la cámara. Verificar en F1b que sdkconfig no incluye driver camera (D9/E9).

### 3.1 Parche `app_feedback.c` (único código de firmware propio, D5)
Módulo añadido al app Native (vía `firmware/parche_aulasense/`, instrucciones de aplicación en su README):
- **Buzzer GPIO14 (LEDC PWM):** beep corto (880 Hz, ~110 ms) en cada onset `idle→motion`, cooldown 5 s (evita spam). Tarea prioridad baja (no compite con el detector).
- **WS2812 GPIO48:** color por estado — p.ej. azul tenue en `idle`, azul brillante/verde en `motion` (el LED onboard es azul; usar intensidad/canal). Supervisor único del pin.
- **Die temp:** `temperature_sensor` driver del S3 → publica cada 30 s en topic `{prefix}/{device_id}/aulasense_temp` `{"timestamp_ms":…,"die_temp_c":…}` sobre el MISMO cliente MQTT de Native (aditivo, no rompe el contrato nativo).
- **BLE NimBLE notify (OPCIONAL, Kconfig default n):** char estado estilo `0xA111` (paquete binario corto) en onset de motion. **Gate:** activar solo si `csi_accepted_pps` no cae vs F1c (coexistencia WiFi+BLE puede afectar capture, R7).
- **Kconfig:** `AULASENSE_FEEDBACK` (bool, default y), `AULASENSE_BUZZER_GPIO` (default 14), `AULASENSE_WS2812_GPIO` (default 48), `AULASENSE_DIE_TEMP` (bool default y), `AULASENSE_BLE` (bool default n), `AULASENSE_BUZZER_COOLDOWN_S` (default 5). Guardas `#error` en pines prohibidos.

## 4. Build, flasheo y provisioning — comandos exactos (PowerShell)

```powershell
# F1a · Toolchain IDF 5.5.6 (desviación documentada de E5 v5.4) + vendor
eim install 5.5.6                      # verificar sintaxis exacta de eim en F1a
eim run "idf.py --version" v5.5.6      # espera: ESP-IDF v5.5.6
git clone --branch v3.2.0 --depth 1 https://github.com/francescopace/espectre.git aulasense/firmware/espectre

# F1b · Build Native para esp32s3 (ruta primaria: idf.py directo vía eim, sin CLI Python)
cd aulasense/firmware/espectre/src/cpp/frontend/native/app
eim run "idf.py set-target esp32s3" v5.5.6
eim run "idf.py build" v5.5.6
# Fallback si el app requiere el CLI: espectre.cmd native build --chip s3 --backend local
#   (el CLI busca IDF_PATH activo primero; eim lo provee. Entorno Python del CLI: 3.14 recomendado,
#    pero la ruta idf.py directa NO necesita ese venv — preferida por E5 python roto.)
# Verificar sdkconfig ANTES de flashear: SIN USB device stack, SIN camera, PSRAM octal 8MB, flash 16MB
eim run "idf.py -p COM6 flash monitor" v5.5.6   # COM6 = USB-Serial-JTAG nativo (autodetectar; monitor sale Ctrl+])

# Provisioning WiFi (2.4 GHz): ruta primaria = Kconfig defaults generados desde .env (gitignored)
C:\Python315\python.exe tools\gen_firmware_config.py   # lee .env → sdkconfig.defaults.local (WIFI_SSID/PASS), NUNCA imprime clave
# Fallback: Improv Serial por navegador (Chrome 151+, espectre.dev/tools/flash) o espectre.cmd provision
#   (clave por ESPECTRE_WIFI_PASSWORD env var, NUNCA argv — coincide con regla de secretos)

# F1d · Config MQTT al broker (lee .env, PATCH /mqtt, nunca imprime clave)
C:\Python315\python.exe tools\config_mqtt.py    # scheme/host/port/user/pass/topic_prefix desde .env

# F2 · Server PC (venv py3.15, sin Docker)
C:\Python315\python.exe -m venv server\.venv
server\.venv\Scripts\python.exe -m pip install -r server\requirements.txt
server\.venv\Scripts\python.exe server\ingesta\ingesta_mqtt.py
server\.venv\Scripts\python.exe server\dashboard\app.py        # http://127.0.0.1:8000
server\.venv\Scripts\python.exe server\fuentes\openmeteo.py

# Demo (un solo comando, patrón heredado)
powershell tools\start_demo.ps1     # ingesta + dashboard + openmeteo (+ BLE si aplica); PIDs en .tmp/demo_pids.json
powershell tools\stop_demo.ps1      # mata SOLO los procesos que arrancó el launcher

# Análisis
powershell analysis\run_analisis.ps1 --origen real --validacion docs\validacion_sesion1.csv
```

## 5. Direct HTTP (LAN, feedback en vivo E8)
- Puerto TCP **62587**, base `/espectre/v1`, protocolo 1.0. Recursos: `health`, `device`, `capabilities`, `sensing`, `wifi`, `diagnostics`, `events` (SSE), `mqtt`, `ota`, `csi` (collection opcional, NO usada).
- **Monitor** (espectre.dev/tools/monitor) y **Device settings** (espectre.dev/tools/device-settings) = feedback en vivo del sensing y configuración (E8). El PC debe estar en la misma LAN.
- Las tools propias (`config_mqtt.py`, sondeo de diagnósticos) envían header `Origin: https://test.espectre.dev` (permitido por defecto, igual que el CLI) + `Content-Type: application/json`. Cuerpo ≤2048 B.
- `GET /espectre/v1/diagnostics?fields=[…]` (query URL-encoded) o cuerpo JSON con `fields`. `["*"]` = todo.
- **Seguridad Direct HTTP:** pensado para LAN confiable; firmware acepta solo orígenes listados, exige preflight Private Network Access, limita cuerpo/colas/clientes/rate; escucha solo en la interfaz station; nunca devuelve claves almacenadas.

## 6. Contrato MQTT (fuente: docs/API.md, verificado 2026-11-12)

- **Broker:** `broker.gilman.pro:14769` (E6; scheme `mqtt` plano o `mqtts` TLS CA pública — ambos nativos; NO WebSocket, NO CA privada, S8). Auth usuario `electiva`, clave en `.env` (gitignored, NUNCA impresa).
- **Base topic:** `aulasense/v1/devices/{device_id}` (`topic_prefix=aulasense/v1/devices` configurado vía `PATCH /mqtt`; aísla del broker compartido, R6). `device_id` = 16 hex lowercase (estable por nodo).
- **Topics (suffix sobre la base):**

| Suffix | Retained | Payload / propósito |
|---|---|---|
| `health` | sí | `{"status":"ok|offline","online":bool,"uptime_s":int,"timestamp_ms":int}` — Last Will offline |
| `device` | sí | `{"device_id","name","label","frontend":"native","firmware":"3.2.0","chip":"esp32s3","csi_profile":"ht20"}` |
| `capabilities` | sí | protocolo 1.0, resources/operations/events soportados |
| `sensing` | sí | `{"enabled","ready","calibrating","mode","detector","threshold","motion_on_hits","motion_off_hits","traffic_generator_mode","csi_target_pps",…}` |
| `wifi` | sí | estado radio redactado (sin SSID/BSSID/IP en MQTT): `{"configured","connected","band","channel","rssi_dbm"}` |
| `motion` | **no** | **`{"timestamp_ms":int,"state":"idle|motion","score":float}`** por evaluación del detector (cada 250 ms) |
| `fault` | no | `{"timestamp_ms":int,"message":str}` |
| `commands/result` | no | resultado correlacionado de un comando |
| `aulasense_temp` | no | **parche D5:** `{"timestamp_ms":int,"die_temp_c":float}` cada 30 s |

- **Comandos:** publicar en `commands/request`: `{"command_id":"cmd-42","command":"read_diagnostics","fields":[…]}`. `command_id` 1–64 chars ASCII. Resultado en `commands/result` repite command_id+command y añade `accepted`,`code`,`message`,`data`. Comandos: `update_device`, `update_sensing`, `recalibrate`, `read_diagnostics`, `check_ota`, `start_ota`.
- **Reloj (crítico, R3):** `timestamp_ms` es reloj MONÓTONO del dispositivo (ms desde boot), NO época. La ingesta mapea a época (patrón heredado del fix de reloj DriveGuard):
  - Al recibir `health`/`device` con `uptime_s`+`timestamp_ms`: `epoca_boot = now_pc_ms − timestamp_ms`.
  - `evaluacion.ts_ms = epoca_boot + timestamp_ms`; `hora_sinc=1` si la estimación es fresca.
  - Si el nodo reinicia (uptime se resetea), re-estimar `epoca_boot`. Fallback de fecha: `recibido_en`.
- **Métricas edge (C12, D4):** la ingesta publica `read_diagnostics` cada 30 s con fields:
  `detection_avg_us, detection_max_us, runtime_load_percent, free_memory_kb, minimum_free_memory_kb, cpu_frequency_mhz, csi_accepted_pps, csi_callback_pps, generator_pps, traffic_tx_pps, traffic_rx_pps, wifi_rssi_dbm, wifi_channel, csi_hw_error_total, csi_occupancy, mqtt` → fila en `telemetria`.

## 7. Ingesta (server/ingesta/ingesta_mqtt.py)
- Suscribe `aulasense/v1/devices/+/motion`, `+/health`, `+/device`, `+/sensing`, `+/wifi`, `+/fault`, `+/commands/result`, `+/aulasense_temp`.
- `paho-mqtt` callback → cola → **writer único** a SQLite (WAL). Validador de esquema por topic (`esquema.py`): tipo errado → 422/log, no escribe. `origen` default `real`; `sintetico` SOLO vía `POST /internal/evento` (C13).
- **Derivación de bordes:** track del último state por device; `idle→motion` = evento `MOVIMIENTO_INICIO`, `motion→idle` = `MOVIMIENTO_FIN` (con `duracion_ms` = tiempo en motion). `health offline` = `NODO_OFFLINE`, `online` = `NODO_ONLINE`. `fault` = `FALLA`.
- **Diagnostics poller:** cada 30 s publica `read_diagnostics` → `commands/result` → `telemetria`.
- **Export CSV acumulativo:** `server/datos/export_evaluaciones.csv` (todas las filas) + `analysis/dataset/consolidado.csv` (SOLO `origen=real`, entregable F4).

## 8. Esquema SQLite (server/datos/aulasense.db, WAL)

```sql
CREATE TABLE IF NOT EXISTS dispositivos (
  device_id TEXT PRIMARY KEY, label TEXT, frontend TEXT, firmware TEXT, chip TEXT, csi_profile TEXT,
  first_seen_ms INTEGER, last_seen_ms INTEGER,
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS evaluaciones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL,
  ts_ms INTEGER NOT NULL, ts_device_ms INTEGER NOT NULL, hora_sinc INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('idle','motion')), score REAL NOT NULL,
  sesion_id INTEGER, origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_eval_ts ON evaluaciones(ts_ms);
CREATE INDEX IF NOT EXISTS idx_eval_dev_ts ON evaluaciones(device_id, ts_ms);
CREATE TABLE IF NOT EXISTS eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  tipo TEXT NOT NULL CHECK(tipo IN ('MOVIMIENTO_INICIO','MOVIMIENTO_FIN','CALIBRACION','FALLA','NODO_OFFLINE','NODO_ONLINE')),
  score REAL, duracion_ms INTEGER,
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_eventos_ts ON eventos(ts_ms);
CREATE TABLE IF NOT EXISTS telemetria (
  id INTEGER PRIMARY KEY AUTOINCREMENT, device_id TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  detection_avg_us REAL, detection_max_us REAL, runtime_load_percent REAL,
  free_memory_kb REAL, minimum_free_memory_kb REAL, cpu_frequency_mhz INTEGER,
  csi_accepted_pps REAL, csi_callback_pps REAL, generator_pps REAL, traffic_tx_pps REAL, traffic_rx_pps REAL,
  wifi_rssi_dbm INTEGER, wifi_channel INTEGER, csi_hw_error_total INTEGER, csi_occupancy REAL,
  detector TEXT, threshold REAL, traffic_generator_mode TEXT, die_temp_c REAL,
  origen TEXT NOT NULL DEFAULT 'real', recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS validacion_sesiones (
  id INTEGER PRIMARY KEY AUTOINCREMENT, sesion TEXT NOT NULL,
  inicio_ms INTEGER NOT NULL, fin_ms INTEGER NOT NULL,
  verdad_terreno TEXT NOT NULL CHECK(verdad_terreno IN ('motion','idle')),
  fuente TEXT NOT NULL DEFAULT 'manual_operador', notas TEXT,
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS fuentes_externas (
  id INTEGER PRIMARY KEY AUTOINCREMENT, fuente TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  lat REAL, lon REAL, temperatura_c REAL, humedad_pct REAL, precipitacion_mm REAL, viento_kmh REAL,
  payload_json TEXT, recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
```
SQL siempre parametrizado; límites en `limite`/`device_id`; writer único (la ingesta). Migraciones idempotentes (`ALTER TABLE` solo lo corre la ingesta).

## 9. Dashboard (server/dashboard/app.py, FastAPI, puerto 8000, bind 127.0.0.1)
- `GET /` → `static/index.html` (Chart.js + Tailwind CDN, una página, pestañas Monitoreo / Reportes / Validación).
- `GET /api/dispositivos` → snapshot por device (estado actual, última evaluación, métricas edge, conteos motion/hora, sesiones, en_vivo/stale).
- `GET /api/evaluaciones?device_id=&desde=&hasta=&limite=` (1–5000), `GET /api/eventos?device_id=&tipo=&limite=`, `GET /api/resumen` (KPIs), `GET /api/telemetria?device_id=&limite=`, `GET /api/validacion?sesion=` (matriz de confusión), `GET /api/fuentes_externas?fuente=&limite=`, `GET /api/export?tabla=&origen=` (CSV, real por default).
- `POST /internal/evento` → inyección sintética/test (Origin guard, reusa validador de ingesta, 201/422). Writer de producción sigue siendo la ingesta.
- `WS /ws` → push live de evaluaciones/eventos (Origin guard; latencia <2 s objetivo).
- **Monitoreo:** tarjeta por device con estado grande idle/motion, score gauge, métricas (detection_avg_us, CPU%, heap, CSI pps, RSSI), feed últimos eventos, badge en_vivo/stale, flash + beep WebAudio en onset motion (toggle alertas sonoras).
- **Reportes:** serie temporal score + métricas edge, motion events/hora, heatmap por hora, tabla paginada, export CSV.
- **Validación:** matriz de confusión por sesión (recall/FPR/latencia vs verdad terreno manual), nota de tamaño muestral.
- **Seguridad (D10, heredada E7):** bind 127.0.0.1 (nunca 0.0.0.0); Origin guard en `/ws` y `/internal/*` (403 cruzado; ampliable SOLO por `AULASENSE_ORIGINS_EXTRA` host[:puerto] exactos sin wildcards); `esc()`/`textContent` XSS; SQL parametrizado; **cero endpoints de imágenes** (no existen imágenes en el sistema).

## 10. Multi-fuente (C6) y análisis (C5)
- **Fuente 1:** eventos/evaluaciones CSI (nodo). **Fuente 2:** die temp S3 (parche D5, proxy carga cómputo, S11). **Fuente 3:** Open-Meteo (`server/fuentes/openmeteo.py`, gratis sin key, hourly/current, coords `.env`; degrada con elegancia si coords vacías, P1).
- **EDA (`analysis/eda.py`):** motion events/día y hora, distribución de score, patrones por sesión, calidad CSI (occupancy, hw errors), métricas edge, correlación die temp↔runtime_load, cruce Open-Meteo.
- **Modelo (`analysis/modelo_ocupacion.py`):** features = hora del día, día semana, actividad móvil (ventanas), die temp, Open-Meteo → target = `motion` en ventana siguiente 10 min. LogisticRegression + DecisionTreeClassifier, split temporal 80/20, métricas accuracy/precision/AUC + importancia de features → `assets/`. **Nota obligatoria de tamaño muestral** (E10). Entrenado SOLO con `origen=real`.

## 11. Guion de demo (C8/E3, detalle en docs/guion_demo.md)
1. `start_demo.ps1` → ingesta + dashboard + openmeteo (PIDs en `.tmp/demo_pids.json`).
2. Nodo ya flasheado y provisionado; abrir Monitor (feedback vivo) + dashboard `http://127.0.0.1:8000`.
3. Zona quieta → dashboard `idle`, WS2812 tenue. (Lightweight: esperar calibración ready; High Accuracy: segundos de CSI válido.)
4. Operador camina por la zona (o mueve un objeto) → `motion` en <2 s, buzzer beep, WS2812 brillante, tarjeta flash + beep dashboard.
5. Operador se queda quieto → vuelve a `idle`.
6. Repetir por distintas zonas (cobertura). Mostrar métricas edge en vivo (detection_avg_us, CPU%, CSI pps).
7. `stop_demo.ps1` limpia PIDs.
- **Sin humanos frágiles:** el estímulo es trivial (caminar/mover objeto), no fisiología ni encuadre. **Honesto:** `idle` ≠ sala vacía (S10).

## 12. WiFi y secretos (E6/D10)
- `.env` (copiado de `.env.example`, GITIGNORED): `MQTT_HOST`, `MQTT_PORT`, `MQTT_SCHEME`, `MQTT_USER`, `MQTT_PASS` (vacío en example), `MQTT_TOPIC_PREFIX`, `WIFI_SSID`, `WIFI_PASS`, `ESPECTRE_WIFI_PASSWORD`, `DEVICE_LABEL`, `OPENMETEO_LAT/LON`, `AULASENSE_ORIGINS_EXTRA`, `DASHSCOPE_API_KEY` (opcional).
- WiFi del nodo: Kconfig defaults generados por `tools/gen_firmware_config.py` desde `.env` → `sdkconfig.defaults.local` (gitignored), o NVS vía Improv Serial. **La clave real NUNCA en docs/logs/argv/evidencias.**
- MQTT: `tools/config_mqtt.py` lee `.env` y hace `PATCH /mqtt`; nunca imprime la clave. Si un componente necesita una clave nueva, se genera con `secrets.token_hex(16)` en código (regla heredada).
- Red 2.4 GHz obligatoria. Plan B demo: hotspot móvil o "Mobile hotspot" de Windows.

## 13. Limitaciones honestas (declaradas, no adornadas)
- ESPectre detecta **MOVIMIENTO**, no presencia estacionaria, no prueba sala vacía, no identifica ni cuenta personas (S10, README vendor).
- Una placa cubre una zona (one board per room); colocación 3–8 m del AP, fuera de metal; sensible al entorno RF (R1).
- Métricas vendor (F1 98,1–99,3 %) son de SU dataset; las locales se miden en F1c/F4 (E10).
- Die temp ≠ temperatura ambiente (S11).
- Reloj del nodo monótono; la época la estima el PC (R3); sin SNTP en el nodo.
- BLE en el nodo es opcional y puede degradar CSI (R7); default n.
- GPLv3: el derivado debe publicar fuente (S9); uso académico OK.
- MQTT: sin WebSocket ni CA privada en Native (S8); si el broker exigiera CA privada → fallback puente local (decide orquestador).