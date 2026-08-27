"""Utilidades compartidas para lanzar subprocesos "worker" desde cualquier herramienta.

Por que subprocesos y no correr todo en el proceso de la GUI: algunas
herramientas (el rastreador, por ejemplo) usan librerias que bloquean el hilo
o que solo pueden inicializarse una vez por proceso (Scrapy/Twisted). Lanzarlas
aparte evita congelar la ventana y permite repetir la operacion muchas veces
sin reiniciar la app.
"""

from __future__ import annotations

import sys


def comando_worker(id_herramienta: str) -> list[str]:
    """
    Arma el inicio del comando para relanzar este mismo programa en modo
    worker de una herramienta especifica (ver el "--worker <id>" en app.py).

    - Congelado (PyInstaller): sys.executable YA ES el .exe de la app, asi
      que basta re-invocarlo con "--worker <id>".
    - Fuente: sys.executable es python.exe, hace falta pasarle el script de
      entrada (sys.argv[0], que en este caso siempre es app.py).
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker", id_herramienta]
    return [sys.executable, sys.argv[0], "--worker", id_herramienta]
