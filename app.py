#!/usr/bin/env python3
"""
app.py — Punto de entrada de Herramientas Digixonic.

Arquitectura modular:
    Cada herramienta vive en su propia carpeta bajo herramientas/, con su
    propia pagina NiceGUI y (si hace falta) su propio motor de subproceso.
    Se registran en herramientas/registro.py y aparecen automaticamente como
    una tarjeta en la pantalla principal (@ui.page("/") en este archivo).
    Agregar una herramienta nueva NO toca este archivo — ver el docstring de
    herramientas/registro.py.

    Los subprocesos "worker" (rastreos, descargas, lo que sea pesado o
    bloqueante) se lanzan relanzando este mismo ejecutable con
    "--worker <id_herramienta> <argumentos...>" (ver core/procesos.py). Asi
    funciona igual en modo fuente ("python app.py") y en el .exe congelado
    con PyInstaller (donde sys.executable ya es el propio .exe).

    Toda pagina vive dentro de un @ui.page(...) propio, nunca en el scope
    global: NiceGUI en "modo script" (UI en el scope global) reejecuta el
    archivo fuente (runpy.run_path(sys.argv[0])) en cada conexion, algo que
    no existe cuando el archivo esta congelado en un .exe (sys.argv[0] es el
    binario, no un .py). "Modo pagina" evita esa reejecucion.

Uso:
    python app.py            # ventana de escritorio (nativa)
    python app.py --web      # en el navegador, http://localhost:8080

Requisitos:
    pip install -r requirements.txt
"""

from __future__ import annotations

import multiprocessing
import sys

from nicegui import app, ui

from herramientas.registro import REGISTRO, buscar


# --------------------------------------------------------------------------- #
# Pantalla principal: una tarjeta por herramienta registrada
# --------------------------------------------------------------------------- #

@ui.page("/")
def inicio():
    ui.colors(primary="#2563eb")

    with ui.column().classes("w-full max-w-5xl mx-auto p-6 gap-4"):
        ui.label("Herramientas de Website").classes("text-3xl font-bold")
        ui.label("Elige una herramienta").classes("text-sm text-gray-500 -mt-3")

        with ui.row().classes("gap-4 flex-wrap mt-2"):
            for herramienta in REGISTRO:
                with ui.card().classes(
                    "w-72 cursor-pointer hover:shadow-lg transition-shadow"
                ).on("click", lambda h=herramienta: ui.navigate.to(h.ruta)):
                    ui.icon(herramienta.icono).classes("text-4xl text-primary")
                    ui.label(herramienta.nombre).classes("text-lg font-bold mt-2")
                    ui.label(herramienta.descripcion).classes("text-sm text-gray-500")


# --------------------------------------------------------------------------- #

if __name__ in {"__main__", "__mp_main__"}:
    multiprocessing.freeze_support()

    if "--worker" in sys.argv:
        idx = sys.argv.index("--worker")
        id_herramienta = sys.argv[idx + 1]
        resto = sys.argv[idx + 2:]

        herramienta = buscar(id_herramienta)
        if herramienta is None or herramienta.worker_main is None:
            print(f"Herramienta desconocida o sin worker: {id_herramienta}", file=sys.stderr)
            sys.exit(1)
        sys.exit(herramienta.worker_main(resto))

    modo_web = "--web" in sys.argv
    if not modo_web:
        # pywebview bloquea toda descarga por defecto (webview.settings
        # ALLOW_DOWNLOADS = False). Sin esto, cualquier boton de descarga
        # (ui.download) de cualquier herramienta no hace nada en la ventana
        # nativa.
        app.native.settings["ALLOW_DOWNLOADS"] = True

    ui.run(
        title="Herramientas Digixonic",
        native=not modo_web,
        window_size=(1200, 850) if not modo_web else None,
        reload=False,
        port=8080,
        favicon="🧰",
    )
