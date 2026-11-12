# AGENTS.md — Convenciones del proyecto AulaSense (Wi-Fi CSI edge sensing)

Este archivo lo lee todo agente que trabaje en el repo. Respétalo al pie de la letra.
Hereda las reglas duras del proyecto anterior (DriveGuard, `../archive/dashcam-ia/AGENTS.md` tras el
archivado) **con las correcciones del dominio CSI**. Plan maestro: `PLAN.md`. Especificación: `SPEC.md`.
Checklist vivo: `../.tmp/checklist.md` (en `iot2\.tmp\checklist.md`, v23).

## Qué es esto
Sensor de movimiento/ocupación dinámica con IA **en el borde** (ESP32-S3 GOOUUU v1.5): detector
neuronal ESPectre sobre Wi-Fi CSI 100 % en el chip, eventos JSON por MQTT a un dashboard en vivo
(FastAPI+Chart.js), dataset propio en SQLite, análisis pandas con modelo predictivo de actividad por
hora, alerta audible (buzzer GPIO14) + visual (WS2812 GPIO48). **Sin cámara, sin micrófono, sin radar.**

## Reglas duras
1. **Edge-first:** el detector ML corre EN el ESP32-S3. Ningún procesamiento de sensing/IA se mueve al
   PC. El PC solo ingesta, almacena, visualiza y analiza eventos (el modelo sklearn de análisis es la
   excepción docente prevista por el curso, NO el detector).
2. **Privacidad por construcción:** no hay cámara ni micrófono. **La cámara OV3660 NUNCA se inicializa**
   (verificar sdkconfig sin driver camera). El **CSI crudo NUNCA sale del chip** en la ruta crítica (el
   modo `GET /csi` collection es opcional, solo investigación, y NO se usa). Solo eventos de movimiento
   + métricas. No añadas endpoints, logs ni código que suban imágenes o CSI crudo.
3. **Python del PC:** el `python` del PATH está ROTO (3.13). Usa SIEMPRE `C:\Python315\python.exe`
   (3.15.2). El venv `server\.venv` se crea con ese intérprete; requirements con wheel cp315 o build
   local (patrón heredado: bleak, pandas, scikit-learn, paho-mqtt, fastapi, uvicorn, matplotlib,
   requests, python-docx, python-pptx, pytest). Sin fallback a otro python.
4. **Sin Docker.** Todo corre nativo en Windows 11. (El CLI de ESPectre ofrece Docker como fallback de
   build; **NO se usa** — ruta primaria `eim run idf.py`.)
5. **ESP-IDF v5.5.6** (NO v5.4): el SDK ESPectre requiere ≥5.5.3 y docs/CLI.md recomienda 5.5.6.
   Desviación documentada de la regla E5 original (v5.4 queda instalado para el archive). Comando
   canónico: `eim run "idf.py …" v5.5.6`. **NO usar** `. $env:IDF_PATH\export.ps1` (exige python sano en
   PATH, que está roto).
6. **PROHIBIDO USB device stack** en el firmware (CONFIG_USB_* / tinyusb / UAC): re-muxea el PHY 19/20
   y apaga el JTAG por el que se reflashea = riesgo de brick (lección F18-A de DriveGuard). En S3 la
   consola de ESPectre es UART (TinyUSB CDC solo S2) — no activar ninguna opción USB-device. Verificar
   sdkconfig ANTES de cada flash.
7. **Secretos:** jamás escribas contraseñas reales en el repo. WiFi/MQTT van en `.env` (copiado de
   `.env.example`, gitignored) y en `sdkconfig.defaults.local` (gitignored, generado por
   `tools/gen_firmware_config.py`) o en la NVS de la placa (Improv Serial). La clave WiFi del CLI de
   ESPectre va por `ESPECTRE_WIFI_PASSWORD` (env var), **NUNCA por argv ni impresa**. Si un componente
   necesita una clave nueva, genérala con `secrets.token_hex(16)` en código. `tools/config_mqtt.py` lee
   `.env` y nunca imprime la clave.
8. **Temporales:** siempre en `.tmp/` de este proyecto o en `iot2\.tmp\`. Nunca en %TEMP%/AppData.
9. **Idioma:** documentación, comentarios de código y mensajes de commit en español; identificadores,
   variables y comandos en inglés.
10. **Datos honestos:** lo sintético se etiqueta `origen=sintetico` / `SIMULADO` en docs y dashboard.
    Nunca se mezcla con captura real ni se presenta como real. El export `analysis/dataset/consolidado.csv`
    es SOLO `origen=real`. Las métricas vendor de ESPectre (F1 98,1–99,3 %) son de SU dataset: las
    propias se miden en F1c/F4 y se reportan con tamaño de muestra. **No prometer accuracy que dependa
    de un dataset propio largo** (lección E10 de DriveGuard).
11. **Framing honesto del sensing:** ESPectre detecta **MOVIMIENTO**, no presencia estacionaria, no
    prueba sala vacía, no identifica ni cuenta personas. `idle` ≠ sala vacía. Docs/dashboard usan
    "movimiento/ocupación dinámica", nunca "presencia" a secas. La die temp es proxy de carga de
    cómputo, NO temperatura ambiente.
12. **GPLv3:** el vendor ESPectre es GPLv3. Preservar su LICENSE. El derivado
    (`firmware/parche_aulasense/`) se trackea y se publica (fuente disponible al profesor). El clon
    vendor (`firmware/espectre/`) va gitignored (reproducible por tag v3.2.0). No relicenciar el vendor.
13. **Qwen API (opcional):** solo capa opcional del dashboard/análisis. El sistema completo debe
    funcionar con `DASHSCOPE_API_KEY` vacío.

## Seguridad de la demo (modelo de amenaza — no regresar estos controles, heredados E7)
- **Dashboard SOLO en `127.0.0.1`** (nunca `0.0.0.0`). Guard de Origin para `/ws` y `/internal/evento`
  (403 al origen cruzado). Ampliable SOLO por `AULASENSE_ORIGINS_EXTRA` explícita (host[:puerto] exactos,
  sin wildcards, opt-in); los defaults `127.0.0.1`/`localhost` no se tocan.
- **Cero endpoints de imágenes**: ningún endpoint acepta/sirve fotos, frames ni CSI crudo (no existen
  imágenes en el sistema). `POST /internal/evento` solo JSON de evento validado.
- **MQTT con auth** (usuario `electiva`, clave en `.env`): el broker es compartido → `topic_prefix=
  aulasense/v1/devices` aísla nuestros topics. La ingesta valida el esquema; los paquetes no llevan
  datos personales identificables.
- **Direct HTTP del nodo (:62587)** es para LAN confiable: el firmware acepta solo orígenes listados,
  exige preflight Private Network Access, limita cuerpo/colas/clientes/rate, nunca devuelve claves.
  Nuestras tools envían `Origin: https://test.espectre.dev` (permitido por defecto, igual que el CLI).
- **XSS**: todo dato dinámico del dashboard se interpola escapado (`esc()`/`textContent`).
- **SQL**: siempre parametrizado; nunca concatenes valores del payload. Límites en `limite`/`device_id`.
- **BLE sin pairing** (si se activa el módulo opcional) es intencional: alcance corto, solo flags.
  Documentar el riesgo; el gate `seguridad` lo evalúa. BLE default n (coexistencia puede degradar CSI).

## GPIO (corregido — fuente: analista de viabilidad 2026-11-12, bbhpy/profharris)
- **UART0 real = 43/44** (la SPEC vieja de DriveGuard decía "41–44", MAL). **41/42 LIBRES**.
- **Buzzer = GPIO14**, **WS2812 = GPIO48**, LED flash = GPIO3 (no usado).
- **PROHIBIDO para periféricos**: 19/20 (USB nativo), 43/44 (UART0), 0/45/46 (strapping), 4–18 (cámara,
  nunca inicializada), 26–37 (flash/PSRAM). 22–25 no existen; 33–34 missing.
- Pool libre: 1, 2 (QWIIC, libres si no se usa el conector), 21, 41, 42, 47 (+38/39/40 sin SD).
- El feedback local (buzzer 14 + WS2812 48) no necesita GPIO libre extra. `app_feedback.c` lleva
  guardas `#error` si se configura un pin prohibido.

## Estructura
```
aulasense/
├─ PLAN.md SPEC.md README.md AGENTS.md .gitignore .env.example
├─ firmware/
│   ├─ espectre/            # vendor ESPectre v3.2.0 (GITIGNORED, clon por tag, GPLv3)
│   └─ parche_aulasense/    # derivado propio: app_feedback.c/.h, Kconfig.projbuild, CMakeLists.txt
├─ server/
│   ├─ ingesta/             # ingesta_mqtt.py + esquema.py (paho-mqtt → SQLite WAL + CSV)
│   ├─ dashboard/           # app.py + static/ (FastAPI + WS + Chart.js, 127.0.0.1)
│   ├─ ble/                 # receptor_ble.py (opcional, bleak)
│   ├─ fuentes/             # openmeteo.py (gratis sin key)
│   ├─ datos/               # aulasense.db + exports (gitignored salvo dataset consolidado)
│   └─ tests/               # pytest
├─ analysis/                # eda.py, modelo_ocupacion.py, run_analisis.ps1 · dataset/consolidado.csv
├─ tools/                   # start_demo/stop_demo.ps1, gen_firmware_config.py, config_mqtt.py, lee_serial.py
│   └─ generadores/         # gen_informe.py / gen_presentacion.py (docs/INFORME.docx, PRESENTACION.pptx)
├─ docs/                    # README.md (índice), PROPUESTA.md, guion_demo.md, fuentes.md, Word/PPTX
├─ assets/                  # figuras EDA/modelo, diagramas
└─ .tmp/                    # temporales (gitignored)
```

## Comandos frecuentes (Windows PowerShell)
```powershell
# Firmware (ESP-IDF 5.5.6 vía eim; NO python del PATH):
cd firmware/espectre/src/cpp/frontend/native/app
eim run "idf.py set-target esp32s3" v5.5.6; eim run "idf.py build" v5.5.6; eim run "idf.py -p COM6 flash monitor" v5.5.6
# Fallback CLI (si el app lo requiere): .\espectre.cmd native build --chip s3 --backend local  (IDF_PATH de eim)
# NO usar . $env:IDF_PATH\export.ps1 (python roto).

# Server (SIEMPRE venv py3.15):
server\.venv\Scripts\python.exe -m pip install -r server/requirements.txt
server\.venv\Scripts\python.exe server/ingesta/ingesta_mqtt.py
server\.venv\Scripts\python.exe server/dashboard/app.py            # http://127.0.0.1:8000
server\.venv\Scripts\python.exe server/fuentes/openmeteo.py

# Config del nodo (lee .env, nunca imprime claves):
C:\Python315\python.exe tools/gen_firmware_config.py    # WiFi → sdkconfig.defaults.local (gitignored)
C:\Python315\python.exe tools/config_mqtt.py            # PATCH /mqtt al broker

# Demo / análisis:
powershell tools/start_demo.ps1 ; powershell tools/stop_demo.ps1
powershell analysis/run_analisis.ps1 --origen real --validacion docs/validacion_sesion1.csv
```

## Estilo
- Firmware C/C++: snake_case, funciones ≤60 líneas, un módulo por responsabilidad. Logs `ESP_LOGI/W/E`
  con tag corto. El parche `app_feedback` no bloquea el detector (tarea de prioridad baja).
- Python: type hints, funciones puras donde se pueda, `paho-mqtt` callback → cola → writer único a
  SQLite. SQL parametrizado. `esc()` en el dashboard.
- **Commits: atómicos, mensaje en español imperativo ("Añade feedback de buzzer en app_feedback"),
  los hace SOLO el `@1-orquestador` con sus manos propias de git** (roster vigente: el ejecutor NO
  commitea; corrección de la regla vieja de DriveGuard que lo encargaba al ejecutor).
- Todo entregable nuevo se registra en `../.tmp/checklist.md`; **solo el orquestador marca `[x]`**
  (verifica en disco); los workers reportan con evidencia `archivo:linea`, nadie más edita el checklist.
- Notificaciones de subagentes pueden perderse (lección E8): el entregable verificable en DISCO con
  criterio objetivo es la fuente de verdad, no el reporte del worker.