#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helper WSL del runner autonomo S2/S3 (tools/sesion_autonoma.py).

Tres subcomandos, todos SIN imprimir credenciales:

  maxid                     -> imprime MAX(id) de la tabla imagenes (BD ro)
  probe --base URL          -> login dashboard + 1 lectura API (exit 0/1)
  qc --desde N --salida RUTA --sesion L [--hasta N2]
                            -> QC PIL estricto de las fotos con id > N
                               (verificar + cargar, magic JPEG, tamano>0) y
                               escribe un informe markdown.

Convenciones AGENTS: la BD canonica es /home/aulasense/aulasense-datos/
aulasense.db, siempre SOLO-LECTURA por URI ``file:...?mode=ro`` (invariante
writer unico); credenciales leidas de /home/aulasense/aulasense/claves/.env
(nunca impresas). Cero llamadas a modelos de IA aqui (regla 13 ToS).
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

DB = "/home/aulasense/aulasense-datos/aulasense.db"
DATOS = Path("/home/aulasense/aulasense-datos")
CLAVES_ENV = "/home/aulasense/aulasense/claves/.env"


def conn_ro() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=15.0)
    con.row_factory = sqlite3.Row
    return con


def leer_credenciales() -> tuple[str, str]:
    txt = Path(CLAVES_ENV).read_text(encoding="utf-8", errors="replace")
    user = (re.search(r"^AULASENSE_ADMIN_USER=(.*)$", txt, re.M) or [None, "admin"])[1].strip() or "admin"
    m = re.search(r"^AULASENSE_ADMIN_PASSWORD=(.*)$", txt, re.M)
    if not m:
        raise SystemExit("no hay AULASENSE_ADMIN_PASSWORD en claves/.env")
    return user, m.group(1).strip()


class Api:
    """Cliente HTTP minimo del dashboard (mismo patron que f4_sesion_pasiva)."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.cookie: str | None = None

    def _req(self, ruta: str, payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        r = urllib.request.Request(self.base + ruta, data=data)
        if data is not None:
            r.add_header("Content-Type", "application/json")
        if self.cookie:
            r.add_header("Cookie", self.cookie)
        resp = urllib.request.urlopen(r, timeout=15)
        cuerpo = resp.read()
        sc = resp.headers.get("Set-Cookie")
        if sc:
            self.cookie = sc.split(";")[0]
        return json.loads(cuerpo.decode("utf-8"))

    def login(self, user: str, pwd: str) -> None:
        self._req("/login", {"username": user, "password": pwd})

    def get(self, ruta: str):
        return self._req(ruta)


def cmd_maxid(_: argparse.Namespace) -> int:
    con = conn_ro()
    try:
        fila = con.execute("SELECT MAX(id) AS m FROM imagenes").fetchone()
        print(int(fila["m"] or 0))
    finally:
        con.close()
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    try:
        user, pwd = leer_credenciales()
        api = Api(args.base)
        api.login(user, pwd)
        api.get("/api/evaluaciones?limite=1")
        print(f"probe OK {args.base} (login sin imprimir pass)")
        return 0
    except Exception as exc:  # noqa: BLE001 - el fallo se reporta como no-disponible
        print(f"probe FALLO {args.base}: {type(exc).__name__}: {exc}")
        return 1


def qc_una(ruta_abs: Path) -> tuple[bool, str]:
    """QC estricto de un JPEG: existe, tamano>0, magic, PIL verify() + load()."""
    try:
        crudo = ruta_abs.read_bytes()
    except FileNotFoundError:
        return False, "archivo no existe"
    except OSError as exc:
        return False, f"IOError: {exc}"
    if len(crudo) == 0:
        return False, "0 bytes"
    if crudo[:3] != b"\xff\xd8\xff":
        return False, "magic JPEG invalido"
    try:
        from PIL import Image

        with Image.open(io.BytesIO(crudo)) as im:
            im.verify()  # integridad de cabeceras/bloques
        with Image.open(io.BytesIO(crudo)) as im2:
            im2.load()  # decodificacion completa de pixeles
            dim = f"{im2.width}x{im2.height}"
        return True, dim
    except Exception as exc:  # noqa: BLE001 - toda excepcion PIL = foto rota
        return False, f"PIL: {type(exc).__name__}: {exc}"


def cmd_qc(args: argparse.Namespace) -> int:
    con = conn_ro()
    try:
        sql = "SELECT id, device_id, ts_ms, ruta, tamano_bytes, origen, canal, imagen_purgada FROM imagenes WHERE id > ?"
        params: list = [args.desde]
        if args.hasta is not None:
            sql += " AND id <= ?"
            params.append(args.hasta)
        filas = [dict(f) for f in con.execute(sql + " ORDER BY id", params)]
    finally:
        con.close()

    validas: list[str] = []
    rotas: list[str] = []
    purgadas: list[str] = []
    for f in filas:
        ruta_abs = DATOS / f["ruta"]
        if f["imagen_purgada"]:
            purgadas.append(f"id={f['id']} ({f['ruta']})")
            continue
        ok, detalle = qc_una(ruta_abs)
        linea = f"id={f['id']} {f['ruta']} {f['tamano_bytes']}B {detalle}"
        (validas if ok else rotas).append(linea)

    veredicto = "PASS" if not rotas else "FAIL"
    salida = Path(args.salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S %z")
    bloque = [
        f"## QC {args.sesion} — {ts}",
        "",
        f"- Rango QC: ids > {args.desde}" + (f" y <= {args.hasta}" if args.hasta is not None else ""),
        f"- Fotos nuevas en BD: **{len(filas)}** (validas {len(validas)} · rotas {len(rotas)} · purgadas {len(purgadas)})",
        f"- Criterio: rotas=0 → **{veredicto}**",
        "",
    ]
    if rotas:
        bloque += [
            "### ROTAS",
            *[f"- {r}" for r in rotas],
            "- Nota: con rotas>0 el criterio alternativo exige verificar que la placa esta subiendo",
            "  `IMAGEN_DESCARTE` (descarte firmware antes del upload). Verificacion requiere monitor",
            "  COM6, NO accesible desde el runner autonomo → reportar al orquestador.",
            "",
        ]
    if validas:
        bloque += ["### Validas", *[f"- {v}" for v in validas], ""]
    if purgadas:
        bloque += ["### Purgadas (fila conservada, dataset intacto)", *[f"- {p}" for p in purgadas], ""]

    texto = "\n".join(bloque) + "\n"
    # anexar (el runner escribe S2 y S3 en el mismo informe)
    with open(salida, "a", encoding="utf-8") as fh:
        fh.write(texto)
    print(texto)
    return 0 if veredicto == "PASS" else 3


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("maxid")
    p = sub.add_parser("probe")
    p.add_argument("--base", required=True)
    q = sub.add_parser("qc")
    q.add_argument("--desde", type=int, required=True)
    q.add_argument("--hasta", type=int, default=None)
    q.add_argument("--salida", required=True)
    q.add_argument("--sesion", required=True)
    args = ap.parse_args()
    return {"maxid": cmd_maxid, "probe": cmd_probe, "qc": cmd_qc}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
