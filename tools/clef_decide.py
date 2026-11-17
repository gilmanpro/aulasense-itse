"""CLI del worker de decision de aula Clef (SPEC §16.6, D-F26-7 — fase F26-a2).

Wrapper fino de ``server/vision/clef.py``. Todas las reglas (cascada
flash->clef, taxonomia, IMAGEN_DEGRADADA local, claim atomico propio, rate
limits persistentes, dedupe por hash, backoff, parada) viven en la libreria.

Uso
---
Paso OBLIGATORIO antes del lote (regla 16 — 1 decision de prueba midiendo
usage REAL, salida SOLO tokens/coste, jamas la key)::

    server\\.venv\\Scripts\\python.exe tools\\clef_decide.py --paso0
    #    (.tmp/f26_clef_paso0.json; con --imagen-id N usa una fila real ya descripta)

Pasada sobre la cola (modo agente-mediado; UNA pasada a la vez por BD,
lanzada y esperada dentro del turno — regla 16 extendida)::

    server\\.venv\\Scripts\\python.exe tools\\clef_decide.py --once --limit 30

Auditoria de la cola (sin API, sin key)::

    server\\.venv\\Scripts\\python.exe tools\\clef_decide.py decision_pending

Parada limpia (bandera en ``.tmp/clef_stop.flag``)::

    server\\.venv\\Scripts\\python.exe tools\\clef_decide.py --stop

Bucle (pasadas encadenadas; el ToS de OpenRouter no lo restringe, pero la
regla 16 pide seguir lanzando esperadas por un agente)::

    server\\.venv\\Scripts\\python.exe tools\\clef_decide.py --loop

Codigos de salida: 0 OK/idle/tope (parada honesta) | 2 uso/config | 3 DB
inexistente o sin migrar F26.

Secretos: ``OPENROUTER_API_KEY`` se resuelve por codigo (entorno ->
``.env`` UNICO raiz (canonico, SPEC 16.8) -> ``claves/.env`` solo como lectura
retrocompatible de ultimo lugar, via ``cliente.valor_env``; F26-b unifica el
archivo) y NUNCA se imprime: solo tipo/origen/longitud.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # aulasense/
_SERVER = ROOT / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from vision import clef  # noqa: E402  (tras ajustar sys.path)

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_ESQUEMA = 3


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="clef_decide.py",
        description=(
            "Worker de decision de aula Clef (SPEC 16.6): consume filas "
            "vision_estado='hecho' AND decision_estado='pendiente' con cascada "
            "cloudflare/clef-flash (texto) -> cloudflare/clef (imagen) si "
            "confidence < 0.6. Taxonomia de 6 etiquetas + noul_cubre/"
            "noul_calidad/score_actividad."
        ),
        epilog=(
            "Ejemplos:\n"
            "  python tools/clef_decide.py --paso0\n"
            "  python tools/clef_decide.py --once --limit 30\n"
            "  python tools/clef_decide.py decision_pending\n"
            "  python tools/clef_decide.py --stop\n"
            "Los topes (AULASENSE_CLEF_MAX_DIA=120, AULASENSE_CLEF_MAX_HORA=20) "
            "son persistentes en la tabla clef_uso. La OPENROUTER_API_KEY nunca "
            "se imprime."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "accion",
        nargs="?",
        choices=("decision_pending",),
        default=None,
        help="decision_pending: cola de decision en JSON sin llamar a la API",
    )
    ap.add_argument(
        "--once", action="store_true", help="una pasada sobre la cola de decision"
    )
    ap.add_argument("--loop", action="store_true", help="pasadas encadenadas (hasta --stop)")
    ap.add_argument(
        "--paso0",
        action="store_true",
        help="1 decision de prueba midiendo usage REAL (regla 16); imprime SOLO "
        "tokens/coste; escribe .tmp/f26_clef_paso0.json",
    )
    ap.add_argument(
        "--stop", action="store_true", help="escribe la bandera de parada y sale"
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help=f"maximo de imagenes por pasada (default {clef.LIMITE_DEFECTO})",
    )
    ap.add_argument("--db", default=None, help="SQLite (default AULASENSE_DB o server/datos/aulasense.db)")
    ap.add_argument("--datos", default=None, help="directorio base de imagenes (AULASENSE_DATOS_DIR)")
    ap.add_argument("--env", default=None, help="ruta del .env de la raiz")
    ap.add_argument("--claves-env", default=None, help="ruta de claves/.env")
    ap.add_argument("--modelo-flash", default=None, help="modelo nivel 1 (default cloudflare/clef-flash)")
    ap.add_argument("--modelo", default=None, help="modelo de escalado (default cloudflare/clef)")
    ap.add_argument("--max-dia", type=int, default=None, help="tope diario (AULASENSE_CLEF_MAX_DIA=120)")
    ap.add_argument("--max-hora", type=int, default=None, help="tope horario (AULASENSE_CLEF_MAX_HORA=20)")
    ap.add_argument(
        "--umbral",
        type=float,
        default=None,
        help="confidence por debajo de la cual se escala (AULASENSE_CLEF_UMBRAL_ESCALADO=0.6)",
    )
    ap.add_argument(
        "--orden",
        choices=("asc", "desc"),
        default="asc",
        help="orden de la cola: asc (FIFO, default) | desc (ids MAS NUEVAS primero)",
    )
    ap.add_argument(
        "--intervalo",
        type=float,
        default=None,
        help=f"segundos entre pasadas del --loop (default {clef.INTERVALO_LOOP_S:.0f})",
    )
    ap.add_argument(
        "--imagen-id",
        type=int,
        default=None,
        help="para --paso0: fila concreta ya descripta (default: state SIMULADA)",
    )
    ap.add_argument(
        "--salida",
        default=None,
        help=f"ruta del JSON del --paso0 (default {clef.PASO0_SALIDA_DEFECTO})",
    )
    return ap


def _config(args: argparse.Namespace) -> clef.ConfigClef:
    return clef.construir_config(
        db=args.db,
        datos=args.datos,
        env=args.env,
        claves=args.claves_env,
        modelo_flash=args.modelo_flash,
        modelo_clef=args.modelo,
        max_dia=args.max_dia,
        max_hora=args.max_hora,
        limite=args.limit,
        umbral=args.umbral,
    )


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada del CLI (devuelve el exit code)."""
    parser = _parser()
    args = parser.parse_args(argv)

    acciones = sum(1 for activo in (args.once, args.loop, args.paso0) if activo)
    if args.stop and (acciones or args.accion):
        clef.log("ERROR", "--stop no se combina con otras acciones")
        return EXIT_CONFIG
    if acciones > 1:
        clef.log("ERROR", "--once, --loop y --paso0 son excluyentes")
        return EXIT_CONFIG

    try:
        cfg = _config(args)
    except clef.ErrorConfiguracion as exc:
        clef.log("ERROR", str(exc))
        return EXIT_CONFIG

    # --- --stop: escribe la bandera y sale (no requiere key ni DB) -----------
    if args.stop:
        ruta = clef.escribir_bandera(cfg)
        print(f"[clef] bandera de parada escrita en {ruta}")
        return EXIT_OK

    # --- decision_pending: solo lectura (no llama a la API, no necesita key) --
    if args.accion == "decision_pending":
        try:
            datos = clef.listar_pendientes_decision(
                cfg, limite=max(1, args.limit or 50)
            )
        except clef.ErrorEsquema as exc:
            clef.log("ERROR", str(exc))
            return EXIT_ESQUEMA
        print(json.dumps(datos, ensure_ascii=False, indent=2))
        return EXIT_OK

    if not (args.once or args.loop or args.paso0):
        parser.print_help(sys.stderr)
        clef.log("ERROR", "indica una accion: --paso0, --once, --loop, --stop o decision_pending")
        return EXIT_CONFIG

    clave = clef.clave_api()
    clef.log("INFO", f"clave: {clave.descripcion_segura}")  # tipo/origen/longitud

    try:
        if args.paso0:
            resultado = clef.paso0(
                cfg,
                clave.valor,
                imagen_id=args.imagen_id,
                salida=Path(args.salida) if args.salida else None,
            )
            # Salida SOLO metricas (regla 16): tokens/coste/latencia. Jamas la key.
            print(
                json.dumps(
                    {
                        "paso0_ok": resultado.ok,
                        "modelo": resultado.modelo,
                        "origen_state": resultado.origen_state,
                        "tokens": resultado.tokens,
                        "coste_usd": resultado.coste_usd,
                        "latencia_ms": resultado.latencia_ms,
                        "error": resultado.error,
                        "archivo": str(resultado.salida) if resultado.salida else None,
                    },
                    ensure_ascii=False,
                )
            )
            return EXIT_OK if resultado.ok else EXIT_CONFIG
        if args.loop:
            clef.instalar_manejadores()
            intervalo = (
                float(args.intervalo)
                if args.intervalo is not None and args.intervalo > 0
                else clef.INTERVALO_LOOP_S
            )
            return clef.ejecutar_bucle(cfg, clave.valor, intervalo_s=intervalo, orden=args.orden)
        clef.instalar_manejadores()
        clef.ejecutar_pasada(cfg, clave.valor, orden=args.orden)
        return EXIT_OK
    except clef.ErrorEsquema as exc:
        clef.log("ERROR", str(exc))
        return EXIT_ESQUEMA
    except clef.ErrorConfiguracion as exc:
        clef.log("ERROR", str(exc))
        return EXIT_CONFIG
    except OSError as exc:  # bandera/archivo de salida no escribible
        clef.log("ERROR", f"fallo de E/S: {exc}")
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
