# AulaSense — Sensing de movimiento/ocupación por Wi-Fi CSI con IA en el ESP32-S3

> Proyecto IoT2/ITSE 2026. **$0 de hardware extra**: solo la placa GOOUUU ESP32-S3-CAM v1.5 (N16R8)
> y el router Wi-Fi 2.4 GHz existente. Sin micrófono, sin radar. **F24:** la cámara OV3660 onboard se usa
> como evidencia de evento (1 JPEG QVGA al onset de motion, solo al servidor local).
> Plan maestro: [`PLAN.md`](PLAN.md) · Especificación: [`SPEC.md`](SPEC.md) · Convenciones: [`AGENTS.md`](AGENTS.md)
> Checklist vivo: `../.tmp/checklist.md` (**v24**, lo mantiene el orquestador; v23 en `checklist_pre_v24.md`).

## Qué es

AulaSense lee el **Channel State Information (CSI)** del radio Wi-Fi y ejecuta **en el chip** un
detector neuronal ([ESPectre](https://github.com/francescopace/espectre) tag 3.2.0, MLP de 529 parámetros)
que decide `idle | motion` cada 250 ms. Cuando alguien (o algo) se mueve en la zona, cambia la forma en
que las señales Wi-Fi viajan por la sala; el detector lo captura. Los eventos JSON viajan por MQTT a un
broker, una ingesta en el PC los guarda en SQLite (dataset propio), un dashboard FastAPI los muestra en
tiempo real, y un análisis pandas + sklearn predice la actividad del aula por franja horaria. Alerta
audible local (buzzer) + visual (LED WS2812).

**F24 (orden del dueño, 2026-11-12):** al onset de movimiento el nodo captura **1 imagen QVGA de
evidencia** (cooldown 15 s) y la sube por HTTP **solo al servidor local del proyecto** (nunca a la nube
ni al broker público); el dashboard tiene galería loopback con retención configurable. La **IA principal
es el detector CSI en-chip**; una capa de visión persona (esp-dl) es **opcional default n**, sujeta a un
spike de coexistencia — si no cabe junto al CSI, se abandona documentado. **Privacidad acotada local-only**
(consentimiento de aula, retención purgable, sin caras-ML): el CSI crudo nunca sale del chip; la imagen es
evidencia accesoria, no vigilancia. **Big data académico:** todas las fuentes (CSI, métricas, imágenes+ia,
clima, config) aterrizan en SQLite + consolidado CSV analyzable.

**Novedad 2026:** ESPectre v3.2.0 se publicó el 2026-11-11 y Espressif lo recomienda como community
project en [`esp-csi`](https://github.com/espressif/esp-csi). El sensing por Wi-Fi CSI (sin sensores
dedicados) es la tendencia 2026 de edge AI; **F24 añade evidencia de imagen QVGA acotada local-only**
(el detector CSI no necesita cámara; la foto es accesoria, solo al servidor local, purgable).

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
git clone --branch 3.2.0 --depth 1 https://github.com/francescopace/espectre.git firmware/espectre

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

- **Privacidad acotada local-only (F24, re-enmarque por orden del dueño):** el sistema captura 1 imagen
  QVGA de evidencia al onset de motion (cooldown 15 s). Las imágenes van **SOLO al servidor local del
  proyecto** (LAN→ingesta :8001 con Bearer token; galería servida solo en loopback 127.0.0.1:8000),
  **NUNCA a la nube ni al broker MQTT público**. El CSI crudo nunca sale del chip. **Consentimiento de
  aula documentado** (las imágenes pueden capturar personas = datos personales); **retención/borrado
  configurables** (default 30 días; `tools/purga_imagenes.py` marca `imagen_purgada` y conserva la fila →
  el dataset no se rompe); imágenes **gitignored**; **sin caras-ML** (no detección facial). La imagen es
  accesoria: si la cola se llena o el heap cae, se descarta/auto-deshabilita y el evento CSI sigue.
  **C9 = cumple-parcial (local-only), no "privacidad por construcción".**
- Los datos de movimiento pueden revelar rutinas → aviso de consentimiento en el aula, minimización,
  broker con auth.
- Dashboard **solo en `127.0.0.1`**, Origin guard, `esc()` XSS, SQL parametrizado. El upload de imágenes
  va a un receptor HTTP separado en la ingesta (:8001, token obligatorio, JPEG magic, ≤64 KB, rate-limit);
  el dashboard solo sirve la galería por id de BD en loopback. Secretos en `.env` (gitignored), nunca
  impresos ni en argv. **Provisioning AP fallback** con ruta privada `/cfg/<token>` (Origin guard, cooldown
  NVS, nunca loguea SSID/pass). Detalle: [`AGENTS.md`](AGENTS.md) regla 2/14 + [`SPEC.md`](SPEC.md) §14.

## Estado

Andamiaje creado por el `planificador` (2026-11-12), **ampliado F24** (evidencia de imagen local +
provisioning AP privado + IA visión opcional spike-gated + big data académico). La ejecución sigue el
mapa de fases de [`PLAN.md`](PLAN.md) §6 (F0→F1∥F2→F3→F4→F5→F6→GATE→F7) **+ §11 (F24-a..f, spike F24-e0)**.
El gate de prueba de vida (F1c) demuestra el fenómeno físico en placa ANTES de construir el resto; si
falla 2 intentos, el orquestador decide el Plan B. **F24-c/d esperan F1 completada** (placa única COM6);
F24-a (spec) y F24-b (server) corren en paralelo sin tocar la placa. La IA de visión solo se integra si
el spike F24-e0 prueba coexistencia con el CSI; si no, se abandona documentado.

## Licencia

El firmware base ESPectre es **GPLv3** (© Francesco Pace y contribuidores). El derivado de AulaSense
(`firmware/parche_aulasense/`) se publica bajo GPLv3 (fuente disponible para el curso). El resto del
código propio (server/analysis/tools) es material académico del curso IoT2/ITSE.