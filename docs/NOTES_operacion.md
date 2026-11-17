# Notas de operación — AulaSense (manual del dueño)

Estado de referencia (2026-11-17): placa ESP32-S3 GOOUUU en sitio remoto, `device_id`
`2e6f0c909e30673e`, build viva **f26.6** válida (sha256
`339A1A74C74A6C2BFEF4F8D913BB24FD30622CDC36A96C3674A2644E9680EF19`, 1.418.720 B).

Operación **100 % outbound-only**: la placa SOLO abre conexiones salientes (MQTT al broker
`broker.gilman.pro:14769` con auth + GET HTTP del binario OTA). El servidor **jamás alcanza a la
placa**: no hay SSH, ni HTTP entrante, ni polling hacia el nodo. Todo lo que sigue asume ese modelo.

Topology operativa: VPS `167.114.169.134` = SOLO broker Mosquitto + túneles SSH (`wsl-port`). El
servidor que gestiona TODO corre en la distro WSL `debian-aulasense` (systemd, venv linux, sin
Docker): ingesta + BD canónica `/home/aulasense/aulasense-datos/aulasense.db` (WAL, writer único =
ingesta), dashboard interno `:8100` → túnel `pub-aulasense-8100` → público
`http://167.114.169.134:10062`, estático OTA `:8101` (unit `aulasense-ota.service`, carpeta
`/home/aulasense/aulasense-ota/`) → túnel `pub-aulasense-ota` → público `:10064`.

## Índice

1. [Cómo disparar una OTA (paso a paso)](#1-cómo-disparar-una-ota-paso-a-paso)
2. [Semántica del rollback](#2-semántica-del-rollback)
3. [Agujero de telemetría esperado (30 s)](#3-agujero-de-telemetría-esperado-30-s)
4. [Re-provisionamiento en sitio (portal fijo)](#4-re-provisionamiento-en-sitio-portal-fijo)
5. [Energía y verificación post-reinstalación](#5-energía-y-verificación-post-reinstalación)
6. [Cámara, gate y heap: qué umbral es cuál](#6-cámara-gate-y-heap-qué-umbral-es-cuál)
7. [Mejoras post-entrega](#7-mejoras-post-entrega)
8. [Limitaciones honestas](#8-limitaciones-honestas)
9. [Lista "nunca"](#9-lista-nunca)
10. [Anexo A — duraciones medidas](#anexo-a--duraciones-medidas)
11. [Anexo B — dónde vive cada credencial](#anexo-b--dónde-vive-cada-credencial)

---

## 1. Cómo disparar una OTA (paso a paso)

La OTA usa el módulo propio `app_ota.c` (NO el canal vendor). El chequeo periódico automático está
DESACTIVADO (`AULASENSE_OTA_CHECK_MIN=0` en el `.env` de la distro): **toda OTA es a mano, por
humano y con verificación entre pasos.**

### 1.1 Preparar el bin y generar el manifest (POR CÓDIGO, nunca a mano)

El `manifest.json` se escribe SIEMPRE por código leyendo sha256 y size del fichero real — jamas se
transcribe a mano (el parser de la placa compara hash y tamaño byte a byte). Patrón de los
publicadores usados en el banco (`.tmp/f26g1_publishN.py`, `.tmp/f26g2_manifest.py`, gitignored):

```python
# (.tmp/publica_bin.py — Windows, invoca wsl; versión resumida del publicador real)
BIN = r"...\firmware\espectre\src\cpp\frontend\native\app\build\espectre-native.bin"
VERSION = "f26.7"                      # argv en el script real
URL = f"http://167.114.169.134:10064/firmware-{VERSION}.bin"
DESTINO = "/home/aulasense/aulasense-ota"

data = open(BIN, "rb").read()
sha = hashlib.sha256(data).hexdigest()   # minusculas, del fichero real
size = len(data)
assert VERSION.encode() in data          # invariante: el bin contiene la version nueva

# 1) copiar el bin al estatico y RE-verificar sha/size en el destino (copia sana)
# 2) escribir el manifest COMPACTO (json.dumps(separators=(",", ":"))) y copiarlo
manifest = {"version": VERSION, "url": URL, "sha256": sha, "size": size}
texto = json.dumps(manifest, separators=(",", ":"))  # parser strstr de la placa
```

Rutas finales: `/home/aulasense/aulasense-ota/firmware-<ver>.bin` +
`/home/aulasense/aulasense-ota/manifest.json` (el manifest SI se reemplaza; los bins viejos NO se
borran, ver §1.5). El manifest debe quedar compacto: con espacios (`": "`) el parser embebido de
`app_ota.c` ignora el comando (hallazgo de interop F26-g1, mismo contrato que el envelope MQTT).

### 1.2 Verificar externamente antes de tocar la placa

Comprobar que el publico ve exactamente lo que se publico (el tunel puede estar caido o servir otra
carpeta):

```powershell
# 1) manifest accesible y con el sha esperado
Invoke-RestMethod http://167.114.169.134:10064/manifest.json

# 2) descargar el bin por la URL publica y comparar sha contra el manifest
curl.exe -s -o .tmp\verifica_firmware.bin http://167.114.169.134:10064/firmware-f26.7.bin
$m = Invoke-RestMethod http://167.114.169.134:10064/manifest.json
if ((Get-FileHash .tmp\verifica_firmware.bin).Hash -eq $m.sha256) { "SHA OK" } else { "NO COINCIDE: no dispares apply" }
```

La comparacion `-eq` de PowerShell es case-insensitive (el manifest publica en minusculas y
`Get-FileHash` devuelve mayusculas: es equivalente). Si algo falla, NO continuar.

### 1.3 Disparar `check` primero; `apply` solo tras revisar

- `check` es **inofensivo**: la placa responde `OTA_CHECK` (~1 s) con `hay_actualizacion` y NO toca
  el slot de firmware.
- `apply` es el **unico** que ejecuta el flujo completo (gate → descarga → verificacion → set_boot →
  reboot). La placa **rechaza re-aplicar la misma version local** (respuesta `sin_cambio`).

El comando se publica por MQTT con el helper `publicar_comando_ota` del arbol DESPLEGADO
(`server/ingesta/ingesta_mqtt.py`), con credenciales leidas del `.env` y **nunca impresas**. Patron
del script real de banco (`.tmp/f26g1_cmd.py`):

```python
# .tmp/comando_ota.py  — uso: wsl -d debian-aulasense -- ~/venvs/aulasense/bin/python \
#   "/mnt/c/<ruta repo>/.tmp/comando_ota.py" check|apply
import json, sys, time, uuid, paho.mqtt.client as mqtt

sys.path.insert(0, "/home/aulasense/aulasense/server/ingesta")
import ingesta_mqtt as im          # helper real del arbol desplegado

env = {}
for line in open("/home/aulasense/aulasense/.env", encoding="utf-8"):
    t = line.strip()
    if t and not t.startswith("#") and "=" in t:
        k, v = t.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

cli = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"ota-{uuid.uuid4().hex[:8]}")
cli.username_pw_set(env.get("MQTT_INGESTA_USER", ""), env.get("MQTT_PASS", ""))  # nunca impresas
cli.connect(env.get("MQTT_HOST", ""), int(env.get("MQTT_PORT", "1883")), 10)
cli.loop_start(); time.sleep(0.5)

reg = im.publicar_comando_ota(cli, "2e6f0c909e30673e", sys.argv[1])   # "check" | "apply"
time.sleep(1.5)   # dejar salir el PUBLISH del outbox
cli.loop_stop(); cli.disconnect()
print(json.dumps(reg, ensure_ascii=False))   # {command_id, topic, payload, qos} — sin secretos
```

El topic es `aulasense/v1/devices/2e6f0c909e30673e/aulasense_ota` y el envelope compacto
`{"command_id":"ota-<ts>","command":"aulasense_ota","action":"check"|"apply"}`. No hay respuesta
sincrona: el estado llega por el retain `.../ota` y los eventos `OTA_*`.

Flujo interno de un `apply` (para interpretar lo que veras): gate de heap **interno post-quiesce**
(free ≥ 96 KB y largest ≥ 48 KB; umbrales ORIGINALES de SPEC §16.1, 120/64, enmendados a 96/48 con
evidencia — ver §6) → pausa captura + telemetria → descarga del bin (~34 s medidos) → verifica
SHA-256 + size → `set_boot` → `OTA_OK` → reboot (~20 s) → self-check de 90 s estables (Wi-Fi GOT_IP
+ MQTT + CSI armado) + 120 s de gracia → marca la imagen como valida. E2E total ~163 s.

Retain `.../ota`: whitelist de estados `idle|checking|downloading|verifying|rebooting|error`
(self-check = `verifying`; exito final = `idle` con `sha256_ok:true` y
"self-check superado: imagen validada").

### 1.4 Verificar la cadena en dashboard / BD (solo lectura)

La cadena completa que debe verse tras un `apply` bueno, en `config_eventos`:

`OTA_CHECK` → `OTA_INICIO` (su `detalle` lleva `post_quiesce free=… largest=…`: confirma el gate) →
`OTA_OK` (con `sha256_ok=true`) → `FIRMWARE` (version nueva) → `OTA_OK` del self-check; y en el
retain final (`estado_sistema.detalle`), `ota` = `idle` + `sha_ok` + "imagen validada".

```python
# .tmp/verifica_ota.py — consulta SOLO-LECTURA (uri mode=ro); no existe CLI sqlite3 en la distro
import json, sqlite3
con = sqlite3.connect("file:/home/aulasense/aulasense-datos/aulasense.db?mode=ro", uri=True)
con.row_factory = sqlite3.Row
for r in con.execute(
    "SELECT ts_ms, tipo, detalle FROM config_eventos "
    "WHERE tipo LIKE 'OTA%' OR tipo='FIRMWARE' ORDER BY ts_ms DESC LIMIT 20"
):
    print(r["ts_ms"], r["tipo"], (r["detalle"] or "")[:160])
det = json.loads(con.execute("SELECT detalle FROM estado_sistema WHERE id=1").fetchone()[0])
print("retain ota:", det.get("ota"))
print("retain imagen_estado:", det.get("imagen_estado"))
```

Ejecucion desde Windows:

```powershell
wsl -d debian-aulasense -- ~/venvs/aulasense/bin/python "/mnt/c/Users/Gilman/Desktop/1-Universidad/itse/iot2/aulasense/.tmp/verifica_ota.py"
```

Tambien se ve en el dashboard (panel `/salud` y el historico de eventos) y desde el propio host:
`powershell tools\wsl_aulasense.ps1 salud`.

### 1.5 Conservar bins y manifests viejos

**No borrar** `firmware-*.bin` ni manifests anteriores del estatico: son la evidencia de auditoria
de las OTAs pasadas (f26.4 → f26.5 → f26.6 + el bin de prueba de rollback). La placa rechaza de
todas formas reinstalar una version igual a la local.

---

## 2. Semántica del rollback

La proteccion **anti-brick vive en el bootloader** (doble slot con verificacion de imagen), ya
flasheado en la placa: **toda OTA futura queda protegida automaticamente**, sin codigo extra.

- **Bin malo con sha VALIDO** (la firma pasa, el firmware no funciona): la placa arranca la imagen
  en estado `PENDING_VERIFY` → self-check 90 s + gracia 120 s → al no superar el self-check, **el
  bootloader revierte SOLO a la imagen anterior**: **225 s sin intervencion humana** (medido:
  225,2 s = 90 s self-check + 120 s gracia + ~15 s de reboot/reconexion + ciclo de telemetria).
  Demostrado en banco con `f26.7-test` (sha correcto, self-check fallido a proposito) y la placa
  "resucitando sola" 5 veces (1 planeada + 4 por un bug de rutas del runner).
- **Que se ve en el dashboard mientras tanto**: el mismo agujero de telemetria de la §3 (la placa
  esta reiniciando), edades subiendo en `/api/salud` **sin falsa alarma** porque el retain `ota`
  estaba activo, y al final un evento `FIRMWARE` con la version **vieja** (la que revivio) — la
  cadena no muestra `OTA_OK` de self-check.
- **Sha alterado** (bin corrupto o manifiesto mentiroso): `OTA_ERROR etapa=sha256` **sin reboot**:
  la placa NUNCA setea el boot en una imagen que no verifica, y sigue corriendo la version actual
  (rechazo a los ~34 s de terminada la descarga).

Regla practica: un `apply` que "desaparece" a la placa NO es una emergencia — es el rollback
haciendo su trabajo; esperar 4 minutos y verificar `FIRMWARE` con la version anterior.

---

## 3. Agujero de telemetría esperado (30 s)

Durante un `apply` hay una ventana de **~30 s sin telemetria**: es el tramo descarga (~34 s, solape
con el ultimo reporte) + reboot (~20 s). **Esperado; no alarmar.**

El panel `/api/salud` lo gestiona solo: con el retain `ota` en estado no-`idle` marca
`ota_en_curso=true` y **SUPRIME la falsa alarma** de telemetria vieja. Si ves `ota_en_curso=true`
con `state=downloading|verifying|rebooting`, la placa esta exactamente donde debe estar.

---

## 4. Re-provisionamiento en sitio (portal fijo)

Realidad flasheada en la build viva (D-F26-4; NO "pendiente"): si la WiFi del sitio falla **3
ciclos**, la placa sube un AP fija:

- SSID **`AULASENSE-CFG`** (fija, sin sufijo MAC), canal 1, WPA2.
- Password del AP: **`itse-aulasense`** (pass canonica; vive en `.env` + esta nota por orden
  explicita del dueño. **Es una capacidad LOCAL**: exige asociarse fisicamente al AP de la placa;
  no habilita nada remoto).
- Portal **sin token** en `http://192.168.4.1`, rutas exactas (el resto 404):
  - `GET /cfg` — formulario HTML (campos `ssid` ≤32 chars, `pass` ≤64 chars).
  - `GET /cfg/estado` — JSON con `wifi_sta` / `ap_activa` / `heap` (sin secretos).
  - `POST /cfg/wifi` — guarda en NVS → **AP OFF → STA puro → connect**.

Pasos con la placa en el sitio nuevo:

1. Llevar la placa al area del router nuevo; esperar a que suba `AULASENSE-CFG` (o reiniciarla y
   dejar que fallen los 3 ciclos de la red vieja).
2. Asociarse al AP `AULASENSE-CFG` con la pass canonica.
3. Abrir `http://192.168.4.1/cfg` en el navegador y llenar SSID/clave de la red del sitio.

Equivalente por curl (cliente no-navegador: el guard de Origin/Referer deja pasar peticiones sin
esas cabeceras; un origen cruzado desde navegador devuelve 403):

```bash
curl http://192.168.4.1/cfg/estado
curl -X POST -d "ssid=MIRED&pass=MIPASS_DE_LA_RED_DEL_SITIO" http://192.168.4.1/cfg/wifi
```

Que pasa despues:

- Guardado OK: la placa responde, guarda NVS, apaga el AP e intenta conectar. **Si vuelve a fallar,
  re-sube el AP** (puedes reintentar).
- Confirmacion REMOTA: los eventos `CFG_GUARDADA` y `STA_RECONECTADO` aterrizan en
  `config_eventos` (y se ven en el dashboard) cuando la placa recupera MQTT — verificacion
  cruzada sin volver al sitio.
- **Cooldown 5 s** entre POSTs: re-postear antes devuelve 429. Body maximo 255 B. La placa NUNCA
  loguea SSID ni pass (solo longitudes).
- **Regla de colision**: el SSID es fijo → **una placa a la vez** en provisioning.
- BOOT 3 s al arrancar sigue dando acceso a **Improv Serial** (recovery in-situ por USB). El
  recovery **remoto** no existe como canal: es el **rollback de la OTA** (§2).

---

## 5. Energía y verificación post-reinstalación

Todo remoto y **SOLO lectura** (la BD la escribe unicamente la ingesta; no existe CLI `sqlite3` en
la distro). Con la placa energizada en el sitio:

1. **Telemetria fresca** (edad < 5 min; el umbral verde es `AULASENSE_SALUD_TELEMETRIA_MAX_MIN=5`):

   ```python
   # .tmp/estado_placa.py (misma URI mode=ro que §1.4)
   con.execute("SELECT MAX(ts_ms) FROM telemetria").fetchone()[0]   # comparar con now_ms
   ```

2. **Retains de estado** en `estado_sistema.detalle` (JSON cacheado por la ingesta):
   - `imagen_estado` → esperar `camara_ok=true`, `gate_off=false`, `captura_habilitada=true` y
     `build=f26.x` (la version que creias instalada).
   - `heap_interno` → publica cada 30 s los valores vivos `internal_free_kb` /
     `internal_largest_block_kb` (ver §6).
3. **Fotos subiendo con el transito real**: `SELECT MAX(id) FROM imagenes` debe crecer cuando hay
   movimiento en la zona.
4. **Evento `FIRMWARE` nuevo** tras energizar (la placa reporta su version al arrancar).
5. Los anillos con reintento **aterrizan aunque MQTT tarde**: si la placa arranco sin red, los
   eventos en cola suben al reconectar — no "perdiste" datos, llegaron despues.

Regla de campo para saber si la evidencia fotografica esta sana: **ratio onsets→fotos ≥ 80 %** en
ventanas con transito (los huecos los explica el cooldown de 15 s entre capturas). Si el ratio baja:

1. Revisar `descartes` en los retains y los eventos `GATE_OFF` / `DESCARTE` en `config_eventos`.
2. Solo entonces, y si hay acceso fisico, serial con `tools/lee_serial.py COM6` (USB-Serial-JTAG,
   **solo lectura, sin reset**). NUNCA `idf.py monitor` para sondear: hace reset de la placa.

---

## 6. Cámara, gate y heap: qué umbral es cuál

Tres umbrales distintos conviven en F26 — no confundirlos:

| Cosa | Umbrales (libre / largest, KB heap INTERNO) | Cuando aplica |
|---|---|---|
| **Gate de captura** (vivo) | **95 / 24** — free<95 o largest<24 → captura auto-deshabilitada | En CADA onset de motion; **sin latch** (re-evalua cada onset, histereis +20 KB para re-habilitar) |
| **Gate de OTA** (post-quiesce) | **96 / 48** | Tras pausar captura+telemetria, antes de descargar el bin |
| SPEC §16.1 originales | ~~120 / 64~~ **ENMENDADOS a 96/48** con evidencia | Motivo: en 200 muestras de banco NINGUNA alcanzo 120; post-quiesce medido 103/48 — con 120/64 la primera OTA por aire habria sido imposible (README del parche §14.2) |

Y el error de lectura mas facil de cometer: **el dato vivo es heap INTERNO**. En la tabla
`telemetria`, `free_memory_kb` ≈ 8.2 MB es **PSRAM** (no sirve para los gates). Los numeros utiles
son `internal_free_kb` / `internal_largest_block_kb` (del retain `heap_interno` o de
`imagen_estado`), en regimen de banco **103–106 / 56**. No asustarse al ver 8.2 MB: no es el heap
que consumen las capturas.

---

## 7. Mejoras post-entrega

Orden recomendado (cada una con su riesgo acotado):

1. **Subir la calidad JPEG** — `jpeg_quality` 12 → ~18–24 en `app_imagen.c:653`, desplegable **por
   OTA** (no toca el sensing CSI: el edge-first queda intacto). Cuello de botella REAL medido: la IA
   de decision ve degradada el 75 % de las fotos no por la decodificacion sino por el encuadre/calidad
   QVGA. **Topes de transporte a vigilar en la MISMA OTA**: el tope b64 de MQTT es 16.384 chars
   (~12 KB decodificados, `app_imagen.c:209`) y el receptor HTTP alternativo es ≤64 KB; los tamaños
   reales hoy (q12) son 3,2–7 KB — subir calidad los duplica, asi que hay que ajustar el tope junto
   con la calidad y **conservar el QC PIL** del servidor.
2. **TLS** para OTA y dashboard (nginx + Let's Encrypt): cierra la limitacion D3 (§8). Post-entrega
   por decicion del dueño.
3. **Rotacion de la pass SSH del VPS** — pendiente, accion humana: la gestiona la app local
   `wsl-port`, NO vive en el `.env` del proyecto.
4. **ACLs por topic en el broker** compartido (aislamiento real por prefijo, hoy es de convencion).

---

## 8. Limitaciones honestas

- **OTA y dashboard por HTTP sin TLS** (decision D3 del dueño, declarada). El tramo distro→VPS va
  cifrado por SSH (tuneles); el tramo VPS→placa es HTTP PLANO, **mitigado** porque el bin se verifica
  con SHA-256 y una imagen mala hace rollback automatico. Aun asi: un atacante en el trayecto puede
  denegar (no suplantar una imagen valida sin conocer el hash esperado... pero no hay firma).
  TLS post-entrega (§7.2).
- **La pass canonica del AP esta publicada en este manual** (`.env` + NOTES, orden explicita del
  dueño). Trade-off anotado: cualquiera fisicamente cerca puede provisionar LA RED de una placa; no
  obtiene acceso remoto alguno (el AP solo existe para provisioning local y una placa a la vez).
- **Ground truth fotografico = 0 en S2B/S3B** (camara caida en el sitio: `validacion_sesion2b.csv`
  y `validacion_sesion3b.csv` solo tienen cabeceras): esas sesiones sirven para estabilidad y
  distribucion, NO para recall/precision.
- **Precision no computable**: no hay marcas de idle con evidencia; se reporta recall-only (0,879
  con n=35, anclado a S1).
- **Clef sin GT de escena**: las 60 decisiones dan distribucion, no precision; el 73,3 % lleva badge
  REVISION_HUMANA (44/60) precisamente porque no hay verdad de terreno anotada.
- Los **escenarios E2–E5** del portal (caida real del router) quedan pendientes de ventana natural.

---

## 9. Lista "nunca"

Reglas duras de operacion (violarlas cuesta una UART y horas):

- **NUNCA abrir la UART** de la placa sin autorizacion explicita del orquestador. La unica UART de
  la historia F26 fue la "destraba" de f26.3, autorizada porque el gate 120/64 hacia la primera OTA
  por aire imposible. Para mirar serial: `tools/lee_serial.py COM6` (solo lectura, sin reset).
- **NUNCA escribir la BD** a mano (ni siquiera "un UPDATE pequeñito"): writer unico = ingesta.
  Sondear con `file:...?mode=ro` y el venv WSL; no existe CLI `sqlite3`.
- **NUNCA imprimir secretos** en logs, consolas ni reportes (ni `MQTT_PASS`, ni pass del VPS, ni
  tokens, ni la pass admin del dashboard). El unico valor escrito en docs es la pass canonica del
  AP, por orden del dueño y solo aqui.
- **NUNCA `idf.py monitor`** para sondear en remoto/recien energizada: resetea la placa.
- **NUNCA generar una segunda build fuera de la build unica F26** ni disparar `apply` sin haber
  pasado `check` + verificacion externa de sha.

---

## Anexo A — duraciones medidas

Todas medidas en F26 (banco y aire), utiles para saber si una operacion va "lenta o rota":

| Paso | Medido |
|---|---|
| `check` publicado → evento `OTA_CHECK` en BD | ~1 s |
| `apply` publicado → `OTA_INICIO` (pasa gate post-quiesce) | ~0,5 s |
| Gate post-quiesce (heap interno en banco) | medido 103 / 48 KB → pasa con 96/48 |
| Descarga del bin 1.418.720 B por el tunel | **32,2–35 s** (~34 s) |
| `OTA_OK` → reboot → evento `FIRMWARE` | 19–22 s (reboot ~20 s) |
| Self-check hasta validar | ~91–110 s (90 s estables + margen) |
| **E2E `apply` → placa validada de vuelta** | **~161–163 s** |
| **Rollback automatico (bin malo con sha valido)** | **225,2 s** sin humano |
| Agujero de telemetria esperado durante apply | **~30 s** |
| Sha alterado → `OTA_ERROR etapa=sha256` (sin reboot) | rechazo a los ~34 s |

Evidencia de referencia (`.tmp/`, gitignored): `f26_g1_ota.md` (OTA #1 por aire), `f26_g2_rollback.md`
(rollback con `f26.7-test` + sha alterado), `f26_g3_revival.md` (resurrecciones ×5),
`f26_c3_evidencia.md` (camera/gate/heap), `f26_clef_paso0.json` (medida Paso 0: 809 tokens /
$1,54e-5 / 747 ms), `f26_analisis.md` (regenerado F26-h3), `f26d_diagrama.md`.

## Anexo B — dónde vive cada credencial

Solo NOMBRES; los valores jamas van a un doc, un log ni un commit (excepto la pass canonica del AP,
por orden explicita del dueño, que esta en la §4):

| Credencial | Donde vive | Quien la genera |
|---|---|---|
| Pass canonica AP `AULASENSE-CFG` | `.env` raiz (`AULASENSE_PROVISION_AP_PASS`) + esta NOTES | Orden del dueño (valor fijo de curso) |
| Pass admin dashboard | `.env` raiz (`AULASENSE_ADMIN_PASSWORD`), unica raiz (D-F26-6) | Solo por codigo (`tools/reset_admin.py`) |
| `MQTT_PASS` / `MQTT_INGESTA_USER` | `.env` raiz | Dueño/proveedor del broker |
| Pass SSH del VPS | **App local `wsl-port`** — NO va al `.env` | Fuera del proyecto; rotacion pendiente (§7.3) |
| `DASHSCOPE_API_KEY` / `OPENROUTER_API_KEY` | `.env` raiz (pegadas por el dueño, canal local) | Proveedor |
| `AULASENSE_UPLOAD_TOKEN` (receptor LAN) | `.env` raiz | Solo por codigo (`gen_firmware_config.py`) |

El `.env` paso a ser **UNICO en la raiz**: `claves/` quedo archivado como `*.bak-f26`
(`tools/consolidar_env.py --unificar`). El loader lee siempre el de raiz.
