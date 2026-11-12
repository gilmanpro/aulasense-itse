# AulaSense — Sensing de movimiento/ocupación por Wi-Fi CSI con IA en el ESP32-S3

> Proyecto IoT2/ITSE 2026. **$0 de hardware extra**: solo la placa GOOUUU ESP32-S3-CAM v1.5 (N16R8)
> y el router Wi-Fi 2.4 GHz existente. Sin cámara, sin micrófono, sin radar.
> Plan maestro: [`PLAN.md`](PLAN.md) · Especificación: [`SPEC.md`](SPEC.md) · Convenciones: [`AGENTS.md`](AGENTS.md)
> Checklist vivo: `../.tmp/checklist.md` (v23, lo mantiene el orquestador).

## Qué es

AulaSense lee el **Channel State Information (CSI)** del radio Wi-Fi y ejecuta **en el chip** un
detector neuronal ([ESPectre](https://github.com/francescopace/espectre) v3.2.0, MLP de 529 parámetros)
que decide `idle | motion` cada 250 ms. Cuando alguien (o algo) se mueve en la zona, cambia la forma en
que las señales Wi-Fi viajan por la sala; el detector lo captura **sin imágenes ni audio** — privacidad
por construcción. Los eventos JSON viajan por MQTT a un broker, una ingesta en el PC los guarda en
SQLite (dataset propio), un dashboard FastAPI los muestra en tiempo real, y un análisis pandas + sklearn
predice la actividad del aula por franja horaria. Alerta audible local (buzzer) + visual (LED WS2812).

**Novedad 2026:** ESPectre v3.2.0 se publicó el 2026-11-11 y Espressif lo recomienda como community
project en [`esp-csi`](https://github.com/espressif/esp-csi). El "no-camera privacy sensing" es la
tendencia 2026 de edge AI privado.

**Limitación honesta:** detecta **MOVIMIENTO**, no presencia estacionaria, no prueba que una sala esté
vacía, no identifica ni cuenta personas. `idle` ≠ sala vacía. Una placa cubre una zona.

## Arquitectura (4 capas)

```
EDGE (ESP32-S3)          TRANSPORTE              SERVER PC                 ANÁLISIS/DOCS
ESPectre Native    →     MQTT broker.gilman.pro  →  ingesta → SQLite   →   EDA pandas
+ detector ML CSI        :14769 (auth)              dashboard FastAPI       modelo sklearn
+ buzzer/WS2812          + Direct HTTP :62587       127.0.0.1:8000          Word + PPTX
+ die temp               (WiFi 2.4 GHz)             + Open-Meteo            validación manual
```

## Instalación rápida

Requisitos: Windows 11, `C:\Python315\python.exe` (el `python` del PATH está roto), `eim` (Espressif
Installation Manager), placa GOOUUU ESP32-S3-CAM v1.5, red Wi-Fi 2.4 GHz. **Sin Docker.**

```powershell
# 1) Secretos: copiar .env.example a .env y rellenar WiFi + clave del broker (NUNCA commitear .env)
Copy-Item .env.example .env

# 2) Toolchain ESP-IDF 5.5.6 (el SDK ESPectre requiere >=5.5.3) + vendor
eim install 5.5.6
eim run "idf.py --version" v5.5.6
git clone --branch v3.2.0 --depth 1 https://github.com/francescopace/espectre.git firmware/espectre

# 3) Build + flash del firmware Native (ver SPEC.md §4 para sdkconfig y provisioning)
cd firmware/espectre/src/cpp/frontend/native/app
eim run "idf.py set-target esp32s3" v5.5.6
eim run "idf.py build" v5.5.6
eim run "idf.py -p COM6 flash monitor" v5.5.6   # COM6 = USB-Serial-JTAG; autodetectar el puerto

# 4) Server PC (venv py3.15)
cd ../../../..
C:\Python315\python.exe -m venv server\.venv
server\.venv\Scripts\python.exe -m pip install -r server\requirements.txt
```

## Arranque (demo)

```powershell
powershell tools\start_demo.ps1    # ingesta + dashboard + openmeteo; abre http://127.0.0.1:8000
# … caminar por la zona → motion en el dashboard + buzzer + LED …
powershell tools\stop_demo.ps1     # mata SOLO los procesos que arrancó el launcher
```

Feedback en vivo: dashboard WebSocket + [Monitor ESPectre](https://espectre.dev/tools/monitor/) + log
serial. Guion completo: [`docs/guion_demo.md`](docs/guion_demo.md) (lo crea la fase F5b).

## Estructura

```
aulasense/
├─ PLAN.md SPEC.md README.md AGENTS.md .gitignore .env.example
├─ firmware/
│   ├─ espectre/            # vendor ESPectre v3.2.0 (clon gitignored, reproducible por tag, GPLv3)
│   └─ parche_aulasense/    # nuestro derivado: app_feedback.c (buzzer/WS2812/die temp/BLE opt)
├─ server/
│   ├─ ingesta/             # suscriptor MQTT → valida → SQLite WAL + CSV (paho-mqtt)
│   ├─ dashboard/           # FastAPI + WebSocket + Chart.js (static/), bind 127.0.0.1
│   ├─ ble/                 # receptor BLE opcional (bleak)
│   ├─ fuentes/             # openmeteo.py (pronóstico gratis sin key)
│   ├─ datos/               # aulasense.db + exports CSV (gitignored salvo dataset consolidado)
│   └─ tests/               # pytest (validador, época, bordes, Origin guard, SQL)
├─ analysis/                # eda.py + modelo_ocupacion.py (sklearn) · dataset/consolidado.csv (real)
├─ tools/                   # start_demo/stop_demo.ps1, gen_firmware_config.py, config_mqtt.py, lee_serial.py
├─ docs/                    # PROPUESTA.md, guion_demo.md, INFORME.docx, PRESENTACION.pptx, fuentes.md
├─ assets/                  # figuras del EDA/modelo, diagramas
└─ .tmp/                    # temporales (gitignored)
```

## Privacidad y seguridad

- **Sin cámara ni micrófono**: el CSI crudo nunca sale del chip en la ruta crítica; solo eventos de
  movimiento + métricas. No hay imágenes que proteger (privacidad por construcción).
- Los datos de movimiento pueden revelar rutinas → aviso de consentimiento en el aula, minimización
  (sin identidades), broker con auth.
- Dashboard **solo en `127.0.0.1`**, Origin guard, `esc()` XSS, SQL parametrizado, cero endpoints de
  imágenes. Secretos en `.env` (gitignored), nunca impresos ni en argv. Detalle: [`AGENTS.md`](AGENTS.md).

## Estado

Andamiaje creado por el `planificador` (2026-11-12). La ejecución sigue el mapa de fases de
[`PLAN.md`](PLAN.md) §6 (F0 inicio repo → F1 piloto CSI ∥ F2 backend → F3 integración → F4 dataset →
F5 análisis/docs → F6 docs finales/ensayo → GATE especialistas → F7 cierre). El gate de prueba de vida
(F1c) demuestra el fenómeno físico en placa ANTES de construir el resto; si falla 2 intentos, el
orquestador decide el Plan B (finalista: visión ligera).

## Licencia

El firmware base ESPectre es **GPLv3** (© Francesco Pace y contribuidores). El derivado de AulaSense
(`firmware/parche_aulasense/`) se publica bajo GPLv3 (fuente disponible para el curso). El resto del
código propio (server/analysis/tools) es material académico del curso IoT2/ITSE.