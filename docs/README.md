# docs/ — Índice de lectura de AulaSense

> Orden recomendado para el profesor/evaluador y para los agentes que ejecutan el plan.
> Estado: ✅ = existe en disco (andamiaje del planificador, 2026-11-12); ⏳ = lo crea la fase indicada
> del mapa `PLAN.md` §6/§11/§12/§13 (checklist vivo: `iot2/.tmp/checklist.md` **v26**).
> **F25 (2026-11-14):** imágenes por MQTT + servidor WSL + visión IA con búsqueda + login +
> diagramas + video — decisiones D-F25-1..13 en `PLAN.md` §12, contratos en `SPEC.md`
> §3.2.1/§6.2/§7.2/§7.3/§8.2/§9.2/§9.3/§12.1/§15.
> **F26 (2026-11-17):** OTA real por MQTT con rollback + provisioning fijo sin token + gate/heap
> interno + Clef + panel de salud + `.env` único — decisiones D-F26-1..12 en `PLAN.md` §13,
> contratos en `SPEC.md` §16 (arquitectura real: SPEC §16.10).

## Lectura rápida (qué es y cómo se usa)

| # | Documento | Estado | Contenido |
|---|---|---|---|
| 1 | [`../README.md`](../README.md) | ✅ | Qué es AulaSense, instalación, arranque de demo, estructura, privacidad |
| 2 | [`PROPUESTA.md`](PROPUESTA.md) | ✅ | Propuesta para el profesor (estilo del curso): objetivo, arquitectura 4 capas, datos, preguntas analíticas, diferenciadores, alcance, limitaciones honestas, cobertura C1–C14 |
| 3 | [`../PLAN.md`](../PLAN.md) | ✅ | Plan maestro: matriz de decisión, supuestos S1–S22+S-F26, decisiones D1–D12 + D-F24-1..8 + D-F25-1..13 + **D-F26-1..12**, mapa de fases F0–F7+GATE+F24+F25+**F26 (§13)** con agentes/entregables/criterios, trazabilidad C1–C14 + **orden del dueño 15 ptos §13.7**, riesgos E1–E10+R1–R16+R-F25+R-F26 |
| 4 | [`../SPEC.md`](../SPEC.md) | ✅ | Especificación técnica: hardware, pinout corregido, firmware ESPectre, contrato MQTT (+image F25 +extensiones F26 §16.4), esquema SQLite (+auth/visión F25 +Clef/estado_sistema/heap interno F26 §16.5), dashboard (+login/búsqueda/+salud §16.7), WSL §15, **contrato F26 completo §16** (OTA/provisioning fijo/Clef/.env único/arquitectura real), comandos exactos |
| 5 | [`../AGENTS.md`](../AGENTS.md) | ✅ | Convenciones y reglas duras para todo agente (edge-first, privacidad, python, IDF 5.5.6, secretos, GPIO, seguridad) |
| 6 | [`ENTREGA_PROFESOR.md`](ENTREGA_PROFESOR.md) | ✅ | **Recorrido guiado de 10 min para el profesor** (el README raíz enlaza aquí): dashboard público, métricas con tamaño muestral, F26 (OTA/rollback/Clef/portal), cómo reproducir y limitaciones honestas |

## Evidencia y fuentes

| Documento | Estado | Contenido |
|---|---|---|
| [`fuentes.md`](fuentes.md) | ✅ | Todas las URLs consultadas (2026-11-12) con el claim que sostiene cada una y el verificador (planificador/analista). 0 llamadas pagadas |
| `iot2/.tmp/f23_research_tendencias.md` | ✅ (gitignored) | Informe verbatim del analista de tendencias + addendum de verificación primaria |
| `iot2/.tmp/f23_research_viabilidad.md` | ✅ (gitignored) | Informe verbatim del analista de viabilidad (GPIO corregido, veredictos A–E) |

## Firmware propio

| Documento | Estado | Contenido |
|---|---|---|
| [`../firmware/parche_aulasense/README.md`](../firmware/parche_aulasense/README.md) | ✅ | Spec del módulo `app_feedback` (buzzer/WS2812/die temp/BLE opcional), `app_imagen` §9/§12/§14 (QVGA + MQTT F25 + gate 95/24 sin latch F26 — valores VIVOS en §14.1/§14.8; §9 conserva la tabla histórica F24-c), `app_provision` §13→§14.4 (portal FIJO sin token F26), **`app_ota` §14 (OTA por comando MQTT + manifiesto SHA-256 + rollback; gate 96/48 enmendado con evidencia §14.2; whitelist retains §14.3)**, instrucciones de aplicación, guardas GPIO, GPLv3 |

## Entregables de ejecución (los crean las fases)

| Documento | Estado | Fase | Contenido |
|---|---|---|---|
| `guion_demo.md` | ✅ | F26-f1 | Guion de demo cronometrado (~10 min) REAL post-F26: público con login, /salud, galería con decisión Clef, disparo `check` de OTA en vivo, portal in-situ opcional, fallbacks y honestidad (SPEC §11 apunta aquí) |
| `NOTES_operacion.md` | ✅ | F26-f1 | **Manual de operación del dueño**: cómo disparar una OTA (check→apply, manifiesto, :10064, verif. sha), semántica del rollback (225 s), agujero de telemetría 30 s, re-provisionamiento en sitio (AP `AULASENSE-CFG` + portal sin token, paso a paso con curl), verificación remota post-reinstalación, mejoras post-entrega y limitaciones |
| `plantilla_validacion.md` | ⏳→N/A | F4 | Plantilla de registro manual: **no se creó como archivo** — el registro de la sesión 1 se publicó directo en `validacion_sesion1.csv` |
| `validacion_sesion1.csv` | ✅ | F4 | Intervalos motion/id de la sesión F4S1B con timestamps (C10); `validacion_sesion2b/3b.csv` ✅ **solo cabecera (GT fotográfico=0** — cámara caída en el sitio remoto; S2B/S3B aportan estabilidad multi-día, NO recall/precision) |
| `INFORME.docx` / `PRESENTACION.pptx` | ✅ REGENERADOS (docs/entrega/) | F6a → **F26-f2 (2026-11-17)** | Regeneración F26 COMPLETA: presentación paso a paso **12 bloques (16 láminas)** con código real del repo (16 snippets `archivo:linea` leídos en tiempo de generación, etiqueta `FALTA_SNIPPET` si alguno falla) e INFORME con §6 Clef y §7 operación/OTA/monitoreo; cifras honestas (81/60 fotos/decisiones, 75 % DEGRADADA, 1.198 filas, recall 0,879 n=35). Generador: `../tools/generar_informe.py` |
| `../analysis/informe_eda.md` | ✅ | F5a | EDA + modelo predictivo generados por `eda.py`/`modelo_ocupacion.py` v f5a-2.0 (0 números a mano); métricas con tamaño muestral (C5) |
| `../dataset/sesion_1/registro_F4S1*.jsonl` | ✅ | F4 | Dataset propio publicado de la sesión 1 con ground truth pasivo corroborado (commit e3ba4b5) |
| `../analysis/dataset/consolidado.csv` | ✅ | F24-b1/f2 → **regen F26-h3** | Dataset consolidado **regenerado 2026-11-17: 1.198 filas, 100 % `origen=real`, 4 días** (S1+S1B+S2B+S3B+eventos F26; regla ts explícita que filtra la fila absurda id 56 — documentada, BD sin editar). Regenerable con `analysis/export_consolida.py` desde la BD canónica WSL |
| Figuras EDA/modelo/Clef (13) | ✅ | F5a → **regen F26-h3** | **En `docs/analisis/graficos/*.png`**: 11 heredadas re-renderizadas + 2 NUEVAS Clef (`clef_distribucion_decisiones.png`, `clef_revision_calidad.png`); JSONs `docs/analisis/{analisis,eda,modelo_ocupacion,clef_resumen}.json` (desviación anotada de la convención vieja `assets/*.png` — PLAN §12.8) |
| [`analisis/informe_metricas.md`](analisis/informe_metricas.md) | ✅ | F5a → **regen F26-h3** | Informe de métricas generado por el pipeline (`analizar_dataset.py` v f5a-1.1, 0 números a mano): matriz de confusión vs GT (**TP 29/FN 4 → recall 0,879, n=35**, precision no computable con nota), integridad JPEG (57/80 = 71,25 %), foto↔onset, latencias, resumen visión/Clef con costes; regenerado en cada corrida de `../analysis/run_analisis.ps1` (último: 2026-11-17) |

## Entregables F25 (expansión 2026-11-14, orden del dueño)

| Documento | Estado | Fase | Contenido |
|---|---|---|---|
| `../assets/diagrama_tecnico.*` | ✅ | F25-f1 → **regen F26-d** | Diagrama técnico C4 en 4 vistas REGENERADO para la arquitectura REAL F26 (SPEC §16.10): `diagrama_tecnico.dsl/.svg/.png` (contenedores: MQTT :14769, dashboard :8100→túnel :10062, estático OTA :8101→túnel :10064), `diagrama_tecnico_contexto.svg/.png`, `diagrama_tecnico_componentes_nodo.svg/.png` (CSI→MLP→cámara gate interno→outbox + prov fijo + app_ota), `diagrama_tecnico_componentes_dashboard.svg/.png` (auth/API/**salud F26**/WS/galería/búsqueda) + `diagrama_flujo_f26.mmd/.svg/.png` (flujos OTA + evidencia + alternativo LAN-only); evidencia `.tmp/f26d_diagrama.md` |
| `../assets/poster_funcionamiento.png` | ✅ | F25-f2 | Póster visual del concepto CSI (ondas/persona/chip/dashboard), skill qwen-imagen (@6-medios) |
| `guion_video.md` | ✅ | F25-f3 | Guion del video explicativo: DriveGuard→fallo caras→CSI→cómo funciona→construcción→finalidad |
| `video_subtitulos.srt` | ✅ | F25-f3/f5 | Subtítulos del video (burn-in en el ensamblaje; re-timado real en `.tmp/video_final.srt`) |
| `video_aulasense.mp4` | ✅ | F25-f5 | Video explicativo completo (H.264+AAC 480P, 65,96 s verificado por ffprobe 2026-11-15; escenas HappyHorse 480P + narración TTS longanlingxin + overlays drawtext + subtítulos burn-in; ensamblaje FFmpeg local, coste extra $0) |
| `video_como_funciona_40s.mp4` | ✅ | paquete visual F25 (F25-f/g) | Resumen técnico silencioso de 40,00 s (H.264 sin pista de audio, verificado en `GUIA_VISUAL.md` §8): CSI → detector en-chip → MQTT → dashboard — pieza 2 del recorrido de `ENTREGA_PROFESOR.md` |
| `../server/deploy/README_wsl.md` | ✅ | F25-b1 (+F25-wsl/F25f) | Despliegue WSL Debian: venv, systemd, portproxy loopback, cutover, fallback Windows; + distro dedicada `debian-aulasense` (:8100, publicación VPS `:10062` por túnel CLI local) y estado actual del receptor `:8001` con caveat de ruteo LAN (matriz final F25-b1 ronda 2 = CUMPLE, `.tmp/f25b1_wsl.md` §13) |
| `../claves/.env.example` | ✅ (referencia **histórica**) | andamiaje F25 | Nombres de variables de auth/IA (valores vacíos); desde F26 la fuente canónica es la **`.env.example` de la raíz** con el layout del `.env` ÚNICO (§16.8) |
| [`consentimiento_aula.md`](consentimiento_aula.md) | ✅ | F24-f→F25-g (publicado 2026-11-17) | **Aviso + consentimiento informado de la captura de evidencia fotográfica** (regla 2): qué se captura (1 JPEG QVGA al onset, cooldown 15 s), qué NO (video/audio/caras-ML/identificación), destino final solo servidor local (broker = transporte retain=false), capa IA opcional transparente, galería tras login, derechos (no vigilancia ni evaluación; motion≠presencia regla 11), voluntariedad (el sistema funciona sin fotos: `AULASENSE_IMAGEN=n`) y firmas alumno/docente |
| [`politica_retencion_imagenes.md`](politica_retencion_imagenes.md) | ✅ | F24-f→F25-g (publicado 2026-11-17) | **Retención 30 d configurable** (`AULASENSE_IMG_RETENCION_DIAS`; prioridad `--dias` > `.env` > 30) + mecanismo auditado `tools/purga_imagenes.py` (borra archivo, marca `imagen_purgada=1`, CONSERVA fila+metadatos no personales → dataset intacto, con explicación de por qué no re-identifica), borrado anticipado (`--dias 0`, registro en BD y acta, limitación honesta de supresión por id), retención de descripciones/decisiones IA (sin nombres — prompt `cliente.py:177`; redacción si el QC detecta una) y copias de seguridad/alcance (BD local WSL; el VPS solo transporta — retain=false) |
| `INFORME.docx` / `PRESENTACION.pptx` | ✅ (regenerados por F26-f2 en `docs/entrega/`) | F6a (actualizado F25 → F26-f2) | La regeneración con TODO (WSL, MQTT-imágenes, visión+búsqueda, login, diagramas, video, costes Credits reales, OTA/Clef F26) **quedó incorporada en la regeneración F26-f2** — ver fila F26-f2 en la tabla de entregables F26 |

> **Nota QC honesta del video (F25-f5):** en la escena 6 la cuadrícula de miniaturas muestra un
> **rostro sintético generado por la IA** (no es persona real); las pantallas de las escenas 5 y 7
> contienen pseudo-texto ilegible (no se requiere legibilidad — todo el texto real va como overlay
> FFmpeg). El video dice "detecta movimiento", nunca "presencia", y no cita métricas del vendor.

## Entregables F26 (expansión 2026-11-17, orden del dueño — contrato: SPEC §16)

| Documento | Estado | Fase | Contenido |
|---|---|---|---|
| `NOTES_operacion.md` | ✅ | F26-f1 | Manual de operación del dueño (OTA paso a paso, rollback, re-provisionamiento en sitio, verificación remota, gate/heap, mejoras y limitaciones) — **destino para operar la placa desde casa** |
| `guion_demo.md` | ✅ | F26-f1 | Guion de demo cronometrado real post-F26 (reemplaza la referencia muerta de SPEC §11) |
| `../firmware/parche_aulasense/README.md` §14 | ✅ | F26-c1→g1 | OTA `app_ota.c`, semántica check/apply, gate **96/48 enmendado con evidencia** (§14.2), retains/events whitelist (§14.3), provisioning fijo (§14.4), sdkconfig y enganches (§14.5/6) |
| `../server/deploy/README_wsl.md` §OTA | ✅ | F26-b | Estático `aulasense-ota.service` (:8101) + túnel `pub-aulasense-ota` → VPS **:10064** + verbo `salud` |
| `../.env.example` | ✅ | F26-b | Layout del `.env` ÚNICO raíz con secciones ordenadas (§16.8; valores placeholder vacíos — la pass canónica del AP solo en `.env`+NOTES por orden del dueño) |
| `analisis/clef_resumen.json` + 2 figuras Clef | ✅ | F26-h2/h3 | Distribución medida de decisión de aula (60 decisiones reales; **75 % IMAGEN_DEGRADADA** = hallazgo central; REVISIÓN_HUMANA 44/60; coste medido $0,0053/62 llamadas API) |
| `INFORME.docx` / `PRESENTACION.pptx` (regenerados F26) | ✅ CUMPLE 2026-11-17 14:40 | **F26-f2** | REGENERADOS: INFORME.docx 3,29 MB (§6 Clef + §7 operación incluidos) + PRESENTACION.pptx 2,41 MB — 12 bloques (11a/11b) en 16 láminas con código real greppable (`archivo:linea`, 16 snippets leídos del repo en tiempo de generación); `verificar_salidas()` OK ≤10 MB; QA visual lámina a lámina. Generador re-ejecutable: `../tools/generar_informe.py` |
| `GUIA_VISUAL.md` / `entrega/GUIA_VISUAL.{docx,pdf}` / `entrega/PRESENTACION_VISUAL.pptx` | ✅ (paquete **F25**, anotado) | F25-f1/f2/g | Paquete visual previo: sigue vigente conceptualmente; el diagrama técnico que enlaza fue regenerado en F26-d (mismas rutas `assets/`). **Las cifras del cuerpo de la guía son del corte F25 (2026-11-15: 462 filas, 54 fotos)** — anotado en el encabezado de `GUIA_VISUAL.md`; los números VIGENTES post-F26 (1.198 filas / 81 fotos / 60 decisiones / f26.6) están en `ENTREGA_PROFESOR.md` y `README.md`. **Refresh F26 completo opcional post-entrega** (no bloquea: el paquete ofimático F26-f2 sale del generador del repo) |

## Cómo ver el sistema (verificado 2026-11-15; URLs re-sondeadas en la auditoría documental de esta tarde **y otra vez 2026-11-17 por el GATE de documentación**: `:10062/login` 200 + `/api/eventos` 401, `:10064/manifest.json` 200 con `version=f26.6` (sha `339A1A74…` == el de NOTES_operacion), NAT `:8100` y `:8000` 200/401 en vivo, `127.0.0.1:8000` sin respuesta como se nota)

> **Arquitectura REAL (SPEC §16.10, D-F26-11 — zero-asunción-LAN D-F26-10):** el **VPS es SOLO
> broker MQTT (`:14769`) + túneles SSH wsl-port**; el servidor que **gestiona TODO** (ingesta, BD
> ext4, dashboard `:8100`, estático OTA `:8101`, workers visión/Clef) corre en **WSL
> `debian-aulasense`**. La placa opera lejos del servidor y **solo abre conexiones salientes**
> (MQTT + descarga OTA por el túnel público): el servidor **jamás alcanza a la placa** — `Direct
> HTTP :62587` y el receptor `:8001` son **LAN-only/opcionales**; la exposición pública del
> dashboard (:10062) y del OTA (**:10064** — asignado y verificado, SPEC §16.9) va por túneles iniciados desde WSL. Recuperación
> remota = rollback automático de la OTA (SPEC §16.1); UART/BOOT solo in situ.

Runtime primario del dataset/despliegue actual: **distro WSL `debian-aulasense`** (dashboard interno
**:8100**, publicado en el VPS en `:10062`); el Debian 13 original (systemd
`aulasense-ingesta`/`aulasense-dashboard`, :8000) sigue vivo como runtime secundario
(`aulasense-vision` instalada y disabled por diseño — SPEC §15, `../server/deploy/README_wsl.md`).
URLs vigentes (orden de preferencia):

| URL | Dónde | Verificación |
|---|---|---|
| `http://167.114.169.134:10062` | **VÍA PÚBLICA PRIMARIA** — publicación VPS del dashboard de `debian-aulasense` (puerto interno 8100) vía túnel SSH auto-start de `wsl-port` **CLI local** (túnel `pub-aulasense-8100`). No requiere token `wsl-port` ni admin de Windows | `/login` → **200** y `/api/eventos` → **401** sin cookie (re-verificado desde fuera el 2026-11-15 por esta auditoría); Origin público pasa. Panel **`/salud`** (F26) tras login |
| `http://167.114.169.134:10064` | **ESTÁTICO OTA PÚBLICO (F26-b, túnel `pub-aulasense-ota` ← WSL :8101)** — SOLO `manifest.json` + bins de firmware; NUNCA forma parte del dashboard ni de la superficie de datos personales (SPEC §16.9) | `manifest.json` → **200** verificado externamente (g1); sha del bin servido == sellado del manifiesto |
| `http://172.26.159.208:8100` | dashboard `debian-aulasense` en la IP NAT de la VM WSL, desde el navegador Windows | `/api/eventos` → **401** (vivo, 2026-11-15). La IP WSL **cambia tras reboot**: refrescar `AULASENSE_ORIGINS_EXTRA` de la unit (receta README_wsl §P7/§P8) |
| `http://172.26.159.208:8000` | Debian 13 original (systemd) — vía fallback NAT de F25-b1 | `/api/eventos` → **401** (vivo, 2026-11-15). Misma nota de IP cambiante: `powershell tools\wsl_aulasense.ps1 status` |
| `http://127.0.0.1:8000/login` | loopback **dentro de la VM WSL** | `/login` → **200** (evidencia `.tmp/f25b1_wsl.md` §6). **No vigente en Windows**: el stack fallback está parado y el portproxy sigue pendiente de admin (hoy ese loopback NO responde — sondeado 2026-11-15) |

Acceso: requiere **login** (usuario `admin`; la password la genera el código y vive en el
**`.env` ÚNICO de la raíz** — F26 D-F26-6/SPEC §16.8; gitignored, NUNCA se imprime en documentación;
`claves/` quedó archivado como referencia histórica).

**Pendientes NO vigentes (honesto — no usar como vía activa):**
1. **Portproxy `netsh` `127.0.0.1:8000`→WSL** en Windows: requiere **admin** y el stack Windows
   parado (cutover F25-b2). La publicación pública **no lo necesita** (vía `:10062` arriba).
2. **Receptor N2 `:8001` alcanzable desde la ESP32** (**canal LAN-only y opcional — en operación
   remota el servidor nunca alcanza a la placa**): re-habilitado el 2026-11-15 DENTRO de
   `debian-aulasense` (bind `172.26.159.208:8001`, fire-test 201 — README_wsl §Receptor), pero la
   IP NAT no es enrutable desde la LAN 192.168.40.x del aula: hace falta portproxy admin sobre la
   IP LAN + firewall, o repunter el `CONFIG_AULASENSE_UPLOAD_HOST` del nodo a la IP LAN del host
   en el siguiente ciclo de build (decisión del dueño). Con MQTT primario (D-F25-1) no es
   bloqueante.
3. **Portal AP fijo — F26 YA ES LA REALIDAD VIVA (build en placa f26.6)**: AP fija `AULASENSE-CFG`
   con WPA2 (pass canónica `itse-aulasense`, publicada por orden explícita del dueño como
   **capacidad local** — exige asociación física al AP) y rutas **sin token** `/cfg`, `/cfg/estado`,
   `/cfg/wifi` (SPEC §16.2). La build vieja con token F24-d (digest `effa314f`) quedó **superada por
   el flasheo c2 + las OTAs g1/g2**. Queda pendiente **solo el humo vivo E2–E5** (caída natural del
   router en el aula): procedimiento completo en `docs/NOTES_operacion.md` §4. Evidencia verificable
   alternativa del portal (bin strings + boot log): `.tmp/f26_c3_evidencia.md` §1.
4. **Sesiones F4 S2B/S3B**: **integradas en el análisis F26-h3** con nota honesta — GT fotográfico=0
   (cámara caída en el sitio remoto: `validacion_sesion2b/3b.csv` solo cabecera) → aportan
   estabilidad multi-día y distribución (pps_eval 3,5–3,9 estable en 4 sesiones), **no** recall ni
   precision; las métricas de validación siguen ancladas a S1 (recall 0,879, TP 29/FN 4, n=35).
5. *(Histórico: la publicación VPS figuraba como pendiente y **ya está vigente** — ver tabla.)*

> **Nota QC honesta de las imágenes (histórico F25, estado actual F26-h3):** en F25 se auditó el lote
> de 55 filas (53 fotos reales + 2 sintéticas): 32 válidas / 23 corruptas **de origen en la placa** — byte-loss GDMA pre-Fix F, causa
> raíz en `iot2/.tmp/jpegqc_causa_raiz.md` (E9: 55 filas = 55 ficheros; las 53 reales, todas `canal='mqtt'`); visión cerró 34 hechas/20 error/1 omitido; las 20
> irreparables quedan en `'error'` sin coste extra). **Estado actual (F26-h3): 81 fotos reales en BD
> (60 decisionadas por Clef); integridad PIL 57/80 = 71,25 % post-exclusión de la fila absurda
> documentada (id 56, BD sin editar); fotos↔onset 67,5 %** — las 26 nuevas post-revival (ids 58-83)
> sin onset ±20 s son señal QC honesta en seguimiento (regla de campo ≥80 % con tránsito real,
> `docs/NOTES_operacion.md` §5). Fix F (xclk 10 MHz + descarte estructural en placa) y Fix S (descarte
> en el decoder) vigentes. Incidente documentado: dos procesos de visión solapados quemaron 2.972
> tokens en 3 re-descripciones (regla: 1 pasada a la vez, dentro del turno — AGENTS.md regla 16).

## Contexto del proyecto anterior (archivado)

DriveGuard (`dashcam-ia/`, dashcam de microsueño) fue **abandonado por decisión del usuario**: el
detector de caras en vivo nunca detectó una cara real pese a encuadre perfecto (fracaso terminal
documentado en `PLAN_DETECCION.md` E1–E3). En F0 el orquestador lo archiva en `iot2/archive/dashcam-ia/`
(historia preservada). AulaSense hereda su infraestructura como **patrón** (E7) y sus lecciones como
**restricciones de diseño** (E1–E10, `iot2/.tmp/registro_riesgos_nuevo.md`), pero es un proyecto nuevo:
sensing por Wi-Fi CSI con IA en-chip (el detector no necesita cámara), evidencia de imagen QVGA acotada
(F24: destino solo el servidor local, consentimiento + retención purgable; F25: transporte primario
MQTT autenticado retain=false, HTTP N2 alternativo) y prueba de vida temprana
(gate F1c).