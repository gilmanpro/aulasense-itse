# AulaSense en WSL Debian 13 — despliegue del servidor (F25, SPEC §15.1)

Runtime **primario** del servidor: distro `Debian` (13 trixie) de WSL2, systemd activo, venv linux
nativo, **sin Docker**. Windows nativo (`start_demo.ps1` / `server\.venv`) queda como **fallback**.
Nunca conviven los dos stacks a la vez: la ingesta es escritora única de la DB (split-brain, AGENTS.md
regla 3).

## Topología (SPEC §15.1 · D-F25-3)

```
  ESP32-S3 (CSI + OV3660) --MQTT/TCP--> broker.gilman.pro:14769 (compartido, prefijo aulasense/v1/devices)
                                            |
                                            v
  +-------------------------- WSL2 VM (NAT) ---------------------------+
  |  Debian 13 · systemd                                              |
  |  aulasense-ingesta.service  -> server/ingesta/ingesta_mqtt.py     |
  |        escritor SQLite + receptor HTTP :8001 (opt-in)             |
  |  aulasense-dashboard.service-> server/dashboard/app.py            |
  |        bind 0.0.0.0:8000  = VM-LOCAL (NAT sin portproxy)          |
  |  aulasense-vision.service   -> tools/vision_describe.py --loop     |
  |        DISABLED hasta F25-d2 (modo A: solo key sk-*)              |
  |  datos: /home/aulasense/aulasense-datos/ (ext4, WAL sano)         |
  +-------------------------------------------------------------------+
                       ^                    ^
      portproxy 127.0.0.1:8000      fallback http://<WSL_IP>:8000
      (requiere admin; cutover b2)  (sin admin; vía NAT de esta distro)
                                            |
                                       Windows (navegador)
```

> **Actualización 2026-11-15:** la vía externa **primaria** ya no es ninguna de las dos de este
> esquema: es el dashboard de la distro dedicada `debian-aulasense` (interno **:8100**) publicado en
> el VPS `http://167.114.169.134:10062` vía túnel SSH auto-start (CLI local, sin admin) — ver
> sección final. El portproxy `127.0.0.1:8000` sigue pendiente de admin y el fallback NAT :8000 de
> esta distro sigue vivo (sondeado 401 en `/api/eventos`, 2026-11-15).

Claves de la decisión:

- **`AULASENSE_HOST=0.0.0.0` dentro de la VM NO expone la LAN**: WSL2 en modo NAT solo es accesible
  desde el propio host Windows. La superficie expuesta en Windows sigue siendo **solo loopback**
  (portproxy) o la IP NAT (fallback), nunca `0.0.0.0` en el adaptador físico.
- **Origin guard intacto**: para el fallback, la unit fija
  `AULASENSE_ORIGINS_EXTRA=172.26.159.208:8000` (host[:puerto] exacto, sin wildcards). Si la WSL_IP
  cambia (tras reboot de la VM) hay que actualizar esa línea y `systemctl daemon-reload`.
  Referencia de la IP: `wsl -d Debian -- hostname -I` o `powershell tools\wsl_aulasense.ps1 status`.
- **`.wslconfig` global y las demás distros NO se tocan** (D-F25-3: 8 distros comparten la VM; el
  modo mirrored per-distro no existe, WSL#13706).
- **Datos**: la DB canónica del runtime primario vive en ext4 (`/home/aulasense/aulasense-datos/`),
  no en `/mnt/c` (rendimiento WAL/pytest sobre 9P). El código también se copia a ext4
  (`/home/aulasense/aulasense`), no se ejecuta desde `/mnt/c`.

## Preparación de la distro (una vez, como root)

```bash
wsl -d Debian -u root -- apt-get update
wsl -d Debian -u root -- apt-get install -y python3 python3-venv python3-pip rsync
python3 --version            # esperado: 3.13.x (trixie)

# Usuario dedicado SIN password (se invoca con `wsl -d Debian -u aulasense -- ...`)
adduser --disabled-password --gecos '' aulasense
mkdir -p /home/aulasense/aulasense-datos/imagenes

# Copia del proyecto a ext4 (excluye artefactos pesados/generados).
# F26 (D-F26-6): `--exclude '.env'` — el .env UNICO de la distro NO se pisa nunca con
# rsync: cada maquina gestiona el suyo (Windows: el del repo; WSL: /home/aulasense/
# aulasense/.env, chmod 600). Se JUBILA el workaround `sed` de la auditoria 2026-11-15.
rsync -a --delete \
  --exclude '.git' --exclude 'server/.venv' --exclude 'server/datos' --exclude '.tmp' \
  --exclude 'firmware' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'analysis/dataset' --exclude 'assets/video' --exclude '.env' \
  /mnt/c/Users/Gilman/Desktop/1-Universidad/itse/iot2/aulasense/ /home/aulasense/aulasense/

chown -R aulasense:aulasense /home/aulasense/aulasense /home/aulasense/aulasense-datos
chmod 600 /home/aulasense/aulasense/.env
# (F26: `claves/.env` ya NO es fuente: migrado al .env UNICO y archivado en
#  claves/*.bak-f26 — no hay segundo chmod que aplicar.)

# venv + dependencias linux (bleak fuera, argon2-cffi dentro)
sudo -u aulasense /usr/bin/python3 -m venv /home/aulasense/venvs/aulasense
sudo -u aulasense /home/aulasense/venvs/aulasense/bin/pip install \
  -r /home/aulasense/aulasense/server/requirements-linux.txt
```

Notas:

- `EnvironmentFile` de systemd **no expande `~`**: todas las rutas de las units son absolutas.
- El `.env` raíz (UNICO canónico desde F26, SPEC §16.8) se copia con permisos `600`;
  **nunca** se imprime su contenido (AGENTS.md regla 7) y el rsync **no lo pisa**
  (`--exclude '.env'`): las diferencias legítimas entre la copia Windows y la copia
  WSL (p. ej. `AULASENSE_UPLOAD_HOST`) se editan UNA vez a cada lado, por código o
  editor, sin imprimir valores.
- F26: las credenciales de auth/IA viven TAMBIÉN en el `.env` raíz (migradas byte-a-byte
  con `tools/consolidar_env.py --unificar`); `claves/.env` y `claves/.env-wsl-nuevo`
  quedaron archivados como `claves/*.bak-f26` (gitignored, solo lectura humana).

## Instalación de las units (como root)

```bash
cp /home/aulasense/aulasense/server/deploy/aulasense-*.service /etc/systemd/system/
systemctl daemon-reload

# INGESTA PRIMERO (es la escritora de la DB), después el dashboard.
systemctl enable --now aulasense-ingesta.service
systemctl enable --now aulasense-dashboard.service

# Vision: INSTALADA pero DISABLED — modo A daemon exige key sk-* (ToS); con sk-sp- (default)
# la guarda la rechaza y se usa modo B agente-mediado: tools/vision_describe.py --once.
systemctl disable aulasense-vision.service

systemctl is-active aulasense-ingesta.service aulasense-dashboard.service
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/login   # 200 dentro de la VM
#   (sonda /login, no /: desde F25-e '/' responde 302->/login sin cookie)
journalctl -u aulasense-ingesta -n 50 --no-pager
```

Desde Windows, sin escribir comandos a mano:

```powershell
powershell tools\wsl_aulasense.ps1 status
powershell tools\wsl_aulasense.ps1 start     # enable --now ingesta + dashboard
powershell tools\wsl_aulasense.ps1 logs aulasense-dashboard
powershell tools\wsl_aulasense.ps1 stop      # orden inverso: vision, dashboard, ingesta
```

El script usa `wsl.exe` **directo** porque `wsl-manager` no está en PATH en esta máquina
(fallback del troubleshooting de la skill `wsl-manager-cli`). No lee ni imprime `.env`.

## Exposición en Windows: dos vías

### Vía A — portproxy loopback (requiere admin; se usa en el cutover F25-b2)

Una única vez, en PowerShell **como Administrador**, con el **stack Windows parado** (el puerto
127.0.0.1:8000 lo ocupa el dashboard Windows mientras la demo está viva):

```powershell
netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=8000
netsh interface portproxy add    v4tov4 listenaddress=127.0.0.1 listenport=8000 connectaddress=<WSL_IP> connectport=8000
netsh interface portproxy show v4tov4
```

`<WSL_IP>` = `wsl -d Debian -- hostname -I` (en F25-b1: `172.26.159.208`; **cambia tras reboot**
→ reejecutar el bloque, verbo `refresh-portproxy`). Tras el cambio de IP, el navegador sigue usando
`http://127.0.0.1:8000` sin tocar la allowlist (es default).

### Vía B — fallback NAT sin admin (la activa hoy en F25-b1)

```powershell
Invoke-WebRequest http://172.26.159.208:8000/login -UseBasicParsing  # esperado 200
#   (sonda /login: '/' responde 302->/login desde F25-e; Invoke-WebRequest sigue el redirect de todos modos)
```

El navegador Windows apunta a `http://<WSL_IP>:8000`; el POST/WS funciona porque la unit lleva
`AULASENSE_ORIGINS_EXTRA=172.26.159.208:8000` (opt-in exacto). Tras reboot, actualizar ese valor en
`server/deploy/aulasense-dashboard.service` (o un drop-in) + `systemctl daemon-reload` + reiniciar el
dashboard.

Pendiente conocido: **sin admin no hay portproxy `127.0.0.1:8000`** → la vía local sigue siendo el
fallback NAT. La **vía primaria de acceso externo ya NO requiere admin**: la publicación VPS
`http://167.114.169.134:10062` de la distro `debian-aulasense` (sección final) corre por túnel SSH
auto-start del `wsl-port` **CLI local**, sin elevación.

**Receptor HTTP de imágenes (`:8001`): desactivado en el Debian original; RE-HABILITADO el
2026-11-15 en `debian-aulasense` (Fix F/bitácora F25f).** La copia `.env` del Debian original quedó
con `AULASENSE_UPLOAD_HOST=` (vacío) — con MQTT como canal primario (D-F25-1) no hacía falta y en
NAT bindear la IP LAN de Windows falla (`Errno 99`). En `debian-aulasense` se activó poniendo
`AULASENSE_UPLOAD_HOST=172.26.159.208` (bind de la IP NAT de la VM, exacto, sin comodín; la unit no
cambia — el receptor vive dentro de la ingesta) y se probó: `LISTEN 172.26.159.208:8001`,
fire-test POST → **201** (fila resultante marcada `vision_estado='omitido'`, no la consumen visión
ni análisis). **Caveat de ruteo LAN:** la IP NAT `172.26.*` NO es enrutable desde la LAN del aula
(192.168.40.x), así que el nodo con `CONFIG_AULASENSE_UPLOAD_HOST=172.26.159.208:8001` hoy SOLO
consume N2 desde dentro de la distro. Para N2 real hacia la placa hace falta, a decisión del dueño:
portproxy **admin** `listen=192.168.40.224:8001 → 172.26.159.208:8001` + regla de firewall, o
repunter ese CONFIG a la IP LAN del host en el siguiente ciclo de build. Nota adicional (mismo
fix): se corrigió un bug latente desde F24-b1 — sin el alias `sys.modules['ingesta_mqtt']`, el
receptor HTTP importaba una segunda copia del módulo y todo upload moraba ('writer no responde').

## Cutover F25-b2 (NO ejecutado aquí — lo decide el orquestador)

1. `powershell tools\stop_demo.ps1` (para ingesta/dashboard Windows; libera 127.0.0.1:8000 y la
   escritura de la DB).
2. Migrar/copiar `server/datos/aulasense.db` (y `imagenes/`) a `/home/aulasense/aulasense-datos/`
   si hace falta conservar histórico; si no, la ingesta WSL crea la DB vacía.
3. `powershell tools\wsl_aulasense.ps1 start` y verificar `is-active` (ingesta+dashboard; vision
   sigue disabled: el daemon modo A exige key pay-as-you-go `sk-*` — con `sk-sp-` la guarda ToS la
   rechaza y se usa modo B `tools/vision_describe.py --once`).
4. Portproxy como admin (vía A) → `http://127.0.0.1:8000` vuelve a funcionar desde el navegador.
5. Quitar el `AULASENSE_ORIGINS_EXTRA` si ya no se usa el fallback (opcional; no abre nada nuevo).

## Rollback a Windows

```powershell
powershell tools\wsl_aulasense.ps1 stop            # para las 3 units WSL
# 'stop' NO deshabilita el autoarranque: sin esto, las units habilitadas RESUCITAN al
# boot de la distro y Partirian la DB con start_demo.ps1 (split-brain, regla 3):
wsl -d Debian -u root -- systemctl disable aulasense-ingesta.service aulasense-dashboard.service
netsh interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=8000   # (admin)
powershell tools\start_demo.ps1                    # stack Windows de siempre
```

Nunca dejes los dos stacks escribiendo la DB a la vez. Si se migraron datos a ext4 y hay que
volver, copia `/home/aulasense/aulasense-datos/aulasense.db` a `server/datos/` **con la ingesta
WSL parada**.

## Desviaciones conscientes de F25-b1

- Código y datos en ext4 (`/home/aulasense/...`), no en `/mnt/c`: el orquestador priorizó el
  rendimiento de pytest/WAL frente a D-F25-2.
- `AULASENSE_HOST=0.0.0.0` es VM-local (SPEC §15.1); en Windows el default `127.0.0.1` no cambia.
- `aulasense-vision.service` instalada y **disabled**: el entrypoint existe (b99e185) pero el daemon
  modo A exige key `sk-*` (ToS Token Plan); con `sk-sp-` default la guarda la rechaza. El checklist
  pide `is-active` ×3 recién en b2.
- `AULASENSE_UPLOAD_HOST=` vacío en la copia `.env` del **Debian original** (receptor :8001 fuera de
  servicio ahí; ver sección de red). En `debian-aulasense` está **re-habilitado** desde 2026-11-15
  con bind NAT `172.26.159.208:8001` (caveat LAN arriba). El `.env` de Windows no se toca.
- `wsl-manager` ausente → `wsl.exe` directo (`tools/wsl_aulasense.ps1`).

## Distro dedicada `debian-aulasense` y publicación VPS (F25-WSL-NUEVO, 2026-11-15)

Además del Debian vivo de arriba (puerto 8000), existe una **distro WSL nueva** `debian-aulasense`
(Debian 13, importada con `wsl --import`) que corre el dashboard en el **puerto interno 8100** (no
8000, para convivir en el namespace NAT compartido). Se **publica en el VPS** en
`http://167.114.169.134:10062` vía túnel SSH auto-start de `wsl-port` **CLI local** (túnel
`pub-aulasense-8100`: `wsl-port tunnels add --distro debian-aulasense --local-port 8100 --remote-port 10062 --type ssh`). Verificada desde fuera: `/login` 200, `/api/eventos` 401 sin cookie, Origin público pasa. **No hace falta token de `wsl-port`** (la vía fue CLI local). Evidencia paso a paso en `aulasense/.tmp/f25wsl_nuevo.md` §P7 (persistencia/autoarranque) y §P8 (publicación VPS); comandos del túnel en `.tmp/f25wsl_tunel_pendiente.md`.

> **Fix systemd (hallazgo §P8):** en systemd, `EnvironmentFile` **pisa** el `Environment=` de la
> unit (man systemd.service: los valores del EnvironmentFile reemplazan `Environment=`). El `.env` de
> `debian-aulasense` (`/home/aulasense/aulasense/.env`) debe llevar
> `AULASENSE_ORIGINS_EXTRA=172.26.159.208:8100,167.114.169.134:10062` (si está vacío, todos los POST
> de navegador quedan en 403). Tras `wsl --shutdown`/reboot que cambie la IP NAT, **refrescar ese
> valor** (receta §P7) + `systemctl daemon-reload` + reiniciar el dashboard; el Origin público
> `167.114.169.134:10062` no depende de la IP NAT.

**Estado re-verificado 2026-11-15 (auditoría documental, sondeo HTTP directo):** `:10062` `/login`
200 + `/api/eventos` 401 sin cookie ✓; `:8100` NAT 401 ✓; `:8000` (Debian original) 401 ✓; receptor
N2 `172.26.159.208:8001` vivo (fire-test 201 de la sesión F25f) con caveat de ruteo LAN (§Receptor).
En esta distro la **BD canónica del pipeline** es `debian-aulasense:/home/aulasense/aulasense-datos/
aulasense.db` (F5a/visión/forense leen de ahí; queries solo-lectura con `python3` URI `mode=ro` — en
las distros no existe el CLI `sqlite3`).

> **`claves/.env-wsl-nuevo` (propósito aclarado, auditoría 2026-11-15):** guarda la password del
> usuario `aulasense` de esta distro nueva (claves `WSL_NEW_USER`/`WSL_NEW_PASS`), **generada por
> código** (`secrets.token_hex(16)`) el 2026-11-15 para el `wsl --import`; gitignored vía `claves/*`
> (regla 15) y el valor nunca se imprime. **No es un duplicado**: `claves/.env` solo lleva
> `AULASENSE_ADMIN_USER`/`AULASENSE_ADMIN_PASSWORD`/`DASHSCOPE_API_KEY` (credenciales distintas).

## OTA: servidor estático :8101 + segundo túnel (F26-b, SPEC §16.9 / D-F26-9)

La placa remota baja sus firmwares por HTTP **saliente** al puerto público del VPS;
el contenido se sirve desde un estático dentro de `debian-aulasense`. NO es ruta del
dashboard: el bin no se mezcla con la superficie de datos personales ni con el guard
de login (informe A §2.3B).

### Servidor estático (unit `aulasense-ota.service`)

```bash
# (root dentro de debian-aulasense)
mkdir -p /home/aulasense/aulasense-ota
chown aulasense:aulasense /home/aulasense/aulasense-ota
cp /home/aulasense/aulasense/server/deploy/aulasense-ota.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now aulasense-ota.service
systemctl is-active aulasense-ota.service          # active
systemd-analyze verify /etc/systemd/system/aulasense-ota.service   # sin errores
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8101/   # 200 (listing)
```

`ExecStart` usa el venv del proyecto (`python -m http.server 8101 --bind 0.0.0.0
--directory /home/aulasense/aulasense-ota`): bind `0.0.0.0` es **VM-local** (NAT sin
portproxy; SPEC §16.9 y verificación F26-b en vivo — el conector del túnel alcanza
el servicio por la IP NAT de la distro; con `--bind 127.0.0.1` el túnel devolvía
"empty reply"). Ningún puerto se abre en el adaptador físico de Windows ni en la LAN.
La unit es `no-root` (`User=aulasense`), `Restart=on-failure` y `ProtectHome=read-only`.
El `manifest.json` y el `firmware-<ver>.bin` los publica el ejecutor POR CÓDIGO
(sha256/size reales) al cerrar g1; los bins son gitignored.

### Túnel `pub-aulasense-ota` (CLI local `wsl-port`, sin token)

Puerto público **validado antes de crear** (panel https://puertos-disponibles.gilman.pro/
+ `wsl-port tunnels list`; candidato 10064, precedente 10061-10063; la API "available"
devuelve rangos aleatorios: no fiable). NUNCA pisar túneles ajenos:

```powershell
$wp = "C:\Users\Gilman\Desktop\APPplicaciones\05-WSL-Redes\wsl-port\wsl+ssh\wsl-port-unified\.venv\Scripts\wsl-port.exe"
& $wp tunnels add --id pub-aulasense-ota --vps "vps1 de canada" --distro debian-aulasense `
    --local-port 8101 --remote-port 10064 --type ssh
& $wp tunnels update pub-aulasense-ota --auto-start true   # persistencia (solo en update)
& $wp tunnels start pub-aulasense-ota
```

Verificación externa (Windows/otred): `http://167.114.169.134:10064/probe.txt` →
HTTP 200 + hash idéntico. Riesgo declarado (D3 del dueño): el tramo VPS→placa es
HTTP plano sin TLS; lo mitigan SHA-256 + rollback del firmware (§16.1); TLS nginx+LE
queda post-entrega. El tramo distro→VPS va cifrado por SSH.

**Estado real de operación (F26-f1, 2026-11-17):** el canal OTA está **demostrado E2E por aire**
(f26.4→f26.5→f26.6; descarga ~34 s, agujero de telemetría 30 s esperado, rollback automático
probado en 225 s con bin malo) y la placa vive corre **f26.6 válida**. El manifiesto vigente apunta
al bin real (`manifest.json` → 200 verificado externamente en g1); gate OTA **96/48 KB
post-quiesce** (enmienda con evidencia sobre los 120/64 contractuales — SPEC §16.1, README del
parche §14.2). Pasos operativos, verificaciones en BD ro y re-provisionamiento en sitio:
[`../../docs/NOTES_operacion.md`](../../docs/NOTES_operacion.md).

### Verbo `salud` (Windows, F26)

```powershell
powershell tools\wsl_aulasense.ps1 salud    # usa debian-aulasense por defecto
```

Agrega: `is-active` de ingesta/dashboard/vision/**ota**, sesión local vía `/login`
(credenciales del `.env` UNICO, jamás impresas) + `GET /api/salud`, sondas `/login`
(200) y `/api/eventos` (401) locales y públicas, sonda al manifiesto OTA y estado de
túneles vía `wsl-port --json status` extrayendo **SOLO `id`/`state`/`remote`/
`auto_start`** de `pub-aulasense-8100` y `pub-aulasense-ota` (el objeto completo
contiene datos de acceso: NUNCA se imprime ni se serializa — lección pass VPS).
Escribe `server/datos/tuneles_estado.json` (`{ts_ms, tuneles:[{id,state,remote,ts_ms}]}`)
que el panel `/salud` lee con frescura (ausente/stale → gris "sin datos", D-F26-8).

## Perímetro de la publicación VPS `:10062` — nota formal de riesgo (auditoría 2026-11-15)

**Los hechos:** el dashboard de `debian-aulasense` (interno `:8100`) se publica en
`http://167.114.169.134:10062` vía el túnel SSH auto-start de `wsl-port`. El túnel cifra el tramo
distro→VPS, pero el tramo **navegador→VPS es HTTP plano sin TLS**. Consecuencias honestas:

1. **Credenciales y sesión viajan en claro en el último tramo:** un observador entre el cliente y
   el VPS ve el POST `/login` (password) y puede reutilizar la cookie de sesión robada. El lockout
   progresivo y los 12 h/2 h de sesión limitan, no eliminan, la ventana.
2. **Las imágenes de evidencia (datos personales del aula, regla 2) se sirven en claro** en ese tramo.
3. Mitigaciones ya vigentes a nivel de aplicación: login obligatorio en todas las rutas salvo
   `/login|/logout| estáticos|/internal/*`, Origin guard exacto (sin wildcards),
   `X-Content-Type-Options`, cookie HttpOnly+SameSite=Lax. **Ninguna de ellas cifra la red.**
4. El patrón es el mismo del resto de servicios del VPS (HTTP:puerto alto, demo docente acotada en
   el tiempo); el dueño conocía el diseño al ordenar la publicación (F25-WSL-NUEVO).

**Mitigables SIN admin de Windows:**

- **Cerrar el bind NAT del :8100 (recomendada, solo root dentro de `debian-aulasense`):** con bind
  `0.0.0.0`, el `:8100` es alcanzable **desde las otras distros de la VM** (namespace NAT
  compartido). El túnel `wsl-port` corre DENTRO de `debian-aulasense` y conecta a `localhost:8100`,
  así que basta con fijar en su `/home/aulasense/aulasense/.env` (el EnvironmentFile pisa a la
  unit — §P8):

  ```bash
  # verificar ANTES de cambiar que el ssh del túnel vive en la misma distro:
  wsl -d debian-aulasense -u root -- ss -tanp | grep :8100   # esperado: LISTEN en 127.0.0.1 y el ssh del tunel local
  # en /home/aulasense/aulasense/.env:  AULASENSE_HOST=127.0.0.1   (y mantener ORIGINS_EXTRA de §P8)
  wsl -d debian-aulasense -u root -- systemctl restart aulasense-dashboard.service
  ```

  Superficie resultante: loopback de la distro + `127.0.0.1:10062` en el VPS + lo que exponga el
  sshd del túnel. Si el `ss -tnp` mostrase conexiones al `:8100` **desde fuera de la distro**, NO
  aplicar (el túnel iría por la IP NAT) y dejar `0.0.0.0`.
- **TLS end-to-end** exige trabajo en el VPS (cert + reverse proxy TLS o `ssh -L` manual por
  visitante): fuera del alcance de esta auditoría (no se toca infra remota). Pendiente humano si el
  contenido del aula deja de ser demo corta.
- **Higiene obligatoria mientras :10062 esté HTTP:** tras cualquier uso real del login público,
  `tools/reset_admin.py` (rota la password en el `.env` ÚNICO raíz — F26 §16.8: ya NO en
  `claves/.env` —, nunca la imprime) y vigilar
  `journalctl -u aulasense-dashboard` por 401/429 anómalos.

**Receptores internos (repaso de binds, mismo criterio):**

| Puerto | Qué | Bind actual | Regla |
|---|---|---|---|
| 8000 | dashboard Debian clásico | `0.0.0.0` VM-local | OK: NAT sin portproxy LAN = inalcanzable desde fuera; exposición Windows solo `127.0.0.1` (vía A) o `WSL_IP:8000` exacto (vía B + `ORIGINS_EXTRA`) |
| 8001 | receptor HTTP imágenes (ingesta) | **Debian original: desactivado** (`.env` con `AULASENSE_UPLOAD_HOST=` vacío); **`debian-aulasense`: bind NAT exacto `172.26.159.208:8001`** (re-habilitado 2026-11-15 — caveat de ruteo LAN en §Receptor); en el `.env` de Windows: IP **exacta** en la sección UPLOAD_HTTP (sin número de línea fijo: el `.env` no se commitea y las líneas rotan) | NUNCA `0.0.0.0`: el código lo rechaza (`receptor_imagen.py:248-249`); si algún día se activa en WSL, IP exacta + portproxy/firewall opt-in (admin) |
| 8100 | dashboard debian-aulasense | `0.0.0.0` VM-local + túnel → VPS `:10062` | Ver mitigación `AULASENSE_HOST=127.0.0.1` arriba |

**El journal no filtra secretos (verificado en código, línea a línea):**

- `ExecStart` de las units: solo rutas + `--loop`; **ningún token/password por argv**. Todos los
  secretos llegan por `EnvironmentFile` (el `.env` UNICO de la raiz, `chmod 600` en la preparación §
  "Preparación de la distro" — desde F26 ya no existe segundo archivo: `claves/*.bak-f26` es solo
  archivo histórico).
- MQTT_PASS: `field(repr=False)` y nunca se loguea (`ingesta_mqtt.py:259,286`); el receptor HTTP
  loggea `len(token)` jamás el valor (`receptor_imagen.py:544-549`).
- `journalctl` del arrancador: los logs de acceso del dashboard (uvicorn) y del receptor
  (`BaseHTTPRequestHandler.log_message`) registran **método + ruta + código**, no headers ni
  cuerpos → el Bearer token (cabecera) y la password (cuerpo del POST /login) NO entran al journal.
  El query string sí se loggea: por `/internal/imagen` viaja metadata no sensible (`device_id`,
  `ts_ms`); NUNCA pongas secretos en query params.
- `tools/wsl_aulasense.ps1` (verbo `logs`) ejecuta `journalctl` **como root** (necesario:
  `aulasense` no está en `systemd-journal`) pero desde la auditoría solo acepta las 3 units de
  AulaSense (allowlist; antes admitía cualquier unidad del sistema como root).

## Receta de validación local (no existe CI: sin repo remoto — NO montar pipeline)

Equivalente al gate que tendría un pipeline; ejecutar tras tocar units/ps1/código:

```powershell
# 1) Units válidas + hardening presente (dentro de la distro, root, solo lectura):
wsl -d Debian -u root -- systemd-analyze verify /home/aulasense/aulasense/server/deploy/aulasense-ingesta.service /home/aulasense/aulasense/server/deploy/aulasense-dashboard.service /home/aulasense/aulasense/server/deploy/aulasense-vision.service   # silencioso = OK
wsl -d Debian -u root -- sh -c 'for u in ingesta dashboard vision; do echo "== $u"; systemctl show -p NoNewPrivileges,ProtectSystem,PrivateTmp,RestrictSUIDSGID,MemoryMax,CPUQuota /home/aulasense/aulasense/server/deploy/aulasense-$u.service; done'

# 2) ps1 parsea sin tocar WSL (Get-Command carga el parser):
powershell -NoProfile -Command "$null = Get-Command 'tools\wsl_aulasense.ps1'"

# 3) Suite verde en AMBOS runtimes (regla 3; última medida completa F26-a3 2026-11-17:
#    407 passed + 1 skipped en Windows py3.15 y WSL; los desgloses parciales de fases previas
#    p. ej. 178 server + 18 analysis quedaron superados):
wsl -d Debian -- sh -c 'cd /home/aulasense/aulasense && ~/venvs/aulasense/bin/python -m pytest server/tests analysis/tests -q'
server\.venv\Scripts\python.exe -m pytest server/tests analysis/tests -q   # fallback Windows
powershell tools\wsl_aulasense.ps1 status    # humo: 3 states + URL; start/stop re-verifican active
```

Nota: `systemd-analyze verify` y `Get-Command` validan sintaxis/carga; las directrices nuevas de
hardening (`ProtectSystem=full`, etc.) no tocan `/home/aulasense/**` (los binds) ni `/proc`, así que
son compatibles con la DB en ext4 y el `.env` por EnvironmentFile. `ProtectHome` quedó fuera a
propósito (rompería WorkingDirectory/Datos). `RestrictAddressFamilies` quedó fuera como mejora
futura: requiere prueba en vivo (DNS/AF_INET) que esta sesión no pudo ejecutar (ver limitaciones
en el informe del auditor).

## Receta de downgrade F26 (gate F26-bd MEDIO-2, 2026-11-17)

F26 es **100 % aditiva** sobre la BD: ninguna columna ni tabla existente cambia de tipo o de nombre.

| Cambio F26 | Naturaleza |
|---|---|
| `imagenes.decision_estado` … `decision_actividad` (9 columnas) | solo `ADD COLUMN`; `decision_estado` lleva `DEFAULT 'pendiente'` (las filas viejas quedan válidas) |
| tablas `clef_uso` y `estado_sistema` | nuevas (`IF NOT EXISTS`) |
| índice `idx_img_decision_estado` | nuevo |
| `telemetria.internal_free_kb` / `internal_largest_block_kb` | solo `ADD COLUMN`, NULL en filas históricas |
| `config_eventos.tipo` | CHECK **ampliado** (R8, reconstrucción con el enum de 20 tipos) |

Consecuencia práctica: **volver el CÓDIGO a la build F25 no exige tocar la BD** — el código viejo
ignora las columnas/tablas de más. El procedimiento de abajo solo aplica si se quiere dejar la BD
**equivalente a F25** (entrega, copia forense o downgrade de versión documentado). Dos avisos:

1. **Para el downgrade de esquema hay que desplegar además el código F25**: con el código F26 vivo,
   `_migrar_f26_respaldo` (dashboard) y `migrar_esquema_f26` (ingesta) **recrean** columnas, tablas e
   índice en el primer arranque/request.
2. **Nunca sobre la BD viva**: se trabaja sobre una COPIA y con ingesta/dashboard **parados**
   (invariante de writer único, AGENTS.md regla 3). La viva se reemplaza solo por `mv` atómico.

### 0. Parar servicios y copiar con `sqlite3.backup` (nunca `cp`)

```bash
wsl -d debian-aulasense -u aulasense -- sh -c '
  systemctl --user stop aulasense-ingesta aulasense-dashboard 2>/dev/null || true
  mkdir -p /home/aulasense/aulasense-datos/downgrade-f26
  ~/venvs/aulasense/bin/python - <<"PY"
import sqlite3
origen = sqlite3.connect("file:/home/aulasense/aulasense-datos/aulasense.db?mode=ro", uri=True)
destino = sqlite3.connect("/home/aulasense/aulasense-datos/downgrade-f26/aulasense_f25.db")
origen.backup(destino)          # copia consistente con WAL (no "cp", no "sqlite3 .backup")
destino.execute("PRAGMA integrity_check").fetchall()
destino.close(); origen.close()
print("copia OK")
PY'
```

`sqlite3.Connection.backup()` congela un snapshot consistente aunque queden lectores; el `cp` de un
`.db` en WAL puede llevarse un fichero sin su `-wal`.

### 1. Reconstruir `imagenes` sin las columnas `decision_*`

`ALTER TABLE … DROP COLUMN` **no sirve** aquí: `decision_estado` participa en un `CHECK` y SQLite
rechaza el drop. Se reconstruye la tabla (mismo patrón que la migración R8) sobre la copia:

```sql
-- Sobre downgrade-f26/aulasense_f25.db (writer único: solo este proceso).
BEGIN IMMEDIATE;
ALTER TABLE imagenes RENAME TO imagenes_f26;
CREATE TABLE imagenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  ruta TEXT NOT NULL,
  hash_sha256 TEXT NOT NULL,
  tamano_bytes INTEGER NOT NULL,
  ancho INTEGER, alto INTEGER,
  evento_id INTEGER,
  ia_persona INTEGER,
  ia_score REAL,
  imagen_purgada INTEGER NOT NULL DEFAULT 0,
  origen TEXT NOT NULL DEFAULT 'real' CHECK(origen IN ('real','sintetico')),
  recibido_en TEXT NOT NULL DEFAULT (datetime('now')),
  canal TEXT NOT NULL DEFAULT 'http' CHECK(canal IN ('http','mqtt')),
  descripcion TEXT,
  etiquetas TEXT,
  vision_estado TEXT NOT NULL DEFAULT 'pendiente'
    CHECK(vision_estado IN ('pendiente','procesando','hecho','error','omitido')),
  vision_modelo TEXT,
  vision_ts_ms INTEGER,
  vision_tokens INTEGER,
  FOREIGN KEY(evento_id) REFERENCES eventos(id)
);
INSERT INTO imagenes (
  id, device_id, ts_ms, ruta, hash_sha256, tamano_bytes, ancho, alto, evento_id,
  ia_persona, ia_score, imagen_purgada, origen, recibido_en, canal, descripcion,
  etiquetas, vision_estado, vision_modelo, vision_ts_ms, vision_tokens)
SELECT
  id, device_id, ts_ms, ruta, hash_sha256, tamano_bytes, ancho, alto, evento_id,
  ia_persona, ia_score, imagen_purgada, origen, recibido_en, canal, descripcion,
  etiquetas, vision_estado, vision_modelo, vision_ts_ms, vision_tokens
FROM imagenes_f26;
DROP TABLE imagenes_f26;           -- se lleva consigo los índices de la tabla vieja
-- Los índices NO sobreviven al DROP: se recrean los canónicos de F25 (nombres exactos).
CREATE UNIQUE INDEX IF NOT EXISTS idx_img_hash_unico ON imagenes(hash_sha256);
CREATE INDEX IF NOT EXISTS idx_img_dev_ts ON imagenes(device_id, ts_ms);
CREATE INDEX IF NOT EXISTS idx_img_evento ON imagenes(evento_id);
CREATE INDEX IF NOT EXISTS idx_img_purgada ON imagenes(imagen_purgada);
CREATE INDEX IF NOT EXISTS idx_img_vision_estado ON imagenes(vision_estado);
CREATE INDEX IF NOT EXISTS idx_img_canal ON imagenes(canal);
COMMIT;
```

Se pierde la metadata de decisión Clef (`decision_etiqueta`, `_confianza`, `_cubre`…): es derivable
volviendo a pasar `clef.py` sobre las mismas imágenes, no un dato primario.

### 2. Quitar tablas e índice F26

```sql
BEGIN IMMEDIATE;
DROP INDEX IF EXISTS idx_img_decision_estado;  -- idempotente (ya cayó con el DROP de la tabla)
DROP TABLE IF EXISTS clef_uso;                 -- auditoría de Credits (no la lee el código F25)
DROP TABLE IF EXISTS estado_sistema;           -- heartbeat F26 (tabla nueva)
-- Opcional, solo para equivalencia byte a byte: las dos columnas de heap interno.
-- OJO: elimina la serie histórica de heap INTERNO (hoy es la única fuente de esa métrica).
-- ALTER TABLE telemetria DROP COLUMN internal_free_kb;
-- ALTER TABLE telemetria DROP COLUMN internal_largest_block_kb;
COMMIT;
```

### 3. `config_eventos` (R8): no hay nada que revertir

La reconstrucción R8 **no se invierte a propósito**: el enum resultante es un superconjunto del F25 y
los ids se conservan, así que el código F25 (que solo emite los tipos viejos) escribe sin problema.
Volver a subir a F26 no vuelve a reconstruir: `_migrar_config_eventos_enum` detecta el enum vigente y
sale sin tocar nada (idempotente). Desde el gate F26-bd, la reconstrucción va en una transacción
`BEGIN IMMEDIATE … COMMIT`, de modo que un corte deja o la tabla vieja o la nueva completa — nunca una
`config_eventos_legacy` huérfana (y si la hubiera de una versión anterior, se limpia al arrancar).

### 4. Verificar la copia antes de publicarla

```bash
wsl -d debian-aulasense -u aulasense -- /home/aulasense/venvs/aulasense/bin/python - <<"PY"
import sqlite3
con = sqlite3.connect(
    "file:/home/aulasense/aulasense-datos/downgrade-f26/aulasense_f25.db?mode=ro", uri=True)
cols = {f[1] for f in con.execute("PRAGMA table_info(imagenes)")}
assert not any(c.startswith("decision_") for c in cols), sorted(cols)
tablas = {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
assert "clef_uso" not in tablas and "estado_sistema" not in tablas
idx = {f[1] for f in con.execute("PRAGMA index_list(imagenes)")}
assert {"idx_img_hash_unico", "idx_img_vision_estado"} <= idx, idx
print("filas:", con.execute("SELECT COUNT(*) FROM imagenes").fetchone()[0],
      "| integridad:", con.execute("PRAGMA integrity_check").fetchone()[0])
print("OK downgrade F25")
PY'
```

Publicación (solo con los servicios parados): renombrar la viva a `aulasense.db.bak-f26`, mover la
copia a `aulasense.db` (mismo dueño `aulasense:aulasense`, `chmod 640`) y volver a arrancar el código
F25. El `.bak-f26` es el único camino de retorno con las decisiones Clef intactas.