# Units del stack de la distro WSL dedicada `debian-aulasense` (:8100)

Copia VERBATIM (2026-11-15, `cmp`/sha256 verificado) de `/etc/systemd/system/` de la distro
`debian-aulasense`: ingesta MQTT (escritor unico SQLite + receptor HTTP :8001 opt-in), dashboard
FastAPI (:8100 interno, publicado al VPS por el tunel SSH `http://167.114.169.134:10062`) y worker
de vision IA (instalado pero DISABLED: solo es legal con key pay-as-you-go `sk-*`). NO son las units
del stack :8000 (`../aulasense-*.service`, WSL `Debian`/Windows nativo): `aulasense-ingesta` y
`aulasense-vision` son byte-identicas a aquellas (mismo endurecimiento, sin puerto propio), y solo
`aulasense-dashboard` difiere (`AULASENSE_PORT=8100` + `AULASENSE_ORIGINS_EXTRA` con `:8100` y `:10062`).
Instalar (root DENTRO de `debian-aulasense`): `cp aulasense-*.service /etc/systemd/system/ &&
systemctl daemon-reload && systemctl enable --now aulasense-ingesta.service aulasense-dashboard.service`.
Codigo en `/home/aulasense/aulasense`, datos en `/home/aulasense/aulasense-datos`; esta ingesta es
escritora unica de la DB: NUNCA debe correr a la vez que la del stack :8000 (AGENTS.md regla 3).