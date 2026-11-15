/*
 * app_imagen.c — AulaSense (parche F24-c + extensión F25-c): evidencia de imagen en el ONSET.
 *
 * Derivado GPLv3 del vendor ESPectre Native 3.2.0. Comportamiento (README §9/§12,
 * SPEC §3.2/§3.2.1/§6.2/§7.1):
 *  - Captura SOLO en la transición idle->motion: 1 JPEG QVGA 320x240 (quality 12) de la OV3660,
 *    fb_count=2 con los framebuffers en PSRAM (CAMERA_FB_IN_PSRAM).
 *  - Cooldown de 15 s (`AULASENSE_IMAGEN_COOLDOWN_S`): 2 onsets seguidos -> 1 sola imagen.
 *  - Cola corta de onsets en PSRAM (`AULASENSE_IMAGEN_QUEUE`=2 slots) + buffers JPEG de
 *    <= 8 KB en PSRAM. Si la cola se llena -> se DESCARTA la imagen, se loguea y se emite
 *    `IMAGEN_DESCARTE`; el EVENTO motion sigue su curso por MQTT (la imagen es accesoria).
 *  - CANAL PRIMARIO (F25-c, D-F25-1): tras capturar, la tarea propia codifica el JPEG en base64
 *    estándar SIN saltos (mbedtls_base64_encode), calcula sha256 del JPEG crudo (mbedtls_sha256)
 *    y publica el envelope JSON de SPEC §6.2 en `{topic_prefix}/{device_id}/image` con el
 *    publisher MQTT nativo ya inyectado (vía `publish()`, retain=false, NO-reemplazable), sobre
 *    la MISMA conexión persistente del nodo (norma N1: prohibido abrir otra conexión).
 *  - CANAL ALTERNATIVO (norma N2): si el publish MQTT falla/rechaza — o el b64 supera
 *    `IMAGEN_MQTT_MAX_B64` (16384) — la imagen va por el upload HTTP de F24-c: POST binario a
 *    `http://CONFIG_AULASENSE_UPLOAD_HOST:PORT/internal/imagen` con `Authorization: Bearer`,
 *    `X-Device-Id`, `X-Ts-Ms`, `Content-Type: application/octet-stream`. `X-Ia-*` se OMITEN
 *    (la IA de visión es F24-e, opcional). Backoff 2/4/8 s y máximo 3 reintentos.
 *  - GATE DE MEMORIA: antes de capturar se comprueba el heap INTERNO. Si libre <
 *    `AULASENSE_IMAGEN_HEAP_MIN_KB` (120) o el mayor bloque libre <
 *    `AULASENSE_IMAGEN_HEAP_BLOCK_MIN_KB` (64) -> captura auto-deshabilitada con ESP_LOGW +
 *    evento `IMAGEN_GATE_OFF`; se re-habilita con histéresis (umbral + 20 KB). El CSI sigue
 *    detectando en todo momento: la foto es accesoria, el evento es la verdad.
 *
 * Diseño de hilos (no negociable, README §9 y AGENTS §Estilo):
 *  - El hook de estado (prio 5, tarea del loop de Native) solo compara y encola: O(1).
 *  - Cámara, memcpy de PSRAM, base64/sha256, publish MQTT y HTTP/backoff viven en la tarea
 *    propia (prio 3), NUNCA en el loop del detector CSI (loop = 5; captura/upload = 3-4, E7).
 *  - La publicación de eventos `aulasense_config` no se hace desde la tarea propia: se bombea
 *    desde la tarea del loop (`app_imagen_publish_pending()`). El envelope de imagen SÍ se
 *    publica desde la tarea propia (el resultado decide el fallback HTTP inmediatamente); el
 *    transporte vendor ya publica desde otras tareas de la app (worker de Direct HTTP ->
 *    `handle_*_write_` -> publish), así que la vía no introduce un patrón nuevo.
 *
 * Comentarios en español, identificadores en inglés (regla del repo AulaSense).
 */
#include "app_imagen.h"

#include "sdkconfig.h"

#include <stdio.h>
#include <string.h>

#include "esp_camera.h"
#include "driver/ledc.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "mbedtls/sha256.h"

/* ------------------------------------------------------------------------------------------
 * Guardas de compilación (SPEC §3.2, AGENTS §GPIO) — obligatorias.
 * ---------------------------------------------------------------------------------------- */

#ifndef CONFIG_AULASENSE_IMAGEN
#error "AulaSense: falta CONFIG_AULASENSE_IMAGEN. Copia Kconfig.projbuild del parche a main/."
#endif

/*
 * PSRAM obligatoria: los 2 framebuffers de la cámara, la cola de onsets y los buffers JPEG
 * viven en PSRAM (el heap interno es el recurso escaso del nodo: ~206 KB en reposo).
 * Sin CONFIG_SPIRAM la captura no cabe y el binario NO debe compilarse en silencio.
 */
#if CONFIG_AULASENSE_IMAGEN && !CONFIG_SPIRAM
#error "AulaSense: AULASENSE_IMAGEN=y exige CONFIG_SPIRAM=y (framebuffers y buffers JPEG en PSRAM). Ver README §10."
#endif

/* Pinout DVP de la GOOUUU ESP32-S3-CAM v1.5 con OV3660 (archive app_camara.c:20-37). */
#define AULASENSE_CAM_PIN_XCLK   15
#define AULASENSE_CAM_PIN_SIOD    4
#define AULASENSE_CAM_PIN_SIOC    5
#define AULASENSE_CAM_PIN_VSYNC   6
#define AULASENSE_CAM_PIN_HREF    7
#define AULASENSE_CAM_PIN_PCLK   13
#define AULASENSE_CAM_PIN_D0     11 /* Y2 */
#define AULASENSE_CAM_PIN_D1      9 /* Y3 */
#define AULASENSE_CAM_PIN_D2      8 /* Y4 */
#define AULASENSE_CAM_PIN_D3     10 /* Y5 */
#define AULASENSE_CAM_PIN_D4     12 /* Y6 */
#define AULASENSE_CAM_PIN_D5     18 /* Y7 */
#define AULASENSE_CAM_PIN_D6     17 /* Y8 */
#define AULASENSE_CAM_PIN_D7     16 /* Y9 */

/*
 * El bus DVP 4-18 es EXCLUSIVO de la cámara bajo AULASENSE_IMAGEN=y. El rango 4..18 excluye
 * por construcción los pines prohibidos SIEMPRE: 19/20 (USB nativo), 43/44 (UART0), 0/45/46
 * (strapping) y 26-37 (QSPI flash / PSRAM octal). El GPIO3 (LED flash de la placa) NO se usa.
 */
#define AULASENSE_DVP_BUS_MIN 4
#define AULASENSE_DVP_BUS_MAX 18

#if (AULASENSE_CAM_PIN_XCLK < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_XCLK > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin XCLK de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_SIOD < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_SIOD > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin SIOD de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_SIOC < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_SIOC > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin SIOC de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_VSYNC < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_VSYNC > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin VSYNC de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_HREF < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_HREF > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin HREF de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_PCLK < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_PCLK > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin PCLK de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_D0 < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_D0 > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin D0 de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_D1 < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_D1 > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin D1 de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_D2 < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_D2 > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin D2 de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_D3 < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_D3 > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin D3 de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_D4 < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_D4 > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin D4 de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_D5 < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_D5 > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin D5 de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_D6 < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_D6 > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin D6 de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif
#if (AULASENSE_CAM_PIN_D7 < AULASENSE_DVP_BUS_MIN) || (AULASENSE_CAM_PIN_D7 > AULASENSE_DVP_BUS_MAX)
#error "AulaSense: pin D7 de la cámara fuera del bus DVP 4-18 (¿pin prohibido?)."
#endif

#if (CONFIG_AULASENSE_IMAGEN_QUEUE < 1) || (CONFIG_AULASENSE_IMAGEN_QUEUE > 8)
#error "AulaSense: AULASENSE_IMAGEN_QUEUE debe estar en 1..8."
#endif

#if !CONFIG_AULASENSE_IMAGEN

/* Módulo apagado por Kconfig: stubs para que app_main.cpp enlace sin #ifdef en el vendor. */

static const char *TAG_OFF = "aulasense.imagen";

void app_imagen_set_mqtt_publisher(app_imagen_publish_fn publish,
                                   app_imagen_connected_fn connected) {
    (void)publish;
    (void)connected;
}

void app_imagen_set_mqtt_image(app_imagen_publish_topic_fn publish,
                               app_imagen_connected_fn connected, const char *topic_prefix) {
    (void)publish;
    (void)connected;
    (void)topic_prefix;
}

bool app_imagen_init(const char *device_id) {
    (void)device_id;
    ESP_LOGI(TAG_OFF, "imagen deshabilitada por Kconfig (AULASENSE_IMAGEN=n)");
    return false;
}

void app_imagen_on_state(bool motion, float score) {
    (void)motion;
    (void)score;
}

void app_imagen_publish_pending(void) {}

#else /* CONFIG_AULASENSE_IMAGEN */

static const char *TAG = "aulasense.imagen";

/* ------------------------------------------------------------------------------------------
 * Parámetros de comportamiento
 * ---------------------------------------------------------------------------------------- */

/* Tarea propia por DEBAJO del loop de Native (prio 5): ENMIENDA E7. Stack 8 KB porque
 * esp_http_client + lwIP + el formateo de cabeceras corren aquí, nunca en el detector. */
#define IMAGEN_TASK_PRIORITY 3
#define IMAGEN_TASK_STACK_SIZE 8192

/* Un JPEG QVGA q12 medido en esta placa: 3270 B (3,2 KB, F24-c 2026-11-14). La SPEC §3.2:93
 * esperaba 4-8 KB; el slot de 8 KB sobra igualmente y el receptor admite hasta 64 KB. */
#define IMAGEN_JPEG_MAX (8 * 1024)
#define IMAGEN_EVENT_QUEUE_LENGTH 5
#define IMAGEN_EVENT_PAYLOAD_MAX 160
#define IMAGEN_HTTP_TIMEOUT_MS 4000
#define IMAGEN_REINTENTOS 3
#define IMAGEN_HISTERESIS_KB 20

/*
 * F25-c (SPEC §3.2.1/§6.2). El tope del b64 va en #define DURO (16384) a propósito: el dispatch
 * de F25-c ordena NO añadir Kconfig nuevo; `AULASENSE_IMAGEN_MQTT` queda implícito en "¿hay
 * publisher inyectado?" (sin `app_imagen_set_mqtt_image()` el módulo se comporta como F24-c).
 * Con IMAGEN_JPEG_MAX = 8192 el b64 máximo es 10924 chars: el tope solo protege ante un cambio
 * futuro del slot JPEG o de la resolución (si se supera -> canal HTTP, nunca truncar).
 */
#define IMAGEN_MQTT_MAX_B64 16384
#define IMAGEN_MQTT_B64_CAP ((((IMAGEN_JPEG_MAX) + 2) / 3) * 4 + 1) /* 10925 para 8192 B */
#define IMAGEN_MQTT_TOPIC_MAX 96
#define IMAGEN_MQTT_PREFIX_MAX 64
#define IMAGEN_MQTT_SHA_HEX_LEN 64

/* ------------------------------------------------------------------------------------------
 * Estado del módulo
 * ---------------------------------------------------------------------------------------- */

typedef struct {
    int64_t ts_ms;
    float score;
} imagen_onset_t;

typedef struct {
    char json[IMAGEN_EVENT_PAYLOAD_MAX];
} imagen_evento_t;

static app_imagen_publish_fn s_publish_fn = NULL;
static app_imagen_connected_fn s_connected_fn = NULL;

/* Canal MQTT de la imagen (F25-c): publisher nativo, topic absoluto y buffers en PSRAM. */
static app_imagen_publish_topic_fn s_image_publish_fn = NULL;
static app_imagen_connected_fn s_image_connected_fn = NULL;
static char s_image_prefix[IMAGEN_MQTT_PREFIX_MAX] = "";
static char s_image_topic[IMAGEN_MQTT_TOPIC_MAX] = "";
static uint8_t *s_b64_buf = NULL;  /* IMAGEN_MQTT_B64_CAP bytes en PSRAM */
static char *s_json_buf = NULL;    /* envelope JSON completo + NUL en PSRAM */
static size_t s_json_cap = 0;
static bool s_mqtt_imagen_listo = false;
static uint32_t s_mqtt_enviadas = 0;

/* Cola de onsets en PSRAM (static queue: el almacenamiento lo reserva este módulo). */
static StaticQueue_t s_onset_queue_struct;
static uint8_t *s_onset_storage = NULL;
static QueueHandle_t s_onset_queue = NULL;

/* Cola de eventos MQTT pendientes (payloads cortos: heap interno, 5 x 160 B). */
static QueueHandle_t s_event_queue = NULL;

static TaskHandle_t s_task = NULL;

/* Pool de buffers JPEG en PSRAM: AULASENSE_IMAGEN_QUEUE slots de 8 KB (SPEC §3.2:95). */
static uint8_t *s_jpeg_pool[CONFIG_AULASENSE_IMAGEN_QUEUE];
static size_t s_jpeg_pool_next = 0;

/* Destino del upload (`s_url`) y cabecera Authorization armada UNA vez (token nunca logueado). */
static char s_device_id[20] = "";
static char s_url[96] = "";
static char s_auth[48] = "";
static bool s_http_listo = false;

static bool s_camera_ok = false;
static bool s_gate_ok = true;
static int64_t s_ultima_captura_us = 0;
static uint32_t s_subidas_ok = 0;
static uint32_t s_descartes = 0;

/* Estado debounced propio (se compara para no capturar por evaluación, solo por transición). */
static portMUX_TYPE s_state_mux = portMUX_INITIALIZER_UNLOCKED;
static bool s_last_motion = false;

/* ------------------------------------------------------------------------------------------
 * Eventos aulasense_config (SPEC §6.1): cola + bombeo desde el loop de Native
 * ---------------------------------------------------------------------------------------- */

static void publicar_evento(const char *evento, const char *detalle) {
    if (s_event_queue == NULL) {
        return;
    }
    imagen_evento_t ev;
    const long long ts_ms = (long long)(esp_timer_get_time() / 1000LL);
    const int n = snprintf(ev.json, sizeof(ev.json),
                           "{\"timestamp_ms\":%lld,\"evento\":\"%s\",\"detalle\":\"%s\"}", ts_ms,
                           evento, detalle);
    if (n <= 0 || n >= (int)sizeof(ev.json)) {
        ESP_LOGW(TAG, "payload del evento %s no cabe: se omite", evento);
        return;
    }
    if (xQueueSend(s_event_queue, &ev, 0) != pdTRUE) {
        ESP_LOGW(TAG, "cola de eventos llena: evento %s descartado", evento);
    }
}

void app_imagen_publish_pending(void) {
    if (s_event_queue == NULL || s_publish_fn == NULL) {
        return;
    }
    imagen_evento_t ev;
    while (xQueueReceive(s_event_queue, &ev, 0) == pdTRUE) {
        if (s_publish_fn(ev.json)) {
            ESP_LOGD(TAG, "evento de imagen publicado en aulasense_config");
        } else {
            ESP_LOGW(TAG, "evento de imagen no publicado: MQTT sin conexion");
        }
    }
}

/* ------------------------------------------------------------------------------------------
 * Heap interno (compartido por el gate de memoria y los logs del canal MQTT)
 * ---------------------------------------------------------------------------------------- */

static size_t heap_interno_libre(void) { return xPortGetFreeHeapSize(); }

static size_t heap_interno_bloque_mayor(void) {
    return heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}

/* ------------------------------------------------------------------------------------------
 * Canal PRIMARIO MQTT de la imagen (F25-c, SPEC §3.2.1/§6.2 — D-F25-1, normas N1/N2)
 * ---------------------------------------------------------------------------------------- */

/*
 * sha256 en hex MINÚSCULA (64 chars + NUL) del JPEG crudo. mbedTLS ya está en el app (el vendor
 * hashea con mbedtls_sha256 en runtime/esp_idf/device_identity.cpp): NO se añade componente.
 */
static bool sha256_hex_lower(const uint8_t *datos, size_t len,
                             char out[IMAGEN_MQTT_SHA_HEX_LEN + 1]) {
    static const char kHex[] = "0123456789abcdef";
    unsigned char digest[32];
    if (mbedtls_sha256(datos, len, digest, 0) != 0) {
        return false;
    }
    for (size_t i = 0; i < sizeof(digest); ++i) {
        out[2 * i] = kHex[digest[i] >> 4];
        out[2 * i + 1] = kHex[digest[i] & 0x0F];
    }
    out[IMAGEN_MQTT_SHA_HEX_LEN] = '\0';
    return true;
}

/*
 * Compone el envelope ESTRICTO de SPEC §6.2 en `s_json_buf`:
 *   {"v":1,"device_id":"<16 hex>","ts_ms":<monotono>,"motivo":"onset","tamano":<bytes>,
 *    "sha256":"<64 hex>","b64":"<JPEG base64 estándar SIN saltos>"}
 * `ts_ms` es el reloj monótono del nodo en ms (el mismo del evento motion), NO época.
 * Devuelve false si el b64 supera IMAGEN_MQTT_MAX_B64 (-> canal HTTP) o si algo no cabe.
 */
static bool construir_envelope(const uint8_t *jpeg, size_t len, int64_t ts_ms, size_t *json_len,
                               size_t *b64_chars) {
    if (s_b64_buf == NULL || s_json_buf == NULL || s_json_cap == 0) {
        return false;
    }

    size_t b64_len = 0;
    if (mbedtls_base64_encode(s_b64_buf, IMAGEN_MQTT_B64_CAP, &b64_len, jpeg, len) != 0) {
        ESP_LOGW(TAG, "base64 fallo para %u B de JPEG -> canal http", (unsigned)len);
        return false;
    }
    if (b64_len > (size_t)IMAGEN_MQTT_MAX_B64) {
        ESP_LOGW(TAG, "imagen grande b64=%d -> canal http", (int)b64_len);
        return false;
    }

    char sha[IMAGEN_MQTT_SHA_HEX_LEN + 1];
    if (!sha256_hex_lower(jpeg, len, sha)) {
        ESP_LOGW(TAG, "sha256 fallo para %u B de JPEG -> canal http", (unsigned)len);
        return false;
    }

    const int cabecera =
        snprintf(s_json_buf, s_json_cap,
                 "{\"v\":1,\"device_id\":\"%s\",\"ts_ms\":%lld,\"motivo\":\"onset\","
                 "\"tamano\":%u,\"sha256\":\"%s\",\"b64\":\"",
                 s_device_id, (long long)ts_ms, (unsigned)len, sha);
    if (cabecera <= 0 || (size_t)cabecera + b64_len + 3U > s_json_cap) {
        ESP_LOGW(TAG, "envelope no cabe (cap %u b64 %u) -> canal http", (unsigned)s_json_cap,
                 (unsigned)b64_len);
        return false;
    }
    memcpy(s_json_buf + cabecera, s_b64_buf, b64_len);
    s_json_buf[cabecera + b64_len] = '"';
    s_json_buf[cabecera + b64_len + 1] = '}';
    s_json_buf[cabecera + b64_len + 2] = '\0';
    *json_len = (size_t)cabecera + b64_len + 2U;
    *b64_chars = b64_len;
    return true;
}

/*
 * Publica el envelope por el publisher NATIVO ya inyectado (la misma conexión MQTT persistente
 * del nodo, N1: nunca se abre otra conexión). Devuelve true si el transporte ACEPTÓ el mensaje
 * en su cola de publicación (QoS 0, retain=false, no-reemplazable); false si no hay publisher,
 * el broker no está conectado o el transporte lo rechaza -> el llamador cae al canal HTTP (N2).
 */
static bool publicar_imagen_mqtt(const uint8_t *jpeg, size_t len, int64_t ts_ms) {
    if (!s_mqtt_imagen_listo || s_image_publish_fn == NULL) {
        ESP_LOGI(TAG, "mqtt publish fallo -> fallback http (canal mqtt no configurado)");
        return false;
    }
    if (s_image_connected_fn != NULL && !s_image_connected_fn()) {
        ESP_LOGW(TAG, "mqtt publish fallo -> fallback http (broker sin conexion)");
        return false;
    }

    size_t json_len = 0;
    size_t b64_len = 0;
    if (!construir_envelope(jpeg, len, ts_ms, &json_len, &b64_len)) {
        return false; /* ya logueado: base64/sha/tope de tamaño -> canal http */
    }

    if (!s_image_publish_fn(s_image_topic, s_json_buf)) {
        ESP_LOGW(TAG, "mqtt publish fallo -> fallback http (transporte rechazo el mensaje)");
        return false;
    }

    s_mqtt_enviadas++;
    ESP_LOGI(TAG, "imagen enviada via mqtt: %u B (b64 %d)", (unsigned)len, (int)b64_len);
    ESP_LOGI(TAG, "imagen mqtt detalle: topic=%s json=%u B total=%u heap_int=%u/%u KB", s_image_topic,
             (unsigned)json_len, (unsigned)s_mqtt_enviadas, (unsigned)(heap_interno_libre() / 1024U),
             (unsigned)(heap_interno_bloque_mayor() / 1024U));
    return true;
}

/* ------------------------------------------------------------------------------------------
 * Gate de memoria (heap INTERNO; la PSRAM no cuenta: los buffers ya están reservados)
 * ---------------------------------------------------------------------------------------- */

/* ------------------------------------------------------------------------------------------
 * Gate de memoria (heap INTERNO; la PSRAM no cuenta: los buffers ya están reservados)
 * ---------------------------------------------------------------------------------------- */

/*
 * Devuelve true si la captura está permitida. Auto-deshabilita con log honesto y evento
 * IMAGEN_GATE_OFF cuando el heap interno cae por debajo de los umbrales; se re-habilita con
 * histéresis (umbral + 20 KB) para no oscilar. El CSI NO se toca en ningún caso.
 */
static bool gate_captura_ok(void) {
    const size_t libre = heap_interno_libre();
    const size_t mayor = heap_interno_bloque_mayor();
    const size_t min_libre = (size_t)CONFIG_AULASENSE_IMAGEN_HEAP_MIN_KB * 1024U;
    const size_t min_mayor = (size_t)CONFIG_AULASENSE_IMAGEN_HEAP_BLOCK_MIN_KB * 1024U;
    const size_t histeresis = (size_t)IMAGEN_HISTERESIS_KB * 1024U;

    if (s_gate_ok) {
        if (libre < min_libre || mayor < min_mayor) {
            s_gate_ok = false;
            ESP_LOGW(TAG,
                     "IMAGEN_GATE_OFF: heap interno libre %u KB (min %d) o mayor bloque %u KB "
                     "(min %d) — captura deshabilitada, el CSI sigue detectando",
                     (unsigned)(libre / 1024U), CONFIG_AULASENSE_IMAGEN_HEAP_MIN_KB,
                     (unsigned)(mayor / 1024U), CONFIG_AULASENSE_IMAGEN_HEAP_BLOCK_MIN_KB);
            publicar_evento("IMAGEN_GATE_OFF", "heap interno bajo: captura off");
        }
    } else if (libre >= (min_libre + histeresis) && mayor >= (min_mayor + histeresis)) {
        s_gate_ok = true;
        ESP_LOGI(TAG,
                 "IMAGEN_GATE_ON (histeresis +%d KB): heap interno %u KB, mayor bloque %u KB",
                 IMAGEN_HISTERESIS_KB, (unsigned)(libre / 1024U), (unsigned)(mayor / 1024U));
    }
    return s_gate_ok;
}

/* ------------------------------------------------------------------------------------------
 * Cámara OV3660 (init una sola vez; en fallo el módulo degrada y el CSI sigue)
 * ---------------------------------------------------------------------------------------- */

static const char *nombre_sensor(uint16_t pid) {
    switch (pid) {
        case OV3660_PID: return "OV3660";
        case OV2640_PID: return "OV2640";
        case OV5640_PID: return "OV5640";
        case OV3640_PID: return "OV3640";
        case GC0308_PID: return "GC0308";
        default: return "desconocido";
    }
}

static bool camera_init(void) {
    const camera_config_t config = {
        .pin_pwdn = -1,
        .pin_reset = -1,
        .pin_xclk = AULASENSE_CAM_PIN_XCLK,
        .pin_sccb_sda = AULASENSE_CAM_PIN_SIOD,
        .pin_sccb_scl = AULASENSE_CAM_PIN_SIOC,
        .pin_d7 = AULASENSE_CAM_PIN_D7,
        .pin_d6 = AULASENSE_CAM_PIN_D6,
        .pin_d5 = AULASENSE_CAM_PIN_D5,
        .pin_d4 = AULASENSE_CAM_PIN_D4,
        .pin_d3 = AULASENSE_CAM_PIN_D3,
        .pin_d2 = AULASENSE_CAM_PIN_D2,
        .pin_d1 = AULASENSE_CAM_PIN_D1,
        .pin_d0 = AULASENSE_CAM_PIN_D0,
        .pin_vsync = AULASENSE_CAM_PIN_VSYNC,
        .pin_href = AULASENSE_CAM_PIN_HREF,
        .pin_pclk = AULASENSE_CAM_PIN_PCLK,
        .xclk_freq_hz = 10000000,  /* Fix F1 (raíz jpegqc 2026-11-15): 20->10 MHz. El byte-loss
                                    * GDMA (huecos de 16 B en captura) aparece con el FIFO al
                                    * límite bajo contención PSRAM octal 80 MHz: dividir el ritmo
                                    * DVP por 2 deja margen al drenado. Captura ~2x más lenta,
                                    * irrelevante a 1 foto/onset con cooldown 15 s. */
        /* LEDC timer/canal 0: el buzzer del feedback usa timer2/canal2 (app_feedback.c). */
        .ledc_timer = LEDC_TIMER_0,
        .ledc_channel = LEDC_CHANNEL_0,
        /*
         * JPEG (ENMIENDA E1): el archive usó PIXFORMAT_RGB565 y su jpeg_quality quedaba
         * inactivo. Para subir la evidencia como JPEG el sensor DEBE comprimir aquí.
         */
        .pixel_format = PIXFORMAT_JPEG,
        .frame_size = FRAMESIZE_QVGA, /* 320x240: tamaño de thumbnail, sin valor extra en SXGA */
        .jpeg_quality = 12,
        .fb_count = 2,
        .fb_location = CAMERA_FB_IN_PSRAM,
        .grab_mode = CAMERA_GRAB_LATEST,
    };

    const esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_camera_init fallo: %s (0x%x) — captura deshabilitada", esp_err_to_name(err),
                 err);
        return false;
    }

    sensor_t *s = esp_camera_sensor_get();
    uint16_t pid = 0;
    if (s != NULL) {
        pid = s->id.PID;
        /* QVGA 320x240 ya viene en la config; algunos sensores la ignoran al arrancar. */
        s->set_framesize(s, FRAMESIZE_QVGA);
    }
    ESP_LOGI(TAG,
             "imagen init ok: camera=%s (PID=0x%04x, OV3660_PID=0x%04x) QVGA 320x240 JPEG q%d "
             "fb_count=%d PSRAM",
             nombre_sensor(pid), pid, (unsigned)OV3660_PID, 12, 2);

    /* Evidencia del coste REAL en heap interno (la PSRAM no se cuenta aquí). */
    multi_heap_info_t info;
    heap_caps_get_info(&info, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    ESP_LOGI(TAG,
             "heap interno tras init: free=%u KB largest=%u KB minimo_libre=%u KB bloques=%u/%u",
             (unsigned)(info.total_free_bytes / 1024U), (unsigned)(info.largest_free_block / 1024U),
             (unsigned)(info.minimum_free_bytes / 1024U), (unsigned)info.allocated_blocks,
             (unsigned)info.free_blocks);
    return true;
}

/* ------------------------------------------------------------------------------------------
 * Upload HTTP (POST binario al receptor de la ingesta, SPEC §7.1)
 * ---------------------------------------------------------------------------------------- */

static int post_una_vez(const uint8_t *datos, size_t len, int64_t ts_ms) {
    esp_http_client_config_t cfg = {
        .url = s_url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = IMAGEN_HTTP_TIMEOUT_MS,
        .disable_auto_redirect = true,
        .buffer_size = 512,
        .buffer_size_tx = 1024,
    };
    esp_http_client_handle_t cliente = esp_http_client_init(&cfg);
    if (cliente == NULL) {
        ESP_LOGW(TAG, "esp_http_client_init fallo");
        return -1;
    }

    char ts[24];
    snprintf(ts, sizeof(ts), "%lld", (long long)ts_ms);
    esp_http_client_set_header(cliente, "Authorization", s_auth);
    esp_http_client_set_header(cliente, "Content-Type", "application/octet-stream");
    esp_http_client_set_header(cliente, "X-Device-Id", s_device_id);
    esp_http_client_set_header(cliente, "X-Ts-Ms", ts);
    /* X-Ia-* se OMITEN: la IA de visión es F24-e (opcional, spike-gated). */
    esp_http_client_set_post_field(cliente, (const char *)datos, (int)len);

    const esp_err_t err = esp_http_client_perform(cliente);
    int status = -1;
    if (err == ESP_OK) {
        status = esp_http_client_get_status_code(cliente);
    } else {
        ESP_LOGW(TAG, "upload error de transporte: %s", esp_err_to_name(err));
    }
    esp_http_client_cleanup(cliente);
    return status;
}

/* Reintentos con backoff 2/4/8 s. Un 4xx es definitivo (no se reintenta). */
static void subir_jpeg(const uint8_t *datos, size_t len, int64_t ts_ms) {
    if (!s_http_listo) {
        ESP_LOGW(TAG, "upload sin configurar (host/token vacios): imagen descartada (%u B)",
                 (unsigned)len);
        publicar_evento("IMAGEN_DESCARTE", "upload sin host/token");
        s_descartes++;
        return;
    }

    int status = -1;
    if (s_connected_fn != NULL && !s_connected_fn()) {
        /* El upload NO depende del broker (LAN directa), pero el estado ayuda a diagnosticar. */
        ESP_LOGD(TAG, "MQTT sin conexion: el upload HTTP se intenta igual");
    }
    for (int intento = 1; intento <= (IMAGEN_REINTENTOS + 1); ++intento) {
        status = post_una_vez(datos, len, ts_ms);
        if (status == 201 || status == 200) {
            s_subidas_ok++;
            ESP_LOGI(TAG, "IMAGEN_SUBIDA ok http=%d %u B intento=%d (total %u)", status,
                     (unsigned)len, intento, (unsigned)s_subidas_ok);
            return;
        }
        if (status >= 400 && status < 500) {
            ESP_LOGW(TAG, "upload rechazado http=%d (%u B): sin reintento", status, (unsigned)len);
            publicar_evento("IMAGEN_DESCARTE", "upload rechazado 4xx");
            s_descartes++;
            return;
        }
        if (intento <= IMAGEN_REINTENTOS) {
            const int espera_ms = 2000 << (intento - 1); /* 2 s, 4 s, 8 s */
            ESP_LOGW(TAG, "upload fallo http=%d (intento %d/%d): reintento en %d ms", status, intento,
                     IMAGEN_REINTENTOS + 1, espera_ms);
            vTaskDelay(pdMS_TO_TICKS(espera_ms));
        }
    }
    ESP_LOGW(TAG,
             "IMAGEN_DESCARTE tras %d intentos (ultimo http=%d): el evento motion sigue por MQTT",
             IMAGEN_REINTENTOS + 1, status);
    publicar_evento("IMAGEN_DESCARTE", "upload agotado tras 3 reintentos");
    s_descartes++;
}

/* ------------------------------------------------------------------------------------------
 * Captura (solo en la tarea propia)
 * ---------------------------------------------------------------------------------------- */

/* Fix F2 (raíz jpegqc 2026-11-15): validacion estructural minima del JPEG recien
 * capturado, ANTES de hashear/publicar. El byte-loss por overflow del FIFO GDMA
 * (contencion del PSRAM octal bajo carga CSI/WiFi) deja SOI FFD8 y EOI FFD9
 * intactos pero pierde segmentos de 16 B en la cabecera (offsets 336-590): el
 * walk de marcadores pierde el sync alli. Mismo contrato que _jpeg_estructura_ok
 * del servidor (Fix S). Limitacion aceptada: un hueco enteramente dentro de la
 * entropy data pasa (el walk se detiene en el primer SOS) — ese filtro lo hace
 * el decoder del servidor solo si rompe algo mas y el QC de PIL post-hoc.
 * Devuelve false con cualquier rotura de sintaxis. */
static bool jpeg_sintaxis_ok(const uint8_t *d, size_t n) {
    if (n < 4U || d[0] != 0xFFU || d[1] != 0xD8U) {
        return false; /* sin SOI */
    }
    bool eoi_ok = false;
    for (size_t i = n - 4U; i + 1U < n; ++i) {
        if (d[i] == 0xFFU && d[i + 1U] == 0xD9U) {
            eoi_ok = true;
            break;
        }
    }
    if (!eoi_ok) {
        return false; /* sin EOI en los ultimos 4 B */
    }
    bool sof_visto = false;
    size_t i = 2U;
    while (i + 1U < n) {
        if (d[i] != 0xFFU) {
            return false; /* perdida de sync */
        }
        size_t k = i + 1U;
        while (k < n && d[k] == 0xFFU) {
            ++k; /* bytes de relleno */
        }
        if (k >= n) {
            return false;
        }
        const uint8_t c = d[k];
        if (c == 0x00U) {
            return false;
        }
        if (c == 0xDAU) {
            return sof_visto; /* SOS: fin del walk; exige SOF previo */
        }
        if (c >= 0xC0U && c <= 0xCFU && c != 0xC4U && c != 0xC8U && c != 0xCCU) {
            sof_visto = true;
        }
        const bool sin_longitud = (c == 0x01U) || (c == 0xD8U) || (c == 0xD9U) ||
                                  (c >= 0xD0U && c <= 0xD7U);
        if (sin_longitud) {
            i = k + 1U;
            continue;
        }
        if (k + 2U >= n) {
            return false;
        }
        const size_t largo = ((size_t)d[k + 1U] << 8) | (size_t)d[k + 2U];
        if (largo < 2U) {
            return false;
        }
        i = k + largo + 1U;
        if (!sof_visto && i >= 2048U) {
            return false; /* sin SOF en los primeros 2 KB */
        }
    }
    return false; /* sin SOS: cabecera incompleta */
}

static void procesar_onset(const imagen_onset_t *onset) {
    ESP_LOGI(TAG, "onset recibido ts=%lld score=%.3f camera=%s gate=%s (diagnostico F24-c)",
             (long long)onset->ts_ms, (double)onset->score, s_camera_ok ? "ok" : "off",
             s_gate_ok ? "open" : "closed");
    if (!s_camera_ok) {
        return;
    }

    const int64_t ahora_us = esp_timer_get_time();
    const int64_t cooldown_us = (int64_t)CONFIG_AULASENSE_IMAGEN_COOLDOWN_S * 1000000LL;
    if (s_ultima_captura_us != 0 && (ahora_us - s_ultima_captura_us) < cooldown_us) {
        ESP_LOGI(TAG, "cooldown %d s vigente (%.1f s desde la ultima captura): onset sin imagen",
                 CONFIG_AULASENSE_IMAGEN_COOLDOWN_S,
                 (double)(ahora_us - s_ultima_captura_us) / 1000000.0);
        return;
    }
    if (!gate_captura_ok()) {
        return;
    }

    ESP_LOGI(TAG, "capturando frame (esp_camera_fb_get) heap_int=%u/%u KB",
             (unsigned)(heap_interno_libre() / 1024U), (unsigned)(heap_interno_bloque_mayor() / 1024U));
    camera_fb_t *fb = esp_camera_fb_get();
    ESP_LOGI(TAG, "frame recibido fb=%p", (void *)fb);
    if (fb == NULL) {
        ESP_LOGW(TAG, "esp_camera_fb_get devolvio NULL: onset sin imagen");
        publicar_evento("IMAGEN_DESCARTE", "framebuffer nulo");
        s_descartes++;
        return;
    }
    if (fb->format != PIXFORMAT_JPEG || fb->len == 0U || fb->len > IMAGEN_JPEG_MAX) {
        ESP_LOGW(TAG, "frame inutilizable: formato=%d len=%u (max %d): onset sin imagen",
                 (int)fb->format, (unsigned)fb->len, IMAGEN_JPEG_MAX);
        esp_camera_fb_return(fb);
        publicar_evento("IMAGEN_DESCARTE", "frame no JPEG o mayor de 8 KB");
        s_descartes++;
        return;
    }

    uint8_t *buf = s_jpeg_pool[s_jpeg_pool_next];
    if (buf == NULL) {
        esp_camera_fb_return(fb);
        ESP_LOGW(TAG, "pool de buffers JPEG agotado: onset sin imagen");
        publicar_evento("IMAGEN_DESCARTE", "pool de buffers agotado");
        s_descartes++;
        return;
    }
    s_jpeg_pool_next = (s_jpeg_pool_next + 1) % CONFIG_AULASENSE_IMAGEN_QUEUE;

    const size_t len = fb->len;
    const int ancho = (int)fb->width;
    const int alto = (int)fb->height;
    memcpy(buf, fb->buf, len);
    esp_camera_fb_return(fb); /* el driver queda libre ANTES del HTTP (no retiene framebuffers) */

    /* Fix F2: el byte-loss de captura se detecta AQUI, tras el memcpy y ANTES de
     * hashear/publicar. Se descarta la imagen, NO el evento: el onset ya se publico
     * por el detector. No se marca cooldown: el siguiente onset reintenta la captura. */
    if (!jpeg_sintaxis_ok(buf, len)) {
        ESP_LOGW(TAG, "byte-loss en captura: estructura JPEG invalida len=%u; imagen descartada",
                 (unsigned)len);
        publicar_evento("IMAGEN_DESCARTE", "byte-loss en captura");
        s_descartes++;
        return;
    }

    s_ultima_captura_us = ahora_us;
    ESP_LOGI(TAG, "IMAGEN_ON captura %u B (%.1f KB) %dx%d score=%.3f ts=%lld heap_int=%u/%u KB",
             (unsigned)len, (double)len / 1024.0, ancho, alto, (double)onset->score,
             (long long)onset->ts_ms, (unsigned)(heap_interno_libre() / 1024U),
             (unsigned)(heap_interno_bloque_mayor() / 1024U));
    publicar_evento("IMAGEN_ON", "captura QVGA en el onset");

    /*
     * F25-c (N2): canal PRIMARIO = envelope JSON+b64 por la conexión MQTT persistente del nodo.
     * Solo si el publish falla/rechaza (o no cabe en 16384 chars de b64) se usa el upload HTTP
     * de F24-c, que queda intacto. La imagen nunca bloquea el evento motion.
     */
    if (publicar_imagen_mqtt(buf, len, onset->ts_ms)) {
        return;
    }
    subir_jpeg(buf, len, onset->ts_ms);
}

/* ------------------------------------------------------------------------------------------
 * Tarea propia del módulo (prioridad 3)
 * ---------------------------------------------------------------------------------------- */

static void imagen_task(void *arg) {
    (void)arg;
    imagen_onset_t onset;
    ESP_LOGI(TAG, "tarea de imagen arrancada (prio %d, stack %d): esperando onsets",
             IMAGEN_TASK_PRIORITY, IMAGEN_TASK_STACK_SIZE);
    for (;;) {
        if (xQueueReceive(s_onset_queue, &onset, pdMS_TO_TICKS(500)) == pdTRUE) {
            procesar_onset(&onset);
        }
    }
}

/* ------------------------------------------------------------------------------------------
 * API pública
 * ---------------------------------------------------------------------------------------- */

void app_imagen_set_mqtt_publisher(app_imagen_publish_fn publish,
                                   app_imagen_connected_fn connected) {
    s_publish_fn = publish;
    s_connected_fn = connected;
}

void app_imagen_set_mqtt_image(app_imagen_publish_topic_fn publish,
                               app_imagen_connected_fn connected, const char *topic_prefix) {
    s_image_publish_fn = publish;
    s_image_connected_fn = connected;
    s_image_prefix[0] = '\0';
    if (topic_prefix != NULL && topic_prefix[0] != '\0' &&
        strlen(topic_prefix) < sizeof(s_image_prefix)) {
        snprintf(s_image_prefix, sizeof(s_image_prefix), "%s", topic_prefix);
    }
}

static void preparar_destino(const char *device_id) {
    const char *host = CONFIG_AULASENSE_UPLOAD_HOST;
    const char *token = CONFIG_AULASENSE_UPLOAD_TOKEN;

    if (device_id != NULL && strlen(device_id) < sizeof(s_device_id)) {
        snprintf(s_device_id, sizeof(s_device_id), "%s", device_id);
    }

    if (host[0] == '\0' || token[0] == '\0') {
        s_http_listo = false;
        ESP_LOGW(TAG,
                 "upload deshabilitado: AULASENSE_UPLOAD_HOST=%s AULASENSE_UPLOAD_TOKEN len=%u "
                 "(solo se captura y se descarta)",
                 host[0] == '\0' ? "vacio" : "presente", (unsigned)strlen(token));
        return;
    }

    const int n = snprintf(s_url, sizeof(s_url), "http://%s:%d/internal/imagen", host,
                           CONFIG_AULASENSE_UPLOAD_PORT);
    if (n <= 0 || n >= (int)sizeof(s_url)) {
        s_http_listo = false;
        ESP_LOGE(TAG, "URL de upload no cabe en el buffer: upload deshabilitado");
        return;
    }
    snprintf(s_auth, sizeof(s_auth), "Bearer %s", token);
    s_http_listo = true;
    /* JAMÁS se loguea el token: solo su longitud y el destino. */
    ESP_LOGI(TAG, "upload listo: http://%s:%d/internal/imagen device=%s token_len=%u (oculto)", host,
             CONFIG_AULASENSE_UPLOAD_PORT, s_device_id, (unsigned)strlen(token));
}

/*
 * Compone el topic ABSOLUTO `{topic_prefix}/{device_id}/image` (mismo `topic_prefix` del config
 * runtime que usa el transporte vendor) y reserva en PSRAM los buffers del canal MQTT.
 * Sin publisher inyectado, el módulo se comporta como F24-c (solo HTTP): no es un error.
 */
static void preparar_mqtt_imagen(void) {
    s_mqtt_imagen_listo = false;
    if (s_image_publish_fn == NULL) {
        ESP_LOGI(TAG, "imagen por MQTT sin publisher inyectado: solo canal http (F24-c)");
        return;
    }
    if (s_device_id[0] == '\0') {
        ESP_LOGW(TAG, "imagen por MQTT sin device_id: solo canal http");
        return;
    }

    const char *prefijo = s_image_prefix[0] != '\0' ? s_image_prefix : CONFIG_ESPECTRE_TOPIC_PREFIX;
    size_t n = strlen(prefijo);
    while (n > 0 && prefijo[n - 1] == '/') {
        --n;
    }
    const int escrito = snprintf(s_image_topic, sizeof(s_image_topic), "%.*s/%s/image", (int)n,
                                 prefijo, s_device_id);
    if (n == 0 || escrito <= 0 || escrito >= (int)sizeof(s_image_topic)) {
        s_image_topic[0] = '\0';
        ESP_LOGE(TAG, "topic de imagen vacio o no cabe (%d): solo canal http", escrito);
        return;
    }

    s_b64_buf = heap_caps_malloc(IMAGEN_MQTT_B64_CAP, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    s_json_cap = (size_t)IMAGEN_MQTT_B64_CAP + 256U;
    s_json_buf = heap_caps_malloc(s_json_cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (s_b64_buf == NULL || s_json_buf == NULL) {
        if (s_b64_buf != NULL) {
            heap_caps_free(s_b64_buf);
            s_b64_buf = NULL;
        }
        if (s_json_buf != NULL) {
            heap_caps_free(s_json_buf);
            s_json_buf = NULL;
        }
        s_json_cap = 0;
        ESP_LOGW(TAG, "PSRAM insuficiente para los buffers MQTT de imagen: solo canal http");
        return;
    }

    s_mqtt_imagen_listo = true;
    ESP_LOGI(TAG,
             "imagen mqtt listo: topic=%s retain=false no-reemplazable max_b64=%d buffers_psram=%u B",
             s_image_topic, IMAGEN_MQTT_MAX_B64, (unsigned)(IMAGEN_MQTT_B64_CAP + s_json_cap));
}

bool app_imagen_init(const char *device_id) {
    if (s_task != NULL) {
        return true;
    }

    preparar_destino(device_id);
    preparar_mqtt_imagen();

    /* Cola de onsets en PSRAM (2 slots por defecto). */
    s_onset_storage = heap_caps_malloc(CONFIG_AULASENSE_IMAGEN_QUEUE * sizeof(imagen_onset_t),
                                       MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (s_onset_storage == NULL) {
        ESP_LOGE(TAG, "no se pudo reservar la cola de onsets en PSRAM: modulo apagado");
        return false;
    }
    s_onset_queue = xQueueCreateStatic(CONFIG_AULASENSE_IMAGEN_QUEUE, sizeof(imagen_onset_t),
                                       s_onset_storage, &s_onset_queue_struct);
    s_event_queue = xQueueCreate(IMAGEN_EVENT_QUEUE_LENGTH, sizeof(imagen_evento_t));
    if (s_onset_queue == NULL || s_event_queue == NULL) {
        ESP_LOGE(TAG, "no se pudo crear la cola de eventos/onsets: modulo apagado");
        return false;
    }

    /* Pool de buffers JPEG en PSRAM: QUEUE slots de 8 KB (16 KB con el default). */
    for (size_t i = 0; i < (size_t)CONFIG_AULASENSE_IMAGEN_QUEUE; ++i) {
        s_jpeg_pool[i] = heap_caps_malloc(IMAGEN_JPEG_MAX, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (s_jpeg_pool[i] == NULL) {
            ESP_LOGE(TAG, "PSRAM insuficiente para el buffer JPEG %u: modulo apagado", (unsigned)i);
            return false;
        }
    }
    ESP_LOGI(TAG, "pool JPEG en PSRAM: %d slots x %d B", CONFIG_AULASENSE_IMAGEN_QUEUE,
             IMAGEN_JPEG_MAX);

    s_camera_ok = camera_init();
    if (!s_camera_ok) {
        /* Degradación honesta: el feedback y el CSI siguen; solo no hay evidencia. */
        ESP_LOGW(TAG, "sin camara: el nodo sigue detectando movimiento por CSI (sin imagenes)");
    } else {
        /*
         * Prueba de captura ÚNICA en el arranque (no es un evento: no se sube nada). Sirve para
         * (a) validar que el pipeline DVP->JPEG entrega frames y (b) MEDIR el tamaño real del
         * JPEG QVGA q12 en esta placa (SPEC §3.2:93 pide medirlo; S13).
         */
        camera_fb_t *prueba = esp_camera_fb_get();
        if (prueba != NULL) {
            ESP_LOGI(TAG, "prueba de captura: %u B (%.1f KB) %dx%d fmt=%d (no se sube: solo medicion)",
                     (unsigned)prueba->len, (double)prueba->len / 1024.0, (int)prueba->width,
                     (int)prueba->height, (int)prueba->format);
            esp_camera_fb_return(prueba);
        } else {
            ESP_LOGW(TAG, "prueba de captura: esp_camera_fb_get devolvio NULL");
        }
    }

    if (xTaskCreate(imagen_task, "aulasense_img", IMAGEN_TASK_STACK_SIZE, NULL, IMAGEN_TASK_PRIORITY,
                    &s_task) != pdPASS) {
        s_task = NULL;
        ESP_LOGE(TAG, "no se pudo crear la tarea de imagen (prio %d)", IMAGEN_TASK_PRIORITY);
        return false;
    }

    ESP_LOGI(TAG,
             "imagen listo: camera=%s upload=%s mqtt=%s cooldown=%d s cola=%d heap_min=%d/%d KB "
             "tarea prio %d",
             s_camera_ok ? "ok" : "off", s_http_listo ? "ok" : "off",
             s_mqtt_imagen_listo ? "ok" : "off", CONFIG_AULASENSE_IMAGEN_COOLDOWN_S,
             CONFIG_AULASENSE_IMAGEN_QUEUE, CONFIG_AULASENSE_IMAGEN_HEAP_MIN_KB,
             CONFIG_AULASENSE_IMAGEN_HEAP_BLOCK_MIN_KB, IMAGEN_TASK_PRIORITY);
    return true;
}

void app_imagen_on_state(bool motion, float score) {
    if (s_onset_queue == NULL) {
        return;
    }

    /* Comparación y actualización del estado previo: sección crítica mínima, sin llamadas. */
    bool changed = false;
    portENTER_CRITICAL(&s_state_mux);
    if (s_last_motion != motion) {
        s_last_motion = motion;
        changed = true;
    }
    portEXIT_CRITICAL(&s_state_mux);
    if (!changed || !motion) {
        return; /* solo interesa el onset idle->motion */
    }

    imagen_onset_t onset = {.ts_ms = esp_timer_get_time() / 1000LL, .score = score};
    if (xQueueSend(s_onset_queue, &onset, 0) != pdTRUE) {
        ESP_LOGW(TAG, "IMAGEN_DESCARTE: cola de onsets llena (%d) — el evento sigue por MQTT",
                 CONFIG_AULASENSE_IMAGEN_QUEUE);
        publicar_evento("IMAGEN_DESCARTE", "cola de onsets llena");
        s_descartes++;
    }
}

#endif /* CONFIG_AULASENSE_IMAGEN */