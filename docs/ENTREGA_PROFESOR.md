# AulaSense — Entrega para el curso (lea esta pagina primero)

**Que es:** un sensor de movimiento que usa las ondas Wi-Fi (CSI) y una red neuronal que corre
**entera dentro del chip ESP32-S3** — sin microrfono, sin radar, sin hardware extra. Cada vez que
detecta movimiento sube el evento por MQTT, guarda una foto de evidencia con descripcion de IA, y
alimenta un dashboard publico, un dataset propio y un informe de metricas totalmente reproducible.

Esta pagina es el recorrido guiado. El `README.md` de la raiz queda como indice tecnico.

---

## Resultado en vivo — empiece aqui

**Dashboard publico:** [http://167.114.169.134:10062](http://167.114.169.134:10062)

> Las credenciales de acceso se entregan al profesor por el **canal privado del curso**. No figuren
> ni figuraran nunca en este repositorio.

Al entrar vera:

- **Eventos en tiempo real**: cada deteccion de `motion` del chip, con senales del nodo (temperatura
  del die, tasa CSI) refrescados por WebSocket.
- **Galeria de evidencia fotografica**: 1 foto QVGA por evento, cada una con la **descripcion
  generada por IA** (Qwen vision) y su estado QC honesto.
- **Busqueda por texto**: consulte la galeria escribiendo en espanol lo que busca ("alguien camina
  frente al pizarron") y la IA filtra por las descripciones.

---

## Recorrido recomendado (10 minutos)

Rutas relativas dentro del repositorio, en orden sugerido:

| # | Pieza | Que aporta | Tiempo |
|---|---|---|---|
| 1 | [`video_aulasense.mp4`](video_aulasense.mp4) | El arco completo del proyecto, **contado honesto**: incluye por que fallo la camara del proyecto anterior (ese fallo es el origen de este diseno) | ~1 min |
| 2 | [`video_como_funciona_40s.mp4`](video_como_funciona_40s.mp4) | Explicacion tecnica rapida: CSI → detector en-chip → MQTT → dashboard | ~40 s |
| 3 | [`GUIA_VISUAL.md`](GUIA_VISUAL.md) | Recorrido grafico con figuras: arquitectura, flujo de datos, topologia de red y privacidad | 3 min |
| 4 | [`entrega/INFORME.docx`](entrega/INFORME.docx) | Informe formal completo (diseno, implementacion, validacion, metricas) | para casa |
| 5 | [`entrega/PRESENTACION_VISUAL.pptx`](entrega/PRESENTACION_VISUAL.pptx) y [`entrega/PRESENTACION.pptx`](entrega/PRESENTACION.pptx) | Dos decks: uno grafico para exponer, uno de datos para defender numeros | 2 min |
| 6 | [`analisis/informe_metricas.md`](analisis/informe_metricas.md) + [`analisis/graficos/`](analisis/graficos/) | Metricas propias con tamano de muestra (matriz de confusion vs ground truth, EDA) y las 11 figuras | 2 min |
| 7 | [`../dataset/sesion_1/`](../dataset/sesion_1/) | **Datos reales crudos** (JSONL) con su ground truth en [`validacion_sesion1.csv`](validacion_sesion1.csv) | 1 min |
| 8 | [`../analysis/run_analisis.ps1`](../analysis/run_analisis.ps1) | Todo el analisis (EDA, metricas, figures, JSON) reproducible **con un comando** | — |
| 9 | [`../firmware/parche_aulasense/`](../firmware/parche_aulasense/) | El codigo propio del chip (derivado GPLv3 de ESPectre, con README que explica como aplicar el parche) | 1 min |
| 10 | [`../server/`](../server/) | Ingesta MQTT, dashboard con login, worker de vision IA y despliegue WSL | — |

---

## Que hace unico este proyecto

- **Edge-first de verdad**: el detector neuronal (MLP de 529 parametros sobre CSI) corre 100 % en el
  ESP32-S3 y decide cada 250 ms. El CSI crudo **nunca sale del chip**; el PC solo recibe eventos ya
  decididos. Cero hardware extra: la placa y el router que ya existian.
- **Dataset propio, medido — no cifras del vendor**: metricas calculadas sobre datos reales con
  ground truth anotado (p. ej. recall de motion 0.8788 sobre tramos validados, con nota honesta de
  por que solo es recall). Las metricas publicadas por ESPectre (98,1–99,3 %) **no** se presentan
  como propias.
- **Ciclo completo hardware → nube → informes**: chip → broker MQTT autenticado → ingesta + SQLite
  en WSL Debian → dashboard con login publicado por tunel en VPS → vision IA → analisis pandas/
  sklearn → informe Word y presentacion, todo reproducible con un comando.
- **Honestidad documentada hasta el fallo**: de 54 fotos reales, 23 llegaron corruptas de origen por
  un byte-loss de 16 B debido a overflow GDMA en la captura; la **causa raiz se encontro y se
  corrigio** (Fix F + Fix S), las 20 irreparables quedan marcadas `'error'` en el dataset, y las
  metricas se reportan **recall-only** con la razon (ground truth pasivo) en lugar de inflarlas.

---

## Como reproducir

1. **Clonar** el repositorio.
2. **Configurar sus credenciales**: copiar `.env.example` a `.env` (archivo ignorado por git; nunca
   se commitea) y rellenar **sus** datos de WiFi 2.4 GHz y del broker MQTT. Las del curso se
   entregan por canal privado.
3. **Firmware**: instalar ESP-IDF **v5.5.6** con `eim`; clonar ESPectre tag `3.2.0` en
   `../firmware/espectre/` y aplicar el parche segun su README:
   `eim run "idf.py -p COM6 flash monitor" v5.5.6` (ruta primaria, sin Docker).
4. **Server**: runtime nativo en **WSL Debian 13** (venv + systemd, sin Docker; Windows como
   fallback). Units y pasos en [`../server/deploy/README_wsl.md`](../server/deploy/README_wsl.md).
5. **Analisis completo**: `powershell analysis/run_analisis.ps1 --origen real --validacion
   docs/validacion_sesion1.csv` regenera metricas, figuras e informes desde el dataset.

---

## Limitaciones honestas

- El endpoint publico se sirve por **HTTP sin TLS** (tunel SSH al VPS): el contenido es de demo; el
  acceso esta protegido por login y lockout, no por cifrado de transporte publico.
- La clave del broker MQTT compartido es corta y **las ACLs por topic aun estan pendientes**
  (aislamiento actual por prefijo de topic propio).
- Los escenarios **E2–E5 del portal de provisionado** (caida real del router) quedan
  **PENDIENTE-ventana-natural**: exigen una interrupcion no controlable; en verificacion.
- Las sesiones **S2/S3 del dataset estan en curso**; S1 ya esta publicada con ground truth.
- El sistema **detecta MOVIMIENTO**: no presencia estacionaria, no personas identificadas ni
  contadas. `idle` no significa sala vacia; una placa cubre una zona.
