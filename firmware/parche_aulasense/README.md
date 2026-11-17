# parche_aulasense/ — Módulos del derivado AulaSense (`app_feedback`, `app_imagen`) sobre ESPectre Native

> **Estado (2026-11-14):** `app_feedback` (F3) y `app_imagen` (F24-c) están IMPLEMENTADOS, compilados
> y flasheados en la placa (COM6). Este documento empezó como spec del planificador (2026-11-12) y se
> mantiene como contrato + bitácora de lo verificado en disco.
> **Actualización (2026-11-17):** `app_provision` (portal FIJO sin token F26) y `app_ota` (OTA por
> MQTT + SHA-256 + rollback) también están implementados y **en la build viva f26.6** — OTA y rollback
> demostrados por aire (g1/g2); gate OTA enmendado 96/48 y gate de captura vivo 95/24 sin latch (§14).
> Espejo parche↔`main/` diff cero 10/10 (regla 17 AGENTS). Operación: `../../docs/NOTES_operacion.md`.
>
> **F24 (2026-11-12):** este directorio se AMPLÍA con módulos nuevos — `app_imagen.c/.h` (F24-c,
> implementado; ver §9), `app_provision.c/.h` (F24-d), `app_ia_imagen.c/.h` (F24-e, opcional
> spike-gated **— no creado, ver veredicto abajo**) — que comparten `Kconfig.projbuild`/`CMakeLists.txt`/este README. Contratos:
> `../../SPEC.md` §3.2–§3.4; decisiones D-F24-1..8: `../../PLAN.md` §11. F24-c/d/e añaden aquí su
> sección de spec (NO reabren F3).
>
> **VEREDICTO F24-e (`app_ia_imagen.c/.h`, 2026-11-15):** **NO LANZADA / archivada documentada** —
> el spike e0 se re-encuadró a backend (WSL) con veredicto **GO-COSTO CONDICIONAL** y calidad QVGA
> pobre (10/327 ≥0,45); decisión del orquestador: **la visión API modo B cubre el objetivo**. Los
> ficheros `app_ia_imagen.c/.h` NUNCA se crearon (no existen en disco ni en `main/`); estado completo
> en `PLAN.md` §12.8 (anotación post-ejecución).
>
> Companion: `../../SPEC.md` §3.1 (contrato), `../../PLAN.md` D5 (decisión), `../../AGENTS.md`
> (reglas duras: GPIO, secretos, edge-first). Fuente del pinout: analista de viabilidad 2026-11-12
> (`bbhpy/docs` GOOUUU_ESP32-S3-CAM.md + `profharris/GOOUUU-Tech-ESP32-S3-CAM-Expansion-Board`).

## 1. Qué es y por qué existe

Módulo de feedback local (D5), primer código de firmware propio del proyecto (F24 añade los módulos de
imagen/provisioning/IA-opcional). Se añade al app **ESPectre Native 3.2.0** (vendor
GPLv3, clon gitignored en `../espectre/`) para cubrir dos condiciones del curso que el vendor no trae:

- **C11 — alerta audible/visual local sin red:** buzzer piezo onboard (GPIO14, LEDC PWM) + LED WS2812
  onboard (GPIO48). El feedback funciona aunque el broker MQTT esté caído (la demo no depende de la red
  para el estímulo→respuesta local).
- **C6 — segunda fuente de datos con $0:** temperatura interna del die del S3 (`temperature_sensor`
  driver), proxy de carga de cómputo del edge (**NO temperatura ambiente**, S11 — honestidad obligatoria
  en docs/dashboard).

Más un sub-hito **opcional**: BLE NimBLE notify (default **n**, gate de coexistencia R7).

**No modifica el detector ni el contrato MQTT nativo.** Es aditivo: se engancha a las transiciones de
estado que Native ya publica y añade un topic propio (`aulasense_temp`). El CSI crudo nunca sale del
chip (D9): este módulo solo consume el estado `idle|motion` ya decidido en el nodo.

## 2. Archivos del parche (los crea F3)

| Archivo | Responsabilidad |
|---|---|
| `app_feedback.h` | API pública: `app_feedback_init()`, `app_feedback_on_state(state, score)`, `app_feedback_start_temp()`, guardas de pines |
| `app_feedback.c` | Implementación: tarea de prioridad baja (no compite con el detector), LEDC buzzer, RMT/led_strip WS2812, die temp periódico, BLE opcional |
| `Kconfig.projbuild` | Opciones del menú "AulaSense Feedback" (tabla §4) |
| `CMakeLists.txt` | Registro de fuentes/dependencias del módulo para el app Native |
| `README.md` | Este documento (spec + instrucciones de aplicación) |

## 3. Comportamiento especificado

### 3.1 Buzzer (GPIO14, LEDC PWM)
- Beep corto **880 Hz, ~110 ms** en cada **onset `idle→motion`** (transición, no por evaluación).
- **Cooldown 5 s** (`AULASENSE_BUZZER_COOLDOWN_S`) para evitar spam en movimiento continuo.
- Canal LEDC de baja resolución (p.ej. 8 bit), timer propio; no comparte canal con ningún otro periférico.
- Confirmación de ejecución: log `ESP_LOGI(TAG, "beep onset …")` + audible por el operador (criterio F3).

### 3.2 WS2812 (GPIO48, LED onboard)
- Color/intensidad por estado: **`idle` = azul tenue**, **`motion` = azul brillante o verde** (el LED
  onboard es azul; usar intensidad/canal del strip). Un solo supervisor del pin (tarea del módulo).
- Actualización en la transición de estado, no por evaluación (evita tráfico RMT innecesario).
- Confirmación: log de cambio de color + visible por el operador (criterio F3).

### 3.3 Die temp (sensor interno del S3)
- Driver `temperature_sensor` del ESP32-S3 (rango típico del die; **no es temperatura ambiente**).
- Publica cada **30 s** en el topic `{topic_prefix}/{device_id}/aulasense_temp` con payload
  `{"timestamp_ms": <monótono del nodo>, "die_temp_c": <float>}`, **sobre el MISMO cliente MQTT de
  Native** (aditivo; no abre otra conexión ni rompe el contrato nativo).
- La ingesta (F2) ya suscribe ese topic y lo persiste en `telemetria.die_temp_c` (SPEC §6-§8).
- Si MQTT no está configurado/conectado: el módulo sigue funcionando (buzzer/LED) y omite la publicación
  (degradación elegante, sin pánico).

### 3.4 BLE NimBLE notify (OPCIONAL, default n)
- Servicio GATT con una característica de estado (estilo `0xA111`): paquete binario corto
  (state + score cuantizado) notificado en onset de motion. Sin pairing (alcance corto, solo flags —
  riesgo documentado, lo evalúa el gate `seguridad`).
- **Gate de coexistencia (R7):** activar SOLO si `csi_accepted_pps` no cae respecto al baseline de F1c
  (comparar `.tmp/f3_metricas.json` vs `.tmp/f1c_diagnostics.json`). WiFi+BLE comparten radio en el S3;
  si el capture se degrada, BLE queda en n y se documenta.
- Receptor PC opcional: `server/ble/receptor_ble.py` (bleak), también F3.

## 4. Kconfig (menú "AulaSense Feedback")

| Opción | Tipo | Default | Notas |
|---|---|---|---|
| `AULASENSE_FEEDBACK` | bool | y | Habilita el módulo completo |
| `AULASENSE_BUZZER_GPIO` | int | 14 | Guardia `#error` si pin prohibido |
| `AULASENSE_WS2812_GPIO` | int | 48 | Guardia `#error` si pin prohibido |
| `AULASENSE_DIE_TEMP` | bool | y | Publicación periódica de die temp |
| `AULASENSE_BLE` | bool | **n** | Gate de coexistencia R7 antes de activar |
| `AULASENSE_BUZZER_COOLDOWN_S` | int | 5 | Anti-spam de beeps |

## 5. Guardas de compilación (obligatorias, AGENTS §GPIO)

`#error` en tiempo de compilación si se configura un pin prohibido:
- **19/20** — USB nativo D-/D+ (USB device stack = riesgo de brick, lección F18-A de DriveGuard).
- **43/44** — UART0 real (consola/flasheo; la SPEC vieja de DriveGuard decía "41–44", corregido).
- **4–18** — bus DVP de la cámara: usados por OV3660 SOLO bajo `AULASENSE_IMAGEN=y` (F24-c, evidencia
  de evento); NINGÚN otro periférico puede tocar sus pines (la regla v23 "nunca inicializada" quedó
  revocada por D-F24-1).
- **0/45/46** — strapping (BOOT/DC/Shutter).
- **26–37** — QSPI flash interna / PSRAM octal.
- 22–25 no existen en el S3; 33–34 missing en este módulo.

El feedback usa **solo periféricos onboard** (buzzer 14 + WS2812 48): **no consume ningún GPIO libre
extra**. Pool libre verificado por si se necesita expansión: 1, 2 (QWIIC, libres si no se usa el
conector), 21, 41, 42, 47 (+38/39/40 sin SD).

## 6. Instrucciones de aplicación al vendor (las ejecuta F3, `@5-dev`)

1. Vendor presente: `../espectre/` clonado en F1a (`git clone --branch 3.2.0 --depth 1
   https://github.com/francescopace/espectre.git` — **tag SIN prefijo v**; `v3.2.0` da 404, verificado
   F1a). **No se trackea el clon** (gitignored, reproducible por tag). LICENSE GPLv3 preservado intacto.
2. Copiar los archivos del parche al app Native: `../espectre/src/cpp/frontend/native/app/main/`
   (o el directorio de componentes que el app use — verificar estructura real en F3 antes de copiar).
3. Registrar `app_feedback.c` en las fuentes del `CMakeLists.txt` del app (SRCS) y añadir las
   dependencias de componente (`driver` para LEDC/RMT/temperature_sensor, `bt`/NimBLE solo si
   `AULASENSE_BLE=y`). El `Kconfig.projbuild` del parche se añade al menú del app.
4. Enganche en el flujo de Native (verificar puntos exactos en el código vendor durante F3):
   - `app_feedback_init()` después de que el sensing y el cliente MQTT estén inicializados.
   - `app_feedback_on_state(state, score)` en el punto donde Native publica el evento `motion`
     (misma transición que va al topic MQTT — no duplicar la lógica del detector).
   - La tarea de die temp arranca con `app_feedback_start_temp()` y usa el cliente MQTT existente.
5. **Verificar sdkconfig ANTES de flashear** (regla dura E5/AGENTS): sin USB device stack
   (CONFIG_USB_*/tinyusb), sin driver camera (**F3 no añade cámara**; F24-c la habilita SOLO bajo
   `AULASENSE_IMAGEN=y` con SPIRAM=y octal y actualiza este precheck), PSRAM octal 8 MB, flash 16 MB,
   `AULASENSE_BLE=n` por default. Consola USB-Serial-JTAG en S3 (COM6; no activar TinyUSB).
6. Rebuild + flash: `eim run "idf.py build" v5.5.6` + `eim run "idf.py -p COM6 flash monitor" v5.5.6`
   (COM autodetectado; recuperación BOOT 3 s si hace falta).
7. Criterios de aceptación (F3, verificables en disco/operador):
   - Beep audible en onset de motion (operador o log LEDC) + cooldown respetado.
   - WS2812 cambia de color idle↔motion (log + visible).
   - Filas `die_temp_c` en `telemetria` de `server/datos/aulasense.db` (≥1 por minuto con MQTT activo).
   - Evento motion del nodo → dashboard <2 s (latencia medida serial↔WS, `.tmp/f3_e2e.md`).
   - Sub-hito BLE solo si el gate de coexistencia pasa (`.tmp/f3_metricas.json`).

## 7. Licencia (GPLv3, S9/R5)

El vendor ESPectre es **GPLv3** (© Francesco Pace y contribuidores). Este parche es un **derivado** y se
publica bajo GPLv3: el fuente se trackea en este directorio y se entrega al profesor (fuente disponible).
El clon vendor va gitignored pero es reproducible por tag `3.2.0` (SIN prefijo v) + las instrucciones de
§6. **No relicenciar el vendor ni eliminar su LICENSE.** Uso académico del curso IoT2/ITSE.

## 8. Límites del módulo (no hacer)

- NO modificar el detector, los pesos ni el pipeline CSI (edge-first: la IA es del vendor, medida en C12).
- NO inicializar la cámara ni el micrófono DESDE ESTE MÓDULO (no hay mic). La cámara OV3660 la usa
  `app_imagen` en F24-c bajo `AULASENSE_IMAGEN=y` (evidencia de evento local, D-F24-1/2 — la regla v23
  "la cámara NUNCA se usa, D9/E9" quedó revocada por orden del dueño).
- NO activar USB device stack ni TinyUSB en S3 (riesgo brick E5; consola USB-Serial-JTAG).
- NO escribir claves en el código: WiFi por NVS/Improv o `sdkconfig.defaults.local` generado desde
  `.env` (gitignored); MQTT por `PATCH /mqtt` desde `tools/config_mqtt.py` (lee `.env`, nunca imprime).
- NO publicar CSI crudo ni imágenes por ningún canal (solo estado + métricas + die temp).
- NO bloquear el detector: la tarea de feedback es de prioridad baja; el beep/LED/temp nunca deben
  retrasar la evaluación de 250 ms (verificar `detection_avg_us` en F3 vs F1c).

## 9. F24-c · `app_imagen` (evidencia de imagen en el onset)

**Qué hace.** En la transición `idle→motion` captura **UN** JPEG QVGA 320×240 (quality 12) de la
OV3660 y lo sube por HTTP al receptor local de la ingesta. La foto es **evidencia** del evento: el
evento MQTT `motion` sigue siendo la fuente de verdad y **nunca** se espera por la imagen.

| Aspecto | Valor |
|---|---|
| Hook | `app_main.cpp` — `espectre_loop_task`, junto a `app_feedback_on_state` (mismo snapshot `ready_to_publish`) |
| Tarea propia | `aulasense_img`, prioridad **3** (loop del detector = 5, NUNCA se usa), stack **8192** |
| Cooldown | `AULASENSE_IMAGEN_COOLDOWN_S` = **15 s** (2 onsets < 15 s → 1 imagen; el 2.º se loguea y se omite) |
| Cola | `AULASENSE_IMAGEN_QUEUE` = **2** slots, almacenamiento en **PSRAM** (`xQueueCreateStatic` + `heap_caps_malloc(MALLOC_CAP_SPIRAM)`) |
| Buffers JPEG | `QUEUE` × **8192 B** en PSRAM (16 KB con el default). **Tamaño real medido:** 3270 B (3,2 KB) por JPEG QVGA q12 |
| Reintentos upload | 1 intento + **3 reintentos** con backoff **2/4/8 s**; un **4xx** es definitivo (no se reintenta) |
| Gate de memoria | heap **interno** libre < **120 KB** o mayor bloque < **64 KB** → captura OFF + `ESP_LOGW` + evento `IMAGEN_GATE_OFF`; re-habilitación con **histéresis +20 KB** |
| Si la cola se llena | se DESCARTA la imagen, `ESP_LOGW` + evento `IMAGEN_DESCARTE`; **el evento motion sigue** |

> **Nota F26 (2026-11-17, contrato vigente §14 + SPEC §16.3):** los umbrales 120/64 de esta tabla eran los de F24-c; el gate de captura **VIVO** en la build actual es **95/24 KB internos, SIN latch** (re-evalúa en cada onset; cola de onsets drop-oldest) y el gate OTA es **96/48 post-quiesce** (enmendados con evidencia desde 120/64 — §14.2/§14.8). Métrica útil: `internal_free_kb`/`internal_largest_block_kb`, nunca `free_memory_kb` (≈8,2 MB = PSRAM).

**Pines (bus DVP 4–18, exclusivo de la cámara bajo `AULASENSE_IMAGEN=y`)**

| Señal | GPIO | | Señal | GPIO |
|---|---|---|---|---|
| XCLK | 15 | | Y2 (D0) | 11 |
| SIOD (SDA) | 4 | | Y3 (D1) | 9 |
| SIOC (SCL) | 5 | | Y4 (D2) | 8 |
| VSYNC | 6 | | Y5 (D3) | 10 |
| HREF | 7 | | Y6 (D4) | 12 |
| PCLK | 13 | | Y7 (D5) | 18 |
| PWDN / RESET | −1 / −1 | | Y8 (D6) | 17 |
| LED flash | **3 → NO se usa** | | Y9 (D7) | 16 |

`app_imagen.c` lleva guardas `#error` que verifican que **cada** pin cae en 4..18 (ese rango excluye
por construcción los prohibidos siempre: 19/20 USB, 43/44 UART0, 0/45/46 strapping, 26–37
flash/PSRAM). El buzzer onboard sigue en GPIO14 (excepción documentada del bus).

**Kconfig (menú nuevo "AulaSense Imagen (F24-c)" en `Kconfig.projbuild`)**

| Opción | Tipo | Default |
|---|---|---|
| `AULASENSE_IMAGEN` | bool | **y** |
| `AULASENSE_IMAGEN_COOLDOWN_S` | int | 15 |
| `AULASENSE_IMAGEN_QUEUE` | int | 2 |
| `AULASENSE_IMAGEN_HEAP_MIN_KB` | int | 120 |
| `AULASENSE_IMAGEN_HEAP_BLOCK_MIN_KB` | int | 64 |
| `AULASENSE_UPLOAD_HOST` | string | "" (vacío = upload desactivado) |
| `AULASENSE_UPLOAD_PORT` | int | 8001 |
| `AULASENSE_UPLOAD_TOKEN` | string | "" (**nunca se imprime**) |

Los tres `AULASENSE_UPLOAD_*` los emite `tools/gen_firmware_config.py` desde `.env`
(`AULASENSE_UPLOAD_HOST/PORT/TOKEN`), que NO regenera el token si ya existe (`len=32`).
`app_imagen.c` exige `CONFIG_SPIRAM=y` con `#error` (ver §10).

**Contrato de upload (SPEC §7.1, receptor `server/ingesta/receptor_imagen.py`)**

```
POST http://<AULASENSE_UPLOAD_HOST>:<PORT>/internal/imagen
Authorization: Bearer <AULASENSE_UPLOAD_TOKEN>
Content-Type: application/octet-stream      (cuerpo = JPEG binario crudo FFD8FF)
X-Device-Id: <16 hex del nodo>              (2e6f0c909e30673e)
X-Ts-Ms: <reloj monótono del nodo en ms>    (la ingesta lo mapea a época, R3)
```
`X-Ia-*` se **omiten** (la IA de visión es F24-e, opcional). Respuesta esperada **201**.
**Norma vigente post-F25 (D-F25-1, revoca "ninguna imagen por MQTT" de F24):** el canal **PRIMARIO**
de la foto-evidencia es el topic `image` del MQTT **persistente** del nodo (norma **N1**: UNA sola
conexión por rol, prohibido abrir otra por imagen); este endpoint HTTP :8001 queda como **FALLBACK**
(norma **N2**, AGENTS regla 2/14, SPEC §3.2.1/§6.2 y §12 abajo). Nunca a la nube.

**Eventos `aulasense_config`** (topic aditivo sobre el mismo cliente MQTT de Native, publicados desde
la tarea del loop con `app_imagen_publish_pending()`): `IMAGEN_ON`, `IMAGEN_DESCARTE`,
`IMAGEN_GATE_OFF`, con `{"timestamp_ms":…,"evento":"…","detalle":"…"}`.

> ✅ **Desajuste de contrato RESUELVIDO (2026-11-15, R8):** el campo es **`"evento"`** (el validador
> lo exige, `server/ingesta/esquema.py:306-327`) y el enum `EVENTOS_CONFIG` **ya incluye `IMAGEN_ON`**
> (`esquema.py:76-83`). La decisión pendiente de aquel entonces —"añadir `IMAGEN_ON` al enum del
> servidor o dejar de publicarlo"— se cerró por la primera opción: se añadió, y la migración **R8**
> reconstruye `config_eventos` al arrancar la ingesta conservando filas/ids (idempotente; validado
> sobre copia de BD: 7 filas, `esquema.py:740-779`; SPEC §8.1 con nota). `IMAGEN_ON` se publica y se
> persiste; ya no queda solo en serial.

## 10. SPIRAM: líneas exactas aplicadas al `sdkconfig` del app

`CONFIG_SPIRAM` estaba **OFF** en el vendor (`app/sdkconfig`: `# CONFIG_SPIRAM is not set`). F24-c la
habilita porque los framebuffers de la cámara, la cola de onsets y los buffers JPEG viven en PSRAM
(N16R8 = 8 MB octal). **`CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP` NO se activa** (ENMIENDA E9: mover
WiFi/LWIP a PSRAM altera la temporización del path CSI; se mediría antes, y no hizo falta).

Bloque aplicado a `firmware/espectre/src/cpp/frontend/native/app/sdkconfig` (2026-11-14):

```
CONFIG_SPIRAM=y
CONFIG_SPIRAM_MODE_OCT=y
CONFIG_SPIRAM_TYPE_AUTO=y
CONFIG_SPIRAM_SPEED_80M=y
CONFIG_SPIRAM_BOOT_INIT=y
CONFIG_SPIRAM_USE_MALLOC=y
CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384
CONFIG_SPIRAM_MALLOC_RESERVE_INTERNAL=0
# CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP is not set
CONFIG_CAMERA_PSRAM_DMA=y
```

**Por qué `RESERVE_INTERNAL=0` y `CAMERA_PSRAM_DMA=y` (medido, no gusto):** con los defaults
(`RESERVE_INTERNAL=32768`, PSRAM DMA off) el heap interno quedaba en **110 KB libres / 31 KB en el
mayor bloque** y el gate de memoria (120/64) **cerraba la captura** (`IMAGEN_GATE_OFF` en el primer
onset, 2026-11-14 17:28). Con estas dos líneas (y `TRY_ALLOCATE_WIFI_LWIP` en **n**) el módulo mide
tras init **`free=141 KB largest=100 KB`** (log `aulasense.imagen: heap interno tras init`): el gate
queda abierto con margen y el perfil es comparable a F3 sin cámara (158/108).

**Trampa detectada (2026-11-14):** un `sdkconfig` puede acabar con **dos** líneas de la misma clave
(p. ej. `# CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP is not set` y, más abajo,
`CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP=y`). kconfiglib se queda con la **última** y el efecto real
(E9 violado) no se ve en un grep superficial. El pre-check
(`iot2\.tmp\f24c_precheck.ps1`) lo detecta (`SPIRAM_TRY_ALLOCATE_WIFI_LWIP: FAIL`); el script de
aplicación elimina duplicados antes de escribir. Con `=y` el transitorio de arranque empeoró
(76 avisos `ping send failed`, MQTT a los 82 s) frente a `=n` (31 avisos, MQTT a los 37 s):
coherente con E9, por eso se queda en `n`.

**Cómo reproducirlas tras un factory reconfigure o un clon limpio**

1. `idf.py fullclean` **no** borra `sdkconfig`; este bloque sobrevive. Si se borra `sdkconfig` o se
   recrea el clon del vendor, hay que re-aplicarlo:
   ```powershell
   # 1) valores del .env -> sdkconfig.defaults.local (nunca imprime el token)
   aulasense\server\.venv\Scripts\python.exe aulasense\tools\gen_firmware_config.py `
       --out iot2\.tmp\sdkconfig.defaults.local
   # 2) aplica SPIRAM + CAMARA + AULASENSE_* al sdkconfig del app (script one-shot de la fase)
   C:\Python315\python.exe iot2\.tmp\f24c_apply_sdkconfig.py
   # 3) re-inyecta el WiFi NATIVO del vendor si hiciera falta (F1)
   C:\Python315\python.exe iot2\.tmp\inject_wifi_f1.py
   ```
2. Alternativa manual: pegar el bloque de arriba al final de `app/sdkconfig` y las tres líneas
   `CONFIG_AULASENSE_UPLOAD_*` (valores del `.env`, sin comillas raras) — el build normaliza el
   fichero (kconfiglib) sin perder los valores.
3. Verificación antes de flashear: `powershell -File iot2\.tmp\f24c_precheck.ps1`
   (USB device stack ausente, SPIRAM octal 80 MHz, TRY_ALLOCATE ausente, `AULASENSE_BLE` n, WiFi
   nativo no vacío, tamaño de bin vs partición 0x1E0000).

## 11. Pruebas realizadas (F24-c, 2026-11-14)

| Prueba | Resultado | Evidencia |
|---|---|---|
| Build IDF 5.5.6 | PASS — bin **1,395,760 B** (1.33 MB), libre **570,320 B (29 %)** de 0x1E0000 | `iot2\.tmp\f24c_build_e9.log` |
| Captura QVGA JPEG (prueba única al arrancar, no se sube) | PASS — **3277 B (3,2 KB), 320×240, fmt=4 (PIXFORMAT_JPEG)** | `iot2\.tmp\f24c_smoke.log` |
| Pre-check pre-flash | PASS (sin USB device; SPIRAM octal 80M; TRY_ALLOCATE ausente; BLE n) | `iot2\.tmp\f24c_precheck.log` |
| Flash COM6 (1 solo esptool) | PASS — 4 regiones con `Hash of data verified` | `iot2\.tmp\f24c_flash.log` |
| Smoke 90 s | PASS — sin Guru/panic; `feedback listo`, `imagen init ok: camera=OV3660 (PID=0x3660)`, `imagen listo`, CSI armed, `sta ip 192.168.40.188`, MQTT connected | `iot2\.tmp\f24c_smoke.log` |
| Gate de memoria | Observado **cerrando** con el sdkconfig inicial (110/31 KB → `IMAGEN_GATE_OFF`) y **abierto** con la config final (141/100 KB) | `iot2\.tmp\f24c_diag.log`, `f24c_smoke.log` |
| No degradación | `csi_accepted_pps` 99.2–100.6 (gate ≥90 PASS); `detection_avg_us` 832–864 µs (vs F3 843 → +0.7 %) | `iot2\.tmp\f24c_metricas.json` |

**Observación honesta (no ocultada):** durante los primeros ~20–40 s tras un reboot el generador de
tráfico registra `ping send failed (errno=12)` y las pps caen a 25–50 hasta que MQTT conecta; después
se estabiliza en ~100 pps. No aparece en el binario de F3 (0 ocurrencias en `f3_smoke.log`), así que
es un efecto del binario F24-c (SPIRAM+cámara) que **se auto-resuelve**; queda como materia del gate
F24-e0 (medir con el resto del sistema ya arrancado). No se activó `SPIRAM_TRY_ALLOCATE_WIFI_LWIP`.

**Criterio E2E con movimiento real:** requiere un operador caminando cerca del nodo (el detector solo
mide movimiento, no presencia). Ver el reporte de la fase para el estado medido.
### 10.1 Enmienda de exactitud (ejecutor, 2026-11-14 18:4x â€” config FINAL realmente compilada)

El `sdkconfig` con el que se construyo **build6/flash 18:05-18:06** (mtime 17:40:56) contenia el bloque
de 10 **MAS** `CONFIG_SPIRAM_TRY_ALLOCATE_WIFI_LWIP=y` (decision E9 del ejecutor 17:38, `f24c_e9.ps1`,
con medicion posterior). Es decir, el texto de 10/11 dice "TRY not set / no hizo falta" pero el valor
EFECTIVO del binario en placa es **=y** (verificado por Select-String sobre `app/sdkconfig` a las 18:3x).
Queda corregido por esta nota: se activo y **SI se midio**, como exige E9 â€” serie 3x60 s post-E9
(`f24c_metricas_cierre.py` -> `f24c_metricas.json.e9_medicion`): `csi_accepted_pps` p50 99.60 (+0.55 %
vs F3-equivalente 99.06), `detection_avg_us` p50 872 us (+0.91 % vs 864.1); gates pps>=90 y avg<=+20 %
**PASS**. Heap interno medido en placa con la config combinada: free=149 KB / largest=108 KB
(`f24c_diag2.log` 18:06:41, `f24c_initpeek.log:319`). No se aiso la contribucion individual de cada
linea (RESERVE=0 vs TRY vs PSRAM_DMA) â€” no hace falta: el estado final esta medido y es el entregado.
Tras un factory reconfigure, re-aplicar TODO el bloque de 10 **incluyendo TRY=y** (o volver a ejecutar
`f24c_apply_sdkconfig.py` + `f24c_e9.ps1`).


### 10.2 Correccion a 10.1 (ejecutor, 2026-11-14 ~20:0x â€” tras el REPORTE FINAL del dev F24-c)

10.1 describia el binario de las 18:05-18:06 (TRY=y efectivo por CLAVE DUPLICADA en sdkconfig, que kconfiglib
resolvio en silencio). El dev detecto despues la duplicidad (su flag #2), la corrigio y **re-flasheo el binario
FINAL sin `SPIRAM_TRY_ALLOCATE_WIFI_LWIP`** (verificado en disco: sdkconfig:1353 `is not set`, unico, y su
`f24c_precheck.log` PASS-AUSENTE). Su medicion comparativa: con TRY=y el transitorio de arranque EMPEORO
(76 `ping send failed` y MQTT a 82 s vs 31 y 37 s con n) â€” el fix correcto del gate era el del dev
(`RESERVE_INTERNAL=0` + `CAMERA_PSRAM_DMA=y` â†’ heap interno 141/100, gate abierto), y mi E9-medicion quedo
superada por evidencia. Bin final: pps media 97,31 (min 90,8 PASS justo), avg 850,8 us (+0,9 % vs F3),
criteria PASS x3 (`f24c_metricas.json.resumen/criterios`). Prueba de captura UNICA al arranque (no onset, no
upload): **JPEG real 3277 B 320x240 fmt=4** â€” ruta DVPâ†’JPEG probada; S13=3,2 KB (por debajo del rango SPEC
4-8 KB: QVGA con escena poco detallada; dentro del slot de 8 KB y del limite de 64 KB del receptor).
Estado de F24-c: TODO CUMPLE MENOS el E2E de >=3 onsets reales (0 motion en 18:30-19:37 â€” RF/sensibilidad,
ver protocolo de caminata en reporte del dev/ejecutor). Flag abierto #1 del dev: `IMAGEN_ON` NO esta en
`EVENTOS_CONFIG` de `server/ingesta/esquema.py:76-82` (SI estan GATE_OFF y DESCARTE) â†’ la ingesta rechazara
con log ese evento hasta que se aÃ±ada al enum (edicion de 1 linea, territorio server â€” decision del orquestador).
**Anotación 2026-11-15 (cierre — no reescribe este texto):** el flag quedó CERRADO por la opción "añadir al
enum": `IMAGEN_ON` está en `EVENTOS_CONFIG` (`esquema.py:80`) y la migración R8 reconstruye `config_eventos`
al arrancar la ingesta, idempotente y conservando filas/ids (validado sobre copia: 7 filas; SPEC §8.1 y la
nota RESUELVIDO del §9 de este README).

## 12. F25-c — Imagen por MQTT (D-F25-1)

**Qué cambia (2026-11-14).** La imagen del onset deja de ser solo HTTP: el canal **PRIMARIO** es la
conexión MQTT **persistente** del nodo (norma **N1**: UNA sola conexión por rol; PROHIBIDO abrir una
conexión nueva por imagen) y el upload HTTP :8001 queda como **FALLBACK** (norma **N2**). Contratos:
`SPEC.md` §3.2.1 (extensión del módulo) y §6.2 (envelope del topic `image`); decisión y evidencia del
probe: `iot2/.tmp/f25a_decision_D-F25-1.md`.

### 12.1 Parche vendor (1 línea, obligatorio)

`firmware/espectre/src/cpp/runtime/esp_idf/mqtt_transport_esp_idf.h` (antes línea 90):

```diff
-  static constexpr uint64_t kMqttOutboxLimitBytes = 8192U;
+  /* AulaSense F25-c (D-F25-1): 8192 -> 32768. El envelope de imagen (SPEC §6.2, b64 <= 16 KB)
+   * no cabe en el outbox nativo de 8 KB. El tope NO pre-asigna memoria (limite del outbox). */
+  static constexpr uint64_t kMqttOutboxLimitBytes = 32768U;
```

- Ese valor se publica tal cual en `esp_mqtt_client_config_t.outbox.limit` (`mqtt_transport_esp_idf.cpp:86`)
  y `esp_mqtt_client_enqueue` rechaza (`-2`) cualquier mensaje que no quepa en el presupuesto del outbox
  (`mqtt_client.c`: `if (len + outbox_get_size(outbox) > outbox_limit) return -2`). Con 8192 el envelope
  QVGA (~4.4-11.5 KB de b64+JSON) **siempre** se rechazaba.
- **No pre-asigna memoria**: `outbox.limit` es un tope de bytes en vuelo, no un buffer reservado.
- Verificación viva en el nodo (debe decir `32768`):
  `GET http://192.168.40.188:62587/espectre/v1/diagnostics?fields=["mqtt"]` -> `outbox_capacity_bytes`.

### 12.2 Por qué `publish()` y NO `publish_suffix()`

- `EspIdfMqttTransport::publish(topic_completo, payload, retain=false)` arma el mensaje con
  `replaceable = retain || topic ha/` -> con `retain=false` y topic propio queda **NO-reemplazable**
  (`mqtt_transport_esp_idf.cpp:154-161`). El envío no se difiere: no hay high-water.
- `publish_suffix()` (`:163-171`) marca **reemplazable** todo lo que no sea `commands/result` y
  `drain_publish_queue_()` **difiere** los reemplazables mientras el outbox tenga
  `kReplaceableOutboxHighWaterBytes` (2048 B) en vuelo (`:379-381`). Una foto de ~11 KB podía quedarse
  esperando (o ser sustituida por otra telemetría del mismo topic) — inaceptable para evidencia.
- **Efecto buscado:** `publish()` recibe el **topic absoluto**, así que el módulo compone
  `{topic_prefix}/{device_id}/image` con el `topic_prefix` del config runtime (el mismo que
  `make_topic_base()` usa para el base del vendor) y no depende de sufijos fijos.

### 12.3 Cambios en el parche (F25-c)

| Archivo | Cambio |
|---|---|
| `app_imagen.h` | Firma NUEVA `app_imagen_publish_topic_fn(const char *topic, const char *payload)` + `app_imagen_set_mqtt_image(publish, connected, topic_prefix)`. `app_imagen_set_mqtt_publisher()` (aulasense_config) queda **intacta** |
| `app_imagen.c` | En la tarea dedicada (prio 3), ANTES del path HTTP: base64 estándar sin saltos (`mbedtls_base64_encode`), sha256 hex minúscula del JPEG crudo (`mbedtls_sha256`), envelope estricto §6.2 y publish por el publisher nativo inyectado. Tope `IMAGEN_MQTT_MAX_B64` = **16384** (`#define` duro, NO Kconfig — orden del dispatch F25-c); si se supera -> log `imagen grande b64=%d -> canal http` y fallback. Log de éxito `imagen enviada via mqtt: %u B (b64 %d)`; en fallo `mqtt publish fallo -> fallback http` y se ejecuta el upload HTTP de F24-c **sin cambios** |
| `app_main.cpp` | Registro ADICIONAL `app_imagen_set_mqtt_image(...)` con `mqtt_transport.publish(topic, payload, false)`; el hook de onset (`app_imagen_on_state`) y el bombeo (`app_imagen_publish_pending`) NO se tocan |

Envelope publicado (SPEC §6.2, UTF-8, sin campos extra):

```json
{"v":1,"device_id":"<16 hex>","ts_ms":<monotono ms>,"motivo":"onset","tamano":<bytes JPEG>,
 "sha256":"<64 hex>","b64":"<JPEG base64 estándar SIN saltos>"}
```

- `ts_ms` = reloj monótono del nodo (`esp_timer_get_time()/1000`), el MISMO del evento `motion`; NO época.
- `retain=false` SIEMPRE (la foto no persiste en el broker), QoS 0 fijo del vendor (`:384`) — la imagen
  es accesoria, el evento CSI es la fuente de verdad.
- Sin publisher inyectado (o `AULASENSE_IMAGEN=n`) el módulo se comporta **exactamente** como F24-c.
- `aulasense_config` (IMAGEN_ON/DESCARTE/GATE_OFF) sigue por `publish_suffix()` y no se toca.

### 12.4 `mqtt_transport.publish()` desde la tarea de imagen (seguridad de hilos)

El vendor publica desde más de una tarea: el worker de Direct HTTP (prio 2) ejecuta
`handle_*_write_()` -> `mqtt_frontend_->publish_message()` (`native_frontend.cpp:336`) sobre el mismo
`EspIdfMqttTransport`. La tarea de imagen (prio 3) usa la misma vía: NO introduce un patrón nuevo ni
abre conexión (N1). El bombeo de `aulasense_config` sigue exclusivamente en la tarea del loop.

### 12.5 Reproducir tras un clon limpio del vendor

1. Aplicar la línea de §12.1 en `firmware/espectre/src/cpp/runtime/esp_idf/mqtt_transport_esp_idf.h`.
2. Copiar los módulos canónicos al app Native:
   `Copy-Item firmware/parche_aulasense/app_imagen.c firmware/espectre/src/cpp/frontend/native/app/main/ -Force`
   (ídem `app_imagen.h`, `app_feedback.*`).
3. En `app/main/app_main.cpp`, tras el bloque `app_imagen_set_mqtt_publisher(...)`, registrar:
   ```cpp
   app_imagen_set_mqtt_image(
       [](const char *topic, const char *payload) { return mqtt_transport.publish(topic, payload, false); },
       []() { return mqtt_transport.connected(); }, device_config.topic_prefix.c_str());
   ```
   (antes de `app_imagen_init(device_id.c_str())`).
4. Confirmar que el `CONFIG_ESPECTRE_TOPIC_PREFIX` del sdkconfig coincide con el `MQTT_TOPIC_PREFIX`
   del `.env` que se aplica por `PATCH /mqtt`; el módulo compone el topic con el valor runtime.
5. Verificar el parche vivo tras el flash: `outbox_capacity_bytes == 32768` en `/diagnostics?fields=["mqtt"]`.

### 12.6 Resultados verificados (F25-c, 2026-11-14/15)

| Prueba | Resultado | Evidencia |
|---|---|---|
| Build IDF 5.5.6 | PASS — `Project build complete`; bin **1.397.648 B** (1.33 MB), libre **568.432 B (28,9 %)** de 0x1E0000 | `iot2\.tmp\f25c_build.log` |
| Strings del bin | PASS — `imagen enviada via mqtt`, `mqtt publish fallo -> fallback http`, `imagen grande b64` presentes en `espectre-native.bin` | `iot2\.tmp\f25c_precheck.log` |
| Pre-check sdkconfig | PASS — sin USB device; cámara 2.1.9 presente (`AULASENSE_IMAGEN=y`); SPIRAM octal 80M; `SPIRAM_TRY_ALLOCATE_WIFI_LWIP` **1 línea única 'is not set'** (sin duplicado); UPLOAD presente (longitudes); BLE n | `iot2\.tmp\f25c_precheck.log` |
| Parche vendor | PASS — `kMqttOutboxLimitBytes = 32768U` (L92) | `iot2\.tmp\f25c_precheck.log` |
| Flash COM6 (1 esptool) | PASS — 4 regiones con `Hash of data verified`, `Leaving...` | `iot2\.tmp\f25c_flash.log` |
| Smoke 60 s | PASS — sin Guru/panic/WDT; `imagen init ok: camera=OV3660`; `imagen mqtt listo: topic=aulasense/v1/devices/2e6f0c909e30673e/image retain=false no-reemplazable max_b64=16384`; `imagen listo: camera=ok upload=ok mqtt=ok`; `upload listo` (192.168.40.224:8001); CSI armed; `MQTT connected`; `sta ip 192.168.40.188` | `iot2\.tmp\f25c_smoke.log` |
| Parche vivo en el nodo | PASS — `/diagnostics?fields=["mqtt"]` → `outbox_capacity_bytes: 32768` | `iot2\.tmp\f25c_precheck.log` (bloque POST-FLASH VERIFY) |
| Métricas CSI | `gen ~100 pps`, `accepted 86–102 pps` (una caída puntual a 86 pps coincidiendo con un pico de `rx 130`; media ~99), `mvmt 0.000` IDLE durante el smoke | `iot2\.tmp\f25c_smoke.log` |

**Pendiente para el criterio E2E de SPEC §3.2.1:** ver una fila `imagenes.canal='mqtt'` con `origen='real'`
producida por un onset humano del nodo (durante el smoke no hubo ninguna transición a MOTION:
`mvmt 0.000`, IDLE estable — la sensibilidad NO se toca, es materia de otro frente).

## 13. F24-d — `app_provision` (AP de emergencia + portal privado `/cfg/<token>`)

**Qué hace (SPEC §3.3, decisiones D-F24-4, ENMIENDAS E2/E3/E4/E5/E7/E8).** Módulo aditivo que
NO toca el detector CSI, el canal MQTT existente (solo re-usa el publisher `aulasense_config`
inyectado, mismo patrón F24-c), la mecánica de imágenes ni el recovery nativo (BOOT 3 s →
Improv preservado).

### 13.1 Disparo del AP (E2: contador propio)

El Native nunca "falla de forma permanente" (`standalone_wifi_service.cpp:698-705,831-838`:
`max_retry=8` + back-off 30 s en bucle), así que `app_provision.c` lleva SU contador:
cada `WIFI_EVENT_STA_DISCONNECTED` sin `IP_EVENT_STA_GOT_IP` intermedio suma; al cruzar
`AULASENSE_PROVISION_RETRIES` (default 3, umbral DEL MODOLO) sube el SoftAP. Con GOT_IP el
contador se resetea. Caso "sin credenciales": un esp_timer de gracia (20 s tras el arranque)
consulta `esp_wifi_get_config(WIFI_IF_STA)`; si el vendor no tiene SSID y el NVS del portal
tiene credenciales, se aplica el flujo E3 directamente (AP OFF → STA puro con las credenciales
del portal); si tampoco las hay, sube el AP.

### 13.2 AP y portal

- SoftAP **`AULASENSE-CFG-<4 hex>`** (2 últimos bytes del MAC — E4, adaptación intencional del
  calco de 6 hex del archive), canal 1, `max_connection=4`, modo **AP PURO** (nunca APSTA).
- Auth: **ABIERTO con aviso** (página + log) por default; con `AULASENSE_PROVISION_AP_PASS`
  (menuconfig, gitignored, NUNCA logueada) → WPA2-PSK. Decisión D-F24-4: el segundo factor es
  la ruta privada con token (proximidad física + conocimiento del token).
- **Token:** 8 hex generados por CÓDIGO con `esp_fill_random` (NUNCA hardcodeado ni fijo),
  persistidos en NVS `aulasense_prov/cfg_token`. Se imprime la URL completa por serial **una vez
  por activación del AP** (`AulaSense CFG URL: http://192.168.4.1/cfg/<token>`); NUNCA sale por
  MQTT ni por endpoints no autenticados. *Desviación justificada del literal "una vez al
  generarse":* reimprimirlo en cada activación (evento raro, ligado al evento, no periódico)
  evita que un operador que perdió el scroll del monitor quede fuera del portal sin borrar NVS;
  quien tiene acceso al serial ya tiene acceso físico a la placa (mismo Threat model que BOOT).
- **Enrutado (E5):** los handlers se registran como `/cfg/*` con `httpd_uri_match_wildcard` y
  el segmento del token se valida en `ruta_token_ok()` con **comparación en tiempo constante**.
  *Desviación documentada:* `consttime_memequal` **NO existe en ESP-IDF 5.5.6** (la SPEC citaba
  `esp_mac.h`; verificado: cero coincidencias `memequal` en `components/**/*.h`) → el módulo
  trae `token_consttime_igual()` (acumulador XOR `volatile`, mismo propósito anti-timing).
  Ruta o token incorrectos → **404 neutro** sin pista (el 403 ni siquiera se emite sin token
  válido, para no delatar rutas).
- **Endpoints:** `GET /cfg/<token>` (formulario text-only, aviso AP abierto, estado sin
  binarios), `GET /cfg/<token>/estado` (JSON: `wifi_sta/ap_activa/fallos_sta/heap/psram`,
  CERO secretos), `POST /cfg/<token>/wifi` (flujo E3).
- httpd: **prio 4, stack 8192, puerto 80** (E7; el Direct HTTP del vendor vive en 62587). La
  tarea del módulo (`aulasense_prov`, prio 3, stack 8192) es la ÚNICA que opera la radio; los
  handlers WiFi/IP no loguean (regla del vendor) y solo postean a la cola.

### 13.3 Flujo de guardado (E3, crítico)

`POST /cfg/<token>/wifi` → Origin/Referer guard (403) → body leído una vez (≤255 B, urldecode
mínimo) → **escritura NVS `aulasense_wifi` SOLO si las credenciales CAMBIAN**, cooldown 5 s →
**429** → respuesta HTML → evento a la tarea del módulo: **AP OFF → `esp_wifi_stop()` →
`WIFI_MODE_STA` puro → `esp_wifi_set_config` con las credenciales del NVS →
`esp_wifi_connect()`**. Watch 25 s: GOT_IP → `STA_RECONECTADO` (AP queda OFF); desconexión o
agotado → **re-levantar AP**. El `maybe_retry_connect_` del vendor contra un driver en modo AP
falla sin ruido (su rama de error solo re-programa el deadline: sin log). `AP_OFF_S` (Kconfig)
cubre el caso defensivo GOT_IP-con-AP-arribado (p. ej. Improv concurrente).

### 13.4 Kconfig (menú "AulaSense Provisioning (F24-d)")

| Opción | Tipo | Default | Notas |
|---|---|---|---|
| `AULASENSE_PROVISION` | bool | y | Desactiva el módulo completo (no-op) |
| `AULASENSE_PROVISION_RETRIES` | int | 3 | Umbral PROPIO del módulo (E2), 1..10 |
| `AULASENSE_PROVISION_AP_PASS` | string | "" | Vacío = AP abierto con aviso; WPA2 opcional; NUNCA commitearla ni imprimirla |
| `AULASENSE_PROVISION_AP_OFF_S` | int | 30 | Gracia AP-off solo para el caso defensivo (13.3) |

Dependencias (E8): **cero componentes nuevos** — `esp_http_server`, `nvs_flash`, `esp_wifi`,
`esp_event` ya estaban en `main/CMakeLists.txt`; `SRCS` gana solo `app_provision.c`.

### 13.5 Eventos `aulasense_config`

`AP_FALLBACK` (`motivo/ap/auth`, sin secretos), `CFG_GUARDADA` (`ssid_len/pass_len` — NUNCA
valores), `STA_RECONECTADO` (`via=portal`). Los tres ya están en `EVENTOS_CONFIG` del servidor
(`server/ingesta/esquema.py:76-82` → filas `config_eventos` sin tocar `server/`). Cola anillo
4 slots con 3 reintentos de publicación (sin STA no hay broker: los eventos del fallback se
emiten cuando MQTT reconecta; si no, quedan solo en serial con log honesto).

### 13.6 Riesgos aceptados (documentados, evalúa el gate F24-e0/e y el gate seguridad)
- **Red auto-curada con AP arriba:** en modo AP puro el STA no puede reconectar solo (radio
  única, E3). Si el router revive, el nodo vuelve con: (a) POST del portal aunque las
  credenciales sean idénticas (siempre re-intenta STA), (b) reboot (el BOOT_CHECK y el
  contador manejan el arranque), o (c) Improv por USB. No hay sondeo periódico STA-AP porque
  la coexistencia cross-canal está precisamente prohibida.
- **CSI durante el fallback:** sin AP de red conocido el detector CSI pierde su fuente de
  paquetes; es inherente a "STA caído" (el vendor ya degrada igual), el módulo no lo empeora
  — el AP solo sirve red local.
- **Contador por desconexión:** 3 blips de red sin IP entre medianías suben el AP (umbral
  pesimista por diseño: el disparo falso es benigno — el portal no estorba y el flujo E3
  reconecta en cuanto la red responde).

### 13.7 Estado verificado en disco/placa (flash v2, 2026-11-15)

Bin `effa314f28e86e79abcf81361de629a0a056190a008a4ceb3ce93bd0411f9a24` (1,407,872 B) flasheada
COM6 intento 1 (log `iot2\aulasense\.tmp\f24d_flash_v2_raw.log`); **readback `verify_flash`
digest matched**; post-checks PASS: sensing 200, mqtt connected + outbox 32768, ingesta viva
(telemetría 3 s). El disparo E2→portal E3-E5 en vivo queda **pendiente de ventana natural**
(caída real del router / demo): la red actual está además EN canal 1, así que el AP fallback
no tendrá conflicto cross-canal. **Cómo leer el token (humanos):** tras
`SoftAP AULASENSE-CFG-xxxx ARRIBADO` en el monitor serial aparece la línea
`AulaSense CFG URL: http://192.168.4.1/cfg/<token>` — abrirla desde un dispositivo anclado al
AP. El token no se expone por ninguna otra vía (ni MQTT ni diagnostics): es capacidad local,
igual que la pass del portal.


## 14. F26 - OTA real + provisioning FIJO + gate/heap interno + observabilidad (SPEC §16.1-16.4, D-F26-1..5)

### 14.0b Fix F26-c3 (aplicado en el árbol, SIN flasheo; viaja en el bin candidato de g1)

- **Anillos de eventos con reintento espaciado** (`app_imagen.c` + `app_ota.c`): el pump
  re-encola el evento que falla el publish con marca temporal `sig_intento_ms = now+1000` y
  tope `*_EVT_REINTENTOS_MAX=120` (peek FIFO: no quema reintentos en el pump de 10 ms).
  Cubre la ventana de ~16 s hasta el primer `connected()` del arranque, donde se perdían
  `FIRMWARE`/`IMAGEN_INIT` (arriesgo documentado en c2; evidencia c3: 21 fotos + 14 IMAGEN_ON
  llegaban pero los eventos de boot no).
- **Versión `f26.2`**: `AULASENSE_FW_VERSION` default 1→ bump porque el bin candidato difiere
  del flasheado (`f26.1`); g1 necesita `version != local` para que `apply` no rechace con
  `sin_cambio`. El bin en placa (sellado c2, sha `8C2662B4…`) sigue siendo `f26.1` hasta que
  g1 le monte el `f26.2` por OTA (canal diseñado — sin segunda UART).
- **Prevalencia del retain `ota` verificada (c3)**: 300 s de suscripción al broker mostraron
  replay retain=true CON NUESTRO formato (`estado:"idle"`, `version_actual:"f26.1"`) y cero
  mensajes nativos `state` — la build F26 no invoca el OTA service del vendor, así que el
  retain nativo huérfano (hallazgo a3) ya no se renueva y el nuestro gana el slot. SIN fix
  adicional necesario; si g1/g2 vieran alternancia, la palanca prevista es refrescar
  `ota_estado()` en el tick de 30 s (2 líneas, no aplicido por no hacer falta).
- `heap_interno` retain=true confirmado en el broker (replay del watch); los 3 sufijos F26
  cacheados en `estado_sistema` (a1).
- **Bin candidato registrado (sha256)**: `965145A6BFCB7B5F9CD9AE77E441F2C405FF675041FB7B25617F69890D7CA40C`
  (1 418 656 B = 0x15A5A0; verificado con literal `f26.2` presente y `f26.1` ausente en rodata;
  build de chequeo verde SIN flasheo, log `iot2\.tmp\f26c3_build.log`). g1 publica ESTE bin.

Fase **F26-c1** (build única; NO flasheo — eso es c2). Cambios de código en este directorio +
espejo byte-a-byte en `firmware/espectre/src/cpp/frontend/native/app/main/` (regla 17, ahora
incluye también `app_ota.c/.h`) + enganches en `app_main.cpp` (sin espejo por convención; se
documentan en §14.6) + edición documentada de `sdkconfig.defaults`/`sdkconfig` (§14.5).

### 14.1 Módulos F26

| Fichero | Qué aporta F26 |
|---|---|
| `app_ota.c/.h` (NUEVO) | OTA propia: comando MQTT → manifiesto → descarga streaming → SHA-256 → set_boot → restart; self-check de rollback; retains `ota` + evento `FIRMWARE` |
| `app_provision.c` | Portal FIJO sin token (rutas `/cfg`, `/cfg/estado`, `/cfg/wifi`), SSID/pass de Kconfig, `_Static_assert` pass 8..63 salvo vacía, `AP_ERROR` en todas las ramas de fallo del AP |
| `app_imagen.c` | Gate de heap INTERNO sin latch (re-eval por onset, histéresis +20 KB), cola drop-oldest, `IMAGEN_INIT`/`CAMARA_ERROR`, retain `imagen_estado` en cada conexión, `app_imagen_pausar()` |
| `app_feedback.c` | Retain `heap_interno` cada 30 s (patrón aulasense_temp) + `app_feedback_pausar()` para la OTA |
| `Kconfig.projbuild` | Menú "AulaSense OTA (F26)" + SSID fijo + `AULASENSE_FW_VERSION`; QUEUE default 4 |
| `CMakeLists.txt` | `app_ota.c` al src (cero deps nuevas: `app_update`/`esp_http_client`/`mbedtls`/`nvs_flash` ya estaban, ENMIENDA E8) |

### 14.2 Semántica OTA (D-F26-1/2/3)

- **Manifiesto** (servido por el estático WSL, SPEC §16.9):
  `{"version":"f26.x","url":"http://<vps>:<puerto>/firmware-f26.x.bin","sha256":"<64 hex minúsculas>","size":<bytes>}`.
  Parser manual estricto (formato controlado por nosotros): cualquier desviación = `OTA_ERROR
  etapa=manifiesto/esquema_invalido` (fail-closed; nunca se flashea "por si acaso"). URL
  absoluta `http(s)://`; `size` ≥ 4096; `sha256` 64 hex minúsculas exactas.
- **Comando** (D-F26-2, topic sufijo propio — NO `commands/request`, el parser vendor rechaza
  comandos desconocidos y parchearlo está prohibido): la ingesta publica en
  `{topic_prefix}/{device_id}/aulasense_ota` (QoS 1, retain=false)
  `{"command_id":"<1-64 ASCII>","command":"aulasense_ota","action":"check"|"apply"}`.
  Suscripción vía API pública `transport.subscribe()` sobre la MISMA conexión persistente
  (N1). `command_id` se sanitiza a `[A-Za-z0-9._-]` antes de viajar en detalles JSON.
- **check** → GET manifiesto → evento `OTA_CHECK` detalle `origen=… id=… local=… fw_remota=…
  hay_actualizacion=true|false`. **apply** → gates → `OTA_INICIO` → pausa captura+telemetría →
  descarga en bloques de 4 KB (buffer PSRAM) al slot inactivo con `esp_ota_begin/write/end` →
  **SHA-256 acumulado EN STREAMING** (decisión: el digest cubre exactamente los bytes escritos;
  evita releer 1.4 MB de flash; la cabecera la valida `esp_ota_end` ANTES de comparar) → verify
  sha+size → `esp_ota_set_boot_partition` → `OTA_OK` + NVS (`aulasense_ota/fw_ver|fw_sha` para
  el `build_sha256_prefix` del próximo `FIRMWARE`) → retain `rebooting` → 2 s de drenaje →
  `esp_restart()`.
- **Cualquier fallo ANTES de `set_boot`** → `OTA_ERROR {etapa, esp_err}` + reanudar pausa +
  el slot actual queda INTACTO (`esp_ota_abort`). Nunca `CONFIG_OTA_ALLOW_HTTP`: no usamos
  `esp_https_ota`, así que HTTP plano es legal por construcción en `esp_http_client`.
- **Gates de inicio** (heap INTERNO, nunca el `free_memory_kb` de diagnósticos que es PSRAM):
  `internal_free ≥ AULASENSE_OTA_HEAP_MIN_KB` AND `largest_block ≥
  AULASENSE_OTA_BLOCK_MIN_KB`. Defaults originales 120/64 (SPEC §16.1) justificados con el
  heap de ARRANQUE (141–166 KB free, README §10/§10.1). **Ajuste F26-g1 con datos medidos en
  steady-state** (`f26g1_diag_heap.py`, 200 muestras de `telemetria.internal_*`): free
  101–106 KB / largest estable 56 KB → 120/64 era inalcanzable en régimen (0/200) y hacía la
  primera OTA imposible (huevo–gallina). **Fix DOBLE (decisión del orquestador 2026-11-17)**:
  (a) `accion_apply` reordenado — QUIESCE (pausa captura+telemetría) ANTES del gate y el
  detalle de `OTA_INICIO` lleva `post_quiesce free=…KB largest=…KB` (evidencia automática en
  BD; el delta liberado por el quiesce se cita desde el propio evento, sin instrumentation
  extra); (b) umbrales **96/48** alineados al mínimo medido PRE-quiesce (101/56) con colchón
  ≥5/8 KB — como el gate ahora corre post-quiesce (solo puede haber más heap, no menos),
  96/48 es la cota conservativa en ambos mundos. Nunca a ciegas. Chequeo periódico
  `AULASENSE_OTA_CHECK_MIN`
  default **0 = desactivado** (reproducibilidad del curso; >0 solo publica OTA_CHECK, jamás
  auto-apply). Tarea OTA prio 4, stack 8192 (NUNCA loop=5, ENMIENDA E7).
- **Rollback (obligatorio antes de la 1ª OTA)**: ver §14.5. Con imagen nueva en
  `ESP_OTA_IMG_PENDING_VERIFY`, el self-check exige **Wi-Fi GOT_IP + MQTT conectado + runtime
  armed CONTINUOS** `AULASENSE_OTA_SELFCHECK_S` s (90; range 60..120; referencia real: MQTT
  conectaba ~37 s tras arranque con WiFi ok) → `esp_ota_mark_app_valid_cancel_rollback()` +
  retain `idle`+`sha256_ok:true` "self-check superado: imagen validada" + evento `OTA_OK`. **Nota de contrato F26-g1**: la ventana de self-check usa el estado `verifying` (no `pending_verify`) y tras validar es `idle` con `sha256_ok:true` (no `valid`): el whitelist de ingesta (`esquema.py:416-423`, SPEC §16.4) solo admite `idle|checking|downloading|verifying|rebooting|error` y descarta fail-closed lo demas; la validacion la evidencia el evento `OTA_OK` + `sha256_ok:true`. Si en ventana+120 s de gracia no se cumple →
  `esp_ota_mark_app_invalid_rollback_and_reboot()` (marca la imagen INVALID y reinicia; el
  bootloader arranca el slot bueno).
  **Decisión documentada**: un `esp_restart()` simple NO revertiría (el estado seguiría
  PENDING_VERIFY y la imagen mala volvería a arrancar en bucle); la API canónica de self-check
  fallido es la de marcado invalid — el texto SPEC/PLAN "no marcar + esp_restart()" se refiere
  al efecto observable (volver al slot bueno), que solo esta API produce sin UART.
- **Particiones INTACTAS** (dual-app 0x1E0000 por slot; `check_sizes` en build = error de
  compilación si el bin no cabe; ampliar slots exigiría UART in situ → NO se hace).

### 14.3 Retains y eventos nuevos (SPEC §16.4)

- Retain `…/ota` (publish() absoluto, retain=true; el del vendor nunca se usa → sin colisión):
  `{"estado":"idle|checking|downloading|verifying|rebooting|error", /* F26-g1: whitelist exacto de esquema.py:416-423; sin pending_verify/valid */
  "version_actual":"<AULASENSE_FW_VERSION>","version_objetivo":"…","sha256_ok":false|true,
  "detalle":"…","ts_ms":<monotono>}` — re-publicado en CADA edge de conexión MQTT.
- Evento `FIRMWARE` (primera conexión tras arranque): detalle `version=… compile_time=<__DATE__
  __TIME__> idf=<esp_get_idf_version()> build_sha256_prefix=<12 hex del NVS|"uart-flash" si la
  imagen llegó por UART>`.
- Retain `…/imagen_estado` (formato EXACTO SPEC §16.3 + campos extra `pausada`/`cooldown_s`):
  `{"camara_ok","camara_pid","camara_err","pipeline_ok","captura_habilitada","gate_off",
  "descartes","imagenes_ok","internal_free_kb","internal_largest_block_kb","build",
  "imagen_habilitado","pausada","cooldown_s","ts_ms"}` — re-posteado en cada conexión y al
  cambiar contadores/gate (ventana única + dirty flag; pump del loop).
- Retain `…/heap_interno` (30 s, publisher periódico de `app_feedback`):
  `{"internal_free_kb","internal_largest_block_kb","ts_ms"}` medidos con
  `heap_caps_* (MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)`. La ingesta cachea el último por device y
  rellena `telemetria.internal_*` (columnas de a1).
- Eventos nuevos por `aulasense_config`: `OTA_CHECK/OTA_INICIO/OTA_OK/OTA_ERROR`, `FIRMWARE`,
  `IMAGEN_INIT`, `CAMARA_ERROR`, `AP_ERROR {etapa, esp_err}` (todas las ramas de ap_levantar:
  set_mode/set_config/wifi_start/httpd — el recovery jamás queda mudo, CRÍTICO informe A §1.1).
  Con `etapa=httpd` el AP SÍ queda arribado (la red sirve; solo el portal falló).

### 14.4 Provisioning FIJO (D-F26-4; reemplaza §13.2)

- SSID `CONFIG_AULASENSE_PROVISION_AP_SSID` (default "AULASENSE-CFG", SIN sufijo MAC; una sola
  unidad — colisión multi-placa = procedimiento "1 placa a la vez"). Pass
  `CONFIG_AULASENSE_PROVISION_AP_PASS` default `""` en Kconfig; el valor canónico viaja
  `.env`→`sdkconfig.defaults.local` en c2 (NUNCA commiteado, regla 7). Con pass no vacía el
  build FALLA si la longitud es 1..7 (`_Static_assert` 8..63 salvo vacío): la pass "itse" de
  2026-11-16 hacía fallar `esp_wifi_set_config` ANTES de publicar nada y el recovery quedaba
  mudo (informe A §1.1). Con `""` se conserva la rama AP ABIERTO con aviso.
- Portal sin token: `GET /cfg` (form, action `/cfg/wifi`, pie `/cfg/estado`), `GET /cfg/estado`
  (JSON sin secretos, ahora también `bloque_mayor_kb`), `POST /cfg/wifi` (E3 intacto).
  `max_uri_handlers` 2→4 y **handler propio por ruta exacta** (el matcher wildcard `"/cfg/*"`
  no casa `"/cfg"` sin barra — informe A §1.2.4); resto = 404 neutro del httpd.
- ELIMINADO del código: todo el mecanismo de token (generación, variable, ruta privada,
  comparación anti-timing). La key NVS antigua (`aulasense_prov`) NO se borra en campo:
  inofensiva, ya no se lee ni se escribe (grep de usos = 0, criterio c1).
- HEREDADAS INTACTAS: Origin/Referer (`host_es_propio` anti-sufijo, 403), cooldown NVS 5 s (429),
  body ≤255 B, escritura solo si cambia, cabeceras nosniff/DENY/no-referrer/no-store/CSP,
  NUNCA loguear SSID/pass (solo longitudes), BOOT 3 s → Improv nativo preservado.
- **Fix F26-g4 (f26.7, hallazgo del auditor externo 2026-11-17)**: la rama
  `esp_netif_create_default_wifi_ap() == NULL` de `ap_levantar()` (app_provision.c:282-296) era
  MUDA y el flujo SEGUÍA hacia `set_config`/`start`/httpd — podía arribarse un AP sin netif
  (portal 192.168.4.1 inalcanzable) publicándose `AP_FALLBACK`, contradiciendo SPEC §16.2
  ("AP_ERROR en TODAS las ramas"). Ahora: chequeo NULL explícito → `AP_ERROR
  {etapa=netif_ap esp_err=NULL motivo=…}` + `ESP_LOGE` + `return` ANTES de tocar
  wifi/httpd; la rama NULL no emite `AP_FALLBACK`. **f26.7 = f26.6 + este fix + bump: pendiente
  de aplicar por OTA post-entrega (viaja en el manifest publicado); la placa demostrativa del
  banco se queda en f26.6 válida — tomará f26.7 en la primera OTA tras la entrega.**

### 14.5 sdkconfig F26 (diff documentado — aplica tras clon limpio/factory reconfigure)

En `firmware/espectre/src/cpp/frontend/native/app/`:

1. `sdkconfig.defaults` (añadido tras la tabla de particiones; ver el bloque con comentario
   "AulaSense F26" en el fichero real):
   ```
   CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y
   CONFIG_APP_ROLLBACK_ENABLE=y
   ```
2. `sdkconfig` vivo (kconfiglib no re-aplica defaults a símbolos ya presentes):
   - `# CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is not set` → `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`
   - `# CONFIG_APP_ROLLBACK_ENABLE is not set` → `CONFIG_APP_ROLLBACK_ENABLE=y`
   - `CONFIG_AULASENSE_IMAGEN_QUEUE=2` → `CONFIG_AULASENSE_IMAGEN_QUEUE=4`
   Script one-shot aplicado en c1: `iot2\.tmp\f26c1_apply_sdkconfig.ps1` (reemplazos exactos,
   fail si no hay coincidencia única; sin duplicidades — trampa §10.1/10.2 ya controlada).
   Los símbolos nuevos del Kconfig F26 entran solos con su default en el primer build.
3. Pre-check ANTES de flashear (c2): `rollback=y` presente y único, cero `CONFIG_TINYUSB`/
   USB-device, cámara `AULASENSE_IMAGEN=y`, PSRAM octal intacta, bin ≤ 0x1E0000.
4. El bootloader con rollback se flashea en c2 junto con otadata_initial (`idf.py -p COM6 flash`
   escribe bootloader+partitions+otadata+app); la placa ya reinstalada lo lleva desde ahí.

### 14.6 Enganches de `app_main.cpp` (fichero SIN espejo — diffs exactos aquí)

1. `#include "app_ota.h"` junto a los otros tres headers del parche.
2. En `espectre_loop_task`, tras `app_provision_publish_pending();`:
   `app_ota_publish_pending();` (eventos OTA + retains + FIRMWARE — misma dueña única del
   transporte; los retains se re-postean en el edge de `connected()` sondeado en el pump:
   el callback del transporte lo posee el vendor (`frontend_mqtt_helpers.cpp:33`) y NO se pisa).
3. Tras `app_imagen_set_mqtt_image(...)` y antes de `app_imagen_init(...)`:
   ```cpp
   app_imagen_set_state_publisher(
       [](const char *payload) { return mqtt_transport.publish_suffix("imagen_estado", payload, true); });
   ```
4. Tras `app_feedback_set_mqtt_publisher(...)` (bloque F3):
   ```cpp
   app_feedback_set_heap_publisher(
       [](const char *payload) { return mqtt_transport.publish_suffix("heap_interno", payload, true); },
       []() { return mqtt_transport.connected(); });
   ```
5. Tras `app_provision_init()` (bloque completo comentado en el fichero):
   ```cpp
   app_ota_set_transport(subscribe_fn, publish_topic_fn, publish_config_fn, connected_fn);
   app_ota_set_runtime_ready([]() { return g_frontend != nullptr &&
       g_frontend->is_setup_complete() && g_frontend->snapshot().ready_to_publish; });
   app_ota_set_identity(device_id.c_str(), device_config.topic_prefix.c_str());
   app_ota_init();
   ```
   (el subscribe_fn envuelve `transport.subscribe()` con un trampolín C→std::function; corre
   en la tarea de loop prio 5 → solo valida y postea, nunca HTTP/flash).

### 14.7 Pruebas F26-c1 (build de check, sin placa)

| Prueba | Resultado |
|---|---|
| `eim run "idf.py set-target esp32s3 && idf.py build" v5.5.6` con pass VACÍA placeholder | VER §14.8 |
| `check_sizes`: bin vs 0x1E0000 | VER §14.8 |
| Espejo parche↔main byte-a-byte + SHA256 por par | VER §14.8 |
| grep token NVS en código (`cfg_token`/`token_asegurar`/`s_token`/`ruta_token_ok`) = 0 | VER §14.8 |
| Pre-check sdkconfig (rollback=y, cero USB-device, IMAGEN=y, PSRAM octal, QUEUE=4) | VER §14.8 |

### 14.8 Resultados verificados (F26-c1, 2026-11-17)

| Prueba | Resultado |
|---|---|
| Build IDF 5.5.6 con pass VACÍA placeholder (`AP_PASS=""`) | **PASS** — `eim run "idf.py build" v5.5.6` verde (`iot2\.tmp\f26c1_build.log`; nota: NO se re-ejecutó `set-target` porque regenera el sdkconfig desde defaults y destruye el bloque vivo — ver §14.9) |
| `check_sizes` vs 0x1E0000 | **PASS** — imagen **0x1597E0 (1 414 624 B)** de 1 966 080 → **0x86820 libres (550 944 B, 28 %)** (archivo .bin 1 415 136 B con relleno de alineado; crece ~7 KB sobre F25-c: app_ota.c + retain/eventos); sha256 `D50DA37505B3525E7AA22E57D6E53B89411AC2EBD9B0D6EB527C39673847521E` |
| Espejo parche↔main byte-a-byte | **PASS** — 10/10 pares con SHA256 idéntico (evidencia `iot2\.tmp\f26c1_espejo_final.csv`; incluye los NUEVOS `app_ota.c/.h` y ahora también `CMakeLists.txt` en espejo estricto) |
| grep token NVS en código | **PASS = 0** — ni `cfg_token` ni los identificadores del mecanismo (generación/variable/validación/comparación) sobreviven en *.c/*.h de parche ni main |
| Pre-check sdkconfig | **PASS** — `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y` y `CONFIG_APP_ROLLBACK_ENABLE=y` únicos (sin duplicados), cero activaciones USB-device (`TINYUSB`/`USB_DEVICE`/`USBD` ausentes; consola = USB-Serial-JTAG con `ESP_CONSOLE_UART_NUM=-1`), `AULASENSE_IMAGEN=y`, PSRAM octal 80 MHz intacta, `IMAGEN_QUEUE=4`, umbrales vivos del gate 95/24 conservados, `AP_PASS=""` (c2 inyecta la canónica) |
| Escala de rollback | bootloader.bin reconstruido con soporte (`build/bootloader/bootloader.bin`, 0x52D0 B) y `ota_data_initial.bin` en `flash_args` (c2 flashea 4 regiones) |

### 14.9 Notas de operación para c2 (desde c1)

1. **NO `idf.py set-target`**: destruye el `sdkconfig` vivo (lo renombra a `.old` y regenera
   desde defaults). El target ya es esp32s3; basta `eim run "idf.py build" v5.5.6`.
   Si hay que reproducir el estado vivo desde cero: `f24c_apply_sdkconfig.py` →
   `iot2\.tmp\f26c1_fix_sdkconfig.py` (SPIRAM/cámara/upload + QUEUE=4 + gates 95/24 + rollback).
2. `tools/gen_firmware_config.py` (extendido en b) debe emitir además
   `CONFIG_AULASENSE_PROVISION_AP_SSID/_AP_PASS` (sección PROVISION_AP del .env) y
   `CONFIG_AULASENSE_OTA_MANIFEST_URL` — en c1 NO se tocó tools/.
3. Con `MANIFEST_URL=""` el módulo OTA compila y arranca pero es INERTE para descargas
   (respuesta `OTA_ERROR origen=manifiesto err=ESP_ERR_INVALID_ARG url_cfg=vacia`): esperado
   hasta que c2 escriba el sdkconfig.defaults.local real.
4. El self-check de rollback NO actúa en la flasheada inicial de c2 (estado Undefined, no
   PENDING_VERIFY) — solo tras la 1ª OTA (g1/g2).
5. Riesgo c2/c3 conocido: si el sitio deja el mayor bloque interno bajo 44 KB (24+20 de
   histéresis), la captura seguirá off HASTA que un onset la re-habilite — ahora los datos
   internos viajan por `heap_interno`/`imagen_estado` y el ajuste puede ir por OTA (g1).

