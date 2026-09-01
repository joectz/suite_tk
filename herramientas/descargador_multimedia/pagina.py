"""
pagina.py — Interfaz NiceGUI del Descargador Multimedia.

Soporta selección de múltiples URLs desde el Mapeador de URLs, jerarquía por subcarpetas de rutas y deduplicación.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from nicegui import app, ui

from core.procesos import comando_worker

ID_HERRAMIENTA = "descargador_multimedia"
RUTA = "/descargador-multimedia"

CARPETA_MAPEADOR = Path(tempfile.gettempdir()) / "mapeador_urls"


class Estado:
    """Estado compartido de las descargas."""

    def __init__(self):
        self.proceso: asyncio.subprocess.Process | None = None
        self.corriendo = False
        self.inicio: datetime | None = None


E = Estado()


@app.on_shutdown
def _limpiar():
    """No dejar procesos huérfanos al cerrar la app."""
    if E.proceso and E.proceso.returncode is None:
        try:
            E.proceso.kill()
        except ProcessLookupError:
            pass


# --------------------------------------------------------------------------- #
# Selector de Carpetas
# --------------------------------------------------------------------------- #

def abrir_selector_carpeta(target_input: ui.input):
    """Abre el explorador nativo del SO (Tkinter) o un modal interactivo NiceGUI de fallback."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        inicial = target_input.value if target_input.value and Path(target_input.value).exists() else str(Path.home())
        folder = filedialog.askdirectory(initialdir=inicial, title="Seleccionar Carpeta Destino")
        root.destroy()
        if folder:
            target_input.value = folder
            return
    except Exception:
        pass

    val_actual = target_input.value.strip() if target_input.value else ""
    path_actual = Path(val_actual).expanduser() if val_actual and Path(val_actual).exists() else Path.home()
    state = {"current": path_actual}

    dialog = ui.dialog()
    with dialog, ui.card().classes("w-96 max-h-96 p-4 gap-2"):
        ui.label("📂 Seleccionar Carpeta Destino").classes("text-base font-bold")
        lbl_ruta = ui.label(str(state["current"])).classes("text-xs text-gray-500 break-all font-mono")
        container = ui.column().classes("w-full h-48 overflow-y-auto border p-2 rounded gap-1 bg-slate-50")

        def render():
            container.clear()
            p = state["current"]
            lbl_ruta.text = str(p)
            with container:
                if p.parent != p:
                    ui.button(".. (Subir nivel)", on_click=lambda: ir_a(p.parent)).props("dense flat text-color=blue").classes("w-full text-left font-bold")
                try:
                    dirs = sorted([d for d in p.iterdir() if d.is_dir() and not d.name.startswith(".")], key=lambda x: x.name.lower())
                    for d in dirs:
                        ui.button(f"📁 {d.name}", on_click=lambda d=d: ir_a(d)).props("dense flat text-color=dark").classes("w-full text-left text-xs")
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
            ui.button("Seleccionar esta carpeta", on_click=seleccionar, color="primary").props("dense")

    dialog.open()


# --------------------------------------------------------------------------- #
# Modal del Mapeador (Selección Múltiple con Checkboxes y Desplegables)
# --------------------------------------------------------------------------- #

def obtener_sitios_mapeados() -> list[dict]:
    """Lee las páginas que han sido rastreadas por el Mapeador de URLs."""
    sitios = []
    if not CARPETA_MAPEADOR.exists():
        return sitios

    for archivo in CARPETA_MAPEADOR.glob("*.json"):
        try:
            with open(archivo, "r", encoding="utf-8") as f:
                datos = json.load(f)
                if isinstance(datos, list) and len(datos) > 0:
                    sitios.append({
                        "dominio": archivo.stem,
                        "url_inicio": datos[0].get("url", f"https://{archivo.stem}"),
                        "total_paginas": len(datos),
                        "fecha": datetime.fromtimestamp(archivo.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                        "paginas": datos
                    })
        except Exception:
            pass

    return sorted(sitios, key=lambda x: x["fecha"], reverse=True)


def abrir_modal_mapeador(url_input: ui.input):
    """Modal con Checkboxes para seleccionar una, varias o todas las URLs mapeadas."""
    dialog = ui.dialog()
    with dialog, ui.card().classes("w-full max-w-4xl max-h-[90vh] p-4 gap-3"):
        
        seleccionadas: set[str] = set()

        with ui.row().classes("w-full items-center justify-between border-b pb-2"):
            ui.label("🗺️ Selección Múltiple de URLs Mapeadas").classes("text-xl font-bold")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")

        sitios = obtener_sitios_mapeados()

        if not sitios:
            ui.label("Aún no has rastreado ningún sitio en la herramienta Mapeador de URLs.") \
                .classes("text-gray-400 py-8 text-center w-full")
        else:
            with ui.column().classes("w-full h-[480px] overflow-y-auto gap-3 p-1"):
                for s in sitios:
                    todas_las_urls_sitio = [s["url_inicio"]] + [p.get("url", "") for p in s["paginas"] if p.get("url")]

                    with ui.expansion(
                        f"{s['url_inicio']}  —  [{s['total_paginas']} páginas]",
                        icon="public"
                    ).classes("w-full bg-slate-50 border rounded-lg shadow-sm"):
                        
                        with ui.row().classes("w-full justify-between items-center p-2 bg-blue-50 rounded mb-2"):
                            def marcar_todas(e, urls=todas_las_urls_sitio):
                                if e.value:
                                    seleccionadas.update(urls)
                                else:
                                    seleccionadas.difference_update(urls)

                            ui.checkbox("Seleccionar TODAS las URLs de este sitio", on_change=marcar_todas) \
                                .classes("font-bold text-xs text-blue-900")

                        # 1. URL PRINCIPAL / RAIZ CON CHECKBOX
                        with ui.row().classes("w-full items-center gap-2 bg-blue-100/60 p-2 rounded border border-blue-200"):
                            def toggle_url(e, url=s["url_inicio"]):
                                if e.value:
                                    seleccionadas.add(url)
                                else:
                                    seleccionadas.discard(url)

                            ui.checkbox(on_change=toggle_url).props("dense")
                            ui.label("⭐ Principal:").classes("text-xs font-bold text-blue-900")
                            ui.label(s["url_inicio"]).classes("text-xs font-mono font-bold text-blue-800 truncate flex-grow")

                        # 2. LISTADO DE SUB-URLS ASOCIADAS CON CHECKBOX
                        ui.label("Sub-URLs encontradas:").classes("text-xs font-bold text-gray-600 mt-2 px-1")
                        
                        with ui.column().classes("w-full max-h-56 overflow-y-auto gap-1 border bg-white rounded p-2"):
                            for pag in s["paginas"]:
                                url_hija = pag.get("url", "")
                                if not url_hija or url_hija == s["url_inicio"]:
                                    continue
                                
                                status = pag.get("status", 200)
                                titulo = pag.get("titulo") or "Sin título"
                                color_status = "green" if status == 200 else "red"

                                with ui.row().classes("w-full items-center gap-2 text-xs py-1 px-2 border-b hover:bg-slate-100 rounded"):
                                    def toggle_hija(e, url=url_hija):
                                        if e.value:
                                            seleccionadas.add(url)
                                        else:
                                            seleccionadas.discard(url)

                                    ui.checkbox(on_change=toggle_hija).props("dense")
                                    ui.badge(str(status), color=color_status).props("dense")
                                    ui.label(url_hija).classes("font-mono truncate text-blue-700 max-w-[60%]")
                                    ui.label(f"({titulo})").classes("text-gray-400 truncate text-[11px]")

        # Pie del Modal: Botón de confirmación para cargar todas las seleccionadas
        with ui.row().classes("w-full justify-between items-center border-t pt-3 mt-2"):
            ui.label("Selecciona una o más URLs para descargar").classes("text-xs text-gray-500")

            with ui.row().classes("gap-2"):
                ui.button("Cancelar", on_click=dialog.close).props("flat dense")
                
                def confirmar_seleccion():
                    if not seleccionadas:
                        ui.notify("Selecciona al menos una URL", type="warning")
                        return
                    
                    url_input.value = " , ".join(sorted(seleccionadas))
                    dialog.close()
                    ui.notify(f"Cargadas {len(seleccionadas)} URLs en el Descargador", type="positive")

                ui.button("Cargar Selección", icon="check_circle", on_click=confirmar_seleccion) \
                    .props("dense color=primary")

    dialog.open()


# --------------------------------------------------------------------------- #
# Interfaz NiceGUI Principal
# --------------------------------------------------------------------------- #

@ui.page(RUTA)
def index():
    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):

        # Barra de navegación superior
        with ui.row().classes("items-center justify-between w-full -mb-2"):
            with ui.row().classes("items-center gap-2"):
                ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")) \
                    .props("flat round dense")
                ui.label("Inicio").classes("text-sm text-gray-500")

            ui.button("Cargar del Mapeador", icon="travel_explore", 
                      on_click=lambda: abrir_modal_mapeador(url_input)) \
                .props("outlined dense color=secondary") \
                .tooltip("Ver y seleccionar URLs mapeadas previamente")

        ui.label("Descargador Multimedia").classes("text-3xl font-bold")
        ui.label("Extrae y descarga toda la media (imágenes, video, audio, etc.) de una o varias URLs").classes(
            "text-sm text-gray-500 -mt-3")

        # ---- Card de Configuración ---------------------------------------- #
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-end gap-3"):
                url_input = ui.input(
                    "URLs del sitio (separadas por coma o espacio)",
                    placeholder="https://www.ejemplo.com , https://www.ejemplo.com/2017",
                    value="https://www.ejemplo.com",
                ).classes("flex-grow").props("outlined dense").mark("url_input")

                boton_iniciar = ui.button("Iniciar Descarga", icon="download").mark("boton_iniciar")
                boton_detener = ui.button("Detener", icon="stop", color="red").mark("boton_detener")
                boton_detener.disable()

            with ui.expansion("Opciones de Descarga y Filtros", icon="tune").classes("w-full"):
                with ui.row().classes("w-full gap-3 items-center flex-wrap"):
                    output_input = ui.input(
                        "Carpeta Destino",
                        placeholder="Por defecto: media_<dominio>",
                    ).classes("flex-grow").props("outlined dense")

                    select_btn = ui.button(icon="folder_open", on_click=lambda: abrir_selector_carpeta(output_input)) \
                        .props("outlined dense").classes("w-12").tooltip("Explorar carpeta...")

                    workers = ui.number("Hilos Simultáneos", value=6, min=1, max=64,
                                        format="%d").props("outlined dense").classes("w-36")
                    timeout = ui.number("Timeout (s)", value=20, min=1,
                                        format="%d").props("outlined dense").classes("w-32")
                    min_size = ui.number("Mín. Bytes", value=0, min=0,
                                         format="%d").props("outlined dense").classes("w-32")

                with ui.row().classes("gap-6 mt-2 flex-wrap"):
                    render_js = ui.checkbox("Renderizar JavaScript (Playwright)")
                    same_domain = ui.checkbox("Solo mismo dominio")
                    no_css = ui.checkbox("Ignorar hojas CSS externas")
                    dry_run = ui.checkbox("Modo simulación (Mostrar solo URLs)")

        # ---- Card de Estado y Progreso ------------------------------------ #
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center gap-6 flex-wrap"):
                lbl_estado = ui.label("Listo").classes("text-lg font-medium")
                ui.space()
                lbl_tiempo = ui.label("0s").classes("text-sm text-gray-500")
            barra = ui.linear_progress(value=0, show_value=False).props("indeterminate=false")
            barra.set_visibility(False)

        # ---- Terminal & Logs ----------------------------------------------- #
        with ui.card().classes("w-full"):
            ui.label("Registro de Actividad").classes("text-lg font-bold")
            log_area = ui.log(max_lines=500).classes("w-full h-64 font-mono text-xs bg-slate-950 text-slate-100 p-2 rounded")

    # ----------------------------------------------------------------------- #
    # Lógica de Ejecución
    # ----------------------------------------------------------------------- #

    async def vigilar():
        """Monitorea el tiempo y estado durante la ejecución."""
        while E.corriendo:
            if E.inicio:
                seg = int((datetime.now() - E.inicio).total_seconds())
                lbl_tiempo.text = f"{seg // 60}m {seg % 60}s" if seg >= 60 else f"{seg}s"
            await asyncio.sleep(0.5)

    async def leer_stdout_stderr():
        """Transfiere los logs del proceso worker a la consola NiceGUI en vivo."""
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

    async def iniciar():
        raw_val = (url_input.value or "").strip()
        if not raw_val:
            ui.notify("Escribe al menos una URL", type="warning")
            return

        # Splitear las URLs ingresadas o seleccionadas
        lista_urls = [u.strip() for u in raw_val.replace(",", " ").split() if u.strip()]

        log_area.clear()

        cmd = [
            *comando_worker(ID_HERRAMIENTA),
            *lista_urls,
            "-w", str(int(workers.value or 6)),
            "-t", str(int(timeout.value or 20)),
            "--min-size", str(int(min_size.value or 0)),
        ]

        if output_input.value and output_input.value.strip():
            cmd.extend(["-o", output_input.value.strip()])
        if render_js.value:
            cmd.append("--render")
        if same_domain.value:
            cmd.append("--same-domain")
        if no_css.value:
            cmd.append("--no-css")
        if dry_run.value:
            cmd.append("--dry-run")

        log_area.push("$ " + " ".join(cmd))

        E.proceso = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name != "nt"),
        )

        E.corriendo = True
        E.inicio = datetime.now()
        boton_iniciar.disable()
        boton_detener.enable()
        barra.set_visibility(True)
        barra.props("indeterminate=true")
        lbl_estado.text = f"Descargando ({len(lista_urls)} páginas)..."
        lbl_estado.classes(replace="text-lg font-medium text-blue-600")

        asyncio.create_task(vigilar())
        asyncio.create_task(leer_stdout_stderr())
        asyncio.create_task(esperar_fin())

    async def esperar_fin():
        await E.proceso.wait()
        E.corriendo = False

        boton_iniciar.enable()
        boton_detener.disable()
        barra.props("indeterminate=false")
        barra.value = 1.0

        codigo = E.proceso.returncode
        if codigo == 0:
            lbl_estado.text = "Completado"
            lbl_estado.classes(replace="text-lg font-medium text-green-600")
            ui.notify("Descarga completada", type="positive")
        else:
            lbl_estado.text = "Detenido o Cancelado"
            lbl_estado.classes(replace="text-lg font-medium text-orange-600")
            ui.notify("Proceso finalizado", type="warning")

    async def detener():
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

    boton_iniciar.on_click(iniciar)
    boton_detener.on_click(detener)
    url_input.on("keydown.enter", iniciar)
