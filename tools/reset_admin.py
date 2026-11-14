"""reset_admin.py — reset LOCAL de la password del dashboard AulaSense (SPEC §9.2).

Uso (acceso fisico al PC = el dueño; NUNCA por red):
  server\\.venv\\Scripts\\python.exe tools/reset_admin.py
  server\\.venv\\Scripts\\python.exe tools/reset_admin.py --usuario admin --db <ruta.db>

Que hace (D-F25-8, mitigacion del encierro R-F25-3):
- Genera una password NUEVA con ``secrets.token_hex(16)`` (por codigo, regla 8/15).
- La escribe en ``claves/.env`` (``AULASENSE_ADMIN_PASSWORD``; crea el archivo con
  ``AULASENSE_ADMIN_USER=admin`` si no existia).
- Actualiza el hash del usuario en la BD (crea el usuario si no existe).
- Limpia los lockouts (``intentos_login``) y las sesiones abiertas del usuario.
- Imprime SOLO "regenerada en claves/.env" + longitud: la password NUNCA se
  imprime, ni se pasa por argv, ni se escribe en logs.

Este CLI escribe por BD (writer unico de auth) y no necesita el dashboard vivo.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # aulasense/
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from dashboard import auth  # noqa: E402

DB_DEFECTO = SERVER_DIR / "datos" / "aulasense.db"


def _db_path(argv_db: str | None) -> Path:
    """``--db`` > ``AULASENSE_DB`` del entorno/.env > server/datos/aulasense.db."""
    if argv_db:
        return Path(argv_db).expanduser()
    crudo = os.environ.get("AULASENSE_DB", "").strip() or auth.valor_env("AULASENSE_DB")
    if crudo:
        return Path(crudo).expanduser()
    return DB_DEFECTO


def resetear(db_path: Path, usuario: str | None = None) -> dict[str, object]:
    """Regenera la password del admin y limpia lockouts/sesiones.

    Devuelve un resumen SIN la password (solo ruta, longitud y contadores).
    """
    auth.cargar_env(exportar=False)
    username = (usuario or auth.usuario_admin()).strip() or "admin"
    password = secrets.token_hex(16)  # generada por codigo, jamas impresa

    ruta_env = auth.escribir_password(password, username)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=5.0)
    try:
        auth.ensure_tablas(con)
        fila = auth.obtener_usuario(con, username)
        nuevo_hash = auth.hash_password(password)
        if fila is None:
            con.execute(
                "INSERT INTO usuarios (username, pass_hash) VALUES (?, ?)",
                (username, nuevo_hash),
            )
            con.commit()
            user_id = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
        else:
            user_id = int(fila["id"])
            con.execute(
                "UPDATE usuarios SET pass_hash = ? WHERE id = ?", (nuevo_hash, user_id)
            )
            con.commit()
        intentos = auth.limpiar_intentos_usuario(con, username)
        sesiones = auth.borrar_sesiones_usuario(con, user_id)
    finally:
        con.close()
    return {
        "env": ruta_env,
        "longitud": len(password),
        "usuario": username,
        "intentos_borrados": intentos,
        "sesiones_borradas": sesiones,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenera la password del admin del dashboard AulaSense (nunca la imprime)."
    )
    parser.add_argument("--usuario", default=None, help="username (default: AULASENSE_ADMIN_USER)")
    parser.add_argument("--db", default=None, help="ruta de la SQLite (default: AULASENSE_DB)")
    args = parser.parse_args(argv)

    resumen = resetear(_db_path(args.db), args.usuario)
    print(f"[reset_admin] password regenerada en {resumen['env']} "
          f"(longitud {resumen['longitud']} caracteres; el valor NO se imprime)")
    print(f"[reset_admin] usuario: {resumen['usuario']}")
    print(f"[reset_admin] lockouts limpiados: {resumen['intentos_borrados']} filas de intentos_login")
    print(f"[reset_admin] sesiones abiertas invalidadas: {resumen['sesiones_borradas']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())