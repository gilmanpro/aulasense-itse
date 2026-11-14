# guion_video.md — Guion del video explicativo de AulaSense (contrato F25-f3 → F25-f4/f5)

> **Fase F25-f3** (checklist v25). Este archivo es el CONTRATO de producción:
> - `@6-medios` genera las escenas con la skill `qwen-video` (modelo `happyhorse-1.1-t2v`, **480P**, clips de 5–8 s — D-F25-12: los clips IA cortos son lo fiable) y la narración TTS con `qwen-audio` (`qwen-audio-3.0-tts-plus`, voces verificadas en español: `longanlingxin` F o `longanlufeng` M).
> - `@5-dev` ensambla con FFmpeg (skill `graphic-video`): concat de escenas + audio TTS + **overlays de texto en pantalla (drawtext)** + subtítulos SRT burn-in → `docs/video_aulasense.mp4` (H.264+AAC).
> - **El texto NUNCA lo renderiza el modelo de video**: todo texto en pantalla va como overlay FFmpeg (los modelos t2v deforman tipografía). Los prompts visuales de abajo piden explícitamente "no text".
> - Duración total objetivo: **60–90 s**. Base de escenas: 8 clips = **61 s**; con tarjeta de título (2 s) y tarjeta final (2 s) y los freeze-extend de narración queda ~65–70 s.

## Reglas de honestidad del guion (heredan checklist F25-f3 y reglas 10/11 AGENTS)

1. El video dice **"detecta movimiento"**, nunca "presencia", "cuenta personas" o "sala vacía" (S10; el propio cierre lo declara: "Detecta movimiento, no presencia").
2. No se citan métricas del vendor (F1 98,1–99,3 %) como propias. El video no da cifras de accuracy; solo hechos de arquitectura (529 parámetros, 250 ms, MQTT, CSI).
3. El fallo de la cámara (escena 2) se cuenta **honesto y sin nombres**: mala luz, ángulo/orientación del montaje y encuadre — nunca hubo un rostro real detectado en vivo. Sin señalar personas ni productos concretos.
4. Datos reales: el dataset es propio y real (`origen=real`); el video no muestra ni promete nada "SIMULADO".
5. Privacidad: se menciona **acotada local-only** (imágenes solo al servidor local, login, retención), no "cero imágenes" (C9 cumple-parcial, D-F24-1).

## Ficha técnica de producción

| Parámetro | Valor |
|---|---|
| Motor de escenas | qwen-video · `happyhorse-1.1-t2v` (allowlist Token Plan) |
| Resolución | 480P (mínimo que cumple; D-F25-12) |
| Formato | 16:9, mp4 por escena |
| Nº escenas | **8** (dentro del rango 6–8) |
| Duración por escena | 5–8 s (ver tabla; total base 61 s) |
| Narración | TTS qwen-audio 1–2 frases por escena, español |
| Texto en pantalla | Overlay FFmpeg, español, **máx. 6 palabras** por escena |
| Subtítulos | `docs/video_subtitulos.srt` (base incluida abajo, mismo texto que la narración) |
| Salida final | `docs/video_aulasense.mp4`, H.264+AAC, 60–90 s, subtítulos burn-in |
| Intermedios | `assets/video/escena_01..08.mp4`, `assets/video/narracion_01..08.mp3` |

### Tabla de tiempos (ventanas SRT acumulativas)

| Escena | Duración (s) | Inicio | Fin |
|---|---|---|---|
| E1 El problema | 8 | 0:00 | 0:08 |
| E2 Por qué falló la cámara | 7 | 0:08 | 0:15 |
| E3 La idea: WiFi como sensor | 8 | 0:15 | 0:23 |
| E4 La IA en el chip | 7 | 0:23 | 0:30 |
| E5 El pipeline completo | 8 | 0:30 | 0:38 |
| E6 Fotos-evidencia + IA + login | 7 | 0:38 | 0:45 |
| E7 Datos y análisis | 8 | 0:45 | 0:53 |
| E8 Finalidad y cierre | 8 | 0:53 | 1:01 |

> Nota de sincronía: si la voz TTS de una escena excede 0,5–1,5 s el largo del clip, 5-dev extiende el último frame (freeze) de esa escena; **no** se acorta la narración. Estilo visual consistente: los prompts comparten sufijo (`cinematic documentary style… no text`) — no quitarlo al editar.

---

## ESCENA 1 — El problema (origen DriveGuard: somnolencia al volante)

- **Duración:** 8 s
- **Descripción visual (ES, para el prompt t2v):** Plano detalle cinematográfico de un conductor de mediana edad en una carretera de noche, párpados pesados y cabeza que cabecea a punto de dormirse; luces azules del tablero iluminando su rostro; por el parabrisas, faros borrosos que se acercan; atmósfera tensa y oscura, desenfoque de fondo, estilo documental.
- **Prompt t2v (EN — copiar y pegar tal cual):**

  ```
  Cinematic close-up of a tired middle-aged male driver on a highway at night, heavy eyelids, head nodding off, dashboard glowing blue on his face, blurred headlights approaching through the windshield, moody low-key lighting, shallow depth of field, subtle slow push-in, documentary film style, muted dark palette, no text, no captions, no logo, no watermark, 16:9 widescreen
  ```

- **Texto en pantalla (ES, overlay FFmpeg, máx. 6 palabras):** `Somnolencia al volante`
- **Narración (TTS, español, ~6,5 s):** "Conducir con sueño causa miles de accidentes. Empezamos intentando detectarlo con una cámara."

## ESCENA 2 — Por qué la cámara falló (honesto, sin nombres)

- **Duración:** 7 s
- **Descripción visual (ES):** Una pequeña cámara de auto montada chueca sobre el tablero de un vehículo en un garaje mal iluminado, apuntando al techo en vez del conductor; asiento del conductor vacío y desenfocado en primer plano; luz parpadeante tenue; el lente diminuto, fuera de foco, inútil; tono realista y melancólico. (No aparece ninguna persona reconocible: el encuadre NO llega — sin nombres de producto ni de personas.)
- **Prompt t2v (EN):**

  ```
  A tiny dashcam module mounted crookedly on a car dashboard inside a dim garage, lens pointing up at the ceiling instead of the driver seat, empty out-of-focus driver seat in the foreground, flickering weak light, realistic cinematic mood, desaturated colors, slow static shot, no people visible, no text, no captions, no logo, no watermark, 16:9 widescreen
  ```

- **Texto en pantalla (ES):** `La cámara no veía`
- **Narración (TTS, ~6 s):** "Pero la cámara nunca detectó un rostro real: mala luz, ángulo y encuadre."

## ESCENA 3 — La idea nueva: el WiFi como sensor (ondas, CSI)

- **Duración:** 8 s
- **Descripción visual (ES):** Aula moderna al anochecer; un router WiFi en una repisa emite ondas concéntricas translúcidas azules que llenan la sala; la silueta de un estudiante camina a través de las ondas y estas se distorsionan y rizan a su paso; visualización de red elegante, paleta fría, movimiento de cámara lento.
- **Prompt t2v (EN):**

  ```
  A dark modern classroom at dusk, a wifi router on a shelf emitting translucent concentric blue waves that fill the room, the silhouette of a student walking through the waves, ripples and distortions spreading where the body crosses them, elegant network visualisation, cool blue palette, slow camera pan, cinematic documentary style, no text, no captions, no logo, no watermark, 16:9 widescreen
  ```

- **Texto en pantalla (ES):** `El WiFi como sensor`
- **Narración (TTS, ~8 s):** "Invertimos el enfoque: el movimiento altera las ondas Wi-Fi. El CSI convierte al router en un sensor invisible."

## ESCENA 4 — La IA en el chip (MLP 529 parámetros, decisión cada 250 ms)

- **Duración:** 7 s
- **Descripción visual (ES):** Macro extremo de un microcontrolador pequeño sobre una placa de circuito verde; delgadas líneas de una red neuronal luminosa animándose sobre la superficie del silicio; diminutos pulsos de luz rítmicos que parpadean varias veces por segundo; fondo oscuro, detalle alto, estética de documental tecnológico.
- **Prompt t2v (EN):**

  ```
  Extreme macro shot of a small microcontroller chip on a green circuit board, thin glowing neural network lines animating across the silicon surface, tiny pulses of light flashing rhythmically several times per second, dark background, high detail, cinematic tech documentary style, warm board tones against cool glow, no text, no captions, no logo, no watermark, 16:9 widescreen
  ```

- **Texto en pantalla (ES):** `IA dentro del chip`
- **Narración (TTS, ~7 s):** "Una red neuronal de 529 parámetros decide en el chip cada 250 milisegundos si hay movimiento."

## ESCENA 5 — El pipeline completo (nodo → broker → WSL → dashboard)

- **Duración:** 8 s
- **Descripción visual (ES):** Trayectoria de datos luminosa: un pequeño dispositivo sensor sobre un escritorio de madera emite un arco de luz hacia un router; la luz viaja por hilos de fibra hasta un servidor torre con luces titilantes en una sala oscura; la cámara se desliza hasta un monitor que muestra un tablero con gráficas de líneas animadas actualizándose en vivo; paleta cian-azul, movimiento dolly fluido. (Las pantallas NO muestran texto legible — eso es del overlay.)
- **Prompt t2v (EN):**

  ```
  Glowing data path visualisation: a small sensor device on a wooden desk emits an arc of light toward a wifi router, the light travels through glowing fibers to a server tower with blinking leds in a dark room, smooth camera dolly continues to a computer monitor showing a live dashboard with animated line charts, dark background, cyan blue palette, cinematic documentary style, no readable text on screens, no text, no captions, no logo, no watermark, 16:9 widescreen
  ```

- **Texto en pantalla (ES):** `Del nodo al tablero`
- **Narración (TTS, ~7 s):** "Cada evento viaja por MQTT al servidor, y se muestra en un tablero en vivo."

## ESCENA 6 — Fotos-evidencia con descripción IA y login seguro

- **Duración:** 7 s
- **Descripción visual (ES):** Vista por encima del hombro de una persona en un escritorio con un monitor que muestra una cuadrícula de miniaturas de fotos desenfocadas; una luz de escaneo suave recorre una miniatura y esta se amplía; la pantalla cambia a un formulario simple de inicio de sesión donde un candado luminoso brilla junto a puntos de contraseña; ambiente de privacidad y seguridad, acentos cian, sin texto legible en pantalla.
- **Prompt t2v (EN):**

  ```
  Over-the-shoulder shot of a person at a dim desk facing a computer screen with a blurred grid of photo thumbnails, a soft scanning light sweeps across one thumbnail and it gently enlarges, the screen shifts to a minimal login form with a glowing padlock icon and hidden password dots, privacy and security mood, cyan accents, cinematic documentary style, no readable text, no text, no captions, no logo, no watermark, 16:9 widescreen
  ```

- **Texto en pantalla (ES):** `Evidencia con IA y login`
- **Narración (TTS, ~6 s):** "Como evidencia, una foto que la IA describe, todo tras un login local."

## ESCENA 7 — Datos y análisis (dataset real, modelo predictivo)

- **Duración:** 8 s
- **Descripción visual (ES):** Visualización de análisis de datos: gráficas de barras holográficas flotantes y una cuadrícula de mapa de calor luminosa que gira lentamente sobre un escritorio universitario con laptop; luz dorada de ventana al atardecer; estilo limpio y futurista-documenal, paleta azul-naranja. Sin números ni texto legibles.
- **Prompt t2v (EN):**

  ```
  Data analysis visualisation: floating holographic bar charts and a glowing color heatmap grid rotating slowly above a university desk with an open laptop, golden hour window light, abstract data glow rising from the desk, clean futuristic documentary style, blue and orange palette, slow orbiting camera, no numbers, no text, no captions, no logo, no watermark, 16:9 widescreen
  ```

- **Texto en pantalla (ES):** `Datos propios, modelo real`
- **Narración (TTS, ~7 s):** "Miles de lecturas reales alimentan un modelo predictivo de la actividad del aula."

## ESCENA 8 — Finalidad y cierre (proyecto ITSE, privacidad acotada, $0)

- **Duración:** 8 s
- **Descripción visual (ES):** Un pequeño dispositivo electrónico reposa sobre la repisa de un aula moderna iluminada por el sol de la mañana; rayos de luz cruzan los escritorios vacíos; la cámara retrocede lentamente atravesando el aula; tono esperanzador, paleta cálida, profundidad de campo suave, sin personas.
- **Prompt t2v (EN):**

  ```
  A small electronic sensor device resting on a shelf in a bright modern classroom in the morning, sun rays crossing empty desks, camera slowly pulling back through the room, hopeful documentary tone, warm palette, shallow depth of field, no people, no text, no captions, no logo, no watermark, 16:9 widescreen
  ```

- **Texto en pantalla (ES):** `ITSE · Privacidad acotada · $0`
- **Narración (TTS, ~6 s):** "Detecta movimiento, no presencia. AulaSense: el proyecto ITSE de costo cero."

---

## SRT base (también escrito en `docs/video_subtitulos.srt`)

Texto idéntico a la narración; 5-dev hace burn-in y puede re-ajustar centésimas tras medir los MP3 reales del TTS.

```srt
1
00:00:00,000 --> 00:00:04,000
Conducir con sueño causa
miles de accidentes.

2
00:00:04,000 --> 00:00:08,000
Empezamos intentando detectarlo
con una cámara.

3
00:00:08,000 --> 00:00:11,600
Pero la cámara nunca detectó
un rostro real:

4
00:00:11,600 --> 00:00:15,000
mala luz, ángulo y encuadre.

5
00:00:15,000 --> 00:00:19,000
Invertimos el enfoque: el movimiento
altera las ondas Wi-Fi.

6
00:00:19,000 --> 00:00:23,000
El CSI convierte al router
en un sensor invisible.

7
00:00:23,000 --> 00:00:26,500
Una red neuronal de 529 parámetros

8
00:00:26,500 --> 00:00:30,000
decide en el chip cada 250 ms.

9
00:00:30,000 --> 00:00:34,000
Cada evento viaja por MQTT al servidor,

10
00:00:34,000 --> 00:00:38,000
y se muestra en un tablero en vivo.

11
00:00:38,000 --> 00:00:41,500
Como evidencia, una foto
que la IA describe,

12
00:00:41,500 --> 00:00:45,000
todo tras un login local.

13
00:00:45,000 --> 00:00:49,000
Miles de lecturas reales alimentan

14
00:00:49,000 --> 00:00:53,000
un modelo predictivo del aula.

15
00:00:53,000 --> 00:00:57,000
Detecta movimiento, no presencia.

16
00:00:57,000 --> 00:01:01,000
AulaSense: el proyecto ITSE de costo cero.
```

## Nota de economía (regla 16 AGENTS — presupuesto obligatorio, cero promesas sin medir)

- **Estimado:** 8 clips de 5–8 s en 480P ≈ **$3–4 equivalentes en Credits** (referencia de la skill `qwen-video`: ~$0,07/s; base 61 s → $4,3 como techo de estimación). La narración TTS (`qwen-audio`) es marginal frente al video.
- **Paso 0 OBLIGATORIO ANTES del lote** (D-F25-6/D-F25-12, checklist F25-f4): generar **1 clip de prueba de 3–5 s en 480P** (se recomienda usar el prompt de la ESCENA 3) y **leer el consumo real** en Model usage; registrar `usage`/coste real por segundo en `.tmp/f25f4_video.md`. Extrapolar el lote a partir de esa medición, no del estimado.
- **Si el consumo real es anómalo → PARAR y reportar** al orquestador; NO improvisar ni subir resolución. Máximo 8 clips (los recortes o re-generaciones cuentan contra ese tope; priorizar repetir solo la escena peor).
- **Degradación honesta permitida:** si TTS qwen-audio no cubre (key/ToS), el video se entrega **solo con subtítulos y overlays** (D-F25-12.3). Si alguna escena sale mal tras 2 intentos, se reemplaza por un still + zoompan de FFmpeg (coste $0 adicional) y se documenta.

## Contrato de ensamblaje (para 5-dev, F25-f5)

1. Concat de `assets/video/escena_01..08.mp4` en orden, durations de la tabla (± freeze-extend según TTS real).
2. Audio: narraciones TTS por escena (español, una sola voz en todo el video — elegir `longanlingxin` y registrarlo en `.tmp/f25f5_ensamblaje.md`); mezcla final ~-16 LUFS; sin música o música muy suave por debajo de la voz.
3. Overlays drawtext (texto en pantalla de cada escena, español, máx. 6 palabras, tipografía sans-serif bold, zona segura inferior-media, aparecer 1 s después del inicio de la escena durante 4 s).
4. Subtítulos: burn-in de `docs/video_subtitulos.srt` (re-timed a los MP3 reales).
5. Salida `docs/video_aulasense.mp4`: H.264 + AAC, 16:9; se permite upscaler a 1080p de las escenas 480P; duración total 60–90 s; verificar con `ffprobe` y apertura local.
6. Intermedios en `assets/video/` (gitignored si pesan; el mp4 final se trackea si <50 MB).

## Trazabilidad narrativa (fuente: PLAN §1–2/§11/§12, SPEC §15, bitácora iot2\.tmp\bitacora.md)

| Escena | Hecho real del proyecto que representa |
|---|---|
| E1–E2 | DriveGuard (dashcam-ia): el detector de caras EN VIVO nunca dio cara=1; causa raíz documentada = apuntamiento físico/orientación/luz/encuadre (bitácora 2026-11-12, snap_encuadre: "la placa mira al TECHO"; cara=0 los 300 s) → pivot |
| E3 | Sensing Wi-Fi CSI (D1/D12 PLAN; ESPectre; gate F1c prueba de vida **10/10 ciclos** caminar/idle — el video alude al fenómeno, no promete métricas) |
| E4 | Detector neuronal ESPectre en-chip: MLP 8→24→12→1, **529 parámetros**, decisión cada **250 ms**, edge-first C1 (PROPUESTA/PLAN §5 capa 1) |
| E5 | Pipeline: nodo → MQTT broker.gilman.pro:14769 (topic_prefix aulasense, auth) → ingesta WSL Debian (systemd, SQLite ext4) → dashboard FastAPI/WS 127.0.0.1 + portproxy (SPEC §15; F3 latencia real medida 14–361 ms) |
| E6 | F24 fotos-evidencia (1 JPEG QVGA al onset, cooldown 15 s) + F25 tránsito por MQTT (b64, retain=false) + worker visión Qwen (descripciones) + login seguro (Argon2id, lockout, loopback) |
| E7 | F4 dataset propio real (≥3 sesiones, verdad terreno manual) + F5a EDA pandas y modelo sklearn de actividad por franja (heatmap por hora) |
| E8 | Finalidad docente ITSE (condiciones C1–C14), privacidad ACOTADA local-only (C9 cumple-parcial, consentimiento, retención purgable), costo total $0 (placa + router existentes) |
