# Guion de demostración — AulaSense (post-F26, ~10 minutos)

Guion en vivo para el profesor/demostrador. Reemplaza la referencia muerta de SPEC §11. Todo lo que
se dice aqui esta **demostrado en el terreno** (build viva f26.6, operacion outbound-only). Donde
hay un fallback, esta escrito abajo del paso.

Regla de oro de honestidad (no se negocia en pantalla): el sistema **detecta MOVIMIENTO**, no
"presencia"; `idle` no significa sala vacia; y las metricas del vendor (98,1–99,3 %) **nunca** se
citan como propias. Las cifras que se muestran son las medidas con tamano de muestra.

## Índice de tiempos

| Bloque | Contenido | Tiempo |
|---|---|---|
| 0 | Pre-checks (antes de que entre la audiencia) | 2 min antes |
| 1 | Dashboard publico + login | 0:30 |
| 2 | Deteccion de motion en vivo (CSI en-chip) | 2:00 |
| 3 | Galeria: foto + descripcion IA + decision Clef | 2:00 |
| 4 | Busqueda por texto | 1:00 |
| 5 | Panel /salud (semáforos) | 1:30 |
| 6 | OTA `check` en vivo (SOLO check) | 1:30 |
| 7 | Portal in-situ (opcional, solo si la demo es EN el aula) | 1:00 |
| 8 | Cierre con cifras honestas | 1:00 |

---

## Paso 0 — Pre-checks (2 minutos ANTES de empezar)

```powershell
powershell tools\wsl_aulasense.ps1 status    # 3 units systemd activas + URL del dashboard
powershell tools\wsl_aulasense.ps1 salud     # /api/salud: sondas + tuneles wsl-port
```

Verificar en la salida de `salud`:

- Tuneles **`state=running`**: `pub-aulasense-8100` (dashboard publico `:10062`) y
  `pub-aulasense-ota` (estatico OTA `:10064`).
- **Telemetria fresca**: edad < 5 min (si la placa lleva >10 min muda, revisar energia/red antes
  de la demo — ver NOTES_operacion §5).
- `imagen_estado` con `camara_ok=true` y `captura_habilitada=true` (si esta en `gate_off`, la
  galeria no producira fotos nuevas: avisar antes, no durante).

Abrir el navegador en `http://167.114.169.134:10062` YA logueado, con la pestaña de eventos y la
de `/salud` listas. Tener a mano este doc y `NOTES_operacion.md`.

## Paso 1 — Dashboard publico (0:30)

URL: `http://167.114.169.134:10062` → login `admin`.

> **Que decir**: "El dashboard corre en un servidor local (WSL Debian detras de mi portatil); lo que
> ven es un tunel SSH a un VPS que solo reenvia puertos. Las credenciales de acceso se entregaron
> por el canal privado del curso — nunca estan en el repositorio."

Nunca escribir la password en pantalla compartida ni leerla en voz alta.

## Paso 2 — Detección de motion en vivo (2:00)

Caminar por la zona cubierta por la placa. Que debe pasar:

- Evento `motion` en el panel **en < 2 s**, con refrescos por WebSocket (tasa CSI ~99 pps,
  temperatura del die — ojo: proxy de carga de computo, NO temperatura ambiente).
- Si la placa esta energizada a la vista: **buzzer (GPIO14) + LED WS2812 (GPIO48)** feedback local.

**Que decir**: "El detector es una red neuronal de 529 parametros que corre ENTERA dentro del
ESP32-S3 sobre las ondas Wi-Fi (CSI) y decide cada 250 ms. El CSI crudo nunca sale del chip: por
MQTT viajan eventos ya decididos. Cero hardware extra: la placa y el router que ya estaban. Y es
honesto con lo que NO hace: detecta movimiento, no presencia estacionaria — una sala en `idle`
puede estar llena de gente quieta."

Fallback: sin movimiento en la sala → mostrar los eventos del historico del dia y explicarlo sin
fingir ("en este momento la sala esta estable; esto es lo que capturemos hace una hora"). La
demo NO depende de que alguien camine.

## Paso 3 — Galería: evidencia + IA + decisión Clef (2:00)

Abrir la galeria: foto QVGA de evidencia por onset (cooldown 15 s), con su descripcion de IA (Qwen
vision) y, encima, la **decision de aula de Clef**: etiqueta de la taxonomia de 6
(`SIN_PERSONAS_VISIBLES`, `PERSONAS_SENTADAS`, `CIRCULACION`, `PRESENTACION_AL_FRENTE`,
`AGLOMERACION_OTRA`, `IMAGEN_DEGRADADA`), confianza, actividad y, cuando el cubrimiento es bajo,
el badge **REVISIÓN_HUMANA**.

**Que decir (honesto, es parte de la nota)**: "En nuestras 81 fotos reales, Clef decidio 60: el 75 %
acabaron en `IMAGEN_DEGRADADA` y 44 de 60 con badge de revision humana. El hallazgo es que
*decodificable no es legible*: las fotos abren bien (71,25 % de integridad JPEG) pero la calidad
media de las degradadas es 0,067 contra 0,542 de las legibles. El cuello no es la IA: es el encuadre
y la calidad QVGA (jpeg_quality=12) que elegimos por presupuesto de memoria. Cero
`PERSONAS_SENTADAS`: el aula nunca quedo bien encuadrada en las sesiones. La mejora esta diseñada y
pendiente post-entrega: subir la calidad por OTA."

La cascada Clef: `clef-flash` (solo texto de la descripcion) y escala a `clef` (con imagen) si la
confianza es < 0,6. Topes de Credits: 120/dia y 20/hora, con Paso 0 de medicion antes de todo lote.

## Paso 4 — Búsqueda por texto (1:00)

En la galeria, buscar en español, p. ej.: `alguien camina frente al pizarron`. Explicar: filtra por
las descripciones que la IA genero de cada foto (busqueda semantica sobre texto ya almacenado, no
sobre las imagenes).

## Paso 5 — Panel /salud (1:30)

Semáforos: ingesta, MQTT, frescura de telemetria, **heap INTERNO vs PSRAM**, colas de vision y Clef,
disco, tuneles y OTA.

Puntos a narrar:

- "El heap que importa es el INTERNO (~103–106 KB libres con la camara al aire); los ~8,2 MB de
  `free_memory_kb` son PSRAM y no sirven para los gates. Los umbrales: captura 95/24 KB y OTA 96/48
  KB, ambos medidos."
- "Si disparo una OTA ahora, veré un **agujero de telemetria de ~30 s ESPERADO** (descarga + reboot).
  El panel lo sabe porque el retain OTA esta activo y pone `ota_en_curso=true`, suprimiendo la falsa
  alarma."

## Paso 6 — OTA `check` en vivo (1:30) — SOLO check, NUNCA apply

Publicar `check` con el script de la NOTES_operacion §1.3 (o el helper del arbol). En ~1 s el evento
`OTA_CHECK` aparece en el historico con `hay_actualizacion` — sin tocar la placa.

**Que decir** (narrar lo que haria un `apply`, ya demostrado en banco y por aire): "Un `apply` pasa
un gate de heap post-quiesce de 96/48 KB, pausa captura y telemetria, descarga el binario por el
mismo tunel por el que la placa SOLO sale (~34 s para 1,4 MB), verifica SHA-256 y tamano, cambia el
slot de arranque, reinicia y se auto-valida con 90 s de self-check mas 120 s de gracia: total ~163 s
de punta a punta. Y si la imagen fuera mala — lo probamos con un binario `f26.7-test` que fallaba el
self-check a proposito — el bootloader revierte SOLO: 225 s y la placa resucita sola, sin humano.
Con un hash alterado ni siquiera reinicia: `OTA_ERROR` etapa sha256. Esto ya paso por aire dos
veces: f26.4 → f26.5 → f26.6, la build que estan viendo."

Nunca `apply` en una demo: el riesgo no es el brick (esta cubierto) sino perder la placa de vista
4 minutos en plena exposicion.

## Paso 7 — Portal in-situ (OPCIONAL; solo si la demo es EN el aula)

Mostrar el re-provisionamiento real: AP **`AULASENSE-CFG`** (WPA2, pass canonica del curso — esta en
`NOTES_operacion.md` §4 y en el `.env`) → asociarse → `http://192.168.4.1/cfg` (sin token) →
formulario con SSID/pass del sitio. Narrar: "solo sube si la WiFi falla 3 ciclos; es una capacidad
LOCAL: hay que estar fisicamente junto a la placa. Una placa a la vez, porque el SSID es fijo. POST
de vuelta en menos de 5 s → 429; la placa nunca loguea la clave. Cuando reconecta, los eventos
`CFG_GUARDADA` y `STA_RECONECTADO` aparecen en el dashboard: confirmacion remota sin volver al
sitio." Recovery remoto no hay canal: es el rollback de la OTA.

## Paso 8 — Cierre con cifras honestas (1:00)

Decir exactamente esto (todo medido, con tamano de muestra):

- "**1.198 filas 100 % reales** en el consolidado (4 dias; regenerado F26), con **498.460
  evaluaciones** del detector (4.428 motion) y tasa estable de 3,5–3,9 evaluaciones/s a traves de 4
  sesiones y varios reflasheos/OTAs."
- "**Recall de motion 0,879** (TP 29 / FN 4, n=35, anclado a la sesion S1 que es la unica con
  ground truth fotografico; en S2B/S3B la camara cayo en el sitio y GT=0 — por eso NO reportamos
  precision: no hay marcas de idle)."
- "El modelo predictivo de analisis (sklearn, n_test=52 con 5 positivos): accuracy 0,904 = **la
  misma que el baseline mayoritario** — lo reportamos como lo que es: un ejercicio docente, sin
  promesas."
- "Costes reales medidos: Clef **$0,0053 por 62 llamadas** API (52.517 tokens); vision 26 llamadas /
  12.180 tokens hoy."
- "Y la arquitectura: la placa esta LEJOS del servidor y solo hace conexiones SALIENTES — el
  servidor jamas la alcanza; la actualizacion remota, el monitoreo y el rollback viven de ese
  principio."

---

## Fallbacks

| Fallo | Plan |
|---|---|
| Tunel publico caido (`:10062`) | Entrar por la IP NAT de WSL `http://<WSL_IP>:8100` (la IP cambia tras cada reboot del PC: salir de `powershell tools\wsl_aulasense.ps1 status` para ver la URL efectiva; si hace falta `refresh-portproxy`) |
| Sin movimiento en la sala | Explicar `idle` con el historico; NUNCA fingir un evento |
| Placa sin energia / muda | Pre-checks del paso 0; en ultimo caso `tools/lee_serial.py COM6` (solo lectura, sin reset). NO `idf.py monitor`: resetea |
| Galeria sin fotos nuevas (`gate_off`) | Mostrar fotos de dias anteriores + explicar el gate 95/24 con la evidencia del heap interno |
| Demo sin Credits frescos (vision/Clef) | No lanzar llamadas nuevas: las descripciones y decisiones **ya persistidas** en la BD se muestran igual |
| Clef caido (OpenRouter) | La galeria funciona sin Clef: la decision es una capa encima de la descripcion |

**Recordatorio final**: detecta movimiento, no presencia. `idle` ≠ sala vacia. Una placa cubre una
zona. Las metricas 98,1–99,3 % son de ESPectre (dataset del vendor), no nuestras.
