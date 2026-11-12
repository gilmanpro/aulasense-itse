"""Configuracion de pytest para el server de AulaSense.

Anade ``server/ingesta`` a ``sys.path`` para que los tests importen los modulos
de la ingesta como modulos planos (``esquema``, ``ingesta_mqtt``), igual que
cuando se ejecuta ``python server/ingesta/ingesta_mqtt.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[1]

for _sub in ("ingesta",):
    _ruta = _SERVER / _sub
    if _ruta.is_dir() and str(_ruta) not in sys.path:
        sys.path.insert(0, str(_ruta))