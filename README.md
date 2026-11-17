# AulaSense — Sensing de movimiento/ocupación por Wi-Fi CSI con IA en el ESP32-S3

> 📋 **Entrega para el curso: [docs/ENTREGA_PROFESOR.md](docs/ENTREGA_PROFESOR.md)** — empiece ahí (recorrido guiado de 10 min). Este README es el índice técnico.

> Proyecto IoT2/ITSE 2026. **$0 de hardware extra**: solo la placa GOOUUU ESP32-S3-CAM v1.5 (N16R8)
> y el router Wi-Fi 2.4 GHz existente. Sin micrófono, sin radar. **F24** añadió la cámara OV3660 como
> evidencia de evento (1 JPEG QVGA al onset de motion); **F25** la canaliza por MQTT al broker
> autenticado (canal primario) y sirve el sistema desde WSL con login, búsqueda IA y publicación VPS;
> **F26** (orden del dueño, 2026-11-17) añade actualización remota del firmware (**OTA por MQTT con
> manifiesto SHA-256 y rollback automático**, descargada por el túnel público), **provisioning fijo sin
> token**, gate de heap interno observable, decisión de aula con Clef y panel de salud — SPEC §16.
> Plan maestro: [`PLAN.md`](PLAN.md) · Especificación: [`SPEC.md`](SPEC.md) · Convenciones: [`AGENTS.md`](AGENTS.md)
> Checklist vivo: `../.tmp/checklist.md` (**v26**, lo mantiene el orquestador; v25 en `checklist_pre_v26.md`).

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
(norma N2, **LAN-only/opcional**); el destino final sigue siendo **solo el servidor local del
proyecto — nunca la nube**.
Galería en el dashboard **con login** y **búsqueda por descripción IA** (worker Qwen modo B
agente-mediado, rate limits + presupuesto de Credits; Fix F/S contra byte-loss de captura). La **IA
principal es el detector CSI en-chip**; la capa de visión persona en-chip (esp-dl) sigue opcional
default n (spike re-encuadrado a backend — PLAN §12.8). **Privacidad acotada** (consentimiento,
retención purgable, sin caras-ML): el CSI crudo nunca sale del chip; la imagen es evidencia accesoria,
no vigilancia. **Big data académico:** todas las fuentes aterrizan en SQLite + `consolidado.csv`
analizable (1.198 filas reales en 4 días, regenerado F26-h3; sesiones S1–S3B publicadas). Detallado en
[`docs/README.md`](docs/README.md); no duplicar aquí.

**Novedad 2026:** ESPectre v3.2.0 se publicó el 2026-11-11 y Espressif lo recomienda como community
project en [`esp-csi`](https://github.com/espressif/esp-csi). El sensing por Wi-Fi CSI (sin sensores
dedicados) es la tendencia 2026 de edge AI; **F24/F25 añaden evidencia de imagen QVGA acotada**
(el detector CSI no necesita cámara; la foto es accesoria, transportada por el broker autenticado con
retain=false, destino solo local, purgable).

**Limitación honesta:** detecta **MOVIMIENTO**, no presencia estacionaria, no prueba que una sala esté
vacía, no identifica ni cuenta personas. `idle` ≠ sala vacía. Una placa cubre una zona.

## Arquitectura (arquitectura REAL — SPEC §16.10, D-F26-11)

```
EDGE (ESP32-S3, sitio remoto)   VPS (SOLO broker + túneles)      SERVER WSL debian-aulasense (= gestiona TODO)   ANÁLISIS/DOCS
ESPectre Native            →    Mosquitto :14769 TCP         →   ingesta MQTT = writer único SQLite (ext4)    →   EDA + modelo
+ detector ML CSI                (auth; eventos + imagen           dashboard :8100 ─túnel─► VPS :10062 (login)     sklearn (F5a)
+ foto-evidencia QVGA              b64, retain=false)             estático OTA :8101 ─túnel─► VPS :10064      run_analisis.ps1
+ buzzer/WS2812                  túneles SSH wsl-port             workers visión (DASHSCOPE) + Clef               docs/entrega/
+ die temp (proxy de carga)      (pub-aulasense-*)                    (OpenRouter Decisions)                          validación manual
```

- **OUTBOUND-ONLY (zero-asunción-LAN, D-F26-10):** la placa opera LEJOS del servidor y **solo abre
  conexiones salientes** — MQTT al broker (eventos, telemetría, imágenes) y descarga HTTP del bin OTA
  por el túnel público. El servidor **JAMÁS alcanza a la placa**: `Direct HTTP :62587` y el receptor
  de imagen `:8001` son **LAN-only/opcionales** (no participan en la operación remota). Toda gestión
  remota (comandos, OTA) viaja por el MQTT que LA PLACA inicia; el recovery remoto es el **rollback
  automático de la OTA** (SPEC §16.1); UART/BOOT solo in situ.
- **VPS = SOLO broker MQTT + túneles SSH**; el servidor que gestiona todo (ingesta, BD, dashboard,
  workers, OTA estático) corre en **WSL `debian-aulasense`** (systemd, venv linux, sin Docker).
- Windows host = fallback (py3.15) + CLI wsl-port + tools (`gen_firmware_config.py`, `lee_serial.py`
  por USB-Serial-JTAG, `wsl_aulasense.ps1 salud`).

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

Feedback en vivo: dashboard WebSocket + [Monitor ESPectre](https://espectre.dev/tools/monitor/) (este
último **solo en LAN**, junto a la placa) + log serial por USB-Serial-JTAG (`tools/lee_serial.py`).
Guion completo de demo post-F26 (público + /salud + galería con decisión Clef + OTA `check` en vivo):
[`docs/guion_demo.md`](docs/guion_demo.md). Manual de operación del dueño (OTA/rollback/re-provisionamiento/
verificación remota): [`docs/NOTES_operacion.md`](docs/NOTES_operacion.md). Panel de salud: `http://167.114.169.134:10062/salud` (tras login).

## Estructura

```
aulasense/
├─ PLAN.md SPEC.md README.md AGENTS.md .gitignore .env.example .env
├─ claves/                  # HISTÓRICO: fuentes de credenciales archivadas `*.bak-f26` (gitignored; la autoridad es el .env raíz único, SPEC §16.8)
├─ firmware/
│   ├─ espectre/            # vendor ESPectre 3.2.0 (clon gitignored, GPLv3)
│   └─ parche_aulasense/    # derivado: app_feedback.c, app_imagen.c (MQTT+HTTP+Fix F; F26 gate
│                           #   95/24 sin latch + observabilidad), app_provision.c (F26: AP fija
│                           #   + /cfg sin token), app_ota.c (F26: OTA comando MQTT + SHA-256 +
│                           #   rollback), Kconfig, CMake
├─ server/
│   ├─ ingesta/             # suscriptor MQTT (+decoder image) → SQLite WAL + CSV + receptor N2
│   ├─ dashboard/           # FastAPI + WS + Chart.js + galería/búsqueda + login (auth.py) + /salud F26
│   ├─ vision/              # cliente worker visión modo B (rate limits, presupuesto, QC local)
│   │                       #   + clef.py (decisión de aula F26, cascada clef-flash→clef)
│   ├─ deploy/              # units systemd WSL + README_wsl.md (debian-aulasense, VPS 10062)
│   │                       #   + aulasense-ota.service (estático :8101 → túnel VPS :10064)
│   ├─ ble/  fuentes/  datos/  tests/   # (BLE opcional; openmeteo; DB/imagenes gitignored; pytest)
├─ analysis/                # eda.py + modelo_ocupacion.py + run_analisis.ps1 (F5a) · dataset/
├─ dataset/sesion_1/        # dataset F4 publicado (jsonl de sesión con GT corroborado)
├─ tools/                   # start/stop_demo, wsl_aulasense, gen_firmware_config, config_mqtt,
│                           #   vision_describe, reset_admin, purga_imagenes, lee_serial,
│                           #   generar_informe.py (generador único de INFORME/PRESENTACION), etc.
├─ docs/                    # README (índice), PROPUESTA, guion_demo (F26), NOTES_operacion (F26),
│                           #   guion_video+video, fuentes, analisis/, entrega/
├─ assets/                  # diagrama_tecnico.*, poster_funcionamiento.png, video/
└─ .tmp/                    # temporales (gitignored)
```

## Privacidad y seguridad

- **Privacidad ACOTADA (F24 re-enmarcado por F25, orden del dueño):** el sistema captura 1 imagen
  QVGA de evidencia al onset de motion (cooldown 15 s). **F25 (D-F25-1):** la foto transita el
  **broker MQTT autenticado** (`broker.gilman.pro`, usuario nodo `electiva`, prefijo aislado
  `aulasense/v1`, topic `image` **retain=false**, QoS 0) con **fallback HTTP :8001** (N2, Bearer
  token, **LAN-only/opcional**: interviene solo cuando placa y servidor comparten red, nunca en la
  operación remota); el **destino final es solo el servidor local del proyecto — NUNCA la nube** (no es el
  "broker público" de la regla F24: es un broker con auth + prefijo + sin retención). Galería y
  dashboard **con login** (admin único, password generada por código → `.env` raíz único, F26
  §16.8). El CSI crudo
  nunca sale del chip. **Consentimiento de aula** y **retención/borrado configurables** (default
  30 días; `tools/purga_imagenes.py` marca `imagen_purgada` y conserva la fila → el dataset no se
  rompe; docs publicados: [`docs/consentimiento_aula.md`](docs/consentimiento_aula.md) y
  [`docs/politica_retencion_imagenes.md`](docs/politica_retencion_imagenes.md));
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
  ingesta (:8001, token obligatorio, JPEG magic+sintaxis, ≤64 KB, rate-limit; **canal LAN-only,
  opcional** — la ruta primaria de la imagen es siempre MQTT saliente, D-F25-1). Secretos en el **`.env` ÚNICO de la raíz** (F26 §16.8; gitignored),
  nunca impresos ni en argv. **Provisioning AP fallback**:
  ← **F26 (SPEC §16.2):** portal con forma nueva — AP fija `AULASENSE-CFG` (WPA2; pass canónica
  `itse-aulasense`, publicada por orden explícita del dueño: **capacidad local**, exige asociarse
  físicamente al AP y no habilita nada remoto) y portal **sin token** en `http://192.168.4.1/cfg`
  (Origin guard, cooldown NVS, nunca loguea SSID/pass; criterios E2–E5 de router caído quedan
  pendientes de ventana natural — `docs/README.md`). **REALIDAD FLASHEA: la build viva (f26.6) ya usa
  el portal fijo sin token** — OTA y rollback demostrados en banco (`docs/NOTES_operacion.md`). Detalle:
  [`AGENTS.md`](AGENTS.md) regla 2/14 + [`SPEC.md`](SPEC.md) §14/§15/§16.2.

## Estado (2026-11-17)

Fases ejecutadas sobre el mapa de [`PLAN.md`](PLAN.md) §6/§11/§12/§13 (+ anotación de estados §12.8):
**F0–F3** completadas (gate F1c de prueba de vida pasado en placa); **F24** b/c/d + Fix F ejecutados
(la capa de visión en-chip F24-e NO se lanzó; el spike F24-e0 se re-encuadró a backend con veredicto
GO-COSTO — §12.8); **F25** a–f1..f5 + b1 + distro `debian-aulasense`/VPS completados (login, búsqueda,
visión modo B **cerrada** 34 hechas/20 error irreparables/1 omitido, Fix S desplegado, video, N2 con
caveat LAN); **F5a** ejecutado (pipeline + `run_analisis.ps1`, flag ts_ms cerrado); **F4**: sesiones
S1–S3B publicadas — `consolidado.csv` **regenerado F26-h3: 1.198 filas 100 % `origen=real`** en 4 días
(reemplaza las 462 iniciales; regla ts explícita que filtró la fila absurda id 56 documentada, BD sin
editar; S2B/S3B con **GT fotográfico=0** → aportan estabilidad, no recall). **Gates F25**: suite 299/299 (suite actual post-F26: **407 passed + 1 skipped** en ambos runtimes — evidencia `.tmp/estado-ejecutor-a3.md`, 2026-11-17) +
coverage 86 % ≥ 78 % + auditoría externa a doble ciego 2026-11-15 **sin críticos**
(`.tmp/informe_consens_20261115.md`; los drifts documentales allí flaggeados se corrigieron en estos
docs). **F26 ejecutada (plan v26), estado en placa 2026-11-17:** OTA real por MQTT **demostrada E2E dos
veces por aire** (f26.4→f26.5→f26.6; descarga ~34 s, agujero de telemetría 30 s esperado y monitoreado)
+ **rollback automático probado** (bin malo → revert del bootloader en 225 s medidos; sha alterado →
`OTA_ERROR` sin reboot; anti-brick ×5) — gate OTA **enmendado con evidencia 120/64→96/48** (SPEC §16.1);
provisioning **fijo sin token en vigor** (AP `AULASENSE-CFG`, portal `http://192.168.4.1/cfg`; build viva
**f26.6 válida**); panel **/salud** + `estado_sistema` + eventos `SISTEMA_*`; **decisión de aula Clef**
sobre 60 de 81 fotos reales (hallazgo honesto: **75 % IMAGEN_DEGRADADA**, decodificable≠legible, cuello
QVGA q12); `.env` **ÚNICO** raíz; costes medidos: Clef **$0,0053/62** llamadas API, visión F26
26 llamadas/12.180 tokens. Operación: [`docs/NOTES_operacion.md`](docs/NOTES_operacion.md).
El checklist vivo (`../.tmp/checklist.md`, **v26**) es la fuente de verdad renglón a renglón.

## Licencia

El firmware base ESPectre es **GPLv3** (© Francesco Pace y contribuidores). El derivado de AulaSense
(`firmware/parche_aulasense/`) se publica bajo GPLv3 (fuente disponible para el curso). El resto del
código propio (server/analysis/tools) es material académico del curso IoT2/ITSE.