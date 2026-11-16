# Diagramas explicativos de AulaSense

Seis figuras didácticas del sistema, pensadas para explicar el proyecto a un lector que no
conoce el código. **No se ha usado IA generativa de imágenes**: todas se dibujan con fuentes
textuales versionables (Mermaid, Graphviz) y se renderizan en local con `mmdc` y `dot`.

- **Idioma:** títulos y etiquetas en español; nombres técnicos (topics, ficheros, comandos) tal cual.
- **Formato:** cada figura tiene fuente + PNG (≥1600 px de ancho) + SVG vectorial.
- **Datos:** todas las cifras provienen de `SPEC.md`, `AGENTS.md`, `.tmp/s2_runner.md` y del
  código del repositorio. Lo ilustrativo está marcado como tal en la propia figura.

## Manifiesto fuente → PNG → SVG → propósito

| # | Fuente | PNG | SVG | Propósito |
|---|---|---|---|---|
| 1 | `secuencia_foto_evidencia.mmd` (Mermaid, sequence) | `secuencia_foto_evidencia.png` (5200×3036) | `secuencia_foto_evidencia.svg` | Cómo se captura y transporta una foto de evidencia: la cámara se activa **solo** en el onset, el JPEG se hashea y codifica **en la placa**, viaja por la **misma** conexión MQTT persistente (N1) al broker, el decoder valida y deduplica, y la fila entra en la BD con `canal='mqtt'`. La rama `alt` muestra el fallback HTTP `:8001` (N2). Notas con latencias medidas (14–361 ms en firmware; ~1 s broker→fila en BD). |
| 2 | `principio_csi.dot` (Graphviz) | `principio_csi.png` (1780×1900) | `principio_csi.svg` | Principio físico del sensing: router TX → ondas por la sala → varias rutas (multipath) → RX; la persona que cruza perturba amplitud y fase. Debajo, bloques de subportadoras con amplitud/fase → vector de características → detector MLP 8→24→12→1 (529 parámetros) en el chip → decisión movimiento/reposo cada 250 ms. |
| 3 | `topologia_despliegue.dot` (Graphviz) | `topologia_despliegue.png` (3160×847) | `topologia_despliegue.svg` | Despliegue físico: ESP32-S3 → router de casa → PC Windows (WSL2 `debian-aulasense` en `:8100`; DECANA histórica `:8000` como rollback) → broker `broker.gilman.pro:14769` (VPS 167.114.169.134) → túnel `ssh -R` (auto‑start) → puerto público `10062` → navegadores. Dos fronteras de confianza en caja punteada: **LAN local** e **Internet**. |
| 4 | `pipeline_datos.mmd` (Mermaid, flowchart LR) | `pipeline_datos.png` (6000×2210) | `pipeline_datos.svg` | Flujo de datos de izquierda a derecha: CSI ~100 pps → características → detector (umbral 0,5) → eventos motion/onset → ingestas (fotos, telemetry, SSE) → BD SQLite WAL con writer único → dashboard (login scrypt, lockout 5/10/20, búsqueda LIKE, chips) → visión modo B (pendiente → worker mediado → hecho/error, techos 120/día y 20/h) → análisis (`run_analisis.ps1` → `analisis.json`) → informes (`generar_informe.py` → docx/pptx). |
| 5 | `privacidad_y_seguridad.dot` (Graphviz) | `privacidad_y_seguridad.png` (1828×1886) | `privacidad_y_seguridad.svg` | Comparación en dos columnas: **lo que sale** de la sala (metadata de movimiento, 1 JPEG solo al onset por broker autenticado, hash SHA‑256) frente a **lo que no sale nunca** (vídeo continuo, caras —no hay reconocimiento facial—, credenciales en `.env` gitignored). Debajo, caja de controles implementados y caja de riesgos aceptados. |
| 6 | `ciclo_de_vida_sesiones.mmd` (Mermaid, state) | `ciclo_de_vida_sesiones.png` (5200×862) | `ciclo_de_vida_sesiones.svg` | Ciclo de vida de las sesiones: S1 grabada hoy (17.913 evaluaciones, GT pasivo foto‑corroborada) → S2 automática (16/11, 10:50, 22 min; velador y marcas idle opcionales que activan FPR) → S3 (11:20) → consolidado de 462 filas → análisis y regeneración de informes, **un comando por entrega**. |

## Cómo regenerar

Requisitos usados en esta máquina: `mmdc` 12.2.0 (mermaid-cli), Graphviz 16.1.1, Python 3.15 + Pillow.

```powershell
cd aulasense
$d = 'assets\diagramas_explicativos'

# 1, 4 y 6 (Mermaid)
mmdc -i "$d\secuencia_foto_evidencia.mmd" -o "$d\secuencia_foto_evidencia.png" --size 2600 -s 2 -b white -c "$d\mermaid-config.json"
mmdc -i "$d\secuencia_foto_evidencia.mmd" -o "$d\secuencia_foto_evidencia.svg" --size 2600 -c "$d\mermaid-config.json"
mmdc -i "$d\pipeline_datos.mmd"           -o "$d\pipeline_datos.png"           --size 3000 -s 2 -b white -c "$d\mermaid-config.json"
mmdc -i "$d\pipeline_datos.mmd"           -o "$d\pipeline_datos.svg"           --size 3000 -c "$d\mermaid-config.json"

# 6 usa el tema por defecto (el tema base oculta las etiquetas de estado en mermaid-cli 12.2.0)
mmdc -i "$d\ciclo_de_vida_sesiones.mmd"   -o "$d\ciclo_de_vida_sesiones.png"   --size 2600 -s 2 -b white
mmdc -i "$d\ciclo_de_vida_sesiones.mmd"   -o "$d\ciclo_de_vida_sesiones.svg"   --size 2600

# 2, 3 y 5 (Graphviz)
dot -Tpng -Gdpi=140 "$d\principio_csi.dot"         -o "$d\principio_csi.png"
dot -Tsvg            "$d\principio_csi.dot"         -o "$d\principio_csi.svg"
dot -Tpng -Gdpi=150 "$d\topologia_despliegue.dot"   -o "$d\topologia_despliegue.png"
dot -Tsvg            "$d\topologia_despliegue.dot"   -o "$d\topologia_despliegue.svg"
dot -Tpng -Gdpi=175 "$d\privacidad_y_seguridad.dot" -o "$d\privacidad_y_seguridad.png"
dot -Tsvg            "$d\privacidad_y_seguridad.dot" -o "$d\privacidad_y_seguridad.svg"
```

`mermaid-config.json` fija la paleta azul/gris y el amarillo de las notas para que las tres
figuras Mermaid (1, 4 y 6) compartan aspecto con las de Graphviz. La figura 6 no lo usa por la
limitación del tema `base` con los diagramas de estado (ver comentario en el bloque de comandos).

## Verificación realizada

- `mmdc` y `dot` terminaron con exit 0 en las seis figuras; las fuentes quedan en disco.
- Dimensiones medidas con Pillow (todas ≥1600 px de ancho): ver tabla.
- PNG abiertos y revisados visualmente uno a uno: etiquetas en español sin mojibake, sin
  solapes de nodos ni texto cortado. En la figura 1 los mensajes largos se parten con `<br/>`
  para no invadir la lifeline vecina.
- Los SVG contienen el texto real (no imágenes incrustadas) y no presentan secuencias de
  mojibake (`Ã`, `Â`, `â€`).

## Notas de honestidad

- **Figura 2:** los valores de amplitud y fase de la tabla son **ilustrativos** (se indica en el
  propio título de la tabla); no son una captura medida. Las etiquetas `UMD`, `UPD` y `DIF`
  se rotulan tal como las indicó el encargo; no se amplía su significado porque no aparecen
  documentadas en el repositorio (marcadas AMBIGUO en su semántica).
- **Figura 2:** la persona **perturba** el canal (atenúa/desvía); el sistema no la detecta,
  identifica ni cuenta. Coherente con la regla 11 de `AGENTS.md`.
- **Figura 6:** S2 arranca a las 10:50 y S3 a las 11:20 (22 min cada una), según el runner real
  documentado en `.tmp/s2_runner.md`; el encargo menciona 10:50 como inicio de la ventana.
- **Figura 3:** el nodo publica al broker por la red doméstica; el dibujo muestra la cadena
  ESP32 → router → PC y las conexiones salientes de la distro, no un enlace directo del ESP32
  al VPS, para no cruzar las fronteras de confianza con aristas ambiguas.
- No se ha modificado ningún artefacto previo (`assets/diagrama_tecnico*`, `docs/entrega`) ni se
  ha hecho commit.