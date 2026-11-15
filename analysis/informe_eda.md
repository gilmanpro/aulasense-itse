# Informe EDA F5a — eda.py f5a-2.0

- Rango analizado (origen=real): 2026-11-14T21:13:32+00:00 → 2026-11-15T20:04:04+00:00 UTC · 165018 evaluaciones (2927 motion)
- Determinismo: ver docs/analisis/eda.json (hash_inputs; único campo volátil generado_utc)

## 1. Score por momento del día (hora local UTC−5)

| hora | n eval | % motion | mediana score | p10 | p90 | frac score≥0.5 |
|---|---|---|---|---|---|---|
| 00 | 803 | 7.8456 | 0.025 | 0.025 | 0.925 | 0.1133 |
| 03 | 28 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 04 | 56 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 05 | 57 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 06 | 49 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 07 | 51 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 08 | 75 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 09 | 83 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 10 | 1561 | 17.6169 | 0.025 | 0.025 | 0.975 | 0.2063 |
| 11 | 11442 | 4.8855 | 0.025 | 0.025 | 0.025 | 0.064 |
| 12 | 12853 | 1.4316 | 0.025 | 0.025 | 0.025 | 0.0157 |
| 13 | 16487 | 0.1334 | 0.025 | 0.025 | 0.025 | 0.002 |
| 14 | 13441 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 15 | 936 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 16 | 11354 | 4.9234 | 0.025 | 0.025 | 0.025 | 0.0762 |
| 17 | 13660 | 2.1816 | 0.025 | 0.025 | 0.025 | 0.0392 |
| 18 | 12726 | 0.165 | 0.025 | 0.025 | 0.025 | 0.0031 |
| 19 | 13739 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 20 | 13990 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 21 | 13992 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 22 | 13987 | 0.0 | 0.025 | 0.025 | 0.025 | 0.0 |
| 23 | 13636 | 6.9375 | 0.025 | 0.025 | 0.025 | 0.0846 |

> histograma de score a resolucion 0.05 (mediana/p10/p90 aproximadas a ±0.025); hora local = UTC-5 segun cabecera de las sesiones F4

## 2. Autocorrelación de la tasa de evaluaciones (pps)

- n buckets 60 s: 1372 · media: 2.0045 evaluaciones/s
- ACF lag1=0.9593 · lag5=0.8521 · lag60=0.7081
- primer lag no significativo: no decae bajo el IC 95 % en 120 lags (memoria larga por los huecos offline) (IC 95 % = ±0.0529)
> ACF estimador sesgado cov(k)/var(0) sobre serie densa de buckets de 60 s; los huecos de nodo offline entran como 0 => correlacion negativa de larga escala atribuible a los huecos (ver outliers HUECO_SERIE), no a ritmo humano intrinseco; el GT F4S1B es una unica manana, no hay ciclo diario muestreado completo (limitacion S13)

## 3. Efecto del RSSI

- muestras telemetría con RSSI: 2963 · rango -31…-14 dBm · mediana -29 dBm
- correlación bucket-score ↔ RSSI: -0.0707 (n=222)
> nodo junto al AP: RSSI en rango estrecho (-31 a -14 dBm) => NO se observa efecto de distancia; la correlacion por bucket refleja variabilidad del canal/ruido, no un experimento de RSSI. Limitacion declarada SPEC §13 (sensible al entorno RF, una placa junto al AP)

## 4. Comparativa entre sesiones

| sesión | dur s | n eval | % motion | pps | mediana score motion | RSSI | die temp |
|---|---|---|---|---|---|---|---|
| F4S1 | 1332.063 | 5192 | 0.0 | 3.8977 | None | -29.8427 | 62.6273 |
| F4S1B | 4710.0 | 16604 | 6.1311 | 3.5253 | 0.975 | -28.9959 | 62.1083 |

> ventanas desde dataset/sesion_*/registro_*.jsonl (autodescubrimiento por glob como el pipeline); F4S1 es la pasada en aula vacia (100 % idle segun cabecera), F4S1B la pasada pasiva corroborada por fotos

## 5. Cobertura del ground truth

- tramos GT motion: 35 · con ≥1 motion: 34 (97.1429 %) · densidad motion mediana en tramos: 0.0841
- tramos con 0 evaluaciones (sin-dato): 0
> densidad = fraccion de evaluaciones motion dentro del tramo GT (buckets de 5 s pesados por solape); tramo sin evaluaciones = sin-dato, no idle

## 6. Outliers con motivo

- **HUECO_SERIE** (n=40): nodo offline/re-enganche (eventos NODO_OFFLINE/ONLINE; época R3 monótona del nodo, sin SNTP — SPEC §13)
  - hueco máx 12673.4 s · mediana huecos 302.1 s
- **IDLE_SCORE_ALTO** (n=1890): score >= 0.50 con state='idle': burst CSI espontáneo (F4 registró movimiento real sin caminata dirigida) o borde de histéresis del detector ESPectre — no re-entrenable aquí, se reporta
- **TS_MS_ANOMALO** (n=1): ts_ms fuera del rango de evaluaciones ±1 h: fire-test con época x2 (id 56) o fila re-enganchada tras cutover — excluida de correlaciones por el pipeline base

## Figuras

- `C:\Users\Gilman\Desktop\1-Universidad\itse\iot2\aulasense\docs\analisis\graficos\eda_score_por_hora.png`
- `C:\Users\Gilman\Desktop\1-Universidad\itse\iot2\aulasense\docs\analisis\graficos\eda_autocorrelacion_pps.png`
- `C:\Users\Gilman\Desktop\1-Universidad\itse\iot2\aulasense\docs\analisis\graficos\eda_histograma_rssi.png`
- `C:\Users\Gilman\Desktop\1-Universidad\itse\iot2\aulasense\docs\analisis\graficos\eda_sesiones_comparativa.png`
- `C:\Users\Gilman\Desktop\1-Universidad\itse\iot2\aulasense\docs\analisis\graficos\eda_cobertura_gt.png`
