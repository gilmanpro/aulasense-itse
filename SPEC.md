# SPEC.md — Especificación técnica AulaSense (ESP32-S3 GOOUUU v1.5 + ESPectre Wi-Fi CSI)

> Companion de `PLAN.md`. Todo dato de hardware/contrato cita fuente. Lo que se mida en los gates
> F1a–F1d se anota en `../.tmp/f1*_*.md` y actualiza esta SPEC. Fuentes primarias verificadas por el
> planificador el 2026-11-12 (webfetch gratuito): README, Native README, docs/API.md, docs/CLI.md,
> docs/SETUP.md de `github.com/francescopace/espectre`; `espectre.dev/sdk/detectors`;
> `github.com/espressif/esp-csi`. Pinout heredado corregido del analista de viabilidad.
>
> **AMPLITUD F24 (2026-11-12, orden explícita del dueño):** esta SPEC añade los contratos de
> **evidencia de imagen local** (§3.2, §6.1, §7.1, §8.1, §9.1), **provisioning AP de fallback con ruta
> privada** (§3.3), **IA de visión opcional spike-gated** (§3.4, §6.1) y **recopilación big data
> académica** (§10.1). La privacidad se RE-ENMARCA: la regla "cero imágenes / privacidad por
> construcción" (D9 v23) queda **REVOCADA** por decisión del usuario → **privacidad acotada local-only**
> (D-F24-1, §14). C9 = cumple-parcial. El tag vendor correcto es **`3.2.0` (SIN prefijo v)** —
> `v3.2.0` da 404 (verificado F1a, `.tmp/f2_backend.md` FLAG 1). Las decisiones D-F24-1..8 con
> evidencia están en §14 y `PLAN.md` §11.

## 1. Hardware objetivo

| Componente | Valor | Fuente/verificación |
|---|---|---|
| Placa | GOOUUU ESP32-S3-CAM **v1.5** (clon compatible ESP32-S3-EYE) | dashcam-ia SPEC §1 (verificado F0a/F1) |
| SoC | ESP32-S3 Xtensa LX7 dual-core 240 MHz, SIMD | dashcam-ia SPEC; ESPectre Native CPU 240 MHz S3 |
| Flash | 16 MB (N16R8) | dashcam-ia `.tmp/env_ok.md` (flash_id 16 MB) |
| PSRAM | 8 MB octal embebida (AP_3v3, `SPIRAM_MODE_OCT`). **El app Native NO la usa hoy** (`app/sdkconfig:1300` `# CONFIG_SPIRAM is not set`); F24 la habilita para cámara+cola+arena esp-dl | vendor sdkconfig verificado en disco; F1b precheck |
| Cámara | OV3660 presente. **F24: SÍ se inicializa bajo `AULASENSE_IMAGEN=y`** (captura QVGA JPEG al onset de motion, evidencia de evento). La regla v23 "NUNCA inicializada" queda revocada (D-F24-1). Config probada en este mismo sensor: `archive/dashcam-ia/firmware/main/app_camara.c:68-71` | D-F24-2; DriveGuard app_camara.c |
| WiFi | 2.4 GHz Wi-Fi 4 (802.11n) — obligatorio para CSI (S3 no ve 5 GHz) | ESPectre README "Supported hardware" |
| BLE | BLE 5.0 (opcional en el nodo, default n; gate coexistencia CSI) | dashcam-ia SPEC; D5 |
| USB-serial | CH340K (COMx autodetectado) **o** USB-C nativo USB-Serial-JTAG (hoy COM6) | dashcam-ia SPEC §1 (F18 flasheó por COM6 JTAG) |
| Sensor temp | **Temperatura interna del die S3** (proxy de carga de cómputo, NO ambiente — S11) | analista tendencias; products.espressif.com |
| Costo extra | **$0** (solo placa + router Wi-Fi existente; la cámara YA está onboard) | D1, E4 |

## 2. Pinout FIJADO (corregido por el analista de viabilidad, 2026-11-12)

> Corrección clave: el UART0 real es **43/44** (la SPEC vieja de DriveGuard decía "41–44 UART0", mal).
> **41/42 están LIBRES**. Fuente: `github.com/bbhpy/docs/blob/main/GOOUUU_ESP32-S3-CAM.md` (mapa 2×20
> de esta placa exacta) + `github.com/profharris/GOOUUU-Tech-ESP32-S3-CAM-Expansion-Board`.

| GPIO | Estado | Función | Uso en AulaSense |
|---:|---|---|---|
| 0 | Reservado | BOOT (strapping) | Recuperación ESPectre: mantener BOOT 3 s borra WiFi → Improv Serial (PRESERVADO en F24) |
| 1 | Casi libre | SDA QWIIC I2C (ADC1_CH0) | Libre si no se usa QWIIC (no se usa) |
| 2 | Casi libre | SCL QWIIC I2C (ADC1_CH1) | Libre si no se usa QWIIC (no se usa) |
| 3 | Ocupado | LED flash | No usado por AulaSense |
| 4–18 | Ocupado | Bus DVP cámara | **F24: usados por OV3660 SOLO bajo `AULASENSE_IMAGEN=y`** (captura evidencia). Guardas `#error` si se configura otro periférico en estos pins |
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
| **43/44** | Reservado | **UART0 real TX0/RX0** | **NUNCA usar para periféricos**. Consola real del vendor = USB-Serial-JTAG (COM6); UART0 callado (`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`, D-005) |
| 45/46 | Reservado | strapping | — |
| 47 | Libre | SPICLK_N SoC | Disponible |
| **48** | Ocupado | **WS2812 RGB NeoPixel onboard** | **Feedback visual: color por estado idle/motion (D5)** |

**Pool libre utilizable** (sin SD ni QWIIC): 1, 2, 21, 41, 42, 47 (+38/39/40). El feedback local
(buzzer 14 + WS2812 48) **no necesita ningún GPIO libre extra**. La cámara F24 usa el bus DVP 4–18
(ya ocupado por el periférico onboard). **Guardas de compilación** en `app_feedback.c`/`app_imagen.c`:
`#error` si se configura un pin prohibido (19/20/43/44/0/45/46/26–37) o si un periférico NO cámara
intenta usar 4–18. Fuente: analista viabilidad §1 + dashcam-ia `app_audio.c:142-146`.

## 3. Firmware: ESPectre Native v3.2.0 (fork académico GPLv3)

- **Vendor:** `git clone --branch 3.2.0 --depth 1 https://github.com/francescopace/espectre.git firmware/espectre` (**tag `3.2.0` SIN prefijo v** — `v3.2.0` da 404, verificado F1a). Gitignored, reproducible por tag. LICENSE GPLv3 preservado. El derivado (parche) se trackea en `firmware/parche_aulasense/` (S9: fuente publicado al profesor).
- **App Native:** `firmware/espectre/src/cpp/frontend/native/app` (proyecto ESP-IDF standalone).
- **ESP-IDF requerido: v5.5.6** (docs/CLI.md lo recomienda; SDK ≥5.5.3). Desviación documentada de E5 (v5.4 queda instalado para el archive). Instalación: `eim install 5.5.6`.
- **Detector (elige en runtime, conmutable por MQTT/Direct):**
  - **High Accuracy** (alternativa): MLP `8→24→12→1`, **529 parámetros**, estado 5.068 B, SIN calibración (threshold 0.5). Métricas vendor (espectre.dev/sdk/detectors, 20 grabaciones reales): recall 99,6 %, FPR 0,5 %, F1 99,3 %, 3,02 ms/detección, CPU 11,83 %.
  - **Lightweight** (default build verificado F1c, D-005): 2 features, combinación logística fija, estado 2.016 B, calibración ~10 s. Métricas vendor: recall 98,5 %, FPR 1,1 %, F1 98,1 %, 0,783 ms, CPU 9,09 %. F1c midió threshold auto 0.150, detection_avg_us 368.
  - **Honestidad (E10/S10):** las métricas vendor son de SU dataset; el piloto F1c mide recall/FPR LOCALES contra verdad terreno manual. Detecta **MOVIMIENTO**, no presencia estacionaria, no sala vacía, no conteo de personas.
  - **IA principal (D-F24-5):** este detector neuronal CSI **YA ES la "IA interna" del proyecto** (edge-first C1, 100 % en el chip). La capa de visión F24 (§3.4) es OPCIONAL y adicional, no la reemplaza.
- **CSI:** capture HT20 2.4 GHz, ~100 muestras/s, decisión cada 250 ms. Tráfico por defecto = ping al gateway; modos `ping|dns|dns_tcp|wifi_raw|external` (swaps si el aula bloquea ICMP, S7).
- **Consola:** USB-Serial-JTAG en S3 (COM6; `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`, UART0 43/44 callado — D-005). TinyUSB CDC solo S2 → **sin riesgo E5 de USB device stack**. `ESPECTRE_EMULATOR_CSI_REPLAY` activo por defecto en S3 pero solo arranca con marcador de MAC de emulador (inocuo en chip real).
- **Recuperación:** BOOT 3 s (GPIO0) borra WiFi → Improv Serial. **PRESERVADO en F24** (el provisioning AP §3.3 es aditivo, no reemplaza la recuperación nativa). Reflasheo USB full factory image = recovery de OTA.
- **Provisioning nativo (verificado en disco, tag 3.2.0):** Native = **Improv Serial** (USB) + BOOT 3 s + Direct HTTP WiFi reconfig (scan/BSSID/erase vía portal first-party espectre.dev). **NO trae AP/SoftAP ni captive portal**: `runtime/esp_idf/standalone_wifi_service.cpp:176` y `wifi_lifecycle.cpp:378` usan `WIFI_MODE_STA` único; el captive portal "ESPectre Fallback" es del frontend **ESPHome** (`frontend/esphome/README.md:14`), NO Native. → F24-d añade módulo propio `app_provision.c` (§3.3). Decisión D-F24-4.
- **Cámara (F24):** Native NO inicializa la cámara por defecto. F24-c la habilita bajo `AULASENSE_IMAGEN=y` con SPIRAM octal. Verificar sdkconfig pre-flash: camera driver presente SOLO si `AULASENSE_IMAGEN=y`, SPIRAM=y, sin USB device.

### 3.1 Parche `app_feedback.c` (feedback local, D5 — fase F3)
Módulo añadido al app Native (vía `firmware/parche_aulasense/`, instrucciones en su README):
- **Buzzer GPIO14 (LEDC PWM):** beep corto (880 Hz, ~110 ms) en cada onset `idle→motion`, cooldown 5 s. Tarea prioridad baja (no compite con el detector).
- **WS2812 GPIO48:** color por estado (azul tenue idle, brillante/verde motion). Supervisor único del pin.
- **Die temp:** `temperature_sensor` driver del S3 → publica cada 30 s en topic `{prefix}/{device_id}/aulasense_temp` `{"timestamp_ms":…,"die_temp_c":…}` sobre el MISMO cliente MQTT de Native (aditivo).
- **BLE NimBLE notify (OPCIONAL, Kconfig default n):** char estado estilo `0xA111` en onset. **Gate:** activar solo si `csi_accepted_pps` no cae vs F1c (R7).
- **Kconfig:** `AULASENSE_FEEDBACK` (bool, default y), `AULASENSE_BUZZER_GPIO` (14), `AULASENSE_WS2812_GPIO` (48), `AULASENSE_DIE_TEMP` (bool y), `AULASENSE_BLE` (bool n), `AULASENSE_BUZZER_COOLDOWN_S` (5). Guardas `#error` en pines prohibidos.

### 3.2 Parche `app_imagen.c` (evidencia de imagen local, D-F24-2/3 — fase F24-c)
Módulo NUEVO añadido al app Native (disjunto de app_feedback; comparte Kconfig.projbuild/CMakeLists/README):
- **Captura SOLO en ONSET `idle→motion`** (hook del callback de motion del runtime, NUNCA en el loop del detector CSI). QVGA 320×240 JPEG quality 12, `fb_count=2`, `CAMERA_FB_IN_PSRAM` (config probada en este mismo OV3660: `archive/dashcam-ia/firmware/main/app_camara.c:68-71`). Tamaño esperado ~4–8 KB (medir en F24-c). **SXGA descartado** (1600×1200 ~100–300 KB, sin valor analítico para persona a 224², D-F24-2).
- **Cooldown 15 s** entre capturas (`AULASENSE_IMAGEN_COOLDOWN_S`): no fotografiar cada motion. 2 onsets <15 s → 1 imagen.
- **Tarea dedicada prioridad baja** (< detector), **cola corta en PSRAM** (`AULASENSE_IMAGEN_QUEUE`=2 slots × 8 KB = 16 KB). **Si la cola se llena → se DESCARTA la imagen y el EVENTO SIGUE** (el evento es la fuente de verdad; la imagen es accesoria). Log de descarte + `aulasense_config` evento IMAGEN_DESCARTE.
- **Upload HTTP** `POST /internal/imagen` (§7.1) desde la tarea dedicada, con **backoff** (reintentos máx 3), Bearer token `AULASENSE_UPLOAD_TOKEN` (de `sdkconfig.defaults.local`, nunca impreso). **NUNCA desde el loop del detector.** Si el servidor no responde → descartar tras reintentos, el evento motion continúa por MQTT.
- **GATE DE MEMORIA (auto-deshabilitación honesta):** si heap interno libre < `AULASENSE_IMAGEN_HEAP_MIN_KB` (120) OR largest free block < `AULASENSE_IMAGEN_HEAP_BLOCK_MIN_KB` (64) → captura auto-deshabilitada + `ESP_LOGW` honesto + `aulasense_config` evento IMAGEN_GATE_OFF; **el sistema SIGUE detectando CSI**. Re-enable con histéresis al recuperar (umbral +20 KB). Baseline F1c: heap libre 206 KB / min 189.9 / largest 164 (`.tmp/f1c_diagnostics.json:4-6`).
- **Kconfig:** `AULASENSE_IMAGEN` (bool, **default y** — orden explícita del usuario), `AULASENSE_IMAGEN_COOLDOWN_S` (15), `AULASENSE_IMAGEN_QUEUE` (2), `AULASENSE_IMAGEN_HEAP_MIN_KB` (120), `AULASENSE_IMAGEN_HEAP_BLOCK_MIN_KB` (64), `AULASENSE_UPLOAD_HOST`/`PORT`/`TOKEN` (string, de .env vía sdkconfig.defaults.local). Guardas `#error`: pines cámara 4–18 exclusivos; SPIRAM requerida.
- **Privacidad (D-F24-1):** la imagen SOLO va al servidor local del proyecto (LAN→ingesta :8001 con token). **NUNCA al broker MQTT público ni a la nube.** Sin caras-ML. Consentimiento de aula documentado; retención configurable (§7.1 purga).

### 3.3 Parche `app_provision.c` (AP fallback + ruta privada, D-F24-4 — fase F24-d)
Módulo NUEVO, calco del patrón endurecido DriveGuard (`archive/dashcam-ia/firmware/main/app_portal.c`, lectura para inspiración, no copia ciega). **Nativo primero (verificado §3): Native NO trae AP → módulo propio aditivo.**
- **Disparo:** STA falla N=`AULASENSE_PROVISION_RETRIES` (3) reintentos, o sin credenciales NVS. Levanta **SoftAP** `AULASENSE-CFG-<4 hex MAC>` (2 últimos bytes del MAC SoftAP), canal 1 (2.4 GHz), `max_connection=4`, modo APSTA (mantiene intento STA).
- **Auth AP:** abierto CON aviso en página+log (patrón DriveGuard D6, `app_portal.c:118-120,387`) por default (`AULASENSE_PROVISION_AP_PASS=""`); si el Kconfig tiene pass → WPA2-PSK (nunca logueada). **Decisión D-F24-4:** abierto + ruta privada token = dos factores (proximidad física al AP + conocimiento del token); WPA2 opcional para quien lo quiera.
- **RUTA PRIVADA:** portal en `http://192.168.4.1/cfg/<token>` donde **token = 8 hex derivados de una clave efímera en NVS** (namespace `aulasense_prov`, key `cfg_token`, generada con `esp_fill_random` al provisionar). **Mostrada UNA vez en serial** al generarse (`AulaSense CFG URL: http://192.168.4.1/cfg/<token>`). Ruta incorrecta → **404 sin pista** (no adivinable). El token es una **capacidad de configuración local, NO una contraseña WiFi/MQTT** (no se rige por la regla de "nunca imprimir" de las credenciales de red; su impresión única es la UX diseñada, orden del orquestador). Nunca en logs recurrentes ni en MQTT/config_eventos.
- **Endpoints (todos bajo `/cfg/<token>`):**
  - `GET /cfg/<token>` → formulario HTML text-only (SSID/pass), aviso AP abierto si aplica, estado STA (texto/números, CERO `<img>`/binarios).
  - `POST /cfg/<token>/wifi` → guarda NVS + reaplica STA. **Endurecimientos (calco app_portal.c):** guard **Origin/Referer** (`host_es_propio`: prefijos `http://192.168.4.1`, `http://localhost`, `http://127.0.0.1` con fin/ruta/puerto; `http://192.168.4.1.evil.com` NO pasa; sin ambas cabeceras = cliente no-navegador permitido) → **403** cruzado (`app_portal.c:73-98,260`); cabeceras seguridad en TODAS las respuestas (`nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, CSP text-only `form-action 'self'`, `app_portal.c:104-112`); **escritura NVS SOLO si credenciales CAMBIAN** + **cooldown 5 s → 429** (`app_portal.c:255-292`, protege desgaste flash); **NUNCA loguea/renderiza SSID/pass** (solo longitudes, `app_portal.c:290-291`); body leído una vez, urldecode mínimo, longitudes absurdas rechazadas.
  - `GET /cfg/<token>/estado` → JSON estado (wifi_sta, ap_activa, heap/psram libre; SIN secretos).
- **NVS:** namespace `aulasense_wifi` (ssid/pass), distinto del `dg_wifi` heredado. Escritura solo con cambio real.
- **AP off:** 30 s tras STA OK (`AULASENSE_PROVISION_AP_OFF_S`, patrón `AP_OFF_MS` app_portal.c:42) → vuelve a STA puro (`esp_wifi_set_mode(WIFI_MODE_STA)`).
- **Recuperación nativa PRESERVADA:** BOOT 3 s → Improv Serial sigue funcionando (el módulo AP es aditivo; no toca el recovery vendor).
- **Eventos:** publica `aulasense_config` (§6.1) en AP_FALLBACK / CFG_GUARDADA / STA_RECONECTADO (detalle SIN secretos: solo longitudes/flags).
- **Kconfig:** `AULASENSE_PROVISION` (bool, default y), `AULASENSE_PROVISION_RETRIES` (3), `AULASENSE_PROVISION_AP_PASS` (string ""), `AULASENSE_PROVISION_AP_OFF_S` (30).
- **Criterio de prueba (F24-d):** flujo completo desconectando WiFi del router / credencial falsa → AP sube → config nueva vía ruta privada → reconecta como STA → AP off. 403/429 verificados. grep logs = CERO SSID/pass.

### 3.4 Parche `app_ia_imagen.c` (IA visión OPCIONAL, spike-gated, D-F24-5 — fase F24-e, SOLO si F24-e0 PASS)
- **La IA principal YA EXISTE** = detector CSI neuronal (§3, edge-first C1). Esta es una **capa de visión opcional adicional** sobre la imagen capturada, **default n** (`AULASENSE_IA_IMAGEN`).
- **Modelo: detección de PERSONA** (ESP-WHO `pedestrian_detect` / esp-dl `espdet_pico` escala humana, verificado en F24-a). **NO caras** (no reintroducir detección facial — E1), **NO COCO/YOLO completo** (inviable en S3 verificado F23: YOLO11n 320² 961 ms ~1 FPS / 8 MB flash; YOLO26n exige 16 MB PSRAM; `.tmp/f23_research_viabilidad.md:94-97,195-196`). espdet_pico 224²: 120 ms ~7.7 FPS (research:100,197).
- **SPIKE CON GATE DURO (F24-e0, time-boxed ≤200 steps):** antes de comprometer F24-e, un spike mide coexistencia esp-dl + ESPectre en el N16R8. **Gate (todos, medidos en placa):** (1) `csi_accepted_pps` no cae >10 % vs F1c (≥90 pps); (2) heap interno libre ≥120 KB AND largest block ≥64 KB durante captura+inferencia; (3) arena esp-dl cabe en PSRAM (`MALLOC_CAP_SPIRAM`, 8 MB libre); (4) latencia inferencia ≤2 s asíncrona; (5) `detection_avg_us` no se degrada >20 %. **Si CUALQUIERA falla → rama ABANDONADA, NO se hace F24-e**, se documenta la limitación en PROPUESTA/SPEC ("la capa de visión IA no cabe junto al detector CSI en este hardware — verificado en spike") y se entrega sin ella (orden explícita del usuario: "si no se puede no lo hagas").
- **Resultado:** `persona/no-persona + score` adjunto al evento JSON (campo opcional `ia_imagen`, §6.1) + metadata del upload + columnas `ia_persona`/`ia_score` en `imagenes` (§8.1) → **DATO ANALIZABLE en el consolidado** (§10.1), no solo galería.
- **Inferencia ASÍNCRONA** (tarea baja prioridad, no bloquea el detector). Gate de memoria §3.2 vigente: si heap cae, IA se deshabilita primero, luego captura.
- **Costo memoria/latencia:** documentado con las mediciones del spike (F24-e0). INCONCLUSO hasta medir: tamaño .espdl espdet_pico (research:215 no lo publica) → resolver en F24-a/e0.
- **Kconfig:** `AULASENSE_IA_IMAGEN` (bool, **default n** en cualquier caso, incluso si spike PASS).

## 4. Build, flasheo y provisioning — comandos exactos (PowerShell)

```powershell
# F1a · Toolchain IDF 5.5.6 + vendor (tag 3.2.0 SIN v)
eim install 5.5.6
eim run "idf.py --version" v5.5.6      # espera: ESP-IDF v5.5.6
git clone --branch 3.2.0 --depth 1 https://github.com/francescopace/espectre.git aulasense/firmware/espectre

# F1b · Build Native para esp32s3 (ruta primaria: idf.py directo vía eim)
cd aulasense/firmware/espectre/src/cpp/frontend/native/app
eim run "idf.py set-target esp32s3" v5.5.6
eim run "idf.py build" v5.5.6
# Fallback si el app requiere el CLI: espectre.cmd native build --chip s3 --backend local
# Verificar sdkconfig ANTES de flashear: SIN USB device stack, PSRAM octal 8MB, flash 16MB.
#   F24: camera driver presente SOLO si AULASENSE_IMAGEN=y; SPIRAM=y (el Native default NO la usa).
eim run "idf.py -p COM6 flash monitor" v5.5.6   # COM6 = USB-Serial-JTAG nativo (autodetectar; monitor sale Ctrl+])

# Provisioning WiFi (2.4 GHz): ruta primaria = Kconfig defaults generados desde .env (gitignored)
C:\Python315\python.exe tools\gen_firmware_config.py   # lee .env → sdkconfig.defaults.local (CONFIG_ESPECTRE_WIFI_* nativos), NUNCA imprime clave
#   F24: también genera AULASENSE_UPLOAD_TOKEN (secrets.token_hex(16)) → .env + sdkconfig.defaults.local
# Fallback nativo: Improv Serial por navegador (Chrome 151+, espectre.dev/tools/flash) o espectre.cmd provision
#   (clave por ESPECTRE_WIFI_PASSWORD env var, NUNCA argv). BOOT 3 s → Improv (recovery preservado).
# F24-d FALLBACK AP: si STA falla 3 reintentos → AP AULASENSE-CFG-xxxx → http://192.168.4.1/cfg/<token>
#   (token mostrado UNA vez en serial al generarse; ruta privada no adivinable).

# F1d · Config MQTT al broker (lee .env, PATCH /mqtt, nunca imprime clave; user nodo electiva)
C:\Python315\python.exe tools\config_mqtt.py    # scheme/host/port/user/pass/topic_prefix desde .env

# F2/F24 · Server PC (venv py3.15, sin Docker)
C:\Python315\python.exe -m venv server\.venv
server\.venv\Scripts\python.exe -m pip install -r server\requirements.txt
server\.venv\Scripts\python.exe server\ingesta\ingesta_mqtt.py   # incluye receptor imagen :8001 si AULASENSE_UPLOAD_HOST no vacío
server\.venv\Scripts\python.exe server\dashboard\app.py          # http://127.0.0.1:8000 (galería loopback)
server\.venv\Scripts\python.exe server\fuentes\openmeteo.py

# F24 · Purga de imágenes (retención configurable; NO rompe el dataset)
server\.venv\Scripts\python.exe tools\purga_imagenes.py --dias 30   # default .env AULASENSE_IMG_RETENCION_DIAS

# Demo / Análisis
powershell tools\start_demo.ps1     # ingesta(+receptor) + dashboard + openmeteo; PIDs en .tmp/demo_pids.json
powershell tools\stop_demo.ps1      # mata SOLO los procesos que arrancó el launcher
powershell analysis\run_analisis.ps1 --origen real --validacion docs\validacion_sesion1.csv
```

## 5. Direct HTTP (LAN, feedback en vivo E8)
- Puerto TCP **62587**, base `/espectre/v1`, protocolo 1.0. Recursos: `health`, `device`, `capabilities`, `sensing`, `wifi`, `diagnostics`, `events` (SSE), `mqtt`, `ota`, `csi` (collection opcional, NO usada).
- **Monitor** (espectre.dev/tools/monitor) y **Device settings** (espectre.dev/tools/device-settings) = feedback en vivo. El PC debe estar en la misma LAN.
- Las tools propias envían header `Origin: https://test.espectre.dev` (permitido por defecto) + `Content-Type: application/json`. Cuerpo ≤2048 B.
- `GET /espectre/v1/diagnostics?fields=[…]` (query URL-encoded). `["*"]` = todo.
- **Seguridad Direct HTTP:** LAN confiable; firmware acepta solo orígenes listados, exige preflight Private Network Access, limita cuerpo/colas/clientes/rate; escucha solo en interfaz station; nunca devuelve claves.
- **F24:** el provisioning AP (§3.3) es un httpd SEPARADO en 192.168.4.1 (interfaz AP), NO el Direct HTTP :62587. No se mezclan.

## 6. Contrato MQTT (fuente: docs/API.md, verificado 2026-11-12) — SIN CAMBIOS F24 (D-F24-6)

- **Broker:** `broker.gilman.pro:14769` (E6; scheme `mqtt` plano o `mqtts` TLS CA pública — ambos nativos; NO WebSocket, NO CA privada, S8). **Auth: nodo publica como usuario `electiva`** (config_mqtt PATCH /mqtt username desde `MQTT_USER`); **ingesta suscribe como `srv-electiva`** (`MQTT_INGESTA_USER`, fallback `MQTT_USER`; bitácora 2026-11-12 verificación srv-electiva rc=0). Claves en `.env` (gitignored, NUNCA impresas). Credenciales vigentes sin cambios.
- **Base topic:** `aulasense/v1/devices/{device_id}` (`topic_prefix=aulasense/v1/devices`; aísla del broker compartido, R6). `device_id` = 16 hex lowercase.
- **Topics (suffix sobre la base):**

| Suffix | Retained | Payload / propósito |
|---|---|---|
| `health` | sí | `{"status":"ok|offline","online":bool,"uptime_s":int,"timestamp_ms":int}` — Last Will offline |
| `device` | sí | `{"device_id","name","label","frontend":"native","firmware":"3.2.0","chip":"esp32s3","csi_profile":"ht20"}` |
| `capabilities` | sí | protocolo 1.0, resources/operations/events soportados |
| `sensing` | sí | `{"enabled","ready","calibrating","mode","detector","threshold","motion_on_hits","motion_off_hits","traffic_generator_mode","csi_target_pps",…}` |
| `wifi` | sí | estado radio redactado (sin SSID/BSSID/IP): `{"configured","connected","band","channel","rssi_dbm"}` |
| `motion` | **no** | `{"timestamp_ms":int,"state":"idle|motion","score":float}` por evaluación (cada 250 ms). **F24: campo OPCIONAL `ia_imagen` (§6.1)** |
| `fault` | no | `{"timestamp_ms":int,"message":str}` |
| `commands/result` | no | resultado correlacionado de un comando |
| `aulasense_temp` | no | parche D5: `{"timestamp_ms":int,"die_temp_c":float}` cada 30 s |
| `aulasense_config` | no | **parche F24 (§6.1):** eventos de provision/config/captura (sin secretos) |

- **Comandos:** publicar en `commands/request`: `{"command_id","command","fields"}`. Comandos: `update_device`, `update_sensing`, `recalibrate`, `read_diagnostics`, `check_ota`, `start_ota`.
- **Reloj (crítico, R3):** `timestamp_ms` es reloj MONÓTONO del dispositivo (ms desde boot), NO época. La ingesta mapea: `epoca_boot = now_pc_ms − timestamp_ms`; `evaluacion.ts_ms = epoca_boot + timestamp_ms`; `hora_sinc=1` si fresca; re-estimar al reboot; fallback `recibido_en`.
- **Métricas edge (C12, D4):** ingesta publica `read_diagnostics` cada 30 s → fila en `telemetria` (campos: detection_avg_us/max_us, runtime_load_percent, free_memory_kb, minimum_free_memory_kb, cpu_frequency_mhz, csi_accepted_pps, csi_callback_pps, generator_pps, traffic_tx_pps, traffic_rx_pps, wifi_rssi_dbm, wifi_channel, csi_hw_error_total, csi_occupancy, mqtt).
- **Imágenes NUNCA por MQTT (D-F24-1/6):** el binario de imagen al broker público violaría el re-enmarque de privacidad. La imagen va por HTTP POST al servidor local (§7.1). MQTT solo lleva el campo opcional `ia_imagen` (resultado, no la foto).

### 6.1 Extensiones F24 al contrato MQTT (aditivas, no rompen el nativo)
- **Campo opcional `ia_imagen` en `motion`** (solo si `AULASENSE_IA_IMAGEN=y` y la inferencia de la imagen del onset completó): `"ia_imagen":{"persona":bool,"score":float}`. Presente SOLO en la evaluación de onset con inferencia lista; ausente en el resto (la ingesta tolera ausencia → null). **No es la foto** (la foto va por HTTP §7.1); es el resultado analizable persona/no-persona.
- **Topic nuevo `aulasense_config`** (no retained): `{"timestamp_ms":int,"evento":"AP_FALLBACK|CFG_GUARDADA|STA_RECONECTADO|IMAGEN_GATE_OFF|IMAGEN_DESCARTE","detalle":str≤120}`. **detalle SIN secretos** (solo longitudes/flags, p.ej. "ssid_len=12 pass_len=0", NUNCA el SSID/pass). La ingesta lo valida → tabla `config_eventos` (§8.1). Cubre "eventos de provision/config" del big data (D-F24-7).
- **Validador (`esquema.py`):** tolera `ia_imagen` opcional en motion (tipo errado → 422/log); valida `aulasense_config` (evento en enum, detalle ≤120, sin campos de secreto). Retrocompatible con F2 (los topics nativos no cambian).

## 7. Ingesta (server/ingesta/ingesta_mqtt.py)
- Suscribe `aulasense/v1/devices/+/motion`, `+/health`, `+/device`, `+/sensing`, `+/wifi`, `+/fault`, `+/commands/result`, `+/aulasense_temp`, **`+/aulasense_config` (F24)**.
- **User ingesta `srv-electiva`** (`MQTT_INGESTA_USER`, fallback `MQTT_USER`; D-F24-6). `paho-mqtt` callback → cola → **writer único** a SQLite (WAL). Validador por topic (`esquema.py`): tipo errado → 422/log, no escribe. `origen` default `real`; `sintetico` SOLO vía `POST /internal/evento` (C13).
- **Derivación de bordes:** `idle→motion` = `MOVIMIENTO_INICIO`, `motion→idle` = `MOVIMIENTO_FIN` (con `duracion_ms`). `health offline` = `NODO_OFFLINE`, `online` = `NODO_ONLINE`. `fault` = `FALLA`. **F24: el evento `MOVIMIENTO_INICIO` es el ancla del FK `imagenes.evento_id`** (la imagen de evidencia se asocia al onset que la disparó).
- **Diagnostics poller:** cada 30 s `read_diagnostics` → `commands/result` → `telemetria`.
- **Export CSV acumulativo:** `server/datos/export_evaluaciones.csv` (todas las filas) + **`analysis/dataset/consolidado.csv` (SOLO real, esquema extendido §10.1, generado por `analysis/export_consolida.py` — F24-b1)**.
- **F24: receptor de imagen** como hilo HTTP dentro del proceso de ingesta (§7.1), preserva el writer único (encola en la misma cola).

### 7.1 Receptor de imagen HTTP (server/ingesta/receptor_imagen.py, D-F24-3 — fase F24-b1)
- **Por qué en ingesta y NO en dashboard:** el dashboard bind **127.0.0.1** es regla dura (E7) y el nodo NO puede alcanzar loopback desde la LAN. Solución: un listener HTTP mínimo **dentro del proceso de ingesta** (hilo `ThreadingHTTPServer`), que encola los jobs validados en la **cola existente del writer único** → preserva la invariante de writer único. El dashboard sigue siendo solo loopback y sirve la galería leyendo de disco/BD. **Esto MEJORA el diseño "endpoint en dashboard" del orquestador** (que rompería la regla loopback o sería inalcanzable desde el nodo).
- **Endpoint:** `POST /internal/imagen` (multipart con campo `imagen` + metadata JSON, o body binario JPEG + cabeceras de metadata). **Bind:** `AULASENSE_UPLOAD_HOST`:8001 (`AULASENSE_UPLOAD_PORT`). **Default vacío = listener DESACTIVADO** (seguro); el operador pone la IP LAN del PC para habilitar. **NUNCA 0.0.0.0 por default** (opt-in explícito, filosofía `AULASENSE_ORIGINS_EXTRA`).
- **Auth:** Bearer token `AULASENSE_UPLOAD_TOKEN` (generado `secrets.token_hex(16)` por `tools/gen_firmware_config.py` → `.env` + `sdkconfig.defaults.local`, nunca impreso). Sin token / token malo → **401**. (El nodo no es navegador: no se exige Origin; el token ES el control de acceso.)
- **Validaciones:** JPEG magic `FFD8FF` (422 si no), tamaño **≤64 KB** (413 si excede; QVGA ~4–8 KB sobra), `device_id` 16 hex en allowlist (tabla `dispositivos`; 422 si desconocido), **rate-limit 1 imagen/10 s por device** (429). Metadata: `device_id`, `ts_ms` (monótono→época vía R3), `evento_id` (FK nullable al onset), `ia_persona`/`ia_score` (opcionales), `origen`.
- **Almacenamiento:** archivo `server/datos/imagenes/<device>/<yyyymmdd_HHMMSS>_<sha256_8>.jpg` (sha256 del contenido, 8 primeros hex) + fila `imagenes` (§8.1). **datos/imagenes/ GITIGNORED** (privacidad: las imágenes no se commitean).
- **Galería (loopback, §9.1):** el dashboard sirve las fotos por id de BD; el receptor NO sirve galería (solo acepta uploads).
- **Retención / purga (`tools/purga_imagenes.py`, D-F24-1/7):** `--dias N` (default `.env` `AULASENSE_IMG_RETENCION_DIAS`=30). Borra el archivo físico, marca `imagen_purgada=1`, **CONSERVA la fila + ia_persona/ia_score + evento_id** → **el dataset NO se rompe** (el consolidado sigue teniendo tiene_imagen/ia aunque la foto se purgue). Purga idempotente, log de cuántas purgó. Ejecutable por el operador o programable; NO automático en la demo (auditable).
- **Sin thumbnail server-side (D-F24-3):** QVGA 320×240 ya es tamaño thumbnail; el navegador escala. Evita el riesgo de wheel Pillow cp315 (no verificado para py3.15). Honesto y simple.
- **Tests (F24-b1):** 201 válido, 401 sin token, 413 >64 KB, 422 JPEG corrupto/device desconocido, 429 rate-limit, FK evento_id, purga con imagen_purgada sin borrar fila, writer único preservado.

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

### 8.1 Tablas nuevas F24 (migración idempotente, solo la ingesta las crea)

```sql
-- Evidencia de imagen local (D-F24-2/3/7). La foto es accesoria; el evento es la fuente de verdad.
CREATE TABLE IF NOT EXISTS imagenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,                 -- época (mapeo R3 del monótono del nodo)
  ruta TEXT NOT NULL,                     -- relativa bajo server/datos/imagenes/<device>/<archivo>.jpg
  hash_sha256 TEXT NOT NULL,              -- sha256 completo del contenido
  tamano_bytes INTEGER NOT NULL,
  ancho INTEGER, alto INTEGER,            -- 320x240 QVGA esperado
  evento_id INTEGER,                      -- FK nullable → eventos.id (el onset MOVIMIENTO_INICIO que la disparó)
  ia_persona INTEGER,                     -- F24-e opcional: 1/0/null (null si IA off o no inferida)
  ia_score REAL,                          -- F24-e opcional: score persona/null
  imagen_purgada INTEGER NOT NULL DEFAULT 0,  -- 1 si la purga borró el archivo (fila+ia CONSERVADAS → dataset intacto)
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(evento_id) REFERENCES eventos(id)
);
CREATE INDEX IF NOT EXISTS idx_img_dev_ts ON imagenes(device_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_img_evento ON imagenes(evento_id);
CREATE INDEX IF NOT EXISTS idx_img_purgada ON imagenes(imagen_purgada);

-- Eventos de provision/config/captura (D-F24-7, sin secretos). Topic aulasense_config (§6.1).
CREATE TABLE IF NOT EXISTS config_eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL, ts_ms INTEGER NOT NULL,
  tipo TEXT NOT NULL CHECK(tipo IN ('AP_FALLBACK','CFG_GUARDADA','STA_RECONECTADO','IMAGEN_GATE_OFF','IMAGEN_DESCARTE')),
  detalle TEXT,                           -- ≤120 chars, SIN secretos (solo longitudes/flags)
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cfg_dev_ts ON config_eventos(device_id, ts_ms);
```
SQL siempre parametrizado; límites en `limite`/`device_id`; writer único (la ingesta, incluido el receptor imagen §7.1 que encola). Migraciones idempotentes (`ALTER TABLE`/`CREATE TABLE IF NOT EXISTS` solo lo corre la ingesta). **La purga (§7.1) hace UPDATE imagen_purgada=1, NUNCA DELETE de la fila** → integridad referencial y dataset preservados (gate bd).

## 9. Dashboard (server/dashboard/app.py, FastAPI, puerto 8000, bind 127.0.0.1)
- `GET /` → `static/index.html` (Chart.js + Tailwind CDN, una página, pestañas Monitoreo / Reportes / Validación / **Galería (F24)**).
- `GET /api/dispositivos`, `GET /api/evaluaciones?device_id=&desde=&hasta=&limite=` (1–5000), `GET /api/eventos?device_id=&tipo=&limite=`, `GET /api/resumen` (KPIs), `GET /api/telemetria?device_id=&limite=`, `GET /api/validacion?sesion=` (matriz de confusión), `GET /api/fuentes_externas?fuente=&limite=`, `GET /api/export?tabla=&origen=` (CSV, real por default).
- `POST /internal/evento` → inyección sintética/test (Origin guard, reusa validador, 201/422). Writer de producción sigue siendo la ingesta.
- `WS /ws` → push live de evaluaciones/eventos (Origin guard; latencia <2 s).
- **Monitoreo:** tarjeta por device (estado idle/motion, score gauge, métricas edge, feed eventos, badge en_vivo/stale, flash+beep WebAudio en onset).
- **Reportes:** serie temporal score+métricas, motion events/hora, **heatmap por hora** (ya previsto v23; F24-f2 produce la figura `assets/heatmap_actividad_hora.png` desde el consolidado), tabla paginada, export CSV.
- **Validación:** matriz de confusión por sesión (recall/FPR/latencia vs verdad terreno), nota de tamaño muestral.
- **Seguridad (D10, heredada E7):** bind 127.0.0.1 (nunca 0.0.0.0); Origin guard en `/ws` y `/internal/*` (403 cruzado; `AULASENSE_ORIGINS_EXTRA` host[:puerto] exactos sin wildcards); `esc()`/`textContent` XSS; SQL parametrizado.
- **F24 — galería de imágenes (§9.1):** la regla v23 "cero endpoints de imágenes" queda **REEMPLAZADA** por "galería de evidencia SOLO loopback, controlada" (D-F24-1/3). El dashboard NUNCA acepta uploads (eso es el receptor :8001 §7.1); solo SIRVE fotos por id de BD en loopback.

### 9.1 API de galería F24 (loopback, D-F24-3 — fase F24-b2)
- `GET /api/imagenes?device_id=&desde=&hasta=&limite=` → metadata paginada (id, device_id, ts_ms, tamano_bytes, ancho/alto, evento_id, ia_persona, ia_score, imagen_purgada, origen). **NUNCA expone la ruta absoluta del archivo.**
- `GET /api/imagenes/{id}/foto` → sirve el JPEG por **id de BD** (entero validado; la ruta se lee de la fila y se verifica que esté bajo `server/datos/imagenes/`; NUNCA path del usuario). `Content-Type: image/jpeg`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`. **404 si `imagen_purgada=1`** (la galería muestra placeholder "purgada"). 404 si id inexistente.
- **Galería UI:** pestaña/sección Galería con `<img src="/api/imagenes/{id}/foto">` escalado por navegador (QVGA ya es thumbnail, sin server-side resize), badge "purgada" si aplica, filtro device/fecha, `esc()`/textContent para metadata, lazy loading. **Sin innerHTML con datos.**
- **Tests (F24-b2):** API metadata 200, foto 200 con nosniff, foto 404 purgada, id inválido 404/422, path traversal rechazado, Origin guard heredado intacto, XSS escapado.

## 10. Multi-fuente (C6) y análisis (C5) + BIG DATA ACADÉMICO (D-F24-7)
- **Fuente 1:** eventos/evaluaciones CSI (nodo). **Fuente 2:** die temp S3 (parche D5, proxy carga, S11). **Fuente 3:** Open-Meteo (gratis sin key, coords `.env`, degrada si vacías P1). **Fuente 4 (F24):** imágenes de evidencia + ia_imagen (persona/score) como DATO ANALIZABLE. **Fuente 5 (F24):** config_eventos (provision/config/captura, sin secretos).
- **EDA (`analysis/eda.py`):** motion events/día y hora, distribución score, patrones por sesión, calidad CSI, métricas edge, correlación die temp↔runtime_load, cruce Open-Meteo, **F24: distribución ia_persona, tasa de imágenes por onset, eventos de provision**.
- **Modelo (`analysis/modelo_ocupacion.py`):** features hora/día/actividad móvil + die temp + Open-Meteo **+ F24 opcionales (ia_persona, tiene_imagen, hora_provision) sin romperse si null** → target `motion` ventana siguiente 10 min. LogisticRegression + DecisionTreeClassifier (D8), split temporal 80/20, métricas CON tamaño muestral (E10). SOLO `origen=real`.
- **Postura "big data académica" (D-F24-7):** el curso (Big Data e IA) pide un **pipeline real de ingesta→almacenamiento→análisis con datos propios**, no petabytes. AulaSense lo cumple: series temporales CSI (~4 evaluaciones/s) + catálogo de imágenes + métricas edge + clima + config, todo en SQLite WAL + consolidado CSV analyzable con pandas/sklearn. Volumen honesto: ~14.4k evaluaciones (3 sesiones × 20 min) + cientos de imágenes (1 por onset, cooldown 15 s). **No se promete escala masiva; se promete el pipeline completo y datos reales propios.** La retención/purga de imágenes NO rompe el dataset (flag imagen_purgada, fila+ia intactas).

### 10.1 Esquema del consolidado (analysis/dataset/consolidado.csv, SOLO origen=real — F24-b1/f2)
**Una fila por EVENTO** (bordes MOVIMIENTO_INICIO/FIN, NODO_*, FALLA, CALIBRACION) con joins por ventana temporal a todas las fuentes. Generado por `analysis/export_consolida.py` (lee SQLite, SOLO real). Columnas:

| Columna | Fuente / join | Nota |
|---|---|---|
| `evento_id` | eventos.id | clave |
| `device_id`, `ts_ms`, `tipo`, `score`, `duracion_ms` | eventos | ts_ms época (R3) |
| `hora`, `dia_semana`, `franja_horaria` | derivados de ts_ms | features del modelo |
| `detection_avg_us`, `runtime_load_percent`, `free_memory_kb`, `csi_accepted_pps`, `csi_occupancy`, `wifi_rssi_dbm`, `die_temp_c` | telemetría más cercana ±30 s | métricas edge (C12) |
| `tiene_imagen` | imagenes por evento_id FK (1/0) | **F24** |
| `ia_persona`, `ia_score` | imagenes.ia_persona/ia_score (null si IA off) | **F24, dato analizable** |
| `imagen_purgada` | imagenes.imagen_purgada (0/1) | **F24: purga no rompe fila** |
| `temperatura_c`, `humedad_pct`, `precipitacion_mm`, `viento_kmh` | fuentes_externas openmeteo ±1 h | clima (C6) |
| `n_provision`, `hora_provision` | config_eventos en ventana (count, flag 1 si hubo CFG_GUARDADA/AP_FALLBACK) | **F24, sin secretos** |
| `verdad_terreno` | validacion_sesiones por ventana (motion/id/null) | C10 |
| `sesion_id`, `origen` | eventos | SOLO real en consolidado |

**Integridad:** los joins son por ventana temporal (no inner join estricto → un evento sin telemetría/clima/imagen igual sale con null/0). `smoke_consolida.py` (F24-f2) valida que el CSV carga en pandas, el esquema coincide, y los joins no duplican filas. **CERO sintético mezclado** (filtro origen=real). El export es reproducible (rerun regenera desde SQLite).

## 11. Guion de demo (C8/E3, detalle en docs/guion_demo.md)
1. `start_demo.ps1` → ingesta(+receptor imagen) + dashboard + openmeteo (PIDs en `.tmp/demo_pids.json`).
2. Nodo flasheado y provisionado; abrir Monitor + dashboard `http://127.0.0.1:8000`.
3. Zona quieta → `idle`, WS2812 tenue.
4. Operador camina → `motion` en <2 s, buzzer beep, WS2812 brillante, tarjeta flash+beep. **F24: imagen de evidencia capturada al onset → galería <5 s (cooldown 15 s).**
5. Operador quieto → `idle`.
6. Repetir por zonas. Mostrar métricas edge en vivo. **F24: mostrar galería + (si IA on) badge persona/no-persona.**
7. `stop_demo.ps1` limpia PIDs.
- **Sin humanos frágiles:** estímulo trivial (caminar/mover objeto). **Honesto:** `idle` ≠ sala vacía (S10). **F24: consentimiento de aula documentado (las imágenes son evidencia local, no vigilancia).**

## 12. WiFi y secretos (E6/D10) + variables F24
- `.env` (copiado de `.env.example`, GITIGNORED): `MQTT_HOST`, `MQTT_PORT`, `MQTT_SCHEME`, `MQTT_USER` (electiva, nodo), **`MQTT_INGESTA_USER` (srv-electiva, F24)**, `MQTT_PASS` (vacío en example), `MQTT_TOPIC_PREFIX`, `WIFI_SSID`, `WIFI_PASS`, `ESPECTRE_WIFI_PASSWORD`, `DEVICE_LABEL`, `OPENMETEO_LAT/LON`, `AULASENSE_ORIGINS_EXTRA`, `DASHSCOPE_API_KEY` (opcional).
- **Variables F24 (nombres en .env.example, valores vacíos/placeholder; NUNCA claves reales):**
  - `AULASENSE_UPLOAD_HOST` (IP LAN del PC para el receptor :8001; **vacío = desactivado**; nunca 0.0.0.0 default).
  - `AULASENSE_UPLOAD_PORT` (default 8001).
  - `AULASENSE_UPLOAD_TOKEN` (Bearer token del receptor; **generado `secrets.token_hex(16)` por gen_firmware_config.py**, nunca impreso ni commiteado).
  - `AULASENSE_IMG_RETENCION_DIAS` (default 30; purga §7.1).
- WiFi del nodo: Kconfig defaults por `tools/gen_firmware_config.py` desde `.env` → `sdkconfig.defaults.local` (gitignored, `CONFIG_ESPECTRE_WIFI_*` nativos), o NVS vía Improv Serial / AP privado F24. **La clave real NUNCA en docs/logs/argv/evidencias.**
- MQTT: `tools/config_mqtt.py` lee `.env`, `PATCH /mqtt` (user nodo electiva); nunca imprime clave. Clave nueva → `secrets.token_hex(16)` en código.
- **Token de ruta privada `/cfg/<token>` (§3.3):** capacidad local generada con `esp_fill_random` en el chip, mostrada UNA vez en serial. NO es contraseña WiFi/MQTT; nunca en logs recurrentes ni MQTT.
- Red 2.4 GHz obligatoria. Plan B demo: hotspot móvil.

## 13. Limitaciones honestas (declaradas, no adornadas)
- ESPectre detecta **MOVIMIENTO**, no presencia estacionaria, no prueba sala vacía, no identifica ni cuenta personas (S10).
- Una placa cubre una zona; colocación 3–8 m del AP, fuera de metal; sensible al entorno RF (R1).
- Métricas vendor (F1 98,1–99,3 %) son de SU dataset; las locales se miden en F1c/F4 (E10).
- Die temp ≠ temperatura ambiente (S11).
- Reloj del nodo monótono; la época la estima el PC (R3); sin SNTP en el nodo.
- BLE opcional puede degradar CSI (R7); default n.
- GPLv3: el derivado debe publicar fuente (S9); uso académico OK.
- MQTT: sin WebSocket ni CA privada en Native (S8).
- **F24 — privacidad PARCIAL (D-F24-1, C9 cumple-parcial):** el sistema YA NO es "cero imágenes". Captura evidencia QVGA al onset de motion. **Acotada:** imágenes SOLO al servidor local del proyecto (LAN→ingesta con token, galería loopback), NUNCA a la nube ni al broker MQTT público; **consentimiento de aula documentado obligatorio**; **retención/borrado configurables** (default 30 días, purga con imagen_purgada que no rompe el dataset); **sin caras-ML** (no detección facial). Las imágenes pueden capturar personas → son datos personales: minimización (QVGA, solo onset, cooldown), acceso local autenticado, gitignored, purgables. **No es privacidad por construcción; es privacidad acotada por mitigación.**
- **F24 — IA visión puede NO caber (D-F24-5):** la capa de visión persona (esp-dl) es opcional default n y está sujeta a spike F24-e0. **Si el spike prueba que no coexiste con el detector CSI (pps/heap), se ABANDONA y se documenta** ("la capa de visión IA no cabe junto al detector CSI en este hardware — verificado"). La IA principal (detector CSI) YA cumple edge-first sin ella.
- **F24 — imagen es accesoria, no fuente de verdad:** si la cola se llena o el heap cae, la imagen se descarta/auto-deshabilita y el EVENTO CSI sigue. La detección de movimiento no depende de la cámara.
- **F24 — AP de provisioning abierto por default:** el AP fallback es abierto CON aviso (patrón DriveGuard); la ruta privada token es el control de acceso. Riesgo: sniffing del POST en AP abierto → mitigado por token de ruta + Origin guard + cooldown + WPA2 opcional. Configurar en lugar privado.
- **F24 — big data académica, no masiva:** pipeline real con datos propios (~14.4k evaluaciones + cientos de imágenes), no petabytes (§10, D-F24-7).

## 14. Decisiones F24 (D-F24-1..8, resumen — detalle con evidencia en PLAN.md §11)
- **D-F24-1 — Re-enmarque de privacidad (revoca D9/regla 2 v23 por orden del usuario).** "Privacidad acotada local-only": imágenes solo al servidor local, nunca nube/broker público; consentimiento; retención configurable; sin caras-ML. C9 cumple-parcial. Evidencia: orden explícita del dueño 2026-11-12 (bitácora F24).
- **D-F24-2 — Imagen = evidencia de evento, no streaming.** QVGA JPEG onset + cooldown 15 s + tarea baja prioridad + cola PSRAM corta (descarte no bloquea evento) + gate de memoria auto-deshabilita. SXGA descartado. Evidencia: DriveGuard app_camara.c:68-71 (QVGA probado en este sensor); esp-dl input 224² (research:100).
- **D-F24-3 — Upload por receptor HTTP en INGESTA (no dashboard), galería loopback.** Dashboard 127.0.0.1 es regla dura y el nodo no alcanza loopback → listener :8001 con Bearer token dentro de ingesta (preserva writer único). Sin thumbnail server-side (QVGA ya es pequeño; evita riesgo Pillow cp315). MEJORA el diseño "endpoint en dashboard" del orquestador. Evidencia: AGENTS regla seguridad (bind 127.0.0.1); f2_backend.md (writer único).
- **D-F24-4 — Provisioning: nativo primero (verificado NO trae AP) → módulo propio endurecido + ruta privada.** Native = Improv Serial + BOOT 3 s, STA-only (standalone_wifi_service.cpp:176, wifi_lifecycle.cpp:378; captive portal solo ESPHome README:14). app_provision.c calco DriveGuard app_portal.c (Origin/Referer 403, cooldown 429, NVS solo cambio real, nunca log SSID/pass) + AP AULASENSE-CFG-xxxx + /cfg/<token NVS 8hex, mostrado una vez en serial>. BOOT 3 s preservado.
- **D-F24-5 — IA interna = detector CSI (YA existe); visión persona OPCIONAL spike-gated default n.** F24-e0 time-boxed con gate duro (pps ≤10 % caída, heap ≥120 KB/64 KB bloque, arena PSRAM, ≤2 s asíncrono); si FAIL → ABANDONADA documentada, no se hace. Sin caras, sin COCO/YOLO (inviable F23). Evidencia: research viabilidad:94-109,195-198; orden usuario "si no se puede no lo hagas".
- **D-F24-6 — MQTT sin cambios.** electiva (nodo) / srv-electiva (ingesta), .env vigente, nunca impreso. Aditivos: topic aulasense_config + campo opcional ia_imagen. Imágenes NUNCA por MQTT. Evidencia: bitácora verificación srv-electiva rc=0; .env.example.
- **D-F24-7 — Recopilación big data académica.** TODAS las fuentes (evaluaciones/eventos, telemetría, imágenes+ia, openmeteo, config/provision, validación) → SQLite + consolidado CSV §10.1 (una fila por evento, joins por ventana). Pipeline real ingesta→almacén→análisis con datos propios; no petabytes. Purga no rompe dataset (imagen_purgada). Evidencia: orden usuario "recopilar datos en backend con big data"; condiciones curso C3/C5.
- **D-F24-8 — Creatividad barata sin riesgo: heatmap de actividad por hora.** Ya previsto en Reportes v23; F24-f2 produce `assets/heatmap_actividad_hora.png` desde el consolidado (datos CSI+horas lo permiten). No infla fases. Galería de evidencia como entregable visual nuevo.
