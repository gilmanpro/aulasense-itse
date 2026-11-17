/*
 * app_ota.c — AulaSense (parche F26): OTA real por comando MQTT propio + rollback.
 *
 * Derivado GPLv3 del vendor ESPectre Native 3.2.0. Contrato completo en app_ota.h;
 * SPEC §16.1/§16.3/§16.4, PLAN §13.2 D-F26-1/2/3, informe .tmp/inv_ota_provision.md.
 *
 * Diseño de hilos (no negociable):
 *  - app_ota_on_message corre en la tarea del loop de Native (prio 5): solo valida el
 *    envelope y postea (xQueueSend espera 0).
 *  - La tarea propia "aulasense_ota" (prio 4, stack 8192 — NUNCA loop=5, ENMIENDA E7) hace
 *    manifiesto, descarga (esp_http_client), escritura del slot (esp_ota_ops), sha-256
 *    (mbedtls), gates de heap interno y self-check de rollback.
 *  - Todos los publish MQTT (eventos aulasense_config + retains ota) se drenan desde el
 *    loop con app_ota_publish_pending() (unica dueña del transporte, norma N1).
 *
 * Comentarios en español, identificadores en inglés (regla del repo AulaSense).
 */
#include "app_ota.h"

#include "sdkconfig.h"

#include <stdio.h>
#include <stdlib.h> /* strtoull (manifiesto) */
#include <stdint.h>
#include <string.h>

#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mbedtls/sha256.h"
#include "nvs.h"

#include "app_feedback.h" /* pausa de telemetria durante la descarga (SPEC §16.1) */
#include "app_imagen.h"   /* app_imagen_pausar() */

static const char *TAG = "aulasense.ota";

#ifndef CONFIG_AULASENSE_OTA
#error "AulaSense: falta CONFIG_AULASENSE_OTA. Copia Kconfig.projbuild del parche a main/."
#endif

/* Guarda: la version local debe caber en los payloads y comparar con el manifiesto. */
_Static_assert(sizeof(CONFIG_AULASENSE_FW_VERSION) >= 2 &&
                   sizeof(CONFIG_AULASENSE_FW_VERSION) <= 32,
               "AulaSense: AULASENSE_FW_VERSION debe ser un string de 1..31 caracteres.");

#if !CONFIG_AULASENSE_OTA

/* Modulo apagado por Kconfig: stubs para que app_main.cpp enlace sin #ifdef en el vendor. */

void app_ota_set_transport(app_ota_subscribe_fn subscribe,
                           app_ota_publish_topic_fn publish_topic,
                           app_ota_publish_config_fn publish_config,
                           app_ota_connected_fn connected) {
    (void)subscribe;
    (void)publish_topic;
    (void)publish_config;
    (void)connected;
}

void app_ota_set_runtime_ready(app_ota_runtime_ready_fn runtime_ready) { (void)runtime_ready; }

void app_ota_set_identity(const char *device_id, const char *topic_prefix) {
    (void)device_id;
    (void)topic_prefix;
}

void app_ota_on_message(const char *topic, const char *payload) {
    (void)topic;
    (void)payload;
}

bool app_ota_init(void) {
    ESP_LOGI(TAG, "ota deshabilitada por Kconfig (AULASENSE_OTA=n)");
    return false;
}

void app_ota_publish_pending(void) {}

#else /* CONFIG_AULASENSE_OTA */

/* ------------------------------------------------------------------------------------------
 * Parametros del modulo
 * ---------------------------------------------------------------------------------------- */

/* Tarea prio 4 (< loop=5, ENMIENDA E7). Stack 8192: esp_http_client + lwIP + esp_ota corren
 * aqui (medido en el mismo patron que el httpd del portal, que vive con 8192). */
#define OTA_TASK_PRIORITY 4
#define OTA_TASK_STACK_SIZE 8192
#define OTA_CMD_QUEUE_LEN 4
#define OTA_EVT_QUEUE_LEN 6
#define OTA_EVT_JSON_CAP 256  /* F26-g1: 160->256 B de holgura del BUFFER. El detalle del
                               * ENVOLTORIO JSON (device_id+evento+timestamp ~66 B) cabe aqui.
                               * OJO: el CONTRATO de ingesta impone 120 chars al VALOR de
                               * `detalle` (config_eventos esquema.py:358 y retain ota :438) y
                               * lo descarta fail-closed si se excede: por eso OTA_INICIO ya NO
                               * incluye `url` (formato post_quiesce ~90 chars, ver accion_apply).
                               * Mantener TODO detalle de placa <=120 chars es invariante. */
#define OTA_RETAIN_JSON_CAP 448
#define OTA_TOPIC_MAX 128
#define OTA_MANIFEST_RESP_MAX 1024
#define OTA_URL_MAX 192
#define OTA_VERSION_MAX 32
#define OTA_SHA_HEX_LEN 64
#define OTA_CHUNK_BYTES 4096U               /* buffer de descarga/escritura en PSRAM */
#define OTA_PROGRESS_BYTES (256U * 1024U)   /* cadencia de refresco del retain downloading */
#define OTA_SELFCHECK_POLL_MS 1000U         /* tick del self-check (deadline+ventana) */
#define OTA_SELFCHECK_GRACE_S 120U          /* gracia extra sobre la ventana antes de revertir */
#define OTA_RESTART_DRAIN_MS 2000U          /* drenar outbox del OTA_OK antes de esp_restart */
#define NVS_NS_OTA "aulasense_ota"          /* version+sha de la imagen instalada por OTA */
#define NVS_KEY_FW_VER "fw_ver"
#define NVS_KEY_FW_SHA "fw_sha"
#define OTA_EVT_REINTENTOS_MAX 120U         /* F26-c3: reintentos ESPACIADOS 1 s del anillo
                                             * (cubre la ventana de ~16 s hasta el primer
                                             * connected() del arranque) */

/* Item del anillo de eventos aulasense_config (F26-c3: reintento con marca temporal). */
typedef struct {
    char json[OTA_EVT_JSON_CAP];
    int64_t sig_intento_ms; /* 0 = intentable ya; >0 = no reintentar antes de esa marca */
    uint8_t intentos;       /* reintentos consumidos */
} ota_evt_item_t;

/* Tipos de trabajo encolados desde el callback del transporte / timer periodico. */
typedef enum {
    OTA_CMD_CHECK = 0, /* {"action":"check"}  desde comando */
    OTA_CMD_APPLY = 1, /* {"action":"apply"} desde comando */
    OTA_CMD_PERIODIC = 2, /* timer AULASENSE_OTA_CHECK_MIN (solo publica OTA_CHECK) */
} ota_cmd_tipo_t;

typedef struct {
    uint8_t tipo;
    char command_id[40];
} ota_cmd_t;

/* ------------------------------------------------------------------------------------------
 * Estado del modulo
 * ---------------------------------------------------------------------------------------- */

static app_ota_subscribe_fn s_subscribe_fn = NULL;
static app_ota_publish_topic_fn s_publish_topic_fn = NULL;
static app_ota_publish_config_fn s_publish_config_fn = NULL;
static app_ota_connected_fn s_connected_fn = NULL;
static app_ota_runtime_ready_fn s_runtime_ready_fn = NULL;

static char s_cmd_topic[OTA_TOPIC_MAX] = "";   /* {base}/aulasense_ota */
static char s_state_topic[OTA_TOPIC_MAX] = ""; /* {base}/ota (retain) */

static QueueHandle_t s_cmd_queue = NULL;
static QueueHandle_t s_evt_queue = NULL; /* anillo de payloads cortos hacia aulasense_config */
static TaskHandle_t s_task = NULL;
static bool s_listo = false;             /* modulo operativo (transportes + topicos armados) */
static volatile bool s_en_curso = false; /* check/apply en vuelo: sin solapamiento */

/* Manifiesto cacheado por el ultimo check valido (apply lo reutiliza). */
static char s_man_version[OTA_VERSION_MAX] = "";
static char s_man_url[OTA_URL_MAX] = "";
static char s_man_sha[OTA_SHA_HEX_LEN + 1] = "";
static unsigned long long s_man_size = 0ULL;
static bool s_man_valido = false;

/* Retain de estado `ota`: unica ventana de valor; se re-publica al cambiar y en cada edge
 * de conexion MQTT (mismo truco que imagen_estado: el fallo de arranque no se pierde).
 * s_retain_mux: la ventana la escribe la tarea OTA y la lee el pump del loop. */
static portMUX_TYPE s_retain_mux = portMUX_INITIALIZER_UNLOCKED;
static char s_retain_json[OTA_RETAIN_JSON_CAP] = "";
static volatile bool s_retain_dirty = false;
static bool s_prev_connected = false;
static bool s_firmware_emitido = false;
static char s_build_sha_prefix[16] = ""; /* NVS si la imagen llego por OTA; "uart" si no */

/* Self-check de rollback (D-F26-3). */
static bool s_sc_activo = false;
static int64_t s_sc_inicio_ms = 0;
static int64_t s_sc_buenos_desde_ms = 0;

static esp_timer_handle_t s_periodic_timer = NULL;

/* ------------------------------------------------------------------------------------------
 * Helpers de publicacion (todo sale por el pump del loop; aqui solo se encola)
 * ---------------------------------------------------------------------------------------- */

static void ota_evento(const char *evento, const char *detalle) {
    if (s_evt_queue == NULL) {
        return;
    }
    ota_evt_item_t ev;
    ev.sig_intento_ms = 0;
    ev.intentos = 0;
    const long long ts_ms = (long long)(esp_timer_get_time() / 1000LL);
    const int n = snprintf(ev.json, sizeof(ev.json),
                           "{\"timestamp_ms\":%lld,\"evento\":\"%s\",\"detalle\":\"%s\"}", ts_ms,
                           evento, detalle);
    if (n <= 0 || n >= (int)sizeof(ev.json)) {
        ESP_LOGW(TAG, "payload del evento %s no cabe: se omite", evento);
        return;
    }
    if (xQueueSend(s_evt_queue, &ev, 0) != pdTRUE) {
        ESP_LOGW(TAG, "cola de eventos OTA llena: %s descartado (queda en serial)", evento);
    }
}

static void ota_estado(const char *estado, const char *version_objetivo, bool sha256_ok,
                       const char *detalle) {
    char json[OTA_RETAIN_JSON_CAP];
    const long long ts_ms = (long long)(esp_timer_get_time() / 1000LL);
    const int n = snprintf(json, sizeof(json),
                           "{\"estado\":\"%s\",\"version_actual\":\"%s\",\"version_objetivo\":\"%s\","
                           "\"sha256_ok\":%s,\"detalle\":\"%s\",\"ts_ms\":%lld}",
                           estado, CONFIG_AULASENSE_FW_VERSION,
                           version_objetivo != NULL ? version_objetivo : "",
                           sha256_ok ? "true" : "false",
                           detalle != NULL ? detalle : "", ts_ms);
    if (n <= 0 || n >= (int)sizeof(json)) {
        ESP_LOGW(TAG, "retain ota no cabe (%d): se conserva el anterior", n);
        return;
    }
    /* Seccion critica minima: construir fuera y copiar punteros aqui (sin llamadas dentro). */
    portENTER_CRITICAL(&s_retain_mux);
    memcpy(s_retain_json, json, sizeof(s_retain_json));
    s_retain_dirty = true;
    portEXIT_CRITICAL(&s_retain_mux);
}

/* ------------------------------------------------------------------------------------------
 * Gates de heap INTERNO (SPEC §16.1: nunca el free_memory_kb de diagnosticos, que es PSRAM)
 * ---------------------------------------------------------------------------------------- */

static bool gate_heap(unsigned *free_kb_out, unsigned *block_kb_out) {
    const size_t libre = heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    const size_t mayor = heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (free_kb_out != NULL) {
        *free_kb_out = (unsigned)(libre / 1024U);
    }
    if (block_kb_out != NULL) {
        *block_kb_out = (unsigned)(mayor / 1024U);
    }
    return libre >= (size_t)CONFIG_AULASENSE_OTA_HEAP_MIN_KB * 1024U &&
           mayor >= (size_t)CONFIG_AULASENSE_OTA_BLOCK_MIN_KB * 1024U;
}

/* ------------------------------------------------------------------------------------------
 * Manifiesto JSON (formato controlado por nosotros: {version,url,sha256,size})
 *
 * Parser manual MINIMO en vez de cJSON: el manifiesto lo genera el repo de la build con un
 * esquema fijo y compacto (SPEC §16.1); evitar un include nuevo es coherente con la ENMIENDA
 * E8 (cero deps). Solo acepta string/entero sin espacios dentro del valor; cualquier bicho
 * => campo no valido y la operacion aborta (fail-closed, nunca se flashea "por si acaso").
 * ---------------------------------------------------------------------------------------- */

static bool json_str(const char *json, const char *key, char *out, size_t n) {
    char patron[64];
    if (strlen(key) + 5U >= sizeof(patron)) {
        return false;
    }
    snprintf(patron, sizeof(patron), "\"%s\":\"", key);
    const char *p = strstr(json, patron);
    if (p == NULL) {
        return false;
    }
    p += strlen(patron);
    const char *fin = strchr(p, '"');
    if (fin == NULL) {
        return false;
    }
    const size_t len = (size_t)(fin - p);
    if (len == 0U || len + 1U > n) {
        return false;
    }
    memcpy(out, p, len);
    out[len] = '\0';
    return true;
}

static bool json_u64(const char *json, const char *key, unsigned long long *out) {
    char patron[64];
    if (strlen(key) + 4U >= sizeof(patron)) {
        return false;
    }
    snprintf(patron, sizeof(patron), "\"%s\":", key);
    const char *p = strstr(json, patron);
    if (p == NULL) {
        return false;
    }
    p += strlen(patron);
    char *fin = NULL;
    const unsigned long long v = strtoull(p, &fin, 10);
    if (fin == p || v == 0ULL) {
        return false;
    }
    *out = v;
    return true;
}

static bool sha_hex_valido(const char *sha) {
    if (strlen(sha) != OTA_SHA_HEX_LEN) {
        return false;
    }
    for (size_t i = 0; i < OTA_SHA_HEX_LEN; ++i) {
        const char c = sha[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
            return false; /* el contrato pide HEX MINUSCULA (SPEC §16.1) */
        }
    }
    return true;
}

/* GET del manifiesto (HTTP plano permitido: NO usamos esp_https_ota, D3 del dueño). */
static esp_err_t manifiesto_descargar(char *resp, size_t resp_n) {
    const char *url = CONFIG_AULASENSE_OTA_MANIFEST_URL;
    if (url[0] == '\0') {
        return ESP_ERR_INVALID_ARG; /* modulo inerte por config (SPEC §16.1) */
    }
    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_GET,
        .timeout_ms = 8000,
        .disable_auto_redirect = true,
        .buffer_size = 512,
    };
    esp_http_client_handle_t cliente = esp_http_client_init(&cfg);
    if (cliente == NULL) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = esp_http_client_open(cliente, 0);
    if (err == ESP_OK) {
        (void)esp_http_client_fetch_headers(cliente);
        if (esp_http_client_get_status_code(cliente) != 200) {
            err = ESP_FAIL;
        }
    }
    size_t total = 0;
    while (err == ESP_OK) {
        const int r = esp_http_client_read(cliente, resp + total, (int)(resp_n - total - 1U));
        if (r < 0) {
            err = ESP_FAIL;
            break;
        }
        if (r == 0) {
            break; /* EOF */
        }
        total += (size_t)r;
        if (total + 1U >= resp_n) {
            err = ESP_ERR_INVALID_SIZE; /* manifiesto absurdo: >1 KB */
            break;
        }
    }
    if (err == ESP_OK && total == 0U) {
        err = ESP_FAIL;
    }
    resp[total] = '\0';
    esp_http_client_cleanup(cliente);
    return err;
}

static bool manifiesto_parsear(const char *resp) {
    s_man_valido = false;
    s_man_version[0] = '\0';
    s_man_url[0] = '\0';
    s_man_sha[0] = '\0';
    s_man_size = 0ULL;
    if (!json_str(resp, "version", s_man_version, sizeof(s_man_version))) {
        return false;
    }
    if (!json_str(resp, "url", s_man_url, sizeof(s_man_url))) {
        return false;
    }
    if (!json_str(resp, "sha256", s_man_sha, sizeof(s_man_sha)) || !sha_hex_valido(s_man_sha)) {
        return false;
    }
    if (!json_u64(resp, "size", &s_man_size) || s_man_size < 4096ULL) {
        return false;
    }
    if (strncmp(s_man_url, "http://", 7) != 0 && strncmp(s_man_url, "https://", 8) != 0) {
        return false;
    }
    s_man_valido = true;
    return true;
}

/* ------------------------------------------------------------------------------------------
 * Acciones: check / apply
 * ---------------------------------------------------------------------------------------- */

static bool accion_check(const char *origen, const char *command_id) {
    char resp[OTA_MANIFEST_RESP_MAX];
    char detalle[OTA_EVT_JSON_CAP];
    const esp_err_t err = manifiesto_descargar(resp, sizeof(resp));
    if (err != ESP_OK) {
        snprintf(detalle, sizeof(detalle), "origen=%s err=%s url_cfg=%s", origen,
                 esp_err_to_name(err),
                 CONFIG_AULASENSE_OTA_MANIFEST_URL[0] == '\0' ? "vacia (modulo inerte)" : "presente");
        ota_evento("OTA_ERROR", detalle);
        ota_estado("error", NULL, false, "manifiesto no disponible");
        return false;
    }
    if (!manifiesto_parsear(resp)) {
        ota_evento("OTA_ERROR", "origen=manifiesto err=esquema_invalido");
        ota_estado("error", NULL, false, "manifiesto invalido");
        return false;
    }
    const bool hay = strcmp(s_man_version, CONFIG_AULASENSE_FW_VERSION) != 0;
    snprintf(detalle, sizeof(detalle), "origen=%s id=%s local=%s fw_remota=%s hay_actualizacion=%s",
             origen, command_id != NULL ? command_id : "-", CONFIG_AULASENSE_FW_VERSION,
             s_man_version, hay ? "true" : "false");
    ota_evento("OTA_CHECK", detalle);
    ota_estado("idle", s_man_version, false, hay ? "actualizacion disponible" : "sin cambios");
    return hay;
}

/* Fase final comun: guarda NVS (para el FIRMWARE.build_sha256_prefix del proximo arranque). */
static void nvs_guardar_installed(const char *version, const char *sha) {
    nvs_handle_t h;
    if (nvs_open(NVS_NS_OTA, NVS_READWRITE, &h) == ESP_OK) {
        (void)nvs_set_str(h, NVS_KEY_FW_VER, version);
        (void)nvs_set_str(h, NVS_KEY_FW_SHA, sha);
        (void)nvs_commit(h);
        nvs_close(h);
    }
}

/* Descarga + escritura del slot con SHA-256 ACUMULADO EN STREAMING (decision D-F26-1:
 * el hash cubre exactamente los bytes escritos; evitar releer 1.4 MB del slot. La
 * validacion de cabecera la hace esp_ota_end antes de comparar el digest). */
static esp_err_t descargar_a_slot(const esp_partition_t *destino, size_t *escrito_out,
                                  char *etapa_out, size_t etapa_n) {
    unsigned char digest[32];
    mbedtls_sha256_context sha_ctx;
    mbedtls_sha256_init(&sha_ctx);
    int rc = mbedtls_sha256_starts(&sha_ctx, 0 /* SHA-256, no 224 */);
    if (rc != 0) {
        snprintf(etapa_out, etapa_n, "sha_init");
        mbedtls_sha256_free(&sha_ctx);
        return ESP_FAIL;
    }

    esp_app_desc_t nuevo_desc; /* para el log de la version empaquetada */
    memset(&nuevo_desc, 0, sizeof(nuevo_desc));

    esp_ota_handle_t handle = 0;
    esp_err_t err = esp_ota_begin(destino, OTA_SIZE_UNKNOWN, &handle);
    if (err != ESP_OK) {
        snprintf(etapa_out, etapa_n, "begin");
        mbedtls_sha256_free(&sha_ctx);
        return err;
    }

    esp_http_client_config_t cfg = {
        .url = s_man_url,
        .method = HTTP_METHOD_GET,
        .timeout_ms = 15000,
        .disable_auto_redirect = true,
        .buffer_size = 1024,
    };
    esp_http_client_handle_t cliente = esp_http_client_init(&cfg);
    if (cliente == NULL) {
        err = ESP_ERR_NO_MEM;
        (void)esp_ota_abort(handle);
        snprintf(etapa_out, etapa_n, "http_init");
        goto fin_sha;
    }

    uint8_t *buf = heap_caps_malloc(OTA_CHUNK_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (buf == NULL) {
        err = ESP_ERR_NO_MEM;
        esp_http_client_cleanup(cliente);
        (void)esp_ota_abort(handle);
        snprintf(etapa_out, etapa_n, "buffer_psram");
        goto fin_sha;
    }

    size_t total = 0;
    size_t desde_progreso = 0;
    err = esp_http_client_open(cliente, 0);
    if (err == ESP_OK) {
        (void)esp_http_client_fetch_headers(cliente);
        if (esp_http_client_get_status_code(cliente) != 200) {
            err = ESP_FAIL;
            snprintf(etapa_out, etapa_n, "http_status");
        }
    } else {
        snprintf(etapa_out, etapa_n, "http_open");
    }
    while (err == ESP_OK) {
        const int r = esp_http_client_read(cliente, (char *)buf, (int)OTA_CHUNK_BYTES);
        if (r < 0) {
            err = ESP_FAIL;
            snprintf(etapa_out, etapa_n, "http_read");
            break;
        }
        if (r == 0) {
            break; /* EOF */
        }
        if (s_man_size != 0ULL && total + (size_t)r > s_man_size) {
            err = ESP_ERR_INVALID_SIZE; /* el servidor manda mas de lo prometido */
            snprintf(etapa_out, etapa_n, "size_exceso");
            break;
        }
        (void)mbedtls_sha256_update(&sha_ctx, buf, (size_t)r);
        err = esp_ota_write(handle, buf, (size_t)r);
        if (err != ESP_OK) {
            snprintf(etapa_out, etapa_n, "ota_write");
            break;
        }
        total += (size_t)r;
        desde_progreso += (size_t)r;
        if (desde_progreso >= OTA_PROGRESS_BYTES) {
            desde_progreso = 0;
            char detalle[64];
            snprintf(detalle, sizeof(detalle), "%u/%u B", (unsigned)total, (unsigned)s_man_size);
            ota_estado("downloading", s_man_version, false, detalle);
        }
    }
    heap_caps_free(buf);
    esp_http_client_cleanup(cliente);

    if (err == ESP_OK && total == 0U) {
        err = ESP_FAIL;
        snprintf(etapa_out, etapa_n, "respuesta_vacia");
    }
    if (err == ESP_OK && s_man_size != 0ULL && total != s_man_size) {
        err = ESP_ERR_INVALID_SIZE;
        snprintf(etapa_out, etapa_n, "size_corto");
    }
    if (err != ESP_OK) {
        (void)esp_ota_abort(handle);
        goto fin_sha;
    }

    /* esp_ota_end valida cabecera+secciones ANTES de nuestra comparacion de digest. */
    err = esp_ota_end(handle);
    if (err != ESP_OK) {
        snprintf(etapa_out, etapa_n, "end");
        goto fin_sha;
    }
    mbedtls_sha256_finish(&sha_ctx, digest);

    char hex[OTA_SHA_HEX_LEN + 1];
    static const char kHex[] = "0123456789abcdef";
    for (size_t i = 0; i < sizeof(digest); ++i) {
        hex[2 * i] = kHex[digest[i] >> 4];
        hex[2 * i + 1] = kHex[digest[i] & 0x0F];
    }
    hex[OTA_SHA_HEX_LEN] = '\0';
    if (strcmp(hex, s_man_sha) != 0) {
        err = ESP_ERR_INVALID_CRC;
        snprintf(etapa_out, etapa_n, "sha256");
    } else {
        if (esp_ota_get_partition_description(destino, &nuevo_desc) == ESP_OK) {
            ESP_LOGI(TAG, "bin verificado: project=%s version=%s", nuevo_desc.project_name,
                     nuevo_desc.version);
        }
        *escrito_out = total;
    }

fin_sha:
    /* Unico punto de liberacion del contexto SHA (evita el doble free de la version inicial). */
    mbedtls_sha256_free(&sha_ctx);
    return err;
}

static void accion_apply(void) {
    char detalle[OTA_EVT_JSON_CAP];
    char etapa[24] = "gate_heap";
    unsigned libre_kb = 0, bloque_kb = 0;

    /* 1) Manifiesto fresco (o cacheado de un check previo si aun sirve). Operacion barata:
     *    ni toca el slot ni consume heap grande, va ANTES del gate. */
    if (!s_man_valido) {
        (void)accion_check("apply", NULL);
    }
    if (!s_man_valido) {
        return; /* OTA_ERROR ya publicado por check */
    }
    if (strcmp(s_man_version, CONFIG_AULASENSE_FW_VERSION) == 0) {
        ota_evento("OTA_ERROR", "etapa=sin_cambio version=local");
        ota_estado("idle", s_man_version, false, "la version objetivo es la actual");
        return;
    }

    const esp_partition_t *destino = esp_ota_get_next_update_partition(NULL);
    if (destino == NULL) {
        ota_evento("OTA_ERROR", "etapa=particion err=no_ota_slot");
        ota_estado("error", s_man_version, false, "sin slot inactivo (tabla? no es dual-app)");
        return;
    }

    /* 2) QUIESCE PRIMERO (F26-g1 decision del orquestador, condicion 1a): la pausa de
     *    captura+telemetria precede al gate para que este se mida sobre el heap del régimen
     *    MAS TRANQUILO posible. La foto es accesoria y el "agujero" de telemetria es el
     *    esperado y documentado (SPEC §16.1); si luego el gate falla, se reanuda todo. */
    app_imagen_pausar(true);
    app_feedback_pausar(true);

    /* 3) Gates de heap interno POST-QUIESCE (si fallan, NO se descarga nada: reanudar y
     *    salir con el slot intacto). */
    if (!gate_heap(&libre_kb, &bloque_kb)) {
        snprintf(detalle, sizeof(detalle),
                 "etapa=gate_heap post_quiesce internal_free_kb=%u internal_largest_block_kb=%u min=%d/%d",
                 libre_kb, bloque_kb, CONFIG_AULASENSE_OTA_HEAP_MIN_KB,
                 CONFIG_AULASENSE_OTA_BLOCK_MIN_KB);
        ota_evento("OTA_ERROR", detalle);
        ota_estado("error", NULL, false, "gate heap interno (post-quiesce)");
        app_imagen_pausar(false);
        app_feedback_pausar(false);
        return;
    }

    /* 4) OTA_INICIO con los NUMEROS post-quiesce visibles en BD (evidencia automaticamente).
     *    SIN url: el contrato de ingesta (esquema.py) descarta detalles de config >120 chars y
     *    el dato ya vive en el manifest/OTA_CHECK. Los numeros post-quiesce son la evidencia. */
    snprintf(detalle, sizeof(detalle),
             "version_objetivo=%s size=%llu slot=%s post_quiesce free=%uKB largest=%uKB",
             s_man_version, s_man_size, destino->label, libre_kb, bloque_kb);
    ota_evento("OTA_INICIO", detalle);
    ota_estado("downloading", s_man_version, false, "0 B");

    /* 5) Descarga + escritura + sha/size. */
    size_t escrito = 0;
    esp_err_t err = descargar_a_slot(destino, &escrito, etapa, sizeof(etapa));
    if (err != ESP_OK) {
        snprintf(detalle, sizeof(detalle), "etapa=%s esp_err=%s bytes=%u", etapa,
                 esp_err_to_name(err), (unsigned)escrito);
        ota_evento("OTA_ERROR", detalle);
        ota_estado("error", s_man_version, false, etapa);
        /* El slot ACTUAL queda intacto: nunca tocamos set_boot. Reanudar lo pausado. */
        app_imagen_pausar(false);
        app_feedback_pausar(false);
        return;
    }

    /* 5) Solo AHORA el cambio de slot de arranque (con rollback: PENDING_VERIFY al arrancar). */
    err = esp_ota_set_boot_partition(destino);
    if (err != ESP_OK) {
        snprintf(detalle, sizeof(detalle), "etapa=set_boot esp_err=%s", esp_err_to_name(err));
        ota_evento("OTA_ERROR", detalle);
        ota_estado("error", s_man_version, true, "set_boot fallo: slot sin cambiar");
        app_imagen_pausar(false);
        app_feedback_pausar(false);
        return;
    }

    nvs_guardar_installed(s_man_version, s_man_sha);
    snprintf(detalle, sizeof(detalle), "version=%s sha256_ok=true bytes=%u", s_man_version,
             (unsigned)escrito);
    ota_evento("OTA_OK", detalle);
    ota_estado("rebooting", s_man_version, true, "reinicio en 2 s (agujero de telemetria esperado)");

    /* 6) Reiniciar. El delay deja salir el OTA_OK + retain por el outbox (QoS 0, TCP ya
     *    aceptado por el broker en condiciones normales; si se pierde, el self-check del
     *    arranque nuevo re-publica todo — decision documentada). */
    vTaskDelay(pdMS_TO_TICKS(OTA_RESTART_DRAIN_MS));
    esp_restart();
}

/* ------------------------------------------------------------------------------------------
 * Self-check de rollback (D-F26-3)
 * ---------------------------------------------------------------------------------------- */

static bool wifi_tiene_ip(void) {
    esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t ip = {0};
    return netif != NULL && esp_netif_get_ip_info(netif, &ip) == ESP_OK && ip.ip.addr != 0U;
}

static bool selfcheck_condiciones(void) {
    const bool mqtt_ok = (s_connected_fn != NULL) ? s_connected_fn() : false;
    const bool runtime_ok = (s_runtime_ready_fn != NULL) ? s_runtime_ready_fn() : true;
    return wifi_tiene_ip() && mqtt_ok && runtime_ok;
}

static void selfcheck_tick(void) {
    const int64_t ahora_ms = esp_timer_get_time() / 1000LL;
    const int64_t ventana_ms = (int64_t)CONFIG_AULASENSE_OTA_SELFCHECK_S * 1000LL;
    const bool ok = selfcheck_condiciones();
    if (ok) {
        if (s_sc_buenos_desde_ms == 0) {
            s_sc_buenos_desde_ms = ahora_ms;
        }
        if (ahora_ms - s_sc_buenos_desde_ms >= ventana_ms) {
            const esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
            ESP_LOGI(TAG, "self-check OK (%d s estables): mark_app_valid -> %s",
                     CONFIG_AULASENSE_OTA_SELFCHECK_S, esp_err_to_name(err));
            ota_evento("OTA_OK", "detalle=self-check rollback cancelado");
            /* Estado terminal de CONTRATO tras mark_app_valid (SPEC §16.4: idle|checking|
             * downloading|verifying|rebooting|error). "valid" NO es estado de contrato y la
             * ingesta lo descartaba fail-closed; la validacion queda evidenciada por el
             * config_evento OTA_OK anterior + retain idle con sha256_ok=true. */
            ota_estado("idle", NULL, true, "self-check superado: imagen validada");
            s_sc_activo = false;
        }
        return;
    }
    s_sc_buenos_desde_ms = 0; /* la ventana exige condiciones CONTINUAS (SPEC §16.1) */
    if (ahora_ms - s_sc_inicio_ms >= ventana_ms + (int64_t)OTA_SELFCHECK_GRACE_S * 1000LL) {
        ESP_LOGE(TAG, "self-check NO superado: revierto al slot bueno (GOT_IP=%s mqtt=%s armed=%s)",
                 wifi_tiene_ip() ? "si" : "no",
                 (s_connected_fn != NULL && s_connected_fn()) ? "si" : "no",
                 (s_runtime_ready_fn == NULL || s_runtime_ready_fn()) ? "si" : "no");
        ota_evento("OTA_ERROR", "etapa=self-check accion=rollback-inmediato");
        /* esp_ota_mark_app_invalid_rollback_and_reboot(): marca la imagen INVALID y reinicia;
         * el bootloader arranca el slot bueno. Un esp_restart simple NO revertiria (el estado
         * seguiria PENDING_VERIFY y la imagen mala volveria a arrancar en bucle). */
        esp_ota_mark_app_invalid_rollback_and_reboot();
    }
}

/* ------------------------------------------------------------------------------------------
 * Timer periodico de chequeo (AULASENSE_OTA_CHECK_MIN; default 0 = desactivado)
 * ---------------------------------------------------------------------------------------- */

static void periodic_timer_cb(void *arg) {
    (void)arg;
    const ota_cmd_t cmd = {.tipo = (uint8_t)OTA_CMD_PERIODIC, .command_id = {0}};
    if (s_cmd_queue != NULL) {
        (void)xQueueSend(s_cmd_queue, &cmd, 0);
    }
}

/* ------------------------------------------------------------------------------------------
 * Tarea propia (prio 4)
 * ---------------------------------------------------------------------------------------- */

static void ota_task(void *arg) {
    (void)arg;
    ota_cmd_t cmd;
    const TickType_t espera =
        s_sc_activo ? pdMS_TO_TICKS(OTA_SELFCHECK_POLL_MS) : portMAX_DELAY;
    TickType_t espera_actual = espera;
    ESP_LOGI(TAG, "tarea OTA arrancada (prio %d, stack %d)", OTA_TASK_PRIORITY, OTA_TASK_STACK_SIZE);
    for (;;) {
        if (xQueueReceive(s_cmd_queue, &cmd, espera_actual) == pdTRUE) {
            s_en_curso = true;
            switch ((ota_cmd_tipo_t)cmd.tipo) {
                case OTA_CMD_CHECK:
                    (void)accion_check("comando", cmd.command_id);
                    break;
                case OTA_CMD_APPLY:
                    accion_apply(); /* puede no volver: esp_restart() */
                    break;
                case OTA_CMD_PERIODIC:
                    if (CONFIG_AULASENSE_OTA_CHECK_MIN > 0) {
                        (void)accion_check("periodico", NULL);
                    }
                    break;
            }
            s_en_curso = false;
        }
        if (s_sc_activo) {
            selfcheck_tick();
            espera_actual = pdMS_TO_TICKS(OTA_SELFCHECK_POLL_MS);
        } else {
            espera_actual = portMAX_DELAY;
        }
    }
}

/* ------------------------------------------------------------------------------------------
 * Envolvente del comando: corre en la tarea del loop (prio 5) — solo valida y postea
 * ---------------------------------------------------------------------------------------- */

void app_ota_on_message(const char *topic, const char *payload) {
    (void)topic;
    if (s_cmd_queue == NULL || payload == NULL) {
        return;
    }
    if (strstr(payload, "\"command\":\"aulasense_ota\"") == NULL) {
        return; /* envelope de otro comando: silencioso a proposito (no spamear errores) */
    }
    char command_id[40];
    command_id[0] = '\0';
    (void)json_str(payload, "command_id", command_id, sizeof(command_id));
    /* command_id es string de auditoria 1-64 ASCII (SPEC §16.1): se sanitiza a un subconjunto
     * JSON-seguro antes de viajar en el detalle de OTA_CHECK (no puede romper el envelope). */
    for (size_t i = 0; i < sizeof(command_id); ++i) {
        const char c = command_id[i];
        if (c == '\0') {
            break;
        }
        const bool segura = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                            (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-';
        if (!segura) {
            command_id[i] = '.';
        }
    }
    ota_cmd_t cmd = {.command_id = {0}};
    if (strstr(payload, "\"action\":\"apply\"") != NULL) {
        cmd.tipo = (uint8_t)OTA_CMD_APPLY;
    } else if (strstr(payload, "\"action\":\"check\"") != NULL) {
        cmd.tipo = (uint8_t)OTA_CMD_CHECK;
    } else {
        if (s_evt_queue != NULL) {
            ota_evento("OTA_ERROR", "etapa=envelope accion=desconocida");
        }
        return;
    }
    memcpy(cmd.command_id, command_id, sizeof(cmd.command_id));
    if (s_en_curso) {
        ESP_LOGW(TAG, "comando OTA recibido con operacion en curso: ignorado");
        return;
    }
    if (xQueueSend(s_cmd_queue, &cmd, 0) != pdTRUE) {
        ESP_LOGW(TAG, "cola OTA llena: comando descartado");
    }
}

/* ------------------------------------------------------------------------------------------
 * Pump (loop de Native): eventos + retain + edge de conexion + FIRMWARE de arranque
 * ---------------------------------------------------------------------------------------- */

void app_ota_publish_pending(void) {
    if (!s_listo) {
        return;
    }
    ota_evt_item_t ev;
    if (s_evt_queue != NULL && s_publish_config_fn != NULL) {
        /* F26-c3: reintento espaciado 1 s con tope (ver app_imagen.c mismo fix). Los eventos
         * del arranque (FIRMWARE/OTA_*) ya no se pierden en la ventana pre-connected(). */
        const int64_t ahora_ms = esp_timer_get_time() / 1000LL;
        for (int n = 0; n < OTA_EVT_QUEUE_LEN; ++n) {
            if (xQueuePeek(s_evt_queue, &ev, 0) != pdTRUE) {
                break;
            }
            if (ev.sig_intento_ms > ahora_ms) {
                break; /* cabecera no madura: FIFO, los demas tampoco */
            }
            if (xQueueReceive(s_evt_queue, &ev, 0) != pdTRUE) {
                break;
            }
            if (s_publish_config_fn(ev.json)) {
                continue;
            }
            if (ev.intentos < OTA_EVT_REINTENTOS_MAX) {
                ev.intentos++;
                ev.sig_intento_ms = ahora_ms + 1000LL;
                if (xQueueSend(s_evt_queue, &ev, 0) != pdTRUE) {
                    ESP_LOGW(TAG, "evento OTA no re-encolable (cola lena): descartado");
                }
            } else {
                ESP_LOGW(TAG, "evento OTA no publicado (agotado): descartado (queda en serial)");
            }
        }
    }

    const bool conectado = (s_connected_fn != NULL) ? s_connected_fn() : false;
    if (conectado && !s_prev_connected) {
        s_retain_dirty = true; /* cada reconexion re-postea el retain ota (late subscriber) */
        if (!s_firmware_emitido) {
            char detalle[OTA_EVT_JSON_CAP];
            snprintf(detalle, sizeof(detalle),
                     "version=%s compile_time=%s %s idf=%s build_sha256_prefix=%s",
                     CONFIG_AULASENSE_FW_VERSION, __DATE__, __TIME__, esp_get_idf_version(),
                     s_build_sha_prefix[0] != '\0' ? s_build_sha_prefix : "uart-flash");
            if (s_evt_queue != NULL) {
                ota_evento("FIRMWARE", detalle);
            }
            ESP_LOGI(TAG, "FIRMWARE %s", detalle);
            s_firmware_emitido = true;
        }
    }
    s_prev_connected = conectado;

    if (s_retain_dirty && conectado && s_publish_topic_fn != NULL && s_state_topic[0] != '\0') {
        char snapshot[OTA_RETAIN_JSON_CAP];
        portENTER_CRITICAL(&s_retain_mux);
        const bool hay = s_retain_json[0] != '\0';
        if (hay) {
            memcpy(snapshot, s_retain_json, sizeof(snapshot));
        }
        portEXIT_CRITICAL(&s_retain_mux);
        if (hay && s_publish_topic_fn(s_state_topic, snapshot, true)) {
            portENTER_CRITICAL(&s_retain_mux);
            s_retain_dirty = false;
            portEXIT_CRITICAL(&s_retain_mux);
        }
    }
}

/* ------------------------------------------------------------------------------------------
 * API publica
 * ---------------------------------------------------------------------------------------- */

void app_ota_set_transport(app_ota_subscribe_fn subscribe,
                           app_ota_publish_topic_fn publish_topic,
                           app_ota_publish_config_fn publish_config,
                           app_ota_connected_fn connected) {
    s_subscribe_fn = subscribe;
    s_publish_topic_fn = publish_topic;
    s_publish_config_fn = publish_config;
    s_connected_fn = connected;
}

void app_ota_set_runtime_ready(app_ota_runtime_ready_fn runtime_ready) {
    s_runtime_ready_fn = runtime_ready;
}

void app_ota_set_identity(const char *device_id, const char *topic_prefix) {
    const char *prefijo =
        (topic_prefix != NULL && topic_prefix[0] != '\0') ? topic_prefix : CONFIG_ESPECTRE_TOPIC_PREFIX;
    char base[OTA_TOPIC_MAX - 32];
    size_t n = strlen(prefijo);
    while (n > 0 && prefijo[n - 1] == '/') {
        --n;
    }
    if (device_id == NULL || device_id[0] == '\0' || n == 0U) {
        s_cmd_topic[0] = '\0';
        s_state_topic[0] = '\0';
        return;
    }
    const int escrito = snprintf(base, sizeof(base), "%.*s/%s", (int)n, prefijo, device_id);
    if (escrito <= 0 || escrito >= (int)sizeof(base)) {
        s_cmd_topic[0] = '\0';
        s_state_topic[0] = '\0';
        return;
    }
    snprintf(s_cmd_topic, sizeof(s_cmd_topic), "%s/aulasense_ota", base);
    snprintf(s_state_topic, sizeof(s_state_topic), "%s/ota", base);
}

bool app_ota_init(void) {
    if (s_task != NULL) {
        return s_listo;
    }

    nvs_handle_t h;
    if (nvs_open(NVS_NS_OTA, NVS_READONLY, &h) == ESP_OK) {
        char sha[OTA_SHA_HEX_LEN + 1] = "";
        size_t l = sizeof(sha);
        if (nvs_get_str(h, NVS_KEY_FW_SHA, sha, &l) == ESP_OK && strlen(sha) >= 12U) {
            memcpy(s_build_sha_prefix, sha, 12U);
            s_build_sha_prefix[12] = '\0';
        }
        nvs_close(h);
    }

    s_cmd_queue = xQueueCreate(OTA_CMD_QUEUE_LEN, sizeof(ota_cmd_t));
    s_evt_queue = xQueueCreate(OTA_EVT_QUEUE_LEN, sizeof(ota_evt_item_t));
    if (s_cmd_queue == NULL || s_evt_queue == NULL) {
        ESP_LOGE(TAG, "sin colas: modulo OTA apagado");
        return false;
    }

    /* Suscripcion al comando propio (API publica; sobrevive reconexiones por contrato del
     * transporte). Con suscripcion fallida el modulo sigue sirviendo el self-check/retains. */
    if (s_subscribe_fn != NULL && s_cmd_topic[0] != '\0') {
        if (!s_subscribe_fn(s_cmd_topic, app_ota_on_message)) {
            ESP_LOGE(TAG, "no pude suscribir %s: OTA por comando inoperativo", s_cmd_topic);
        }
    } else {
        ESP_LOGW(TAG, "sin topic de comando (identity no registrada): OTA por comando inerte");
    }

    /* Rollback pendiente? (arranque tras esp_ota_set_boot_partition con bootloader rollback). */
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t estado_img = ESP_OTA_IMG_UNDEFINED;
    if (esp_ota_get_state_partition(running, &estado_img) == ESP_OK &&
        estado_img == ESP_OTA_IMG_PENDING_VERIFY) {
#if CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
        s_sc_activo = true;
        s_sc_inicio_ms = esp_timer_get_time() / 1000LL;
        ESP_LOGW(TAG, "imagen en PENDING_VERIFY: self-check de rollback activo (%d s estables, "
                     "gracia %u s)",
                 CONFIG_AULASENSE_OTA_SELFCHECK_S, (unsigned)OTA_SELFCHECK_GRACE_S);
        /* Estado del CONTRATO (SPEC §16.4): "verifying" es la ventana de self-check. Usar
         * "pending_verify" lo descartaba la ingesta (whitelist fail-closed esquema.py:416). */
        ota_estado("verifying", NULL, false, "self-check rollback pendiente");
#else
        ESP_LOGE(TAG, "imagen PENDING_VERIFY pero el rollback esta DESACTIVADO en Kconfig");
#endif
    }

    if (CONFIG_AULASENSE_OTA_CHECK_MIN > 0) {
        const esp_timer_create_args_t targs = {
            .callback = periodic_timer_cb,
            .name = "aulasense_otacheck",
        };
        if (esp_timer_create(&targs, &s_periodic_timer) == ESP_OK) {
            const uint64_t periodo_us =
                (uint64_t)CONFIG_AULASENSE_OTA_CHECK_MIN * 60ULL * 1000000ULL;
            (void)esp_timer_start_periodic(s_periodic_timer, periodo_us);
            ESP_LOGI(TAG, "chequeo periodico de manifiesto cada %d min (solo OTA_CHECK)",
                     CONFIG_AULASENSE_OTA_CHECK_MIN);
        }
    }

    if (CONFIG_AULASENSE_OTA_MANIFEST_URL[0] == '\0') {
        ESP_LOGW(TAG, "AULASENSE_OTA_MANIFEST_URL vacia: modulo INERTE para descargas "
                      "(comando check/apply respondera con OTA_ERROR hasta el proximo build "
                      "con .env -> sdkconfig.defaults.local)");
    }

    if (xTaskCreate(ota_task, "aulasense_ota", OTA_TASK_STACK_SIZE, NULL, OTA_TASK_PRIORITY,
                    &s_task) != pdPASS) {
        s_task = NULL;
        ESP_LOGE(TAG, "no se pudo crear la tarea OTA");
        return false;
    }

    ota_estado("idle", NULL, false, CONFIG_AULASENSE_OTA_MANIFEST_URL[0] == '\0'
                                       ? "sin manifiesto configurado"
                                       : "listo");
    s_listo = true;
    ESP_LOGI(TAG, "ota listo: comando=%s retain=%s gates=%d/%d KB selfcheck=%d s check_min=%d",
             s_cmd_topic, s_state_topic, CONFIG_AULASENSE_OTA_HEAP_MIN_KB,
             CONFIG_AULASENSE_OTA_BLOCK_MIN_KB, CONFIG_AULASENSE_OTA_SELFCHECK_S,
             CONFIG_AULASENSE_OTA_CHECK_MIN);
    return true;
}

#endif /* CONFIG_AULASENSE_OTA */
