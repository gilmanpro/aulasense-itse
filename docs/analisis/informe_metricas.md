# Informe de metricas F5a — pipeline f5a-1.1

- Generado (UTC): 2026-11-15T20:03:54+00:00
- Hash de inputs (determinismo): 287d9d761c1f2050cf484d5442e528f394f1b5af1c929e3eecbdc6f90b06e4c8 (script) — ver analisis.json completo

## Metricas por sesion

| Sesion | Duracion s | Evals | %motion | pps medio | score motion mediana | media |
|---|---|---|---|---|---|---|
| F4S1 | 1332.063 | 5192 | 0.0 | 3.8977 | None | None |
| F4S1B | 4710.0 | 16604 | 6.1311 | 3.5253 | 0.9993 | 0.7615 |

## Matriz de confusion vs ground truth

- Metodo: motion detectado = >=1 evaluacion state=motion dentro del tramo GT [inicio-0ms, fin+0ms]
- Tramos GT: motion=35  idle=0
- TP=29  FN=4  **recall motion = 0.8788**
- FP=None  TN=None  FPR=None  precision=None
- Cobertura media de motion dentro de tramos GT: 0.1755

> **Nota honesta:** RECALL-ONLY: el GT actual es pasivo_foto_corroborada (solo motion confirmado por foto; los huecos quedan sin-dato, NO son idle). FPR/precision no computables. Al existir marcas idle de las sesiones activas S2/S3 el mismo codigo produce FPR/TPR/precision reales sin cambio de codigo.

## Integridad JPEG (PIL estricto)

- Validas: 31 · Rotas: 23 · Ausentes(no purgadas): 0 · Purgadas(retencion): 0
- Excluidas fire-test (id 56 etc.): 1
- **Tasa de integridad: 0.5741** (validas/(validas+rotas))

## Correlacion foto↔onset (±20000 s)

- Fotos reales (post-exclusion): 54 · Onsets en rango: 92
- Fotos emparejadas: 53 (98.1481 %) · Onsets con foto: 100.0 %
- Fotos sin match: [57] · Onsets sin foto (±20 s): 0 []
- Lag foto−onset (ms): mediana 2.0 · p95 3.0
- FK evento_id en fila imagen: 31.4815 %

## Latencias evento→fila (recibido_en UTC − ts_ms)

| Fuente | n | mediana ms | mediana corr. truncacion | p95 ms | %|diff|<2 s |
|---|---|---|---|---|---|
| eventos | 187 | -593.0 | -93.0 | -3.0 | 82.8877 |
| imagenes | 54 | -507.0 | -7.0 | -30.0 | 81.4815 |

> recibido_en en BD tiene precision de 1 s (UTC) => sesgo de truncacion de hasta -1000 ms; se reporta mediana corregida (+500 ms). ts_ms es epoca estimada por R3 (monotono del nodo), no reloj wall-clock exacto. |diff|>10 min se excluye como anomalia y se lista.
> Anomalos excluidos eventos: [] · imagenes: []

## Divergencia registros jsonl vs BD canonica (fuente de verdad: BD)

- ids en registros: 199 · ids en BD (rango): 187 · comunes: 187
- ts_ms divergentes >5 s entre dump y BD actual: 36 (re-enganche R3/cutover; no se reescribe nada)
- fuente de verdad = BD canonica actual; los jsonl son el dump historico de la sesion. ts_ms divergentes = filas re-enganchadas tras el dump (cutover/fallback); no se reescribe nada, se reporta.

## Resumen vision (solo lectura)

- Estados imagenes reales: {"error": 20, "hecho": 34, "omitido": 1}
- Tokens vision_uso totales: 18895
- Uso por resultado: error=10(0 tok), hecho=39(18895 tok), tope=1(0 tok)
- estados leidos de la BD en modo SOLO-LECTURA; la pasada 3 de vision (pendientes de S2/S3) re-alimentara estas cifras al re-ejecutar el pipeline. Presupuesto: iot2/.tmp/f25_vision_presupuesto.md (media real medida 484,5 tok/imagen; topes 120/dia 20/h).

## Graficos

- `docs/analisis/graficos/histograma_scores.png`
- `docs/analisis/graficos/serie_motion.png`
- `docs/analisis/graficos/matriz_confusion.png`

## Pendientes al llegar S2/S3 y pasada 3 de vision

- Re-ejecutar el MISMO script: los registros jsonl nuevos y las filas idle del CSV
  activan automaticamente FP/TN/FPR/precision (codigo ya preparado).
- Las imagenes `pendiente`/`error` recuperadas por vision actualizan el resumen.
- Las sesiones S2/S3 generan sus propios registros en dataset/sesion_2|3 y sus filas
  en docs/validacion_sesion2|3.csv: el pipeline los descubre por glob, cero cambios.
