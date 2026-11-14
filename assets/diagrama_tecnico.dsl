// AulaSense - diagrama tecnico (Structurizr C4) - F25-f1 (D-F25-10)
// Fuente de verdad topologica: SPEC.md seccion 15 (Despliegue WSL), 6.2 (topic image),
// 7 (ingesta/decoder/worker vision), 9 (dashboard: auth, galeria, busqueda),
// y latencias E2E medidas en F3 (.tmp/f3_e2e.md: broker a WS 14-361 ms).
// Cadena ASCII deliberada: el exportador DOT escribe con el charset de la JVM y los
// caracteres no-ASCII (<, >, secciones, dashes largos) rompen las etiquetas HTML-like.
// Regenerar: ver .tmp/f25f1_diagrama.md (structurizr-cli validate + export -format dot
// + graphviz dot -Tsvg/-Tpng).

workspace "AulaSense" "Arquitectura tecnica - deteccion de movimiento por Wi-Fi CSI edge-first (ESP32-S3) + broker Mosquitto + servidor en WSL2 Debian 13 + dashboard FastAPI loopback+login. Coherente con SPEC v25 (secciones 15, 6.2, 7, 9)." {

  model {

    // ---------- Actores (C4-1) ----------
    aula = person "Aula (personas)" "Su movimiento perturba el canal Wi-Fi y se captura como CSI. Sin microfono, sin caras-ML: privacidad acotada local-only. Ojo honesto: idle no significa sala vacia."
    profesor = person "Profesor / Operador" "Monitoriza en vivo, marca verdad terreno, gestiona galeria y retencion (login admin unico), ejecuta pasadas de vision y provisiona el nodo."

    // ---------- Sistemas externos ----------
    qwen = softwareSystem "Qwen Cloud (Token Plan)" "API de vision qwen3.8-flash (allowlist TP exact-string). La key sk-sp- es SOLO para modo B agente-mediado (ToS: prohibido daemon autonomo)."
    openmeteo = softwareSystem "Open-Meteo API" "Meteorologia horaria gratuita, sin clave."

    // ---------- Sistema: toda la solucion desplegada ----------
    aulasense = softwareSystem "AulaSense" "Detector neuronal ESPectre (MLP 529 parametros) 100 por ciento en el ESP32-S3; eventos JSON por MQTT; servidor en WSL (systemd, sin Docker, datos en ext4); dashboard+galeria en loopback con login; credenciales en .env y claves/.env (gitignored, generadas solo por codigo)." {

      // ===== Zona: aula =====
      group "Aula - red Wi-Fi 2.4 GHz" {
        nodo = container "Nodo ESP32-S3 (GOOUUU v1.5)" "device_id real 2e6f0c909e30673e - firmware derivado: ESPectre 3.2.0 (GPLv3) + parche_aulasense, ESP-IDF v5.5.6. CSI crudo NUNCA sale del chip. Feedback local: buzzer GPIO14 (beep 880 Hz al onset) + WS2812 GPIO48 por estado." "C/C++ ESP-IDF 5.5.6" {
          csi = component "Captura CSI" "Radio HT20 2.4 GHz, ~100 muestras/s con trafico ping/DNS al gateway (modos conmutables si el aula bloquea ICMP)." "ESPectre"
          mlp = component "Detector MLP (IA en el borde)" "Perfil High Accuracy 8-24-12-1, 529 parametros, sin calibracion. Decide idle/motion cada 250 ms. Metricas edge via read_diagnostics (poll 30 s): detection_avg_us, pps, heap, RSSI." "ESPectre"
          camara = component "Camara OV3660 - evidencia de evento" "1 JPEG QVGA 320x240 calidad 12 SOLO al onset idle a motion, cooldown 15 s, tarea dedicada de baja prioridad (nunca el loop del detector). Gate de memoria: heap libre por debajo de 120 KB o bloque de 64 KB y la captura se auto-deshabilita; el CSI sigue." "ESP32 CAM JPEG"
          tx = component "Transporte MQTT (outbox)" "Publica eventos+health JSON y el topic image (b64 hasta 16 KB, QoS 0, retain=false). Parche documentado del outbox vendor de 8192 a 32768 bytes (F25-c). Autentica como usuario electiva." "mqtt_transport_esp_idf"
          prov = component "Provisioning + HTTP local" "AP fallback AULASENSE-CFG-xxxx con portal solo en ruta privada /cfg con token (404 sin pista), Improv Serial (BOOT 3 s) y HTTP :62587 para configurar WiFi/MQTT desde el PC." "C Improv"
          csi -> mlp "muestras CSI ~100/s"
          mlp -> camara "onset dispara captura (cooldown 15 s)"
          mlp -> tx "evaluacion motion ~4 msg/s (cada 250 ms)"
          camara -> tx "JPEG b64 hasta 16 KB (topic image)"
        }
      }

      // ===== Zona: broker =====
      group "VPS gilman.pro - broker compartido (infraestructura propia)" {
        mosquitto = container "Mosquitto - broker.gilman.pro:14769" "MQTT sobre TCP plano (mqtts TLS con CA publica opcional; NO WebSocket). message_size_limit 0 / max_packet_size 2 MB: el cuello real es el outbox del nodo, no el broker. Broker compartido: topic_prefix aulasense/v1/devices aisla nuestros topics. retain=false en image (la foto no persiste en el broker)." "Eclipse Mosquitto"
      }

      // ===== Zona: servidor =====
      group "WSL2 Debian 13 (trixie) - VM NAT, systemd ON, sin Docker" {
        ingesta = container "Ingesta (aulasense-ingesta.service)" "venv ~/venvs/aulasense (Python 3.13). Suscribe +/motion, +/health, +/device, +/sensing, +/wifi, +/fault, +/aulasense_temp, +/aulasense_config y +/image (F25). Callback paho, cola, y WRITER UNICO a SQLite WAL. Decoder image (SPEC 7.2): envelope v1, magic JPEG FFD8FF, sha256 dedupe, FK al MOVIMIENTO_INICIO en ventana de -15 s a +5 s, rate 1 imagen/10 s por device; fila con canal=mqtt. Receptor HTTP :8001 ALTERNATIVO opt-in (POST /internal/imagen, Bearer token, hasta 64 KB; bind vacio es desactivado). Poller read_diagnostics cada 30 s. Usuario de suscripcion srv-electiva." "Python 3.13 paho-mqtt"
        vision = container "Worker vision IA (aulasense-vision.service)" "Modo B default. Describe imagenes con vision_estado pendiente: descripcion (1-2 frases en espanol) + etiquetas via qwen3.8-flash. RATE LIMITS obligatorios 120/dia + 20/h con contadores persistentes (tabla vision_uso), dedupe hash_sha256, backoff exponencial max 2, interruptible (SIGTERM, --stop). UPDATE solo columnas vision_* con WAL busy_timeout 5000. Modo A daemon SOLO con key pay-as-you-go sk-*: el CLI rechaza sk-sp- (ToS Token Plan). Key vacia es idle honesto." "Python 3.13 REST compatible-mode"
        dashboard = container "Dashboard (aulasense-dashboard.service)" "FastAPI + uvicorn con bind 0.0.0.0 DENTRO de la VM (NAT: no alcanzable desde LAN sin portproxy); la exposicion en Windows es SOLO loopback via portproxy 127.0.0.1:8000. Login admin + galeria de evidencia + busqueda por texto. UI de una pagina: Chart.js + Tailwind CDN." "Python 3.13 FastAPI" {
          auth = component "Auth (F25-e, D-F25-8)" "Admin unico; password generada por codigo (secrets.token_hex(16)) guardada en claves/.env (gitignored, NUNCA impresa); hash Argon2id con fallback scrypt autodescriptivo. Cookie HttpOnly + SameSite=Lax (en BD solo SHA-256 del token); expiracion absoluta 12 h + idle 2 h. Lockout por IP+usuario: 5/10/20 fallos dan 60 s / 15 min / 1 h (429 + Retry-After). Middleware exige sesion en TODAS las rutas salvo /login, /logout, estaticos y /internal/* (Origin guard). WS /ws valida cookie en el handshake." "Argon2id scrypt"
          api = component "API REST" "Rutas: /api/dispositivos, /evaluaciones (limite 1-5000), /eventos, /resumen, /telemetria, /validacion (matriz de confusion), /fuentes_externas, /export CSV (real por default), /internal/evento (solo sintetico/test). SQL siempre parametrizado; XSS con esc()/textContent." "FastAPI"
          wsb = component "WS /ws push en vivo" "Polling de las filas escritas por la ingesta (proceso aparte) cada 400 ms y broadcast a clientes con sesion. Latencia E2E REAL medida en F3: broker a WS entre 14 ms (buen caso) y 361 ms (peor caso), dentro del criterio de menos de 2 s." "WebSocket"
          galeria = component "Galeria de evidencia (F24, D-F24-3)" "GET /api/imagenes (metadata paginada, NUNCA la ruta absoluta) y /api/imagenes/{id}/foto (JPEG por id de BD, ruta validada bajo datos/imagenes/, nosniff + no-store, 404 si purgada). El dashboard NUNCA acepta uploads. Retencion 30 dias: la purga marca imagen_purgada y CONSERVA la fila (dataset intacto)." "FastAPI"
          busqueda = component "Busqueda por texto (F25-d3)" "GET /api/imagenes?q= con LIKE parametrizado sobre descripcion y etiquetas (escapa comodines), filtros por canal (mqtt/http) y vision_estado. FTS5 solo si mas de 5000 imagenes o mas de 100 ms (criterio documentado)." "SQLite LIKE"
        }
        datos = container "SQLite WAL - aulasense.db" "Ruta canonica tras el cutover F25-b2: ~/aulasense-datos/aulasense.db en ext4 (no /mnt/c). Tablas: evaluaciones, eventos, telemetria, imagenes (canal http o mqtt, vision_estado, hash_sha256 unico), dispositivos, sesiones, intentos_login, vision_uso, validacion_sesiones, fuentes_externas. Writer unico = ingesta; el worker vision solo actualiza columnas vision_*. datos/imagenes/ gitignored." "SQLite 3 WAL"
        meteo = container "Fetcher Open-Meteo" "Mismo venv; consulta horaria sin clave e INSERT a tabla fuentes_externas (cruce con die temp y carga en el EDA)." "Python 3.13"
      }

      // ===== Zona: host Windows =====
      group "Windows 11 (host) - repo montado en /mnt/c; fallback de emergencia" {
        navegador = container "Navegador (profesor)" "Abre http://127.0.0.1:8000 - netsh portproxy (listenaddress 127.0.0.1:8000 a WSL_IP:8000, refrescado por tools/wsl_aulasense.ps1 refresh-portproxy). Fallback sin admin: http://WSL_IP:8000 con AULASENSE_ORIGINS_EXTRA exacto." "Chrome o Edge"
        tools = container "Tools config/provision" "gen_firmware_config.py (WiFi a sdkconfig.defaults.local + AULASENSE_UPLOAD_TOKEN), config_mqtt.py (PATCH /mqtt al nodo; nunca imprime claves), wsl_aulasense.ps1 (start/stop/status/refresh-portproxy), reset_admin.py, purga_imagenes.py, session_logger.py. Python 3.15 (C:/Python315) con tests verdes." "PowerShell Python 3.15"
        clivision = container "CLI vision (modo B agente-mediado)" "tools/vision_describe.py --once --limit N, lanzado por un agente en sesion interactiva: unica forma legal de usar la key TP sk-sp-. El orquestador agenda pasadas tras F4 y antes de F6a." "Python"
      }

      // ---------- Flujos de datos etiquetados ----------
      aula -> nodo "movimiento: perturbacion del canal CSI (no identifica personas)"

      // Pipeline de sensing: CSI ~100 pps, decision cada 250 ms, motion ~4 msg/s
      nodo -> mosquitto "eventos+health+config JSON: topic aulasense/v1/devices/2e6f0c909e30673e/motion|health|device|sensing|wifi|fault|aulasense_temp|aulasense_config"
      tx -> mosquitto "topic .../image: JSON+b64 hasta 16 KB, QoS 0, retain=false (canal primario, D-F25-1)"
      mosquitto -> ingesta "subscribe como srv-electiva (conexion outbound NAT desde WSL; credenciales en .env)"

      // Canal alternativo HTTP (opt-in) - flecha discontinua via relationship style
      nodo -> ingesta "ALTERNATIVO (opt-in): HTTP LAN :8001 POST /internal/imagen, Bearer token, hasta 64 KB. Con MQTT primario no hace falta (portproxy IP_LAN + regla firewall)"

      // Servidor, datos y dashboard
      ingesta -> datos "callback, cola y writer unico (WAL): evaluaciones, eventos, telemetria, imagenes (canal=mqtt, vision_estado=pendiente)"
      datos -> dashboard "lecturas SQL parametrizadas + polling de filas nuevas cada 400 ms"
      navegador -> dashboard "HTTP/WS a 127.0.0.1:8000 via portproxy loopback - todo detras del login (defensa en profundidad)"

      // Vision: CLI agente-mediado, worker, Qwen y BD
      clivision -> vision "lanza pasadas --once (agente mediado, ToS Token Plan)"
      vision -> qwen "POST chat/completions con image_url data-URI: qwen3.8-flash, rate limit 120/dia + 20/h, ~1k tokens/imagen (Paso 0 midio usage real)"
      vision -> datos "UPDATE descripcion/etiquetas/vision_estado (busy_timeout 5000, dedupe sha256)"

      // Clima
      meteo -> openmeteo "GET horario sin clave"
      meteo -> datos "INSERT fuentes_externas"

      // Operacion
      tools -> nodo "provision WiFi/MQTT: Improv Serial + ESPectre CLI/HTTP :62587 (Origin test.espectre.dev)"
      profesor -> navegador "monitorizar en vivo, validar sesiones (verdad terreno), galeria y busqueda"
      profesor -> tools "configurar, purgar, reset_admin (acceso fisico del dueno)"
      profesor -> clivision "ejecutar pasadas de descripcion IA (F25-d2, modo B)"
    }
  }

  views {
    systemContext aulasense "Contexto" {
      include *
      autoLayout
    }

    container aulasense "Contenedores" "Topologia y flujos de datos (SPEC seccion 15). Cifras verificadas: CSI ~100 muestras/s y motion cada 250 ms; imagen b64 hasta 16 KB por MQTT (HTTP :8001 alternativo); E2E medido F3 broker a WS 14-361 ms (criterio por debajo de 2 s); vision 120/dia + 20/h; credenciales en .env y claves/.env (gitignored)." {
      include *
      autoLayout tb
    }

    component nodo "Componentes-Nodo" "Firmware ESP32-S3: captura CSI, detector MLP en-chip, eventos y evidencia de imagen (edge-first)." {
      include *
      autoLayout lr
    }

    component dashboard "Componentes-Dashboard" "Dashboard FastAPI: auth F25-e, API REST, WS push, galeria F24 y busqueda F25 - loopback + login." {
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
      element "name:Open-Meteo API" {
        background #6b7280
        color #ffffff
      }
      element "name:Mosquitto - broker.gilman.pro:14769" {
        background #b45309
        color #ffffff
      }
      relationship "ALTERNATIVO (opt-in): HTTP LAN :8001 POST /internal/imagen, Bearer token, hasta 64 KB. Con MQTT primario no hace falta (portproxy IP_LAN + regla firewall)" {
        dashed true
        color #b45309
      }
    }
  }
}
