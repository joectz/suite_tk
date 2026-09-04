"""
pagina.py — Interfaz estandarizada para Color Tools.

Incluye:
  - Extracción de tema desde Web Scraping (1 URL o selección del historial).
  - Escalas de color con exportación multiformato (CSS, Tailwind, JavaScript, Python, JSON, SCSS).
  - Selector de formatos con opción "Todos" seleccionada por defecto.
  - Armonías de color (Complementario, Análogo, Triádico, Monocromático).
  - Guardado estándar en carpeta local ./colores_<dominio>/.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from nicegui import ui

from .calculo_color import (
    calcular_armonias,
    es_hex_valido,
    generar_escala_tailwind,
    mejor_color_texto,
    normalizar_hex,
)
from .exportador import (
    exportar_tema_local,
    generar_formato_css,
    generar_formato_js,
    generar_formato_python,
    generar_formato_scss,
    generar_formato_tailwind,
)
from .extractor_tema import TemasSitio, extraer_tema_de_sitio

ID_HERRAMIENTA = "color_tools"
RUTA = "/color-tools"

CARPETA_MAPEADOR = Path(tempfile.gettempdir()) / "mapeador_urls"


# --------------------------------------------------------------------------- #
# Lectura del Historial del Mapeador de URLs
# --------------------------------------------------------------------------- #

def _obtener_sitios_mapeados() -> list[dict]:
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
                        "paginas": datos,
                    })
        except Exception:
            pass

    return sorted(sitios, key=lambda x: x["fecha"], reverse=True)


# --------------------------------------------------------------------------- #
# Selector de Carpeta
# --------------------------------------------------------------------------- #

def _abrir_selector_carpeta(target_input: ui.input):
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        inicial = target_input.value if target_input.value and Path(target_input.value).exists() else str(Path.cwd())
        folder = filedialog.askdirectory(initialdir=inicial, title="Seleccionar Carpeta para Guardar Colores")
        root.destroy()
        if folder:
            target_input.value = folder
            return
    except Exception:
        pass

    val_actual = target_input.value.strip() if target_input.value else ""
    path_actual = Path(val_actual).expanduser() if val_actual and Path(val_actual).exists() else Path.cwd()
    state = {"current": path_actual}

    dialog = ui.dialog()
    with dialog, ui.card().classes("w-96 max-h-96 p-4 gap-2"):
        ui.label("📂 Seleccionar Carpeta").classes("text-base font-bold")
        lbl_ruta = ui.label(str(state["current"])).classes("text-xs text-gray-500 break-all font-mono")
        container = ui.column().classes("w-full h-48 overflow-y-auto border p-2 rounded gap-1 bg-slate-50")

        def render():
            container.clear()
            p = state["current"]
            lbl_ruta.text = str(p)
            with container:
                if p.parent != p:
                    ui.button(".. (Subir nivel)", on_click=lambda: ir_a(p.parent)).props(
                        "dense flat text-color=blue"
                    ).classes("w-full text-left font-bold")
                try:
                    dirs = sorted(
                        [d for d in p.iterdir() if d.is_dir() and not d.name.startswith(".")],
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
            ui.button("Seleccionar esta carpeta", on_click=seleccionar, color="primary").props("dense")

    dialog.open()


# --------------------------------------------------------------------------- #
# Modal del Historial del Mapeador (1 URL o Todas)
# --------------------------------------------------------------------------- #

def _abrir_modal_mapeador(url_input: ui.input, al_cargar: callable | None = None):
    dialog = ui.dialog()
    with dialog, ui.card().classes("w-full max-w-4xl max-h-[90vh] p-4 gap-3"):
        seleccionadas: set[str] = set()

        with ui.row().classes("w-full items-center justify-between border-b pb-2"):
            ui.label("🗺️ Historial de Sitios Mapeados").classes("text-xl font-bold")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")

        sitios = _obtener_sitios_mapeados()

        if not sitios:
            ui.label(
                "Aún no has rastreado ningún sitio en el Mapeador de URLs. "
                "Puedes ingresar una URL manualmente en el campo de texto."
            ).classes("text-gray-400 py-8 text-center w-full")
        else:
            with ui.column().classes("w-full h-[480px] overflow-y-auto gap-3 p-1"):
                for s in sitios:
                    todas_urls = [s["url_inicio"]] + [p.get("url", "") for p in s["paginas"] if p.get("url")]

                    with ui.expansion(
                        f"{s['url_inicio']}  —  [{s['total_paginas']} páginas · {s['fecha']}]",
                        icon="language",
                    ).classes("w-full bg-slate-50 border rounded-lg shadow-sm"):

                        with ui.row().classes("w-full justify-between items-center p-2 bg-blue-50 rounded mb-2"):
                            def marcar_todas(e, urls=todas_urls):
                                if e.value:
                                    seleccionadas.update(urls)
                                else:
                                    seleccionadas.difference_update(urls)

                            ui.checkbox("Seleccionar TODAS las páginas de este sitio", on_change=marcar_todas).classes(
                                "font-bold text-xs text-blue-900"
                            )

                        with ui.row().classes("w-full items-center gap-2 bg-blue-100/60 p-2 rounded border border-blue-200"):
                            def toggle_raiz(e, url=s["url_inicio"]):
                                if e.value:
                                    seleccionadas.add(url)
                                else:
                                    seleccionadas.discard(url)

                            ui.checkbox(on_change=toggle_raiz).props("dense")
                            ui.label("⭐ Principal:").classes("text-xs font-bold text-blue-900")
                            ui.label(s["url_inicio"]).classes(
                                "text-xs font-mono font-bold text-blue-800 truncate flex-grow"
                            )

                            def elegir_solo_esta(url=s["url_inicio"]):
                                url_input.value = url
                                dialog.close()
                                ui.notify(f"URL cargada: {url}", type="positive")
                                if al_cargar:
                                    al_cargar()

                            ui.button("Solo esta", on_click=elegir_solo_esta).props(
                                "flat dense text-color=primary size=sm"
                            ).tooltip("Analizar únicamente esta URL")

                        ui.label("Sub-páginas encontradas:").classes("text-xs font-bold text-gray-600 mt-2 px-1")
                        with ui.column().classes("w-full max-h-52 overflow-y-auto gap-1 border bg-white rounded p-2"):
                            for pag in s["paginas"][:80]:
                                url_hija = pag.get("url", "")
                                if not url_hija or url_hija == s["url_inicio"]:
                                    continue
                                titulo = pag.get("titulo") or "Sin título"

                                with ui.row().classes(
                                    "w-full items-center gap-2 text-xs py-1 px-2 border-b hover:bg-slate-50 rounded"
                                ):
                                    def toggle_sub(e, u=url_hija):
                                        if e.value:
                                            seleccionadas.add(u)
                                        else:
                                            seleccionadas.discard(u)

                                    ui.checkbox(on_change=toggle_sub).props("dense")
                                    ui.label(url_hija).classes("font-mono truncate text-blue-700 max-w-[60%]")
                                    ui.label(f"({titulo})").classes("text-gray-400 truncate text-[11px] flex-grow")

                                    def elegir_esta_sub(u=url_hija):
                                        url_input.value = u
                                        dialog.close()
                                        ui.notify(f"URL cargada: {u}", type="positive")
                                        if al_cargar:
                                            al_cargar()

                                    ui.button("Solo esta", on_click=elegir_esta_sub).props(
                                        "flat dense text-color=primary size=xs"
                                    )

        with ui.row().classes("w-full justify-between items-center border-t pt-3 mt-2"):
            ui.label("Selecciona una o varias URLs para analizar su tema").classes("text-xs text-gray-500")
            with ui.row().classes("gap-2"):
                ui.button("Cancelar", on_click=dialog.close).props("flat dense")

                def confirmar():
                    if not seleccionadas:
                        ui.notify("Selecciona al menos una URL", type="warning")
                        return
                    url_input.value = " , ".join(sorted(seleccionadas))
                    dialog.close()
                    ui.notify(f"Cargadas {len(seleccionadas)} URLs para análisis", type="positive")
                    if al_cargar:
                        al_cargar()

                ui.button("Cargar Selección", icon="check_circle", on_click=confirmar).props("dense color=primary")

    dialog.open()


# --------------------------------------------------------------------------- #
# Página Principal de Color Tools
# --------------------------------------------------------------------------- #

@ui.page(RUTA)
def index():
    estado = {
        "color_activo": "#2563EB",
        "tema_actual": None,
        "escala_actual": generar_escala_tailwind("#2563EB"),
        "formato_seleccionado": "todos",
    }

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):

        # ------------------------------------------------------------------- #
        # Barra Superior
        # ------------------------------------------------------------------- #
        with ui.row().classes("items-center justify-between w-full -mb-2 flex-wrap gap-2"):
            with ui.row().classes("items-center gap-2"):
                ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props("flat round dense")
                ui.label("Inicio").classes("text-sm text-gray-500")

            # Indicador de Color Activo
            with ui.row().classes("items-center gap-2 px-3 py-1 bg-slate-100 rounded-full border"):
                swatch_header = ui.element("div").classes("w-4 h-4 rounded-full border").style(
                    f"background-color: {estado['color_activo']};"
                )
                lbl_header_color = ui.label(f"Color Activo: {estado['color_activo']}").classes(
                    "font-mono text-xs font-bold text-gray-800"
                )

        ui.label("Color Tools").classes("text-3xl font-bold")
        ui.label("Extractor de temas web, generador de escalas multiformato y armonías de color").classes(
            "text-sm text-gray-500 -mt-3"
        )

        # ------------------------------------------------------------------- #
        # Pestañas Principales (3 Pestañas Estandarizadas)
        # ------------------------------------------------------------------- #
        with ui.tabs().classes("w-full") as tabs:
            tab_scraper = ui.tab("Tema Web", icon="travel_explore")
            tab_escalas = ui.tab("Escalas de Color", icon="tune")
            tab_armonias = ui.tab("Armonías de Color", icon="palette")

        with ui.tab_panels(tabs, value=tab_scraper).classes("w-full bg-transparent p-0"):

            # =============================================================== #
            # PESTAÑA 1: TEMA WEB
            # =============================================================== #
            with ui.tab_panel(tab_scraper).classes("p-0 gap-4 column"):
                with ui.card().classes("w-full gap-3"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label("🌐 Extraer Tema y Colores desde Web Scraping").classes("text-lg font-bold")
                        ui.button(
                            "Cargar del Mapeador de URLs",
                            icon="history",
                            on_click=lambda: _abrir_modal_mapeador(url_input),
                        ).props("outlined dense color=secondary").tooltip(
                            "Abrir historial de sitios ya rastreados en la suite"
                        )

                    with ui.row().classes("w-full items-end gap-3"):
                        url_input = (
                            ui.input(
                                "URLs a analizar (separadas por coma)",
                                placeholder="https://ejemplo.com o selecciona del historial",
                                value="https://digixonic.com",
                            )
                            .classes("flex-grow")
                            .props("outlined dense")
                        )

                        boton_analizar = ui.button("Analizar Tema", icon="search").props("dense color=primary")

                    with ui.expansion("Opciones de guardado local", icon="folder").classes("w-full"):
                        with ui.row().classes("w-full items-center gap-3"):
                            carpeta_input = (
                                ui.input(
                                    "Carpeta de guardado",
                                    placeholder="Por defecto: ./colores_<dominio> (en carpeta del proyecto)",
                                )
                                .classes("flex-grow")
                                .props("outlined dense")
                            )
                            ui.button(
                                icon="folder_open",
                                on_click=lambda: _abrir_selector_carpeta(carpeta_input),
                            ).props("outlined dense").classes("w-12").tooltip("Explorar carpeta...")

                contenedor_resultados = ui.column().classes("w-full gap-4")

            # =============================================================== #
            # PESTAÑA 2: ESCALAS DE COLOR (MULTIFORMATO)
            # =============================================================== #
            with ui.tab_panel(tab_escalas).classes("p-0 gap-4 column"):
                with ui.card().classes("w-full gap-3"):
                    with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                        with ui.column().classes("gap-0"):
                            ui.label("📐 Escala de Color Tonal (11 Matices)").classes("text-lg font-bold")
                            ui.label("Selecciona el formato o lenguaje deseado para ver y copiar el código.").classes(
                                "text-xs text-gray-500"
                            )

                        color_input_escalas = ui.color_input(
                            "Color Base",
                            value=estado["color_activo"],
                            on_change=lambda e: fijar_color_activo(e.value),
                        ).classes("w-56")

                    # Selector de Formatos (Todos seleccionado por defecto)
                    with ui.row().classes("w-full items-center gap-4 bg-slate-50 p-2 rounded border mt-2 flex-wrap"):
                        ui.label("Formato de Salida:").classes("text-xs font-bold text-gray-700")
                        selector_formato = ui.radio(
                            {
                                "todos": "Todos los Formatos (General)",
                                "css": "CSS (:root)",
                                "tailwind": "Tailwind CSS",
                                "js": "JavaScript / TS",
                                "python": "Python (dict)",
                                "json": "JSON",
                                "scss": "SCSS",
                            },
                            value=estado["formato_seleccionado"],
                            on_change=lambda e: cambiar_formato(e.value),
                        ).props("dense inline").classes("text-xs")

                    # Fila de muestras de color (Swatches 50 a 950)
                    contenedor_escala = ui.row().classes("w-full gap-2 flex-wrap mt-2")

                    # Bloque de Código Generado
                    contenedor_codigo_escala = ui.column().classes("w-full gap-2 mt-3")

            # =============================================================== #
            # PESTAÑA 3: ARMONÍAS DE COLOR
            # =============================================================== #
            with ui.tab_panel(tab_armonias).classes("p-0 gap-4 column"):
                with ui.card().classes("w-full gap-3"):
                    with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                        with ui.column().classes("gap-0"):
                            ui.label("🎨 Armonías de Color").classes("text-lg font-bold")
                            ui.label("Paletas calculadas matemáticamente a partir del color principal.").classes(
                                "text-xs text-gray-500"
                            )

                        color_input_armonias = ui.color_input(
                            "Color Principal",
                            value=estado["color_activo"],
                            on_change=lambda e: fijar_color_activo(e.value),
                        ).classes("w-56")

                    contenedor_armonias = ui.column().classes("w-full gap-4 mt-2")

    # ----------------------------------------------------------------------- #
    # Lógica Reactiva
    # ----------------------------------------------------------------------- #

    def fijar_color_activo(hex_color: str, notificar: bool = True):
        if not es_hex_valido(hex_color):
            return
        hex_norm = normalizar_hex(hex_color)
        estado["color_activo"] = hex_norm

        swatch_header.style(f"background-color: {hex_norm};")
        lbl_header_color.text = f"Color Activo: {hex_norm}"

        if color_input_escalas.value != hex_norm:
            color_input_escalas.value = hex_norm
        if color_input_armonias.value != hex_norm:
            color_input_armonias.value = hex_norm

        estado["escala_actual"] = generar_escala_tailwind(hex_norm)
        renderizar_escala()
        renderizar_armonias()

        if notificar:
            ui.notify(f"Color activo: {hex_norm}", type="positive", icon="palette")

    def cambiar_formato(formato: str):
        estado["formato_seleccionado"] = formato
        renderizar_codigo_escala()

    # ---- Renderizado de la Escala ---- #
    def renderizar_escala():
        contenedor_escala.clear()
        with contenedor_escala:
            for tono, hex_c in estado["escala_actual"].items():
                txt_col = mejor_color_texto(hex_c)
                with ui.card().classes(
                    "w-20 p-2 items-center text-center cursor-pointer hover:scale-105 transition-transform"
                ).style(f"background-color: {hex_c}; color: {txt_col};").tooltip(
                    f"Clic para activar tono {tono} ({hex_c})"
                ).on("click", lambda _e, c=hex_c: fijar_color_activo(c)):
                    ui.label(str(tono)).classes("text-xs font-bold")
                    ui.label(hex_c).classes("text-[10px] font-mono mt-2")

        renderizar_codigo_escala()

    def renderizar_codigo_escala():
        contenedor_codigo_escala.clear()
        fmt = estado["formato_seleccionado"]
        escala = estado["escala_actual"]
        prim = estado["color_activo"]

        # Crear objeto TemasSitio sintético si no hay análisis web cargado
        tema_actual = estado["tema_actual"] or TemasSitio(
            dominio="mi-proyecto",
            urls_analizadas=[],
            primary=prim,
            secondary="#4F46E5",
            background="#FFFFFF",
            surface="#F8FAFC",
            text_primary="#0F172A",
            text_muted="#64748B",
        )

        with contenedor_codigo_escala:
            if fmt in ("todos", "css"):
                codigo_css = generar_formato_css(tema_actual, escala)
                _crear_bloque_codigo("CSS (:root)", codigo_css, "css")

            if fmt in ("todos", "tailwind"):
                codigo_tw = generar_formato_tailwind(tema_actual, escala)
                _crear_bloque_codigo("Tailwind CSS (tailwind.config.js)", codigo_tw, "javascript")

            if fmt in ("todos", "js"):
                codigo_js = generar_formato_js(tema_actual, escala)
                _crear_bloque_codigo("JavaScript / TypeScript (ES6)", codigo_js, "javascript")

            if fmt in ("todos", "python"):
                codigo_py = generar_formato_python(tema_actual, escala)
                _crear_bloque_codigo("Python (dict)", codigo_py, "python")

            if fmt == "json":
                codigo_json = json.dumps({"primary": prim, "escala": escala}, indent=2)
                _crear_bloque_codigo("JSON", codigo_json, "json")

            if fmt == "scss":
                codigo_scss = generar_formato_scss(tema_actual, escala)
                _crear_bloque_codigo("SCSS", codigo_scss, "scss")

    def _crear_bloque_codigo(titulo: str, contenido: str, lenguaje: str):
        with ui.card().classes("w-full p-3 border bg-slate-50 gap-2"):
            with ui.row().classes("w-full justify-between items-center"):
                ui.label(titulo).classes("text-xs font-bold text-gray-700")
                ui.button(
                    "Copiar",
                    icon="content_copy",
                    on_click=lambda c=contenido, t=titulo: ui.clipboard.write(c) or ui.notify(
                        f"Copiado {t} al portapapeles", type="positive"
                    ),
                ).props("flat dense size=sm color=primary")

            ui.code(contenido, language=lenguaje).classes("w-full text-xs font-mono max-h-48 overflow-y-auto")

    # ---- Renderizado de Armonías ---- #
    def renderizar_armonias():
        contenedor_armonias.clear()
        with contenedor_armonias:
            armonias = calcular_armonias(estado["color_activo"])
            for nombre, paleta in armonias.items():
                with ui.column().classes("w-full gap-1"):
                    ui.label(nombre.replace("_", " ").title()).classes("text-sm font-bold text-gray-700")
                    with ui.row().classes("gap-3 flex-wrap"):
                        for c in paleta:
                            txt_col = mejor_color_texto(c)
                            with ui.card().classes(
                                "w-28 h-16 p-2 items-center justify-center cursor-pointer hover:shadow-md transition-shadow"
                            ).style(f"background-color: {c}; color: {txt_col};").tooltip(
                                f"Clic para activar {c} en toda la suite"
                            ).on("click", lambda _e, col=c: fijar_color_activo(col)):
                                ui.label(c).classes("font-mono text-xs font-bold")

    # ---- Análisis de Tema Web ---- #
    async def analizar_tema():
        raw_val = (url_input.value or "").strip()
        if not raw_val:
            ui.notify("Ingresa al menos una URL para analizar", type="warning")
            return

        urls = [u.strip() for u in raw_val.replace(",", " ").split() if u.strip()]
        urls_limpias = [f"https://{u}" if not urlparse(u).scheme else u for u in urls]

        boton_analizar.disable()
        boton_analizar.props("loading=true")
        contenedor_resultados.clear()

        with contenedor_resultados:
            ui.spinner("dots", size="lg").classes("mx-auto my-3")
            lbl_progreso = ui.label("Iniciando conexión...").classes(
                "text-xs text-gray-500 font-mono text-center mx-auto"
            )

        loop = asyncio.get_running_loop()

        def al_progresar(mensaje: str):
            loop.call_soon_threadsafe(lambda m=mensaje: setattr(lbl_progreso, "text", m))

        try:
            tema: TemasSitio = await asyncio.to_thread(
                extraer_tema_de_sitio,
                urls_limpias,
                callback_progreso=al_progresar,
            )
            estado["tema_actual"] = tema
            fijar_color_activo(tema.primary, notificar=False)
            renderizar_resultados_tema(tema)
            ui.notify(f"Tema de {tema.dominio} analizado con éxito", type="positive", icon="check_circle")
        except Exception as err:
            contenedor_resultados.clear()
            with contenedor_resultados:
                ui.label(f"No se pudo completar el análisis: {err}").classes("text-sm text-red-600 p-2")
            ui.notify(f"Error al analizar: {err}", type="negative")
        finally:
            boton_analizar.enable()
            boton_analizar.props("loading=false")

    def renderizar_resultados_tema(tema: TemasSitio):
        contenedor_resultados.clear()
        with contenedor_resultados:
            with ui.card().classes("w-full gap-4"):
                with ui.row().classes("w-full justify-between items-center flex-wrap gap-2"):
                    with ui.column().classes("gap-0"):
                        ui.label(f"Tema: {tema.dominio}").classes("text-xl font-bold text-primary")
                        ui.label(f"{len(tema.urls_analizadas)} página(s) analizada(s)").classes("text-xs text-gray-500")

                    def guardar_local():
                        carpeta = carpeta_input.value.strip() if carpeta_input.value else None
                        res = exportar_tema_local(tema, carpeta)
                        ui.notify(
                            f"Archivos guardados en: {res['carpeta']} ({res['total_archivos']} formatos)",
                            type="positive",
                            icon="save",
                        )

                    ui.button("Guardar en Carpeta Local", icon="save", on_click=guardar_local).props(
                        "elevated color=primary dense"
                    ).tooltip("Guarda en JSON, CSS, JS, Python, Tailwind y SCSS")

                # Colores del Tema Principal
                ui.label("Colores del Tema Principal (Haz clic para usarlo en Escalas y Armonías):").classes(
                    "text-sm font-bold mt-2"
                )

                with ui.row().classes("w-full gap-3 flex-wrap"):
                    items_tema = [
                        ("Primario", tema.primary),
                        ("Secundario", tema.secondary),
                        ("Fondo", tema.background),
                        ("Superficie", tema.surface),
                        ("Texto Principal", tema.text_primary),
                        ("Texto Secundario", tema.text_muted),
                    ]
                    for etiqueta, hex_col in items_tema:
                        txt_col = mejor_color_texto(hex_col)
                        with ui.card().classes(
                            "flex-1 min-w-[150px] p-3 border cursor-pointer hover:shadow-md transition-shadow"
                        ).style(f"background-color: {hex_col}; color: {txt_col};").tooltip(
                            f"Clic para activar {hex_col}"
                        ).on("click", lambda _e, c=hex_col: fijar_color_activo(c)):
                            ui.label(etiqueta).classes("text-[11px] font-medium opacity-90")
                            ui.label(hex_col).classes("text-sm font-bold font-mono mt-1")

                # Botones de salto directo
                with ui.row().classes("gap-3 mt-2"):
                    ui.button(
                        "Ver Escalas de este Tema ->",
                        icon="tune",
                        on_click=lambda: tabs.set_value(tab_escalas),
                    ).props("flat dense color=primary")
                    ui.button(
                        "Ver Armonías ->",
                        icon="palette",
                        on_click=lambda: tabs.set_value(tab_armonias),
                    ).props("flat dense color=teal")

                # Colores detectados en la web
                if tema.paleta_completa:
                    ui.label("Colores Detectados en la Web:").classes("text-sm font-bold mt-3")
                    with ui.row().classes("gap-2 flex-wrap"):
                        for item in tema.paleta_completa:
                            c = item["hex"]
                            txt_col = mejor_color_texto(c)
                            with ui.card().classes(
                                "p-2 text-center cursor-pointer hover:scale-110 transition-transform"
                            ).style(f"background-color: {c}; color: {txt_col};").tooltip(
                                f"Clic para activar {c}"
                            ).on("click", lambda _e, col=c: fijar_color_activo(col)):
                                ui.label(c).classes("font-mono text-[11px] font-bold")
                                ui.label(f"{item['conteo']}x").classes("text-[9px] opacity-75")

    boton_analizar.on_click(analizar_tema)

    # Inicializar vistas
    renderizar_escala()
    renderizar_armonias()
