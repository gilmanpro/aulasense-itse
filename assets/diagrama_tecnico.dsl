// AulaSense - diagrama tecnico (Structurizr C4) - F26-d (D-F26-11, SPEC seccion 16.10)
// Fuente de verdad topologica: SPEC.md seccion 16.10 (arquitectura REAL), 16.9 (tuneles/puertos OTA),
// 16.1 (app_ota: comando, manifiesto, SHA-256, rollback, self-check), 16.2 (provisioning FIJO sin token),
// 16.3 (observabilidad camara + gate heap INTERNO), 16.4 (sufijos MQTT nuevos), 16.5 (esquema SQLite F26),
// 16.6 (Clef: cascada clef-flash a clef), 16.7 (salud del sistema). Puertos verificados en terreno:
// tuneles running en server/datos/tuneles_estado.json (pub-aulasense-8100 a VPS 10062;
// pub-aulasense-ota a VPS 10064 - puerto final validado en F26-b).
// Cadena ASCII deliberada: el exportador DOT escribe con el charset de la JVM y los
// caracteres no-ASCII (<, >, secciones, dashes largos) rompen las etiquetas HTML-like.
// Regenerar: .tmp/f26d_diagrama.md (structurizr-cli validate + export -format dot
// + graphviz dot -Tsvg/-Tpng -Gdpi=130 por vista + mmdc para el flujo F26).

workspace "AulaSense" "Arquitectura tecnica REAL F26 - deteccion de movimiento por Wi-Fi CSI edge-first (ESP32-S3 GOOUUU) + broker Mosquitto en VPS (SOLO broker y tuneles) + servidor TODO en WSL Debian (ingesta, dashboard, vision, Clef, OTA) + dashboard publico via tunel SSH con login. Cero asunciones LAN: el servidor jamas alcanza a la placa (SPEC 16.10)." {

  model {

    // ---------- Actores (C4-1) ----------
    aula = person "Aula (personas)" "Su movimiento perturba el canal Wi-Fi y se captura como CSI. Sin microfono, sin caras-ML: privacidad acotada local-only. Ojo honesto: idle no significa sala vacia."
    profesor = person "Profesor / Operador" "Monitoriza en vivo desde la URL publica (login), marca verdad terreno, gestiona galeria y retencion, ejecuta pasadas de vision y Clef, y dispara actualizaciones OTA por MQTT (remoto) o el portal (in situ)."

    // ---------- Sistemas externos ----------
    qwen = softwareSystem "Qwen Cloud (Token Plan)" "API de vision qwen3.8-flash (allowlist TP exact-string) con DASHSCOPE_API_KEY sk-sp. La key sk-sp es SOLO para modo B agente-mediado (ToS: prohibido daemon autonomo). Rate limits 120/dia + 20/h, dedupe, backoff, Paso 0 medido."
    openrouter = softwareSystem "OpenRouter Decisions API (Clef)" "POST a openrouter.ai/api/alpha/decisions con OPENROUTER_API_KEY (del .env unico, jamas impresa). Preguntas tipadas: choice de taxonomia (6 valores), noul_cubre, noul_calidad, score_actividad."
    openmeteo = softwareSystem "Open-Meteo API" "Meteorologia horaria gratuita, sin clave."

    // ---------- Sistema: toda la solucion desplegada ----------
    aulasense = softwareSystem "AulaSense" "Detector neuronal ESPectre (MLP 529 parametros) 100 por ciento en el ESP32-S3; todo el servidor (ingesta, dashboard, workers, OTA) en WSL debian-aulasense; el VPS es SOLO broker Mosquitto y tuneles SSH wsl-port; la placa es 100 por ciento OUTBOUND (jamas alcanzada por el servidor); credenciales en el .env unico (gitignored, generadas solo por codigo)." {

      // ===== Zona: aula remota =====
      group "Aula (sitio remoto) - red Wi-Fi 2.4 GHz - placa 100 por ciento OUTBOUND" {
        nodo = container "Nodo ESP32-S3 (GOOUUU v1.5)" "device_id real 2e6f0c909e30673e - firmware derivado: ESPectre 3.2.0 (GPLv3) + parche_aulasense (app_ota NUEVO F26), ESP-IDF v5.5.6, SOLO energizada. CSI crudo NUNCA sale del chip. Feedback local: buzzer GPIO14 (beep 880 Hz al onset) + WS2812 GPIO48 por estado. Todas las conexiones (MQTT y HTTP OTA) las INICIA la placa; el servidor jamas abre conexion hacia ella." "C/C++ ESP-IDF 5.5.6" {
          csi = component "Captura CSI" "Radio HT20 2.4 GHz, ~100 muestras/s con trafico ping/DNS al gateway (modos conmutables si el aula bloquea ICMP)." "ESPectre"
          mlp = component "Detector MLP (IA en el borde)" "Perfil High Accuracy 8-24-12-1, 529 parametros, sin calibracion. Decide idle/motion cada 250 ms. Metricas edge via read_diagnostics (poll 30 s): detection_avg_us, pps, heap, RSSI." "ESPectre"
          camara = component "Camara OV3660 - evidencia accesoria" "1 JPEG QVGA 320x240 calidad 12 SOLO al onset idle a motion, cooldown 15 s, tarea de baja prioridad (nunca el loop del detector), cola de onsets drop-oldest. Gate de heap INTERNO (F26, MALLOC_CAP_INTERNAL, no PSRAM): free menor que 120 KB o bloque menor que 64 KB desactiva la captura SIN latch (re-evalua en cada onset, histereis +20 KB); IMAGEN_INIT y CAMARA_ERROR publicados; estado retain imagen_estado re-publicado en cada connected()." "ESP32 CAM JPEG"
          tx = component "Transporte MQTT (outbox)" "Publica eventos+health JSON y el topic image (b64 hasta 16 KB, QoS 0, retain=false) + los retians ota, imagen_estado y heap_interno. Recibe el comando aulasense_ota por transport.subscribe en la MISMA conexion persistente (patron N1: prohibida otra conexion). Parche documentado del outbox vendor de 8192 a 32768 bytes (F25-c). Autentica como usuario electiva." "mqtt_transport_esp_idf"
          prov = component "Provisioning FIJO F26 (solo in situ)" "AP AULASENSE-CFG fijo (SSID sin sufijo MAC, canal 1) WPA2 con pass canonica (solo en .env + NOTES; jamas en este diagrama). Portal SIN token: GET /cfg (form HTML), GET /cfg/estado (JSON sin secretos), POST /cfg/wifi (guarda NVS, AP OFF, STA puro). Guardas heredadas: Origin/Referer 403, cooldown 429, body hasta 255 B, nunca loguea pass. AP_ERROR en TODAS las ramas de fallo del AP. BOOT 3 s a Improv nativo. Solo HTTP Direct :62587 sigue LAN-only." "C Improv"
          ota = component "OTA (app_ota, modulo NUEVO F26)" "esp_ota_ops + esp_http_client + mbedtls SHA-256 (NO esp_https_ota ni el canal vendor). Disparada por el comando MQTT aulasense_ota (check o apply). Gate de heap interno (120/64 KB) antes de empezar. Descarga HTTP del manifiesto y el bin por el puerto publico del tunel OTA (10064); verifica SHA-256 y size ANTES de set_boot. Slot inactivo (particiones dual-app ota_0/ota_1, 0x1E0000 c/u, INTACTAS). Rollback del bootloader (CONFIG_APP_ROLLBACK_ENABLE=y): self-check 60-120 s (default 90 s: WiFi GOT_IP + MQTT + CSI estables) a mark_app_valid o reboot y revierte al slot bueno. Eventos OTA_CHECK/OTA_INICIO/OTA_OK/OTA_ERROR y FIRMWARE; estado retain ota. Agujero de telemetria durante la descarga es ESPERADO (captura+telemetria en pausa)." "C esp_ota_ops"
          csi -> mlp "muestras CSI ~100/s"
          mlp -> camara "onset dispara captura (cooldown 15 s, gate heap interno sin latch)"
          mlp -> tx "evaluacion motion ~4 msg/s (cada 250 ms)"
          camara -> tx "JPEG b64 hasta 16 KB (topic image)"
          tx -> ota "comando aulasense_ota QoS 1 por la MISMA conexion (transport.subscribe)"
          ota -> camara "pausa captura y telemetria durante la descarga (reanuda si aborta)"
        }
      }

      // ===== Zona: VPS (SOLO broker + tuneles) =====
      group "VPS 167.114.169.134 broker.gilman.pro - SOLO broker + tuneles (ningun servicio de datos)" {
        mosquitto = container "Mosquitto - broker.gilman.pro:14769" "MQTT sobre TCP plano 14769 (existen 14770 WS, 14771 TLS y 14772 mTLS pero la placa NO puede usarlos: sin CA privada ni WebSocket native). message_size_limit 0 / max_packet_size 2 MB: el cuello real es el outbox del nodo, no el broker. Broker compartido: topic_prefix aulasense/v1/devices aisla nuestros topics. retain=false en image (la foto NO persiste). El comando aulasense_ota (QoS 1) lo publica la ingesta WSL y el broker lo entrega por la conexion que la placa ya tiene abierta." "Eclipse Mosquitto"
        tuneles = container "Tuneles SSH wsl-port (reverse forward)" "SOLO reenvio de puertos: pub-aulasense-8100 lleva WSL 8100 a VPS 10062 (dashboard publico con login) y pub-aulasense-ota lleva WSL 8101 a VPS 10064 (bins + manifiesto). Ambos iniciados DESDE WSL (auto-start=true), verificados running en datos/tuneles_estado.json. Cifrado: el tramo distro-a-VPS va por SSH; el tramo VPS-al-cliente/placa es HTTP PLANO (decision D3 del dueno: SHA-256 + rollback mitigan; TLS nginx+LE post-entrega)." "OpenSSH via wsl-port"
      }

      // ===== Zona: servidor WSL (TODO) =====
      group "WSL debian-aulasense - el servidor que gestiona TODO (NAT, systemd, venv py3.13, sin Docker)" {
        ingesta = container "Ingesta (aulasense-ingesta.service)" "venv ~/venvs/aulasense (Python 3.13). Suscribe +/motion, +/health, +/device, +/sensing, +/wifi, +/fault, +/aulasense_temp, +/aulasense_config, +/image + cachea los retians nuevos ota/imagen_estado/heap_interno (sufijos dados de alta en esquema.py F26). Callback paho, cola, y WRITER UNICO a SQLite WAL. Da alta los eventos F26 OTA_CHECK/OTA_INICIO/OTA_OK/OTA_ERROR/FIRMWARE/IMAGEN_INIT/CAMARA_ERROR/AP_ERROR/SISTEMA_* en config_eventos y las columnas decision_*/internal_* (migracion idempotente). Helper publicar_comando_ota(device_id, action): publica el topic aulasense_ota (command_id auditable). Heartbeat estado_sistema cada 30 s con is_connected(). Receptor HTTP :8001 ALTERNATIVO LAN-only (opt-in; MQTT es primario). Usuario de suscripcion srv-electiva." "Python 3.13 paho-mqtt"
        vision = container "Worker vision IA (aulasense-vision.service)" "Modo B default. Describe imagenes con vision_estado pendiente: descripcion (1-2 frases en espanol) + etiquetas via qwen3.8-flash. RATE LIMITS obligatorios 120/dia + 20/h con contadores persistentes (tabla vision_uso), dedupe hash_sha256, backoff exponencial, interruptible (SIGTERM, --stop). UPDATE solo columnas vision_* con WAL busy_timeout 5000. Modo A daemon SOLO con key pay-as-you-go sk-*: el CLI rechaza sk-sp (ToS Token Plan). Key vacia es idle honesto." "Python 3.13 REST compatible-mode"
        clef = container "Worker Clef (decision de aula, F26)" "server/vision/clef.py + tools/clef_decide.py (PASADA SEPARADA post-descripcion: cola vision_estado hecho y decision_estado pendiente, claim atomico propio, UNA pasada a la vez). Cascada: (1) cloudflare/clef-flash con state = JSON de texto (descripcion+etiquetas almacenadas, NO re-envia la imagen); (2) SOLO si confianza menor que 0.6, cloudflare/clef con la imagen QVGA inline. Taxonomia de 6 valores (SIN_PERSONAS_VISIBLES, PERSONAS_SENTADAS, CIRCULACION, PRESENTACION_AL_FRENTE, AGLOMERACION_OTRA, IMAGEN_DEGRADADA) + noul_cubre/noul_calidad/score_actividad. IMAGEN_DEGRADADA se marca LOCAL sin llamada para JPEG no decodificables. Limites 120/dia + 20/h (tabla clef_uso), Paso 0 con usage medido ANTES del lote; refusal fail-closed a revision humana. decision_cubre menor que 0.7 muestra badge REVISION_HUMANA." "Python 3.13 OpenRouter Decisions"
        dashboard = container "Dashboard (aulasense-dashboard.service)" "FastAPI + uvicorn con bind 0.0.0.0 DENTRO de la VM en :8100 (NAT: no alcanzable desde LAN sin portproxy); la publicacion EXTERNA es el tunel pub-aulasense-8100 a VPS :10062 (HTTP con login). Login admin + galeria de evidencia + busqueda por texto + panel /salud F26. UI de una pagina: Chart.js + Tailwind CDN." "Python 3.13 FastAPI" {
          auth = component "Auth (F25-e, D-F25-8)" "Admin unico; password generada por codigo (secrets.token_hex(16)) guardada en el .env unico (gitignored, NUNCA impresa); hash Argon2id con fallback scrypt autodescriptivo. Cookie HttpOnly + SameSite=Lax (en BD solo SHA-256 del token); expiracion absoluta 12 h + idle 2 h. Lockout por IP+usuario: 5/10/20 fallos dan 60 s / 15 min / 1 h (429 + Retry-After). Middleware exige sesion en TODAS las rutas salvo /login, /logout, estaticos y /internal/* (Origin guard). WS /ws valida cookie en el handshake." "Argon2id scrypt"
          api = component "API REST" "Rutas: /api/dispositivos, /evaluaciones (limite 1-5000), /eventos, /resumen, /telemetria, /validacion (matriz de confusion), /fuentes_externas, /export CSV (real por default), /internal/evento (solo sintetico/test). SQL siempre parametrizado; XSS con esc()/textContent." "FastAPI"
          salud = component "Salud del sistema (F26, D-F26-8)" "GET /api/salud tras login + panel /salud con semaforos: ingesta activa (estado_sistema fresco hasta 90 s), MQTT conectado, frescura telemetria (hasta 5 min), heap INTERNO de la placa (hasta 120 KB warn), RSSI, colas vision+decision, consumo dia (vision_uso/clef_uso), disco (hasta 85 pct), tuneles (datos/tuneles_estado.json, SOLO state/remote) y OTA en curso (informativo: SUPRIME la alarma de frescura porque el agujero es esperado). Tarjeta camara/gate desde el retain imagen_estado + config_eventos (GATE_OFF/DESCARTE/INIT/CAMARA_ERROR). Cero secretos en las respuestas." "FastAPI"
          wsb = component "WS /ws push en vivo" "Polling de las filas escritas por la ingesta (proceso aparte) cada 400 ms y broadcast a clientes con sesion. Latencia E2E REAL medida en F3: broker a WS entre 14 ms (buen caso) y 361 ms (peor caso), dentro del criterio de menos de 2 s." "WebSocket"
          galeria = component "Galeria de evidencia (F24, D-F24-3)" "GET /api/imagenes (metadata paginada, NUNCA la ruta absoluta) y /api/imagenes/{id}/foto (JPEG por id de BD, ruta validada bajo datos/imagenes/, nosniff + no-store, 404 si purgada). El dashboard NUNCA acepta uploads. Retencion 30 dias: la purga marca imagen_purgada y CONSERVA la fila (dataset intacto)." "FastAPI"
          busqueda = component "Busqueda por texto (F25-d3)" "GET /api/imagenes?q= con LIKE parametrizado sobre descripcion y etiquetas (escapa comodines), filtros por canal (mqtt/http) y vision_estado. FTS5 solo si mas de 5000 imagenes o mas de 100 ms (criterio documentado)." "SQLite LIKE"
        }
        otahttp = container "Estatico OTA :8101 (aulasense-ota.service)" "python3 -m http.server 8101 bind 0.0.0.0 (VM-local NAT; la superficie publica es SOLO el tunel a VPS 10064). Sirve DESDE /home/aulasense/aulasense-ota/ el manifest.json (version, url, sha256, size reales por codigo) y firmware-VER.bin (bins gitignored). Sin datos personales: SOLO binarios + manifiesto; NO es ruta del dashboard (guard de login + separacion de superficie)." "Python http.server"
        datos = container "SQLite WAL - aulasense.db" "Ruta canonica: ~/aulasense-datos/aulasense.db en ext4 (no /mnt/c). Tablas: evaluaciones, eventos, telemetria (F26: internal_free_kb + internal_largest_block_kb desde el retain heap_interno; free_memory_kb sigue siendo PSRAM-inclusive), imagenes (canal, vision_estado, decision_estado/etiqueta/confianza/modelo/tokens/cubre/calidad/actividad F26, hash_sha256 unico), dispositivos, sesiones, intentos_login, vision_uso, clef_uso (auditoria por llamada F26), estado_sistema (heartbeat F26, fila unica), config_eventos (viven OTA_*/FIRMWARE/IMAGEN_*/AP_ERROR/SISTEMA_*), validacion_sesiones, fuentes_externas. Writer unico = ingesta; vision/clef solo actualizan sus columnas. datos/imagenes/ gitignored." "SQLite 3 WAL"
        meteo = container "Fetcher Open-Meteo" "Mismo venv; consulta horaria sin clave e INSERT a tabla fuentes_externas (cruce con die temp y carga en el EDA)." "Python 3.13"
      }

      // ===== Zona: host Windows =====
      group "Windows 11 (host) - fallback + CLI wsl-port + tools" {
        navegador = container "Navegador (profesor)" "Abre la URL PUBLICA http://167.114.169.134:10062 (tunel SSH al dashboard WSL :8100, todo detras del login). Fallback LAN: portproxy 127.0.0.1:8000 o http://WSL_IP:8100 con AULASENSE_ORIGINS_EXTRA exacto." "Chrome o Edge"
        tools = container "Tools config/operacion" "gen_firmware_config.py (WiFi + pass AP + OTA_MANIFEST_URL al sdkconfig.defaults local gitignored, valores del .env unico; nunca imprime), config_mqtt.py (LAN-only), wsl_aulasense.ps1 (start/stop/status/salud: escribe datos/tuneles_estado.json con SOLO state/remote; jamas serializa el objeto completo), reset_admin.py, purga_imagenes.py, lee_serial.py (consola USB-Serial-JTAG COM6, solo lectura), ota_cmd.py (dispara check/apply por MQTT). Python 3.15 (C:/Python315)." "PowerShell Python 3.15"
        clivision = container "CLI vision+Clef (modo B agente-mediado)" "tools/vision_describe.py --once --limit N y tools/clef_decide.py --once (o --paso0 para medir usage real ANTES del lote), lanzados por un agente en sesion interactiva y ESPERADOS dentro del turno: unica forma legal de usar la key TP sk-sp. UNA pasada a la vez por BD (prohibido solapar). El orquestador agenda las pasadas." "Python"
      }

      // ---------- Flujos de datos etiquetados (etiquetas CORTAS: el detalle vive en las descripciones) ----------
      aula -> nodo "movimiento: perturbacion del canal CSI (no identifica personas)"

      // Pipeline de sensing: CSI ~100 pps, decision cada 250 ms, motion ~4 msg/s
      // (UNA sola flecha con etiqueta doble: dos flechas paralelas solapaban sus etiquetas;
      //  la direccion de cada flujo vive en las descripciones y en diagrama_flujo_f26.png)
      tx -> mosquitto "MQTT/TCP 14769 (todo lo inicia la placa): SALIENTES eventos+health+config+image b64+retians; ENTRANTE comando aulasense_ota por el MISMO socket"
      mosquitto -> ingesta "subscribe srv-electiva (outbound desde WSL)"
      ingesta -> mosquitto "publica comando OTA"

      // Flujo OTA (descarga por el tunel publico)
      nodo -> otahttp "OTA saliente: GET manifiesto+bin a http://167.114.169.134:10064 (SHA-256 y size verificados ANTES de set_boot)"
      tuneles -> otahttp "tunel pub-aulasense-ota: WSL 8101 a VPS 10064 (tramo distro a VPS cifrado SSH)"
      tuneles -> dashboard "tunel pub-aulasense-8100: WSL 8100 a VPS 10062 (tramo distro a VPS cifrado SSH)"
      navegador -> tuneles "URL publica http://167.114.169.134:10062 (login obligatorio)"

      // Canal alternativo HTTP (opt-in, LAN-only)
      nodo -> ingesta "ALTERNATIVO LAN-only: HTTP 8001 POST /internal/imagen (Bearer, hasta 64 KB)"

      // Servidor, datos y dashboard
      ingesta -> datos "writer unico WAL + heartbeat estado_sistema ~30 s"
      datos -> dashboard "lecturas SQL + polling 400 ms + datos del /salud"
      navegador -> dashboard "FALLBACK LAN: portproxy 127.0.0.1:8000 u 8100 (operacion local)"

      // Vision: CLI agente-mediado, worker, Qwen y BD
      clivision -> vision "lanza pasadas --once de vision y clef (agente mediado, UNA a la vez)"
      vision -> qwen "POST chat/completions qwen3.8-flash (rate 120/dia + 20/h)"
      vision -> datos "UPDATE descripcion/etiquetas/vision_estado"

      // Clef: pasada separada post-descripcion
      datos -> clef "cola decision_estado pendiente (claim atomico)"
      clef -> openrouter "POST api/alpha/decisions: clef-flash texto; clef con imagen si conf menor que 0.6"
      clef -> datos "UPDATE decision_* + auditoria clef_uso"

      // Clima
      meteo -> openmeteo "GET horario sin clave"
      meteo -> datos "INSERT fuentes_externas"

      // Operacion
      tools -> nodo "PROVISION SOLO IN SITU (LAN-only): portal http://192.168.4.1/cfg sin token + Direct HTTP 62587 + Improv BOOT 3 s"
      tools -> tuneles "wsl-port CLI; verbo salud escribe tuneles_estado.json (solo state/remote)"
      tools -> otahttp "publica bin + manifest.json generado POR CODIGO"
      profesor -> navegador "monitorizar en vivo (URL publica con login), validar sesiones (verdad terreno), galeria y busqueda, ver panel /salud"
      profesor -> tools "configurar, purgar, reset_admin (acceso fisico del dueno), disparar OTA check/apply"
      profesor -> clivision "ejecutar pasadas de descripcion IA y decision Clef (modo B, dentro del turno)"
    }
  }

  views {
    systemContext aulasense "Contexto" {
      include *
      autoLayout
    }

    container aulasense "Contenedores" "Topologia y flujos de datos REAL (SPEC 16.10 y 16.9). Puertos verificados en terreno: MQTT/TCP 14769; dashboard WSL 8100 a VPS 10062 (tunel running); OTA estatico WSL 8101 a VPS 10064 (tunel running, puerto final validado en F26-b); receptor 8001 y Direct HTTP 62587 LAN-only alternativos; portal AP http://192.168.4.1/cfg solo in situ. E2E medido F3 broker a WS 14-361 ms; vision y Clef 120/dia + 20/h cada una; credenciales SOLO en el .env unico (jamas dibujadas)." {
      include *
      autoLayout tb
    }

    component nodo "Componentes-Nodo" "Firmware ESP32-S3 F26: captura CSI, detector MLP en-chip, evidencia con gate de heap INTERNO sin latch, transporte MQTT (retians imagen_estado/heap_interno/ota), OTA por comando con SHA-256 + rollback, provisioning FIJO sin token (solo in situ)." {
      include *
      autoLayout lr
    }

    component dashboard "Componentes-Dashboard" "Dashboard FastAPI :8100 (publicado a VPS 10062): auth F25-e, API REST, salud F26 D-F26-8, WS push, galeria F24, busqueda F25 - login obligatorio." {
      include *
      autoLayout
    }

    styles {
      element "Person" {
        shape Person
        color #ffffff
        background #083f81
      }
      element "name:SQLite WAL - aulasense.db" {
        shape Cylinder
        background #4f46e5
        color #ffffff
      }
      element "name:Qwen Cloud (Token Plan)" {
        background #6b7280
        color #ffffff
      }
      element "name:OpenRouter Decisions API (Clef)" {
        background #6b7280
        color #ffffff
      }
      element "name:Open-Meteo API" {
        background #6b7280
        color #ffffff
      }
      element "name:Mosquitto - broker.gilman.pro:14769" {
        background #b45309
        color #ffffff
      }
      element "name:Tuneles SSH wsl-port (reverse forward)" {
        background #0e7490
        color #ffffff
      }
      element "name:Estatico OTA :8101 (aulasense-ota.service)" {
        background #7c3aed
        color #ffffff
      }
      element "name:Worker Clef (decision de aula, F26)" {
        background #0e7490
        color #ffffff
      }
      element "name:OTA (app_ota, modulo NUEVO F26)" {
        background #7c3aed
        color #ffffff
      }
      element "name:Salud del sistema (F26, D-F26-8)" {
        background #16a34a
        color #ffffff
      }
      relationship "ALTERNATIVO LAN-only: HTTP 8001 POST /internal/imagen (Bearer, hasta 64 KB)" {
        dashed true
        color #b45309
      }
      relationship "PROVISION SOLO IN SITU (LAN-only): portal http://192.168.4.1/cfg sin token + Direct HTTP 62587 + Improv BOOT 3 s" {
        dashed true
        color #b45309
      }
      relationship "FALLBACK LAN: portproxy 127.0.0.1:8000 u 8100 (operacion local)" {
        dashed true
        color #6b7280
      }
    }
  }
}
