# Fuentes — AulaSense (URL + fecha + claim que sostiene)

> Consolidado por el planificador (2026-11-12). Todas las URLs fueron consultadas el **2026-11-12**
> por vías gratuitas (`webfetch`/`websearch` del arnés). **0 llamadas pagadas usadas** (presupuesto
> qwen-web-search intacto). Verificador: **P** = planificador (webfetch directo a fuente primaria),
> **A** = analista (`3-analista`, informes verbatim en `iot2/.tmp/f23_research_tendencias.md` y
> `iot2/.tmp/f23_research_viabilidad.md` — esos archivos están gitignored; este documento consolida
> lo citable para que viaje con el repo).
>
> Regla de citación del proyecto: toda cifra de hardware/contrato/rendimiento en PLAN/SPEC/docs
> debe poder rastrearse a una fila de esta tabla o a evidencia local de ejecución (`.tmp/f*_*.md`).

## 1. ESPectre — fuente primaria del firmware (verificación directa del planificador)

| URL | Claim que sostiene | Verificador |
|---|---|---|
| https://raw.githubusercontent.com/francescopace/espectre/main/README.md | GPLv3 (+ licencia comercial closed-source, irrelevante en curso); ESP32-S3 soportado; Wi-Fi 4 (802.11n) 2.4 GHz; firmware Native standalone con MQTT + HA Discovery + Direct HTTP + HTTPS OTA; dataset CSI abierto (`data/dataset_info.json`) y pesos abiertos (`src/cpp/core/ml_weights.h`, `tools/lib/ml_weights.py`); CLI `espectre.cmd` en Windows; flash por navegador (Chrome 151+); "one board per room"; **"does not identify people, count them, prove that a room is empty"** (detecta MOVIMIENTO, no presencia estacionaria) | P |
| https://api.github.com/repos/francescopace/espectre/contents/ | Estructura real del repo: `src/cpp/frontend/native/`, `docs/` (SETUP/API/CLI/DISCOVERY/SDK), `data/`, `tools/` — rutas citadas en SPEC verificadas contra el árbol | P |
| https://raw.githubusercontent.com/francescopace/espectre/main/src/cpp/frontend/native/README.md | MQTT esquemas `mqtt` (TCP plano) y `mqtts` (TLS, CA pública + verificación de hostname); host/puerto/credenciales/`topic_prefix` configurables (Device settings o `PATCH /mqtt`); **NO soporta MQTT sobre WebSocket ni CA privada**; HA Discovery (Motion Detected, Movement Score, Threshold, Detection Profile, CSI Traffic Source, Recalibrate); tráfico CSI por defecto = ping al gateway, modos `ping\|dns\|dns_tcp\|wifi_raw\|external`; recuperación BOOT 3 s (GPIO0 en S3); Improv Serial por el puerto serie; el README del vendor afirma que en S3 la consola principal sale por UART (TinyUSB CDC solo S2) — **CORREGIDO (2026-11-17): en el build de este proyecto la consola es USB-Serial-JTAG, no UART** (`CONFIG_ESP_CONSOLE_UART_NUM=-1`, `sdkconfig:1460-1465`; evidencia `.tmp/inv_ota_provision.md` §2.5 y captura COM6 = `USB\VID_303A&PID_1001` en `.tmp/inv_com6_resultado.md`); lo que sigue PROHIBIDO es el **USB device stack** (CONFIG_USB_*/tinyusb), no el USB-Serial-JTAG nativo → sin riesgo E5; `ESPECTRE_EMULATOR_CSI_REPLAY` inocuo en chip real (marcador MAC de emulador); CPU 240 MHz en S3; imágenes oficiales solo OTA firmado, builds propios por USB | P |
| https://raw.githubusercontent.com/francescopace/espectre/main/docs/SETUP.md | Chips soportados por frontend (Native: ESP32/S2/S3/C3/C5/C6); colocación **3–8 m del AP**, fuera de cajas metálicas; la red debe permitir el tráfico del generador (si ICMP bloqueado → dns/dns_tcp/wifi_raw/external); procedimiento de primera detección (caminar→motion, quieto→idle, repetir por zona); Lightweight calibra al arranque (sala quieta hasta ready); High Accuracy salta calibración pero necesita segundos de CSI válido | P |
| https://raw.githubusercontent.com/francescopace/espectre/main/docs/API.md | Protocolo **1.0**; Direct HTTP TCP **62587**, base `/espectre/v1`; MQTT base `espectre/v1/devices/{device_id}` (`topic_prefix` configurable); `device_id` = 16 hex; topics retained `health\|device\|capabilities\|sensing\|wifi\|ota`, no-retained `motion\|fault\|commands/result`; comandos por `commands/request` (`command_id` 1–64 ASCII); **evento motion por evaluación: `{"timestamp_ms":int,"state":"idle\|motion","score":float}` con reloj MONÓTONO** (ms desde boot, no época); `read_diagnostics` devuelve nativamente `detection_avg_us/max_us`, `runtime_load_percent`, `free_memory_kb`, `minimum_free_memory_kb`, `cpu_frequency_mhz`, `csi_accepted_pps`, `csi_callback_pps`, `generator_pps`, `traffic_tx/rx_pps`, `wifi_rssi_dbm`, `wifi_channel`, contadores de calidad CSI, grupo `mqtt` (→ cubre C12 sin parche); comandos `update_device`, `update_sensing`, `recalibrate`, `read_diagnostics`, `check_ota`, `start_ota`; colección CSI binaria `GET /csi` opcional de investigación que **pausa los eventos derivados** (NO se usa en ruta crítica); HTTP y MQTT comparten payloads/validación/códigos | P |
| https://raw.githubusercontent.com/francescopace/espectre/main/docs/CLI.md | Prerequisitos de build local: el CLI busca IDF en `IDF_PATH` activo primero (compatible con `eim run`); **versión recomendada explícita: ESP-IDF 5.5.6** (refina el ≥5.5.3 de releases → el plan pinea 5.5.6, desviación documentada de E5 v5.4); entorno Python del CLI recomendado 3.14 (la ruta `idf.py` directa NO necesita ese venv); Docker solo fallback opcional (NO se usa, E5); `espectre provision` pasa la clave WiFi por env var **`ESPECTRE_WIFI_PASSWORD`, nunca por argv ni impresa**; `espectre doctor` verifica el entorno; `espectre collect` graba datasets CSI crudos (extensión opcional); `espectre mqtt` shell interactivo | P |

## 2. ESPectre — sitio y releases (analista de tendencias)

| URL | Claim que sostiene | Verificador |
|---|---|---|
| https://github.com/francescopace/espectre/releases | v3.0.0 (2026-10-07), v3.1.0 (2026-11-01), **v3.2.0 (2026-11-11**, assets 2026-11-11); SDK requiere ESP-IDF ≥5.5.3; ESP32-S3 soportado; librería Arduino v3.2.0; Matter occupancy; dataset+pesos abiertos | A |
| https://espectre.dev ; https://espectre.dev/sdk ; https://espectre.dev/sdk/detectors | Sensing sin cámara/mic; dos perfiles conmutables en runtime: **Lightweight** (2 features, logística fija, calibración ~10 s hasta ~30 s con burst, estado 2.016 B) y **High Accuracy** (MLP 8→24→12→1, **529 parámetros**, sin calibración, threshold 0.5, estado 5.068 B); métricas medidas en 20 grabaciones reales por perfil: recall 98,5/99,6 %, FPR 1,1/0,5 %, **F1 98,1/99,3 %**, detección **0,783/3,02 ms**, CPU **9,09/11,83 %**; licencia GPLv3 | A |
| https://esphome.io/components/espectre | Chips soportados (S3); requisitos de framework; limitación "no establece presencia de persona estacionaria" | A |
| https://components.espressif.com/components/francescopace/espectre | SDK publicado en el ESP Component Registry (ruta alternativa de integración) | A |

## 3. Espressif oficial — respaldo, límites de visión y audio

| URL | Claim que sostiene | Verificador |
|---|---|---|
| https://github.com/espressif/esp-csi | **Respaldo oficial Espressif a ESPectre** ("Community project recommendation: ESPectre"); CSI disponible en toda la serie ESP32; 1.752 estrellas | A |
| https://raw.githubusercontent.com/espressif/esp-dl/master/models/coco_detect/README.md | **YOLO11n en S3: 320² = 961,5 ms (~1 FPS), 640² = 3906 ms (~0,26 FPS)**, mAP 0,276/0,370, flash 8 MB; nota SD exige PSRAM ≥16 MB → COCO/YOLO en tiempo real INVIABLE en esta placa (8 MB PSRAM) | A (citado en addendum P) |
| https://raw.githubusercontent.com/espressif/esp-dl/master/models/yolo26/README.md | YOLO26n 512² S3 = 2348 ms (~0,43 FPS), 640² = 3793 ms; **exige 16 MB PSRAM** (la placa tiene 8) → inviable | A (citado en addendum P) |
| https://github.com/espressif/esp-dl/blob/master/models/cat_detect/README.md ; .../hand_detect/README.md | espdet_pico_224: model 120 ms (~7,7 FPS), mAP cat 0,656 / hand 0,408; 416² = 418 ms → visión ligera viable (fallback) | A |
| https://github.com/espressif/esp-dl/blob/master/models/hand_gesture_recognition/README.md | MobileNetV2_0.5 128²: 69 ms + 4 ms pre (~13 FPS); componente `HandGestureRecognizer` → fallback (b) | A |
| https://github.com/espressif/esp-dl/blob/master/models/README.md | Model zoo; **OCR PP-OCRv6 solo ESP32-P4** (inviable en S3); color_detect/motion_detect "All ESP32" | A |
| https://developer.espressif.com/workshops/edge-ai-with-esp32-s3/assignment-6 | Taller oficial: YOLO11n en S3 **6–26 s/frame**, "el S3 no tiene NPU"; pregunta retórica sobre si tiene sentido con cámara en vivo | A |
| https://esp32-s31.espressif.com/en/official-projects/6 | 26.057 ms S3 vs 1.366 ms P4 (640×640) — el tiempo real de visión migró a P4/S31 | A |
| https://github.com/espressif/esp-detection | ESPDet-Pico ~126 ms en S3 a 224×224; exige entrenar el objeto propio (roza E10) | A |
| https://github.com/espressif/esp-sr | WakeNet10 con **español** (2026-09-30, `wn10_es_holaesp`); **MultiNet solo cn/en en S3** (no hay comandos de voz en español); VADNet; AEC full-duplex | A |
| https://docs.espressif.com/projects/esp-sr/en/latest/esp32s3/benchmark | WakeNet9: 16 KB RAM/324 KB PSRAM/3,0 ms; WakeNet10: 17 KB/523 KB/7,1 ms/22,6 % CPU; MultiNet5-7: 16–32 KB RAM/2,3–4,1 MB PSRAM; AFE 49–91 KB RAM/740–1240 KB PSRAM | A |
| https://github.com/espressif/esp-who | Refactorizado, IDF v5.4 soportado; examples face/pedestrian/QR; sin object-detection COCO propio | A |
| https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-reference/peripherals/i2s.html | ESP32-S3 tiene 2×I2S; STD RX mono/left; DMA independiente (coexistencia con DVP) — para la extensión futura de audio | A |
| https://github.com/espressif/esp-idf/issues/15770 | Receta I2S RX 32-bit mono 16 kHz (INMP441) — extensión futura | A |
| https://docs.espressif.com/projects/esp-matter/en/latest/esp32s3/introduction.html ; https://www.espressif.com/en/solutions/device-connectivity/esp-matter-solution | ESP32-S3 Matter over Wi-Fi (Thread requiere C6/H2/C5); no aporta al eje Big Data/IA del curso | A |
| https://products.espressif.com/api/user/file/Espressif_SoC_Product_Portfolio.pdf | **ESP32-S3 tiene sensor de temperatura interno** ("Temp Sensor: 1") → fuente 2 de C6 con $0 (die temp, proxy de carga, NO ambiente) | A |

## 4. Hardware GOOUUU ESP32-S3-CAM v1.5 — pinout corregido

| URL | Claim que sostiene | Verificador |
|---|---|---|
| https://github.com/bbhpy/docs/blob/main/GOOUUU_ESP32-S3-CAM.md | Mapa 2×20 exacto de esta placa: `IO1 SDA`/`IO2 SCL` = QWIIC I2C; `IO38/39/40` = SD; **`IO43 TX`/`IO44 RX` = UART0 real** (la SPEC vieja decía "41–44", mal); **41/42 LIBRES**; `IO21`, `IO47` libres; **`IO48 RGB[WS2812]`**; GPIO 22–25 no existen, 33–34 missing; 26–32 QSPI flash, 35–37 PSRAM | A |
| https://github.com/profharris/GOOUUU-Tech-ESP32-S3-CAM-Expansion-Board | Re-usa G1/G2 (VR/DHT11) y G41/G42 como I2C; **LED D1 = GPIO48 RGB** (confirma WS2812 onboard) | A |
| https://sargineer.com/p/goouuu-esp32s3cam | Pinmap alternativo de la placa (cámara = ESP32-S3-EYE pins) | A |
| Local: `dashcam-ia/SPEC.md` §1-2, `PLAN_DETECCION.md` E1–E3/E12, `firmware/main/Kconfig.projbuild:31-70`, `firmware/main/app_audio.c:128-146` (tras F0: `archive/dashcam-ia/…`) | Flash 16 MB / PSRAM 8 MB octal verificados en F0a; OV3660 PID 0x3660 verificado F1; **buzzer GPIO14** (LEDC), WS2812 GPIO48 confirmado por 4 fuentes (E12); PROHIBIDO 19/20 (USB device stack = riesgo brick, F18-A); recursos app 3,06 MB/1,08 libre, PSRAM 7,4 MB, heap 143–317 KB | A (evidencia local) |

## 5. Candidatos alternativos (matriz de decisión — finalistas y descartes)

| URL | Claim que sostiene | Verificador |
|---|---|---|
| https://edgeimpulse.com/pricing ; /faqs ; /blog/introducing-the-developer-plan ; https://tooldirectory.ai/tools/edge-impulse-ai-edge-machine-learning-iot-solutions | Edge Impulse adquirido por Qualcomm (mar-2025), sigue operativo con hardware no-Qualcomm incl. ESP32-S3; Developer Plan GRATIS (GPU training, FOMO, export C++/Arduino, ≤1000 unidades) — para la extensión futura (a) audio | A |
| https://docs.edgeimpulse.com/tutorials/end-to-end/sound-recognition ; https://docs.edgeimpulse.com/projects/expert-network/gunshot-audio-classification-arduino ; https://github.com/aureleq/HonkDetector ; https://github.com/lpesr/Low-Power-Sound-Recognition-Classification | Audio TinyML con datasets públicos ESC-50/UrbanSound8K; flujo MQTT-notificación documentado | A |
| https://wafaa-portfolio.vercel.app/blog/esp32-voice-recognition-debugging-journey | Trampa real (2025-10): modelo entrenado con mic de laptop falla con INMP441 (mismatch) → lección E10-audio: entrenar/validar desde la propia placa | A |
| https://github.com/worthant/edge-ai-voice-recognition ; https://hackster.io/amy/keyword-spotting-on-esp32-s3-with-inmp441-and-max7219 ; https://wiki.seeedstudio.com/xiao_esp32s3_keyword_spotting ; https://foresthub.ai/resources/esp32-s3-voice-recognition-edge-impulse ; https://teachmemicro.com | KWS ESP32-S3+INMP441 end-to-end: 96,12 % acc, 101 KB, 460 ms; S3 "Excelente" (modelo 80 KB, arena 120–200 KB); caveat ESP-NN no-oficial en SDK EI | A |
| https://note.com/ndenki/n/na74e725bc6aa ; https://github.com/VarunChopra11/WakeWord | Receta I2S STD RX 32-bit mono/left 16 kHz; cableado INMP441 en ESP32-S3-N16R8 (SCK41/WS42/SD2) — extensión futura | A |
| https://holocraft.dev/parts/inmp441-i2s-microphone ; https://aliexpress.com/w/wholesale-esp32-microphone-inmp441.html | Precio INMP441 ~US$0,93–1,50 (snapshot 2026-11-08, no oferta confirmada) → (a) requiere compra, NO viable HOY (E4) | A |
| https://ld2410.com/ld2410-esp32-arduino/ ; https://atomic14.com/esp32/sensors/hlk-ld2410 | LD2410: UART 256000 8N1, 5V alimentación + lógica 3,3 V, TX/RX cruzado, $3–6; presencia móvil+estática; **no cuenta personas** | A |
| https://github.com/ruvnet/RuView (`mmwave_sensor.c`) | Bytes exactos de la trama LD2410 (header F4F3F2F1, target_state/dist/energy, footer F8F7F6F5) | A |
| https://github.com/ncmreynolds/ld2410 ; https://github.com/iavorvel/MyLD2410 ; https://github.com/mgiesen/LD2410 ; https://github.com/lizardking/LD2410Async | Librerías Arduino maduras LD2410/B/C (fw 2.44); port a IDF trivial | A |
| https://esphome.io/components/sensor/ld2410 ; https://esphome.io/components/sensor/ld2450 ; https://r0.hlktech.com (manual LD2450 V1.03) | Componentes ESPHome nativos; LD2450 = tracking 2D de hasta 3 blancos (X/Y/ángulo/velocidad), BLE, débil en presencia estática | A |
| https://tinytronics.nl (HLK-LD2450, €7, 2025-06-20) ; https://espboards.dev/sensors/ld2450 (~$12) ; https://smarthomescene.com (2023-12) ; https://shillehtek.com (2026-06/09) ; https://hackster.io/craftiarenko (2023-11) | mmWave mainstream desde 2023 (novedad 2/5); precios LD2450 | A |
| https://arxiv.org/html/2610.13447v1 ; https://github.com/lokutor-ai/oido | Oído: ASR open-vocabulary en ESP32-S3 N16R8 (la placa exacta) pero ~1,97× bajo tiempo real (8,7 s para comando de 4 s) y exige INMP441 → tendencia 2026 no realtime, descartada como núcleo | A |
| https://blog.adafruit.com (2026-10-12) ; https://esp32.diy/esp-ai ; https://circuitdigest.com/articles/running-260K-tiny-llm-on-esp32 (2026-09-23) ; https://derekmolloy.ie (2026-07-10) ; https://ijert.org (AIVA, 2026-10) | Panorama edge-LLM 2026: viable en SBC/NPU; en MCU solo intenciones minúsculas (TinyLLM 260K params, 22 tok/s); "capacidad de lenguaje = ninguna en rango MCU"; hardware de voz requerido → (e) descartado | A |

## 6. Servicios externos

| URL | Claim que sostiene | Verificador |
|---|---|---|
| https://open-meteo.com/en/docs | API de pronóstico **gratis sin API key para uso no comercial**; hourly/current/past_days; modelos globales → fuente 3 de C6 con $0 (coords del aula en `.env`, P1) | A |
| https://espectre.dev/tools/monitor/ ; https://espectre.dev/tools/device-settings/ ; https://espectre.dev/tools/flash | Monitor (feedback en vivo del sensing), Device settings (config MQTT/sensing) y flash por navegador — herramientas LAN del vendor usadas en F1/F6b (E8) | P (referenciadas en Native README/SETUP) |

## 7. Evidencia local del proyecto (rutas, no URLs)

| Ruta | Qué sostiene |
|---|---|
| `iot2/.tmp/f23_research_tendencias.md` | Informe verbatim del analista de tendencias (matriz 32,0 vs 27,3, fórmula declarada) + ADDENDUM de verificación primaria del planificador (6 fetches ESPectre + confirmación esp-dl) |
| `iot2/.tmp/f23_research_viabilidad.md` | Informe verbatim del analista de viabilidad (mapa GPIO corregido, veredictos A–E, descartes firmes COCO/YOLO/OCR/MultiNet-es) |
| `iot2/.tmp/condiciones_curso.md` | C1–C14 extraídas de la propuesta aprobada y del PDF de referencia del curso |
| `iot2/.tmp/registro_riesgos_nuevo.md` | E1–E10 (restricciones de diseño heredadas del fracaso DriveGuard) + regla de prueba de vida temprana |
| `iot2/.tmp/ref_propuesta.txt` | Texto del PDF de referencia `1-Propuesta-Afluencia-Heladeria.pdf` (Proyecto 7 del curso) |
| `dashcam-ia/docs/PROPUESTA.md` (tras F0: `archive/dashcam-ia/docs/PROPUESTA.md`) | Formato de propuesta aprobado por el profesor (estilo heredado) |
| `dashcam-ia/SPEC.md`, `AGENTS.md`, `PLAN_DETECCION.md`, `.tmp/env_ok.md` | Hardware verificado F0a/F1, reglas duras heredadas, lecciones E1–E12 |

## 8. Notas de honestidad sobre las fuentes

- Las métricas del detector (F1 98,1–99,3 %, ms, CPU %) son del **dataset del vendor** (20 grabaciones reales por perfil, publicadas en espectre.dev/sdk/detectors). Las métricas LOCALES se miden en F1c (piloto) y F4 (validación manual) y se reportan con tamaño de muestra. No se prometen las cifras vendor como propias (E10).
- Los precios de AliExpress/tinytronics son snapshots de búsqueda, no ofertas confirmadas en checkout.
- La compatibilidad exacta del módulo GOOUUU N16R8 con ESPectre Native está documentada por familia de chip (S3 soportado) pero **no verificada empíricamente** → gate F1c (prueba de vida, S6).
- El esquema MQTT del broker en 14769 (plano vs TLS) no está verificado → gate F1d (S8); ambos esquemas soportados nativamente; sin WebSocket ni CA privada.
- INCONCLUSOS empíricos que se cierran en ejecución: RF del aula concurrida (S7), build con IDF 5.5.6 (S4/S5), latencia/tamaño exactos de modelos alternativos (solo aplican al fallback).