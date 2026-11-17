# Informe EDA F5a — eda.py f5a-2.0

- Rango analizado (origen=real): 2026-11-14T21:13:32+00:00 → 2026-11-17T18:36:27+00:00 UTC · 498460 evaluaciones (4428 motion)
- Determinismo: ver docs/analisis/eda.json (hash_inputs; único campo volátil generado_utc)

## 1. Score por momento del día (hora local UTC−5)

| hora | n eval | % motion | mediana score | p10 | p90 | frac score≥0.5 |
|---|---|---|---|---|---|---|
| 00 | 16688 | 2.1033 | 0.025 | 0.025 | 0.025 | 0.0382 |
| 01 | 13918 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 02 | 13978 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0002 |
| 03 | 13682 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 04 | 13875 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 05 | 13910 | 3.271 | 0.025 | 0.025 | 0.025 | 0.0419 |
| 06 | 13595 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 07 | 13814 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 08 | 8648 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 09 | 12316 | 0.3491 | 0.025 | 0.025 | 0.025 | 0.0053 |
| 10 | 15519 | 1.8816 | 0.025 | 0.025 | 0.025 | 0.0235 |
| 11 | 25407 | 2.2002 | 0.025 | 0.025 | 0.025 | 0.0288 |
| 12 | 26521 | 0.6938 | 0.025 | 0.025 | 0.025 | 0.0076 |
| 13 | 24682 | 0.0891 | 0.025 | 0.025 | 0.025 | 0.0013 |
| 14 | 13441 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 15 | 18324 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 16 | 39286 | 1.4229 | 0.025 | 0.025 | 0.025 | 0.0221 |
| 17 | 39084 | 1.5914 | 0.025 | 0.025 | 0.025 | 0.0233 |
| 18 | 12726 | 0.165 | 0.025 | 0.025 | 0.025 | 0.0031 |
| 19 | 22883 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 20 | 27974 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 21 | 27981 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 22 | 28579 | 0.2134 | 0.025 | 0.025 | 0.025 | 0.0033 |
| 23 | 41605 | 3.0261 | 0.025 | 0.025 | 0.025 | 0.0425 |

> histograma de score a resolucion 0.05 (mediana/p10/p90 aproximadas a ±0.025); hora local = UTC-5 segun cabecera de las sesiones F4

## 2. Autocorrelación de la tasa de evaluaciones (pps)

- n buckets 60 s: 4164 · media: 1.995 evaluaciones/s
- ACF lag1=0.9786 · lag5=0.9185 · lag60=0.6895
- primer lag no significativo: no decae bajo el IC 95 % en 120 lags (memoria larga por los huecos offline) (IC 95 % = ±0.0304)
> ACF estimador sesgado cov(k)/var(0) sobre serie densa de buckets de 60 s; los huecos de nodo offline entran como 0 => correlacion negativa de larga escala atribuible a los huecos (ver outliers HUECO_SERIE), no a ritmo humano intrinseco; el GT F4S1B es una unica manana, no hay ciclo diario muestreado completo (limitacion S13)

## 3. Efecto del RSSI

- muestras telemetría con RSSI: 8706 · rango -31…-14 dBm · mediana -20 dBm
- correlación bucket-score ↔ RSSI: -0.047 (n=497)
> nodo junto al AP: RSSI en rango estrecho (-31 a -14 dBm) => NO se observa efecto de distancia; la correlacion por bucket refleja variabilidad del canal/ruido, no un experimento de RSSI. Limitacion declarada SPEC §13 (sensible al entorno RF, una placa junto al AP)

## 4. Comparativa entre sesiones

| sesión | dur s | n eval | % motion | pps | mediana score motion | RSSI | die temp |
|---|---|---|---|---|---|---|---|
| F4S1 | 1332.063 | 5192 | 0.0 | 3.8977 | None | -29.8427 | 62.6273 |
| F4S1B | 4710.0 | 16604 | 6.1311 | 3.5253 | 0.975 | -28.9959 | 62.1083 |
| S2B | 1333.864 | 5178 | 2.1051 | 3.882 | 0.875 | -19.7159 | 61.4 |
| S3B | 1336.813 | 5191 | 2.3695 | 3.8831 | 0.925 | -19.6932 | 60.9682 |

> ventanas desde dataset/sesion_*/registro_*.jsonl (autodescubrimiento por glob como el pipeline); F4S1 es la pasada en aula vacia (100 % idle segun cabecera), F4S1B la pasada pasiva corroborada por fotos

## 5. Cobertura del ground truth

- tramos GT motion: 35 · con ≥1 motion: 34 (97.1429 %) · densidad motion mediana en tramos: 0.0841
- tramos con 0 evaluaciones (sin-dato): 0
> densidad = fraccion de evaluaciones motion dentro del tramo GT (buckets de 5 s pesados por solape); tramo sin evaluaciones = sin-dato, no idle. 20 con JPEG irrecoverable (error) contadas como evidencia fisica de captura, no como material vision

## 6. Outliers con motivo

- **HUECO_SERIE** (n=40): nodo offline/re-enganche (eventos NODO_OFFLINE/ONLINE; época R3 monótona del nodo, sin SNTP — SPEC §13)
  - hueco máx 55972.1 s · mediana huecos 400.9 s
- **IDLE_SCORE_ALTO** (n=3188): score >= 0.50 con state='idle': burst CSI espontáneo (F4 registró movimiento real sin caminata dirigida) o borde de histéresis del detector ESPectre — no re-entrenable aquí, se reporta
- **TS_MS_ANOMALO** (n=1): ts_ms fuera del rango de evaluaciones ±1 h: fire-test con época x2 (id 56) o fila re-enganchada tras cutover — excluida de correlaciones por el pipeline base

## Figuras

- `C:\Users\Gilman\Desktop\1-Universidad\itse\iot2\aulasense\docs\analisis\graficos\eda_score_por_hora.png`
- `C:\Users\Gilman\Desktop\1-Universidad\itse\iot2\aulasense\docs\analisis\graficos\eda_autocorrelacion_pps.png`
- `C:\Users\Gilman\Desktop\1-Universidad\itse\iot2\aulasense\docs\analisis\graficos\eda_histograma_rssi.png`
- `C:\Users\Gilman\Desktop\1-Universidad\itse\iot2\aulasense\docs\analisis\graficos\eda_sesiones_comparativa.png`
- `C:\Users\Gilman\Desktop\1-Universidad\itse\iot2\aulasense\docs\analisis\graficos\eda_cobertura_gt.png`
