# AulaSense en WSL Debian 13 — despliegue del servidor (F25, SPEC §15.1)

Runtime **primario** del servidor: distro `Debian` (13 trixie) de WSL2, systemd activo, venv linux
nativo, **sin Docker**. Windows nativo (`start_demo.ps1` / `server\.venv`) queda como **fallback**.
Nunca conviven los dos stacks a la vez: la ingesta es escritora única de la DB (split-brain, AGENTS.md
regla 3).

## Topología (SPEC §15.1 · D-F25-3)

```
  ESP32-S3 (CSI + OV3660) --MQTT/TLS--> broker.gilman.pro:14769 (compartido, prefijo aulasense/v1)
                                            |
                                            v
  +-------------------------- WSL2 VM (NAT) ---------------------------+
  |  Debian 13 · systemd                                              |
  |  aulasense-ingesta.service  -> server/ingesta/ingesta_mqtt.py     |
  |        escritor SQLite + receptor HTTP :8001 (opt-in)             |
  |  aulasense-dashboard.service-> server/dashboard/app.py            |
  |        bind 0.0.0.0:8000  = VM-LOCAL (NAT sin portproxy)          |
  |  aulasense-vision.service   -> tools/vision_describe.py --daemon   |
  |        DISABLED hasta F25-d2 (modo A: solo key sk-*)              |
  |  datos: /home/aulasense/aulasense-datos/ (ext4, WAL sano)         |
  +-------------------------------------------------------------------+
                       ^                    ^
      portproxy 127.0.0.1:8000      fallback http://<WSL_IP>:8000
      (requiere admin; cutover b2)  (sin admin; via activa en b1)
                                            |
                                       Windows (navegador)
```

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

# Copia del proyecto a ext4 (excluye artefactos pesados/generados)
rsync -a --delete \
  --exclude '.git' --exclude 'server/.venv' --exclude 'server/datos' --exclude '.tmp' \
  --exclude 'firmware' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude 'analysis/dataset' --exclude 'assets/video' \
  /mnt/c/Users/Gilman/Desktop/1-Universidad/itse/iot2/aulasense/ /home/aulasense/aulasense/

chown -R aulasense:aulasense /home/aulasense/aulasense /home/aulasense/aulasense-datos
chmod 600 /home/aulasense/aulasense/.env
[ -f /home/aulasense/aulasense/claves/.env ] && chmod 600 /home/aulasense/aulasense/claves/.env

# venv + dependencias linux (bleak fuera, argon2-cffi dentro)
sudo -u aulasense /usr/bin/python3 -m venv /home/aulasense/venvs/aulasense
sudo -u aulasense /home/aulasense/venvs/aulasense/bin/pip install \
  -r /home/aulasense/aulasense/server/requirements-linux.txt
```

Notas:

- `EnvironmentFile` de systemd **no expande `~`**: todas las rutas de las units son absolutas.
- El `.env` raíz se copia con permisos `600`; **nunca** se imprime su contenido (AGENTS.md regla 7).
- Las claves de auth/IA viven en `claves/.env` (gitignored, generado por código en F25-e).

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

Pendiente conocido: **sin admin no hay portproxy** → en b1 la vía oficial sigue siendo el fallback.

**Receptor HTTP de imágenes (`:8001`) DESACTIVADO en WSL:** la copia `.env` de WSL quedó con
`AULASENSE_UPLOAD_HOST=` (vacío). Con MQTT como canal primario (D-F25-1) el canal LAN→WSL no hace
falta; en NAT además sería imposible bindear la IP LAN de Windows (falla con `Errno 99`). Si el dueño
lo quiere activar algún día: portproxy opt-in sobre la IP LAN + regla de firewall (D-F25-3), no aquí.

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
- `AULASENSE_UPLOAD_HOST=` vaciado en la **copia WSL** del `.env` (receptor :8001 fuera de servicio
  en NAT; ver sección de red arriba). El `.env` de Windows no se toca.
- `wsl-manager` ausente → `wsl.exe` directo (`tools/wsl_aulasense.ps1`).