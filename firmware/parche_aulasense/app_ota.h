/*
 * app_ota.h — AulaSense (parche F26, módulo NUEVO): OTA real por comando MQTT propio.
 *
 * Módulo del derivado GPLv3 sobre el vendor ESPectre Native 3.2.0 (ver README.md de este
 * directorio §14, SPEC.md §16.1, PLAN §13.2 D-F26-1/2/3 y el informe .tmp/inv_ota_provision.md).
 *
 * Qué hace:
 *  - Descarga firmware propio por HTTP PLANO con verificación SHA-256 + size del MANIFIESTO
 *    {version,url,sha256,size} servido por el estático WSL (decisión D3 del dueño: HTTP+SHA
 *    +rollback; TLS post-entrega). Usa SOLO `esp_ota_ops` + `esp_http_client` + mbedtls:
 *    NUNCA `esp_https_ota` (por eso NO hace falta CONFIG_OTA_ALLOW_HTTP) y NUNCA el canal
 *    OTA del vendor (URL hardcodeada a GitHub + https obligatorio + overrides rechazados:
 *    ota_protocol.cpp:78-107, ota_service_https.cpp:44,592 — informe A §2.2).
 *  - Disparador (D-F26-2): comando por el MISMO broker por el que la placa ya publica:
 *    topic `{topic_prefix}/{device_id}/aulasense_ota` (sufijo propio; el parser vendor de
 *    `commands/request` rechaza comandos desconocidos y parchearlo está prohibido).
 *    Envelope: {"command_id":"<1-64 ASCII>","command":"aulasense_ota","action":"check"|"apply"}.
 *    Se suscribe con la API PÚBLICA transport.subscribe() sobre la MISMA conexión MQTT
 *    persistente (norma N1: cero conexiones extra). Las suscripciones del transporte
 *    sobreviven reconexiones (contrato IMqttTransport).
 *  - Secuencia apply: gate heap INTERNO (AULASENSE_OTA_HEAP_MIN_KB / _BLOCK_MIN_KB) →
 *    OTA_INICIO → pausa captura (app_imagen_pausar) + telemetría (app_feedback_pausar) →
 *    streaming GET al slot inactivo (buffer PSRAM 4 KB, SHA-256 ACUMULADO EN STREAMING —
 *    decisión: el hash verifica exactamente los bytes escritos, sin releer 1.4 MB de flash;
 *    esp_ota_end valida la cabecera antes de la comparación) → verify sha256+size →
 *    esp_ota_set_boot_partition → OTA_OK + retain "rebooting" → esp_restart(). Cualquier
 *    fallo ANTES de set_boot → OTA_ERROR {etapa,...} + reanudar + slot actual intacto.
 *  - Rollback (D-F26-3, obligatorio): con CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y la primera
 *    arranque de imagen nueva llega en ESP_OTA_IMG_PENDING_VERIFY. Self-check periódico:
 *    Wi-Fi GOT_IP + MQTT conectado + runtime armed CONTINUOS durante
 *    AULASENSE_OTA_SELFCHECK_S s (default 90, range 60..120) →
 *    esp_ota_mark_app_valid_cancel_rollback() + retain "valid". Si no se cumple en
 *    SELFCHECK_S + 120 s de gracia → esp_ota_mark_app_invalid_rollback_and_reboot(): el
 *    bootloader revierte al slot bueno sin tocar UART (decisión documentada: un
 *    esp_restart simple NO revierte — el estado sigue pending-verify y la imagen arrancaría
 *    otra vez; la API canónica de fallo de self-check es la de marcado invalid).
 *  - Observabilidad (SPEC §16.4): estado retain `ota` {estado: idle|checking|downloading|
 *    pending_verify|verifying|rebooting|valid|error, version_actual, version_objetivo,
 *    sha256_ok, detalle, ts_ms} re-publicado en cada conexión MQTT; eventos
 *    `aulasense_config`: OTA_CHECK, OTA_INICIO, OTA_OK, OTA_ERROR y FIRMWARE (versión al
 *    arrancar: {version, compile_time, idf, build_sha256_prefix} — sha leido de NVS si la
 *    imagen llegó por OTA; vacío en builds flasheados por UART, honesto).
 *  - Chequeo periódico AULASENSE_OTA_CHECK_MIN (default 0 = DESACTIVADO, reproducibilidad
 *    académica del curso; >0 solo publica OTA_CHECK, jamás auto-apply).
 *  - Sin CONFIG_AULASENSE_OTA_MANIFEST_URL ("" default) el módulo queda INERTE (no hay
 *    destino de descarga): solo mantiene el enganche compilado.
 *
 * Contrato de hilos (obligatorio):
 *  - El callback del transporte (`app_ota_on_message`) corre en la tarea del loop de Native
 *    (prio 5): SOLO valida el envelope y postea a la cola (xQueueSend espera 0). Nada de
 *    HTTP/flash desde ahí.
 *  - Toda la acción (manifiesto, descarga, esp_ota_*, sha, gates, self-check) vive en la
 *    tarea propia "aulasense_ota" (prio 4, stack 8192 — NUNCA prio 5 del loop, ENMIENDA E7).
 *  - La publicación MQTT de eventos/retains se bombea desde el loop
 *    (`app_ota_publish_pending()`), única dueña del transporte (misma regla N1 que
 *    app_feedback/app_imagen/app_provision).
 *  - app_ota_set_transport()/app_ota_set_identity() antes de app_ota_init().
 *
 * Secretos: NINGUNO. La URL del manifiesto viene de CONFIG_AULASENSE_OTA_MANIFEST_URL
 * (sdkconfig.defaults.local gitignored, generado desde .env; NUNCA commiteada). El bin es
 * dato no confidencial (GPLv3 fuente pública); la integridad la firma el SHA-256 del
 * manifiesto servido por el mismo túnel (trade-off HTTP plano documentado, D-F26-1/D3).
 *
 * Comentarios en español, identificadores en inglés (regla del repo AulaSense).
 */
#pragma once

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Handler de mensaje MQTT registrado por el módulo (corre en la tarea del loop de Native). */
typedef void (*app_ota_msg_fn)(const char *topic, const char *payload);

/**
 * Suscripción al topic ABSOLUTO del comando (`{prefix}/{device_id}/aulasense_ota`) sobre el
 * transporte de Native (API pública `subscribe(topic, callback)` de mqtt_transport_esp_idf.h:41).
 * Implementado por app_main.cpp como lambda capture-less que envuelve el callback C.
 */
typedef bool (*app_ota_subscribe_fn)(const char *topic, app_ota_msg_fn on_message);

/**
 * Publicación a topic ABSOLUTO (retain `ota` va a `{prefix}/{device_id}/ota`): envuelve
 * `publish(topic, payload, retain)` del transporte nativo (vía NO-reemplazable).
 */
typedef bool (*app_ota_publish_topic_fn)(const char *topic, const char *payload, bool retain);

/**
 * Publicador de eventos en el topic aditivo `{prefix}/{device_id}/aulasense_config`
 * (publish_suffix no-reemplazable, mismo canal que app_imagen/app_provision).
 */
typedef bool (*app_ota_publish_config_fn)(const char *payload);

/** Sondeo de conexión MQTT del transporte de Native (sin bloquear). */
typedef bool (*app_ota_connected_fn)(void);

/** Sondeo "runtime armed": setup del frontend completo y snapshot listo para publicar. */
typedef bool (*app_ota_runtime_ready_fn)(void);

/**
 * Registra los handles del transporte. Debe llamarse ANTES de app_ota_init().
 * Sin ellos el módulo degrada a log (no puede suscribirse ni publicar).
 */
void app_ota_set_transport(app_ota_subscribe_fn subscribe,
                           app_ota_publish_topic_fn publish_topic,
                           app_ota_publish_config_fn publish_config,
                           app_ota_connected_fn connected);

/**
 * Registra el sondeo de "CSI/runtime armado" para el self-check de rollback
 * (app_main.cpp lo implementa sobre g_frontend->is_setup_complete() + snapshot.ready_to_publish).
 */
void app_ota_set_runtime_ready(app_ota_runtime_ready_fn runtime_ready);

/**
 * Compone los topics absolutos (comando `aulasense_ota` y retain `ota`) a partir del
 * device_id (16 hex) y del topic_prefix del config runtime (vacío/NULL -> CONFIG_ESPECTRE_TOPIC_PREFIX,
 * igual que app_imagen). Debe llamarse ANTES de app_ota_init().
 */
void app_ota_set_identity(const char *device_id, const char *topic_prefix);

/**
 * Callback del transporte para el topic de comando. NO bloquea: valida el envelope
 * ({command:"aulasense_ota", action:check|apply}) y postea a la cola de la tarea OTA.
 * Lo invoca app_main.cpp desde el lambda que enruta `transport.subscribe()`.
 */
void app_ota_on_message(const char *topic, const char *payload);

/**
 * Inicializa el módulo: crea cola + tarea propia (prio 4), suscribe el topic de comando,
 * evalúa el estado de rollback pendiente del bootloader (arranque post-OTA → self-check),
 * crea el timer de chequeo periódico si AULASENSE_OTA_CHECK_MIN > 0 y marca el retain
 * inicial + evento FIRMWARE para el primer connected(). Idempotente.
 * @return true si quedó operativo; false si está deshabilitado/inerte (el resto del
 *         firmware sigue funcionando igual).
 */
bool app_ota_init(void);

/**
 * Bombea la cola de eventos/retains `aulasense_config`/`ota`. La llama la tarea del loop de
 * Native (única dueña del transporte). O(1) cuando no hay nada pendiente.
 */
void app_ota_publish_pending(void);

#ifdef __cplusplus
}
#endif
