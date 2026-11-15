"""R3: el ciclo ``ingesta_mqtt`` <-> ``receptor_imagen`` esta ROTO (gate arq. 2026-11-15).

Direccion unica del contrato: el nucleo (``ingesta_mqtt``) define
``resolver_datos_dir`` y ``dimensiones_jpeg``; ``receptor_imagen`` las IMPORTA.
Antes el nucleo las resolvia con un import dinamico del receptor
(``_modulo_receptor``), es decir el nucleo dependia del plugin en runtime.

Este test fija la inversion en los DOS ordenes de import y en interpretes LIMPIOS
(subprocess): si el ciclo reapareciera, o si un doble import creara una segunda
copia del modulo (fenomeno del incidente F25f "writer no responde"), las dos
referencias serian objetos DISTINTOS y el test fallaria.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# ``server/ingesta``: mismo sys.path que usa la suite (conftest) y el CLI.
_INGESTA = Path(__file__).resolve().parents[1] / "ingesta"

_GUION = textwrap.dedent(
    """
    import importlib
    import sys

    importlib.import_module(sys.argv[1])
    importlib.import_module(sys.argv[2])
    ingesta = sys.modules["ingesta_mqtt"]
    receptor = sys.modules["receptor_imagen"]

    # 1. Misma funcion, un solo objeto (dos copias del modulo = objetos distintos).
    for nombre in ("resolver_datos_dir", "dimensiones_jpeg"):
        assert getattr(receptor, nombre) is getattr(ingesta, nombre), nombre
        assert getattr(receptor, nombre).__module__ == "ingesta_mqtt", nombre

    # 2. Una sola copia por fichero fuente en sys.modules (sin alias magic fresco).
    for modulo in (ingesta, receptor):
        ruta = str(modulo.__file__)
        copias = [
            nombre
            for nombre, otro in sys.modules.items()
            if str(getattr(otro, "__file__", "")) == ruta
        ]
        assert copias == [modulo.__name__], (ruta, copias)

    # 3. El nucleo ya no arrastra el plugin: nada de _modulo_receptor ni del
    #    modulo del receptor como atributo de ingesta_mqtt.
    assert not hasattr(ingesta, "_modulo_receptor")
    assert not hasattr(ingesta, "receptor_imagen")
    assert receptor.IngestaWriter is ingesta.IngestaWriter
    print("sin ciclo:", sys.argv[1], "->", sys.argv[2])
    """
)


@pytest.mark.parametrize(
    "primero, segundo",
    [
        ("receptor_imagen", "ingesta_mqtt"),
        ("ingesta_mqtt", "receptor_imagen"),
    ],
)
def test_orden_de_import_en_interprete_limpio(primero: str, segundo: str) -> None:
    """Ambos ordenes de import, en procesos nuevos, comparten las funciones."""
    completado = subprocess.run(
        [sys.executable, "-c", _GUION, primero, segundo],
        cwd=str(_INGESTA),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completado.returncode == 0, completado.stderr
    assert "sin ciclo" in completado.stdout


def test_identidad_en_el_proceso_de_la_suite() -> None:
    """Ya importados por la suite: el re-export del receptor apunta al nucleo."""
    import ingesta_mqtt
    import receptor_imagen

    assert receptor_imagen.resolver_datos_dir is ingesta_mqtt.resolver_datos_dir
    assert receptor_imagen.dimensiones_jpeg is ingesta_mqtt.dimensiones_jpeg
    assert receptor_imagen.dimensiones_jpeg.__module__ == "ingesta_mqtt"