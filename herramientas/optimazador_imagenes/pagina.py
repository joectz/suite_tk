"""
pagina.py — Interfaz de usuario para el Optimizador de Imágenes .

Características:
  - Lienzo central con slider de comparación interactivo Antes / Después (60 FPS).
  - Previsualización instantánea en memoria con cálculo de ahorro en vivo.
  - Panel lateral de compresión: formatos WebP, AVIF, JPEG y PNG, slider de calidad,
    modo sin pérdida (lossless), redimensionamiento con escala % o píxeles exactos
    y bloqueo de proporción, submuestreo cromático y limpieza de metadatos.
  - Tira de miniaturas para alternar entre imágenes cargadas.
  - Soporte para optimización masiva por lotes con descarga en ZIP, JSON y CSV.
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw
from nicegui import app, ui

from .motor import (
    FORMATOS_SOPORTADOS,
    OpcionesOptimizacion,
    ResultadoFormato,
    ResultadoOptimizacion,
    buscar_imagenes,
    es_avif_disponible,
    formatear_bytes,
    optimizar_en_memoria,
    optimizar_imagen,
)

# --------------------------------------------------------------------------- #
# Constantes y Rutas
# --------------------------------------------------------------------------- #

ID_HERRAMIENTA = "optimizador_imagenes"
RUTA = "/optimizador-imagenes"
RUTA_ALIAS = "/optimizador-imagenes"

CARPETA_TEMP = Path(tempfile.gettempdir()) / "optimizador_imagenes"
CARPETA_SUBIDAS = CARPETA_TEMP / "subidas"
CARPETA_SALIDAS = CARPETA_TEMP / "salidas"

CARPETA_SUBIDAS.mkdir(parents=True, exist_ok=True)
CARPETA_SALIDAS.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Estructuras de Estado
# --------------------------------------------------------------------------- #

class ItemImagen:
    """Representa una imagen cargada para inspección o procesamiento."""
    def __init__(self, ruta: str, nombre: str, peso: int, ancho: int, alto: int, bytes_data: bytes | None = None):
        self.ruta = ruta
        self.nombre = nombre
        self.peso = peso
        self.ancho = ancho
        self.alto = alto
        self.bytes_data = bytes_data


class EstadoPagina:
    """Estado reactivo de la sesión."""
    def __init__(self):
        self.imagenes: list[ItemImagen] = []
        self.indice_activo: int = 0
        self.resultados_lote: list[ResultadoOptimizacion] = []
        self.corriendo_lote: bool = False
        self.cancelar_lote: bool = False
        self.inicio_lote: datetime | None = None

        # Caché de preview actual
        self.preview_bytes: bytes | None = None
        self.preview_data_url: str = ""
        self.preview_info: dict = {}
        self.orig_data_url: str = ""


# --------------------------------------------------------------------------- #
# Helper para extraer archivos de eventos de subida de NiceGUI
# --------------------------------------------------------------------------- #

async def extraer_archivo_subido(e) -> tuple[str, bytes]:
    """
    Extrae de forma robusta el nombre y los bytes de cualquier evento de subida
    de NiceGUI (compatible con NiceGUI 3.x FileUpload y versiones previas).
    """
    nombre = "imagen.png"
    contenido = b""

    try:
        # NiceGUI 3.x: e.file es una instancia de FileUpload
        if hasattr(e, "file") and e.file:
            nombre = getattr(e.file, "name", "imagen.png")
            if hasattr(e.file, "read"):
                res = e.file.read()
                contenido = await res if asyncio.iscoroutine(res) else res
        # Compatibilidad con NiceGUI 1.x / 2.x
        elif hasattr(e, "name"):
            nombre = e.name
            c = getattr(e, "content", b"")
            if hasattr(c, "read"):
                res = c.read()
                contenido = await res if asyncio.iscoroutine(res) else res
            elif isinstance(c, bytes):
                contenido = c
    except Exception as ex:
        print(f"Error extrayendo archivo subido: {ex}")

    return nombre, contenido


def crear_imagen_demo() -> tuple[bytes, str]:
    """Genera una imagen artística en memoria para probar Squoosh al instante."""
    ancho, alto = 1280, 720
    im = Image.new("RGBA", (ancho, alto), color=(15, 23, 42, 255))
    draw = ImageDraw.Draw(im)

    colores = [
        (239, 68, 68),   # Rojo
        (249, 115, 22),  # Naranja
        (234, 179, 8),   # Amarillo
        (16, 185, 129),  # Verde esmeralda
        (59, 130, 246),  # Azul
        (168, 85, 247),  # Púrpura
    ]

    for i, col in enumerate(colores):
        offset = i * 160
        draw.ellipse([offset + 40, 80, offset + 340, 380], fill=(*col, 200), outline=(255, 255, 255, 230), width=4)
        draw.rounded_rectangle([offset + 80, 280, offset + 380, 580], radius=30, fill=(*col, 160), outline=(255, 255, 255, 200), width=3)

    buf = io.BytesIO()
    im.save(buf, format="PNG")
    data = buf.getvalue()
    ruta_demo = str(CARPETA_SUBIDAS / "squoosh_demo.png")
    with open(ruta_demo, "wb") as f:
        f.write(data)
    return data, ruta_demo


# --------------------------------------------------------------------------- #
# Página Principal NiceGUI
# --------------------------------------------------------------------------- #

@ui.page(RUTA)
@ui.page(RUTA_ALIAS)
def index():
    avif_disponible = es_avif_disponible()
    E = EstadoPagina()

    # CSS dedicado para la experiencia visual de Squoosh
    ui.add_head_html("""
    <style>
        .squoosh-bg {
            background-color: #0b0f19;
            color: #f1f5f9;
            min-height: 100vh;
        }
        .squoosh-card {
            background: #131b2e;
            border: 1px solid #1e293b;
            border-radius: 12px;
        }
        .squoosh-panel {
            background: #131b2e;
            border: 1px solid #1e293b;
            border-radius: 12px;
        }
        .squoosh-canvas-box {
            position: relative;
            width: 100%;
            height: 520px;
            background: #090d16;
            border-radius: 12px;
            overflow: hidden;
            border: 1px solid #1e293b;
            box-shadow: 0 10px 30px -10px rgba(0,0,0,0.8);
            user-select: none;
        }
        .squoosh-pattern {
            position: absolute;
            inset: 0;
            background-image:
                linear-gradient(45deg, #182238 25%, transparent 25%),
                linear-gradient(-45deg, #182238 25%, transparent 25%),
                linear-gradient(45deg, transparent 75%, #182238 75%),
                linear-gradient(-45deg, transparent 75%, #182238 75%);
            background-size: 20px 20px;
            background-position: 0 0, 0 10px, 10px -10px, -10px 0px;
            background-color: #0e1626;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .squoosh-layer {
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            pointer-events: none;
        }
        .squoosh-layer .q-img {
            width: 100%;
            height: 100%;
            pointer-events: none;
        }
        .squoosh-layer-opt {
            clip-path: polygon(var(--squoosh-pos, 50%) 0, 100% 0, 100% 100%, var(--squoosh-pos, 50%) 100%);
            z-index: 10;
        }
        .squoosh-divider-line {
            position: absolute;
            top: 0;
            bottom: 0;
            left: var(--squoosh-pos, 50%);
            width: 3px;
            background: #38bdf8;
            transform: translateX(-50%);
            box-shadow: 0 0 12px rgba(56, 189, 248, 0.95);
            pointer-events: none;
            z-index: 20;
        }
        .squoosh-handle-btn {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: 42px;
            height: 42px;
            border-radius: 50%;
            background: #ffffff;
            color: #0f172a;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 4px 16px rgba(0,0,0,0.6), 0 0 0 3px #38bdf8;
            font-size: 16px;
            font-weight: 900;
            cursor: ew-resize;
            transition: transform 0.15s ease;
        }
        .squoosh-range-stealth {
            position: absolute;
            inset: 0;
            width: 100%;
            height: 100%;
            opacity: 0;
            cursor: ew-resize;
            margin: 0;
            z-index: 30;
        }
        .squoosh-tag {
            position: absolute;
            padding: 6px 14px;
            border-radius: 8px;
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.12);
            color: #ffffff;
            font-size: 12px;
            z-index: 25;
            pointer-events: none;
        }
        .squoosh-pill-saving {
            position: absolute;
            bottom: 16px;
            right: 16px;
            background: #10b981;
            color: #ffffff;
            font-weight: 800;
            font-size: 13px;
            padding: 6px 14px;
            border-radius: 24px;
            box-shadow: 0 4px 14px rgba(16, 185, 129, 0.4);
            z-index: 25;
            pointer-events: none;
        }
        .squoosh-thumb-card {
            cursor: pointer;
            transition: all 0.2s ease;
            border: 2px solid transparent;
        }
        .squoosh-thumb-card.active {
            border-color: #38bdf8;
            box-shadow: 0 0 10px rgba(56, 189, 248, 0.4);
        }

        /* ======== NUEVO: Forzar color blanco en todos los textos de paneles y tarjetas ======== */
        .squoosh-panel *,
        .squoosh-card * {
            color: #ffffff !important;
        }

        /* Excepciones: botones y elementos que ya tienen colores específicos */
        .squoosh-panel .q-btn,
        .squoosh-card .q-btn {
            color: inherit !important; /* respeta el color del botón */
        }
        .squoosh-panel .q-radio,
        .squoosh-card .q-radio {
            color: #ffffff !important;
        }
        .squoosh-panel .q-checkbox,
        .squoosh-card .q-checkbox {
            color: #ffffff !important;
        }
        .squoosh-panel .q-tab,
        .squoosh-card .q-tab {
            color: #ffffff !important;
        }
        .squoosh-panel .q-field__control,
        .squoosh-card .q-field__control {
            color: #ffffff !important;
        }
        .squoosh-panel .q-field__label,
        .squoosh-card .q-field__label {
            color: #ffffff !important;
        }
        .squoosh-panel .q-select .q-field__native,
        .squoosh-card .q-select .q-field__native {
            color: #ffffff !important;
        }
        .squoosh-panel .q-option,
        .squoosh-card .q-option {
            color: #ffffff !important;
        }
        /* También forzamos el color de los números en los inputs */
        .squoosh-panel .q-input,
        .squoosh-card .q-input {
            color: #ffffff !important;
        }
        .squoosh-panel .q-input .q-field__native,
        .squoosh-card .q-input .q-field__native {
            color: #ffffff !important;
        }
        /* Para los selectores de opciones (menú desplegable) */
        .q-item__label {
            color: #ffffff !important;
        }
        /* Ajuste para los títulos de los expansion items */
        .q-expansion-item .q-item__label {
            color: #ffffff !important;
        }
        /* Para los sliders, aunque su texto es el valor, no es necesario cambiar */
    </style>
    """)

    # ----------------------------------------------------------------------- #
    # Componentes de UI Principales
    # ----------------------------------------------------------------------- #

    with ui.column().classes("w-full max-w-7xl mx-auto p-4 gap-4"):

        # =================================================================== #
        # HEADER SUPERIOR
        # =================================================================== #
        with ui.row().classes("items-center justify-between w-full pb-3 border-b border-slate-800"):
            with ui.row().classes("items-center gap-3"):
                ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")) \
                    .props("flat round dense color=primary").tooltip("Volver al inicio")
                with ui.row().classes("items-center gap-2"):
                    ui.icon("tune", size="1.8rem").classes("text-sky-400")
                    ui.label("Squoosh Image Studio").classes("text-2xl font-black tracking-tight text-white")
                    ui.badge("V2 PRO", color="indigo").classes("text-xs font-bold")

            with ui.row().classes("items-center gap-2"):
                if avif_disponible:
                    ui.badge("AVIF Soportado", color="emerald").classes("text-xs font-semibold")
                else:
                    ui.badge("AVIF Inactivo", color="gray").classes("text-xs")

                ui.button("Cargar Ejemplo", icon="auto_fix_high", on_click=lambda: cargar_demo()) \
                    .props("outline dense color=sky").classes("text-xs")

        # =================================================================== #
        # CONTENEDOR SIN IMÁGENES (Dropzone Inicial)
        # =================================================================== #
        panel_dropzone = ui.card().classes("w-full squoosh-card p-10 flex flex-col items-center justify-center gap-4 text-center border-dashed border-2 border-slate-700")

        # =================================================================== #
        # CONTENEDOR CON IMÁGENES (Workspace Squoosh)
        # =================================================================== #
        panel_workspace = ui.column().classes("w-full gap-4")

        # ------------------------------------------------------------------- #
        # 1. TIRA DE MINIATURAS (Thumbnail Bar)
        # ------------------------------------------------------------------- #
        with panel_workspace:
            with ui.row().classes("w-full items-center justify-between bg-slate-900/60 p-2 rounded-xl border border-slate-800"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("collections", size="1.2rem").classes("text-slate-400")
                    lbl_total_imagenes = ui.label("0 imágenes").classes("text-sm font-semibold text-slate-300")

                with ui.row().classes("items-center gap-2"):
                    ui.button("+ Agregar más", icon="add_photo_alternate", on_click=lambda: dialog_agregar.open()) \
                        .props("flat dense color=sky").classes("text-xs")
                    ui.button("Limpiar todo", icon="delete_sweep", on_click=lambda: limpiar_todo()) \
                        .props("flat dense color=red-4").classes("text-xs")

            tira_miniaturas = ui.row().classes("w-full gap-2 overflow-x-auto pb-2 items-center")

            # --------------------------------------------------------------- #
            # 2. ESPACIO PRINCIPAL: CANVAS CENTRAL + PANEL LATERAL SQUOOSH
            # --------------------------------------------------------------- #
            with ui.row().classes("w-full gap-4 items-start"):

                # =========================================================== #
                # LIENZO CENTRAL (Split Slider Squoosh)
                # =========================================================== #
                with ui.column().classes("flex-grow min-w-[320px] gap-2"):
                    # Barra de control de visualización
                    with ui.row().classes("w-full items-center justify-between px-2"):
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("compare", size="1rem").classes("text-sky-400")
                            ui.label("Comparación en Vivo").classes("text-sm font-bold text-slate-200")
                        
                        with ui.row().classes("items-center gap-1"):
                            btn_vista_split = ui.button("Deslizador", icon="splitscreen", on_click=lambda: cambiar_modo_vista("split")) \
                                .props("dense flat size=sm color=sky")
                            btn_vista_lado = ui.button("Lado a Lado", icon="view_column", on_click=lambda: cambiar_modo_vista("side")) \
                                .props("dense flat size=sm color=slate-400")

                    # Contenedor del Visor Squoosh
                    contenedor_canvas = ui.element("div").classes("squoosh-canvas-box")
                    with contenedor_canvas:
                        contenedor_canvas.style("--squoosh-pos: 50%;")

                        # Patrón de cuadrícula de transparencia
                        with ui.element("div").classes("squoosh-pattern"):
                            # Capa inferior: Imagen Original
                            with ui.element("div").classes("squoosh-layer squoosh-layer-orig"):
                                img_orig_tag = ui.image("").props("fit=contain no-spinner no-transition").classes("w-full h-full")

                            # Capa superior: Imagen Comprimida (recortada por el slider)
                            with ui.element("div").classes("squoosh-layer squoosh-layer-opt"):
                                img_opt_tag = ui.image("").props("fit=contain no-spinner no-transition").classes("w-full h-full")

                            # Línea divisoria y tirador Squoosh
                            with ui.element("div").classes("squoosh-divider-line"):
                                with ui.element("div").classes("squoosh-handle-btn"):
                                    ui.label("↔")

                            # Range input nativo en HTML para movimiento inmediato a 60 FPS
                            ui.html("""
                            <input type="range" min="0" max="100" value="50" class="squoosh-range-stealth"
                                   oninput="this.closest('.squoosh-canvas-box').style.setProperty('--squoosh-pos', this.value + '%')" />
                            """)

                            # Etiquetas flotantes sobre la imagen
                            with ui.element("div").classes("squoosh-tag").style("top: 16px; left: 16px;"):
                                ui.label("ORIGINAL").classes("text-[10px] font-black text-sky-400 tracking-wider")
                                lbl_orig_info = ui.label("---").classes("font-semibold")

                            with ui.element("div").classes("squoosh-tag").style("top: 16px; right: 16px; text-align: right;"):
                                lbl_opt_formato_tag = ui.label("WEBP").classes("text-[10px] font-black text-indigo-400 tracking-wider")
                                lbl_opt_info = ui.label("---").classes("font-semibold")

                            # Pastilla de Ahorro
                            lbl_ahorro_pill = ui.element("div").classes("squoosh-pill-saving")
                            with lbl_ahorro_pill:
                                lbl_ahorro_texto = ui.label("0% Ahorro").classes("text-white")

                    # Vista Lado a Lado (Alternativa)
                    contenedor_lado_a_lado = ui.row().classes("w-full gap-3").set_visibility(False)
                    with contenedor_lado_a_lado:
                        with ui.card().classes("flex-1 squoosh-card p-3 gap-1"):
                            ui.label("Original").classes("text-xs font-bold text-slate-400")
                            img_side_orig = ui.image("").props("fit=contain no-spinner").classes("w-full h-80 rounded bg-slate-950")
                        with ui.card().classes("flex-1 squoosh-card p-3 gap-1"):
                            ui.label("Comprimida").classes("text-xs font-bold text-indigo-400")
                            img_side_opt = ui.image("").props("fit=contain no-spinner").classes("w-full h-80 rounded bg-slate-950")

                # =========================================================== #
                # PANEL LATERAL DERECHO: CONTROLES SQUOOSH
                # =========================================================== #
                # AÑADIDO .style("color: white;") para forzar herencia
                with ui.card().classes("w-80 squoosh-panel p-4 gap-4 flex-shrink-0").style("color: white;"):
                    with ui.row().classes("items-center justify-between w-full border-b border-slate-800 pb-2"):
                        ui.label("Ajustes de Compresión").classes("text-base font-bold text-white")
                        spinner_preview = ui.spinner(size="sm", color="sky").set_visibility(False)

                    # 1. Selector de Formato
                    with ui.column().classes("w-full gap-1"):
                        ui.label("Formato de salida").classes("text-xs font-semibold text-white")
                        
                        opciones_fmt = {
                            "webp": "WebP",
                            "avif": "AVIF" if avif_disponible else "AVIF (No disp.)",
                            "jpeg": "MozJPEG",
                            "png": "OxiPNG",
                        }
                        radio_formato = ui.radio(opciones_fmt, value="webp") \
                            .props("dense color=sky inline").classes("w-full text-xs text-white")

                    # 2. Calidad de Compresión
                    with ui.column().classes("w-full gap-1"):
                        with ui.row().classes("items-center justify-between w-full"):
                            ui.label("Calidad").classes("text-xs font-semibold text-white")
                            lbl_calidad_num = ui.label("80%").classes("text-xs font-bold text-sky-400")

                        slider_calidad = ui.slider(min=1, max=100, value=80) \
                            .props("dense color=sky")
                        slider_calidad.on_value_change(lambda e: lbl_calidad_num.set_text(f"{int(e.value)}%"))

                    # Toggles de Compresión
                    with ui.column().classes("w-full gap-0"):
                        chk_lossless = ui.checkbox("Sin pérdida (Lossless)") \
                            .props("dense size=sm color=sky").classes("text-white")
                        chk_strip_meta = ui.checkbox("Eliminar metadatos EXIF", value=True) \
                            .props("dense size=sm color=sky").classes("text-white")
                        chk_submuestreo = ui.checkbox("Máxima fidelidad de color (4:4:4)", value=False) \
                            .props("dense size=sm color=sky").classes("text-white") \
                            .tooltip("Desactiva submuestreo para nitidez máxima en texto y gráficos")

                    # 3. Redimensionamiento
                    with ui.expansion("Redimensionar", icon="aspect_ratio").classes("w-full border border-slate-800 rounded-lg"):
                        with ui.column().classes("w-full p-2 gap-2"):
                            chk_activar_resize = ui.checkbox("Activar redimensionado") \
                                .props("dense size=sm color=sky").classes("text-white")

                            # Selector de modo: Porcentaje vs Píxeles exactos
                            tabs_resize = ui.tabs().classes("w-full dense text-xs text-white")
                            with tabs_resize:
                                tab_escala = ui.tab("Porcentaje (%)").classes("text-white")
                                tab_pixeles = ui.tab("Dimensiones (px)").classes("text-white")

                            with ui.tab_panels(tabs_resize, value=tab_escala).classes("w-full bg-transparent p-0"):
                                with ui.tab_panel(tab_escala).classes("p-0 gap-2 flex flex-col"):
                                    # Botones rápidos de escala %
                                    with ui.row().classes("w-full gap-1 items-center mt-1"):
                                        for pct in [25, 50, 75, 100]:
                                            ui.button(f"{pct}%", on_click=lambda p=pct: aplicar_preset_escala(p)) \
                                                .props("dense outline size=xs color=sky").classes("flex-1")

                                    inp_scale_pct = ui.number("Escala %", value=100, min=5, max=200, step=5) \
                                        .props("dense outlined suffix=%").classes("w-full text-xs mt-1 text-white")

                                with ui.tab_panel(tab_pixeles).classes("p-0 gap-2 flex flex-col"):
                                    chk_candado_aspecto = ui.checkbox("Mantener proporción 🔒", value=True) \
                                        .props("dense size=sm color=sky").classes("text-white")

                                    with ui.row().classes("w-full gap-2 items-center mt-1"):
                                        inp_px_ancho = ui.number("Ancho px", value=0, min=1) \
                                            .props("dense outlined suffix=px").classes("flex-1 text-xs text-white")
                                        inp_px_alto = ui.number("Alto px", value=0, min=1) \
                                            .props("dense outlined suffix=px").classes("flex-1 text-xs text-white")

                            # Filtro de remuestreo
                            sel_filtro = ui.select(
                                {"lanczos": "Lanczos (Más nítido)", "bicubic": "Bicubic", "bilinear": "Bilinear"},
                                value="lanczos"
                            ).props("dense outlined color=white").classes("w-full text-xs mt-1 text-white")

                    # 4. Cuantización de Paleta
                    with ui.expansion("Reducir Paleta (Colores)", icon="palette").classes("w-full border border-slate-800 rounded-lg"):
                        with ui.column().classes("w-full p-2 gap-2"):
                            ui.label("Ideal para iconos y PNGs ligeros").classes("text-[11px] text-white")
                            sel_cuantizar = ui.select(
                                {0: "Original (24/32-bit)", 256: "256 colores (8-bit)", 128: "128 colores", 64: "64 colores", 16: "16 colores"},
                                value=0
                            ).props("dense outlined color=white").classes("w-full text-xs text-white")

                    # 5. BOTÓN DE DESCARGA DIRECTA (SQUOOSH STYLE)
                    with ui.column().classes("w-full gap-2 pt-2 border-t border-slate-800"):
                        btn_descargar_activo = ui.button("Descargar Imagen", icon="download", on_click=lambda: descargar_imagen_activa()) \
                            .props("push color=sky size=md").classes("w-full font-bold shadow-lg shadow-sky-500/20")
                        lbl_descarga_peso = ui.label("---").classes("text-center text-xs text-white w-full")
                        
            # --------------------------------------------------------------- #
            # 3. SECCIÓN DE PROCESAMIENTO POR LOTES (Batch Mode)
            # --------------------------------------------------------------- #
            with ui.card().classes("w-full squoosh-card p-4 gap-4 mt-4"):
                with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("auto_awesome_motion", size="1.4rem").classes("text-sky-400")
                        ui.label("Optimización por Lotes").classes("text-base font-bold text-white")
                        ui.label("(Aplica los ajustes de Squoosh a todas las imágenes)").classes("text-xs text-slate-400")

                    with ui.row().classes("items-center gap-2"):
                        btn_lote_iniciar = ui.button("Optimizar Todas", icon="bolt", on_click=lambda: iniciar_proceso_lote()) \
                            .props("push color=emerald dense size=md").classes("font-bold")
                        btn_lote_detener = ui.button("Detener", icon="stop", on_click=lambda: detener_proceso_lote()) \
                            .props("outline color=red dense size=md").disable()

                # Barra de progreso
                barra_lote = ui.linear_progress(value=0, show_value=False).props("color=emerald").classes("w-full")
                barra_lote.set_visibility(False)

                # Métricas de lote
                with ui.row().classes("w-full items-center justify-between bg-slate-900/50 p-3 rounded-xl flex-wrap gap-4"):
                    with ui.column().classes("gap-0"):
                        ui.label("Procesadas").classes("text-[11px] text-slate-400")
                        lbl_lote_conteo = ui.label("0 / 0").classes("text-base font-bold text-white")

                    with ui.column().classes("gap-0"):
                        ui.label("Peso Original").classes("text-[11px] text-slate-400")
                        lbl_lote_peso_orig = ui.label("0 B").classes("text-base font-bold text-slate-300")

                    with ui.column().classes("gap-0"):
                        ui.label("Peso Final").classes("text-[11px] text-slate-400")
                        lbl_lote_peso_opt = ui.label("0 B").classes("text-base font-bold text-sky-400")

                    with ui.column().classes("gap-0"):
                        ui.label("Ahorro Total").classes("text-[11px] text-slate-400")
                        lbl_lote_ahorro = ui.label("0%").classes("text-base font-bold text-emerald-400")

                    # Botones de Descarga en Bloque
                    with ui.row().classes("items-center gap-2"):
                        ui.button("ZIP", icon="archive", on_click=lambda: exportar_zip()) \
                            .props("push color=sky dense size=sm").tooltip("Descargar todas en archivo ZIP")
                        ui.button("CSV", icon="table_chart", on_click=lambda: exportar_csv()) \
                            .props("outline dense size=sm color=slate-300").tooltip("Reporte CSV")
                        ui.button("JSON", icon="code", on_click=lambda: exportar_json()) \
                            .props("outline dense size=sm color=slate-300").tooltip("Reporte JSON")
                        ui.button("Carpeta", icon="folder_open", on_click=lambda: abrir_carpeta_salidas()) \
                            .props("outline dense size=sm color=slate-300").tooltip("Abrir en Explorador")

                # Resultados por imagen
                contenedor_resultados_lote = ui.column().classes("w-full gap-2 max-h-64 overflow-y-auto")

            # --------------------------------------------------------------- #
            # 4. REGISTRO TÉCNICO
            # --------------------------------------------------------------- #
            with ui.expansion("Registro Técnico", icon="terminal").classes("w-full border border-slate-800 rounded-lg mt-2 text-xs"):
                log_tecnico = ui.log(max_lines=200).classes("w-full h-32 font-mono text-xs bg-black text-emerald-400 p-2 rounded")

    # ======================================================================= #
    # DIÁLOGO PARA AGREGAR RUTAS O ARCHIVOS LOCALES
    # ======================================================================= #
    with ui.dialog() as dialog_agregar, ui.card().classes("squoosh-card p-6 gap-4 min-w-[460px]"):
        ui.label("Agregar Imágenes").classes("text-lg font-bold text-white")
        
        async def on_subida_dialogo(e):
            nombre, contenido = await extraer_archivo_subido(e)
            if contenido:
                await procesar_archivo_subido(nombre, contenido)
                dialog_agregar.close()

        ui.upload(
            label="Arrastra o selecciona imágenes",
            multiple=True,
            auto_upload=True,
            on_upload=on_subida_dialogo,
        ).props("outlined accept=image/* color=sky").classes("w-full")

        with ui.row().classes("w-full gap-2 items-end mt-2"):
            inp_rutas_locales = ui.input("O ingresa rutas de archivos o carpetas") \
                .props("outlined dense color=sky").classes("flex-grow text-xs")
            
            def agregar_desde_rutas():
                txt = inp_rutas_locales.value or ""
                partes = [r.strip().strip('"') for r in txt.split(",") if r.strip()]
                if partes:
                    encontradas = buscar_imagenes(partes)
                    if encontradas:
                        for p in encontradas:
                            cargar_ruta_en_estado(p)
                        E.indice_activo = len(E.imagenes) - 1
                        actualizar_workspace()
                        ui.notify(f"Agregadas {len(encontradas)} imágenes", type="positive")
                        dialog_agregar.close()
                    else:
                        ui.notify("No se encontraron imágenes válidas", type="warning")
                else:
                    ui.notify("Escribe al menos una ruta", type="warning")

            ui.button("Cargar", on_click=agregar_desde_rutas).props("dense color=sky")

        ui.button("Cerrar", on_click=dialog_agregar.close).props("flat dense color=slate-400")

    # ======================================================================= #
    # DROPZONE CONTENIDO INICIAL
    # ======================================================================= #
    with panel_dropzone:
        ui.icon("add_photo_alternate", size="4rem").classes("text-sky-400")
        ui.label("Arrastra tus imágenes aquí o haz clic para comenzar").classes("text-xl font-bold text-white")
        ui.label("Soporta WebP, AVIF, JPEG, PNG, BMP, TIFF y GIF con previsualización en vivo").classes("text-sm text-slate-400 max-w-md")

        async def on_subida_inicial(e):
            nombre, contenido = await extraer_archivo_subido(e)
            if contenido:
                await procesar_archivo_subido(nombre, contenido)

        ui.upload(
            label="Seleccionar imágenes",
            multiple=True,
            auto_upload=True,
            on_upload=on_subida_inicial,
        ).props("outlined accept=image/* color=sky").classes("w-72 mt-2")

        with ui.row().classes("items-center gap-3 mt-4"):
            ui.button("Cargar imagen de prueba", icon="auto_fix_high", on_click=lambda: cargar_demo()) \
                .props("push color=indigo").classes("font-semibold")

    # ======================================================================= #
    # LÓGICA Y CALLBACKS REACTIVOS (ESTILO SQUOOSH)
    # ======================================================================= #

    modo_vista_actual = "split"

    def cambiar_modo_vista(modo: str):
        nonlocal modo_vista_actual
        modo_vista_actual = modo
        if modo == "split":
            contenedor_canvas.set_visibility(True)
            contenedor_lado_a_lado.set_visibility(False)
            btn_vista_split.props("color=sky")
            btn_vista_lado.props("color=slate-400")
        else:
            contenedor_canvas.set_visibility(False)
            contenedor_lado_a_lado.set_visibility(True)
            btn_vista_split.props("color=slate-400")
            btn_vista_lado.props("color=sky")

    def aplicar_preset_escala(pct: int):
        chk_activar_resize.value = True
        tabs_resize.value = tab_escala
        inp_scale_pct.value = pct
        trigger_actualizacion_preview()

    # Manejo de proporciones para dimensiones exactas en px
    sincronizando_dimensiones = False

    def on_ancho_cambiado(e):
        nonlocal sincronizando_dimensiones
        if sincronizando_dimensiones or not chk_candado_aspecto.value:
            trigger_actualizacion_preview()
            return

        if not E.imagenes or E.indice_activo >= len(E.imagenes):
            return
        img = E.imagenes[E.indice_activo]
        if img.ancho > 0 and e.value and e.value > 0:
            sincronizando_dimensiones = True
            try:
                nuevo_alto = max(1, int(round(float(e.value) * img.alto / img.ancho)))
                inp_px_alto.value = nuevo_alto
            finally:
                sincronizando_dimensiones = False
        trigger_actualizacion_preview()

    def on_alto_cambiado(e):
        nonlocal sincronizando_dimensiones
        if sincronizando_dimensiones or not chk_candado_aspecto.value:
            trigger_actualizacion_preview()
            return

        if not E.imagenes or E.indice_activo >= len(E.imagenes):
            return
        img = E.imagenes[E.indice_activo]
        if img.alto > 0 and e.value and e.value > 0:
            sincronizando_dimensiones = True
            try:
                nuevo_ancho = max(1, int(round(float(e.value) * img.ancho / img.alto)))
                inp_px_ancho.value = nuevo_ancho
            finally:
                sincronizando_dimensiones = False
        trigger_actualizacion_preview()

    inp_px_ancho.on_value_change(on_ancho_cambiado)
    inp_px_alto.on_value_change(on_alto_cambiado)

    async def procesar_archivo_subido(nombre: str, contenido: bytes):
        try:
            ruta = CARPETA_SUBIDAS / nombre
            with open(ruta, "wb") as f:
                f.write(contenido)
            cargar_ruta_en_estado(str(ruta), contenido=contenido)
            E.indice_activo = len(E.imagenes) - 1
            actualizar_workspace()
            ui.notify(f"Imagen '{nombre}' cargada con éxito", type="positive")
        except Exception as ex:
            ui.notify(f"Error al subir imagen: {ex}", type="negative")

    def cargar_ruta_en_estado(ruta_str: str, contenido: bytes | None = None):
        p = Path(ruta_str)
        if any(img.ruta == str(p) for img in E.imagenes):
            return

        peso = len(contenido) if contenido else (p.stat().st_size if p.exists() else 0)
        w, h = 0, 0
        try:
            if contenido:
                with Image.open(io.BytesIO(contenido)) as im:
                    w, h = im.width, im.height
            else:
                with Image.open(p) as im:
                    w, h = im.width, im.height
        except Exception:
            pass

        item = ItemImagen(
            ruta=str(p),
            nombre=p.name,
            peso=peso,
            ancho=w,
            alto=h,
            bytes_data=contenido,
        )
        E.imagenes.append(item)

    def cargar_demo():
        data, ruta_demo = crear_imagen_demo()
        cargar_ruta_en_estado(ruta_demo, contenido=data)
        E.indice_activo = len(E.imagenes) - 1
        actualizar_workspace()
        ui.notify("Ejemplo de Squoosh cargado con éxito", type="positive")

    def limpiar_todo():
        E.imagenes.clear()
        E.indice_activo = 0
        E.resultados_lote.clear()
        actualizar_workspace()

    def seleccionar_imagen(idx: int):
        if 0 <= idx < len(E.imagenes):
            E.indice_activo = idx
            actualizar_tira_miniaturas()
            actualizar_imagen_activa()

    def actualizar_tira_miniaturas():
        tira_miniaturas.clear()
        with tira_miniaturas:
            for i, img in enumerate(E.imagenes):
                es_activa = (i == E.indice_activo)
                clase_activa = "active" if es_activa else ""
                
                with ui.card().classes(f"squoosh-thumb-card {clase_activa} bg-slate-900 p-2 rounded-lg flex-row items-center gap-2 min-w-[170px]") \
                        .on("click", lambda _, idx=i: seleccionar_imagen(idx)):
                    ui.icon("image", size="1.2rem").classes("text-sky-400" if es_activa else "text-slate-500")
                    with ui.column().classes("gap-0 overflow-hidden"):
                        ui.label(img.nombre).classes("text-xs font-bold text-white truncate max-w-[110px]")
                        ui.label(f"{formatear_bytes(img.peso)}").classes("text-[10px] text-slate-400")

    def actualizar_workspace():
        hay_imagenes = len(E.imagenes) > 0
        panel_dropzone.set_visibility(not hay_imagenes)
        panel_workspace.set_visibility(hay_imagenes)

        if not hay_imagenes:
            return

        lbl_total_imagenes.set_text(f"{len(E.imagenes)} imágenes")
        if E.indice_activo >= len(E.imagenes):
            E.indice_activo = 0

        actualizar_tira_miniaturas()
        actualizar_imagen_activa()

    # ----------------------------------------------------------------------- #
    # Motor de Previsualización Reactiva Squoosh (Debounced)
    # ----------------------------------------------------------------------- #

    preview_task: asyncio.Task | None = None

    def trigger_actualizacion_preview():
        nonlocal preview_task
        if preview_task and not preview_task.done():
            preview_task.cancel()
        preview_task = asyncio.create_task(ejecutar_actualizacion_preview())

    async def ejecutar_actualizacion_preview():
        if not E.imagenes or E.indice_activo >= len(E.imagenes):
            return

        # Breve pausa para debouncing al mover sliders rápidamente
        try:
            await asyncio.sleep(0.04)
        except asyncio.CancelledError:
            return

        img_activa = E.imagenes[E.indice_activo]
        spinner_preview.set_visibility(True)

        fmt = str(radio_formato.value or "webp")
        if fmt == "avif" and not avif_disponible:
            fmt = "webp"
            radio_formato.value = "webp"

        calidad = int(slider_calidad.value)
        lossless = bool(chk_lossless.value)
        strip_meta = bool(chk_strip_meta.value)
        submuestreo = "4:4:4" if chk_submuestreo.value else "4:2:0"
        cuantizar = int(sel_cuantizar.value or 0)

        redimensionar = bool(chk_activar_resize.value)
        escala = float(inp_scale_pct.value or 100)
        filtro = str(sel_filtro.value or "lanczos")

        modo_resize = "none"
        ancho_target = 0
        alto_target = 0

        if redimensionar:
            if tabs_resize.value == tab_escala:
                modo_resize = "scale"
            else:
                modo_resize = "fit"
                ancho_target = int(inp_px_ancho.value or 0)
                alto_target = int(inp_px_alto.value or 0)

        opciones = OpcionesOptimizacion(
            formato=fmt,
            calidad=calidad,
            sin_perdida=lossless,
            eliminar_metadata=strip_meta,
            submuestreo=submuestreo,
            cuantizar_colores=cuantizar,
            redimensionar=redimensionar,
            modo_resize=modo_resize,
            escala=escala,
            ancho_max=ancho_target,
            alto_max=alto_target,
            mantener_aspecto=bool(chk_candado_aspecto.value),
            filtro_resample=filtro,
        )

        try:
            entrada = img_activa.bytes_data if img_activa.bytes_data else img_activa.ruta
            bytes_opt, info = await asyncio.to_thread(optimizar_en_memoria, entrada, opciones, formato=fmt)

            E.preview_bytes = bytes_opt
            E.preview_data_url = info["data_url"]
            E.preview_info = info

            # Actualizar visualizadores en el lienzo
            img_opt_tag.set_source(info["data_url"])
            img_side_opt.set_source(info["data_url"])

            # Actualizar etiquetas de formato y tamaño
            lbl_opt_formato_tag.set_text(fmt.upper())
            lbl_opt_info.set_text(f"{info['ancho']}×{info['alto']} • {formatear_bytes(info['peso_bytes'])}")

            ahorro_pct = info["ahorro_porcentaje"]
            if ahorro_pct >= 0:
                lbl_ahorro_texto.set_text(f"↓ -{ahorro_pct:.1f}% ({formatear_bytes(info['ahorro_bytes'])} menos)")
                lbl_ahorro_pill.classes(replace="squoosh-pill-saving bg-emerald-600")
            else:
                lbl_ahorro_texto.set_text(f"↑ +{abs(ahorro_pct):.1f}% ({formatear_bytes(abs(info['ahorro_bytes']))})")
                lbl_ahorro_pill.classes(replace="squoosh-pill-saving bg-amber-600")

            btn_descargar_activo.set_text(f"Descargar {fmt.upper()} ({formatear_bytes(info['peso_bytes'])})")
            lbl_descarga_peso.set_text(f"{formatear_bytes(info['peso_bytes'])} ({'-' if ahorro_pct >= 0 else '+'}{abs(ahorro_pct):.1f}%)")

        except asyncio.CancelledError:
            return
        except Exception as ex:
            log_tecnico.push(f"Error en preview: {ex}")
        finally:
            spinner_preview.set_visibility(False)

    def actualizar_imagen_activa():
        if not E.imagenes or E.indice_activo >= len(E.imagenes):
            return

        img = E.imagenes[E.indice_activo]
        try:
            if img.bytes_data:
                raw_bytes = img.bytes_data
            else:
                with open(img.ruta, "rb") as f:
                    raw_bytes = f.read()

            ext = Path(img.nombre).suffix.lower()
            mime_map = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
                ".avif": "image/avif",
                ".gif": "image/gif",
                ".bmp": "image/bmp",
            }
            mime = mime_map.get(ext, "image/png")
            b64_str = base64.b64encode(raw_bytes).decode("ascii")
            data_url_orig = f"data:{mime};base64,{b64_str}"

            img_orig_tag.set_source(data_url_orig)
            img_side_orig.set_source(data_url_orig)
            lbl_orig_info.set_text(f"{img.ancho}×{img.alto} • {formatear_bytes(img.peso)}")

            # Inicializar inputs de dimensiones en píxeles si la imagen tiene medidas válidas
            if img.ancho > 0 and img.alto > 0:
                nonlocal sincronizando_dimensiones
                sincronizando_dimensiones = True
                try:
                    inp_px_ancho.value = img.ancho
                    inp_px_alto.value = img.alto
                finally:
                    sincronizando_dimensiones = False
        except Exception as e:
            log_tecnico.push(f"Error cargando imagen original: {e}")

        # Disparar compresión de vista previa
        trigger_actualizacion_preview()

    # Enlazar cambios de controles al preview reactivo
    radio_formato.on_value_change(lambda _: trigger_actualizacion_preview())
    slider_calidad.on_value_change(lambda _: trigger_actualizacion_preview())
    chk_lossless.on_value_change(lambda _: trigger_actualizacion_preview())
    chk_strip_meta.on_value_change(lambda _: trigger_actualizacion_preview())
    chk_submuestreo.on_value_change(lambda _: trigger_actualizacion_preview())
    chk_activar_resize.on_value_change(lambda _: trigger_actualizacion_preview())
    tabs_resize.on_value_change(lambda _: trigger_actualizacion_preview())
    inp_scale_pct.on_value_change(lambda _: trigger_actualizacion_preview())
    chk_candado_aspecto.on_value_change(lambda _: trigger_actualizacion_preview())
    sel_filtro.on_value_change(lambda _: trigger_actualizacion_preview())
    sel_cuantizar.on_value_change(lambda _: trigger_actualizacion_preview())

    # ----------------------------------------------------------------------- #
    # Descarga Directa
    # ----------------------------------------------------------------------- #

    def descargar_imagen_activa():
        if not E.preview_bytes or not E.imagenes:
            ui.notify("No hay imagen procesada para descargar", type="warning")
            return

        img = E.imagenes[E.indice_activo]
        fmt = str(radio_formato.value or "webp").lower()
        ext = ".jpg" if fmt == "jpeg" else f".{fmt}"
        stem = Path(img.nombre).stem
        nombre_descarga = f"{stem}_squoosh{ext}"

        ui.download(E.preview_bytes, nombre_descarga)
        ui.notify(f"Descargando {nombre_descarga}", type="positive")

    # ----------------------------------------------------------------------- #
    # Procesamiento por Lotes
    # ----------------------------------------------------------------------- #

    async def iniciar_proceso_lote():
        if not E.imagenes:
            ui.notify("Agrega al menos una imagen", type="warning")
            return

        E.corriendo_lote = True
        E.cancelar_lote = False
        E.inicio_lote = datetime.now()
        E.resultados_lote.clear()
        contenedor_resultados_lote.clear()

        btn_lote_iniciar.disable()
        btn_lote_detener.enable()
        barra_lote.set_visibility(True)
        barra_lote.value = 0.0

        fmt = str(radio_formato.value or "webp").lower()
        calidad = int(slider_calidad.value)
        lossless = bool(chk_lossless.value)
        strip_meta = bool(chk_strip_meta.value)
        submuestreo = "4:4:4" if chk_submuestreo.value else "4:2:0"

        escala = float(inp_scale_pct.value or 100) if (chk_activar_resize.value and tabs_resize.value == tab_escala) else None
        ancho_target = int(inp_px_ancho.value or 0) if (chk_activar_resize.value and tabs_resize.value == tab_pixeles) else None
        alto_target = int(inp_px_alto.value or 0) if (chk_activar_resize.value and tabs_resize.value == tab_pixeles) else None

        total = len(E.imagenes)
        peso_orig_total = sum(i.peso for i in E.imagenes)
        peso_opt_total = 0

        log_tecnico.push(f"--- Iniciando lote: {total} imágenes a {fmt.upper()} (Calidad {calidad}%) ---")

        for idx, img in enumerate(E.imagenes, 1):
            if E.cancelar_lote:
                log_tecnico.push("¡Proceso por lotes detenido!")
                break

            try:
                res = await asyncio.to_thread(
                    optimizar_imagen,
                    ruta=img.ruta,
                    formatos=[fmt],
                    calidad=calidad,
                    escala=escala,
                    ancho=ancho_target,
                    alto=alto_target,
                    sin_perdida=lossless,
                    mantener_metadata=not strip_meta,
                    carpeta_salida=str(CARPETA_SALIDAS),
                    sufijo="_squoosh",
                    submuestreo=submuestreo,
                )
                E.resultados_lote.append(res)

                for f in res.resultados:
                    if not f.error:
                        peso_opt_total += f.peso_bytes
                        log_tecnico.push(f"[{idx}/{total}] ✓ {img.nombre} -> {formatear_bytes(f.peso_bytes)} (-{f.ahorro_porcentaje}%)")
                    else:
                        log_tecnico.push(f"[{idx}/{total}] ✗ {img.nombre}: {f.error}")

                # Tarjeta en la lista de resultados
                with contenedor_resultados_lote:
                    with ui.row().classes("w-full items-center justify-between p-2 rounded bg-slate-900 border border-slate-800"):
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("check_circle", color="green").classes("text-sm")
                            ui.label(img.nombre).classes("text-xs font-bold text-white")
                            peso_salida = res.resultados[0].peso_bytes if res.resultados else 0
                            ui.label(f"{formatear_bytes(img.peso)} → {formatear_bytes(peso_salida)}").classes("text-xs text-slate-400")

                        with ui.row().classes("items-center gap-2"):
                            if res.resultados and not res.resultados[0].error:
                                rf = res.resultados[0]
                                ui.badge(f"-{rf.ahorro_porcentaje}%", color="emerald").classes("text-xs")
                                def descargar_individual(rf_item=rf):
                                    if os.path.exists(rf_item.ruta_salida):
                                        with open(rf_item.ruta_salida, "rb") as arch:
                                            ui.download(arch.read(), os.path.basename(rf_item.ruta_salida))
                                ui.button(icon="download", on_click=descargar_individual).props("flat dense round size=xs color=sky")

            except Exception as e:
                log_tecnico.push(f"Error procesando {img.nombre}: {e}")

            # Actualizar estadísticas de lote
            barra_lote.value = idx / total
            lbl_lote_conteo.set_text(f"{idx} / {total}")
            lbl_lote_peso_orig.set_text(formatear_bytes(peso_orig_total))
            lbl_lote_peso_opt.set_text(formatear_bytes(peso_opt_total))
            if peso_orig_total > 0:
                ahorro = (peso_orig_total - peso_opt_total) / peso_orig_total * 100
                lbl_lote_ahorro.set_text(f"-{ahorro:.1f}%")

            await asyncio.sleep(0.01)

        E.corriendo_lote = False
        btn_lote_iniciar.enable()
        btn_lote_detener.disable()

        if not E.cancelar_lote:
            barra_lote.value = 1.0
            ui.notify(f"Lote completado: {len(E.resultados_lote)} imágenes procesadas", type="positive")

    def detener_proceso_lote():
        if E.corriendo_lote:
            E.cancelar_lote = True
            btn_lote_detener.disable()
            ui.notify("Deteniendo proceso por lotes...", type="info")

    # ----------------------------------------------------------------------- #
    # Exportaciones de Lotes
    # ----------------------------------------------------------------------- #

    def exportar_zip():
        if not E.resultados_lote:
            ui.notify("No hay resultados de lote para exportar", type="warning")
            return

        buf = io.BytesIO()
        count = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for r in E.resultados_lote:
                for f in r.resultados:
                    if not f.error and os.path.exists(f.ruta_salida):
                        zf.write(f.ruta_salida, arcname=os.path.basename(f.ruta_salida))
                        count += 1

        if count == 0:
            ui.notify("No se encontraron archivos para empaquetar", type="warning")
            return

        ui.download(buf.getvalue(), "imagenes_optimizadas_squoosh.zip")
        ui.notify(f"Descargando ZIP con {count} imágenes", type="positive")

    def exportar_csv():
        if not E.resultados_lote:
            ui.notify("No hay resultados de lote", type="warning")
            return

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Archivo", "Formato", "Peso_Original_Bytes", "Peso_Optimizado_Bytes", "Ahorro_Porcentaje"])
        for r in E.resultados_lote:
            for f in r.resultados:
                if not f.error:
                    w.writerow([Path(r.ruta_original).name, f.formato, r.peso_original_bytes, f.peso_bytes, f.ahorro_porcentaje])

        ui.download(buf.getvalue().encode("utf-8-sig"), "reporte_squoosh.csv")

    def exportar_json():
        if not E.resultados_lote:
            ui.notify("No hay resultados de lote", type="warning")
            return

        datos = []
        for r in E.resultados_lote:
            datos.append({
                "archivo": Path(r.ruta_original).name,
                "peso_original": r.peso_original_bytes,
                "ancho_original": r.ancho_original,
                "alto_original": r.alto_original,
                "resultados": [
                    {"formato": f.formato, "peso": f.peso_bytes, "ahorro": f.ahorro_porcentaje}
                    for f in r.resultados if not f.error
                ]
            })
        ui.download(json.dumps(datos, indent=2).encode(), "reporte_squoosh.json")

    def abrir_carpeta_salidas():
        carpeta = str(CARPETA_SALIDAS)
        if os.path.exists(carpeta):
            if os.name == "nt":
                os.startfile(carpeta)
            else:
                ui.notify(f"Carpeta: {carpeta}", type="info")
        else:
            ui.notify("La carpeta no existe", type="warning")

    # Iniciar con workspace oculto hasta cargar imágenes
    actualizar_workspace()