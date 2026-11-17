/*
 * app_provision.c — AulaSense (parche F24-d; F26: provisioning FIJO sin token, SPEC §16.2):
 * AP de emergencia con SSID/pass fijos + portal en rutas explícitas /cfg, /cfg/estado, /cfg/wifi.
 *
 * Calco ENDURECIDO del patrón DriveGuard (archive/dashcam-ia/firmware/main/app_portal.c,
 * F17/F18-A) adaptado al Native según SPEC §16.2 y las ENMIENDAS E2/E3/E4/E7. Contrato
 * completo en app_provision.h y README §14 (F26) / §13 (F24-d) de este directorio.
 *
 * Cambios F26 (D-F26-4):
 *  - SSID fijo CONFIG_AULASENSE_PROVISION_AP_SSID (default "AULASENSE-CFG") SIN sufijo MAC
 *    (una sola unidad en el proyecto; colisión multi-placa = procedimiento 1-placa-a-la-vez).
 *  - Portal SIN token: eliminado el mecanismo completo (generacion por codigo, variable,
 *    validacion de ruta y comparacion anti-timing). La clave NVS antigua del token NO se borra
 *    en campo (inofensiva, ya no se usa; D-F26-4).
 *  - Rutas EXPLÍCITAS GET /cfg (form), GET /cfg/estado (JSON), POST /cfg/wifi con
 *    max_uri_handlers=4 (el matcher comodin prefijo-asterisco no casa "/cfg" sin barra
 *    — informe A §1.2.4).
 *  - AP_ERROR publicado en TODAS las ramas de fallo de ap_levantar() (set_mode/set_config/
 *    wifi_start/httpd): el recovery jamás queda mudo (CRÍTICO informe A §1.1).
 *  - Guarda de compilación: pass no vacía ⇒ longitud 8..63 (rechaza build si 1..7; "itse" de
 *    4 chars producía set_config failing + AP mudo — informe A §1.1).
 *  - Guardas HEREDADAS INTACTAS: Origin/Referer (403), cooldown NVS 5 s (429), body ≤255 B,
 *    escritura solo si cambia, cabeceras duras, NUNCA loguear SSID/pass (solo longitudes).
 *
 * NO toca: el detector CSI, el canal MQTT existente (solo re-usa el publisher inyectado),
 * la mecánica de imágenes (Fix F/F24-c/F25-c/F26) ni el recovery nativo (BOOT 3 s → Improv).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_heap_caps.h"  /* heap/psram libre para /cfg/estado */
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "nvs.h"
#include "sdkconfig.h"

#include "esp_http_server.h" /* ya en REQUIRES del main (ENMIENDA E8: cero deps nuevas) */

#include "app_provision.h"

static const char *TAG = "aulasense.provision";

#ifndef CONFIG_AULASENSE_PROVISION
#error "AulaSense: falta CONFIG_AULASENSE_PROVISION. Copia Kconfig.projbuild del parche a main/."
#endif

/* Guardas de rango (el archive app_portal.c las delegaba al Kconfig; aqui se re-validan). */
#if CONFIG_AULASENSE_PROVISION_RETRIES < 1 || CONFIG_AULASENSE_PROVISION_RETRIES > 10
#error "AulaSense: AULASENSE_PROVISION_RETRIES debe estar en 1..10 (umbral DEL MODULO, E2)."
#endif
#if CONFIG_AULASENSE_PROVISION_AP_OFF_S < 5 || CONFIG_AULASENSE_PROVISION_AP_OFF_S > 600
#error "AulaSense: AULASENSE_PROVISION_AP_OFF_S debe estar en 5..600."
#endif
/* F26 (informe A §1.1): pass no vacía ⇒ WPA2 exige 8..63 chars. sizeof incluye el NUL:
 * 9..64 bytes. Con pass vacía el AP queda ABIERTO con aviso (rama de degradación conservada).
 * Un valor de 1..7 chars es ERROR DE BUILD, no un brick silencioso del recovery. */
_Static_assert(sizeof(CONFIG_AULASENSE_PROVISION_AP_PASS) <= 1 ||
                   (sizeof(CONFIG_AULASENSE_PROVISION_AP_PASS) >= 9 &&
                    sizeof(CONFIG_AULASENSE_PROVISION_AP_PASS) <= 64),
               "AulaSense: AULASENSE_PROVISION_AP_PASS vacia (=AP abierto con aviso) o de 8..63 "
               "caracteres (minimo WPA2-PSK). 1..7 chars haria fallar esp_wifi_set_config y el "
               "AP de emergencia no levantaria: recovery mudo (informe A §1.1).");
/* El SSID del AP de emergencia cabe en wifi_config_t.ap.ssid (32 chars + NUL). */
_Static_assert(sizeof(CONFIG_AULASENSE_PROVISION_AP_SSID) <= 33,
               "AulaSense: AULASENSE_PROVISION_AP_SSID admite como maximo 32 bytes.");
/* El AP del portal NUNCA toca el bus DVP ni los pines prohibidos: no usa GPIO propios. */

#if !CONFIG_AULASENSE_PROVISION

void app_provision_set_publisher(app_provision_publish_fn publish,
                                 app_provision_connected_fn connected) {
    (void)publish;
    (void)connected;
}
bool app_provision_init(void) { return false; }
void app_provision_publish_pending(void) {}
bool app_provision_ap_activa(void) { return false; }

#else /* CONFIG_AULASENSE_PROVISION */

/* ------------------------- constantes del contrato ------------------------- */

#define NVS_NS_WIFI   "aulasense_wifi" /* ssid/pass puestos por el portal (distincto del vendor) */
#define NVS_KEY_SSID  "ssid"
#define NVS_KEY_PASS  "pass"
/* F26: la key NVS vieja del token de portal (namespace "aulasense_prov") queda OBSOLETA:
 * no se lee ni se escribe y NO se borra en campo (inofensiva, D-F26-4). El código ya no la usa. */

#define NVS_COOLDOWN_MS 5000U        /* anti-desgaste flash: 429 si se excede (calco app_portal.c) */
#define WATCH_STA_MS  25000U         /* tiempo para que la red nueva responda tras el guardado */
#define BOOT_GRACIA_MS 20000U        /* esperar antes del chequeo "sin credenciales" */
#define STA_REINTENTO_PROPIO_MS 25000U

#define TASK_EVT_QUEUE_LEN 8
#define EVENT_JSON_CAP 160
#define EVENT_RING_LEN 4
#define EVENT_REINTENTOS_MAX 3

typedef enum {
    PROV_EV_STA_DISCON = 0, /* .dato = reason wifi */
    PROV_EV_GOT_IP,
    PROV_EV_APPLY,          /* credenciales guardadas en NVS: AP off -> STA puro (E3) */
    PROV_EV_BOOT_CHECK,     /* fin de gracia de arranque: sin credenciales -> AP */
    PROV_EV_AP_OFF_DELAY,   /* caso defensivo: STA OK con AP aun arriba -> AP off */
    PROV_EV_TICK,
} prov_evt_tipo_t;

typedef struct {
    uint8_t tipo;
    uint8_t dato;
} prov_evt_t;

/* Maquina del modulo (vive SOLO en la tarea propia; los handlers solo postean eventos). */
typedef enum {
    PROV_ESTADO_NORMAL = 0, /* STA gestionada por el vendor; modulo observando */
    PROV_ESTADO_WATCH,      /* intento STA propio en curso (tras guardado o re-provision) */
    PROV_ESTADO_AP,         /* SoftAP + portal arribados */
} prov_estado_t;

/* ------------------------------- estado ---------------------------------- */

static QueueHandle_t s_evt_queue = NULL;
static httpd_handle_t s_httpd = NULL;
static esp_netif_t *s_ap_netif = NULL;
static volatile bool s_ap_activa = false;
static prov_estado_t s_estado = PROV_ESTADO_NORMAL; /* escriba unico: la tarea del modulo */
static uint32_t s_fallos_sta = 0;                   /* contador PROPIO (ENMIENDA E2) */
static bool s_hubo_ip = false;                      /* alguna vez asociado/GOT_IP */
static int64_t s_watch_fin_ms = 0;
static int64_t s_ultimo_guardado_ms = 0;
static char s_ap_ssid[33] = {0};
static bool s_observando = false;
static app_provision_publish_fn s_publish_fn = NULL;
static app_provision_connected_fn s_connected_fn = NULL;

/* Cola de eventos aulasense_config (anillo simple; el productor puede ser cualquier tarea). */
typedef struct {
    char json[EVENT_JSON_CAP];
    uint8_t intentos;
} prov_evento_t;
static StaticQueue_t s_evt_ring_struct;
static uint8_t s_evt_ring_store[EVENT_RING_LEN * (sizeof(prov_evento_t) + 16)];
static QueueHandle_t s_event_queue = NULL;

static void post_evt(prov_evt_tipo_t tipo, uint8_t dato) {
    if (s_evt_queue == NULL) {
        return;
    }
    const prov_evt_t e = {.tipo = (uint8_t)tipo, .dato = dato};
    (void)xQueueSend(s_evt_queue, &e, 0); /* espera 0: nunca bloquear sys_evt ni el httpd */
}

/* ----------------------- helpers NVS (sin secretos en log) ----------------------- */

static bool nvs_wifi_leer(char *ssid, size_t ssid_n, char *pass, size_t pass_n) {
    ssid[0] = '\0';
    pass[0] = '\0';
    nvs_handle_t h;
    if (nvs_open(NVS_NS_WIFI, NVS_READONLY, &h) != ESP_OK) {
        return false;
    }
    size_t l1 = ssid_n, l2 = pass_n;
    const bool ok = nvs_get_str(h, NVS_KEY_SSID, ssid, &l1) == ESP_OK;
    if (nvs_get_str(h, NVS_KEY_PASS, pass, &l2) != ESP_OK) {
        pass[0] = '\0';
    }
    nvs_close(h);
    return ok && ssid[0] != '\0';
}

static esp_err_t nvs_wifi_guardar(const char *ssid, const char *pass) {
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS_WIFI, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        err = nvs_set_str(h, NVS_KEY_SSID, ssid);
        if (err == ESP_OK) {
            err = nvs_set_str(h, NVS_KEY_PASS, pass);
        }
        if (err == ESP_OK) {
            err = nvs_commit(h);
        }
        nvs_close(h);
    }
    return err;
}

/* -------------------- eventos aulasense_config (SPEC §6.1) -------------------- */

static void publicar_evento(const char *evento, const char *detalle) {
    if (s_event_queue == NULL) {
        return;
    }
    prov_evento_t ev;
    const long long ts_ms = (long long)(esp_timer_get_time() / 1000LL);
    const int n = snprintf(ev.json, sizeof(ev.json),
                           "{\"timestamp_ms\":%lld,\"evento\":\"%s\",\"detalle\":\"%s\"}", ts_ms,
                           evento, detalle);
    if (n <= 0 || n >= (int)sizeof(ev.json)) {
        ESP_LOGW(TAG, "payload del evento %s no cabe: se omite", evento);
        return;
    }
    ev.intentos = 0;
    if (xQueueSend(s_event_queue, &ev, 0) != pdTRUE) {
        ESP_LOGW(TAG, "cola de eventos llena: %s descartado (queda solo en serial)", evento);
    }
}

void app_provision_publish_pending(void) {
    if (s_event_queue == NULL || s_publish_fn == NULL) {
        return;
    }
    prov_evento_t ev;
    while (xQueueReceive(s_event_queue, &ev, 0) == pdTRUE) {
        if (s_publish_fn(ev.json)) {
            continue;
        }
        /* Sin STA no hay broker: re-intentar con tope para no acumular eventos viejos. */
        if (ev.intentos + 1U < EVENT_REINTENTOS_MAX && xQueueSend(s_event_queue, &ev, 0) == pdTRUE) {
            ++ev.intentos;
        } else {
            ESP_LOGW(TAG, "evento de provision no publicado (MQTT sin conexion): descartado");
        }
    }
}

/* --------------------- AP de emergencia (ENMIENDA E3/E4; F26: SSID fijo) --------------------- */

/* SSID FIJO desde Kconfig (F26, D-F26-4): sin sufijo MAC. Vacio/absurdo -> default del contrato. */
static void ap_nombre(void) {
    const char *ssid = CONFIG_AULASENSE_PROVISION_AP_SSID;
    if (ssid[0] == '\0') {
        ssid = "AULASENSE-CFG";
    }
    strlcpy(s_ap_ssid, ssid, sizeof(s_ap_ssid));
}

static bool credenciales_propias_disponibles(void) {
    char ssid[33] = {0};
    char pass[65] = {0};
    return nvs_wifi_leer(ssid, sizeof(ssid), pass, sizeof(pass));
}

/* Levantar AP: esp_wifi_stop() + modo AP PURO (NUNCA APSTA cross-canal para reconectar,
 * E3: una sola radio en el S3 y el canal AP 1 != canal de la red real). Los intentos de
 * esp_wifi_connect() del vendor contra el driver en modo AP fallan sin ruido (la rama de
 * error del vendor solo re-programa su deadline: sin log). */
/* Adelanto: la definicion vive mas abajo (seccion portal httpd). */
static esp_err_t httpd_arrancar(void);

static void ap_levantar(const char *motivo) {
    if (s_estado == PROV_ESTADO_AP) {
        return;
    }
    ap_nombre();

    char detalle[EVENT_JSON_CAP / 2];
    esp_err_t err = esp_wifi_stop();
    if (err != ESP_OK) {
        /* F26: ninguna rama de fallo queda muda (informe A §1.1: recovery mudo). */
        snprintf(detalle, sizeof(detalle), "etapa=wifi_stop esp_err=%s motivo=%s",
                 esp_err_to_name(err), motivo);
        publicar_evento("AP_ERROR", detalle);
        ESP_LOGE(TAG, "esp_wifi_stop fallo: portal no disponible (%s)", motivo);
        return;
    }
    err = esp_wifi_set_mode(WIFI_MODE_AP);
    if (err != ESP_OK) {
        snprintf(detalle, sizeof(detalle), "etapa=set_mode esp_err=%s motivo=%s",
                 esp_err_to_name(err), motivo);
        publicar_evento("AP_ERROR", detalle);
        ESP_LOGE(TAG, "set_mode(AP) fallo: portal no disponible (%s)", motivo);
        return;
    }
    if (s_ap_netif == NULL) {
        s_ap_netif = esp_netif_create_default_wifi_ap();
        if (s_ap_netif == NULL) {
            /* F26-g4 (hallazgo auditor externo; SPEC §16.2: AP_ERROR en TODAS las ramas):
             * antes esta rama era muda y el codigo SEGUIA hacia set_config/start, arribando
             * un AP SIN netif: portal 192.168.4.1 inalcanzable mientras se publicaba
             * AP_FALLBACK. Cortar aqui: evento + log + return ANTES de tocar wifi/httpd. */
            snprintf(detalle, sizeof(detalle), "etapa=netif_ap esp_err=NULL motivo=%s",
                     motivo);
            publicar_evento("AP_ERROR", detalle);
            ESP_LOGE(TAG, "esp_netif_create_default_wifi_ap devolvio NULL: portal no "
                          "disponible (%s)", motivo);
            return;
        }
    }
    wifi_config_t ap = {0};
    strlcpy((char *)ap.ap.ssid, s_ap_ssid, sizeof(ap.ap.ssid));
    ap.ap.ssid_len = (uint8_t)strlen(s_ap_ssid);
    ap.ap.channel = 1; /* 2.4 GHz obligatorio (el S3 no ve 5 GHz) */
    ap.ap.max_connection = 4;
    if (sizeof(CONFIG_AULASENSE_PROVISION_AP_PASS) > 1) {
        ap.ap.authmode = WIFI_AUTH_WPA2_PSK; /* pass por Kconfig/.local: NUNCA se loguea */
        strlcpy((char *)ap.ap.password, CONFIG_AULASENSE_PROVISION_AP_PASS, sizeof(ap.ap.password));
    } else {
        ap.ap.authmode = WIFI_AUTH_OPEN; /* pass vacia: abierto CON aviso (pagina+log), D-F24-4 */
    }
    err = esp_wifi_set_config(WIFI_IF_AP, &ap);
    if (err != ESP_OK) {
        /* Con la guarda de compilacion (pass 8..63 salvo vacia) esto solo puede pasar por una
         * causa exotica; aun asi el evento llega al dashboard antes de cualquier return. */
        snprintf(detalle, sizeof(detalle), "etapa=set_config esp_err=%s motivo=%s ssid_len=%u",
                 esp_err_to_name(err), motivo, (unsigned)strlen(s_ap_ssid));
        publicar_evento("AP_ERROR", detalle);
        ESP_LOGE(TAG, "set_config(AP) fallo: portal no disponible (%s)", motivo);
        return;
    }
    err = esp_wifi_start();
    if (err != ESP_OK) {
        snprintf(detalle, sizeof(detalle), "etapa=wifi_start esp_err=%s motivo=%s",
                 esp_err_to_name(err), motivo);
        publicar_evento("AP_ERROR", detalle);
        ESP_LOGE(TAG, "esp_wifi_start (AP) fallo: portal no disponible (%s)", motivo);
        return;
    }
    /* El portal vive en las rutas explicitas /cfg, /cfg/estado y /cfg/wifi (F26, sin token). */
    err = httpd_arrancar();
    if (err != ESP_OK) {
        snprintf(detalle, sizeof(detalle), "etapa=httpd esp_err=%s motivo=%s",
                 esp_err_to_name(err), motivo);
        publicar_evento("AP_ERROR", detalle);
        ESP_LOGE(TAG, "httpd NO arranco: portal caido, AP si (%s)", motivo);
        /* NO se return: el AP de red sigue arribado; el fallo del portal ya es visible. */
    }
    s_ap_activa = true;
    s_estado = PROV_ESTADO_AP;
    s_fallos_sta = 0; /* el umbral es por ciclo de fallback, no acumulado entre APs */

    snprintf(detalle, sizeof(detalle), "motivo=%s ap=%s auth=%s", motivo, s_ap_ssid,
             ap.ap.authmode == WIFI_AUTH_OPEN ? "abierta" : "wpa2");
    publicar_evento("AP_FALLBACK", detalle);

    ESP_LOGW(TAG, "SoftAP '%s' ARRIBADO en 192.168.4.1 — %s", s_ap_ssid,
             ap.ap.authmode == WIFI_AUTH_OPEN
                 ? "AP ABIERTO (aviso en la pagina; fija AULASENSE_PROVISION_AP_PASS si quieres WPA2)"
                 : "AP con WPA2 (pass por .env/sdkconfig.defaults.local, nunca se loguea)");
    ESP_LOGI(TAG, "AulaSense CFG URL: http://192.168.4.1/cfg");
}

/* Apagar AP + httpd. NO toca el modo WiFi: quien decide STA-puro-lo-hace (E3). */
static void ap_apagar(void) {
    if (s_httpd != NULL) {
        httpd_stop(s_httpd);
        s_httpd = NULL;
    }
    s_ap_activa = false;
    ESP_LOGI(TAG, "SoftAP APAGADO");
}

/* AP off -> WIFI_MODE_STA PURO -> config desde NVS aulasense_wifi -> connect (E3). */
static void aplicar_credenciales_y_conectar(const char *via) {
    ap_apagar();
    char ssid[33] = {0};
    char pass[65] = {0};
    if (!nvs_wifi_leer(ssid, sizeof(ssid), pass, sizeof(pass))) {
        ESP_LOGW(TAG, "sin credenciales en NVS %s: el portal sigue siendo la via", NVS_NS_WIFI);
        s_estado = PROV_ESTADO_NORMAL;
        return;
    }
    esp_wifi_stop();
    if (esp_wifi_set_mode(WIFI_MODE_STA) != ESP_OK) {
        ESP_LOGE(TAG, "set_mode(STA) fallo: se re-intenta el AP en el proximo tick");
        post_evt(PROV_EV_TICK, 0);
        s_estado = PROV_ESTADO_NORMAL;
        return;
    }
    wifi_config_t st = {0};
    strlcpy((char *)st.sta.ssid, ssid, sizeof(st.sta.ssid));
    strlcpy((char *)st.sta.password, pass, sizeof(st.sta.password));
    st.sta.threshold.authmode = pass[0] == '\0' ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
    if (esp_wifi_set_config(WIFI_IF_STA, &st) != ESP_OK) {
        ESP_LOGE(TAG, "set_config(STA) fallo (ssid largo=%u)", (unsigned)strlen(ssid));
        s_estado = PROV_ESTADO_NORMAL;
        return;
    }
    if (esp_wifi_start() != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_start (STA) fallo");
        s_estado = PROV_ESTADO_NORMAL;
        return;
    }
    (void)esp_wifi_connect();
    s_estado = PROV_ESTADO_WATCH;
    s_watch_fin_ms = esp_timer_get_time() / 1000LL + (int64_t)WATCH_STA_MS;
    ESP_LOGI(TAG, "intentando STA con credenciales del portal (via=%s, ssid largo=%u, pass largo=%u)",
             via, (unsigned)strlen(ssid), (unsigned)strlen(pass));
}

/* ------------------------------ portal httpd ------------------------------ */

/* Guard anti-CSRF identico al archive (app_portal.c:73-98): http://192.168.4.1.evil.com NO pasa. */
static bool host_es_propio(const char *url) {
    static const char *PREFIJOS[] = {
        "http://192.168.4.1", "http://localhost", "http://127.0.0.1",
    };
    for (size_t i = 0; i < sizeof(PREFIJOS) / sizeof(PREFIJOS[0]); i++) {
        const size_t n = strlen(PREFIJOS[i]);
        if (strncmp(url, PREFIJOS[i], n) == 0) {
            const char c = url[n];
            if (c == '\0' || c == '/' || c == ':') {
                return true; /* fin, ruta o puerto */
            }
        }
    }
    return false;
}

static bool origin_permitido(httpd_req_t *req) {
    char val[96] = {0};
    if (httpd_req_get_hdr_value_str(req, "Origin", val, sizeof(val)) == ESP_OK) {
        return host_es_propio(val);
    }
    if (httpd_req_get_hdr_value_str(req, "Referer", val, sizeof(val)) == ESP_OK) {
        return host_es_propio(val); /* algunos webviews de cautivo mandan solo Referer */
    }
    return true; /* sin ambas: cliente no-navegador */
}

/* Cabeceras de hardening en TODAS las respuestas (calco app_portal.c:104-112; CSP text-only). */
static void cabeceras_seguridad(httpd_req_t *req) {
    httpd_resp_set_hdr(req, "X-Content-Type-Options", "nosniff");
    httpd_resp_set_hdr(req, "X-Frame-Options", "DENY");
    httpd_resp_set_hdr(req, "Referrer-Policy", "no-referrer");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "Content-Security-Policy",
                       "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                       "frame-ancestors 'none'");
}

/* F26 (SPEC §16.2): portal SIN token con RUTAS EXPLÍCITAS. Se registra cada ruta exacta con el
 * matcher default del httpd (sin comodines). Decisión verificada por diseño (informe A §1.2.4):
 * el patron comodin (prefijo "/cfg/" + asterisco) exige barra tras /cfg, así que "/cfg" a
 * secas NECESITA handler propio; con rutas exactas desaparece también la necesidad de parsear
 * el segmento. La
 * comparacion anti-timing del token se elimina porque ya no hay secreto que comparar (D-F26-4).
 * Cualquier otra URI -> 404 por defecto del propio httpd (sin pista de rutas validas). */
static bool uri_es(const char *uri, const char *ruta) {
    return strcmp(uri, ruta) == 0;
}

static const char *wifi_estado_texto(void) {
    if (s_hubo_ip && s_estado != PROV_ESTADO_WATCH) {
        return "conectada";
    }
    if (s_estado == PROV_ESTADO_WATCH) {
        return "intentando (portal)";
    }
    if (credenciales_propias_disponibles()) {
        return "reintentando";
    }
    return "sin credenciales";
}

/* urldecode minimo para formulario x-www-form-urlencoded (calco app_portal.c:189-248):
 * solo lo que necesita ssid/pass; longitudes absurdas se rechazan con truncado por
 * capacidad destino + body cap. */
static void url_decode(const char *src, size_t n, char *dst, size_t dn) {
    size_t j = 0;
    for (size_t i = 0; i < n && j + 1 < dn; i++) {
        const char c = src[i];
        if (c == '+') {
            dst[j++] = ' ';
        } else if (c == '%' && i + 2 < n) {
            char hex[3] = {src[i + 1], src[i + 2], 0};
            char *fin = NULL;
            const long v = strtol(hex, &fin, 16);
            if (fin && fin == hex + 2 && v >= 1 && v <= 127) {
                dst[j++] = (char)v;
            }
            i += 2;
        } else if (c != '&') {
            dst[j++] = c;
        }
    }
    dst[j] = '\0';
}

/* Extrae key=value del inicio de cada token &-separado (no busca dentro de valores ajenos). */
static bool form_extraer(const char *body, const char *key, char *out, size_t n_out) {
    const size_t klen = strlen(key);
    const char *tok = body;
    while (tok && *tok) {
        const char *next = strchr(tok, '&');
        const size_t tlen = next ? (size_t)(next - tok) : strlen(tok);
        const char *eq = memchr(tok, '=', tlen);
        if (eq && (size_t)(eq - tok) == klen && strncmp(tok, key, klen) == 0) {
            url_decode(eq + 1, tlen - klen - 1, out, n_out);
            return true;
        }
        tok = next ? next + 1 : NULL;
    }
    return false;
}

/* GET /cfg/estado → JSON de estado SIN secretos (wifi_sta, ap_activa, heap interno/psram). */
static esp_err_t h_cfg_estado(httpd_req_t *req) {
    cabeceras_seguridad(req);
    char json[320];
    snprintf(json, sizeof(json),
             "{\"wifi_sta\":\"%s\",\"ap_activa\":%s,\"fallos_sta\":%u,"
             "\"heap_interno_kb\":%u,\"bloque_mayor_kb\":%u,\"psram_libre_kb\":%u}",
             wifi_estado_texto(), s_ap_activa ? "true" : "false",
             (unsigned)s_fallos_sta,
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) / 1024U),
             (unsigned)(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) /
                        1024U),
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024U));
    httpd_resp_set_type(req, "application/json");
    return httpd_resp_sendstr(req, json);
}

/* GET /cfg → formulario 100 % texto/numeros: CERO <img>/binarios; NUNCA renderiza SSID/pass
 * guardados (decision D-F24-4 conservada en F26). Rutas del formulario/estado ABSOLUTAS FIJAS:
 * el portal ya no lleva token en la URL (SPEC §16.2). */
static esp_err_t h_cfg_form(httpd_req_t *req) {
    cabeceras_seguridad(req);
    static const char AVISO_ABIERTO[] =
        "<p class=aviso>AP ABIERTO (sin clave): alguien cerca podria ver lo que escribas aqui."
        " Recomendado: fija AULASENSE_PROVISION_AP_PASS (minimo 8 caracteres) via .env.</p>";
    static const char CAB[] =
        "<!doctype html><html lang=es><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>AulaSense - Config WiFi</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:640px;margin:1.5rem auto;padding:0 1rem}"
        "input{width:100%;padding:.5rem;margin:.25rem 0 1rem}button{padding:.6rem 1rem}"
        "label{font-weight:600}.aviso{background:#fff3cd;border:1px solid #ffe08a;padding:.6rem}"
        ".estado{font-family:monospace;white-space:pre-wrap;background:#f4f4f4;padding:.6rem}</style>"
        "</head><body><h1>AulaSense portal de emergencia</h1>";
    static const char MEDIO[] =
        "<h2>Guardar red WiFi (solo 2.4 GHz)</h2>"
        "<p>Al guardar, el AP se APAGA y el nodo intenta conectar como STA puro. Si la red no "
        "responde en ~25 s, este portal vuelve a estar disponible aqui mismo.</p>";
    char estado[256];
    snprintf(estado, sizeof(estado),
             "<div class=estado>WiFi STA: %s\nModo AP: %s\nFallos STA acumulados: %u\n"
             "heap interno libre: %u KB | mayor bloque: %u KB | psram libre: %u KB</div>",
             wifi_estado_texto(), s_ap_activa ? "ACTIVA" : "apagada", (unsigned)s_fallos_sta,
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) / 1024U),
             (unsigned)(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) /
                        1024U),
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024U));
    static const char FORM[] =
        "<form method=post action='/cfg/wifi'><label>SSID</label>"
        "<input name=ssid maxlength=32 autocomplete=off>"
        "<label>Clave (vacia = red abierta)</label>"
        "<input name=pass type=password maxlength=64 autocomplete=off>"
        "<button>Guardar y conectar</button></form>";
    static const char PIE[] =
        "<p><a href='/cfg/estado'>estado (JSON)</a></p></body></html>";
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    if (httpd_resp_sendstr_chunk(req, CAB) != ESP_OK) {
        return ESP_FAIL;
    }
    if (sizeof(CONFIG_AULASENSE_PROVISION_AP_PASS) <= 1 &&
        httpd_resp_sendstr_chunk(req, AVISO_ABIERTO) != ESP_OK) {
        return ESP_FAIL;
    }
    if (httpd_resp_sendstr_chunk(req, estado) != ESP_OK) {
        return ESP_FAIL;
    }
    if (httpd_resp_sendstr_chunk(req, FORM) != ESP_OK) {
        return ESP_FAIL;
    }
    if (httpd_resp_sendstr_chunk(req, MEDIO) != ESP_OK) {
        return ESP_FAIL;
    }
    if (httpd_resp_sendstr_chunk(req, PIE) != ESP_OK) {
        return ESP_FAIL;
    }
    return httpd_resp_sendstr_chunk(req, NULL); /* cierra chunked */
}

/* POST /cfg/wifi → guardado NVS + AP OFF → STA puro → connect (ENMIENDA E3 intacta).
 * Guardas heredadas SIN cambios: Origin/Referer 403, body ≤255 B, ssid obligatorio,
 * cooldown NVS 5 s (429), sin escritura si no cambia, NUNCA loguea valores (solo longitudes). */
static esp_err_t h_cfg_post(httpd_req_t *req) {
    cabeceras_seguridad(req);
    if (!uri_es(req->uri, "/cfg/wifi")) {
        return httpd_resp_send_404(req); /* ruta no registrada: 404 neutro del propio handler */
    }
    if (!origin_permitido(req)) {
        httpd_resp_set_status(req, "403 Forbidden");
        httpd_resp_set_type(req, "text/plain; charset=utf-8");
        return httpd_resp_sendstr(req, "origen cruzado bloqueado");
    }
    char body[256];
    const int len = httpd_req_recv(req, body, sizeof(body) - 1);
    if (len <= 0) {
        httpd_resp_set_status(req, "400 Bad Request");
        httpd_resp_set_type(req, "text/plain; charset=utf-8");
        return httpd_resp_sendstr(req, "cuerpo ausente o ilegible");
    }
    body[len] = '\0';
    if ((size_t)len == sizeof(body) - 1) {
        /* body demasiado largo: longitudes absurdas rechazadas (calco app_portal.c) */
        httpd_resp_set_status(req, "400 Bad Request");
        httpd_resp_set_type(req, "text/plain; charset=utf-8");
        return httpd_resp_sendstr(req, "cuerpo demasiado largo");
    }
    char ssid[33] = {0};
    char pass[65] = {0};
    if (!form_extraer(body, "ssid", ssid, sizeof(ssid)) || ssid[0] == '\0') {
        httpd_resp_set_status(req, "400 Bad Request");
        httpd_resp_set_type(req, "text/plain; charset=utf-8");
        return httpd_resp_sendstr(req, "falta campo ssid");
    }
    (void)form_extraer(body, "pass", pass, sizeof(pass)); /* red abierta: ausencia valida */

    char cur_ssid[33] = {0};
    char cur_pass[65] = {0};
    nvs_wifi_leer(cur_ssid, sizeof(cur_ssid), cur_pass, sizeof(cur_pass));
    if (strcmp(cur_ssid, ssid) == 0 && strcmp(cur_pass, pass) == 0) {
        ESP_LOGI(TAG, "portal: credenciales identicas a las del NVS — sin escritura");
    } else {
        const int64_t ahora_ms = esp_timer_get_time() / 1000LL;
        if (s_ultimo_guardado_ms != 0 && (ahora_ms - s_ultimo_guardado_ms) < (int64_t)NVS_COOLDOWN_MS) {
            httpd_resp_set_status(req, "429 Too Many Requests");
            httpd_resp_set_type(req, "text/plain; charset=utf-8");
            return httpd_resp_sendstr(req,
                "espera unos segundos entre guardados (proteccion de la flash NVS)");
        }
        if (nvs_wifi_guardar(ssid, pass) != ESP_OK) {
            httpd_resp_set_status(req, "500 Internal Server Error");
            httpd_resp_set_type(req, "text/plain; charset=utf-8");
            return httpd_resp_sendstr(req, "NVS fallo");
        }
        s_ultimo_guardado_ms = ahora_ms;
        /* NUNCA loguear valores: solo longitudes (calco app_portal.c:290-291). */
        ESP_LOGI(TAG, "portal: credenciales guardadas en NVS %s (ssid largo=%u, pass largo=%u)",
                 NVS_NS_WIFI, (unsigned)strlen(ssid), (unsigned)strlen(pass));
        char detalle[80];
        snprintf(detalle, sizeof(detalle), "ssid_len=%u pass_len=%u", (unsigned)strlen(ssid),
                 (unsigned)strlen(pass));
        publicar_evento("CFG_GUARDADA", detalle);
    }
    /* E3: la operacion de radio (AP off -> STA puro -> connect) va a la tarea del modulo;
     * el POST nunca llama esp_wifi_* desde la tarea del httpd. Se postea DESPUES de enviar
     * la respuesta: el modulo puede llamar a httpd_stop() en cuanto reciba el evento. */
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    const esp_err_t ret = httpd_resp_sendstr(req,
        "<!doctype html><html lang=es><body><h2>Guardado</h2>"
        "<p>El AP se apaga y el nodo intenta conectar como STA. Si la red no responde, este "
        "portal (SSID de AULASENSE_PROVISION_AP_SSID, por defecto AULASENSE-CFG) vuelve a estar "
        "disponible en http://192.168.4.1/cfg.</p>"
        "</body></html>");
    post_evt(PROV_EV_APPLY, 0);
    return ret;
}

static esp_err_t httpd_arrancar(void) {
    if (s_httpd != NULL) {
        return ESP_OK;
    }
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.task_priority = 4;  /* ENMIENDA E7: NUNCA en el loop=5 del detector */
    cfg.stack_size = 8192;  /* snprintf de pagina con margen (leccion F13 del archive) */
    /* F26: 2 -> 4 handlers (GET /cfg, GET /cfg/estado, POST /cfg/wifi + margen).
     * Matcher DEFAULT (rutas exactas): el patron comodin con barra final no casa "/cfg" sin barra
     * (informe A §1.2.4), por eso cada ruta lleva handler propio. */
    cfg.max_uri_handlers = 4;
    cfg.server_port = 80;   /* puerto propio del AP; el Direct HTTP del vendor vive en 62587 */
    const esp_err_t err = httpd_start(&s_httpd, &cfg);
    if (err != ESP_OK) {
        s_httpd = NULL;
        return err;
    }
    static const httpd_uri_t uris[] = {
        { .uri = "/cfg", .method = HTTP_GET, .handler = h_cfg_form },
        { .uri = "/cfg/estado", .method = HTTP_GET, .handler = h_cfg_estado },
        { .uri = "/cfg/wifi", .method = HTTP_POST, .handler = h_cfg_post },
    };
    for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) {
        const esp_err_t reg = httpd_register_uri_handler(s_httpd, &uris[i]);
        if (reg != ESP_OK) {
            ESP_LOGE(TAG, "no pude registrar %s (%s)", uris[i].uri, esp_err_to_name(reg));
        }
    }
    ESP_LOGI(TAG, "httpd del portal escuchando en http://192.168.4.1/cfg "
                  "(GET formulario, GET /cfg/estado, POST /cfg/wifi)");
    return ESP_OK;
}

/* --------------------------- handlers de eventos --------------------------- */

/* Regla del vendor (src/cpp/AGENTS.md): los handlers WiFi/IP NO loguean — solo postean. */
static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    if (base != WIFI_EVENT || id != WIFI_EVENT_STA_DISCONNECTED) {
        return;
    }
    const wifi_event_sta_disconnected_t *ev = (const wifi_event_sta_disconnected_t *)data;
    post_evt(PROV_EV_STA_DISCON, ev != NULL ? ev->reason : 0U);
}

static void on_ip_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    (void)base;
    (void)id;
    (void)data;
    post_evt(PROV_EV_GOT_IP, 0U);
}

/* --------------------------- maquina del modulo ---------------------------- */

static void manejar(prov_evt_t e) {
    switch ((prov_evt_tipo_t)e.tipo) {
        case PROV_EV_STA_DISCON:
            if (s_estado == PROV_ESTADO_AP) {
                break; /* en modo AP puro no hay eventos STA: defensivo */
            }
            if (s_estado == PROV_ESTADO_WATCH) {
                ESP_LOGW(TAG, "intento STA del portal fallo (reason=%u): re-levanto el AP", e.dato);
                ap_levantar("fallo-red-del-portal");
                break;
            }
            s_fallos_sta++;
            if (s_fallos_sta >= (uint32_t)CONFIG_AULASENSE_PROVISION_RETRIES) {
                ESP_LOGW(TAG, "STA fallo %lu veces (umbral AULASENSE_PROVISION_RETRIES=%d): "
                              "activando AP de emergencia (reason ultimo=%u)",
                         (unsigned long)s_fallos_sta, CONFIG_AULASENSE_PROVISION_RETRIES, e.dato);
                ap_levantar("sta-fallos");
            }
            break;

        case PROV_EV_GOT_IP:
            if (!s_hubo_ip) {
                ESP_LOGI(TAG, "STA conectada");
            }
            s_hubo_ip = true;
            s_fallos_sta = 0;
            if (s_estado == PROV_ESTADO_WATCH) {
                s_estado = PROV_ESTADO_NORMAL;
                publicar_evento("STA_RECONECTADO", "via=portal");
                ESP_LOGI(TAG, "STA RECONECTADA con credenciales del portal: AP queda OFF (E3)");
            } else if (s_estado == PROV_ESTADO_AP) {
                /* Caso defensivo (p. ej. provisoning Improv concurrente): AP off tras
                 * AULASENSE_PROVISION_AP_OFF_S y vuelta a STA puro. */
                ESP_LOGI(TAG, "GOT_IP con AP arribado: AP off en %d s (STA puro)",
                         CONFIG_AULASENSE_PROVISION_AP_OFF_S);
                post_evt(PROV_EV_AP_OFF_DELAY, 0);
            }
            break;

        case PROV_EV_APPLY:
            aplicar_credenciales_y_conectar("post-wifi");
            break;

        case PROV_EV_BOOT_CHECK:
            if (s_estado != PROV_ESTADO_NORMAL || s_hubo_ip) {
                break;
            }
            wifi_config_t cur = {0};
            const bool tiene_ssid =
                esp_wifi_get_config(WIFI_IF_STA, &cur) == ESP_OK && cur.sta.ssid[0] != '\0';
            if (!tiene_ssid) {
                if (credenciales_propias_disponibles()) {
                    ESP_LOGW(TAG, "arranque sin credenciales del vendor: pruebo las del NVS "
                                  "del portal (AP OFF -> STA puro, E3)");
                    aplicar_credenciales_y_conectar("nvs-portal");
                } else {
                    ESP_LOGW(TAG, "arranque SIN CREDENCIALES en ningun sitio: AP de emergencia");
                    ap_levantar("sin-credenciales");
                }
            }
            /* Si el vendor tiene credenciales todavia no sabemos: el contador de fallos decide. */
            break;

        case PROV_EV_AP_OFF_DELAY:
            if (s_estado == PROV_ESTADO_AP) {
                ESP_LOGI(TAG, "AP off tras STA OK: vuelvo a WIFI_MODE_STA puro");
                ap_apagar();
                esp_wifi_stop();
                if (esp_wifi_set_mode(WIFI_MODE_STA) == ESP_OK) {
                    (void)esp_wifi_start();
                    s_estado = PROV_ESTADO_NORMAL;
                }
            }
            break;

        case PROV_EV_TICK:
            if (s_estado == PROV_ESTADO_WATCH &&
                esp_timer_get_time() / 1000LL > s_watch_fin_ms) {
                ESP_LOGW(TAG, "la red nueva no respondio en %u ms: re-levanto el AP",
                         (unsigned)WATCH_STA_MS);
                ap_levantar("watch-agotado");
            }
            break;
    }
}

static void prov_task(void *arg) {
    (void)arg;
    prov_evt_t e;
    for (;;) {
        if (xQueueReceive(s_evt_queue, &e, pdMS_TO_TICKS(1000)) == pdTRUE) {
            manejar(e);
        } else {
            manejar((prov_evt_t){.tipo = PROV_EV_TICK, .dato = 0});
        }
    }
}

static void boot_check_timer(void *arg) {
    (void)arg;
    post_evt(PROV_EV_BOOT_CHECK, 0);
}

/* ------------------------------- API pública ------------------------------- */

void app_provision_set_publisher(app_provision_publish_fn publish,
                                 app_provision_connected_fn connected) {
    s_publish_fn = publish;
    s_connected_fn = connected;
}

bool app_provision_init(void) {
    if (s_observando) {
        return true; /* idempotente */
    }
    s_evt_queue = xQueueCreate(TASK_EVT_QUEUE_LEN, sizeof(prov_evt_t));
    if (s_evt_queue == NULL) {
        ESP_LOGE(TAG, "sin cola de eventos: modulo deshabilitado");
        return false;
    }
    s_event_queue = xQueueCreateStatic(EVENT_RING_LEN, sizeof(prov_evento_t), s_evt_ring_store,
                                       &s_evt_ring_struct);
    if (s_event_queue == NULL) {
        ESP_LOGE(TAG, "sin anillo de eventos: las aulasense_config quedaran solo en serial");
    }

    /* Solo LEEMOS el flujo del vendor: nada de esto re-configura la estacion. */
    if (esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, &on_wifi_event, NULL) != ESP_OK) {
        ESP_LOGE(TAG, "no pude registrar el handler WIFI_EVENT");
        return false;
    }
    if (esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &on_ip_event, NULL) != ESP_OK) {
        ESP_LOGE(TAG, "no pude registrar el handler IP_EVENT");
        return false;
    }

    BaseType_t ok = xTaskCreate(prov_task, "aulasense_prov", 8192, NULL, 3, NULL);
    if (ok != pdPASS) {
        ESP_LOGE(TAG, "no pude crear la tarea del modulo");
        return false;
    }

    /* Comprobacion "sin credenciales" tras la gracia de arranque (una vez). */
    const esp_timer_create_args_t tc = {
        .callback = &boot_check_timer,
        .name = "aulasense_bootcheck",
    };
    esp_timer_handle_t tmr = NULL;
    if (esp_timer_create(&tc, &tmr) == ESP_OK) {
        (void)esp_timer_start_once(tmr, (uint64_t)BOOT_GRACIA_MS * 1000ULL);
    } else {
        ESP_LOGW(TAG, "sin timer de arranque: el caso 'sin credenciales' queda al contador");
    }

    s_observando = true;
    ESP_LOGI(TAG, "provision listo: AP de emergencia '%s' tras %d fallos STA, umbral propio (E2); "
                  "portal en http://192.168.4.1/cfg (F26: rutas fijas, sin token)",
             CONFIG_AULASENSE_PROVISION_AP_SSID, CONFIG_AULASENSE_PROVISION_RETRIES);
    return true;
}

bool app_provision_ap_activa(void) {
    return s_ap_activa;
}

#endif /* CONFIG_AULASENSE_PROVISION */
