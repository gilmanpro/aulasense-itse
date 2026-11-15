/*
 * app_imagen.h — AulaSense (parche F24-c): evidencia de imagen en el ONSET de motion.
 *
 * Módulo del derivado GPLv3 sobre el vendor ESPectre Native 3.2.0 (ver README.md de este
 * directorio, SPEC.md §3.2/§3.2.1 y §6.2/§7.1, AGENTS.md regla 2/14). Captura UN JPEG QVGA de
 * la OV3660 en la transición idle->motion y lo publica por MQTT (canal PRIMARIO desde F25-c:
 * envelope JSON+b64 en `{topic_prefix}/{device_id}/image`, retain=false); si el publish falla,
 * cae al receptor HTTP local de la ingesta (POST /internal/imagen, LAN :8001, Bearer token).
 * La foto es EVIDENCIA del evento; el evento MQTT (`motion`) sigue siendo la fuente de verdad y
 * NUNCA se espera por la imagen.
 *
 * Contrato de hilos (obligatorio, README §9):
 *  - app_imagen_on_state() y app_imagen_publish_pending() se llaman desde la tarea del loop
 *    de Native (prio 5): son O(1), sin captura, sin HTTP y sin esperas. El hook solo compara
 *    el estado previo y hace un xQueueSend con espera 0.
 *  - Toda la periferia (cámara, memoria, base64/sha256, MQTT de imagen y HTTP) vive en la tarea
 *    propia del módulo (prio 3, stack 8 KB): NUNCA en el loop del detector CSI.
 *  - app_imagen_set_mqtt_publisher()/app_imagen_set_mqtt_image() deben llamarse ANTES de
 *    app_imagen_init().
 *
 * F25-c (D-F25-1, SPEC §3.2.1/§6.2): la imagen del onset viaja por la conexión MQTT PERSISTENTE
 * del nodo (canal PRIMARIO, topic `{topic_prefix}/{device_id}/image`, retain=false, mensaje
 * NO-reemplazable del transporte vendor) y HTTP :8001 queda como FALLBACK (N2). Norma N1: NUNCA
 * se abre otra conexión MQTT por imagen; se reutiliza el publisher del cliente nativo.
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
 * `{topic_prefix}/{device_id}/aulasense_config` usando el MISMO cliente de Native.
 * Devuelve false si el broker no está conectado o la cola de publicación lo rechaza.
 */
typedef bool (*app_imagen_publish_fn)(const char *payload);

/** Sondeo de conexión MQTT del transporte de Native (sin bloquear). */
typedef bool (*app_imagen_connected_fn)(void);

/**
 * Publicador MQTT de la imagen (firma ampliada F25-c): recibe el topic ABSOLUTO ya compuesto
 * (`{topic_prefix}/{device_id}/image`) y el envelope JSON de SPEC §6.2.
 *
 * El app lo implementa sobre el transporte vendor como
 * `mqtt_transport.publish(topic, payload, false)` — es decir, la vía NO-reemplazable
 * (`publish()` en `mqtt_transport_esp_idf.cpp`, retain=false) y NO `publish_suffix()`, que marca
 * el mensaje como reemplazable y lo difiere por el high-water de 2048 B. Devuelve false si el
 * broker no está conectado o la cola de publicación del transporte lo rechaza (-> fallback HTTP).
 */
typedef bool (*app_imagen_publish_topic_fn)(const char *topic, const char *payload);

/**
 * Registra el publicador MQTT de eventos de captura (topic `aulasense_config`).
 * Sin publicador el módulo degrada a log (la captura y el upload siguen funcionando).
 */
void app_imagen_set_mqtt_publisher(app_imagen_publish_fn publish,
                                   app_imagen_connected_fn connected);

/**
 * Registra el canal MQTT PRIMARIO de la imagen (F25-c) y el `topic_prefix` del config runtime
 * (el mismo `EspectreDeviceConfig.topic_prefix` que usa el transporte vendor; vacío/NULL ->
 * `CONFIG_ESPECTRE_TOPIC_PREFIX`). El módulo compone y retiene
 * `{topic_prefix}/{device_id}/image` en `app_imagen_init()`.
 *
 * Sin esta llamada el módulo se comporta como F24-c (solo HTTP). `connected` puede ser NULL:
 * en ese caso se intenta publicar igual y el publisher decide.
 */
void app_imagen_set_mqtt_image(app_imagen_publish_topic_fn publish,
                               app_imagen_connected_fn connected, const char *topic_prefix);

/**
 * Inicializa el módulo: cola de onsets en PSRAM, buffers JPEG en PSRAM, cámara OV3660
 * (QVGA JPEG q12, fb_count=2, framebuffers en PSRAM) y la tarea propia (prio 3).
 * Idempotente.
 *
 * @param device_id Identificador de 16 hex del nodo, para la cabecera `X-Device-Id` del
 *                  upload (el app lo deriva de la MAC con el contrato del vendor).
 *                  Se copia; el puntero no se retiene.
 * @return true si la tarea quedó creada; false si el módulo está deshabilitado por Kconfig
 *         o no arrancó (el CSI y el feedback siguen funcionando igual).
 */
bool app_imagen_init(const char *device_id);

/**
 * Hook de estado: se llama en cada publicación de snapshot que Native ya decidió.
 * Internamente compara con el estado previo y solo encola en la transición
 * idle->motion (cooldown y gate de memoria se evalúan en la tarea, no aquí).
 *
 * @param motion Estado debounced de Native (MotionState::MOTION == true).
 * @param score  movement_metric de Native (0..1), solo informativo para el log.
 *
 * No bloquea: como máximo un xQueueSend con tiempo de espera 0.
 */
void app_imagen_on_state(bool motion, float score);

/**
 * Bombea la cola de eventos `aulasense_config` pendientes. La llama la tarea del loop de
 * Native, única dueña del transporte MQTT (misma regla que app_feedback).
 * O(1) cuando no hay nada pendiente.
 */
void app_imagen_publish_pending(void);

#ifdef __cplusplus
}
#endif