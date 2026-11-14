/*
 * app_feedback.h — AulaSense (parche F3): feedback local y die temp del ESP32-S3.
 *
 * Módulo del derivado GPLv3 sobre el vendor ESPectre Native 3.2.0 (ver README.md de este
 * directorio, SPEC.md §3.1 y AGENTS.md §GPIO). Cubre las condiciones de curso C11 (alerta
 * audible/visual local sin red: buzzer GPIO14 + WS2812 GPIO48) y C6 (segunda fuente de datos
 * con $0: temperatura interna del die — proxy de carga de cómputo, NO temperatura ambiente).
 *
 * Contrato de hilos (obligatorio, ver README §3):
 *  - app_feedback_on_state() y app_feedback_publish_pending() se llaman desde la tarea del
 *    loop de Native (prio 5): son O(1), sin vTaskDelay y sin log pesado. El trabajo real
 *    (LEDC/RMT/sensor/MQTT) lo hace la tarea propia del módulo (prio 3) o la cola del loop.
 *  - app_feedback_set_mqtt_publisher() debe llamarse ANTES de app_feedback_init().
 *
 * Comentarios en español, identificadores en inglés (regla del repo AulaSense).
 */
#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Publicador MQTT inyectado desde el app: publica `payload` en el topic aditivo
 * `{topic_prefix}/{device_id}/aulasense_temp` usando el MISMO cliente de Native.
 * Devuelve false si el broker no está conectado o la cola de publicación lo rechaza.
 */
typedef bool (*app_feedback_publish_fn)(const char *payload);

/** Sondeo de conexión MQTT del transporte de Native (sin bloquear). */
typedef bool (*app_feedback_connected_fn)(void);

/**
 * Registra el publicador MQTT del nodo. Lo llama app_main.cpp justo después de que
 * Native haya configurado su transporte. Sin publicador, el módulo solo loguea (degradación).
 */
void app_feedback_set_mqtt_publisher(app_feedback_publish_fn publish, app_feedback_connected_fn connected);

/**
 * Inicializa buzzer (LEDC), WS2812 (RMT/led_strip), sensor de die temp y la tarea propia
 * (prioridad baja: nunca compite con el detector). Idempotente.
 *
 * @return true si la tarea de feedback quedó creada; false si el módulo está deshabilitado
 *         por Kconfig o no pudo arrancar (el resto del firmware sigue funcionando).
 */
bool app_feedback_init(void);

/**
 * Arranca (idempotente) la publicación periódica de die temp cada 30 s.
 * Se invoca desde app_feedback_init(); queda público para pruebas/rearranque.
 */
bool app_feedback_start_temp(void);

/**
 * Hook de estado: se llama en CADA evaluación/transición que Native ya decidió.
 * Internamente compara con el estado previo y solo encola en la transición
 * idle->motion (beep + LED brillante) o motion->idle (LED tenue).
 *
 * @param motion Estado debounced de Native (MotionState::MOTION == true).
 * @param score  movement_metric de Native (0..1), solo informativo para el log.
 *
 * No bloquea: como máximo un xQueueSend con tiempo de espera 0.
 */
void app_feedback_on_state(bool motion, float score);

/**
 * Bombea la cola de publicación pendiente (die temp). La llama la tarea del loop de Native,
 * que es la única que puede tocar su transporte MQTT sin condición de carrera.
 * O(1) cuando no hay nada pendiente.
 */
void app_feedback_publish_pending(void);

#ifdef __cplusplus
}
#endif