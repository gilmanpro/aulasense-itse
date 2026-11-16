# Manifiesto — Serie de 5 láminas pedagógicas AulaSense

**Generado:** 2026-11-15  
**Skill validada:** qwen-imagen → `generar_imagen.py` → `qwen-image-3.0-pro`  
**Estilo base:** flat illustration, paleta fría azul/blanco, coherente con `assets/poster_funcionamiento.png`  
**Resolución:** 2688×1536 px (16:9, tier 2K)  
**Parámetros:** `--no-extend --no-thinking` (control fino del prompt)

---

## Tabla de láminas

| # | Tema | Archivo | Texto renderizado | Estado | Coste |
|---|------|---------|-------------------|--------|-------|
| 1 | Portada | `lamina_01_portada.png` | "AulaSense" + "Detectar movimiento con WiFi" | **ok** | $0.075 |
| 2 | ¿Por qué no cámaras? | `lamina_02_por_que_no_camaras.png` | "Tu WiFi ya ve el movimiento" + "NO" | **ok** | $0.075 |
| 3 | Cómo detecta (CSI) | `lamina_03_como_detecta.png` | "Ondas rotas = movimiento" + "MOV" + "ON" | **ok** | $0.075 |
| 4 | La evidencia | `lamina_04_la_evidencia.png` | "Una foto solo cuando hay movimiento" + "MQTT" + "EVENTO" | **ok** | $0.075 |
| 5 | Resultados | `lamina_05_resultados.png` | "Del aula al informe" + "MQTT" + "PDF" + "ITSE proyecto IoT" | **ok** | $0.075 |

---

## Verificación visual (cada lámina)

| # | Texto legible | Ortografía ES | Sin letras deformadas | Sin manos raras | Estilo coherente |
|---|:---:|:---:|:---:|:---:|:---:|
| 1 | ✓ | ✓ | ✓ | ✓ (silueta) | ✓ |
| 2 | ✓ | ✓ | ✓ | ✓ (thumbs-down simple) | ✓ |
| 3 | ✓ | ✓ | ✓ | ✓ (silueta) | ✓ |
| 4 | ✓ | ✓ | ✓ | ✓ (silueta) | ✓ |
| 5 | ✓ | ✓ | ✓ | ✓ (sin figuras) | ✓ |

---

## Versiones para docx

Las imágenes PNG a 2688×1536 px son directamente insertables en Word (16:9). Para docx:
- Insertar como imagen a ancho completo (25.4 cm en página carta)
- O bien: `python-docx` con `add_picture(path, width=Cm(25.4))`

No se generan versiones separadas "para docx" porque el PNG ya es formato universal compatible. El orquestador las incrusta directamente en los documentos.

---

## Notas

- **0 reintentos necesarios** — todas las láminas pasaron a la primera verificación
- **0 láminas "sin-texto"** — toda la tipografía renderizada correctamente
- **Coste total: $0.375** (75% del techo de $0.50)
- Archivos temporales de prompts en `.tmp/prompt_l0[1-5].txt` (limpiar al cerrar)
