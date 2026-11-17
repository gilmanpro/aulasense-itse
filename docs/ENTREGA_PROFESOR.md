# AulaSense — Entrega para el curso (lea esta pagina primero)

**Que es:** un sensor de movimiento que usa las ondas Wi-Fi (CSI) y una red neuronal que corre
**entera dentro del chip ESP32-S3** — sin microrfono, sin radar, sin hardware extra. Cada vez que
detecta movimiento sube el evento por MQTT, guarda una foto de evidencia con descripcion de IA, y
alimenta un dashboard publico, un dataset propio y un informe de metricas totalmente reproducible.

Esta pagina es el recorrido guiado. El `README.md` de la raiz queda como indice tecnico.

---

## Resultado en vivo — empiece aqui

**Dashboard publico:** [http://167.114.169.134:10062](http://167.114.169.134:10062)

> Las credenciales de acceso se entregan al profesor por el **canal privado del curso**. No figuren
> ni figuraran nunca en este repositorio.

Al entrar vera:

- **Eventos en tiempo real**: cada deteccion de `motion` del chip, con senales del nodo (temperatura
  del die, tasa CSI) refrescados por WebSocket.
- **Galeria de evidencia fotografica**: 1 foto QVGA por evento, cada una con la **descripcion
  generada por IA** (Qwen vision), su **decision de aula con Clef** (etiqueta + confianza + badge
  de revision humana cuando corresponde) y su estado QC honesto. Hoy: **81 fotos reales**
  (**60 con decision Clef**).
- **Busqueda por texto**: consulte la galeria escribiendo en espanol lo que busca ("alguien camina
  frente al pizarron") y la IA filtra por las descripciones.

---

## Recorrido recomendado (10 minutos)

Rutas relativas dentro del repositorio, en orden sugerido:

| # | Pieza | Que aporta | Tiempo |
|---|---|---|---|
| 1 | [`video_aulasense.mp4`](video_aulasense.mp4) | El arco completo del proyecto, **contado honesto**: incluye por que fallo la camara del proyecto anterior (ese fallo es el origen de este diseno) | ~1 min |
| 2 | [`video_como_funciona_40s.mp4`](video_como_funciona_40s.mp4) | Explicacion tecnica rapida: CSI → detector en-chip → MQTT → dashboard | ~40 s |
| 3 | [`GUIA_VISUAL.md`](GUIA_VISUAL.md) | Recorrido grafico con figuras: arquitectura, flujo de datos, topologia de red y privacidad | 3 min |
| 4 | [`entrega/INFORME.docx`](entrega/INFORME.docx) | Informe formal completo (diseno, implementacion, validacion, metricas) | para casa |
| 5 | [`entrega/PRESENTACION_VISUAL.pptx`](entrega/PRESENTACION_VISUAL.pptx) y [`entrega/PRESENTACION.pptx`](entrega/PRESENTACION.pptx) | Dos decks: uno grafico para exponer, uno de datos para defender numeros | 2 min |
| 6 | [`analisis/informe_metricas.md`](analisis/informe_metricas.md) + [`analisis/graficos/`](analisis/graficos/) | Metricas propias con tamano de muestra (matriz de confusion vs ground truth, EDA y el resumen Clef) y las 13 figuras | 2 min |
| 7 | [`../dataset/sesion_1/`](../dataset/sesion_1/) | **Datos reales crudos** (JSONL) con su ground truth en [`validacion_sesion1.csv`](validacion_sesion1.csv) | 1 min |
| 8 | [`../analysis/run_analisis.ps1`](../analysis/run_analisis.ps1) | Todo el analisis (EDA, metricas, figures, JSON) reproducible **con un comando** | — |
| 9 | [`../firmware/parche_aulasense/`](../firmware/parche_aulasense/) | El codigo propio del chip (derivado GPLv3 de ESPectre, con README que explica como aplicar el parche) | 1 min |
| 10 | [`../server/`](../server/) | Ingesta MQTT, dashboard con login, worker de vision IA y despliegue WSL | — |
| 11 | [`../assets/diagrama_tecnico.png`](../assets/diagrama_tecnico.png) | Diagrama C4 de **contenedores F26**: que corre donde y con que protocolos/puertos (MQTT `:14769` al broker, dashboard publico `:10062` → interno `:8100`, OTA estatica `:10064` → `:8101` en WSL; VPS = solo broker + tuneles). Versiones de detalle: `diagrama_tecnico_contexto.png`, `diagrama_tecnico_componentes_nodo.png`, `diagrama_tecnico_componentes_dashboard.png` (mismo `.svg` editable que el `.png`) | 1 min |
| 12 | [`../assets/diagrama_flujo_f26.png`](../assets/diagrama_flujo_f26.png) | Flujos F26 fuente-a-destino: **OTA** (comando MQTT → gate → descarga → SHA-256 → reboot → self-check → rollback 225 s), **evidencia** (onset CSI → foto → MQTT → vision → Clef) y el **alternativo LAN-only** (fuente Mermaid en [`../assets/diagrama_flujo_f26.mmd`](../assets/diagrama_flujo_f26.mmd)) | 1 min |
| 13 | [`guion_demo.md`](guion_demo.md) | **Guion de demo cronometrado (~10 min)** para exponer en vivo: pre-checks, que decir en cada panel y fallbacks | pre-demo |
| 14 | [`NOTES_operacion.md`](NOTES_operacion.md) | **Manual de operacion del dueño**: como disparar una OTA paso a paso, semantica del rollback, re-provisionamiento en sitio, verificacion post-energia y lista "nunca" | para casa |
| 15 | [`consentimiento_aula.md`](consentimiento_aula.md) y [`politica_retencion_imagenes.md`](politica_retencion_imagenes.md) | **Cumplimiento docente de la evidencia fotografica**: aviso + consentimiento informado de la captura (que se captura, que NO, quien lo ve, caracter voluntario) y politica de retencion (30 dias configurables, borrado auditado con `tools/purga_imagenes.py`, solicitud anticipada) — exigidos por `AGENTS.md` regla 2 | 1 min |

---

## Que hace unico este proyecto

- **Edge-first de verdad**: el detector neuronal (MLP de 529 parametros sobre CSI) corre 100 % en el
  ESP32-S3 y decide cada 250 ms. El CSI crudo **nunca sale del chip**; el PC solo recibe eventos ya
  decididos. Cero hardware extra: la placa y el router que ya existian.
- **Dataset propio, medido — no cifras del vendor**: metricas calculadas sobre datos reales con
  ground truth anotado: **recall de motion 0,879** (TP 29 / FN 4, **n=35**, anclado a la sesion S1,
  la unica con ground truth fotografico — en S2B/S3B la camara cayo en el sitio y GT=0, por eso
  sirven para estabilidad pero no para recall/precision). El **consolidado tiene 1.198 filas 100 %
  origen=real** (4 dias, regenerado en F26 con regla de timestamp explicita que filtra la fila
  absurda detectada sin editar la BD; 498.460 evaluaciones del detector). Las metricas publicadas
  por ESPectre (98,1–99,3 %) **no** se presentan como propias.
- **Ciclo completo hardware → nube → informes**: chip → broker MQTT autenticado → ingesta + SQLite
  en WSL Debian → dashboard con login publicado por tunel en VPS → vision IA → analisis pandas/
  sklearn → informe Word y presentacion, todo reproducible con un comando. La **arquitectura real**
  (SPEC §16.10): el VPS es **SOLO broker MQTT + tunels SSH**; el servidor que gestiona TODO (ingesta,
  BD, dashboard, workers de vision/Clef, servidor OTA estatico) corre en **WSL `debian-aulasense`**.
- **La placa opera LEJOS del servidor y solo hace conexiones SALIENTES** (zero-asuncion-LAN,
  D-F26-10): publica eventos/telemetria/imagenes por MQTT al broker y, desde la build F26, descarga
  el binario de actualizacion (OTA) por el puerto publico del tunel — el servidor **jamás alcanza a
  la placa**. Los canales Direct HTTP (:62587) y receptor de imagen (:8001) existen solo **en LAN y
  son opcionales**; toda gestion remota (incluida la OTA) viaja por el MQTT que la placa inicia, y el
  recovery remoto es el rollback automatico de la OTA (SPEC §16.1).
- **Honestidad documentada hasta el fallo**: de las 53 fotos reales originales (55 filas auditadas
  contando las 2 sintéticas), 23 llegaron
  corruptas de origen por un byte-loss de 16 B debido a overflow GDMA en la captura; la **causa
  raiz se encontro y se corrigio** (Fix F + Fix S). Estado actual del dataset: **81 fotos reales**
  (60 con decision Clef), integridad PIL **57/80 = 71,25 %** tras excluir la fila absurda
  documentada; las 20 irreparables quedan marcadas como tal y la etapa de decision las deriva a
  `IMAGEN_DEGRADADA` **en local, sin coste de API**. Las metricas se reportan **recall-only** con
  la razon (ground truth pasivo) en lugar de inflarlas.

---

## F26 — actualizacion remota, IA de decision y monitoreo (noviembre 2026)

La fase final anadio tres capacidades demostradas **en la placa viva (build f26.6)**:

- **OTA por aire, de punta a punta, dos veces** (f26.4 → f26.5 → f26.6): comando MQTT → gate de
  heap post-quiesce (96/48 KB, enmendado con evidencia desde los 120/64 del plan original) →
  descarga del binario por el tunel publico (~34 s para 1,4 MB) → verificacion **SHA-256 + tamano**
  → reboot → self-check de 90 s + gracia de 120 s → imagen validada. E2E ~163 s. El **agujero de
  telemetria de ~30 s es esperado** y el panel lo detecta (`ota_en_curso=true`) sin falsa alarma.
- **Rollback automatico probado** (anti-brick): con un binario malo de SHA valido (`f26.7-test`,
  self-check fallido a proposito) el bootloader revierte **solo, en 225 s medidos**, y la placa
  resucito sola 5 veces; con un hash alterado ni siquiera reinicia (`OTA_ERROR etapa=sha256`).
  La proteccion vive en el bootloader: toda OTA futura queda cubierta.
- **Portal de provisionado fijo, ya flasheado** (D-F26-4, realidad en la build viva): AP
  `AULASENSE-CFG` (WPA2) + rutas `http://192.168.4.1/cfg` **sin token** con sus guardas (Origin 403,
  cooldown 429, nunca loguea credenciales). Capacidad exclusivamente local: exige estar junto a la
  placa. Manual completo en [`NOTES_operacion.md`](NOTES_operacion.md).
- **Panel `/salud` con semaforos** (SPEC §16.7): ingesta, MQTT, frescura de telemetria, heap
  **interno** (no confundir con los ~8,2 MB de PSRAM), colas de vision/Clef, disco, tuneles y
  estado OTA retenido. Verificacion post-energia sin ir al sitio: polls solo-lectura a la BD.
- **Decision de aula con Clef** (OpenRouter Decisions API): cascada `clef-flash` (texto) → `clef`
  (imagen) si confianza < 0,6, sobre una taxonomia de 6 etiquetas (`SIN_PERSONAS_VISIBLES`,
  `PERSONAS_SENTADAS`, `CIRCULACION`, `PRESENTACION_AL_FRENTE`, `AGLOMERACION_OTRA`,
  `IMAGEN_DEGRADADA`), con badge REVISION_HUMANA y topes de Credits (120/dia, 20/hora) precedidos
  de medicion real (Paso 0: 809 tokens / $1,54e-5 / 747 ms). Medido sobre 81 fotos: **60 decisiones**,
  distribucion DEGRADADA 45 (**75 %**), SIN_PERSONAS 14, CIRCULACION 1; confianza media 0,648;
  REVISION_HUMANA 44/60. **Hallazgo honesto**: *decodificable ≠ legible* — calidad media 0,067 en
  las degradadas vs 0,542 en las legibles; el cuello es el encuadre QVGA (`jpeg_quality=12`), no la
  IA, y la mejora esta diseñada post-entrega (subirla **por OTA**, sin tocar el sensing CSI).
  Costes medidos: Clef **$0,0053 por 62 llamadas** (52.517 tokens); vision 26 llamadas / 12.180
  tokens.
- **Arquitectura real consolidada** (SPEC §16.10, visible en los diagramas de arriba): el VPS es
  **SOLO broker MQTT + tuneles SSH**; todo lo demas (ingesta, BD, dashboard, workers, estatico OTA)
  corre en WSL `debian-aulasense`, y la gestion remota es **outbound-only**: el servidor jamas
  alcanza a la placa. Las credenciales pasaron a un **`.env` unico en la raiz** (`claves/`
  archivado como `*.bak-f26`).

---

## Como reproducir

1. **Clonar** el repositorio.
2. **Configurar sus credenciales**: copiar `.env.example` a `.env` (archivo ignorado por git; nunca
   se commitea) y rellenar **sus** datos de WiFi 2.4 GHz y del broker MQTT. Las del curso se
   entregan por canal privado.
3. **Firmware**: instalar ESP-IDF **v5.5.6** con `eim`; clonar ESPectre tag `3.2.0` en
   `../firmware/espectre/` y aplicar el parche segun su README:
   `eim run "idf.py -p COM6 flash monitor" v5.5.6` (ruta primaria, sin Docker). COM6 es el puerto
   **USB-Serial-JTAG** nativo del S3 (la consola de este build NO sale por UART; para mirar logs sin
   resetear la placa: `tools/lee_serial.py COM6`).
4. **Server**: runtime nativo en **WSL Debian 13** (venv + systemd, sin Docker; Windows como
   fallback). Units y pasos en [`../server/deploy/README_wsl.md`](../server/deploy/README_wsl.md).
   El VPS interviene **solo** como broker MQTT (`:14769`) y tunels SSH de publicacion; no ejecuta
   codigo del proyecto.
5. **Analisis completo**: `powershell analysis/run_analisis.ps1` (sin flags) regenera metricas,
   figuras e informes desde el dataset: el pipeline **ya es inherente a origen=real** y **autodescubre**
   las validaciones por glob `docs/validacion_sesion*.csv` (los flags `--origen`/`--validacion` NO
   existen en el script; sus unicos parametros son `-Python`, `-Distro`, `-SinWsl`, `-NoFiguras`).

---

## Limitaciones honestas

- El endpoint publico se sirve por **HTTP sin TLS** (tunel SSH al VPS): el contenido es de demo; el
  acceso esta protegido por login y lockout, no por cifrado de transporte publico.
- La clave del broker MQTT compartido es corta y **las ACLs por topic aun estan pendientes**
  (aislamiento actual por prefijo de topic propio).
- El **portal de provisionado** tiene en F26 una forma fija **ya flasheada y operativa en la build
  viva (f26.6)** (D-F26-4, SPEC §16.2): AP `AULASENSE-CFG` con WPA2 (pass canónica del AP, que por
  orden explicita del dueño solo figura en [`NOTES_operacion.md`](NOTES_operacion.md) y en el `.env`:
  es una **capacidad local** — exige asociarse físicamente al AP de la placa y no habilita nada
  remoto) y ruta **sin token** `http://192.168.4.1/cfg`, con
  guardas (Origin/Referer 403, cooldown 5 s 429, body ≤255 B, nunca loguea SSID/pass). Quedan
  **pendientes** solo los escenarios **E2–E5** (caida real del router: **PENDIENTE-ventana-natural**,
  exigen una interrupcion no controlable).
- La actualizacion remota del firmware (OTA, F26) se sirve por **HTTP + verificacion SHA-256 con
  rollback automatico**, sin TLS (decision D3 del dueño, declarada): el tramo distro→VPS va cifrado
  por SSH y el tramo VPS→placa es HTTP plano **mitigado** por hash + rollback; TLS (nginx + Let's
  Encrypt) queda **post-entrega**. La descarga la inicia siempre la placa (saliente, por el tunel
  publico del VPS). La gestion remota es **outbound-only**: el servidor jamas alcanza a la placa
  (el recovery remoto es el rollback, no un canal hacia el nodo). Su gate de memoria quedo
  **enmendado de 120/64 a 96/48 KB con evidencia** (0/200 muestras alcanzaban 120; post-quiesce
  medido 103/48 — README del parche §14.2).
- **Rotacion de la pass SSH del VPS pendiente** (accion humana): la gestiona la app local `wsl-port`
  y no vive en el `.env` del proyecto.
- El dataset consolidado tiene **1.198 filas 100 % origen=real** (4 dias, regenerado en F26). Nota
  honesta de ground truth: **S2B/S3B tienen GT fotografico = 0** (la camara cayo en el sitio; sus
  CSV de validacion solo tienen cabeceras) — sirven para estabilidad y distribucion, **no** para
  recall/precision. La **precision no es computable** (no hay marcas de idle); por eso el recall
  0,879 se reporta anclado a S1 (n=35). Clef tampoco tiene GT de escena: sus 60 decisiones dan
  **distribucion, no precision**. El modelo de analisis (sklearn, n_test=52 con 5 positivos) da
  accuracy 0,904 = **igual al baseline**: se presenta como ejercicio docente, sin promesas.
- El sistema **detecta MOVIMIENTO**: no presencia estacionaria, no personas identificadas ni
  contadas. `idle` no significa sala vacia; una placa cubre una zona.
