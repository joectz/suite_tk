"""
pagina.py — Interfaz NiceGUI del Generador de Sitemap.

Arquitectura:
    El generador se lanza como subproceso (ver core.procesos.comando_worker)
    y esta GUI lee la salida estándar en tiempo real para mostrar el progreso.
    Patrón idéntico al Mapeador de URLs y al Descargador Multimedia.

    Toda la UI vive dentro de un @ui.page(...) — modo página, no modo script.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from nicegui import app, ui

from core.procesos import comando_worker

ID_HERRAMIENTA = "generador_sitemap"
RUTA = "/generador-sitemap"

CARPETA_MAPEADOR = Path(tempfile.gettempdir()) / "mapeador_urls"


class Estado:
    """Estado compartido de una generación de sitemap."""

    def __init__(self):
        self.proceso: asyncio.subprocess.Process | None = None
        self.corriendo = False
        self.inicio: datetime | None = None
        self.archivos_generados: list[Path] = []


E = Estado()


@app.on_shutdown
def _limpiar():
    """No dejar el worker huérfano si se cierra la ventana."""
    if E.proceso and E.proceso.returncode is None:
        try:
            E.proceso.kill()
        except ProcessLookupError:
            pass


# --------------------------------------------------------------------------- #
# Lectura de sitios ya mapeados
# --------------------------------------------------------------------------- #


def _obtener_sitios_mapeados() -> list[dict]:
    """Lee los JSON generados por el Mapeador de URLs."""
    sitios = []
    if not CARPETA_MAPEADOR.exists():
        return sitios

    for archivo in CARPETA_MAPEADOR.glob("*.json"):
        try:
            with open(archivo, "r", encoding="utf-8") as f:
                datos = json.load(f)
                if isinstance(datos, list) and len(datos) > 0:
                    sitios.append(
                        {
                            "dominio": archivo.stem,
                            "url_inicio": datos[0].get(
                                "url", f"https://{archivo.stem}"
                            ),
                            "total_paginas": len(datos),
                            "fecha": datetime.fromtimestamp(
                                archivo.stat().st_mtime
                            ).strftime("%Y-%m-%d %H:%M"),
                            "ruta_json": str(archivo),
                        }
                    )
        except Exception:
            pass

    return sorted(sitios, key=lambda x: x["fecha"], reverse=True)


# --------------------------------------------------------------------------- #
# Selector de carpeta
# --------------------------------------------------------------------------- #


def _abrir_selector_carpeta(target_input: ui.input):
    """Abre el explorador nativo del SO (Tkinter) o un modal NiceGUI de fallback."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        inicial = (
            target_input.value
            if target_input.value and Path(target_input.value).exists()
            else str(Path.home())
        )
        folder = filedialog.askdirectory(
            initialdir=inicial, title="Seleccionar Carpeta Destino"
        )
        root.destroy()
        if folder:
            target_input.value = folder
            return
    except Exception:
        pass

    # Fallback: explorador NiceGUI
    val_actual = target_input.value.strip() if target_input.value else ""
    path_actual = (
        Path(val_actual).expanduser()
        if val_actual and Path(val_actual).exists()
        else Path.home()
    )
    state = {"current": path_actual}

    dialog = ui.dialog()
    with dialog, ui.card().classes("w-96 max-h-96 p-4 gap-2"):
        ui.label("📂 Seleccionar Carpeta Destino").classes("text-base font-bold")
        lbl_ruta = ui.label(str(state["current"])).classes(
            "text-xs text-gray-500 break-all font-mono"
        )
        container = ui.column().classes(
            "w-full h-48 overflow-y-auto border p-2 rounded gap-1 bg-slate-50"
        )

        def render():
            container.clear()
            p = state["current"]
            lbl_ruta.text = str(p)
            with container:
                if p.parent != p:
                    ui.button(
                        ".. (Subir nivel)", on_click=lambda: ir_a(p.parent)
                    ).props("dense flat text-color=blue").classes(
                        "w-full text-left font-bold"
                    )
                try:
                    dirs = sorted(
                        [
                            d
                            for d in p.iterdir()
                            if d.is_dir() and not d.name.startswith(".")
                        ],
                        key=lambda x: x.name.lower(),
                    )
                    for d in dirs:
                        ui.button(f"📁 {d.name}", on_click=lambda d=d: ir_a(d)).props(
                            "dense flat text-color=dark"
                        ).classes("w-full text-left text-xs")
                except PermissionError:
                    ui.notify("Permiso denegado en esta carpeta", type="warning")

        def ir_a(nueva_ruta):
            state["current"] = nueva_ruta
            render()

        def seleccionar():
            target_input.value = str(state["current"])
            dialog.close()

        render()

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancelar", on_click=dialog.close).props("flat dense")
            ui.button(
                "Seleccionar esta carpeta", on_click=seleccionar, color="primary"
            ).props("dense")

    dialog.open()


# --------------------------------------------------------------------------- #
# Modal de importación del Mapeador
# --------------------------------------------------------------------------- #


def _abrir_modal_mapeador(json_input: ui.input, urlbase_input: ui.input):
    """Modal para seleccionar un sitio ya mapeado."""
    dialog = ui.dialog()
    with dialog, ui.card().classes("w-full max-w-3xl max-h-[80vh] p-4 gap-3"):

        with ui.row().classes("w-full items-center justify-between border-b pb-2"):
            ui.label("Sitios Mapeados Disponibles").classes("text-xl font-bold")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")

        sitios = _obtener_sitios_mapeados()

        if not sitios:
            ui.label(
                "Aún no has rastreado ningún sitio con el Mapeador de URLs."
            ).classes("text-gray-400 py-8 text-center w-full")
        else:
            with ui.column().classes("w-full max-h-[400px] overflow-y-auto gap-2 p-1"):
                for s in sitios:
                    with ui.card().classes(
                        "w-full cursor-pointer hover:shadow-md transition-shadow border"
                    ).on(
                        "click",
                        lambda _e, s=s: _seleccionar_sitio(
                            s, json_input, urlbase_input, dialog
                        ),
                    ):
                        with ui.row().classes("w-full items-center gap-4"):
                            ui.icon("public").classes("text-2xl text-primary")
                            with ui.column().classes("flex-grow gap-0"):
                                ui.label(s["url_inicio"]).classes(
                                    "text-sm font-bold font-mono text-blue-800"
                                )
                                with ui.row().classes("gap-4 text-xs text-gray-500"):
                                    ui.label(f" {s['total_paginas']} páginas")
                                    ui.label(f" {s['fecha']}")

        with ui.row().classes("w-full justify-end border-t pt-3 mt-2"):
            ui.button("Cerrar", on_click=dialog.close).props("flat dense")

    dialog.open()


def _seleccionar_sitio(
    sitio: dict, json_input: ui.input, urlbase_input: ui.input, dialog
):
    """Callback al seleccionar un sitio del modal."""
    json_input.value = sitio["ruta_json"]

    # Pre-rellenar URL base desde la URL de inicio del mapeador
    parsed = urlparse(sitio["url_inicio"])
    urlbase_input.value = f"{parsed.scheme}://{parsed.netloc}"

    dialog.close()
    ui.notify(
        f"Sitio cargado: {sitio['url_inicio']} ({sitio['total_paginas']} URLs)",
        type="positive",
    )


# --------------------------------------------------------------------------- #
# Interfaz principal
# --------------------------------------------------------------------------- #


@ui.page(RUTA)
def index():
    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):

        # Barra de navegación
        with ui.row().classes("items-center gap-2 -mb-2"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
                "flat round dense"
            )
            ui.label("Inicio").classes("text-sm text-gray-500")

        ui.label("Generador de Sitemap").classes("text-3xl font-bold")
        ui.label(
            "Genera un sitemap.xml SEO-ready a partir de las URLs mapeadas"
        ).classes("text-sm text-gray-500 -mt-3")

        # ---- Configuración ------------------------------------------------ #
        with ui.card().classes("w-full"):
            ui.label("Fuente de datos").classes("text-lg font-bold mb-1")

            with ui.row().classes("w-full items-end gap-3"):
                json_input = (
                    ui.input(
                        "Ruta al JSON del Mapeador",
                        placeholder="/tmp/mapeador_urls/ejemplo.json",
                    )
                    .classes("flex-grow")
                    .props("outlined dense")
                    .mark("json_input")
                )

                ui.button(
                    "Importar del Mapeador",
                    icon="travel_explore",
                    on_click=lambda: _abrir_modal_mapeador(json_input, urlbase_input),
                ).props("outlined dense color=secondary").tooltip(
                    "Seleccionar un sitio ya rastreado"
                )

            with ui.row().classes("w-full items-end gap-3 mt-2"):
                urlbase_input = (
                    ui.input(
                        "URL base del sitio",
                        placeholder="https://www.ejemplo.com",
                    )
                    .classes("flex-grow")
                    .props("outlined dense")
                    .mark("urlbase_input")
                )

            with ui.row().classes("w-full items-end gap-3 mt-2"):
                output_input = (
                    ui.input(
                        "Carpeta de salida",
                        placeholder="(por defecto: misma carpeta que el JSON)",
                    )
                    .classes("flex-grow")
                    .props("outlined dense")
                )

                ui.button(
                    icon="folder_open",
                    on_click=lambda: _abrir_selector_carpeta(output_input),
                ).props("outlined dense").classes("w-12").tooltip("Explorar carpeta...")

        # ---- Opciones avanzadas ------------------------------------------ #
        with ui.card().classes("w-full"):
            with ui.expansion("Opciones avanzadas", icon="tune").classes("w-full"):
                with ui.column().classes("w-full gap-3"):
                    ui.label("Modo de <lastmod>").classes("text-sm font-bold")
                    modo_lastmod = ui.radio(
                        {
                            "head": "HEAD en tiempo real (más preciso, más lento)",
                            "rastreo": "Fecha del rastreo (rápido, menos preciso)",
                        },
                        value="rastreo",
                    ).props("dense")

                    with ui.row().classes("gap-4 items-center flex-wrap"):
                        workers_head = (
                            ui.number(
                                "Hilos para HEAD",
                                value=8,
                                min=1,
                                max=32,
                                format="%d",
                            )
                            .props("outlined dense")
                            .classes("w-36")
                        )
                        timeout_input = (
                            ui.number(
                                "Timeout (s)",
                                value=10,
                                min=1,
                                format="%d",
                            )
                            .props("outlined dense")
                            .classes("w-32")
                        )

                    with ui.row().classes("gap-6 mt-1 flex-wrap"):
                        chk_segmentar = ui.checkbox(
                            "Segmentar por tipo de contenido (HTML, PDF, imágenes…)"
                        )
                        chk_robots = ui.checkbox(
                            "Verificar robots.txt del sitio", value=True
                        )

        # ---- Preview ----------------------------------------------------- #
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label("Preview de URLs").classes("text-lg font-bold")
                ui.space()
                lbl_stats = ui.label("").classes("text-sm text-gray-500")
                boton_preview = ui.button("Cargar preview", icon="visibility").props(
                    "outline dense"
                )

            filtro = (
                ui.input(placeholder="Filtrar por URL o tipo...")
                .props("outlined dense clearable")
                .classes("w-full mt-2")
            )

            tabla = ui.table(
                columns=[
                    {
                        "name": "status",
                        "label": "Estado",
                        "field": "status",
                        "align": "center",
                        "sortable": True,
                    },
                    {
                        "name": "url",
                        "label": "URL",
                        "field": "url",
                        "align": "left",
                        "sortable": True,
                    },
                    {
                        "name": "tipo",
                        "label": "Tipo",
                        "field": "tipo",
                        "align": "center",
                        "sortable": True,
                    },
                    {
                        "name": "incluida",
                        "label": "En Sitemap",
                        "field": "incluida",
                        "align": "center",
                        "sortable": True,
                    },
                ],
                rows=[],
                row_key="url",
                pagination={"rowsPerPage": 25},
            ).classes("w-full")

            # Colorear estado
            tabla.add_slot(
                "body-cell-status",
                r"""
                <q-td :props="props">
                    <q-badge :color="props.value === 200 ? 'green'
                                     : (props.value === 0 ? 'grey' : 'red')">
                        {{ props.value === 0 ? 'ERR' : props.value }}
                    </q-badge>
                </q-td>
            """,
            )

            # Colorear inclusión
            tabla.add_slot(
                "body-cell-incluida",
                r"""
                <q-td :props="props">
                    <q-badge :color="props.value === 'Sí' ? 'green' : 'orange'">
                        {{ props.value }}
                    </q-badge>
                </q-td>
            """,
            )

        # ---- Acción ------------------------------------------------------ #
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center gap-6 flex-wrap"):
                lbl_estado = ui.label("Listo").classes("text-lg font-medium")
                ui.space()
                lbl_tiempo = ui.label("0s").classes("text-sm text-gray-500")
            barra = ui.linear_progress(value=0, show_value=False).props(
                "indeterminate=false"
            )
            barra.set_visibility(False)

            with ui.row().classes("w-full gap-3 mt-2"):
                boton_generar = ui.button("Generar Sitemap", icon="map").mark(
                    "boton_generar"
                )
                boton_detener = ui.button("Detener", icon="stop", color="red").mark(
                    "boton_detener"
                )
                boton_detener.disable()

        # ---- Log --------------------------------------------------------- #
        with ui.expansion("Registro técnico", icon="terminal").classes("w-full"):
            log_area = ui.log(max_lines=500).classes("w-full h-48 font-mono text-xs")

        # ---- Resultados -------------------------------------------------- #
        card_resultados = ui.card().classes("w-full")
        card_resultados.set_visibility(False)
        with card_resultados:
            ui.label("Archivos generados").classes("text-lg font-bold")
            container_archivos = ui.column().classes("w-full gap-2")

    # ----------------------------------------------------------------------- #
    # Lógica
    # ----------------------------------------------------------------------- #

    def cargar_preview():
        """Carga el JSON y muestra un preview de qué URLs entrarán."""
        ruta = (json_input.value or "").strip()
        if not ruta:
            ui.notify("Selecciona un archivo JSON primero", type="warning")
            return

        ruta_p = Path(ruta)
        if not ruta_p.exists():
            ui.notify(f"No se encontró: {ruta}", type="negative")
            return

        try:
            with open(ruta_p, encoding="utf-8") as f:
                items_raw = json.load(f)
        except Exception as e:
            ui.notify(f"Error al leer JSON: {e}", type="negative")
            return

        if not isinstance(items_raw, list):
            ui.notify("El JSON no es una lista de URLs", type="negative")
            return

        from herramientas.sitemap.filtros import debe_incluir, clasificar_tipo

        filas = []
        incluidas = 0
        excluidas = 0
        for it in items_raw:
            inc = debe_incluir(it)
            if inc:
                incluidas += 1
            else:
                excluidas += 1
            filas.append(
                {
                    "status": it.get("status", 0),
                    "url": it.get("url", ""),
                    "tipo": clasificar_tipo(it),
                    "incluida": "Sí" if inc else "No",
                }
            )

        lbl_stats.text = (
            f"{incluidas} incluidas · {excluidas} excluidas · {len(items_raw)} total"
        )
        tabla.rows = filas
        tabla.update()

    def filtrar_tabla():
        """Aplica el filtro de texto a la tabla."""
        texto = (filtro.value or "").lower().strip()
        if not texto:
            cargar_preview()
            return
        tabla.rows = [
            f
            for f in tabla.rows
            if texto in f["url"].lower() or texto in f["tipo"].lower()
        ]
        tabla.update()

    filtro.on_value_change(lambda _: filtrar_tabla())
    boton_preview.on_click(cargar_preview)

    async def vigilar():
        """Monitorea el tiempo durante la ejecución."""
        while E.corriendo:
            if E.inicio:
                seg = int((datetime.now() - E.inicio).total_seconds())
                lbl_tiempo.text = (
                    f"{seg // 60}m {seg % 60}s" if seg >= 60 else f"{seg}s"
                )
            await asyncio.sleep(0.5)

    async def leer_stdout_stderr():
        """Transfiere los logs del proceso worker al panel en vivo."""
        if not E.proceso:
            return

        async def _leer_stream(stream):
            if not stream:
                return
            async for raw in stream:
                linea = raw.decode("utf-8", "ignore").rstrip()
                if linea:
                    log_area.push(linea)

        await asyncio.gather(
            _leer_stream(E.proceso.stdout),
            _leer_stream(E.proceso.stderr),
        )

    async def generar():
        """Lanza el worker de generación de sitemap."""
        ruta_json = (json_input.value or "").strip()
        url_base = (urlbase_input.value or "").strip()

        if not ruta_json:
            ui.notify("Selecciona un archivo JSON del Mapeador", type="warning")
            return
        if not url_base:
            ui.notify("Indica la URL base del sitio", type="warning")
            return

        log_area.clear()
        E.archivos_generados.clear()
        card_resultados.set_visibility(False)
        container_archivos.clear()

        cmd = [
            *comando_worker(ID_HERRAMIENTA),
            "--json",
            ruta_json,
            "--url-base",
            url_base,
            "--lastmod",
            modo_lastmod.value or "rastreo",
            "--workers",
            str(int(workers_head.value or 8)),
            "--timeout",
            str(int(timeout_input.value or 10)),
        ]

        carpeta_salida = (output_input.value or "").strip()
        if carpeta_salida:
            cmd.extend(["--output", carpeta_salida])
        if chk_segmentar.value:
            cmd.append("--segmentar")
        if not chk_robots.value:
            cmd.append("--sin-robots")

        log_area.push("$ " + " ".join(cmd))

        E.proceso = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name != "nt"),
        )

        E.corriendo = True
        E.inicio = datetime.now()
        boton_generar.disable()
        boton_detener.enable()
        barra.set_visibility(True)
        barra.props("indeterminate=true")
        lbl_estado.text = "Generando sitemap..."
        lbl_estado.classes(replace="text-lg font-medium text-blue-600")

        asyncio.create_task(vigilar())
        asyncio.create_task(leer_stdout_stderr())
        asyncio.create_task(esperar_fin())

    async def esperar_fin():
        """Espera a que el worker termine y actualiza la UI."""
        await E.proceso.wait()
        E.corriendo = False

        boton_generar.enable()
        boton_detener.disable()
        barra.props("indeterminate=false")
        barra.value = 1.0

        codigo = E.proceso.returncode
        if codigo == 0:
            lbl_estado.text = "Completado"
            lbl_estado.classes(replace="text-lg font-medium text-green-600")
            ui.notify("Sitemap generado exitosamente", type="positive")
            _mostrar_resultados()
        else:
            lbl_estado.text = "Error"
            lbl_estado.classes(replace="text-lg font-medium text-red-600")
            ui.notify("El generador terminó con errores", type="negative")

    def _mostrar_resultados():
        """Busca los XML generados y muestra botones de descarga."""
        carpeta_salida = (output_input.value or "").strip()
        if carpeta_salida:
            carpeta = Path(carpeta_salida)
        else:
            ruta_json = (json_input.value or "").strip()
            carpeta = Path(ruta_json).parent if ruta_json else None

        if not carpeta or not carpeta.exists():
            return

        xmls = sorted(carpeta.glob("sitemap*.xml"))
        if not xmls:
            return

        card_resultados.set_visibility(True)
        with container_archivos:
            for xml_path in xmls:
                tamano = xml_path.stat().st_size
                tamano_str = (
                    f"{tamano:,} bytes"
                    if tamano < 1024 * 1024
                    else f"{tamano / 1024 / 1024:.1f} MB"
                )

                with ui.row().classes("w-full items-center gap-3 border rounded p-2"):
                    ui.icon("description").classes("text-xl text-green-600")
                    ui.label(xml_path.name).classes(
                        "text-sm font-mono font-bold flex-grow"
                    )
                    ui.label(tamano_str).classes("text-xs text-gray-500")
                    ui.button(
                        "Descargar",
                        icon="download",
                        on_click=lambda _e, p=xml_path: ui.download(
                            p.read_bytes(),
                            p.name,
                        ),
                    ).props("outline dense")

    async def detener():
        """Detiene el worker si está corriendo."""
        if not E.proceso or E.proceso.returncode is not None:
            return
        lbl_estado.text = "Deteniendo..."

        try:
            if os.name == "nt":
                E.proceso.terminate()
            else:
                os.killpg(os.getpgid(E.proceso.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            E.proceso.kill()

    boton_generar.on_click(generar)
    boton_detener.on_click(detener)
