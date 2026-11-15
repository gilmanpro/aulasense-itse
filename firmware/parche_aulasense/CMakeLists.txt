# CMakeLists.txt — fragmento canónico del parche AulaSense (F3 + F24-c) para el app Native.
#
# NO es un componente independiente: se APLICA a
#   firmware/espectre/src/cpp/frontend/native/app/main/CMakeLists.txt
# (ver README.md §6 paso 3). El registro real vive allí; este fichero documenta el diff exacto
# para que el parche sea reproducible sobre un clon limpio del vendor tag 3.2.0.
#
# Cambios respecto al CMakeLists.txt del vendor:
#   1. SRCS: se añaden "app_feedback.c" (F3) y "app_imagen.c" (F24-c).
#   2. REQUIRES: se añade `driver` (LEDC del buzzer + temperature_sensor del die) y
#      `esp32-camera` (driver DVP de la OV3660, F24-c).
#      - El WS2812 usa el componente gestionado espressif/led_strip, declarado en
#        main/idf_component.yml: el component manager lo añade como MANAGED_REQUIRES de `main`,
#        así que NO se lista en REQUIRES (mismo patrón que usó el archive dashcam-ia).
#      - esp32-camera también se declara en main/idf_component.yml (espressif/esp32-camera).
#      - esp_http_client ya estaba en REQUIRES (F24-c lo usa para subir el JPEG).
#      - BLE (`bt`) NO se añade: AULASENSE_BLE queda en n (gate R7, sin decidir todavía).
#
# Nota: como `main` declara REQUIRES explícitos, IDF desactiva la comodidad "main depende de
# todos los componentes"; por eso las dependencias nuevas se declaran de forma explícita.

idf_component_register(
    SRCS
        "app_main.cpp"
        "app_feedback.c"
        "app_imagen.c"
    INCLUDE_DIRS
        "."
        "../../espectre"
        "../../.."
    REQUIRES
        espectre
        esp_event
        esp_netif
        esp_wifi
        freertos
        log
        lwip
        mqtt
        app_update
        esp_http_client
        esp_http_server
        esp_https_ota
        esp-tls
        mbedtls
        nvs_flash
        driver
        esp32-camera
)

set(_native_ota_channel "${NATIVE_OTA_CHANNEL}")
if(_native_ota_channel STREQUAL "")
    set(_native_ota_channel "$ENV{NATIVE_OTA_CHANNEL}")
endif()
if(_native_ota_channel STREQUAL "")
    set(_native_ota_channel "release")
endif()

if(_native_ota_channel STREQUAL "preview")
    target_compile_definitions(
        ${COMPONENT_LIB}
        PRIVATE ESPECTRE_OTA_PREVIEW_BUILD=1
    )
elseif(_native_ota_channel STREQUAL "develop")
    target_compile_definitions(
        ${COMPONENT_LIB}
        PRIVATE ESPECTRE_OTA_DEVELOP_BUILD=1
    )
elseif(NOT _native_ota_channel STREQUAL "release")
    message(FATAL_ERROR "Invalid Native OTA channel: ${_native_ota_channel}")
endif()