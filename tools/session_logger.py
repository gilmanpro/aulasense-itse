#!/usr/bin/env python
"""Session logger de AulaSense (F3): registra sesiones de captura en SQLite.

Escribe la tabla ``sesiones`` (extension aditiva de SPEC §8, definida en
``server/ingesta/esquema.py``). El dashboard lee esa tabla en ``GET
/api/validacion`` para etiquetar las ventanas de verdad terreno por sesion y F4
puede cruzarla con ``evaluaciones.sesion_id`` por ventana temporal.

Uso (SIEMPRE con el python del venv ``server\\.venv``):

    server\\.venv\\Scripts\\python.exe tools\\session_logger.py start --id S1 --nota "aula con 2 personas"
    server\\.venv\\Scripts\\python.exe tools\\session_logger.py list
    server\\.venv\\Scripts\\python.exe tools\\session_logger.py stop  --id S1

Reglas duras del proyecto:
- DB compartida por convencion ``AULASENSE_DB`` (default ``server/datos/aulasense.db``).
- SQL parametrizado; DDL idempotente (``CREATE IF NOT EXISTS``) antes de escribir.
- Nunca imprime credenciales (aqui no hay ninguna).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Raiz del repo (tools/session_logger.py -> aulasense/).
_RAIZ_REPO = Path(__file__).resolve().parents[1]
_INGESTA_DIR = _RAIZ_REPO / "server" / "ingesta"
if str(_INGESTA_DIR) not in sys.path:
    # Mismo patron que server/dashboard/app.py: el proyecto no es paquete instalable.
    sys.path.insert(0, str(_INGESTA_DIR))

import esquema  # noqa: E402

_DB_DEFECTO = _RAIZ_REPO / "server" / "datos" / "aulasense.db"
_RE_SESION = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
_NOTA_MAX = 200


class ErrorSesion(Exception):
    """Error de dominio del session logger (id invalido, duplicado, no activa)."""


def ahora_ms() -> int:
    """Epoca del PC en milisegundos (misma convencion que la ingesta)."""
    return int(time.time() * 1000)


def ruta_db(db_path: str | Path | None = None) -> Path:
    """DB compartida: argumento explicito > env ``AULASENSE_DB`` > default."""
    if db_path is not None and str(db_path).strip():
        return Path(db_path).expanduser()
    crudo = os.environ.get("AULASENSE_DB", "").strip()
    if crudo:
        return Path(crudo).expanduser()
    return _DB_DEFECTO


def _conectar(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Abre la DB en WAL y garantiza el esquema (idempotente)."""
    ruta = ruta_db(db_path)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(ruta), timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(esquema.ESQUEMA_SQL)
    con.commit()
    return con


def validar_sesion(sesion: str) -> str:
    """Valida el id de sesion (alfanumerico + ``._-``, 1-32 chars)."""
    texto = (sesion or "").strip()
    if _RE_SESION.fullmatch(texto) is None:
        raise ErrorSesion(
            "id de sesion invalido: usa 1-32 caracteres [A-Za-z0-9._-]"
        )
    return texto


def _normalizar_nota(nota: str | None) -> str | None:
    if nota is None:
        return None
    texto = str(nota).strip()
    if len(texto) > _NOTA_MAX:
        raise ErrorSesion(f"nota supera {_NOTA_MAX} caracteres")
    return texto or None


def _fila_a_dict(fila: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(fila) if fila is not None else None


# --- API importable (la consume F4 y los tests) -----------------------------


def iniciar_sesion(
    sesion: str,
    nota: str | None = None,
    device_id: str | None = None,
    db_path: str | Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Abre una sesion activa. Rechaza duplicados y sesiones ya activas."""
    nombre = validar_sesion(sesion)
    texto_nota = _normalizar_nota(nota)
    inicio = int(now_ms) if now_ms is not None else ahora_ms()
    con = _conectar(db_path)
    try:
        with con:
            activa = con.execute(
                "SELECT sesion FROM sesiones WHERE estado = 'activa' "
                "ORDER BY inicio_ms DESC LIMIT 1"
            ).fetchone()
            if activa is not None:
                raise ErrorSesion(
                    f"ya hay una sesion activa: {activa['sesion']} (detenla primero)"
                )
            try:
                cursor = con.execute(
                    "INSERT INTO sesiones (sesion, inicio_ms, estado, device_id, nota) "
                    "VALUES (?, ?, 'activa', ?, ?)",
                    (nombre, inicio, device_id, texto_nota),
                )
            except sqlite3.IntegrityError as exc:
                raise ErrorSesion(f"la sesion {nombre} ya existe") from exc
            fila = con.execute(
                "SELECT * FROM sesiones WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return _fila_a_dict(fila) or {}
    finally:
        con.close()


def detener_sesion(
    sesion: str,
    nota: str | None = None,
    db_path: str | Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Cierra una sesion activa. Error si no existe o ya estaba cerrada."""
    nombre = validar_sesion(sesion)
    texto_nota = _normalizar_nota(nota)
    fin = int(now_ms) if now_ms is not None else ahora_ms()
    con = _conectar(db_path)
    try:
        with con:
            fila = con.execute(
                "SELECT * FROM sesiones WHERE sesion = ?", (nombre,)
            ).fetchone()
            if fila is None:
                raise ErrorSesion(f"la sesion {nombre} no existe")
            if fila["estado"] != "activa":
                raise ErrorSesion(f"la sesion {nombre} ya estaba cerrada")
            con.execute(
                "UPDATE sesiones SET fin_ms = ?, estado = 'cerrada', "
                "nota = COALESCE(?, nota) WHERE id = ?",
                (max(int(fila["inicio_ms"]), fin), texto_nota, int(fila["id"])),
            )
            actualizada = con.execute(
                "SELECT * FROM sesiones WHERE id = ?", (int(fila["id"]),)
            ).fetchone()
        return _fila_a_dict(actualizada) or {}
    finally:
        con.close()


def listar_sesiones(
    db_path: str | Path | None = None, limite: int = 100
) -> list[dict[str, Any]]:
    """Sesiones registradas, mas recientes primero (limite clampado 1..1000)."""
    lim = max(1, min(1000, int(limite)))
    con = _conectar(db_path)
    try:
        filas = con.execute(
            "SELECT * FROM sesiones ORDER BY inicio_ms DESC, id DESC LIMIT ?", (lim,)
        ).fetchall()
        return [dict(fila) for fila in filas]
    finally:
        con.close()


def sesion_activa(
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Sesion activa mas reciente, o ``None``. Helper para ingesta/dashboard/F4."""
    con = _conectar(db_path)
    try:
        fila = con.execute(
            "SELECT * FROM sesiones WHERE estado = 'activa' "
            "ORDER BY inicio_ms DESC, id DESC LIMIT 1"
        ).fetchone()
        return _fila_a_dict(fila)
    finally:
        con.close()


# --- CLI --------------------------------------------------------------------


def _fmt_ms(valor: int | None) -> str:
    if valor is None:
        return "—"
    return datetime.fromtimestamp(int(valor) / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _duracion_s(fila: dict[str, Any]) -> str:
    inicio = fila.get("inicio_ms")
    fin = fila.get("fin_ms")
    if inicio is None or fin is None:
        return "—"
    return f"{max(0, int(fin) - int(inicio)) / 1000.0:.1f} s"


def _imprimir_lista(filas: list[dict[str, Any]]) -> None:
    if not filas:
        print("No hay sesiones registradas.")
        return
    print(f"{'sesion':<16} {'estado':<8} {'inicio':<19} {'fin':<19} {'dur':>9}  nota")
    print("-" * 86)
    for fila in filas:
        print(
            f"{fila['sesion']:<16} {fila['estado']:<8} {_fmt_ms(fila.get('inicio_ms')):<19} "
            f"{_fmt_ms(fila.get('fin_ms')):<19} {_duracion_s(fila):>9}  {fila.get('nota') or ''}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Session logger de AulaSense (tabla sesiones, SPEC §8 + extension F3)"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="ruta de la SQLite (default: env AULASENSE_DB o server/datos/aulasense.db)",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p_start = sub.add_parser("start", help="abre una sesion activa")
    p_start.add_argument("--id", required=True, dest="sesion", help="id de sesion (S1, S2, ...)")
    p_start.add_argument("--nota", default=None, help="nota libre (max 200 chars)")
    p_start.add_argument("--device", default=None, help="device_id 16 hex (opcional)")

    p_stop = sub.add_parser("stop", help="cierra una sesion activa")
    p_stop.add_argument("--id", required=True, dest="sesion", help="id de sesion a cerrar")
    p_stop.add_argument("--nota", default=None, help="nota final opcional")

    p_list = sub.add_parser("list", help="lista sesiones registradas")
    p_list.add_argument("--limite", type=int, default=100, help="maximo de filas (1-1000)")
    p_list.add_argument("--json", action="store_true", help="salida JSON")

    args = parser.parse_args(argv)

    try:
        if args.comando == "start":
            fila = iniciar_sesion(
                args.sesion, nota=args.nota, device_id=args.device, db_path=args.db
            )
            print(
                f"[session_logger] sesion {fila['sesion']} ACTIVA "
                f"(inicio {_fmt_ms(fila['inicio_ms'])}, id={fila['id']})"
            )
            return 0
        if args.comando == "stop":
            fila = detener_sesion(args.sesion, nota=args.nota, db_path=args.db)
            print(
                f"[session_logger] sesion {fila['sesion']} CERRADA "
                f"(duracion {_duracion_s(fila)})"
            )
            return 0
        filas = listar_sesiones(db_path=args.db, limite=args.limite)
        if args.json:
            print(json.dumps(filas, ensure_ascii=False, indent=2))
        else:
            _imprimir_lista(filas)
        return 0
    except ErrorSesion as exc:
        print(f"[session_logger] ERROR: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"[session_logger] ERROR de SQLite: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())