/*
 * app_provision.h — AulaSense (parche F24-d): AP de emergencia + portal privado /cfg/<token>.
 *
 * Módulo del derivado GPLv3 sobre el vendor ESPectre Native 3.2.0 (ver README.md de este
 * directorio §13, SPEC.md §3.3 y ENMIENDAS E2/E3/E4/E5/E7 de .tmp/f24a_spec_implementable.md,
 * AGENTS.md "Provisioning AP privado"). Es ADITIVO: no toca el detector CSI, el canal MQTT
 * existente ni la mecánica de imágenes (F24-c/F25-c), y PRESERVA el recovery nativo
 * (BOOT 3 s → Improv Serial sigue funcionando).
 *
 * Qué hace:
 *  - Lleva SU PROPIO contador de fallos STA observando WIFI_EVENT_STA_DISCONNECTED /
 *    IP_EVENT_STA_GOT_IP (ENMIENDA E2: el Native nunca "falla de forma permanente": su pila
 *    re-intenta con max_retry=8 + back-off de 30 s en bucle). Al cruzar
 *    AULASENSE_PROVISION_RETRIES (o sin credenciales en el arranque) levanta SoftAP
 *    "AULASENSE-CFG-<4 hex>" (ENMIENDA E4: 2 últimos bytes del MAC) canal 1, max_connection 4.
 *  - Portal SOLO en la ruta privada http://192.168.4.1/cfg/<token> donde token = 8 hex de una
 *    clave efímera generada con esp_fill_random y persistida en NVS (namespace
 *    "aulasense_prov", clave "cfg_token"). Se IMPRIME por serial al activarse el AP
 *    (decision F24-d: también en re-activaciones, ver README §13 — nunca por MQTT ni en
 *    endpoints no autenticados). Ruta o token incorrectos → 404 NEUTRO sin pista
 *    (ENMIENDA E5: catch-all bajo "/cfg" + consttime_memequal, anti-timing).
 *  - Flujo de guardado (ENMIENDA E3, radio única del S3): POST /cfg/<token>/wifi → NVS →
 *    AP OFF → WIFI_MODE_STA PURO → esp_wifi_connect() → si falla, re-levantar AP.
 *    NUNCA WIFI_MODE_APSTA cross-canal para reconectar.
 *  - Endpoints: GET /cfg/<token> (formulario text-only), GET /cfg/<token>/estado (JSON sin
 *    secretos), POST /cfg/<token>/wifi (Origin/Referer guard 403, cooldown NVS 5 s 429,
 *    escritura solo si las credenciales CAMBIAN, NUNCA loguea/renderiza SSID/pass).
 *  - "AP off: 30 s tras STA OK" (AULASENSE_PROVISION_AP_OFF_S): en el flujo E3 el AP se
 *    apaga ANTES del intento STA; el timer cubre solo el caso defensivo GOT_IP-con-AP
 *    (p. ej. provisoning por Improv mientras el AP está arribado) → vuelve a STA puro.
 *  - Eventos aulasense_config (SPEC §6.1, ya en el enum del servidor): AP_FALLBACK,
 *    CFG_GUARDADA, STA_RECONECTADO — detalle SIN secretos (solo longitudes/flags).
 *
 * Contrato de hilos (patrón del archive app_portal.c + reglas del vendor):
 *  - Los handlers de eventos WiFi/IP son SOLO registro (xQueueSend con espera 0, SIN log):
 *    toda la acción vive en la tarea propia "aulasense_prov" (prio 3, stack 8192).
 *  - El httpd del portal corre a prio 4, stack 8192 (ENMIENDA E7: NUNCA en el loop=5 del
 *    detector). El POST escribe NVS en su propia tarea y delega la operación de radio a la
 *    tarea del módulo vía la cola (nunca esp_wifi_* desde el handler del httpd).
 *  - app_provision_set_publisher() debe llamarse ANTES de app_provision_init().
 *  - app_provision_publish_pending() se bombea desde la tarea del loop de Native (única
 *    dueña del transporte MQTT, misma regla que app_feedback/app_imagen).
 *
 * Secretos: NINGUNO hardcodeado. La pass del AP (opcional, WPA2) viene de
 * CONFIG_AULASENSE_PROVISION_AP_PASS vía menuconfig/.env (vacio = AP ABIERTO con aviso,
 * decision D-F24-4) y NUNCA se loguea. Las credenciales WiFi del portal van a NVS
 * (namespace "aulasense_wifi"). El token del portal se genera por codigo
 * (esp_fill_random) y se consulta por el log serial.
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
 * `{topic_prefix}/{device_id}/aulasense_config` usando el MISMO cliente de Native
 * (publish_suffix NO-reemplazable, igual que app_imagen). Devuelve false si el broker no
 * está conectado (el evento se reintenta con tope y luego se descarta: el serial es la
 * fuente de verdad durante el fallback, porque sin STA no hay MQTT).
 */
typedef bool (*app_provision_publish_fn)(const char *payload);

/** Sondeo de conexión MQTT del transporte de Native (sin bloquear). */
typedef bool (*app_provision_connected_fn)(void);

/**
 * Registra el publicador de eventos de provisioning (topic `aulasense_config`).
 * Sin publicador el módulo degrada a log (el AP y el portal siguen funcionando).
 */
void app_provision_set_publisher(app_provision_publish_fn publish,
                                 app_provision_connected_fn connected);

/**
 * Inicializa el módulo: registra handlers de eventos WiFi/IP (aditivos), crea la cola de
 * trabajo y la tarea propia (prio 3). NO levanta el AP aquí: el disparo es el contador de
 * fallos o la comprobación "sin credenciales" tras la gracia de arranque (20 s).
 * Idempotente. Si AULASENSE_PROVISION=n es un no-op que devuelve false.
 */
bool app_provision_init(void);

/**
 * Bombea la cola de eventos `aulasense_config` pendientes. La llama la tarea del loop de
 * Native (igual que app_feedback_publish_pending/app_imagen_publish_pending). O(1) cuando
 * no hay nada pendiente.
 */
void app_provision_publish_pending(void);

/** true si el SoftAP de emergencia está arribado ahora mismo (para estado/logs). */
bool app_provision_ap_activa(void);

#ifdef __cplusplus
}
#endif
