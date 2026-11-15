"""CLI del worker de descripcion IA de imagenes de AulaSense (SPEC §7.3, F25-d2).

Wrapper fino de ``server/vision/cliente.py``. Todas las reglas (rate limits
persistentes, dedupe por hash, backoff, presupuesto, interruptibilidad y
cumplimiento ToS) viven en la libreria.

Uso
---
Modo B (DEFAULT, agente-mediado; admite key del Token Plan ``sk-sp-``)::

    # A) pasada directa lanzada por un agente interactivo:
    server\\.venv\\Scripts\\python.exe tools\\vision_describe.py --once --limit 30
    #    (--orden desc prioriza las ids MAS NUEVAS; default asc = FIFO)

    # B) superficie agente-mediada (el agente describe con su skill y aplica):
    server\\.venv\\Scripts\\python.exe tools\\vision_describe.py vision_pending --limit 30 > .tmp\\vision_pending.json
    #    ... el agente escribe .tmp\\vision_resultados.json ...
    server\\.venv\\Scripts\\python.exe tools\\vision_describe.py vision_apply .tmp\\vision_resultados.json

Modo A (daemon; SOLO key pay-as-you-go ``sk-*``)::

    server\\.venv\\Scripts\\python.exe tools\\vision_describe.py --loop

Parada limpia (bandera en ``.tmp/vision_stop.flag``)::

    server\\.venv\\Scripts\\python.exe tools\\vision_describe.py --stop

Codigos de salida: 0 OK/idle/tope (parada honesta) | 2 uso/config (incluye el
rechazo ToS de ``sk-sp-`` en modo A) | 3 DB inexistente o sin migrar.

Secretos: la key se resuelve por codigo (env ``DASHSCOPE_API_KEY`` ->
``claves/.env`` -> auth.json del Token Plan) y NUNCA se imprime; solo se publica
su tipo/origen/longitud.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # aulasense/
_SERVER = ROOT / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from vision import cliente  # noqa: E402  (tras ajustar sys.path)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_ESQUEMA = 3


def _parser() -> argparse.ArgumentParser:
    """Argumentos en espanol (help del CLI)."""
    ap = argparse.ArgumentParser(
        prog="vision_describe.py",
        description=(
            "Worker de descripcion IA de imagenes de AulaSense (SPEC 7.3). "
            "Modo B = agente-mediado (default, admite la key del Token Plan); "
            "modo A = daemon SOLO con key pay-as-you-go sk-*."
        ),
        epilog=(
            "Ejemplos:\n"
            "  python tools/vision_describe.py --once --limit 30\n"
            "  python tools/vision_describe.py vision_pending --limit 30\n"
            "  python tools/vision_describe.py vision_apply .tmp/vision_resultados.json\n"
            "  python tools/vision_describe.py --loop\n"
            "  python tools/vision_describe.py --stop\n"
            "Los topes (AULASENSE_VISION_MAX_DIA=120, AULASENSE_VISION_MAX_HORA=20) "
            "son persistentes en la tabla vision_uso."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "accion",
        nargs="?",
        choices=("vision_pending", "vision_apply"),
        default=None,
        help="vision_pending: cola pendiente en JSON sin llamar a la API; "
        "vision_apply: escribe los resultados que produjo el agente",
    )
    ap.add_argument(
        "archivo",
        nargs="?",
        default=None,
        help="JSON de resultados para vision_apply",
    )
    ap.add_argument(
        "--once",
        action="store_true",
        help="una pasada sobre la cola (modo B agente-mediado)",
    )
    ap.add_argument(
        "--loop",
        action="store_true",
        help="modo A daemon; SOLO arranca con key pay-as-you-go sk-* (ToS Token Plan)",
    )
    ap.add_argument(
        "--stop",
        action="store_true",
        help="escribe la bandera de parada (.tmp/vision_stop.flag) y sale",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help=f"maximo de imagenes por pasada (default {cliente.LIMITE_DEFECTO})",
    )
    ap.add_argument("--db", default=None, help="SQLite (default AULASENSE_DB o server/datos/aulasense.db)")
    ap.add_argument("--datos", default=None, help="directorio base de imagenes (default AULASENSE_DATOS_DIR)")
    ap.add_argument("--env", default=None, help="ruta del .env de la raiz")
    ap.add_argument("--claves-env", default=None, help="ruta de claves/.env")
    ap.add_argument(
        "--modelo",
        default=None,
        help=f"modelo de vision (default AULASENSE_VISION_MODELO o {cliente.MODELO_DEFECTO})",
    )
    ap.add_argument(
        "--max-dia",
        type=int,
        default=None,
        help=f"tope diario de llamadas (default AULASENSE_VISION_MAX_DIA={cliente.MAX_DIA_DEFECTO})",
    )
    ap.add_argument(
        "--max-hora",
        type=int,
        default=None,
        help=f"tope por hora de llamadas (default AULASENSE_VISION_MAX_HORA={cliente.MAX_HORA_DEFECTO})",
    )
    ap.add_argument(
        "--modo",
        choices=("A", "B"),
        default=None,
        help="modo de operacion registrado en vision_uso (default AULASENSE_VISION_MODO o B)",
    )
    ap.add_argument(
        "--intervalo",
        type=float,
        default=None,
        help=f"segundos entre pasadas del --loop (default {cliente.INTERVALO_LOOP_S:.0f})",
    )
    ap.add_argument(
        "--orden",
        choices=cliente.ORDENES_VALIDOS,
        default=None,
        help="orden de la cola: asc (FIFO, default) | desc (ids MAS NUEVAS primero)",
    )
    return ap


def _config(args: argparse.Namespace) -> cliente.ConfigVision:
    return cliente.construir_config(
        db=args.db,
        datos=args.datos,
        env=args.env,
        claves=args.claves_env,
        modelo=args.modelo,
        max_dia=args.max_dia,
        max_hora=args.max_hora,
        modo=args.modo,
        limite=args.limit,
        orden=args.orden,
    )


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del CLI (devuelve el exit code)."""
    parser = _parser()
    args = parser.parse_args(argv)
    cfg = _config(args)
    # ``--loop`` ES el modo A (daemon): la guarda ToS de sk-sp- debe aplicarse
    # aunque AULASENSE_VISION_MODO diga otra cosa; un --modo explicito manda.
    if args.loop and args.modo is None:
        cfg = dataclasses.replace(cfg, modo="A")

    # --- --stop: escribe la bandera y sale (no requiere key ni DB) ----------
    if args.stop:
        ruta = cliente.escribir_bandera(cfg)
        print(f"[vision] bandera de parada escrita en {ruta}")
        return EXIT_OK

    # --- vision_pending: solo lectura (no llama a la API, no necesita key) --
    if args.accion == "vision_pending":
        try:
            datos = cliente.listar_pendientes(cfg)
        except cliente.ErrorEsquema as exc:
            cliente.log("ERROR", str(exc))
            return EXIT_ESQUEMA
        print(json.dumps(datos, ensure_ascii=False, indent=2))
        return EXIT_OK

    # --- vision_apply: escribe los resultados del agente --------------------
    if args.accion == "vision_apply":
        if not args.archivo:
            cliente.log(
                "ERROR",
                "vision_apply necesita la ruta del JSON de resultados "
                "(ej. .tmp/vision_resultados.json)",
            )
            return EXIT_CONFIG
        ruta_json = Path(args.archivo)
        try:
            datos = json.loads(ruta_json.read_text(encoding="utf-8"))
        except OSError as exc:
            cliente.log("ERROR", f"no se pudo leer {ruta_json}: {exc}")
            return EXIT_CONFIG
        except ValueError as exc:
            cliente.log("ERROR", f"JSON invalido en {ruta_json}: {exc}")
            return EXIT_CONFIG
        try:
            resumen = cliente.aplicar_resultados(cfg, datos)
        except cliente.ErrorEsquema as exc:
            cliente.log("ERROR", str(exc))
            return EXIT_ESQUEMA
        except cliente.ErrorConfiguracion as exc:
            cliente.log("ERROR", str(exc))
            return EXIT_CONFIG
        cliente.log(
            "INFO",
            f"vision_apply: aplicadas={resumen.aplicadas} invalidas={resumen.invalidas} "
            f"tope={resumen.tope or 'no'}",
        )
        # Si el archivo entero era invalido y no se aplico nada, error de uso.
        return EXIT_OK if (resumen.aplicadas or resumen.tope) else EXIT_CONFIG

    # --- --once / --loop: pasadas que SI llaman al modelo -------------------
    if args.once and args.loop:
        cliente.log("ERROR", "--once y --loop son excluyentes")
        return EXIT_CONFIG
    if not args.once and not args.loop:
        parser.print_help(sys.stderr)
        cliente.log(
            "ERROR",
            "indica una accion: --once, --loop, --stop, vision_pending o vision_apply",
        )
        return EXIT_CONFIG

    clave = cliente.resolver_clave(env_path=cfg.env_path, claves_path=cfg.claves_path)
    cliente.log("INFO", f"clave: {clave.descripcion_segura}")  # nunca el valor

    try:
        if args.loop:
            intervalo = (
                float(args.intervalo)
                if args.intervalo is not None and args.intervalo > 0
                else cliente.INTERVALO_LOOP_S
            )
            return cliente.ejecutar_bucle(cfg, clave, intervalo_s=intervalo)
        cliente.ejecutar_pasada(cfg, clave)
        return EXIT_OK
    except cliente.ErrorToS as exc:
        cliente.log("ERROR", str(exc))
        return EXIT_CONFIG
    except cliente.ErrorEsquema as exc:
        cliente.log("ERROR", str(exc))
        return EXIT_ESQUEMA
    except cliente.ErrorConfiguracion as exc:
        cliente.log("ERROR", str(exc))
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())