# parche_aulasense/ — Módulo de feedback local `app_feedback` (spec del derivado GPLv3)

> **Estado: SPECIFICACIÓN.** Este README lo escribió el planificador (2026-11-12) como contrato del
> módulo. El código (`app_feedback.c`, `app_feedback.h`, `Kconfig.projbuild`, `CMakeLists.txt`) lo
> implementa `@5-dev` en la **fase F3** del mapa `PLAN.md` §6, bajo el ejecutor. Nada de este parche
> existe aún como código; este documento es el entregable de diseño verificable.
>
> **F24 (2026-11-12):** este directorio se AMPLÍA con módulos nuevos — `app_imagen.c/.h` (F24-c),
> `app_provision.c/.h` (F24-d), `app_ia_imagen.c/.h` (F24-e, opcional spike-gated) — que comparten
> `Kconfig.projbuild`/`CMakeLists.txt`/este README. Contratos: `../../SPEC.md` §3.2–§3.4; decisiones
> D-F24-1..8: `../../PLAN.md` §11. F24-c/d/e añaden aquí su sección de spec (NO reabren F3).
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