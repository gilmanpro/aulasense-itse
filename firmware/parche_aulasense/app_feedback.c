/*
 * app_feedback.c — AulaSense (parche F3): feedback local (buzzer + WS2812) y die temp del S3.
 *
 * Derivado GPLv3 del vendor ESPectre Native 3.2.0. Comportamiento (README §3, SPEC §3.1):
 *  - Buzzer GPIO14 por LEDC: beep de 880 Hz y ~110 ms en cada ONSET idle->motion, con
 *    cooldown configurable (5 s por defecto) para no spamear en movimiento continuo.
 *  - WS2812 GPIO48 por RMT (componente gestionado espressif/led_strip): azul tenue en `idle`,
 *    azul brillante en `motion`; se refresca SOLO en la transición (no por evaluación).
 *  - Die temp por el driver `temperature_sensor` del ESP32-S3 cada 30 s, publicada en el topic
 *    aditivo `aulasense_temp` sobre el MISMO cliente MQTT de Native (lo inyecta app_main.cpp)
 *    y además logueada en formato parseable `AULASENSE,T,die_temp_c=XX.X`.
 *
 * Diseño de hilos (no negociable, README §8 y AGENTS §Estilo):
 *  - El hook de estado (prio 5, tarea del loop de Native) solo compara y encola: O(1).
 *  - Toda la periferia (LEDC, RMT, sensor, formato JSON) vive en la tarea propia del módulo,
 *    prioridad 3 (< 5 del loop del detector y < 4 de F24), stack 4 KB.
 *  - La publicación MQTT nunca se hace desde la tarea propia: se bombea desde la tarea del
 *    loop (`app_feedback_publish_pending()`), que es la dueña del transporte del vendor.
 *
 * Comentarios en español, identificadores en inglés (regla del repo AulaSense).
 */
#include "app_feedback.h"

#include "sdkconfig.h"

#include <stdio.h>

#include "esp_err.h"
#include "esp_heap_caps.h" /* F26: heap_interno retain (SPEC §16.3) */
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include "driver/ledc.h"
#include "driver/temperature_sensor.h"
#include "led_strip.h"

static const char *TAG = "aulasense.feedback";

/* ------------------------------------------------------------------------------------------
 * Guardas de compilación de pines (SPEC §5, AGENTS §GPIO) — obligatorias.
 * Se evalúan SIEMPRE, con el módulo encendido o apagado: un pin prohibido es un error de
 * configuración del proyecto, no una preferencia de build.
 * ---------------------------------------------------------------------------------------- */

#ifndef CONFIG_AULASENSE_BUZZER_GPIO
#error "AulaSense: falta CONFIG_AULASENSE_BUZZER_GPIO. Copia Kconfig.projbuild del parche a main/."
#endif
#ifndef CONFIG_AULASENSE_WS2812_GPIO
#error "AulaSense: falta CONFIG_AULASENSE_WS2812_GPIO. Copia Kconfig.projbuild del parche a main/."
#endif
#ifndef CONFIG_AULASENSE_FEEDBACK
#error "AulaSense: falta CONFIG_AULASENSE_FEEDBACK. Copia Kconfig.projbuild del parche a main/."
#endif

/* Rango físico del ESP32-S3. 22-25 no existen; 33-34 no están disponibles en esta placa. */
#if (CONFIG_AULASENSE_BUZZER_GPIO < 0) || (CONFIG_AULASENSE_BUZZER_GPIO > 48)
#error "AulaSense: AULASENSE_BUZZER_GPIO fuera del rango 0-48 del ESP32-S3."
#endif
#if (CONFIG_AULASENSE_WS2812_GPIO < 0) || (CONFIG_AULASENSE_WS2812_GPIO > 48)
#error "AulaSense: AULASENSE_WS2812_GPIO fuera del rango 0-48 del ESP32-S3."
#endif

/* 19/20 = USB nativo D-/D+ (regla E5: el USB device stack re-muxea el PHY y puede brickear). */
#if (CONFIG_AULASENSE_BUZZER_GPIO == 19) || (CONFIG_AULASENSE_BUZZER_GPIO == 20)
#error "AulaSense: GPIO 19/20 es el USB nativo D-/D+ (prohibido, regla E5)."
#endif
#if (CONFIG_AULASENSE_WS2812_GPIO == 19) || (CONFIG_AULASENSE_WS2812_GPIO == 20)
#error "AulaSense: GPIO 19/20 es el USB nativo D-/D+ (prohibido, regla E5)."
#endif

/* 43/44 = UART0 real (consola/flasheo de respaldo). */
#if (CONFIG_AULASENSE_BUZZER_GPIO == 43) || (CONFIG_AULASENSE_BUZZER_GPIO == 44)
#error "AulaSense: GPIO 43/44 es el UART0 real (prohibido)."
#endif
#if (CONFIG_AULASENSE_WS2812_GPIO == 43) || (CONFIG_AULASENSE_WS2812_GPIO == 44)
#error "AulaSense: GPIO 43/44 es el UART0 real (prohibido)."
#endif

/* 0/45/46 = strapping (BOOT/DC/Shutter). */
#if (CONFIG_AULASENSE_BUZZER_GPIO == 0) || (CONFIG_AULASENSE_BUZZER_GPIO == 45) || \
    (CONFIG_AULASENSE_BUZZER_GPIO == 46)
#error "AulaSense: GPIO 0/45/46 es strapping (prohibido)."
#endif
#if (CONFIG_AULASENSE_WS2812_GPIO == 0) || (CONFIG_AULASENSE_WS2812_GPIO == 45) || \
    (CONFIG_AULASENSE_WS2812_GPIO == 46)
#error "AulaSense: GPIO 0/45/46 es strapping (prohibido)."
#endif

/* 26-37 = QSPI de la flash interna y PSRAM octal (incluye 33/34, no disponibles). */
#if (CONFIG_AULASENSE_BUZZER_GPIO >= 26) && (CONFIG_AULASENSE_BUZZER_GPIO <= 37)
#error "AulaSense: GPIO 26-37 es QSPI flash/PSRAM octal (prohibido)."
#endif
#if (CONFIG_AULASENSE_WS2812_GPIO >= 26) && (CONFIG_AULASENSE_WS2812_GPIO <= 37)
#error "AulaSense: GPIO 26-37 es QSPI flash/PSRAM octal (prohibido)."
#endif

/* 22-25 no existen en el ESP32-S3 (y 33/34 ya quedan cubiertos arriba). */
#if (CONFIG_AULASENSE_BUZZER_GPIO >= 22) && (CONFIG_AULASENSE_BUZZER_GPIO <= 25)
#error "AulaSense: GPIO 22-25 no existe en el ESP32-S3."
#endif
#if (CONFIG_AULASENSE_WS2812_GPIO >= 22) && (CONFIG_AULASENSE_WS2812_GPIO <= 25)
#error "AulaSense: GPIO 22-25 no existe en el ESP32-S3."
#endif

/*
 * 4-18 = bus DVP de la cámara OV3660 (F24-c, solo bajo AULASENSE_IMAGEN=y). Ningún otro
 * periférico puede usarlos. EXCEPCIÓN documentada: el buzzer PIEZO ONBOARD de esta placa
 * (GOOUUU ESP32-S3-CAM v1.5) está cableado al GPIO14 — la pareja (pin 14, buzzer onboard)
 * es la única admitida; cualquier otro pin de ese rango queda prohibido, también para el
 * buzzer. Sin AULASENSE_IMAGEN (estado actual) la cámara no se inicializa (guarda propia de F24-c).
 */
#define AULASENSE_DVP_BUS_MIN 4
#define AULASENSE_DVP_BUS_MAX 18
#define AULASENSE_ONBOARD_BUZZER_PIN 14

#if (CONFIG_AULASENSE_BUZZER_GPIO >= AULASENSE_DVP_BUS_MIN) && \
    (CONFIG_AULASENSE_BUZZER_GPIO <= AULASENSE_DVP_BUS_MAX) && \
    (CONFIG_AULASENSE_BUZZER_GPIO != AULASENSE_ONBOARD_BUZZER_PIN)
#error "AulaSense: AULASENSE_BUZZER_GPIO cae en el bus DVP 4-18 (cámara). Solo el buzzer onboard en GPIO14 es excepción."
#endif
#if (CONFIG_AULASENSE_WS2812_GPIO >= AULASENSE_DVP_BUS_MIN) && \
    (CONFIG_AULASENSE_WS2812_GPIO <= AULASENSE_DVP_BUS_MAX)
#error "AulaSense: AULASENSE_WS2812_GPIO cae en el bus DVP 4-18 (cámara): el WS2812 onboard va en GPIO48."
#endif

/* Un mismo GPIO no puede tener dos supervisores (patrón de un solo dueño por pin). */
#if (CONFIG_AULASENSE_BUZZER_GPIO == CONFIG_AULASENSE_WS2812_GPIO)
#error "AulaSense: buzzer y WS2812 no pueden compartir el mismo GPIO."
#endif

/*
 * BLE NimBLE notify: NO implementado en F3 (gate de coexistencia R7 pendiente de números).
 * Falla ruidosamente en vez de dejar un módulo a medias silencioso.
 */
#if CONFIG_AULASENSE_BLE
#error "AulaSense: AULASENSE_BLE=y no está implementado en F3. Gate R7: decidir con .tmp/f3_metricas.json antes de activar."
#endif

#if !CONFIG_AULASENSE_FEEDBACK

/* Módulo apagado por Kconfig: stubs para que app_main.cpp enlace sin #ifdef en el vendor. */

void app_feedback_set_mqtt_publisher(app_feedback_publish_fn publish, app_feedback_connected_fn connected) {
    (void)publish;
    (void)connected;
}

bool app_feedback_init(void) {
    ESP_LOGI(TAG, "feedback deshabilitado por Kconfig (AULASENSE_FEEDBACK=n)");
    return false;
}

bool app_feedback_start_temp(void) { return false; }

void app_feedback_set_heap_publisher(app_feedback_publish_fn publish,
                                     app_feedback_connected_fn connected) {
    (void)publish;
    (void)connected;
}

void app_feedback_pausar(bool pausa) { (void)pausa; }

void app_feedback_on_state(bool motion, float score) {
    (void)motion;
    (void)score;
}

void app_feedback_publish_pending(void) {}

#else /* CONFIG_AULASENSE_FEEDBACK */

/* ------------------------------------------------------------------------------------------
 * Parámetros de comportamiento
 * ---------------------------------------------------------------------------------------- */

/* Tarea propia por DEBAJO del loop de Native (prio 5) y de los servicios de F24 (3-4). */
#define FEEDBACK_TASK_PRIORITY 3
#define FEEDBACK_TASK_STACK_SIZE 4096
#define FEEDBACK_QUEUE_LENGTH 4

/* LEDC: timer y canal PROPIOS (el vendor no usa LEDC; F24-c usará timer0/canal0 para el XCLK
 * de la cámara, así que el feedback se queda en timer2/canal2 para no colisionar nunca). */
#define BUZZER_LEDC_SPEED_MODE LEDC_LOW_SPEED_MODE
#define BUZZER_LEDC_TIMER LEDC_TIMER_2
#define BUZZER_LEDC_CHANNEL LEDC_CHANNEL_2
#define BUZZER_LEDC_DUTY_RES LEDC_TIMER_8_BIT
#define BUZZER_FREQ_HZ 880
#define BUZZER_DUTY 128 /* 50% de 8 bit: máxima amplitud sin saturar un piezo pasivo */
#define BUZZER_BEEP_MS 110

/* WS2812: azul del NeoPixel onboard por canal azul (GRB). Intensidad según estado. */
#define LED_IDLE_BLUE 24   /* azul tenue: limita corriente y no deslumbra en `idle` */
#define LED_MOTION_BLUE 200 /* azul brillante en `motion` (visible desde toda el aula) */

/* Die temp: periodo de publicación y tamaño del JSON. */
#define TEMP_PERIOD_US (30LL * 1000LL * 1000LL)
#define TEMP_PAYLOAD_MAX 96

/* F26 (SPEC §16.3): retain heap_interno por el MISMO canal periodico del parche (30 s,
 * patron del publisher de aulasense_temp). NUNCA confundir con free_memory_kb de
 * read_diagnostics (PSRAM-inclusive, ~8.2 MB). */
#define HEAP_RETAIN_PERIOD_US (30LL * 1000LL * 1000LL)
#define HEAP_PAYLOAD_MAX 128

/* ------------------------------------------------------------------------------------------
 * Estado del módulo
 * ---------------------------------------------------------------------------------------- */

typedef struct {
    bool motion;
    float score;
} feedback_event_t;

static app_feedback_publish_fn s_publish_fn = NULL;
static app_feedback_connected_fn s_connected_fn = NULL;

/* F26: publicador del retain heap_interno (lambda del app con suffix+retain horneados) y
 * pausa de telemetria para la descarga OTA (SPEC §16.1). */
static app_feedback_publish_fn s_heap_publish_fn = NULL;
static app_feedback_connected_fn s_heap_connected_fn = NULL;
static volatile bool s_pausado = false;

static QueueHandle_t s_state_queue = NULL;
static QueueHandle_t s_temp_queue = NULL;
static QueueHandle_t s_heap_queue = NULL;
static TaskHandle_t s_task = NULL;

static led_strip_handle_t s_strip = NULL;
static temperature_sensor_handle_t s_temp_sensor = NULL;
static bool s_temp_started = false;

static bool s_buzzer_ready = false;
static int64_t s_last_beep_us = 0;

/* Estado debounced propio (se compara para no actuar por evaluación, solo por transición). */
static portMUX_TYPE s_state_mux = portMUX_INITIALIZER_UNLOCKED;
static bool s_last_motion = false;

/* ------------------------------------------------------------------------------------------
 * Buzzer (GPIO14, LEDC PWM)
 * ---------------------------------------------------------------------------------------- */

static esp_err_t buzzer_init(void) {
    ledc_timer_config_t timer_cfg = {
        .speed_mode = BUZZER_LEDC_SPEED_MODE,
        .duty_resolution = BUZZER_LEDC_DUTY_RES,
        .timer_num = BUZZER_LEDC_TIMER,
        .freq_hz = BUZZER_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    esp_err_t err = ledc_timer_config(&timer_cfg);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "LEDC timer no configurado (%s): sin beep", esp_err_to_name(err));
        return err;
    }

    ledc_channel_config_t channel_cfg = {
        .gpio_num = CONFIG_AULASENSE_BUZZER_GPIO,
        .speed_mode = BUZZER_LEDC_SPEED_MODE,
        .channel = BUZZER_LEDC_CHANNEL,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = BUZZER_LEDC_TIMER,
        .duty = 0,
        .hpoint = 0,
    };
    err = ledc_channel_config(&channel_cfg);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "LEDC canal no configurado (%s): sin beep", esp_err_to_name(err));
        return err;
    }
    return ESP_OK;
}

/* Beep corto con cooldown. Corre en la tarea propia (prio 3): el vTaskDelay de 110 ms solo
 * retrasa este módulo, nunca el detector. */
static void buzzer_beep_onset(void) {
    if (!s_buzzer_ready) {
        return;
    }
    const int64_t now_us = esp_timer_get_time();
    const int64_t cooldown_us = (int64_t)CONFIG_AULASENSE_BUZZER_COOLDOWN_S * 1000000LL;
    if (s_last_beep_us != 0 && (now_us - s_last_beep_us) < cooldown_us) {
        ESP_LOGD(TAG, "beep omitido: cooldown de %d s vigente", CONFIG_AULASENSE_BUZZER_COOLDOWN_S);
        return;
    }
    s_last_beep_us = now_us;

    ledc_set_duty(BUZZER_LEDC_SPEED_MODE, BUZZER_LEDC_CHANNEL, BUZZER_DUTY);
    ledc_update_duty(BUZZER_LEDC_SPEED_MODE, BUZZER_LEDC_CHANNEL);
    vTaskDelay(pdMS_TO_TICKS(BUZZER_BEEP_MS));
    ledc_set_duty(BUZZER_LEDC_SPEED_MODE, BUZZER_LEDC_CHANNEL, 0);
    ledc_update_duty(BUZZER_LEDC_SPEED_MODE, BUZZER_LEDC_CHANNEL);

    ESP_LOGI(TAG, "beep onset %d Hz %d ms (cooldown %d s)", BUZZER_FREQ_HZ, BUZZER_BEEP_MS,
             CONFIG_AULASENSE_BUZZER_COOLDOWN_S);
}

/* ------------------------------------------------------------------------------------------
 * WS2812 (GPIO48, RMT vía espressif/led_strip). Supervisor único del pin: solo esta tarea.
 * ---------------------------------------------------------------------------------------- */

static esp_err_t led_init(void) {
    led_strip_config_t strip_cfg = {
        .strip_gpio_num = CONFIG_AULASENSE_WS2812_GPIO,
        .max_leds = 1,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
    };
    led_strip_rmt_config_t rmt_cfg = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10000000, /* 10 MHz: resolución de bit del WS2812 */
    };
    esp_err_t err = led_strip_new_rmt_device(&strip_cfg, &rmt_cfg, &s_strip);
    if (err != ESP_OK) {
        s_strip = NULL;
        ESP_LOGW(TAG, "WS2812 GPIO%d no inicializado (%s): el feedback visual queda apagado",
                 CONFIG_AULASENSE_WS2812_GPIO, esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "WS2812 GPIO%d inicializado (RMT, 1 LED GRB, 10 MHz)", CONFIG_AULASENSE_WS2812_GPIO);
    return ESP_OK;
}

static void led_apply(bool motion) {
    if (s_strip == NULL) {
        return;
    }
    const int blue = motion ? LED_MOTION_BLUE : LED_IDLE_BLUE;
    esp_err_t err = led_strip_set_pixel(s_strip, 0, 0, 0, (uint32_t)blue);
    if (err == ESP_OK) {
        err = led_strip_refresh(s_strip);
    }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "WS2812 no refrescado (%s)", esp_err_to_name(err));
        return;
    }
    ESP_LOGI(TAG, "WS2812 -> %s (azul %d/255)", motion ? "motion" : "idle", blue);
}

/* ------------------------------------------------------------------------------------------
 * Die temp (sensor interno del S3 — proxy de carga de cómputo, NO temperatura ambiente)
 * ---------------------------------------------------------------------------------------- */

static esp_err_t temp_sensor_init(void) {
    temperature_sensor_config_t cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);
    esp_err_t err = temperature_sensor_install(&cfg, &s_temp_sensor);
    if (err != ESP_OK) {
        s_temp_sensor = NULL;
        ESP_LOGW(TAG, "temperature_sensor no instalado (%s): sin die temp", esp_err_to_name(err));
        return err;
    }
    err = temperature_sensor_enable(s_temp_sensor);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "temperature_sensor no habilitado (%s): sin die temp", esp_err_to_name(err));
        return err;
    }
    return ESP_OK;
}

/* Muestrea, loguea en formato parseable y encola para publicar por MQTT. */
static void temp_sample_and_post(void) {
    if (s_temp_sensor == NULL || s_temp_queue == NULL) {
        return;
    }
    if (s_pausado) {
        return; /* OTA en curso: telemetria en pausa (el "agujero" es ESPERADO y documentado) */
    }
    float celsius = 0.0f;
    esp_err_t err = temperature_sensor_get_celsius(s_temp_sensor, &celsius);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "lectura de die temp fallida (%s)", esp_err_to_name(err));
        return;
    }

    /* Log parseable: evidencia del smoke y fallback si el broker no está disponible. */
    ESP_LOGI(TAG, "AULASENSE,T,die_temp_c=%.1f", (double)celsius);

    char payload[TEMP_PAYLOAD_MAX];
    const long long timestamp_ms = (long long)(esp_timer_get_time() / 1000LL);
    const int written = snprintf(payload, sizeof(payload),
                                 "{\"timestamp_ms\":%lld,\"die_temp_c\":%.1f}", timestamp_ms,
                                 (double)celsius);
    if (written <= 0 || written >= (int)sizeof(payload)) {
        ESP_LOGW(TAG, "payload de die temp truncado: se omite la publicación");
        return;
    }

    /* Degradación elegante: sin MQTT no hay publicación, pero buzzer/LED siguen. */
    if (s_publish_fn == NULL || s_connected_fn == NULL || !s_connected_fn()) {
        ESP_LOGW(TAG, "MQTT no conectado: die_temp solo en log");
        return;
    }
    if (xQueueSend(s_temp_queue, payload, 0) != pdTRUE) {
        ESP_LOGW(TAG, "cola de publicación llena: die_temp descartada");
    }
}

/*
 * F26 (SPEC §16.3): mide el heap INTERNO con heap_caps (NUNCA xPortGetFreeHeapSize: con
 * SPIRAM_USE_MALLOC mezcla PSRAM) y encola el retain heap_interno {internal_free_kb,
 * internal_largest_block_kb, ts_ms}. Se encola SIEMPRE (ventana 30 s, cola 2 con
 * latest-wins): el pump lo publica cuando el broker este conectado, y el retain queda
 * fresco en el broker entre conexiones.
 */
static void heap_sample_and_post(void) {
    if (s_heap_queue == NULL || s_pausado) {
        return;
    }
    char payload[HEAP_PAYLOAD_MAX];
    const long long ts_ms = (long long)(esp_timer_get_time() / 1000LL);
    const int written = snprintf(payload, sizeof(payload),
                                 "{\"internal_free_kb\":%u,\"internal_largest_block_kb\":%u,"
                                 "\"ts_ms\":%lld}",
                                 (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL |
                                                                    MALLOC_CAP_8BIT) / 1024U),
                                 (unsigned)(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL |
                                                                             MALLOC_CAP_8BIT) /
                                            1024U),
                                 ts_ms);
    if (written <= 0 || written >= (int)sizeof(payload)) {
        return;
    }
    if (xQueueSend(s_heap_queue, payload, 0) != pdTRUE) {
        /* Latest-wins: si la cola esta llena (broker caido 60+ s), expulsa el mas viejo. */
        char viejo[HEAP_PAYLOAD_MAX];
        if (xQueueReceive(s_heap_queue, viejo, 0) == pdTRUE) {
            (void)xQueueSend(s_heap_queue, payload, 0);
        }
    }
}

/* ------------------------------------------------------------------------------------------
 * Tarea propia del módulo (prioridad 3)
 * ---------------------------------------------------------------------------------------- */

static void feedback_task(void *arg) {
    (void)arg;
    /* El primer muestreo de die temp ocurre al cumplirse el primer periodo (30 s de vida). */
    int64_t last_temp_us = esp_timer_get_time();
    int64_t last_heap_us = esp_timer_get_time();
    feedback_event_t event;

    for (;;) {
        if (xQueueReceive(s_state_queue, &event, pdMS_TO_TICKS(250)) == pdTRUE) {
            if (event.motion) {
                led_apply(true);
                buzzer_beep_onset();
            } else {
                led_apply(false);
            }
        }

        const int64_t now_us = esp_timer_get_time();
        if ((now_us - last_temp_us) >= TEMP_PERIOD_US) {
            last_temp_us = now_us;
            temp_sample_and_post();
        }
        if ((now_us - last_heap_us) >= HEAP_RETAIN_PERIOD_US) {
            last_heap_us = now_us;
            heap_sample_and_post();
        }
    }
}

/* ------------------------------------------------------------------------------------------
 * API pública
 * ---------------------------------------------------------------------------------------- */

void app_feedback_set_mqtt_publisher(app_feedback_publish_fn publish, app_feedback_connected_fn connected) {
    s_publish_fn = publish;
    s_connected_fn = connected;
}

/* F26 (SPEC §16.3): publicador del retain heap_interno (el lambda del app hornea el suffix
 * "heap_interno" y retain=true sobre publish_suffix del transporte nativo). */
void app_feedback_set_heap_publisher(app_feedback_publish_fn publish,
                                     app_feedback_connected_fn connected) {
    s_heap_publish_fn = publish;
    s_heap_connected_fn = connected;
}

/* F26 (SPEC §16.1): pausa de TELEMETRIA durante la descarga OTA (die temp + heap_interno).
 * El buzzer/LED (feedback local, C11) NO se pausan: no tocan el transporte. */
void app_feedback_pausar(bool pausa) {
    s_pausado = pausa;
    ESP_LOGI(TAG, "telemetria %s (OTA)", pausa ? "PAUSADA" : "REANUDADA");
}

bool app_feedback_start_temp(void) {
#if CONFIG_AULASENSE_DIE_TEMP
    if (s_temp_started) {
        return true;
    }
    if (s_temp_sensor == NULL) {
        return false;
    }
    s_temp_started = true;
    ESP_LOGI(TAG, "die temp activa: publicacion cada %lld s (%s)",
             (long long)(TEMP_PERIOD_US / 1000000LL),
             s_publish_fn != NULL ? "topic aulasense_temp" : "solo log (sin publicador MQTT)");
    return true;
#else
    ESP_LOGI(TAG, "die temp deshabilitada por Kconfig (AULASENSE_DIE_TEMP=n)");
    return false;
#endif
}

bool app_feedback_init(void) {
    if (s_task != NULL) {
        return true;
    }

    s_state_queue = xQueueCreate(FEEDBACK_QUEUE_LENGTH, sizeof(feedback_event_t));
    s_temp_queue = xQueueCreate(2, TEMP_PAYLOAD_MAX);
    s_heap_queue = xQueueCreate(2, HEAP_PAYLOAD_MAX); /* F26: retain heap_interno (latest-wins) */
    if (s_state_queue == NULL || s_temp_queue == NULL || s_heap_queue == NULL) {
        ESP_LOGE(TAG, "no se pudo crear la cola del feedback: modulo apagado");
        return false;
    }

    /* Buzzer y LED son independientes: el fallo de uno no apaga el otro. */
    s_buzzer_ready = (buzzer_init() == ESP_OK);
    if (s_buzzer_ready) {
        ESP_LOGI(TAG, "buzzer GPIO%d listo (LEDC %d Hz, cooldown %d s)",
                 CONFIG_AULASENSE_BUZZER_GPIO, BUZZER_FREQ_HZ, CONFIG_AULASENSE_BUZZER_COOLDOWN_S);
    }
    const bool led_ready = (led_init() == ESP_OK);

#if CONFIG_AULASENSE_DIE_TEMP
    const bool temp_ready = (temp_sensor_init() == ESP_OK);
#else
    const bool temp_ready = false;
#endif

    if (led_ready) {
        led_apply(false); /* estado inicial: idle = azul tenue */
    }
    if (temp_ready) {
        (void)app_feedback_start_temp();
    }

    if (xTaskCreate(feedback_task, "aulasense_fb", FEEDBACK_TASK_STACK_SIZE, NULL,
                    FEEDBACK_TASK_PRIORITY, &s_task) != pdPASS) {
        s_task = NULL;
        ESP_LOGE(TAG, "no se pudo crear la tarea de feedback (prio %d)", FEEDBACK_TASK_PRIORITY);
        return false;
    }

    ESP_LOGI(TAG, "feedback listo: buzzer=%s led=%s die_temp=%s tarea prio %d",
             s_buzzer_ready ? "ok" : "off", led_ready ? "ok" : "off", temp_ready ? "ok" : "off",
             FEEDBACK_TASK_PRIORITY);
    return true;
}

void app_feedback_on_state(bool motion, float score) {
    if (s_state_queue == NULL) {
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
    if (!changed) {
        return;
    }

    ESP_LOGD(TAG, "transicion a %s (score %.3f)", motion ? "motion" : "idle", (double)score);
    feedback_event_t event = {.motion = motion, .score = score};
    if (xQueueSend(s_state_queue, &event, 0) != pdTRUE) {
        ESP_LOGW(TAG, "cola de estado llena: transicion a %s descartada", motion ? "motion" : "idle");
    }
}

void app_feedback_publish_pending(void) {
    if (s_temp_queue != NULL && s_publish_fn != NULL) {
        char payload[TEMP_PAYLOAD_MAX];
        while (xQueueReceive(s_temp_queue, payload, 0) == pdTRUE) {
            if (s_publish_fn(payload)) {
                ESP_LOGD(TAG, "die_temp publicada en aulasense_temp");
            } else {
                ESP_LOGW(TAG, "die_temp no publicada: MQTT sin conexion");
            }
        }
    }
    /* F26: retain heap_interno (publish_suffix "heap_interno" retain=true, horneado en el
     * lambda inyectado). Si el broker no esta conectado el payload VUELVE a la cola
     * (latest-wins: el de 30 s anterior pierde) para no quedar mudo hasta el proximo tick. */
    if (s_heap_queue != NULL && s_heap_publish_fn != NULL) {
        if (s_heap_connected_fn == NULL || s_heap_connected_fn()) {
            char payload[HEAP_PAYLOAD_MAX];
            while (xQueueReceive(s_heap_queue, payload, 0) == pdTRUE) {
                if (!s_heap_publish_fn(payload)) {
                    (void)xQueueSend(s_heap_queue, payload, 0);
                    break;
                }
            }
        }
    }
}

#endif /* CONFIG_AULASENSE_FEEDBACK */