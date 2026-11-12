"""purga_imagenes.py — retencion de la evidencia de imagen de AulaSense (SPEC §7.1).

Uso:
  server\\.venv\\Scripts\\python.exe tools/purga_imagenes.py            # default .env (30 dias)
  server\\.venv\\Scripts\\python.exe tools/purga_imagenes.py --dias 7
  server\\.venv\\Scripts\\python.exe tools/purga_imagenes.py --dry-run

Politica (AGENTS.md regla 2/14, D-F24-7):
- Selecciona ``imagenes`` con ``ts_ms`` anterior a ahora - N dias e
  ``imagen_purgada = 0``.
- Borra el archivo fisico (si existe) y marca ``imagen_purgada = 1``.
- NUNCA hace ``DELETE``: la fila, el ``evento_id`` y ``ia_persona``/``ia_score``
  se CONSERVAN para que el dataset consolidado no se rompa.
- Idempotente: una segunda corrida no encuentra filas pendientes (0).
- ``--dry-run`` informa sin borrar ni marcar nada.

La ruta de cada fila es relativa al directorio de datos (``AULASENSE_DATOS_DIR``
o el padre de la DB). Cualquier ruta que escape de ese directorio se ignora
(guarda anti path traversal) y se reporta como anomalia sin borrar nada.

Secretos: no lee ni imprime credenciales; solo cuenta filas.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]  # aulasense/
ENV_DEFECTO = ROOT / ".env"
DB_DEFECTO = ROOT / "server" / "datos" / "aulasense.db"
RETENCION_DEFECTO = 30


def ahora_ms() -> int:
    """Epoca del PC en milisegundos (misma base que ``imagenes.ts_ms``)."""
    return int(time.time() * 1000)


def leer_retencion(env_path: Path, argv_dias: int | None) -> int:
    """Dias de retencion: ``--dias`` > ``AULASENSE_IMG_RETENCION_DIAS`` > 30."""
    if argv_dias is not None:
        return max(0, int(argv_dias))
    if env_path.exists():
        cfg = dotenv_values(env_path)
        crudo = str(cfg.get("AULASENSE_IMG_RETENCION_DIAS") or "").strip()
        if crudo:
            try:
                return max(0, int(crudo))
            except ValueError:
                print(f"[purga] WARN: AULASENSE_IMG_RETENCION_DIAS={crudo!r} no es entero")
    return RETENCION_DEFECTO


def resolver_datos_dir(db_path: Path, env_path: Path) -> Path:
    """DIR base de datos: ``AULASENSE_DATOS_DIR`` del .env/entorno o el padre de la DB."""
    crudo = ""
    if env_path.exists():
        crudo = str(dotenv_values(env_path).get("AULASENSE_DATOS_DIR") or "").strip()
    if not crudo:
        crudo = os.environ.get("AULASENSE_DATOS_DIR", "").strip()
    if crudo:
        return Path(crudo).expanduser()
    return db_path.parent


def _ruta_segura(datos_dir: Path, ruta_relativa: str) -> Path | None:
    """Resuelve ``ruta`` bajo ``datos_dir``; ``None`` si escapa (path traversal)."""
    try:
        candidata = (datos_dir / ruta_relativa).resolve()
        base = datos_dir.resolve()
    except (OSError, RuntimeError):
        return None
    if candidata == base or base in candidata.parents:
        return candidata
    return None


def purgar(
    db_path: Path, datos_dir: Path, dias: int, dry_run: bool = False
) -> dict[str, int]:
    """Purga idempotente. Devuelve contadores: ``filas``, ``archivos``, ``anomalias``."""
    corte_ms = ahora_ms() - dias * 24 * 60 * 60 * 1000
    resumen = {"filas": 0, "archivos": 0, "anomalias": 0}

    if not db_path.exists():
        print(f"[purga] DB inexistente: {db_path} (nada que purgar)")
        return resumen

    con = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        con.execute("PRAGMA busy_timeout=10000")
        try:
            filas = con.execute(
                "SELECT id, ruta, ts_ms FROM imagenes "
                "WHERE ts_ms < ? AND imagen_purgada = 0 ORDER BY id",
                (corte_ms,),
            ).fetchall()
        except sqlite3.OperationalError:
            print("[purga] tabla 'imagenes' inexistente (nada que purgar)")
            return resumen

        for imagen_id, ruta, _ts_ms in filas:
            destino = _ruta_segura(datos_dir, str(ruta or ""))
            if destino is None:
                resumen["anomalias"] += 1
                print(f"[purga] WARN: ruta fuera de datos/ ignorada (id={imagen_id})")
                continue
            existe = destino.is_file()
            if dry_run:
                print(
                    f"[purga] dry-run: id={imagen_id} archivo={'si' if existe else 'no'} "
                    f"ruta={ruta}"
                )
                resumen["filas"] += 1
                resumen["archivos"] += 1 if existe else 0
                continue
            if existe:
                try:
                    destino.unlink()
                    resumen["archivos"] += 1
                except OSError as exc:
                    print(f"[purga] WARN: no se pudo borrar id={imagen_id} ({exc})")
                    continue
            # La fila se conserva SIEMPRE (dataset intacto); solo se marca.
            con.execute(
                "UPDATE imagenes SET imagen_purgada = 1 "
                "WHERE id = ? AND imagen_purgada = 0",
                (imagen_id,),
            )
            resumen["filas"] += 1
        if not dry_run:
            con.commit()
    finally:
        con.close()
    return resumen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Purga imagenes de evidencia por antiguedad (conserva la fila)"
    )
    ap.add_argument("--db", default=None, help=f"SQLite (default: {DB_DEFECTO})")
    ap.add_argument(
        "--datos", default=None, help="dir base de datos (default: AULASENSE_DATOS_DIR o padre de la DB)"
    )
    ap.add_argument(
        "--dias",
        type=int,
        default=None,
        help="retencion en dias (default: AULASENSE_IMG_RETENCION_DIAS o 30)",
    )
    ap.add_argument("--env", default=str(ENV_DEFECTO), help="ruta del .env")
    ap.add_argument("--dry-run", action="store_true", help="informa sin borrar ni marcar")
    args = ap.parse_args(argv)

    env_path = Path(args.env)
    db_path = Path(args.db) if args.db else DB_DEFECTO
    datos_dir = Path(args.datos) if args.datos else resolver_datos_dir(db_path, env_path)
    dias = leer_retencion(env_path, args.dias)

    print(f"[purga] db={db_path} datos={datos_dir} retencion={dias}d dry_run={args.dry_run}")
    resumen = purgar(db_path, datos_dir, dias, dry_run=args.dry_run)
    if args.dry_run:
        print(
            f"[purga] dry-run: {resumen['filas']} filas pendientes "
            f"({resumen['archivos']} archivos presentes)"
        )
    else:
        print(
            f"[purga] {resumen['filas']} imagenes purgadas "
            f"({resumen['archivos']} archivos borrados; filas conservadas)"
        )
    if resumen["anomalias"]:
        print(f"[purga] WARN: {resumen['anomalias']} rutas anomalas ignoradas")
    return 0


if __name__ == "__main__":
    sys.exit(main())