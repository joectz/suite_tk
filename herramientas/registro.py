"""
registro.py — Lista de herramientas disponibles en el menu principal.

Para agregar una herramienta nueva:
    1. Crear su carpeta bajo herramientas/<id_herramienta>/.
    2. Definir su pagina con @ui.page("/su-ruta") en un modulo de esa carpeta
       (ej. pagina.py) y, si necesita un subproceso, su motor.main(argv).
    3. Agregar una entrada de Herramienta() aqui, importando ese modulo.

No hace falta tocar app.py: la pantalla principal y el despacho de
"--worker <id>" leen este registro automaticamente.
"""

from __future__ import annotations

from . import base
from .mapeador_urls import motor as _mapeador_motor
from .mapeador_urls import pagina as _mapeador_pagina  # noqa: F401  (el import registra la @ui.page)

REGISTRO: list[base.Herramienta] = [
    base.Herramienta(
        id=_mapeador_pagina.ID_HERRAMIENTA,
        nombre="Mapeador de URLs",
        descripcion="Rastrea un sitio y lista todas sus páginas, "
                    "siguiendo enlaces internos y el sitemap.xml",
        icono="travel_explore",
        ruta=_mapeador_pagina.RUTA,
        worker_main=_mapeador_motor.main,
    ),
]


def buscar(id_herramienta: str) -> base.Herramienta | None:
    return next((h for h in REGISTRO if h.id == id_herramienta), None)
