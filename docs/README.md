# docs/ — Índice de lectura de AulaSense

> Orden recomendado para el profesor/evaluador y para los agentes que ejecutan el plan.
> Estado: ✅ = existe en disco (andamiaje del planificador, 2026-11-12); ⏳ = lo crea la fase indicada
> del mapa `PLAN.md` §6/§11/§12 (checklist vivo: `iot2/.tmp/checklist.md` **v25**).
> **F25 (2026-11-14):** imágenes por MQTT + servidor WSL + visión IA con búsqueda + login +
> diagramas + video — decisiones D-F25-1..13 en `PLAN.md` §12, contratos en `SPEC.md`
> §3.2.1/§6.2/§7.2/§7.3/§8.2/§9.2/§9.3/§12.1/§15.

## Lectura rápida (qué es y cómo se usa)

| # | Documento | Estado | Contenido |
|---|---|---|---|
| 1 | [`../README.md`](../README.md) | ✅ | Qué es AulaSense, instalación, arranque de demo, estructura, privacidad |
| 2 | [`PROPUESTA.md`](PROPUESTA.md) | ✅ | Propuesta para el profesor (estilo del curso): objetivo, arquitectura 4 capas, datos, preguntas analíticas, diferenciadores, alcance, limitaciones honestas, cobertura C1–C14 |
| 3 | [`../PLAN.md`](../PLAN.md) | ✅ | Plan maestro: matriz de decisión, supuestos S1–S22, decisiones D1–D12 + D-F24-1..8 + D-F25-1..13, mapa de fases F0–F7+GATE+F24+F25 con agentes/entregables/criterios, trazabilidad C1–C14, riesgos E1–E10+R1–R16+R-F25 |
| 4 | [`../SPEC.md`](../SPEC.md) | ✅ | Especificación técnica: hardware, pinout corregido, firmware ESPectre, contrato MQTT (+image F25), esquema SQLite (+auth/visión F25), dashboard (+login/búsqueda), WSL §15, comandos exactos |
| 5 | [`../AGENTS.md`](../AGENTS.md) | ✅ | Convenciones y reglas duras para todo agente (edge-first, privacidad, python, IDF 5.5.6, secretos, GPIO, seguridad) |

## Evidencia y fuentes

| Documento | Estado | Contenido |
|---|---|---|
| [`fuentes.md`](fuentes.md) | ✅ | Todas las URLs consultadas (2026-11-12) con el claim que sostiene cada una y el verificador (planificador/analista). 0 llamadas pagadas |
| `iot2/.tmp/f23_research_tendencias.md` | ✅ (gitignored) | Informe verbatim del analista de tendencias + addendum de verificación primaria |
| `iot2/.tmp/f23_research_viabilidad.md` | ✅ (gitignored) | Informe verbatim del analista de viabilidad (GPIO corregido, veredictos A–E) |

## Firmware propio

| Documento | Estado | Contenido |
|---|---|---|
| [`../firmware/parche_aulasense/README.md`](../firmware/parche_aulasense/README.md) | ✅ | Spec del módulo `app_feedback` (buzzer/WS2812/die temp/BLE opcional), instrucciones de aplicación al vendor, guardas GPIO, nota GPLv3. El código lo crea F3 |

## Entregables de ejecución (los crean las fases)

| Documento | Estado | Fase | Contenido |
|---|---|---|---|
| `guion_demo.md` | ⏳ | F5b | Guion de demo cronometrado con fallbacks (C8/E3). NO creado aún (espera S2/S3 de F4 para el ensayo completo) |
| `plantilla_validacion.md` | ⏳→N/A | F4 | Plantilla de registro manual: **no se creó como archivo** — el registro de la sesión 1 se publicó directo en `validacion_sesion1.csv` |
| `validacion_sesion1.csv` | ✅ | F4 | Intervalos motion/id de la sesión F4S1B con timestamps (C10); sesion2/sesion3 ⏳ (mañana) |
| `INFORME.docx` / `PRESENTACION.pptx` | ✅ (docs/entrega/) | F6a | Versión inicial publicada en `docs/entrega/`; la ACTUALIZACIÓN F6a está **en vuelo** (herramienta `tools/generar_informe.py` en curso — no tocar desde esta auditoría) |
| `../analysis/informe_eda.md` | ✅ | F5a | EDA + modelo predictivo generados por `eda.py`/`modelo_ocupacion.py` v f5a-2.0 (0 números a mano); métricas con tamaño muestral (C5) |
| `../dataset/sesion_1/registro_F4S1*.jsonl` | ✅ | F4 | Dataset propio publicado de la sesión 1 con ground truth pasivo corroborado (commit e3ba4b5) |
| `../analysis/dataset/consolidado.csv` | ✅ | F24-b1/f2 | Dataset consolidado S1 **en disco y comiteado** (462 filas, 100 % `origen=real`, commit 7dbbb9a; verificado en la auditoría externa 2026-11-15). Regenerable con `analysis/export_consolida.py` desde la BD canónica WSL |
| Figuras EDA/modelo (11, ≥6) | ✅ | F5a | **En `docs/analisis/graficos/*.png`** (desviación anotada de la convención vieja `assets/*.png` — PLAN §12.8) |

## Entregables F25 (expansión 2026-11-14, orden del dueño)

| Documento | Estado | Fase | Contenido |
|---|---|---|---|
| `../assets/diagrama_tecnico.*` | ✅ | F25-f1 | Diagrama técnico C4 en 4 vistas: `diagrama_tecnico.dsl/.svg/.png` (contenedores + flujo ESP32→broker→WSL→dashboard), `diagrama_tecnico_contexto.svg/.png`, `diagrama_tecnico_componentes_nodo.svg/.png`, `diagrama_tecnico_componentes_dashboard.svg/.png` (skill graphic-arq-software; evidencia `.tmp/f25f1_diagrama.md`) |
| `../assets/poster_funcionamiento.png` | ✅ | F25-f2 | Póster visual del concepto CSI (ondas/persona/chip/dashboard), skill qwen-imagen (@6-medios) |
| `guion_video.md` | ✅ | F25-f3 | Guion del video explicativo: DriveGuard→fallo caras→CSI→cómo funciona→construcción→finalidad |
| `video_subtitulos.srt` | ✅ | F25-f3/f5 | Subtítulos del video (burn-in en el ensamblaje; re-timado real en `.tmp/video_final.srt`) |
| `video_aulasense.mp4` | ✅ | F25-f5 | Video explicativo completo (H.264+AAC 480P, 65,96 s verificado por ffprobe 2026-11-15; escenas HappyHorse 480P + narración TTS longanlingxin + overlays drawtext + subtítulos burn-in; ensamblaje FFmpeg local, coste extra $0) |
| `../server/deploy/README_wsl.md` | ✅ | F25-b1 (+F25-wsl/F25f) | Despliegue WSL Debian: venv, systemd, portproxy loopback, cutover, fallback Windows; + distro dedicada `debian-aulasense` (:8100, publicación VPS `:10062` por túnel CLI local) y estado actual del receptor `:8001` con caveat de ruteo LAN (matriz final F25-b1 ronda 2 = CUMPLE, `.tmp/f25b1_wsl.md` §13) |
| `../claves/.env.example` | ✅ | andamiaje F25 | Nombres de variables de auth/IA (valores vacíos; la password la genera el código en F25-e) |
| `consentimiento_aula.md` | ⏳ | F24-f→F25-g | Consentimiento ACTUALIZADO: las imágenes transitan el broker autenticado (retain=false) |
| `politica_retencion_imagenes.md` | ⏳ | F24-f→F25-g | Retención 30 d purgable (imagen_purgada conserva fila+descripción; dataset intacto) |
| `INFORME.docx` / `PRESENTACION.pptx` | 🔄 (inicial ✅ en `docs/entrega/`) | F6a (actualizado F25) | Versión inicial publicada; la regeneración con TODO (WSL, MQTT-imágenes, visión+búsqueda, login, diagramas, video, costes Credits reales) está **en vuelo** — ver fila F6a arriba |

> **Nota QC honesta del video (F25-f5):** en la escena 6 la cuadrícula de miniaturas muestra un
> **rostro sintético generado por la IA** (no es persona real); las pantallas de las escenas 5 y 7
> contienen pseudo-texto ilegible (no se requiere legibilidad — todo el texto real va como overlay
> FFmpeg). El video dice "detecta movimiento", nunca "presencia", y no cita métricas del vendor.

## Cómo ver el sistema (verificado 2026-11-15; URLs re-sondeadas en la auditoría documental de esta tarde)

Runtime primario del dataset/despliegue actual: **distro WSL `debian-aulasense`** (dashboard interno
**:8100**, publicado en el VPS en `:10062`); el Debian 13 original (systemd
`aulasense-ingesta`/`aulasense-dashboard`, :8000) sigue vivo como runtime secundario
(`aulasense-vision` instalada y disabled por diseño — SPEC §15, `../server/deploy/README_wsl.md`).
URLs vigentes (orden de preferencia):

| URL | Dónde | Verificación |
|---|---|---|
| `http://167.114.169.134:10062` | **VÍA PÚBLICA PRIMARIA** — publicación VPS del dashboard de `debian-aulasense` (puerto interno 8100) vía túnel SSH auto-start de `wsl-port` **CLI local** (túnel `pub-aulasense-8100`). No requiere token `wsl-port` ni admin de Windows | `/login` → **200** y `/api/eventos` → **401** sin cookie (re-verificado desde fuera el 2026-11-15 por esta auditoría); Origin público pasa |
| `http://172.26.159.208:8100` | dashboard `debian-aulasense` en la IP NAT de la VM WSL, desde el navegador Windows | `/api/eventos` → **401** (vivo, 2026-11-15). La IP WSL **cambia tras reboot**: refrescar `AULASENSE_ORIGINS_EXTRA` de la unit (receta README_wsl §P7/§P8) |
| `http://172.26.159.208:8000` | Debian 13 original (systemd) — vía fallback NAT de F25-b1 | `/api/eventos` → **401** (vivo, 2026-11-15). Misma nota de IP cambiante: `powershell tools\wsl_aulasense.ps1 status` |
| `http://127.0.0.1:8000/login` | loopback **dentro de la VM WSL** | `/login` → **200** (evidencia `.tmp/f25b1_wsl.md` §6). **No vigente en Windows**: el stack fallback está parado y el portproxy sigue pendiente de admin (hoy ese loopback NO responde — sondeado 2026-11-15) |

Acceso: requiere **login** (usuario `admin`; la password la genera el código y vive en
`claves/.env`, gitignored — NUNCA se imprime en documentación).

**Pendientes NO vigentes (honesto — no usar como vía activa):**
1. **Portproxy `netsh` `127.0.0.1:8000`→WSL** en Windows: requiere **admin** y el stack Windows
   parado (cutover F25-b2). La publicación pública **no lo necesita** (vía `:10062` arriba).
2. **Receptor N2 `:8001` alcanzable desde la ESP32**: re-habilitado el 2026-11-15 DENTRO de
   `debian-aulasense` (bind `172.26.159.208:8001`, fire-test 201 — README_wsl §Receptor), pero la
   IP NAT no es enrutable desde la LAN 192.168.40.x del aula: hace falta portproxy admin sobre la
   IP LAN + firewall, o repunter el `CONFIG_AULASENSE_UPLOAD_HOST` del nodo a la IP LAN del host
   en el siguiente ciclo de build (decisión del dueño). Con MQTT primario (D-F25-1) no es
   bloqueante.
3. **Portal AP `/cfg` (F24-d) — criterios E2–E5 en vivo**: binario flasheado y verificado
   bit-a-bit (readback digest `effa314f`), pero el disparo real (3 fallos STA → AP → token en
   serial → POST) queda **pendiente-ventana-natural**: se ejercitará con la primera caída real del
   router o en la demo (procedimiento humano: `firmware/parche_aulasense/README.md` §13).
4. **Sesiones F4 S2/S3**: mañana (autorizado); aportan tramos idle para FPR/precision del análisis
   y la segunda/thirda pasada de evidencia fotográfica post-Fix F.
5. *(Histórico: la publicación VPS figuraba como pendiente y **ya está vigente** — ver tabla.)*

> **Nota QC honesta de las imágenes (visión modo B, cerrada 2026-11-15):** de 55 fotos, las 53
> reales auditadas: 32 válidas / 23 corruptas **de origen en la placa** (byte-loss GDMA pre-Fix F,
> causa raíz en `iot2/.tmp/jpegqc_causa_raiz.md`). Estado final visión: **34 hechas · 20 error · 1
> omitido** — las 20 irreparables quedan en `'error'` (el CHECK de `vision_estado` NO tiene estado
> terminal de QC; la validación local de dos etapas las salta SIN coste de Credits). Fix F (xclk
> 10 MHz + descarte estructural antes de publicar) flasheado; Fix S (descarte estructural en el
> decoder) desplegado. Incidente documentado: dos procesos de visión solapados quemaron 2.972
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