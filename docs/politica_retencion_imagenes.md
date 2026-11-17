# Política de retención y borrado de imágenes de evidencia (AulaSense)

**Institución:** ITSE (Perú) · **Materia:** Big Data e Inteligencia Artificial — proyecto IoT2/ITSE 2026
**Documento:** plazos de conservación, mecanismo de borrado auditado y procedimiento de supresión
anticipada de las imágenes de evidencia de movimiento · **Versión:** 1.0 (2026-11-17)
**Fundamento en el repositorio:** exigida por `../AGENTS.md` regla 2 ("Retención y borrado
configurables") y regla 14; contratos técnicos en `../SPEC.md` §7.1 (purga, decisión D-F24-1/7),
§8.1 (tabla `imagenes`), §9.1 (galería 404 si purgada), §16.5-§16.6 (columnas de IA) y §10 (dataset
consolidado). Ejecutor material: `../tools/purga_imagenes.py`.
**Documento complementario:** [`consentimiento_aula.md`](consentimiento_aula.md) (aviso y
consentimiento informado de la captura).

---

## Índice

1. [Alcance: qué regula esta política y qué no](#1-alcance-qu%C3%A9-regula-esta-pol%C3%ADtica-y-qu%C3%A9-no)
2. [Plazo de retención](#2-plazo-de-retenci%C3%B3n)
3. [Mecanismo de borrado (herramienta auditada)](#3-mecanismo-de-borrado-herramienta-auditada)
4. [Por qué conservar la fila de base de datos NO re-identifica a ninguna persona](#4-por-qu%C3%A9-conservar-la-fila-de-base-de-datos-no-re-identifica-a-ninguna-persona)
5. [Solicitud de borrado anticipado](#5-solicitud-de-borrado-anticipado)
6. [Retención de descripciones y decisiones de IA](#6-retenci%C3%B3n-de-descripciones-y-decisiones-de-ia)
7. [Copias de seguridad, almacenamiento y alcance de la superficie de datos](#7-copias-de-seguridad-almacenamiento-y-alcance-de-la-superficie-de-datos)
8. [Registro, responsabilidades y firma](#8-registro-responsabilidades-y-firma)

---

## 1. Alcance: qué regula esta política y qué no

**Regula:**

- Los **archivos de imagen** JPEG QVGA guardados por la ingesta bajo el directorio de datos del
  servidor (`datos/imagenes/<device_id>/<yyyymmdd_HHMMSS>_<sha8>.jpg`, `../SPEC.md` §7.1) — la única
  copia persistente de la imagen, ya que puede contener personas y por tanto es dato personal
  (véase [`consentimiento_aula.md`](consentimiento_aula.md)).
- Las **filas de la tabla `imagenes`** de la base de datos SQLite (`../SPEC.md` §8.1), con sus
  metadatos y los campos generados por las capas opcionales de IA (descripción, etiquetas, decisión).

**No regula (y se aclara por qué):**

- Los **eventos de movimiento** (tabla `eventos`) y la **telemetría** del nodo (score CSI,
  temperatura del die, heap interno, etc.): no contienen imágenes ni ningún identificador de
  persona — solo la señal de que hubo movimiento y métricas del dispositivo.
- Las tablas de auditoría de consumo de IA (`vision_uso`, `clef_uso`, `../SPEC.md` §16.5): registran
  modelo, tokens y coste por llamada; no contienen contenido de imagen.
- El **broker MQTT**: no es un destino de conservación (véase §7 — `retain=false`).

## 2. Plazo de retención

| Parámetro | Valor | Dónde se define |
|---|---|---|
| Retención por defecto de las imágenes | **30 días** desde el timestamp del evento | `../AGENTS.md` regla 2; `../SPEC.md` §7.1 |
| Variable de configuración | `AULASENSE_IMG_RETENCION_DIAS` (entero ≥ 0) en el **`.env` único de la raíz** (gitignored) | `../tools/purga_imagenes.py:46-58`; `../AGENTS.md` regla 15 / `SPEC.md` §16.8 |
| Prioridad de valores | `--dias N` (línea de comandos) > `AULASENSE_IMG_RETENCION_DIAS` (`.env`) > 30 (defecto del código, `RETENCION_DEFECTO`) | `../tools/purga_imagenes.py:38,46-58` |

- El plazo es **configurable a la baja** (días, o `0`, que en la siguiente corrida purga todos los
  archivos) y hacia arriba
  solo por decisión del responsable del proyecto con registro de la causa (véase §5 y §8).
- La purga **no está automatizada en la demo**: se ejecuta a mano o programada por el operador, a
  propósito, para que cada corrida sea un acto auditable (`../SPEC.md` §7.1: "NO automático en la
  demo (auditable)"). Buena práctica del curso: ejecutarla al cierre de cada semana de uso y anotar
  la fecha en el registro del §8.

## 3. Mecanismo de borrado (herramienta auditada)

El borrado lo ejecuta **exclusivamente** `../tools/purga_imagenes.py` (implementación verificable en
el repositorio; docstring `purga_imagenes.py:8-21`). Su comportamiento por imagen vencida:

1. **Selección:** filas de `imagenes` con `ts_ms` anterior a (ahora − N días) y
   `imagen_purgada = 0` (`purga_imagenes.py:100-103`).
2. **Guarda anti-salida de ruta:** toda ruta del archivo se resuelve bajo el directorio de datos;
   cualquier ruta que escape se **ignora y se reporta como anomalía sin borrar nada**
   (`purga_imagenes.py:73-82,109-114`).
3. **Borra el archivo físico** del disco (`unlink`, `purga_imagenes.py:124-130`).
4. **Marca la fila** con `imagen_purgada = 1` (`purga_imagenes.py:131-136`). **NUNCA ejecuta
   `DELETE`**: la fila, el `evento_id` (FK al evento de movimiento) y los campos de IA se conservan
   por diseño (`../SPEC.md` §7.1 y §8 notas de integridad).
5. **Idempotente:** una segunda corrida no encuentra filas pendientes (0). Con `--dry-run` informa
   sin borrar ni marcar nada (`purga_imagenes.py:116-123`).
6. **Informe por corrida:** imprime contadores de filas procesadas, archivos borrados y anomalías —
   evidencia textual que el operador pega en el registro del §8.

**Comandos de ejecución (runtime primario WSL, distro `debian-aulasense`):**

```bash
# dentro de debian-aulasense, sobre la BD canónica de ext4:
cd /home/aulasense/aulasense && ~/venvs/aulasense/bin/python tools/purga_imagenes.py \
    --db /home/aulasense/aulasense-datos/aulasense.db --dry-run   # reconocer el lote
cd /home/aulasense/aulasense && ~/venvs/aulasense/bin/python tools/purga_imagenes.py \
    --db /home/aulasense/aulasense-datos/aulasense.db             # aplicar (30 d por defecto)
```

**Comando en el fallback Windows** (venv del proyecto, ruta canónica de `../AGENTS.md`):

```powershell
server\.venv\Scripts\python.exe tools/purga_imagenes.py --dias 30
```

**Efecto en la galería:** una vez purgada, la API responde **404** para ese id y el tablero muestra
el placeholder "purgada" (`../SPEC.md` §9.1, línea de `GET /api/imagenes/{id}/foto`): la imagen deja
de ser accesible para cualquier usuario, incluido el administrador.

## 4. Por qué conservar la fila de base de datos NO re-identifica a ninguna persona

La decisión de diseño (D-F24-7) es que **el borrado del archivo no rompa el dataset académico**.
Que esto sea compatible con protección de datos se sustenta así:

- **El único elemento potencialmente identificable era la imagen, y esta se destruye.** Tras la
  purga no queda rostro, ni escena, ni nada que permita ver cómo era el aula en ese instante.
- **Lo que permanece no son datos personales, sino metadatos técnicos del dispositivo y del
  archivo:**
  | Campo conservado | Naturaleza | ¿Identifica a una persona? |
  |---|---|---|
  | `device_id` | serial de 16 hex de **la placa** (no de una persona) | No |
  | `ts_ms`, `recibido_en` | marca temporal del evento | No |
  | `ruta`, `hash_sha256`, `tamano_bytes`, `ancho/alto` | integridad/formato de un archivo **que ya no existe** | No |
  | `evento_id` | vínculo al evento CSI (señal de movimiento) | No |
  | `imagen_purgada = 1` | constancia de que el borrado ocurrió | No — es la **traza de supresión** |
  | `canal` (`mqtt`/`http`) | transporte de llegada | No |
  | `descripcion`/`etiquetas` (IA) | texto general, prohibido nombrar personas (prompt `../server/vision/cliente.py:175-180`) | No (véase §6) |
  | `decision_etiqueta`/confianza (Clef) | 6 categorías de escena (`PERSONAS_SENTADAS`, `CIRCULACION`, …, `../SPEC.md` §16.6) | No — describen la escena, jamás a individuos |
  | `ia_persona`/`ia_score` | columna opcional de una visión en-chip **nunca implementada** (F24-e archivada, `../PLAN.md` §12.8): queda NULL | No |
- **Ninguna tabla del proyecto almacena nombres, legajos, notas ni plantillas biométricas o
  faciales**: el sistema no tiene dónde asociar una fila a una identidad (`../SPEC.md` §8 — la
  galería se sirve por id de BD, sin identidad humana alguna).
- **Beneficio académico preservado:** el consolidado del curso (`analysis/dataset/consolidado.csv`,
  esquema `../SPEC.md` §10.1) mantiene `tiene_imagen`/`imagen_purgada`, de modo que las métricas de
  la entrega (tasa de imagen por onset, integridad JPEG, distribución Clef) siguen siendo
  reproducibles sobre datos **no personales** (`../AGENTS.md` regla 2; `SPEC.md` §10: "la
  retención/purga de imágenes NO rompe el dataset").

## 5. Solicitud de borrado anticipado

Cualquier persona presente en el aula (o el docente a su pedido) puede solicitar el borrado **antes
del plazo de 30 días**. Procedimiento del responsable:

1. **Recibir la solicitud** (verbal o escrita) y registrarla en el acta del §8: fecha, solicitante,
   alcance (todo el período / rango de fechas).
2. **Decidir el barrido:**
   - *Borrado total inmediato:* ejecutar la purga con `--dias 0` — selecciona todas las filas con
     `imagen_purgada = 0` anteriores a "ahora" (aceptado por el código:
     `max(0, int(argv_dias))`, `purga_imagenes.py:49,55`) y borra todos los archivos.
   - *Borrado por antigüedad acotada:* `--dias N` con el N que cubra el período del aula afectada.
3. **Verificar:** re-ejecutar con `--dry-run` (debe reportar 0 pendientes) y confirmar que la
   galería responde 404 sobre los ids afectados (`../SPEC.md` §9.1).
4. **Registrar en la base de datos:** la propia marca `imagen_purgada = 1` junto con `ts_ms` y
   `hash_sha256` conserva el **registro auditable** de qué imagen existió y fue suprimida, sin
   conservar la imagen (`purga_imagenes.py:131-136`). La salida de la corrida (contadores) se pega
   en el acta.
5. **Limitación honesta (supresión selectiva por imagen individual):** la herramienta purga **por
   antigüedad** (todos los vencidos); **no existe aún una CLI por `id` específico**. Si la
   solicitud recae sobre una única imagen concreta, el responsable la atiende con un barrido
   conservador (`--dias` corto sobre el rango del caso) o, de forma puntual, borrando el archivo y
   ejecutando el `UPDATE imagen_purgada = 1` de esa fila **con la ingesta detenida** (invariante de
   *writer único* sobre la BD, `../AGENTS.md` regla 3) y verificando después con `--dry-run`.
   Mejora post-entrega registrada: botón/CLI `--id` en `tools/purga_imagenes.py`.
6. **Plazo de atención:** la supresión se ejecuta a más tardar **5 días hábiles** después de la
   solicitud, y se comunica al solicitante el resultado (contadores de la corrida).

## 6. Retención de descripciones y decisiones de IA

- Las **descripciones** (Qwen visión) y las **decisiones de aula** (Clef) se **conservan sin plazo
  de caducidad automática** junto con la fila, porque son el insumo no-personal del dataset
  académico del curso (búsqueda por texto, distribución de escenas, métricas de la entrega).
- **Contenido esperado:** frases generales sobre la escena ("se observa movimiento frente al
  pizarrón", etc.). **No deben contener nombres ni datos directos de personas**: el prompt del
  worker lo prohíbe expresamente (`../server/vision/cliente.py:177`) y la salida de Clef es una
  etiqueta de taxonomía fija de 6 valores (`../SPEC.md` §16.6).
- **Control de calidad:** si en una revisión humana se detecta una descripción que mencione o
  permita inferir la identidad de una persona (por ejemplo, un apodo alucinado por el modelo), el
  responsable la **corrige suprimiendo el fragmento problemático o la elimina** por la misma vía del
  punto 5 (UPDATE manual con ingesta
  detenida) y lo anota en el acta. Las imágenes asociadas se tratan como datos personales hasta
  ese control.
- **Si se desactiva la capa de IA** (claves vacías, workers no ejecutados — `../AGENTS.md`
  regla 13), simplemente no existen descripciones que retener: el ciclo de vida queda limitado al
  del archivo de imagen (§2).

## 7. Copias de seguridad, almacenamiento y alcance de la superficie de datos

| Componente | Dónde vive la imagen | Persistencia |
|---|---|---|
| Servidor local del proyecto (destino **único**) | disco del servidor WSL `debian-aulasense`: `datos/imagenes/` bajo `AULASENSE_DATOS_DIR` (BD canónica `/home/aulasense/aulasense-datos/aulasense.db`, ext4; `../server/deploy/README_wsl.md`) | sí — sujeto a esta política |
| Repositorio Git | **no entra nunca**: `datos/imagenes/` está gitignored (`../AGENTS.md` regla 14) | n/a |
| Broker MQTT del dueño (`broker.gilman.pro:14769`) | **solo transporte**: topic `image` con `retain=false` y QoS 0 → la foto **no persiste en el broker**; un reinicio del broker no la conserva ni la reenvía (`../AGENTS.md` regla 2, D-F25-1; `../SPEC.md` §6.2) | no |
| VPS público (`167.114.169.134`) | **solo broker + túneles SSH**; no ejecuta código del proyecto ni guarda datos. La publicación del dashboard (`:10062`) requiere login; el estático OTA (`:10064`) sirve únicamente `manifest.json` + binarios de firmware y **jamás** forma parte de la superficie de datos personales (`../SPEC.md` §15/§16.9/§16.10; índice de esta carpeta: [`README.md`](README.md), "Cómo ver el sistema") | no |
| Servicios de IA opcionales (QwenCloud; OpenRouter/Clef) | procesamiento momentáneo para producir **texto**; no son destino de almacenamiento en el proyecto (detalle y opción de desactivación: `consentimiento_aula.md` §5) | no (en nuestro sistema) |
| **Copias de seguridad** | el repo **no automatiza backups**. Si el responsable crea una copia de la BD o de `datos/imagenes/` (p. ej. antes de un cutover de distro WSL), dicha copia **hereda esta política**: mismo plazo, misma herramienta de purga aplicable sobre ella y misma custodia local (cifrado en reposo recomendado, fuera de rutas sincronizadas con la nube) | según la copia |

**Filtro de integridad en origen (complemento de minimización):** los frames corruptos se descartan
en la placa (Fix F) y en la ingesta (Fix S, `../SPEC.md` §7.2) — nunca se almacena "basura" ni
duplicados (dedupe por `hash_sha256`), de modo que el acervo bajo esta política es exactamente el
mínimo necesario como evidencia.

## 8. Registro, responsabilidades y firma

**Roles:**

- **Responsable del tratamiento / dueño del proyecto:** docente a cargo del curso IoT2/ITSE.
  Decide cambios de plazo, atiende solicitudes de borrado (§5) y ejecuta o supervisa cada purga.
- **Operador técnico:** quien lanza `tools/purga_imagenes.py` y archiva la salida.
- **Canal de solicitudes:** ante el docente responsable (en aula o por los canales del curso).

**Acta de corridas de purga** (registro mínimo por ejecución — se conserva junto a este documento):

| Fecha | Comando exacto | Filas / archivos borrados / anomalías | Solicitante (si anticipado) | Ejecutor |
|---|---|---|---|---|
| ____ | `purga_imagenes.py --dias ____` | ____ / ____ / ____ | ____ | ____ |
| ____ | | | | |

**Aprobación:**

| Campo | Valor |
|---|---|
| Elaborado por | Equipo del proyecto AulaSense (IoT2/ITSE 2026) |
| Revisado y aprobado — Docente responsable | ______________________________________ |
| Fecha de entrada en vigor | ____ / ____ / 20____ |
| Próxima revisión | al cierre del ciclo académico o ante cualquier cambio en `tools/purga_imagenes.py` o en `SPEC.md` §7.1/§8 |

> Esta política entra en vigor junto con el [`consentimiento_aula.md`](consentimiento_aula.md):
> ninguna captura de imágenes en aula debe iniciarse sin que ambos documentos estén firmados y
> sin que el plazo de retención configurado (`AULASENSE_IMG_RETENCION_DIAS`) conste en el `.env`
> del servidor.
