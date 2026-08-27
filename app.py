#!/usr/bin/env python3
"""
app.py — Interfaz de escritorio para scraper.py

Arquitectura:
    El crawler NO corre dentro de este proceso. Scrapy usa el reactor de
    Twisted, que solo puede arrancarse UNA vez por proceso y ademas bloquea
    el hilo. Por eso el crawl se lanza como subproceso y esta GUI se limita
    a leer el archivo .jsonl que va escribiendo. Ventajas:
      - la ventana nunca se congela
      - se puede rastrear muchas veces seguidas sin reiniciar la app
      - el boton Detener es un kill limpio del subproceso

    Ese subproceso es EL MISMO ejecutable (sys.executable) invocado con el
    flag oculto "--crawl": en modo fuente eso es "python.exe app.py --crawl",
    en modo congelado (PyInstaller onefile) es "app.exe --crawl". Asi no hace
    falta empaquetar scraper.py como binario aparte. Ver el final del archivo.

    Toda la UI vive dentro de un unico @ui.page('/') en vez del scope global:
    NiceGUI en "modo script" (UI en el scope global) reejecuta el archivo
    fuente (runpy.run_path(sys.argv[0])) en cada conexion, algo que no existe
    cuando el archivo esta congelado en un .exe (sys.argv[0] es el binario,
    no un .py). "Modo pagina" evita esa reejecucion.

Uso:
    python app.py            # ventana de escritorio (nativa)
    python app.py --web      # en el navegador, http://localhost:8080

Requisitos:
    pip install nicegui scrapy
    pip install pywebview     # solo para el modo ventana nativa
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import multiprocessing
import os
import sys
import signal
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from nicegui import ui, app

CARPETA = Path(tempfile.gettempdir()) / "mapeador_urls"
CARPETA.mkdir(exist_ok=True)


class Estado:
    """Estado compartido de un rastreo."""

    def __init__(self):
        self.proceso: asyncio.subprocess.Process | None = None
        self.filas: list[dict] = []
        self.vistas: set[str] = set()
        self.jsonl: Path | None = None
        self.corriendo = False
        self.inicio: datetime | None = None
        self.log: list[str] = []


E = Estado()


def comando_worker() -> list[str]:
    """
    Arma el inicio del comando para lanzar un crawl en un subproceso propio.

    - Congelado (PyInstaller): sys.executable YA ES app.exe, asi que basta
      re-invocarlo con "--crawl" para que el bloque final salte a modo worker.
    - Fuente: sys.executable es python.exe, hace falta pasarle __file__.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--crawl"]
    return [sys.executable, __file__, "--crawl"]


@app.on_shutdown
def limpiar():
    """No dejar el crawler huérfano si se cierra la ventana."""
    if E.proceso and E.proceso.returncode is None:
        try:
            E.proceso.kill()
        except ProcessLookupError:
            pass


# --------------------------------------------------------------------------- #
# Interfaz
# --------------------------------------------------------------------------- #

@ui.page("/")
def index():
    ui.colors(primary="#2563eb")

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):

        ui.label("Mapeador de URLs").classes("text-3xl font-bold")
        ui.label("Rastrea un sitio y lista todas sus páginas").classes(
            "text-sm text-gray-500 -mt-3")

        # ---- Configuracion -------------------------------------------- #
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-end gap-3"):
                url_input = ui.input(
                    "URL del sitio",
                    placeholder="https://www.ejemplo.com",
                    value="https://www.ejemplos.com",
                ).classes("flex-grow").props("outlined dense").mark("url_input")

                boton_iniciar = ui.button("Rastrear", icon="play_arrow").mark("boton_iniciar")
                boton_detener = ui.button("Detener", icon="stop", color="red").mark("boton_detener")
                boton_detener.disable()

            with ui.expansion("Opciones avanzadas", icon="tune").classes("w-full"):
                with ui.row().classes("w-full gap-4 items-center flex-wrap"):
                    max_pag = ui.number("Máx. páginas", value=500, min=0,
                                        format="%d").props("outlined dense").classes("w-36")
                    profundidad = ui.number("Profundidad", value=10, min=0,
                                            format="%d").props("outlined dense").classes("w-32")
                    delay = ui.number("Delay (s)", value=1.0, min=0, step=0.5,
                                      format="%.1f").props("outlined dense").classes("w-32")
                    concurrencia = ui.number("Concurrencia", value=4, min=1,
                                             format="%d").props("outlined dense").classes("w-32")
                with ui.row().classes("gap-6 mt-2 flex-wrap"):
                    ignorar_query = ui.checkbox("Ignorar parámetros ?a=1")
                    obedecer_robots = ui.checkbox("Respetar robots.txt", value=True)
                    incluir_sitemap = ui.checkbox(
                        "Incluir sitemap.xml (encuentra páginas sin enlaces internos)",
                        value=True)

        # ---- Progreso ---------------------------------------------------- #
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center gap-6 flex-wrap"):
                lbl_estado = ui.label("Listo").classes("text-lg font-medium")
                ui.space()
                lbl_paginas = ui.label("0 páginas").classes("text-sm")
                lbl_errores = ui.label("0 errores").classes("text-sm text-red-600")
                lbl_tiempo = ui.label("0s").classes("text-sm text-gray-500")
            barra = ui.linear_progress(value=0, show_value=False).props("indeterminate=false")
            barra.set_visibility(False)

        # ---- Resultados ---------------------------------------------------- #
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center gap-3"):
                filtro = ui.input(placeholder="Filtrar por URL o título...").props(
                    "outlined dense clearable").classes("flex-grow")
                ui.space()
                boton_json = ui.button("JSON", icon="download").props("outline")
                boton_csv = ui.button("CSV", icon="download").props("outline")

            tabla = ui.table(
                columns=[
                    {"name": "status", "label": "Estado", "field": "status",
                     "align": "center", "sortable": True},
                    {"name": "url", "label": "URL", "field": "url",
                     "align": "left", "sortable": True},
                    {"name": "titulo", "label": "Título", "field": "titulo",
                     "align": "left", "sortable": True},
                    {"name": "profundidad", "label": "Nivel", "field": "profundidad",
                     "align": "center", "sortable": True},
                ],
                rows=[],
                row_key="url",
                pagination={"rowsPerPage": 25},
            ).classes("w-full")

            # Colorear el codigo de estado: verde OK, rojo error
            tabla.add_slot("body-cell-status", r"""
                <q-td :props="props">
                    <q-badge :color="props.value === 200 ? 'green'
                                     : (props.value === 0 ? 'grey' : 'red')">
                        {{ props.value === 0 ? 'ERR' : props.value }}
                    </q-badge>
                </q-td>
            """)

        # ---- Log ------------------------------------------------------------ #
        with ui.expansion("Registro técnico", icon="terminal").classes("w-full"):
            log_area = ui.log(max_lines=300).classes("w-full h-48 font-mono text-xs")

    # ----------------------------------------------------------------------- #
    # Logica (cierra sobre los widgets de esta pagina)
    # ----------------------------------------------------------------------- #

    def refrescar_tabla():
        """Aplica el filtro de texto y vuelca las filas en la tabla."""
        texto = (filtro.value or "").lower().strip()
        if texto:
            tabla.rows = [f for f in E.filas
                          if texto in f["url"].lower() or texto in f["titulo"].lower()]
        else:
            tabla.rows = list(E.filas)
        tabla.update()

    filtro.on_value_change(lambda _: refrescar_tabla())

    def leer_nuevas_lineas():
        """Lee del .jsonl solo lo que aun no se ha mostrado."""
        if not E.jsonl or not E.jsonl.exists():
            return 0

        nuevas = 0
        with open(E.jsonl, encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    d = json.loads(linea)
                except json.JSONDecodeError:
                    continue  # ultima linea a medio escribir; se lee en el siguiente ciclo
                if d["url"] in E.vistas:
                    continue
                E.vistas.add(d["url"])
                E.filas.append(d)
                nuevas += 1
        return nuevas

    async def vigilar():
        """Refresca la UI mientras el subproceso trabaja."""
        while E.corriendo:
            if leer_nuevas_lineas():
                refrescar_tabla()

            errores = sum(1 for f in E.filas if f["status"] != 200)
            lbl_paginas.text = f"{len(E.filas)} páginas"
            lbl_errores.text = f"{errores} errores"

            if E.inicio:
                seg = int((datetime.now() - E.inicio).total_seconds())
                lbl_tiempo.text = f"{seg // 60}m {seg % 60}s" if seg >= 60 else f"{seg}s"

            objetivo = int(max_pag.value or 0)
            if objetivo > 0:
                barra.value = min(len(E.filas) / objetivo, 1.0)

            await asyncio.sleep(0.4)

    async def leer_stderr():
        """Vuelca el log de Scrapy en el panel tecnico."""
        if not E.proceso or not E.proceso.stderr:
            return
        async for raw in E.proceso.stderr:
            linea = raw.decode("utf-8", "ignore").rstrip()
            if linea:
                log_area.push(linea)

    async def iniciar():
        url = (url_input.value or "").strip()
        if not url:
            ui.notify("Escribe una URL", type="warning")
            return

        # Reset
        E.filas.clear()
        E.vistas.clear()
        tabla.rows = []
        tabla.update()
        log_area.clear()

        dominio = urlparse(url).netloc.lower()

        # Quitar www.
        if dominio.startswith("www."):
            dominio = dominio[4:]

        # Reemplazar caracteres que puedan causar problemas en nombres de archivo
        dominio = dominio.replace(":", "_")

        base = CARPETA / dominio
        E.jsonl = Path(f"{base}.jsonl")

        cmd = [
            *comando_worker(), url,
            "-o", str(base),
            "--max-paginas", str(int(max_pag.value or 0)),
            "--profundidad", str(int(profundidad.value or 0)),
            "--delay", str(float(delay.value or 1)),
            "--concurrencia", str(int(concurrencia.value or 4)),
        ]
        if ignorar_query.value:
            cmd.append("--ignorar-query")
        if obedecer_robots.value:
            cmd.append("--obedecer-robots")
        if not incluir_sitemap.value:
            cmd.append("--sin-sitemap")

        log_area.push("$ " + " ".join(cmd))

        E.proceso = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            # Grupo propio para poder matar Scrapy y sus hijos de una
            start_new_session=(os.name != "nt"),
        )

        E.corriendo = True
        E.inicio = datetime.now()
        boton_iniciar.disable()
        boton_detener.enable()
        barra.set_visibility(True)
        barra.value = 0
        lbl_estado.text = "Rastreando..."
        lbl_estado.classes(replace="text-lg font-medium text-blue-600")

        asyncio.create_task(vigilar())
        asyncio.create_task(leer_stderr())
        asyncio.create_task(esperar_fin())

    async def esperar_fin():
        await E.proceso.wait()
        await asyncio.sleep(0.6)          # margen para el ultimo flush del feed
        E.corriendo = False
        leer_nuevas_lineas()
        refrescar_tabla()

        boton_iniciar.enable()
        boton_detener.disable()
        barra.value = 1.0

        codigo = E.proceso.returncode
        if codigo == 0:
            lbl_estado.text = "Completado"
            lbl_estado.classes(replace="text-lg font-medium text-green-600")
            ui.notify(f"Listo: {len(E.filas)} páginas", type="positive")
        else:
            lbl_estado.text = "Detenido"
            lbl_estado.classes(replace="text-lg font-medium text-orange-600")
            ui.notify(f"Detenido con {len(E.filas)} páginas rastreadas", type="warning")

    async def detener():
        if not E.proceso or E.proceso.returncode is not None:
            return
        lbl_estado.text = "Deteniendo..."

        def señal(sig):
            try:
                if os.name == "nt":
                    E.proceso.terminate()
                else:
                    os.killpg(os.getpgid(E.proceso.pid), sig)
            except (ProcessLookupError, PermissionError):
                E.proceso.terminate()

        # Scrapy replica el comportamiento de Ctrl-C: el PRIMER SIGINT pide un
        # cierre ordenado (espera a las peticiones en vuelo), el SEGUNDO fuerza.
        # Por eso hay que escalar en vez de esperar indefinidamente al primero.
        señal(signal.SIGINT)
        try:
            await asyncio.wait_for(E.proceso.wait(), timeout=5)
            return
        except asyncio.TimeoutError:
            log_area.push("Cierre ordenado lento, enviando segunda señal...")

        señal(signal.SIGINT)
        try:
            await asyncio.wait_for(E.proceso.wait(), timeout=4)
            return
        except asyncio.TimeoutError:
            log_area.push("Forzando kill...")
            E.proceso.kill()
            await E.proceso.wait()

    def descargar_json():
        if not E.filas:
            ui.notify("No hay resultados todavía", type="warning")
            return

        contenido = json.dumps(E.filas, ensure_ascii=False, indent=2)
        ui.download(contenido.encode("utf-8"), "paginas.json")

    def descargar_csv():
        if not E.filas:
            ui.notify("No hay resultados todavía", type="warning")
            return

        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(E.filas[0].keys()))
        w.writeheader()
        w.writerows(E.filas)

        ui.download(
            buf.getvalue().encode("utf-8-sig"),
            "paginas.csv"
        )

    boton_iniciar.on_click(iniciar)
    boton_detener.on_click(detener)
    boton_json.on_click(descargar_json)
    boton_csv.on_click(descargar_csv)
    url_input.on("keydown.enter", iniciar)


# --------------------------------------------------------------------------- #
# Modo worker: este mismo ejecutable relanzado con "--crawl" hace el rastreo
# --------------------------------------------------------------------------- #

def _modo_crawl(argv_resto: list[str]) -> int:
    import scraper
    sys.argv = [sys.argv[0], *argv_resto]
    return scraper.main()


# --------------------------------------------------------------------------- #

if __name__ in {"__main__", "__mp_main__"}:
    multiprocessing.freeze_support()

    if "--crawl" in sys.argv:
        resto = [a for a in sys.argv[1:] if a != "--crawl"]
        sys.exit(_modo_crawl(resto))

    modo_web = "--web" in sys.argv
    if not modo_web:
        # pywebview bloquea toda descarga por defecto (webview.settings
        # ALLOW_DOWNLOADS = False). Sin esto, los botones JSON/CSV
        # (ui.download) no hacen nada en la ventana nativa.
        app.native.settings["ALLOW_DOWNLOADS"] = True

    ui.run(
        title="Mapeador de URLs",
        native=not modo_web,
        window_size=(1200, 850) if not modo_web else None,
        reload=False,
        port=8080,
        favicon="🔗",
    )
