/*
 * app_provision.h — AulaSense (parche F24-d; F26: provisioning FIJO, SPEC §16.2):
 * AP de emergencia con SSID/pass fijos + portal en rutas explicitas.
 *
 * Módulo del derivado GPLv3 sobre el vendor ESPectre Native 3.2.0 (ver README.md de este
 * directorio §14 F26 y §13 F24-d, SPEC.md §16.2 y ENMIENDAS E2/E3/E4/E7). Es ADITIVO: no toca
 * el detector CSI, el canal MQTT existente ni la mecánica de imágenes (F24-c/F25-c/F26), y
 * PRESERVA el recovery nativo (BOOT 3 s → Improv Serial sigue funcionando).
 *
 * Qué hace (F26):
 *  - Lleva SU PROPIO contador de fallos STA observando WIFI_EVENT_STA_DISCONNECTED /
 *    IP_EVENT_STA_GOT_IP (ENMIENDA E2: el Native nunca "falla de forma permanente": su pila
 *    re-intenta con max_retry=8 + back-off de 30 s en bucle). Al cruzar
 *    AULASENSE_PROVISION_RETRIES (o sin credenciales en el arranque) levanta SoftAP con SSID
 *    FIJO CONFIG_AULASENSE_PROVISION_AP_SSID (default "AULASENSE-CFG", SIN sufijo MAC: una
 *    sola unidad en el proyecto; colisión multi-placa = procedimiento "1 placa a la vez"),
 *    canal 1, max_connection 4.
 *  - Autenticación: CONFIG_AULASENSE_PROVISION_AP_PASS (default "" = AP ABIERTO con aviso,
 *    rama de degradación conservada). Con valor, WPA2-PSK con guarda de COMPILACIÓN: pass no
 *    vacía exige 8..63 caracteres (informe A §1.1: una pass de menos de 8 hacía fallar
 *    esp_wifi_set_config ANTES de publicar nada y el recovery quedaba mudo). El valor real
 *    viaja SOLO por .env → sdkconfig.defaults.local (gitignored); NUNCA commiteado.
 *  - Portal SIN token (el mecanismo de ruta privada con token queda ELIMINADO del código; la
 *    clave NVS antigua no se borra en campo: es inofensiva y ya no se lee ni se escribe).
 *    Rutas EXPLÍCITAS (max_uri_handlers=4):
 *      GET  /cfg         → formulario text-only (action /cfg/wifi, pie /cfg/estado)
 *      GET  /cfg/estado  → JSON sin secretos (wifi_sta, ap_activa, heap interno/psram)
 *      POST /cfg/wifi    → NVS → AP OFF → STA PURO → connect (ENMIENDA E3 intacta)
 *    Cualquier otra URI → 404 neutro del propio httpd.
 *  - Guardas HEREDADAS INTACTAS: Origin/Referer guard (host_es_propio anti-sufijo → 403),
 *    cooldown NVS 5 s → 429, body ≤255 B, escritura solo si cambia, cabeceras duras
 *    (nosniff/DENY/no-referrer/no-store/CSP text-only) y NUNCA loguear SSID/pass (solo
 *    longitudes).
 *  - "AP off: 30 s tras STA OK" (AULASENSE_PROVISION_AP_OFF_S): en el flujo E3 el AP se
 *    apaga ANTES del intento STA; el timer cubre solo el caso defensivo GOT_IP-con-AP
 *    (p. ej. provisioning Improv concurrente) → vuelve a STA puro.
 *  - Eventos aulasense_config (SPEC §6.1/§16.4): AP_FALLBACK, CFG_GUARDADA, STA_RECONECTADO
 *    y AP_ERROR {etapa, esp_err} en TODAS las ramas de fallo del levantamiento del AP
 *    (set_mode/set_config/wifi_start/httpd) — el recovery jamás queda mudo.
 *
 * Contrato de hilos (patrón del archive app_portal.c + reglas del vendor):
 *  - Los handlers de eventos WiFi/IP son SOLO registro (xQueueSend con espera 0, SIN log):
 *    toda la acción vive en la tarea propia "aulasense_prov" (prio 3, stack 8192).
 *  - El httpd del portal corre a prio 4, stack 8192 (ENMIENDA E7: NUNCA en el loop=5 del
 *    detector). El POST escribe NVS en su propia tarea y delega la operación de radio a la
 *    tarea del módulo vía la cola (nunca esp_wifi_* desde el handler del httpd).
 *  - app_provision_set_publisher() debe llamarse ANTES de app_provision_init().
 *  - app_provision_publish_pending() se bombea desde la tarea del loop de Native (única
 *    dueña del transporte MQTT, misma regla que app_feedback/app_imagen/app_ota).
 *
 * Secretos: NINGUNO hardcodeado. La pass del AP viene de CONFIG_AULASENSE_PROVISION_AP_PASS
 * (vía .env → sdkconfig.defaults.local, decisión D-F26-4) y NUNCA se loguea. Las credenciales
 * WiFi del portal van a NVS (namespace "aulasense_wifi").
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
