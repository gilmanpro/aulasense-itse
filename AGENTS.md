# AGENTS.md — Convenciones del proyecto AulaSense (Wi-Fi CSI edge sensing)

Este archivo lo lee todo agente que trabaje en el repo. Respétalo al pie de la letra.
Hereda las reglas duras del proyecto anterior (DriveGuard, `../archive/dashcam-ia/AGENTS.md` tras el
archivado) **con las correcciones del dominio CSI**. Plan maestro: `PLAN.md`. Especificación: `SPEC.md`.
Checklist vivo: `../.tmp/checklist.md` (en `iot2\.tmp\checklist.md`, **v26**; v25 archivada en `checklist_pre_v26.md`).

## Qué es esto
Sensor de movimiento/ocupación dinámica con IA **en el borde** (ESP32-S3 GOOUUU v1.5): detector
neuronal ESPectre sobre Wi-Fi CSI 100 % en el chip, eventos JSON por MQTT a un dashboard en vivo
(FastAPI+Chart.js), dataset propio en SQLite, análisis pandas con modelo predictivo de actividad por
hora, alerta audible (buzzer GPIO14) + visual (WS2812 GPIO48). **Sin micrófono, sin radar.**
**F24 (orden del dueño, 2026-11-12):** la cámara OV3660 SÍ se usa como **evidencia de evento** (1 JPEG
QVGA al onset de motion, cooldown 15 s, subida HTTP al servidor local, galería loopback). La IA principal
sigue siendo el detector CSI en-chip; una capa de visión persona (esp-dl) es OPCIONAL default n,
spike-gated (si no cabe junto al CSI, se abandona documentado). **Privacidad acotada local-only** (regla 2).
**F25 (orden del dueño, 2026-11-14):** las imágenes viajan por **MQTT** (topic `image`, b64, QoS 0,
retain=false) al broker autenticado — la regla "nunca por MQTT" de F24 queda REVOCADA (D-F25-1);
HTTP :8001 queda alternativo. El servidor (ingesta+dashboard+worker visión) corre en **WSL Debian 13**
(systemd, venv linux, sin Docker; datos en ext4; Windows fallback). El dashboard gana **login seguro**
(admin único, Argon2id/scrypt, cookie HttpOnly+SameSite, lockout progresivo) y **búsqueda por texto**
en la galería (descripciones IA de Qwen vision con rate limits obligatorios y cumplimiento ToS del
Token Plan). Credenciales de auth/IA en **`.env` ÚNICO de la raíz** (F26 D-F26-6; gitignored,
generadas SOLO por código; el `claves/.env` de F25 quedó archivado como referencia histórica).

## Reglas duras
1. **Edge-first:** el detector ML corre EN el ESP32-S3. Ningún procesamiento de sensing/IA se mueve al
   PC. El PC solo ingesta, almacena, visualiza y analiza eventos (el modelo sklearn de análisis es la
   excepción docente prevista por el curso, NO el detector).
2. **Privacidad ACOTADA (local-only) — RE-ENMARQUE F24 por orden explícita del dueño (2026-11-12).**
   La regla v23 "privacidad por construcción / cero imágenes / la OV3660 NUNCA se inicializa" queda
   **REVOCADA**. Nueva política:
   - **Imágenes SOLO al servidor local del proyecto como destino final** — **F25 (D-F25-1, orden
     del dueño 2026-11-14): el transporte PRIMARIO es MQTT** (topic
     `aulasense/v1/devices/<id>/image`, JSON+b64, QoS 0, **retain=false** → la foto NO persiste en
     el broker; auth electiva/srv-electiva, prefijo aislado). HTTP :8001 con Bearer token queda
     como canal ALTERNATIVO (`canal` en BD). Galería servida SOLO en loopback **con login**
     (127.0.0.1:8000, SPEC §9.2). **NUNCA a la nube.** La regla F24 "las imágenes NO viajan por
     MQTT" queda **REVOCADA** por orden del dueño (viabilidad: Mosquitto message_size_limit
     default 0 / max_packet_size 2 MB, man oficial 2026-11-14 + probe F25-a). El resultado
     opcional `ia_imagen` persona/score sigue como estaba.
   - **Captura = evidencia de evento, no streaming:** 1 JPEG QVGA al ONSET de motion, cooldown 15 s,
     tarea dedicada de baja prioridad (NUNCA desde el loop del detector CSI), cola corta en PSRAM
     (si se llena: se DESCARTA la imagen y el EVENTO SIGUE — el evento es la fuente de verdad).
   - **Gate de memoria:** si heap interno libre < 120 KB o largest block < 64 KB → captura
     auto-deshabilitada con log honesto; el CSI sigue detectando. **F26 (SPEC §16.3): umbrales
     VIVOS 95/24 KB y re-evaluación en CADA onset (sin latch permanente; cola de onsets
     drop-oldest); el gate OTA es 96/48 post-quiesce (§16.1 enmendado). Métrica útil:
     `internal_free_kb`/`internal_largest_block_kb` (heap INTERNO), NUNCA `free_memory_kb`
     (≈8,2 MB = PSRAM).**
   - **Consentimiento de aula documentado** (obligatorio: las imágenes pueden capturar personas = datos
     personales). **Retención y borrado configurables** (default 30 días; `tools/purga_imagenes.py`
     marca `imagen_purgada=1` y CONSERVA la fila+ia → el dataset NO se rompe). Imágenes **gitignored**.
   - **SIN caras-ML:** no reintroducir detección facial (E1). La visión opcional es detección de
     PERSONA (esp-dl espdet_pico/ESP-WHO pedestrian), NO caras, NO COCO/YOLO (inviable en S3, F23).
   - El **CSI crudo NUNCA sale del chip** en la ruta crítica (el modo `GET /csi` collection es opcional,
     solo investigación, y NO se usa). Solo eventos de movimiento + métricas + la imagen de evidencia
     acotada. **C9 = cumple-parcial (local-only), no "privacidad por construcción".**
3. **Python del PC:** el `python` del PATH está ROTO (3.13). Usa SIEMPRE `C:\Python315\python.exe`
   (3.15.2) para firmware/tools y el **fallback Windows** del server. El venv `server\.venv` se crea
   con ese intérprete; requirements con wheel cp315 o build local (patrón heredado: bleak, pandas,
   scikit-learn, paho-mqtt, fastapi, uvicorn, matplotlib, requests, python-docx, python-pptx, pytest).
   Sin fallback a otro python. **F25: el runtime PRIMARIO del server es el venv linux de WSL Debian**
   (`~/venvs/aulasense`, python3.13 de apt, `server/requirements-linux.txt` — argon2-cffi incluido,
   bleak fuera). Ambos runtimes deben pasar pytest; NUNCA dos stacks vivos a la vez (split-brain).
   **F25f (2026-11-15): la distro canónica de datos/análisis es `debian-aulasense`** (dashboard
   interno :8100, publicación VPS :10062; BD `/home/aulasense/aulasense-datos/aulasense.db`).
   **NO existe el CLI `sqlite3` en las distros**: toda consulta a la BD WSL va por SOLO-LECTURA con
   el `python3` del venv y URI `file:...?mode=ro` (patrón `analysis/analizar_dataset.py` y los
   forenses `.tmp/`); prohibido invocar `sqlite3` directo y prohibido ESCRIBIR por esa vía
   (invariante writer único). PowerShell rompe heredocs/quotes largos: escribir el script a fichero
   y ejecutarlo vía stdin/ruta bajo `/mnt/c`.
4. **Sin Docker.** El server corre nativo: **F25 = WSL Debian 13 (nativo linux, systemd, NO Docker)**
   como runtime primario; Windows nativo como fallback. (El CLI de ESPectre ofrece Docker como
   fallback de build; **NO se usa** — ruta primaria `eim run idf.py`.) La VM WSL es compartida con
   otras distros del usuario: **NO tocar `.wslconfig` global ni otras distros** (D-F25-3).
5. **ESP-IDF v5.5.6** (NO v5.4): el SDK ESPectre requiere ≥5.5.3 y docs/CLI.md recomienda 5.5.6.
   Desviación documentada de la regla E5 original (v5.4 queda instalado para el archive). Comando
   canónico: `eim run "idf.py …" v5.5.6`. **NO usar** `. $env:IDF_PATH\export.ps1` (exige python sano en
   PATH, que está roto). **F26 (estado 2026-11-17):** la BUILD ÚNICA de referencia es la familia F26
   (placa viva f26.6, espejo 10/10); ningún cambio de firmware se flashea por UART — viaja por OTA
   (SPEC §16.1). La ÚNICA reflasheada UART autorizada fue la "destraba" f26.3 (el gate contractual
   120/64 era inalcanzable y hacía la primera OTA imposible por OTA — evidencia `f26_g1_ota.md` §0a;
   umbrales enmendados a 96/48). Builds de chequeo: `idf.py build` simple, NO `set-target` a posteriori
   (lección c2).
6. **PROHIBIDO USB device stack** en el firmware (CONFIG_USB_* / tinyusb / UAC): re-muxea el PHY 19/20
   y apaga el JTAG por el que se reflashea = riesgo de brick (lección F18-A de DriveGuard). La
   **consola** de este build S3 es **USB-Serial-JTAG, NO UART** (`CONFIG_ESP_CONSOLE_UART_NUM=-1`;
   evidencia `sdkconfig:1460-1465` e `inv_ota_provision.md` §2.5 — corrige la afirmación vieja "en S3
   la consola de ESPectre es UART"): lo PROHIBIDO sigue siendo el **USB device stack** (TinyUSB CDC
   solo aplica a S2), no el USB-Serial-JTAG nativo por el que sale la consola y por el que se
   reflashea. El monitor de la fase F26 es `tools/lee_serial.py` por JTAG (solo lectura, sin reset;
   evitar `idf.py monitor` para sondeo). No activar ninguna opción USB-device. Verificar sdkconfig
   ANTES de cada flash.
7. **Secretos:** jamás escribas contraseñas reales en el repo. **F26 (D-F26-6, SPEC §16.8): el
   archivo canónico es el `.env` ÚNICO en la raíz** (gitignored, secciones ordenadas WiFi/MQTT/
   PROVISION_AP/UPLOAD/ADMIN/IA/VISION/CLEF/MONITOREO/WSL/REMOTO); `claves/` quedó archivado
   (`*.bak-f26`) y los loaders ya no le dan prioridad. Firmware WiFi/MQTT va además en
   `sdkconfig.defaults.local` (gitignored, generado por `tools/gen_firmware_config.py`) o en la NVS
   de la placa (Improv Serial / portal AP fijo). Única excepción publicada por **orden explícita del
   dueño**: la pass canónica del AP de provisioning (`itse-aulasense`) — solo en `.env` +
   `docs/NOTES_operacion.md` (capacidad local, no credencial de red). La clave WiFi del CLI de
   ESPectre va por `ESPECTRE_WIFI_PASSWORD` (env var), **NUNCA por argv ni impresa**. Si un componente
   necesita una clave nueva, genérala con `secrets.token_hex(16)` en código. `tools/config_mqtt.py` lee
   `.env` y nunca imprime la clave. La pass SSH del VPS NO entra (la gestiona wsl-port; rotación
   pendiente — NUNCA imprimirla ni serializar el objeto túnel completo, solo `state`/`remote`).
8. **Temporales:** siempre en `.tmp/` de este proyecto o en `iot2\.tmp\`. Nunca en %TEMP%/AppData.
9. **Idioma:** documentación, comentarios de código y mensajes de commit en español; identificadores,
   variables y comandos en inglés.
10. **Datos honestos:** lo sintético se etiqueta `origen=sintetico` / `SIMULADO` en docs y dashboard.
    Nunca se mezcla con captura real ni se presenta como real. El export `analysis/dataset/consolidado.csv`
    es SOLO `origen=real`. Las métricas vendor de ESPectre (F1 98,1–99,3 %) son de SU dataset: las
    propias se miden en F1c/F4 y se reportan con tamaño de muestra. **No prometer accuracy que dependa
    de un dataset propio largo** (lección E10 de DriveGuard).
11. **Framing honesto del sensing:** ESPectre detecta **MOVIMIENTO**, no presencia estacionaria, no
    prueba sala vacía, no identifica ni cuenta personas. `idle` ≠ sala vacía. Docs/dashboard usan
    "movimiento/ocupación dinámica", nunca "presencia" a secas. La die temp es proxy de carga de
    cómputo, NO temperatura ambiente.
12. **GPLv3:** el vendor ESPectre es GPLv3. Preservar su LICENSE. El derivado
    (`firmware/parche_aulasense/`) se trackea y se publica (fuente disponible al profesor). El clon
    vendor (`firmware/espectre/`) va gitignored (reproducible por tag `3.2.0`, SIN prefijo v — `v3.2.0`
    da 404, verificado F1a). No relicenciar el vendor.
13. **Qwen API (opcional):** solo capa opcional del dashboard/análisis. El sistema completo debe
    funcionar con `DASHSCOPE_API_KEY` vacío. **F25: el worker de visión (§7.3 SPEC) usa la allowlist
    TP exact-string (default `qwen3.8-flash`; qwen*-vl NO está en TP) y respeta los ToS del Token
    Plan: la key `sk-sp-` es SOLO para uso interactivo en agentes — PROHIBIDO daemon autónomo con
    ella (modo B agente-mediado; modo A daemon solo con key pay-as-you-go `sk-*`). Rate limits
    obligatorios (regla 16).**
14. **Imágenes locales = evidencia de evento (F24, complementa regla 2):** la OV3660 se inicializa
    SOLO bajo `AULASENSE_IMAGEN=y` (default y), captura QVGA JPEG al onset con cooldown 15 s, sube por
    `POST /internal/imagen` al receptor de ingesta (LAN :8001, Bearer token `AULASENSE_UPLOAD_TOKEN`
    generado `secrets.token_hex(16)`, nunca impreso). **F25 (D-F25-1): el canal PRIMARIO es MQTT
    (topic `image`, b64, QoS 0, retain=false, parche outbox vendor 8192→32768 documentado); HTTP
    :8001 queda alternativo. NUNCA a la nube.** Normas del firmware (README del parche §12):
    **N1** = la imagen viaja por la MISMA conexión MQTT persistente del nodo — prohibido abrir otra
    conexión por imagen; **N2** = fallback HTTP :8001 (hoy re-habilitado en `debian-aulasense` con
    caveat de ruteo LAN, README_wsl). El
    dashboard sirve la galería tras **login** con bind local (loopback en Windows; VM-local en WSL
    :8100) y exposición pública SOLO por el túnel VPS `:10062` tras el guard de Origin
    (SPEC §15/§16.10). `datos/imagenes/` gitignored.
    Purga con `imagen_purgada` conserva la fila (dataset intacto). La IA de visión persona en-chip es
    OPCIONAL (`AULASENSE_IA_IMAGEN` default n), spike-gated F24-e0: si no coexiste con el CSI, se
    ABANDONA documentado (orden del usuario: "si no se puede no lo hagas") — **veredicto 2026-11-15:
    F24-e NO LANZADA, archivada documentada** (el spike se re-encuadró a backend con veredicto
    GO-COSTO; el módulo en-chip nunca se creó en disco — PLAN §12.8 y README del parche). **La IA
    principal YA ES el detector
    CSI neuronal en-chip** (edge-first C1); la visión es capa adicional, no la reemplaza.
    **F26 (estado 2026-11-17):** el gate de captura VIVO es 95/24 KB internos SIN latch (re-evalúa
    cada onset; SPEC §16.3) y la observabilidad es nativa: retain `imagen_estado` (camara_ok/
    gate_off/captura_habilitada/build), sufijo `heap_interno` (30 s) y columnas
    `telemetria.internal_free_kb`/`internal_largest_block_kb` — el estado de cámara se verifica REMOTO
    (BD ro/dashboard), sin serial salvo anomalía. 81 fotos reales en BD (60 con decisión Clef; NO
    "~130"). Calidad JPEG regulable post-entrega (`jpeg_quality` en `app_imagen.c:653`) via OTA.
15. **`.env` ÚNICO en la raíz (F26 D-F26-6, SPEC §16.8 — reemplaza el `claves/.env` de F25 D-F25-9):**
    todas las credenciales del proyecto (auth admin, IA `DASHSCOPE_API_KEY`/`OPENROUTER_API_KEY`,
    broker, upload, PROVISION_AP, monitoreo, remoto) viven en `aulasense/.env` — archivo NUEVO único
    editable por humanos, **gitignored**, generado/migrado por `tools/consolidar_env.py --unificar`
    (valores copiados byte-a-byte por código; el modelo nunca los ve ni imprime). **Generación SOLO
    por código** (`secrets.token_hex(16)`; `tools/reset_admin.py` reescribe en el `.env` raíz).
    **Entrega al dueño por canal local** (el archivo en disco; la ruta, NUNCA el valor). Prohibido:
    imprimirla en logs/reportes/evidencia `.tmp/`, escribirla en docs o commitearla — con la única
    excepción de la pass canónica del AP, publicada por orden explícita del dueño en `.env`+NOTES.
    Las fuentes viejas (`claves/.env`, `claves/.env-wsl-nuevo`) quedaron archivadas como
    `claves/*.bak-f26` (gitignored); `claves/.env.example` es referencia histórica. rsync a WSL con
    `--exclude '.env'` + chmod 600. El modelo NUNCA asigna contraseñas.
16. **Presupuesto de Credits (F25, D-F25-6, OBLIGATORIO):** toda llamada a modelos de pago (visión,
    imagen, video, TTS) lleva **rate limit configurable** (visión: `AULASENSE_VISION_MAX_DIA=120` +
    `AULASENSE_VISION_MAX_HORA=20`, contadores persistentes), **dedupe por hash**, **backoff
    exponencial** (429 → Retry-After), **interruptible** (SIGTERM/`--stop`, estado en BD) y
    **Paso 0 de medición real** antes de lotes (1 llamada/clip de prueba → registrar `usage`/consumo
    en `.tmp/`). CERO promesas de coste sin medir (patrón E10). Video: ≤8 clips 480P 5 s, clip de
    prueba medido ANTES del lote; si el consumo es anómalo → parar y reportar. ToS del Token Plan
    respetados (regla 13): nada de backends autónomos con key `sk-sp-`. **Una pasada de visión a la
    vez (regla nacida de incidente, 2026-11-15):** el worker NO tiene lock entre instancias sobre la
    misma BD — solo puede haber UN `vision_describe.py` vivo por BD, y cada pasada se LANZA Y ESPERA
    dentro del turno del agente (prohibido dejarlas en background entre turnos); el incidente de dos
    procesos solapados costó 3 descripciones duplicadas y 2.972 tokens.
17. **Espejo `parche_aulasense` ↔ `main/` con DIFF CERO (obligatorio):** todo archivo del parche
    (`app_imagen.*`, `app_provision.*`, `app_feedback.*`, `app_ota.*` — F26), `Kconfig.projbuild`,
    `CMakeLists.txt`)
    debe quedar IDÉNTICO entre `firmware/parche_aulasense/` y el árbol vivo
    `firmware/espectre/src/cpp/frontend/native/app/main/` al cerrar cualquier tarea de placa
    (verificar con diff/hash; `f24d_reconciliacion.md` §3 es el patrón). Los ficheros sin espejo por
    convención (p. ej. `app_main.cpp`) llevan sus enganches DOCUMENTADOS en el README del parche.
    Dos sesiones editando el mismo árbol/ninja sin coordinar = razas de build y hashes de bin leídos
    mid-write (incidente documentado 2026-11-15): una sola sesión dueña de COM6 y del árbol a la vez.

## Seguridad de la demo (modelo de amenaza — no regresar estos controles, heredados E7)
- **Dashboard SOLO en `127.0.0.1` + LOGIN (F25: loopback+auth, defensa en profundidad).** Guard de
  Origin para `/ws` y `/internal/evento` (403 al origen cruzado). Ampliable SOLO por
  `AULASENSE_ORIGINS_EXTRA` explícita (host[:puerto] exactos, sin wildcards, opt-in); los defaults
  `127.0.0.1`/`localhost` no se tocan. **F25 (D-F25-8):** todas las rutas exigen sesión salvo
  /login, /logout, estáticos de login y /internal/* (Origin guard, máquina local); cookie
  HttpOnly+SameSite=Lax, sesión 12 h absoluta + 2 h idle, lockout progresivo por IP+usuario
  (5/10/20 fallos → 60 s/15 min/1 h, 429+Retry-After), logout, `tools/reset_admin.py` local.
  Password admin generada por código → `.env` raíz único (regla 15, SPEC §16.8), hash Argon2id/scrypt.
- **Exposición WSL (F25, D-F25-3, reinterpreta el bind):** el server primario corre en WSL Debian
  (NAT). Dentro de la VM el dashboard bind `0.0.0.0` es **VM-local** (NAT sin portproxy LAN =
  inalcanzable desde la LAN); la **superficie expuesta en Windows sigue siendo SOLO 127.0.0.1**
  (portproxy `listenaddress=127.0.0.1:8000` → WSL_IP; fallback sin admin: http://WSL_IP:8000 +
  ORIGINS_EXTRA exacto). **NO tocar `.wslconfig` global** (8 distros comparten la VM; mirrored
  per-distro imposible, WSL#13706). El receptor :8001 LAN→WSL es OPT-IN (portproxy+firewall); con
  MQTT primario no hace falta. Origin guard y login intactos en todos los casos.
- **Galería de imágenes tras login (F24, reemplaza "cero endpoints de imágenes" v23):** el dashboard
  NUNCA acepta uploads; sirve fotos de evidencia por id de BD en `GET /api/imagenes/{id}/foto` (bind
  127.0.0.1 en Windows / VM-local :8100 en WSL; exposición pública solo por el túnel :10062 con
  sesión+Origin guard; nosniff, ruta validada bajo datos/imagenes/, 404 si purgada). El **upload** va a un receptor
  HTTP SEPARADO dentro de la ingesta (`POST /internal/imagen`, LAN :8001, **Bearer token** obligatorio,
  JPEG magic, ≤64 KB, device allowlist, rate-limit 1/10 s → 401/413/422/429). Bind del receptor
  `AULASENSE_UPLOAD_HOST` (vacío=desactivado; NUNCA 0.0.0.0 default). **F25: las imágenes SÍ viajan
  por MQTT al broker autenticado (canal primario, retain=false, QoS 0, prefijo aislado); NUNCA a la
  nube.** `POST /internal/evento` sigue siendo solo JSON de evento validado.
- **Provisioning AP privado (F24):** AP fallback `AULASENSE-CFG-xxxx` abierto CON aviso (o WPA2 opcional);
  portal SOLO en ruta privada `http://192.168.4.1/cfg/<token>` (token NVS 8 hex, mostrado una vez en
  serial, 404 sin pista). POST con Origin/Referer guard (403), cooldown NVS 5 s (429), **NUNCA loguea
  SSID/pass** (solo longitudes), cabeceras seguridad (nosniff/DENY/no-referrer/CSP text-only). Patrón
  heredado endurecido DriveGuard `app_portal.c`. BOOT 3 s → Improv nativo PRESERVADO.
  ← **F26 (D-F26-4, SPEC §16.2): reemplazado por portal FIJO SIN token** — AP `AULASENSE-CFG` (SSID
  fijo, WPA2 con pass canónica `itse-aulasense`, publicada por orden explícita del dueño: capacidad
  local que exige asociación física al AP, no credencial de red) + rutas explícitas `GET /cfg` (form
  HTML), `GET /cfg/estado` (JSON), `POST /cfg/wifi`; token NVS eliminado del código; guardas HEREDADAS
  intactas (Origin/Referer 403, cooldown 429, body ≤255 B, nunca loguear pass, cabeceras duras);
  `AP_ERROR` publicado en TODAS las ramas de fallo del AP. **REALIDAD FLASHEA (c2→f26.6): la build
  viva ya usa el portal fijo sin token — NO queda pendiente de flasheo; la primera OTA E2E por aire
  y el rollback están demostrados (g1/g2). Pasos operativos: `docs/NOTES_operacion.md`.**
- **MQTT con auth** (usuario `electiva`, clave en `.env`): el broker es compartido → `topic_prefix=
  aulasense/v1/devices` aísla nuestros topics. La ingesta valida el esquema; los paquetes no llevan
  datos personales identificables.
- **Direct HTTP del nodo (:62587)** es para LAN confiable **y SOLO LAN (zero-asunción-LAN D-F26-10: el
  servidor JAMÁS alcanza a la placa en operación remota — toda gestión remota viaja por MQTT iniciado
  por la placa; recovery remoto = rollback OTA, SPEC §16.10)**: el firmware acepta solo orígenes listados,
  exige preflight Private Network Access, limita cuerpo/colas/clientes/rate, nunca devuelve claves.
  Nuestras tools envían `Origin: https://test.espectre.dev` (permitido por defecto, igual que el CLI).
- **XSS**: todo dato dinámico del dashboard se interpola escapado (`esc()`/`textContent`).
- **SQL**: siempre parametrizado; nunca concatenes valores del payload. Límites en `limite`/`device_id`.
- **BLE sin pairing** (si se activa el módulo opcional) es intencional: alcance corto, solo flags.
  Documentar el riesgo; el gate `seguridad` lo evalúa. BLE default n (coexistencia puede degradar CSI).

## GPIO (corregido — fuente: analista de viabilidad 2026-11-12, bbhpy/profharris)
- **UART0 real = 43/44** (la SPEC vieja de DriveGuard decía "41–44", MAL). **41/42 LIBRES**.
- **Buzzer = GPIO14**, **WS2812 = GPIO48**, LED flash = GPIO3 (no usado).
- **PROHIBIDO para periféricos**: 19/20 (USB nativo), 43/44 (UART0), 0/45/46 (strapping), 26–37
  (flash/PSRAM). **4–18 = bus DVP cámara: usados por OV3660 SOLO bajo `AULASENSE_IMAGEN=y` (F24);
  NINGÚN otro periférico puede tocarlos** (guardas `#error`). 22–25 no existen; 33–34 missing.
- Pool libre: 1, 2 (QWIIC, libres si no se usa el conector), 21, 41, 42, 47 (+38/39/40 sin SD).
- El feedback local (buzzer 14 + WS2812 48) no necesita GPIO libre extra. `app_feedback.c` lleva
  guardas `#error` si se configura un pin prohibido.

## Estructura
```
aulasense/
├─ PLAN.md SPEC.md README.md AGENTS.md .gitignore .env.example .env   # F26: .env ÚNICO raíz (gitignored)
├─ claves/                  # HISTÓRICO F25: fuentes archivadas `*.bak-f26` (GITIGNORED; la autoridad es .env raíz)
├─ firmware/
│   ├─ espectre/            # vendor ESPectre tag 3.2.0 (GITIGNORED, clon por tag, GPLv3)
│   └─ parche_aulasense/    # derivado propio: app_feedback.c/.h (F3), app_imagen.c/.h (F24-c,
│                           #   +transporte MQTT F25-c, +observabilidad/gate-sin-latch F26),
│                           #   app_provision.c/.h (F24-d; F26: AP fija + portal sin token),
│                           #   app_ota.c/.h (F26-c1: OTA por comando MQTT + manifiesto SHA-256 +
│                           #   rollback bootloader), Kconfig, CMake
│                           #   (F24-e visión en-chip: NO lanzada, archivada 2026-11-15 — PLAN §12.8)
├─ server/
│   ├─ ingesta/             # ingesta_mqtt.py (+decoder image MQTT F25) + esquema.py + receptor_imagen.py
│   ├─ dashboard/           # app.py + auth.py (F25 login) + static/ (FastAPI + WS + Chart.js +
│   │                       #   galería F24 + búsqueda F25, loopback+auth)
│   ├─ vision/              # F25: cliente.py (worker descripción IA, rate limits, modos A/B)
│   │                       #   F26: clef.py (decisión de aula, cascada clef-flash→clef, §16.6)
│   ├─ deploy/              # F25: units systemd WSL + README_wsl.md
│   │                       #   F26: + aulasense-ota.service (estático :8101 → túnel :10064)
│   ├─ ble/                 # receptor_ble.py (opcional, bleak; fuera de WSL)
│   ├─ fuentes/             # openmeteo.py (gratis sin key)
│   ├─ datos/               # aulasense.db + exports + imagenes/ (gitignored; tras cutover F25 la DB
│   │                       #   canónica vive en ~/aulasense-datos/ ext4 de WSL)
│   └─ tests/               # pytest (ambos runtimes: Windows py3.15 + WSL py3.13)
├─ analysis/                # eda.py, modelo_ocupacion.py, export_consolida.py, smoke_consolida.py (F24)
│                           #   clef_resumen.py (F26-h3) · run_analisis.ps1 · dataset/consolidado.csv (esquema §10.1)
├─ tools/                   # start_demo/stop_demo.ps1, gen_firmware_config.py, config_mqtt.py,
│   │                       #   lee_serial.py, purga_imagenes.py (F24), session_logger.py (F3)
│   │                       #   F25: vision_describe.py, reset_admin.py, wsl_aulasense.ps1
│   │                       #   F26: clef_decide.py, consolidar_env.py (--unificar .env único)
│   └─ generar_informe.py   # GENERADOR único de docs/entrega/INFORME.docx + PRESENTACION.pptx
│                           #   (la ruta vieja con subcarpeta de generadores no existe — D-F26-12)
├─ docs/                    # README.md (índice), PROPUESTA.md, fuentes.md, guion_demo.md (F26-f1),
│                           #   NOTES_operacion.md (F26-f1: manual de operación del dueño), Word/PPTX
│                           #   F25: guion_video.md, video_subtitulos.srt, video_aulasense.mp4
├─ assets/                  # figuras EDA/modelo, diagramas; F25: diagrama_tecnico.*, 
│                           #   poster_funcionamiento.png, video/ (escenas/audio intermedios)
└─ .tmp/                    # temporales (gitignored)
```

## Comandos frecuentes (Windows PowerShell)
```powershell
# Firmware (ESP-IDF 5.5.6 vía eim; NO python del PATH):
cd firmware/espectre/src/cpp/frontend/native/app
eim run "idf.py set-target esp32s3" v5.5.6; eim run "idf.py build" v5.5.6; eim run "idf.py -p COM6 flash monitor" v5.5.6
# BUILD ÚNICA F26 EN VIGOR (placa viva f26.6): NO volver a flashear por UART — la única reflasheada
# autorizada fue la "destraba" f26.3 (orquestador, 2026-11-17; gate 120/64 inalcanzable → 96/48).
# TODO cambio futuro de firmware viaja por OTA (check→apply; SPEC §16.1, docs/NOTES_operacion.md).
# Fallback CLI (si el app lo requiere): .\espectre.cmd native build --chip s3 --backend local  (IDF_PATH de eim)
# NO usar . $env:IDF_PATH\export.ps1 (python roto).

# Server (SIEMPRE venv py3.15):
server\.venv\Scripts\python.exe -m pip install -r server/requirements.txt
server\.venv\Scripts\python.exe server/ingesta/ingesta_mqtt.py
server\.venv\Scripts\python.exe server/dashboard/app.py            # http://127.0.0.1:8000
server\.venv\Scripts\python.exe server/fuentes/openmeteo.py

# Config del nodo (lee .env, nunca imprime claves):
C:\Python315\python.exe tools/gen_firmware_config.py    # WiFi → sdkconfig.defaults.local (gitignored) + AULASENSE_UPLOAD_TOKEN
C:\Python315\python.exe tools/config_mqtt.py            # PATCH /mqtt al broker (user nodo electiva)

# F24 · Purga de imágenes (retención configurable; conserva fila con imagen_purgada, dataset intacto):
server\.venv\Scripts\python.exe tools/purga_imagenes.py --dias 30   # default .env AULASENSE_IMG_RETENCION_DIAS

# Demo / análisis:
powershell tools/start_demo.ps1 ; powershell tools/stop_demo.ps1
powershell analysis/run_analisis.ps1    # SIN flags --origen/--validacion (NO existen): origen=real es
#   inherente al pipeline y las validaciones se autodescubren por glob docs/validacion_sesion*.csv.
#   Parámetros reales: -Python, -Distro, -SinWsl, -NoFiguras.

# F25 · Runtime WSL (primario; skill wsl-manager-cli; NUNCA dos stacks vivos):
powershell tools/wsl_aulasense.ps1 status          # systemctl is-active ×3 + URL del dashboard
powershell tools/wsl_aulasense.ps1 start           # arranca servicios WSL (ingesta/dashboard/vision)
powershell tools/wsl_aulasense.ps1 stop            # para servicios WSL
powershell tools/wsl_aulasense.ps1 salud           # F26: /api/salud + túneles SOLO state/remote → datos/tuneles_estado.json
powershell tools/wsl_aulasense.ps1 refresh-portproxy  # WSL_IP cambió tras reboot (admin; fallback sin admin)
wsl -d Debian -- sh -c 'cd /home/aulasense/aulasense && ~/venvs/aulasense/bin/python -m pytest server/tests analysis/tests -q'   # tests en runtime WSL (el `cd` es obligatorio: sin él, `server/tests` no resuelve)
# F25 · Visión (modo B agente-mediado con key TP; modo A daemon SOLO sk-* pay-as-you-go):
#   el agente lanza: tools/vision_describe.py --once --limit 30   (rate limits/dedupe/backoff en el CLI)
# F25/F26 · Reset admin (acceso físico del dueño; regenera password en el .env ÚNICO raíz —
#   F26 §16.8: ya NO en claves/.env —, nunca la imprime):
#   tools/reset_admin.py
# F26 · OTA: disparo check|apply por MQTT con el helper publicar_comando_ota de la ingesta —
#   paso a paso y verificaciones en docs/NOTES_operacion.md (NUNCA apply sin sha externo verificado).
```

## Estilo
- Firmware C/C++: snake_case, funciones ≤60 líneas, un módulo por responsabilidad. Logs `ESP_LOGI/W/E`
  con tag corto. El parche `app_feedback` no bloquea el detector (tarea de prioridad baja).
- Python: type hints, funciones puras donde se pueda, `paho-mqtt` callback → cola → writer único a
  SQLite. SQL parametrizado. `esc()` en el dashboard.
- **Commits: atómicos, mensaje en español imperativo ("Añade feedback de buzzer en app_feedback"),
  los hace SOLO el `@1-orquestador` con sus manos propias de git** (roster vigente: el ejecutor NO
  commitea; corrección de la regla vieja de DriveGuard que lo encargaba al ejecutor).
- Todo entregable nuevo se registra en `../.tmp/checklist.md`; **solo el orquestador marca `[x]`**
  (verifica en disco); los workers reportan con evidencia `archivo:linea`, nadie más edita el checklist.
- Notificaciones de subagentes pueden perderse (lección E8): el entregable verificable en DISCO con
  criterio objetivo es la fuente de verdad, no el reporte del worker.