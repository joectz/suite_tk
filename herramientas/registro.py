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
from .descargador_multimedia import motor as _descargador_motor
from .descargador_multimedia import pagina as _descargador_pagina  # noqa: F401
from .sitemap import motor as _sitemap_motor
from .sitemap import pagina as _sitemap_pagina  # noqa: F401
from .color_tools import motor as _color_motor
from .color_tools import pagina as _color_pagina  # noqa: F401

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
    base.Herramienta(
        id=_descargador_pagina.ID_HERRAMIENTA,
        nombre="Descargador Multimedia",
        descripcion="Extrae y descarga imágenes, videos, audio y recursos "
                    "multimedia de páginas web.",
        icono="download",
        ruta=_descargador_pagina.RUTA,
        worker_main=_descargador_motor.main,
    ),
    base.Herramienta(
        id=_sitemap_pagina.ID_HERRAMIENTA,
        nombre="Generador de Sitemap",
        descripcion="Genera un sitemap.xml SEO-ready a partir de "
                    "sitios mapeados previamente",
        icono="map",
        ruta=_sitemap_pagina.RUTA,
        worker_main=_sitemap_motor.main,
    ),
    base.Herramienta(
        id=_color_pagina.ID_HERRAMIENTA,
        nombre="Color Tools",
        descripcion="Extractor de temas desde scraping, escalas multiformato "
                    "y armonías de color.",
        icono="palette",
        ruta=_color_pagina.RUTA,
        worker_main=_color_motor.main,
    ),
]


def buscar(id_herramienta: str) -> base.Herramienta | None:
    return next((h for h in REGISTRO if h.id == id_herramienta), None)
