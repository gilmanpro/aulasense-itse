# AulaSense — Sensing de movimiento/ocupación por Wi-Fi CSI con IA en el ESP32-S3

> 📋 **Entrega para el curso: [docs/ENTREGA_PROFESOR.md](docs/ENTREGA_PROFESOR.md)** — empiece ahí (recorrido guiado de 10 min). Este README es el índice técnico.

> Proyecto IoT2/ITSE 2026. **$0 de hardware extra**: solo la placa GOOUUU ESP32-S3-CAM v1.5 (N16R8)
> y el router Wi-Fi 2.4 GHz existente. Sin micrófono, sin radar. **F24** añadió la cámara OV3660 como
> evidencia de evento (1 JPEG QVGA al onset de motion); **F25** la canaliza por MQTT al broker
> autenticado (canal primario) y sirve el sistema desde WSL con login, búsqueda IA y publicación VPS.
> Plan maestro: [`PLAN.md`](PLAN.md) · Especificación: [`SPEC.md`](SPEC.md) · Convenciones: [`AGENTS.md`](AGENTS.md)
> Checklist vivo: `../.tmp/checklist.md` (**v25**, lo mantiene el orquestador; v24 en `checklist_pre_v25.md`).

## Qué es

AulaSense lee el **Channel State Information (CSI)** del radio Wi-Fi y ejecuta **en el chip** un
detector neuronal ([ESPectre](https://github.com/francescopace/espectre) tag 3.2.0, MLP de 529 parámetros)
que decide `idle | motion` cada 250 ms. Cuando alguien (o algo) se mueve en la zona, cambia la forma en
que las señales Wi-Fi viajan por la sala; el detector lo captura. Los eventos JSON (y la foto-evidencia
JPEG, canal primario MQTT) viajan por MQTT a un broker autenticado, una ingesta en **WSL Debian** los
guarda en SQLite (dataset propio), un dashboard FastAPI **con login** los muestra en tiempo real
(local y publicado en el VPS), y un análisis pandas + sklearn predice la actividad del aula por franja
horaria. Alerta audible local (buzzer) + visual (LED WS2812).

**F24/F25 (orden del dueño, 2026-11-12/14):** al onset de movimiento el nodo captura **1 imagen QVGA de
evidencia** (cooldown 15 s) y la envía por **MQTT al broker autenticado** (topic `image`, b64, QoS 0,
retain=false — D-F25-1; norma N1: la MISMA conexión persistente del nodo) con **fallback HTTP :8001**
(norma N2); el destino final sigue siendo **solo el servidor local del proyecto — nunca la nube**.
Galería en el dashboard **con login** y **búsqueda por descripción IA** (worker Qwen modo B
agente-mediado, rate limits + presupuesto de Credits; Fix F/S contra byte-loss de captura). La **IA
principal es el detector CSI en-chip**; la capa de visión persona en-chip (esp-dl) sigue opcional
default n (spike re-encuadrado a backend — PLAN §12.8). **Privacidad acotada** (consentimiento,
retención purgable, sin caras-ML): el CSI crudo nunca sale del chip; la imagen es evidencia accesoria,
no vigilancia. **Big data académico:** todas las fuentes aterrizan en SQLite + `consolidado.csv`
analizable; sesión S1 del dataset F4 ya publicada (S2/S3 en curso). Detallado en
[`docs/README.md`](docs/README.md); no duplicar aquí.

**Novedad 2026:** ESPectre v3.2.0 se publicó el 2026-11-11 y Espressif lo recomienda como community
project en [`esp-csi`](https://github.com/espressif/esp-csi). El sensing por Wi-Fi CSI (sin sensores
dedicados) es la tendencia 2026 de edge AI; **F24/F25 añaden evidencia de imagen QVGA acotada**
(el detector CSI no necesita cámara; la foto es accesoria, transportada por el broker autenticado con
retain=false, destino solo local, purgable).

**Limitación honesta:** detecta **MOVIMIENTO**, no presencia estacionaria, no prueba que una sala esté
vacía, no identifica ni cuenta personas. `idle` ≠ sala vacía. Una placa cubre una zona.

## Arquitectura (4 capas)

```
EDGE (ESP32-S3)          TRANSPORTE                 SERVER (WSL Debian)       ANÁLISIS/DOCS
ESPectre Native    →     MQTT broker.gilman.pro  →  ingesta → SQLite     →   EDA + modelo
+ detector ML CSI        :14769 (auth; eventos +     dashboard +login           sklearn (F5a)
+ foto-evidencia QVGA      imagen, D-F25-1)           8100 NAT → VPS 10062     run_analisis.ps1
+ buzzer/WS2812          + HTTP :8001 N2 (fallback)   worker visión modo B     docs/entrega/
+ die temp               + Direct HTTP :62587         + Open-Meteo             validación manual
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
# Runtime PRIMARIO (WSL Debian / debian-aulasense; URLs vigentes en docs/README.md):
powershell tools\wsl_aulasense.ps1 status   # dashboard: http://167.114.169.134:10062 (público)
# … caminar por la zona → motion en el dashboard + buzzer + LED + foto-evidencia en galería …

# Fallback Windows (NUNCA a la vez que el runtime WSL — split-brain, AGENTS regla 3):
powershell tools\start_demo.ps1    # ingesta + dashboard + openmeteo; http://127.0.0.1:8000
powershell tools\stop_demo.ps1     # mata SOLO los procesos que arrancó el launcher
```

Feedback en vivo: dashboard WebSocket + [Monitor ESPectre](https://espectre.dev/tools/monitor/) + log
serial. Guion completo: [`docs/guion_demo.md`](docs/guion_demo.md) (lo crea la fase F5b).

## Estructura

```
aulasense/
├─ PLAN.md SPEC.md README.md AGENTS.md .gitignore .env.example
├─ claves/                  # .env de auth/IA (gitignored, generado por código) + .env.example
├─ firmware/
│   ├─ espectre/            # vendor ESPectre 3.2.0 (clon gitignored, GPLv3)
│   └─ parche_aulasense/    # derivado: app_feedback.c, app_imagen.c (MQTT+HTTP+Fix F),
│                           #   app_provision.c (AP + /cfg/<token>), Kconfig, CMake
├─ server/
│   ├─ ingesta/             # suscriptor MQTT (+decoder image) → SQLite WAL + CSV + receptor N2
│   ├─ dashboard/           # FastAPI + WS + Chart.js + galería/búsqueda + login (auth.py)
│   ├─ vision/              # cliente worker visión modo B (rate limits, presupuesto, QC local)
│   ├─ deploy/              # units systemd WSL + README_wsl.md (debian-aulasense, VPS 10062)
│   ├─ ble/  fuentes/  datos/  tests/   # (BLE opcional; openmeteo; DB/imagenes gitignored; pytest)
├─ analysis/                # eda.py + modelo_ocupacion.py + run_analisis.ps1 (F5a) · dataset/
├─ dataset/sesion_1/        # dataset F4 publicado (jsonl de sesión con GT corroborado)
├─ tools/                   # start/stop_demo, wsl_aulasense, gen_firmware_config, config_mqtt,
│                           #   vision_describe, reset_admin, purga_imagenes, lee_serial, etc.
├─ docs/                    # README (índice), PROPUESTA, guion_video+video, fuentes, analisis/, entrega/
├─ assets/                  # diagrama_tecnico.*, poster_funcionamiento.png, video/
└─ .tmp/                    # temporales (gitignored)
```

## Privacidad y seguridad

- **Privacidad ACOTADA (F24 re-enmarcado por F25, orden del dueño):** el sistema captura 1 imagen
  QVGA de evidencia al onset de motion (cooldown 15 s). **F25 (D-F25-1):** la foto transita el
  **broker MQTT autenticado** (`broker.gilman.pro`, usuario nodo `electiva`, prefijo aislado
  `aulasense/v1`, topic `image` **retain=false**, QoS 0) con **fallback HTTP :8001** (N2, Bearer
  token); el **destino final es solo el servidor local del proyecto — NUNCA la nube** (no es el
  "broker público" de la regla F24: es un broker con auth + prefijo + sin retención). Galería y
  dashboard **con login** (admin único, password generada por código → `claves/.env`). El CSI crudo
  nunca sale del chip. **Consentimiento de aula** y **retención/borrado configurables** (default
  30 días; `tools/purga_imagenes.py` marca `imagen_purgada` y conserva la fila → el dataset no se
  rompe; docs `consentimiento_aula.md`/`politica_retencion_imagenes.md` se publican en F25-g);
  imágenes **gitignored**; **sin caras-ML**. Fix F/S: frames byte-loss se descartan en placa y en
  el decoder (nunca basura en BD). La imagen es accesoria: si la cola se llena o el heap cae, se
  descarta/auto-deshabilita y el evento CSI sigue. **C9 = cumple-parcial (local-only + broker
  autenticado), no "privacidad por construcción".**
- Los datos de movimiento pueden revelar rutinas → aviso de consentimiento en el aula, minimización,
  broker con auth.
- Dashboard con **login en todas las rutas** (cookie HttpOnly+SameSite, lockout progresivo, Origin
  guard, `esc()` XSS, SQL parametrizado). Bind: loopback en Windows; `0.0.0.0` SOLO VM-local en WSL
  (NAT); la exposición externa es el túnel VPS `:10062` tras el guard de Origin — detalle operativo
  en `server/deploy/README_wsl.md`. El upload N2 HTTP lo atiende un receptor separado dentro de la
  ingesta (:8001, token obligatorio, JPEG magic+sintaxis, ≤64 KB, rate-limit). Secretos en `.env` /
  `claves/.env` (gitignored), nunca impresos ni en argv. **Provisioning AP fallback** con ruta
  privada `/cfg/<token>` (Origin guard, cooldown NVS, nunca loguea SSID/pass; criterios E2–E5
  pendientes de ventana natural — `docs/README.md`). Detalle: [`AGENTS.md`](AGENTS.md) regla 2/14 +
  [`SPEC.md`](SPEC.md) §14/§15.

## Estado (2026-11-15)

Fases ejecutadas sobre el mapa de [`PLAN.md`](PLAN.md) §6/§11/§12 (+ anotación de estados §12.8):
**F0–F3** completadas (gate F1c de prueba de vida pasado en placa); **F24** b/c/d + Fix F ejecutados
(la capa de visión en-chip F24-e NO se lanzó; el spike F24-e0 se re-encuadró a backend con veredicto
GO-COSTO — §12.8); **F25** a–f1..f5 + b1 + distro `debian-aulasense`/VPS completados (login, búsqueda,
visión modo B **cerrada** 34 hechas/20 error irreparables/1 omitido, Fix S desplegado, video, N2 con
caveat LAN); **F5a** ejecutado (pipeline + `run_analisis.ps1`, flag ts_ms cerrado); **F4**: sesión S1
publicada (dataset + `consolidado.csv` 462 filas real) con S2/S3 en curso. **Gates**: suite 299/299 +
coverage 86 % ≥ 78 % + auditoría externa a doble ciego 2026-11-15 **sin críticos**
(`.tmp/informe_consens_20261115.md`; los drifts documentales allí flaggeados se corrigieron en estos
docs). **En vuelo:** F6a (INFORME/PRESENTACIÓN con TODO), F6b (ensayo en WSL), GATES finales ×7 y F7.
El checklist vivo (`../.tmp/checklist.md`, v25) es la fuente de verdad renglón a renglón.

## Licencia

El firmware base ESPectre es **GPLv3** (© Francesco Pace y contribuidores). El derivado de AulaSense
(`firmware/parche_aulasense/`) se publica bajo GPLv3 (fuente disponible para el curso). El resto del
código propio (server/analysis/tools) es material académico del curso IoT2/ITSE.