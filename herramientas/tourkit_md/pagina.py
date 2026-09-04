"""
pagina.py — Interfaz del conversor de tours a Markdown TourKit.

Arquitectura:
    A diferencia del Mapeador de URLs, esta herramienta NO necesita
    subproceso: leer un PDF con PyMuPDF cuesta decenas de milisegundos (26
    tours siguen estando por debajo del segundo), asi que montar la maquinaria
    de core.procesos aqui solo anadiria complejidad sin ganar nada. Todo corre
    en el proceso de la GUI.

    El estado vive DENTRO de index() y no en un objeto de modulo: cada
    conexion abre su propia conversion, y compartir los archivos subidos entre
    ventanas seria un error.

    Toda la UI va dentro de un unico @ui.page(...) por la misma razon que en el
    resto del proyecto: en "modo script" NiceGUI reejecuta el archivo fuente en
    cada conexion, algo que no funciona cuando esta congelado en un .exe.
"""

from __future__ import annotations

import tempfile
from copy import deepcopy
from pathlib import Path

from nicegui import ui

from . import idiomas, perfil as modulo_perfil
from .conversion import Resultado, convertir
from .extraccion import ErrorExtraccion

ID_HERRAMIENTA = "tourkit_md"
RUTA = "/tourkit-md"

CARPETA = Path(tempfile.gettempdir()) / "tourkit_md"
CARPETA.mkdir(exist_ok=True)

OPCIONES_IDIOMA = {codigo: idiomas.etiqueta(codigo) for codigo in idiomas.codigos()}


@ui.page(RUTA)
def index():
    perfil = modulo_perfil.cargar()
    subidos: dict[str, Path] = {}       # "base" | "traduccion" -> archivo en disco
    resultado: dict[str, Resultado] = {}  # caja mutable para el ultimo resultado

    # Los ajustes se actualizan cuando el usuario TOCA un control, no leyendo
    # todos los widgets al guardar. El panel "Valores por defecto" es un
    # ui.expansion y su contenido no se renderiza hasta que se despliega: al
    # leerlo cerrado, los campos devolvian None o "" y machacaban el perfil con
    # valores vacios, que ademas se releian en el siguiente arranque. Un control
    # que nadie toca ya no puede pisar nada.
    ajustes: dict = deepcopy(perfil)

    def _fijar(clave: str, valor) -> None:
        if valor is not None:
            ajustes[clave] = valor

    def _fijar_entero(clave: str, valor) -> None:
        if valor not in (None, ""):
            ajustes[clave] = int(valor)

    def _fijar_idioma(codigo: str, campo: str, valor) -> None:
        if valor is not None:
            ajustes.setdefault("por_idioma", {}).setdefault(codigo, {})[campo] = valor

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-4"):

        with ui.row().classes("items-center gap-2 -mb-2"):
            ui.button(icon="arrow_back", on_click=lambda: ui.navigate.to("/")) \
                .props("flat round dense")
            ui.label("Inicio").classes("text-sm text-gray-500")

        ui.label("Tours a Markdown (TourKit)").classes("text-3xl font-bold")
        ui.label("Convierte PDF o DOCX de tours al formato de importación de TourKit, "
                 "enlazando cada tour con su traducción").classes("text-sm text-gray-500 -mt-3")

        # ---- Documentos ---------------------------------------------------- #
        with ui.card().classes("w-full"):
            ui.label("Documentos").classes("text-lg font-medium")
            ui.label("El segundo documento es opcional: si trabajas en un solo idioma, "
                     "sube solo el primero.").classes("text-xs text-gray-500 -mt-2")

            with ui.row().classes("w-full gap-4 items-start flex-wrap"):
                for ranura, titulo, obligatorio in (
                    ("base", "Idioma base (original)", True),
                    ("traduccion", "Traducción", False),
                ):
                    with ui.column().classes("flex-grow min-w-80 gap-2"):
                        with ui.row().classes("items-center gap-2"):
                            ui.label(titulo).classes("font-medium")
                            if not obligatorio:
                                ui.badge("opcional").props("color=grey outline")

                        selector = ui.select(
                            OPCIONES_IDIOMA,
                            value="es" if ranura == "base" else "en",
                            label="Idioma del documento",
                        ).props("outlined dense").classes("w-full")

                        etiqueta_archivo = ui.label("Ningún archivo").classes(
                            "text-xs text-gray-500")

                        ui.upload(
                            label="Arrastra el .pdf o .docx",
                            auto_upload=True,
                            on_upload=lambda evento, r=ranura, lbl=etiqueta_archivo:
                                _recibir(evento, r, lbl, subidos),
                            on_rejected=lambda: ui.notify(
                                "Archivo rechazado: solo se aceptan .pdf y .docx",
                                type="warning"),
                        ).props('accept=".pdf,.docx" flat bordered').classes("w-full")

                        if ranura == "base":
                            idioma_base = selector
                        else:
                            idioma_traduccion = selector

        # ---- Ajustes -------------------------------------------------------- #
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-end gap-4 flex-wrap"):
                id_inicial = ui.number(
                    "ID inicial de WordPress",
                    value=perfil["id_inicial"], min=1, format="%d",
                ).props("outlined dense").classes("w-56")
                lbl_rango = ui.label("").classes("text-sm text-gray-500 pb-2")
                ui.space()
                ui.label("El botón de descarga aparece aquí abajo al convertir") \
                    .classes("text-xs text-gray-500 pb-2")
                boton_convertir = ui.button("Convertir", icon="play_arrow")

            ui.label(
                "En WordPress el ID es un AUTO_INCREMENT compartido por posts, revisiones, "
                "adjuntos y borradores automáticos, así que crece más rápido de lo que "
                "parece. Contrasta el rango con SELECT MAX(ID) FROM wp_posts antes de importar."
            ).classes("text-xs text-gray-500")

            with ui.expansion("Valores por defecto de la agencia", icon="tune").classes("w-full"):
                ui.label("Datos que no vienen en el documento y se repiten en todos los "
                         "tours. Se guardan para la próxima vez.").classes(
                    "text-xs text-gray-500 mb-2")

                with ui.row().classes("w-full gap-3 flex-wrap"):
                    ui.number("Grupo mín.", value=perfil["group_min"], format="%d",
                              on_change=lambda e: _fijar_entero("group_min", e.value)) \
                        .props("outlined dense").classes("w-32")
                    ui.number("Grupo máx.", value=perfil["group_max"], format="%d",
                              on_change=lambda e: _fijar_entero("group_max", e.value)) \
                        .props("outlined dense").classes("w-32")
                    ui.input("Moneda", value=perfil["currency"],
                             on_change=lambda e: _fijar("currency", e.value)) \
                        .props("outlined dense").classes("w-28")
                    ui.select(["group", "private"], value=perfil["tour_type"], label="Tipo",
                              on_change=lambda e: _fijar("tour_type", e.value)) \
                        .props("outlined dense").classes("w-36")
                    ui.select(["publish", "draft", "pending"], value=perfil["status"],
                              label="Estado",
                              on_change=lambda e: _fijar("status", e.value)) \
                        .props("outlined dense").classes("w-36")
                    ui.select(["daily", "fixed", "on_request"],
                              value=perfil["availability_type"], label="Disponibilidad",
                              on_change=lambda e: _fijar("availability_type", e.value)) \
                        .props("outlined dense").classes("w-40")

                for codigo in idiomas.codigos():
                    guardado = perfil["por_idioma"].get(codigo, {})
                    ui.label(idiomas.etiqueta(codigo)).classes("font-medium mt-3")
                    with ui.row().classes("w-full gap-3 flex-wrap"):
                        ui.input("Punto de inicio", value=guardado.get("start_point", ""),
                                 on_change=lambda e, c=codigo:
                                     _fijar_idioma(c, "start_point", e.value)) \
                            .props("outlined dense").classes("w-56")
                        ui.input("Punto final", value=guardado.get("end_point", ""),
                                 on_change=lambda e, c=codigo:
                                     _fijar_idioma(c, "end_point", e.value)) \
                            .props("outlined dense").classes("w-56")
                        ui.input("Categorías (separadas por coma)",
                                 value=", ".join(guardado.get("categories", [])),
                                 on_change=lambda e, c=codigo: _fijar_idioma(
                                     c, "categories",
                                     [x.strip() for x in (e.value or "").split(",") if x.strip()])) \
                            .props("outlined dense").classes("w-72")

        # ---- Resultado ------------------------------------------------------ #
        tarjeta_resultado = ui.card().classes("w-full tarjeta-resultado")
        tarjeta_resultado.set_visibility(False)
        with tarjeta_resultado:
            with ui.row().classes("w-full items-center gap-4 flex-wrap"):
                lbl_resumen = ui.label("").classes("text-lg font-medium")
                ui.space()
                boton_descargar = ui.button(
                    "Descargar .md", icon="download", color="primary"
                ).props("size=lg unelevated")

            tabla_tours = ui.table(
                columns=[
                    {"name": "id", "label": "ID", "field": "id", "align": "center"},
                    {"name": "idioma", "label": "Idioma", "field": "idioma", "align": "center"},
                    {"name": "titulo", "label": "Título", "field": "titulo", "align": "left"},
                    {"name": "sku", "label": "SKU", "field": "sku", "align": "left"},
                    {"name": "dias", "label": "Días", "field": "dias", "align": "center"},
                    {"name": "precio", "label": "Precio", "field": "precio", "align": "right"},
                    {"name": "original", "label": "Original", "field": "original",
                     "align": "center"},
                ],
                rows=[], row_key="id", pagination={"rowsPerPage": 20},
            ).classes("w-full")

            with ui.expansion("Revisión", icon="fact_check").classes("w-full") as panel_revision:
                tabla_hallazgos = ui.table(
                    columns=[
                        {"name": "nivel", "label": "Nivel", "field": "nivel", "align": "center"},
                        {"name": "tour", "label": "Tour", "field": "tour", "align": "left"},
                        {"name": "mensaje", "label": "Detalle", "field": "mensaje",
                         "align": "left"},
                    ],
                    rows=[], pagination={"rowsPerPage": 15},
                ).classes("w-full")
                tabla_hallazgos.add_slot("body-cell-nivel", r"""
                    <q-td :props="props">
                        <q-badge :color="props.value === 'error' ? 'red' : 'orange'">
                            {{ props.value }}
                        </q-badge>
                    </q-td>
                """)

            with ui.expansion("Vista previa del Markdown", icon="description").classes("w-full"):
                vista_previa = ui.code("").classes("w-full max-h-96 overflow-auto text-xs")

    # ----------------------------------------------------------------------- #
    # Logica
    # ----------------------------------------------------------------------- #

    def recoger_perfil() -> dict:
        """Devuelve el perfil vigente y lo guarda para la proxima vez."""
        ajustes["id_inicial"] = int(id_inicial.value or 2000)
        try:
            modulo_perfil.guardar(ajustes)
        except OSError:
            pass  # no poder persistir el perfil no debe impedir convertir
        return ajustes

    def convertir_ahora():
        if "base" not in subidos:
            ui.notify("Falta el documento del idioma base", type="warning")
            return
        if idioma_base.value == idioma_traduccion.value and "traduccion" in subidos:
            ui.notify("Los dos documentos declaran el mismo idioma", type="warning")
            return

        try:
            salida = convertir(
                subidos["base"], idioma_base.value,
                subidos.get("traduccion"),
                idioma_traduccion.value if "traduccion" in subidos else None,
                perfil=recoger_perfil(),
            )
        except ErrorExtraccion as error:
            ui.notify(str(error), type="negative", multi_line=True, close_button=True)
            return

        resultado["actual"] = salida
        _pintar(salida)

    def _pintar(salida: Resultado):
        tabla_tours.rows = [
            {
                "id": t["id"],
                "idioma": t["language"],
                "titulo": t["title"],
                "sku": t["sku"],
                "dias": t["duration_days"],
                "precio": f"{t['price_base']} {t['currency']}" if t["price_base"] else "—",
                "original": "—" if t["translation_of"] else "sí",
            }
            for t in salida.tours
        ]
        tabla_tours.update()

        tabla_hallazgos.rows = [
            {"nivel": h.nivel, "tour": h.tour, "mensaje": h.mensaje} for h in salida.hallazgos
        ]
        tabla_hallazgos.update()

        primero, ultimo = salida.rango_ids
        lbl_rango.text = f"Ocupará los IDs {primero} – {ultimo}"
        lbl_resumen.text = (
            f"{len(salida.tours)} tours · IDs {primero}–{ultimo} · "
            f"{salida.errores} errores, {salida.avisos} avisos"
        )
        lbl_resumen.classes(replace="text-lg font-medium " + (
            "text-red-600" if salida.errores else
            "text-orange-600" if salida.avisos else "text-green-600"))

        panel_revision.value = bool(salida.hallazgos)
        vista_previa.content = salida.markdown[:20000]
        tarjeta_resultado.set_visibility(True)

        # Con el panel de ajustes abierto, la tarjeta de resultados nace fuera
        # de la pantalla y el boton de descarga pasa desapercibido.
        ui.run_javascript(
            "document.querySelector('.tarjeta-resultado')"
            "?.scrollIntoView({behavior: 'smooth', block: 'start'});"
        )

        if salida.errores:
            ui.notify(f"{salida.errores} errores: revísalos antes de importar",
                      type="negative")
        else:
            ui.notify(f"{len(salida.tours)} tours convertidos", type="positive")

    def descargar():
        salida = resultado.get("actual")
        if not salida:
            ui.notify("Todavía no hay nada convertido", type="warning")
            return
        ui.download(salida.markdown.encode("utf-8"), "tours-tourkit.md")

    boton_convertir.on_click(convertir_ahora)
    boton_descargar.on_click(descargar)


async def _recibir(evento, ranura: str, etiqueta, subidos: dict[str, Path]) -> None:
    """
    Guarda en disco el archivo subido; extraccion.py trabaja sobre rutas.

    El handler es asincrono porque en NiceGUI 3.x el evento de subida entrega
    un FileUpload (evento.file) cuya API es async: .save() y .read() son
    corrutinas. Usar .save() en vez de leer los bytes a memoria importa con los
    archivos grandes, que NiceGUI ya tiene volcados en un temporal y solo hay
    que mover.
    """
    archivo = evento.file
    destino = CARPETA / f"{ranura}_{archivo.name}"
    await archivo.save(destino)

    subidos[ranura] = destino
    etiqueta.text = f"{archivo.name} ({archivo.size() // 1024} KB)"
    etiqueta.classes(replace="text-xs text-green-700 font-medium")
