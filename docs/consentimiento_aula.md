# Consentimiento de aula — Captura de imágenes de evidencia de movimiento (AulaSense)

**Institución:** ITSE (Perú) · **Materia:** Big Data e Inteligencia Artificial — proyecto IoT2/ITSE 2026
**Documento:** aviso de información y consentimiento informado para la captura de imágenes de
evidencia en aula · **Versión:** 1.0 (2026-11-17)
**Estado:** exigido por `../AGENTS.md` regla 2 ("Consentimiento de aula documentado — obligatorio") y
regla 14; contratos técnicos en `../SPEC.md` §7.1/§7.2 (captura e ingesta de imagen), §9.1-§9.2
(galería tras login) y §16 (estado F26).
**Documento complementario:** [`politica_retencion_imagenes.md`](politica_retencion_imagenes.md)
(plazos, borrado y solicitudes).

> El presente documento describe **exactamente** lo que el sistema hace — ni más ni menos — conforme
> a la regla de honestidad del proyecto (`../AGENTS.md` reglas 2, 10 y 11). Si alguna palabra de este
> aviso no se cumple en el funcionamiento real, el responsable del proyecto debe corregir el sistema
> o este documento.

---

## Índice

1. [Quién realiza la captura y con qué finalidad](#1-qui%C3%A9n-realiza-la-captura-y-con-qu%C3%A9-finalidad)
2. [Qué se captura exactamente](#2-qu%C3%A9-se-captura-exactamente)
3. [Qué NO se captura ni se hace](#3-qu%C3%A9-no-se-captura-ni-se-hace)
4. [Dónde viaja y dónde se guarda la imagen](#4-d%C3%B3nde-viaja-y-d%C3%B3nde-se-guarda-la-imagen)
5. [Procesamiento opcional por IA (capa de descripción y decisión de aula)](#5-procesamiento-opcional-por-ia-capa-de-descripci%C3%B3n-y-decisi%C3%B3n-de-aula)
6. [Quién puede ver las imágenes](#6-qui%C3%A9n-puede-ver-las-im%C3%A1genes)
7. [Derechos y límites de uso del sistema](#7-derechos-y-l%C3%ADmites-de-uso-del-sistema)
8. [Carácter voluntario, alternativas y revocación](#8-car%C3%A1cter-voluntario-alternativas-y-revocaci%C3%B3n)
9. [Conservación y derecho de supresión](#9-conservaci%C3%B3n-y-derecho-de-supresi%C3%B3n)
10. [Firmas](#10-firmas)

---

## 1. Quién realiza la captura y con qué finalidad

El proyecto **AulaSense** es un sensor de **movimiento/ocupación dinámica** construido como trabajo
académico del curso. El detector principal es una red neuronal que corre **dentro del chip
ESP32-S3** y que no necesita cámara. La cámara incorporada en la placa (OV3660) se usa
**únicamente como prueba visual de que un evento detectado ocurrió**: una "foto-testigo" del
instante en que el sistema registró movimiento.

Finalidad exclusiva: **evidencia de evento del proyecto IoT del curso** — control de calidad del
detector (verificar que lo que el sensor clasifica como `motion` coincide con lo que pasaba en el aula),
dataset académico del pipeline ingesta→almacenamiento→análisis (`../SPEC.md` §10, decisión D-F24-7)
y material demostrativo de la entrega al profesor.

## 2. Qué se captura exactamente

| Aspecto | Realidad técnica del sistema | Dónde está definido |
|---|---|---|
| Frecuencia | **1 sola imagen JPEG por evento de movimiento** (en el inicio/onset de `motion`), con **15 segundos de enfriamiento** (cooldown) entre tomas | `../AGENTS.md` regla 2; `../SPEC.md` §7.1 |
| Formato y resolución | JPEG **QVGA (320×240)**, como máximo 64 KB por imagen; la calidad JPEG está fijada baja (`jpeg_quality` en `../firmware/parche_aulasense/app_imagen.c`) | `../SPEC.md` §7.1; regla 14 |
| Disparador | Solo cuando el detector CSI neuronal del chip clasifica el evento como `MOVIMIENTO_INICIO` | `../SPEC.md` §7 (derivación de bordes) |
| Contenido | Una toma fija del aula desde el punto de vista de la placa. **Puede incluir personas presentes en el encuadre**: por eso este aviso y consentimiento existen | `../AGENTS.md` regla 2 |
| Metadatos | Identificador de la placa (`device_id`, un código de 16 caracteres hexadecimales asignado al dispositivo — **no es el nombre de ninguna persona**), marca de tiempo, hash de integridad y tamaño del archivo | `../SPEC.md` §8.1 (tabla `imagenes`) |

## 3. Qué NO se captura ni se hace

- **NO se graba video.** La placa captura fotogramas sueltos de evidencia, un JPEG por evento.
- **NO se captura audio.** La placa del proyecto no tiene micrófono funcional en este sistema
  ("Sin micrófono, sin radar": `../AGENTS.md`, sección *Qué es esto*).
- **NO hay reconocimiento facial.** La detección facial está prohibida como regla dura del proyecto
  (regla 2: "SIN caras-ML: no reintroducir detección facial"). La opción de una IA de visión en el
  chip fue evaluada y **archivada sin implementarse** (F24-e NO LANZADA, `../PLAN.md` §12.8).
- **NO se identifica a ninguna persona.** La IA opcional de descripción trabaja sobre un prompt
  que lo prohíbe expresamente: *"puede haber personas; **no identifiques a nadie**"*
  (`../server/vision/cliente.py:175-180`, `PROMPT_VISION`).
- **NO sale el CSI crudo del chip**: el servidor solo recibe eventos ya decididos
  (movimiento sí/no + score) y métricas del dispositivo (`../AGENTS.md` regla 2 y 11).
- **NO se hace streaming ni monitoreo continuo**: cero imágenes entre eventos; si la cola de
  captura se llena o la memoria del chip es insuficiente, **se descarta la imagen y el evento se
  conserva igual** (la imagen es accesoria; el evento es la fuente de verdad, regla 2).

## 4. Dónde viaja y dónde se guarda la imagen

- **Destino final: SOLO el servidor local del proyecto.** La imagen se almacena en el disco del
  servidor del curso (WSL Debian, carpeta `datos/imagenes/`, fuera del repositorio y **gitignored**:
  las imágenes nunca se commitean ni viajan con el código). **NUNCA se almacena en la nube.**
- **El broker MQTT es solo un tubo de transporte.** La foto viaja por el broker autenticado del
  dueño (usuario y clave aparte, prefijo de topics aislado) y de ahí a la ingesta local, con
  `retain=false` y QoS 0: el broker **no la conserva** (si se reinicia, la imagen no queda
  almacenada ahí) (`../AGENTS.md` regla 2, decisión D-F25-1; `../SPEC.md` §6.2).
- **Canal alternativo (LAN):** receptor HTTP local `:8001` con token, solo cuando placa y servidor
  comparten red; opt-in, desactivado por defecto (`../SPEC.md` §7.1).
- **El servidor jamás consulta a la placa**: toda comunicación la inicia la placa hacia afuera
  (zero-asunción-LAN, decisión D-F26-10).

## 5. Procesamiento opcional por IA (capa de descripción y decisión de aula)

Transparencia completa — esta capa es **opcional y desactivable**:

- Un **worker de visión** puede enviar el contenido de la imagen al servicio de IA **QwenCloud**
  para generar una **descripción textual de 1-2 frases en español** y etiquetas, y habilitar la
  búsqueda por texto en la galería (`../SPEC.md` §7.3).
- Una segunda etapa (**decisión de aula con Clef**, vía OpenRouter) clasifica la escena en una
  taxonomía de 6 categorías generales (`SIN_PERSONAS_VISIBLES`, `PERSONAS_SENTADAS`, `CIRCULACION`,
  `PRESENTACION_AL_FRENTE`, `AGLOMERACION_OTRA`, `IMAGEN_DEGRADADA`); solo reenvía la imagen si la
  confianza del primer nivel es baja (`../SPEC.md` §16.6).
- **Lo que regresa de estos servicios es TEXTO** (descripción, etiquetas, etiqueta de decisión y
  confianza), que se guarda en el servidor local. **Ninguno de los dos servicios es destino de
  almacenamiento**: la única copia persistente de la imagen es la del servidor local del proyecto.
- Las descripciones generadas **no incluyen nombres de personas** (prohibido por el prompt, §3
  arriba) y las decisiones de Clef son categorías de escena, no identidades.
- **El sistema completo funciona sin esta capa**: con las claves de IA vacías (`DASHSCOPE_API_KEY`
  vacío) no se envía imagen alguna a ningún proveedor externo y la galería sigue operando con las
  fotos locales (`../AGENTS.md` regla 13). Si el aula prefiere cero envíos a servicios de IA, el
  responsable simplemente no ejecuta los workers `tools/vision_describe.py` ni `tools/clef_decide.py`.

## 6. Quién puede ver las imágenes

- **Dashboard del proyecto con inicio de sesión obligatorio** (usuario único del responsable del
  proyecto; contraseña generada por código y resguardada en archivo local ignorado por git, con
  cookie HttpOnly, bloqueo progresivo por intentos fallidos — `../SPEC.md` §9.2).
- **Acceso local:** el tablero corre en el servidor del proyecto (loopback en Windows; interno
  `:8100` en WSL). **Acceso remoto para el curso:** la URL pública del curso
  (`http://167.114.169.134:10062`) es un túnel SSH al mismo servidor local; **también exige login**
  y las credenciales se entregan **solo por el canal privado del curso**
  (`ENTREGA_PROFESOR.md`, sección "Resultado en vivo").
- Las imágenes se sirven **una por una, por identificador de base de datos y tras sesión válida**;
  la API nunca expone rutas de archivo, y una imagen purgada responde 404 con placeholder
  (`../SPEC.md` §9.1).
- **Audiencia:** profesor y compañeros del curso durante la demostración y evaluación del proyecto,
  en el marco académico ITSE.

## 7. Derechos y límites de uso del sistema

Al amparo de la Ley N.º 29733 (Ley de Protección de Datos Personales, Perú) y su Reglamento, las
imágenes de evidencia pueden contener personas y por tanto constituyen datos personales. El
proyecto se compromete a lo siguiente:

- **No vigilancia:** el sistema no se usa para vigilar, seguir ni grabar la jornada de ninguna
  persona. Solo toma una foto aislada cuando un sensor de movimiento ya decidió que hay un evento.
- **No evaluación de personas:** la evidencia **no se usa para calificar, sancionar ni evaluar** a
  ningún estudiante o docente. Su único uso es técnico-académico (validar el detector y el
  dataset del curso).
- **Framing honesto del sensor** (regla 11 del proyecto): el sistema detecta **MOVIMIENTO**, no
  "presencia": una lectura `idle` **no** prueba que el aula esté vacía, y ninguna categoría del
  sistema (`SIN_PERSONAS_VISIBLES` incluida) identifica ni cuenta personas. Nadie debe tomar
  decisiones sobre personas a partir de estas lecturas.
- **Minimización:** QVGA 320×240, baja calidad, 1 foto por evento, cooldown de 15 s, sin audio,
  sin video, sin rostros procesados.
- **Derechos ARCO** (acceso, rectificación, cancelación y oposición): ejercibles ante el docente
  responsable o el dueño del proyecto; la vía de supresión está descrita en la
  [`politica_retencion_imagenes.md`](politica_retencion_imagenes.md).

## 8. Carácter voluntario, alternativas y revocación

- **El consentimiento es libre, específico y revocable.** Negarse no tiene consecuencia académica
  alguna: no afecta nota, participación ni trato.
- **El curso puede realizarse sin fotos.** Lo consentido aquí es la **evidencia fotográfica**; el
  sistema es plenamente funcional sin ella: el detector CSI, los eventos, el dashboard, el dataset
  de movimiento y el análisis estadístico **no dependen de la cámara** (la imagen es accesoria por
  diseño: si falta, el evento se registra igual — regla 2). Alternativas equivalentes para el
  aprendizaje del curso: el análisis de eventos CSI, el modelo predictivo de actividad y las
  métricas de detección se trabajan con datos de movimiento puros.
- **Si una persona se opone o revoca su consentimiento:** se procurará, en lo posible, que no
  vuelva a aparecer en el encuadre (reubicación de la placa o cobertura del objetivo) y se
  procederá al **borrado
  anticipado** de las imágenes del rango temporal que solicite, según la
  [`politica_retencion_imagenes.md`](politica_retencion_imagenes.md) (herramienta
  `../tools/purga_imagenes.py --dias 0` o supresión selectiva documentada).
- Si el **aula en pleno** no otorga el consentimiento: la captura se desactiva por configuración
  del firmware (`AULASENSE_IMAGEN=n`, regla 14) y el proyecto se entrega y evalúa con el sistema
  en modo sin imágenes (que es el modo en que ya funcionó en las sesiones remotas donde la cámara
  estaba caída — índice de la carpeta docs: [`README.md`](README.md), nota honesta S2B/S3B).

## 9. Conservación y derecho de supresión

Las imágenes se conservan **30 días por defecto** (plazo configurable) y luego se borran con la
herramienta auditada del proyecto. El detalle técnico, el mecanismo de borrado y el procedimiento
de solicitud anticipada están en [`politica_retencion_imagenes.md`](politica_retencion_imagenes.md),
que forma parte indivisible de este consentimiento.

## 10. Firmas

Declaro haber leído este aviso (o habérselo leído a la persona interesada), comprender qué captura
el sistema, para qué se usa, quién puede verlo y que mi participación sin fotos es una alternativa
válida.

### Firma del estudiante / persona presente en el aula

| Campo | Valor |
|---|---|
| Apellidos y nombres | ______________________________________ |
| Código / sección | ______________________________________ |
| ¿Otorgo consentimiento para la captura de imágenes de evidencia? | ☐ SÍ  ☐ NO *(ambas opciones son válidas)* |
| observaciones (opcional) | ______________________________________ |
| Fecha | ____ / ____ / 20____ |
| Firma | ______________________________________ |

### Firma del docente responsable del proyecto

| Campo | Valor |
|---|---|
| Apellidos y nombres | ______________________________________ |
| Cargo / curso | Big Data e Inteligencia Artificial — ITSE |
| Compromiso | La evidencia se usará **solo** con la finalidad académica descrita; se respetarán los plazos de retención y las solicitudes de borrado |
| Fecha | ____ / ____ / 20____ |
| Firma y sello | ______________________________________ |

> Custodia: este formulario firmado se conserva fuera del repositorio público del curso (archivo
> del docente responsable), junto con el registro de la política de retención. Ningún dato de estas
> actas se carga al dashboard ni a la base de datos del proyecto.
