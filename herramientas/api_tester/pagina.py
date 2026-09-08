"""
pagina.py — Interfaz de usuario NiceGUI para API Tester.

Incluye:
  - Constructor de peticiones (Request Builder) con parámetros, headers, body, auth y assertions.
  - Visualizador de respuestas con status coloreado, métricas de tiempo/tamaño, JSON interactivo y tests.
  - Gestor de colecciones con guardado en ./colecciones_api/ e importación del Mapeador de URLs.
  - Entornos de variables ({{base_url}}, etc.) con interpolación en vivo.
  - Historial persistente de peticiones con restauración en 1 clic.
  - Exportador a cURL, Python y JavaScript.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from nicegui import ui

from .cliente import ResultadoAssertion, ResultadoPeticion, enviar_peticion
from .colecciones import (
    CARPETA_DEFAULT as CARPETA_COLECCIONES,
    CarpetaPeticiones,
    Coleccion,
    Peticion,
    cargar_coleccion,
    guardar_coleccion,
    importar_desde_curl,
    importar_desde_mapeador,
    listar_colecciones_locales,
)
from .entornos import (
    Entorno,
    Variable,
    cargar_entornos,
    guardar_entornos,
    interpolar,
)
from .descubridor import (
    ResultadoDescubrimiento,
    RutaDescubierta,
    convertir_a_coleccion,
    descubrir_rutas_api,
)
from .exportador import a_curl, a_javascript_fetch, a_python_httpx, a_python_requests

ID_HERRAMIENTA = "api_tester"
RUTA = "/api-tester"

CARPETA_HISTORIAL = Path(tempfile.gettempdir()) / "api_tester"
ARCHIVO_HISTORIAL = CARPETA_HISTORIAL / "historial.json"
CARPETA_MAPEADOR = Path(tempfile.gettempdir()) / "mapeador_urls"

# Colores por método HTTP
_COLOR_METODO = {
    "GET": "#2196F3",  # azul
    "POST": "#4CAF50",  # verde
    "PUT": "#FF9800",  # naranja
    "PATCH": "#FFC107",  # ámbar
    "DELETE": "#F44336",  # rojo
    "HEAD": "#9C27B0",  # púrpura
    "OPTIONS": "#607D8B",  # gris-azul
}

_BADGE_METODO = {
    "GET": "blue",
    "POST": "green",
    "PUT": "orange",
    "PATCH": "amber",
    "DELETE": "red",
    "HEAD": "purple",
    "OPTIONS": "grey",
}

# Tabla simplificada de razones HTTP
_HTTP_REASON = {
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


# --------------------------------------------------------------------------- #
# Helpers de Historial
# --------------------------------------------------------------------------- #


def _guardar_en_historial(pet: Peticion, res: ResultadoPeticion, url_final: str):
    try:
        CARPETA_HISTORIAL.mkdir(parents=True, exist_ok=True)
        items = []
        if ARCHIVO_HISTORIAL.exists():
            try:
                items = json.loads(ARCHIVO_HISTORIAL.read_text(encoding="utf-8"))
            except Exception:
                items = []

        nuevo = {
            "id": str(uuid.uuid4())[:8],
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "metodo": pet.metodo,
            "url": url_final,
            "url_raw": pet.url,
            "status": res.status,
            "tiempo_ms": res.tiempo_ms,
            "tamaño_kb": res.tamaño_kb,
            "peticion": pet.to_dict(),
        }
        items.insert(0, nuevo)
        items = items[:150]
        ARCHIVO_HISTORIAL.write_text(
            json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def _leer_historial() -> list[dict]:
    if not ARCHIVO_HISTORIAL.exists():
        return []
    try:
        return json.loads(ARCHIVO_HISTORIAL.read_text(encoding="utf-8"))
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Página Principal de API Tester
# --------------------------------------------------------------------------- #


@ui.page(RUTA)
def index():
    # Estado reactivo de la sesión
    entornos_cargados = cargar_entornos()
    estado = {
        "entornos": entornos_cargados,
        "entorno_activo": entornos_cargados[0] if entornos_cargados else None,
        "peticion_actual": Peticion(
            nombre="Petición de Ejemplo",
            metodo="GET",
            url="",
            assertions=[
                {"tipo": "status", "operador": "==", "valor": "200"},
                {"tipo": "tiempo", "operador": "<", "valor": "1000"},
            ],
        ),
        "ultima_respuesta": None,
        "params_filas": [],
        "headers_filas": [],
        "assertions_filas": [],
        "enviando": False,
    }

    # Estilos CSS adicionales para la página
    ui.add_head_html("""
    <style>
        .metodo-badge {
            font-weight: 700;
            font-size: 11px;
            letter-spacing: 0.5px;
            padding: 2px 8px;
            border-radius: 4px;
            color: white;
            min-width: 52px;
            text-align: center;
            display: inline-block;
        }
        .url-bar { border: 2px solid #e0e0e0; border-radius: 8px; transition: border-color 0.2s; }
        .url-bar:focus-within { border-color: #2196F3; }
        .stat-pill {
            display: inline-flex; align-items: center; gap: 4px;
            padding: 3px 10px; border-radius: 20px;
            font-size: 11px; font-weight: 600; font-family: monospace;
        }
        .stat-pill-ok { background: #e8f5e9; color: #2e7d32; }
        .stat-pill-redirect { background: #fff3e0; color: #e65100; }
        .stat-pill-error { background: #ffebee; color: #c62828; }
        .stat-pill-neutral { background: #f5f5f5; color: #616161; }
        .kv-row:hover { background: #fafafa; }
        .resp-empty { 
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            padding: 48px 16px; color: #9e9e9e; text-align: center;
        }
        .resp-empty .material-icons { font-size: 48px; margin-bottom: 8px; opacity: 0.4; }
    </style>
    """)

    with ui.column().classes("w-full max-w-7xl mx-auto p-4 gap-3"):

        # ------------------------------------------------------------------- #
        # Barra Superior: Navegación + Título + Entorno
        # ------------------------------------------------------------------- #
        with ui.row().classes("items-center justify-between w-full flex-wrap gap-2"):
            with ui.row().classes("items-center gap-3"):
                ui.button(
                    icon="arrow_back", on_click=lambda: ui.navigate.to("/")
                ).props("flat round dense size=sm")
                ui.label("API Tester").classes("text-2xl font-bold")

            # Selector de Entorno (barra superior, siempre visible)
            with ui.row().classes(
                "items-center gap-2 bg-slate-50 px-3 py-1.5 rounded-lg border"
            ):
                ui.icon("dns").classes("text-base text-blue-600")

                nombres_entornos = ["Sin entorno"] + [
                    e.nombre for e in estado["entornos"]
                ]
                val_inicial = (
                    estado["entorno_activo"].nombre
                    if estado["entorno_activo"]
                    else "Sin entorno"
                )

                def al_cambiar_entorno(e):
                    nom = e.value
                    if nom == "Sin entorno":
                        estado["entorno_activo"] = None
                        lbl_env_info.text = "Variables no activas"
                    else:
                        ent = next(
                            (x for x in estado["entornos"] if x.nombre == nom), None
                        )
                        estado["entorno_activo"] = ent
                        lbl_env_info.text = (
                            f"{len(ent.variables)} variables" if ent else ""
                        )
                    actualizar_url_previsualizada()

                select_entorno = (
                    ui.select(
                        options=nombres_entornos,
                        value=val_inicial,
                        on_change=al_cambiar_entorno,
                    )
                    .props("dense borderless")
                    .classes("text-sm font-mono font-bold min-w-[140px]")
                )

                lbl_env_info = ui.label(
                    f"{len(estado['entorno_activo'].variables)} variables"
                    if estado["entorno_activo"]
                    else "Variables no activas"
                ).classes("text-[10px] text-gray-400")

        # ------------------------------------------------------------------- #
        # Pestañas Principales
        # ------------------------------------------------------------------- #
        with ui.tabs().classes("w-full") as tabs:
            tab_peticion = ui.tab("Petición", icon="send")
            tab_colecciones = ui.tab("Colecciones", icon="folder")
            tab_entornos = ui.tab("Entornos", icon="tune")
            tab_historial = ui.tab("Historial", icon="history")

        with ui.tab_panels(tabs, value=tab_peticion).classes(
            "w-full bg-transparent p-0"
        ):

            # =============================================================== #
            # PESTAÑA 1: PETICIÓN
            # =============================================================== #
            with ui.tab_panel(tab_peticion).classes("p-0 gap-0 column"):

                # ----- BARRA DE URL: Método + URL + Enviar ----- #
                with ui.row().classes("w-full items-stretch url-bar p-0 bg-white"):

                    # Selector de método con color
                    metodo_select = (
                        ui.select(
                            [
                                "GET",
                                "POST",
                                "PUT",
                                "PATCH",
                                "DELETE",
                                "HEAD",
                                "OPTIONS",
                            ],
                            value=estado["peticion_actual"].metodo,
                        )
                        .classes("w-28")
                        .props("dense borderless behavior=menu")
                        .style(
                            f"font-weight:700; color: {_COLOR_METODO.get(estado['peticion_actual'].metodo, '#333')}"
                        )
                    )

                    def al_cambiar_metodo(e):
                        c = _COLOR_METODO.get(e.value, "#333")
                        metodo_select.style(f"font-weight:700; color: {c}")

                    metodo_select.on_value_change(al_cambiar_metodo)

                    ui.separator().props("vertical")

                    # Input de URL
                    url_input = (
                        ui.input(
                            placeholder="https://api.ejemplo.com/recurso",
                            value=estado["peticion_actual"].url,
                        )
                        .classes("flex-grow")
                        .props("dense borderless")
                        .style(
                            "font-family: monospace; font-size: 13px; padding: 8px 12px;"
                        )
                    )

                    # Botón Enviar
                    btn_enviar = (
                        ui.button(
                            "Enviar",
                            icon="send",
                            on_click=lambda: ejecutar_envio(),
                        )
                        .props("unelevated dense color=primary no-caps")
                        .classes("rounded-none rounded-r-lg px-5")
                    )

                # URL resuelta (solo aparece cuando hay interpolación)
                lbl_url_resuelto = ui.label("").classes(
                    "text-[11px] font-mono text-blue-400 pl-2 -mt-1"
                )

                def actualizar_url_previsualizada():
                    u_raw = url_input.value or ""
                    u_interp = interpolar(u_raw, estado["entorno_activo"])
                    if u_raw != u_interp:
                        lbl_url_resuelto.text = f"→ {u_interp}"
                    else:
                        lbl_url_resuelto.text = ""

                url_input.on_value_change(actualizar_url_previsualizada)
                actualizar_url_previsualizada()

                # Barra de acciones secundarias
                with ui.row().classes("w-full items-center justify-between mt-2 mb-1"):
                    with ui.row().classes("items-center gap-1"):
                        ui.button(
                            "Guardar",
                            icon="bookmark_border",
                            on_click=lambda: abrir_modal_guardar(),
                        ).props("flat dense size=sm no-caps color=primary")
                        ui.button(
                            "Descubrir Rutas",
                            icon="radar",
                            on_click=lambda: abrir_modal_descubrimiento(),
                        ).props("flat dense size=sm no-caps color=secondary").tooltip(
                            "Auto-descubrir endpoints y métodos (GET, POST, etc.)"
                        )
                        ui.button(
                            "Código",
                            icon="code",
                            on_click=lambda: abrir_modal_codigo(),
                        ).props("flat dense size=sm no-caps")
                        ui.button(
                            "Importar cURL",
                            icon="terminal",
                            on_click=lambda: abrir_modal_importar_curl(),
                        ).props("flat dense size=sm no-caps")

                    # Atajo de teclado
                    ui.label("Ctrl+Enter para enviar").classes(
                        "text-[10px] text-gray-400"
                    )

                # Registrar atajo Ctrl+Enter
                ui.keyboard(
                    on_key=lambda e: (
                        ejecutar_envio()
                        if e.key.enter and e.modifiers.ctrl and not estado["enviando"]
                        else None
                    )
                )

                # ----- CONTENIDO DIVIDIDO: Configuración + Respuesta ----- #
                with ui.splitter(value=50).classes("w-full mt-2").style(
                    "min-height: 520px"
                ) as splitter:

                    # ===================================================== #
                    # PANEL IZQUIERDO: Configuración de la Petición
                    # ===================================================== #
                    with splitter.before:
                        with ui.column().classes("w-full h-full gap-0"):
                            with ui.tabs().classes("w-full").props("dense") as req_tabs:
                                subtab_params = ui.tab("Params")
                                subtab_headers = ui.tab("Headers")
                                subtab_body = ui.tab("Body")
                                subtab_auth = ui.tab("Auth")
                                subtab_tests = ui.tab("Tests")
                                subtab_ajustes = ui.tab("Ajustes")

                            with ui.tab_panels(req_tabs, value=subtab_params).classes(
                                "w-full p-3 flex-grow"
                            ):

                                # ---- PARAMS ---- #
                                with ui.tab_panel(subtab_params).classes(
                                    "p-0 gap-2 column"
                                ):
                                    ui.label("Parámetros de consulta (Query)").classes(
                                        "text-xs font-bold text-gray-500 uppercase tracking-wide"
                                    )
                                    cont_params = ui.column().classes("w-full gap-1")

                                    def agregar_fila_param(k="", v=""):
                                        with cont_params:
                                            with ui.row().classes(
                                                "w-full items-center gap-2 kv-row p-1 rounded"
                                            ) as fila:
                                                inp_k = (
                                                    ui.input(
                                                        placeholder="clave", value=k
                                                    )
                                                    .classes("flex-1")
                                                    .props("dense outlined")
                                                )
                                                ui.label("=").classes(
                                                    "text-gray-400 text-sm"
                                                )
                                                inp_v = (
                                                    ui.input(
                                                        placeholder="valor", value=v
                                                    )
                                                    .classes("flex-1")
                                                    .props("dense outlined")
                                                )
                                                ui.button(
                                                    icon="close",
                                                    on_click=lambda f=fila: f.delete(),
                                                ).props(
                                                    "flat dense round size=xs color=grey"
                                                )
                                                estado["params_filas"].append(
                                                    (inp_k, inp_v, fila)
                                                )

                                    ui.button(
                                        "Agregar parámetro",
                                        icon="add",
                                        on_click=lambda: agregar_fila_param(),
                                    ).props("flat dense size=sm no-caps color=primary")

                                # ---- HEADERS ---- #
                                with ui.tab_panel(subtab_headers).classes(
                                    "p-0 gap-2 column"
                                ):
                                    with ui.row().classes(
                                        "w-full justify-between items-center"
                                    ):
                                        ui.label("Cabeceras HTTP").classes(
                                            "text-xs font-bold text-gray-500 uppercase tracking-wide"
                                        )
                                        with ui.row().classes("gap-1"):
                                            ui.button(
                                                "JSON",
                                                on_click=lambda: agregar_fila_header(
                                                    "Content-Type", "application/json"
                                                ),
                                            ).props(
                                                "flat dense size=xs no-caps color=primary"
                                            )
                                            ui.button(
                                                "Accept",
                                                on_click=lambda: agregar_fila_header(
                                                    "Accept", "application/json"
                                                ),
                                            ).props(
                                                "flat dense size=xs no-caps color=primary"
                                            )

                                    cont_headers = ui.column().classes("w-full gap-1")

                                    def agregar_fila_header(k="", v=""):
                                        with cont_headers:
                                            with ui.row().classes(
                                                "w-full items-center gap-2 kv-row p-1 rounded"
                                            ) as fila:
                                                inp_k = (
                                                    ui.input(
                                                        placeholder="Header-Name",
                                                        value=k,
                                                    )
                                                    .classes("flex-1")
                                                    .props("dense outlined")
                                                )
                                                inp_v = (
                                                    ui.input(
                                                        placeholder="valor", value=v
                                                    )
                                                    .classes("flex-1")
                                                    .props("dense outlined")
                                                )
                                                ui.button(
                                                    icon="close",
                                                    on_click=lambda f=fila: f.delete(),
                                                ).props(
                                                    "flat dense round size=xs color=grey"
                                                )
                                                estado["headers_filas"].append(
                                                    (inp_k, inp_v, fila)
                                                )

                                    ui.button(
                                        "Agregar cabecera",
                                        icon="add",
                                        on_click=lambda: agregar_fila_header(),
                                    ).props("flat dense size=sm no-caps color=primary")

                                # ---- BODY ---- #
                                with ui.tab_panel(subtab_body).classes(
                                    "p-0 gap-3 column"
                                ):
                                    body_tipo_select = ui.radio(
                                        {
                                            "none": "Ninguno",
                                            "json": "JSON",
                                            "form": "Form Data",
                                            "raw": "Texto",
                                        },
                                        value=estado["peticion_actual"].body_tipo,
                                    ).props("dense inline")

                                    body_editor = (
                                        ui.textarea(
                                            placeholder='{\n  "clave": "valor"\n}',
                                            value=estado[
                                                "peticion_actual"
                                            ].body_contenido,
                                        )
                                        .classes("w-full font-mono text-xs")
                                        .props("outlined")
                                        .style("min-height: 200px")
                                    )

                                    with ui.row().classes("w-full justify-end"):

                                        def formatear_json():
                                            try:
                                                p = json.loads(body_editor.value)
                                                body_editor.value = json.dumps(
                                                    p, indent=2
                                                )
                                                ui.notify(
                                                    "JSON formateado", type="positive"
                                                )
                                            except Exception as err:
                                                ui.notify(
                                                    f"JSON inválido: {err}",
                                                    type="warning",
                                                )

                                        ui.button(
                                            "Formatear",
                                            icon="auto_fix_high",
                                            on_click=formatear_json,
                                        ).props("flat dense size=sm no-caps")

                                # ---- AUTH ---- #
                                with ui.tab_panel(subtab_auth).classes(
                                    "p-0 gap-3 column"
                                ):
                                    ui.label("Tipo de autenticación").classes(
                                        "text-xs font-bold text-gray-500 uppercase tracking-wide"
                                    )
                                    auth_tipo_select = (
                                        ui.select(
                                            {
                                                "none": "Sin autenticación",
                                                "bearer": "Bearer Token",
                                                "basic": "Basic Auth",
                                                "api_key": "API Key",
                                            },
                                            value=estado["peticion_actual"].auth_tipo,
                                        )
                                        .props("dense outlined")
                                        .classes("w-full")
                                    )

                                    cont_auth_campos = ui.column().classes(
                                        "w-full gap-2"
                                    )

                                    def render_auth_campos():
                                        cont_auth_campos.clear()
                                        t = auth_tipo_select.value
                                        with cont_auth_campos:
                                            if t == "none":
                                                ui.label(
                                                    "Esta petición no incluye autenticación."
                                                ).classes("text-xs text-gray-400 py-4")
                                            elif t == "bearer":
                                                ui.input(
                                                    "Token",
                                                    value=estado[
                                                        "peticion_actual"
                                                    ].auth_datos.get("token", ""),
                                                    placeholder="eyJhbGciOi...",
                                                ).classes("w-full font-mono").props(
                                                    "dense outlined"
                                                )
                                            elif t == "basic":
                                                ui.input(
                                                    "Usuario",
                                                    value=estado[
                                                        "peticion_actual"
                                                    ].auth_datos.get("usuario", ""),
                                                ).classes("w-full").props(
                                                    "dense outlined"
                                                )
                                                ui.input(
                                                    "Contraseña",
                                                    value=estado[
                                                        "peticion_actual"
                                                    ].auth_datos.get("password", ""),
                                                ).classes("w-full").props(
                                                    "dense outlined type=password"
                                                )
                                            elif t == "api_key":
                                                ui.input(
                                                    "Nombre de la clave",
                                                    value=estado[
                                                        "peticion_actual"
                                                    ].auth_datos.get(
                                                        "nombre", "X-API-Key"
                                                    ),
                                                ).classes("w-full").props(
                                                    "dense outlined"
                                                )
                                                ui.input(
                                                    "Valor",
                                                    value=estado[
                                                        "peticion_actual"
                                                    ].auth_datos.get("valor", ""),
                                                ).classes("w-full font-mono").props(
                                                    "dense outlined type=password"
                                                )
                                                ui.select(
                                                    {
                                                        "header": "Enviar en Header",
                                                        "query": "Enviar en Query",
                                                    },
                                                    value=estado[
                                                        "peticion_actual"
                                                    ].auth_datos.get(
                                                        "ubicacion", "header"
                                                    ),
                                                ).classes("w-full").props(
                                                    "dense outlined"
                                                )

                                    auth_tipo_select.on_value_change(render_auth_campos)
                                    render_auth_campos()

                                # ---- TESTS / ASSERTIONS ---- #
                                with ui.tab_panel(subtab_tests).classes(
                                    "p-0 gap-3 column"
                                ):
                                    ui.label("Pruebas automáticas").classes(
                                        "text-xs font-bold text-gray-500 uppercase tracking-wide"
                                    )
                                    ui.label(
                                        "Se evalúan automáticamente al recibir la respuesta."
                                    ).classes("text-[11px] text-gray-400 -mt-2")

                                    cont_assertions = ui.column().classes(
                                        "w-full gap-1"
                                    )

                                    def renderizar_assertions():
                                        cont_assertions.clear()
                                        with cont_assertions:
                                            for idx, a in enumerate(
                                                estado["peticion_actual"].assertions
                                            ):
                                                tipo_str = (
                                                    a.get("tipo", "")
                                                    .replace("_", " ")
                                                    .title()
                                                )
                                                prop = (
                                                    f".{a['propiedad']}"
                                                    if a.get("propiedad")
                                                    else ""
                                                )
                                                resumen = f"{tipo_str}{prop} {a['operador']} {a['valor']}"

                                                with ui.row().classes(
                                                    "w-full items-center justify-between p-2 "
                                                    "bg-slate-50 rounded border text-xs"
                                                ):
                                                    ui.label(resumen).classes(
                                                        "font-mono font-bold text-gray-700"
                                                    )
                                                    ui.button(
                                                        icon="close",
                                                        on_click=lambda i=idx: borrar_assertion(
                                                            i
                                                        ),
                                                    ).props(
                                                        "flat dense round size=xs color=grey"
                                                    )

                                    def borrar_assertion(idx: int):
                                        estado["peticion_actual"].assertions.pop(idx)
                                        renderizar_assertions()

                                    # Constructor de nueva assertion
                                    with ui.card().classes(
                                        "w-full p-3 bg-blue-50/50 border border-blue-100 gap-2"
                                    ):
                                        ui.label("Nueva prueba").classes(
                                            "text-xs font-bold text-blue-800"
                                        )
                                        with ui.row().classes(
                                            "w-full gap-2 items-end flex-wrap"
                                        ):
                                            sel_tipo_assert = (
                                                ui.select(
                                                    {
                                                        "status": "Status Code",
                                                        "tiempo": "Tiempo (ms)",
                                                        "header": "Cabecera",
                                                        "body_json": "Campo JSON",
                                                        "body_texto": "Texto en body",
                                                    },
                                                    value="status",
                                                    label="Verificar",
                                                )
                                                .classes("w-32")
                                                .props("dense outlined")
                                            )

                                            inp_prop = (
                                                ui.input(
                                                    label="Propiedad",
                                                    placeholder="ej: data.id",
                                                )
                                                .classes("w-28")
                                                .props("dense outlined")
                                            )

                                            sel_op = (
                                                ui.select(
                                                    [
                                                        "==",
                                                        "!=",
                                                        "<",
                                                        ">",
                                                        "<=",
                                                        ">=",
                                                        "contiene",
                                                        "existe",
                                                    ],
                                                    value="==",
                                                    label="Operador",
                                                )
                                                .classes("w-24")
                                                .props("dense outlined")
                                            )

                                            inp_val = (
                                                ui.input(
                                                    label="Valor",
                                                    placeholder="200",
                                                    value="200",
                                                )
                                                .classes("w-24")
                                                .props("dense outlined")
                                            )

                                            def agregar_assertion():
                                                estado[
                                                    "peticion_actual"
                                                ].assertions.append(
                                                    {
                                                        "tipo": sel_tipo_assert.value,
                                                        "propiedad": inp_prop.value.strip(),
                                                        "operador": sel_op.value,
                                                        "valor": inp_val.value.strip(),
                                                    }
                                                )
                                                renderizar_assertions()
                                                ui.notify(
                                                    "Prueba agregada", type="positive"
                                                )

                                            ui.button(
                                                icon="add",
                                                on_click=agregar_assertion,
                                            ).props(
                                                "dense unelevated color=primary round size=sm"
                                            )

                                    renderizar_assertions()

                                # ---- AJUSTES ---- #
                                with ui.tab_panel(subtab_ajustes).classes(
                                    "p-0 gap-3 column"
                                ):
                                    ui.label("Configuración de la petición").classes(
                                        "text-xs font-bold text-gray-500 uppercase tracking-wide"
                                    )
                                    chk_ssl = ui.checkbox(
                                        "Verificar certificados SSL", value=True
                                    )
                                    chk_redirects = ui.checkbox(
                                        "Seguir redirecciones", value=True
                                    )
                                    num_timeout = (
                                        ui.number(
                                            "Timeout (segundos)",
                                            value=30,
                                            min=1,
                                            max=300,
                                        )
                                        .props("dense outlined")
                                        .classes("w-40")
                                    )

                    # ===================================================== #
                    # PANEL DERECHO: Respuesta
                    # ===================================================== #
                    with splitter.after:
                        with ui.column().classes("w-full h-full gap-0"):

                            # Barra de métricas de respuesta
                            with ui.row().classes(
                                "w-full items-center gap-3 px-3 py-2 bg-slate-50 "
                                "border-b flex-wrap"
                            ):
                                badge_status = ui.html(
                                    '<span class="stat-pill stat-pill-neutral">Esperando</span>'
                                )
                                lbl_tiempo = ui.html(
                                    '<span class="stat-pill stat-pill-neutral">— ms</span>'
                                )
                                lbl_tam = ui.html(
                                    '<span class="stat-pill stat-pill-neutral">— KB</span>'
                                )
                                lbl_proto = ui.label("").classes(
                                    "text-[10px] font-mono text-gray-400"
                                )

                                ui.space()

                                badge_tests_summary = ui.html(
                                    '<span class="stat-pill stat-pill-neutral">Sin tests</span>'
                                )

                            # Pestañas de respuesta
                            with ui.tabs().classes("w-full").props(
                                "dense"
                            ) as resp_tabs:
                                subtab_resp_body = ui.tab("Body")
                                subtab_resp_headers = ui.tab("Headers")
                                subtab_resp_cookies = ui.tab("Cookies")
                                subtab_resp_tests = ui.tab("Tests")
                                subtab_resp_redirs = ui.tab("Redirecciones")

                            with ui.tab_panels(
                                resp_tabs, value=subtab_resp_body
                            ).classes("w-full p-3 flex-grow"):

                                # ---- RESP: BODY ---- #
                                with ui.tab_panel(subtab_resp_body).classes(
                                    "p-0 gap-2 column"
                                ):
                                    with ui.row().classes(
                                        "w-full justify-between items-center"
                                    ):
                                        radio_vista_body = ui.radio(
                                            ["Formateado", "Raw"],
                                            value="Formateado",
                                        ).props("dense inline")
                                        ui.button(
                                            icon="content_copy",
                                            on_click=lambda: copiar_respuesta(),
                                        ).props("flat dense round size=sm").tooltip(
                                            "Copiar respuesta"
                                        )

                                    cont_resp_body = ui.column().classes(
                                        "w-full max-h-[400px] overflow-y-auto"
                                    )
                                    cont_resp_body_raw = (
                                        ui.textarea()
                                        .classes("w-full font-mono text-xs")
                                        .props("outlined readonly")
                                        .style("min-height:300px")
                                    )
                                    cont_resp_body_raw.set_visibility(False)

                                    # Estado vacío
                                    cont_resp_empty = ui.column().classes("resp-empty")
                                    with cont_resp_empty:
                                        ui.icon("send").classes(
                                            "text-5xl text-gray-300"
                                        )
                                        ui.label(
                                            "Envía una petición para ver la respuesta"
                                        ).classes("text-sm text-gray-400")

                                    def copiar_respuesta():
                                        r: ResultadoPeticion | None = estado[
                                            "ultima_respuesta"
                                        ]
                                        if r and r.body_text:
                                            ui.clipboard.write(r.body_text)
                                            ui.notify(
                                                "Copiado al portapapeles",
                                                type="positive",
                                            )

                                # ---- RESP: HEADERS ---- #
                                with ui.tab_panel(subtab_resp_headers).classes(
                                    "p-0 gap-2 column"
                                ):
                                    cont_resp_headers = ui.column().classes(
                                        "w-full max-h-[400px] overflow-y-auto gap-0"
                                    )

                                # ---- RESP: COOKIES ---- #
                                with ui.tab_panel(subtab_resp_cookies).classes(
                                    "p-0 gap-2 column"
                                ):
                                    cont_resp_cookies = ui.column().classes(
                                        "w-full max-h-[400px] overflow-y-auto gap-0"
                                    )

                                # ---- RESP: TESTS ---- #
                                with ui.tab_panel(subtab_resp_tests).classes(
                                    "p-0 gap-2 column"
                                ):
                                    cont_resp_tests = ui.column().classes(
                                        "w-full max-h-[400px] overflow-y-auto gap-2"
                                    )

                                # ---- RESP: REDIRECCIONES ---- #
                                with ui.tab_panel(subtab_resp_redirs).classes(
                                    "p-0 gap-2 column"
                                ):
                                    cont_resp_redirs = ui.column().classes(
                                        "w-full max-h-[400px] overflow-y-auto gap-1"
                                    )

            # =============================================================== #
            # PESTAÑA 2: COLECCIONES
            # =============================================================== #
            with ui.tab_panel(tab_colecciones).classes("p-0 gap-4 column"):
                with ui.row().classes(
                    "w-full items-center justify-between flex-wrap gap-2"
                ):
                    ui.label("Colecciones").classes("text-lg font-bold")

                    with ui.row().classes("gap-2 flex-wrap"):
                        ui.button(
                            "Auto-Descubrir API",
                            icon="radar",
                            on_click=lambda: abrir_modal_descubrimiento(),
                        ).props("unelevated dense no-caps color=primary size=sm")

                        ui.button(
                            "Importar desde Mapeador",
                            icon="travel_explore",
                            on_click=lambda: abrir_modal_importar_mapeador(),
                        ).props("outlined dense no-caps size=sm")

                        ui.button(
                            "Nueva colección",
                            icon="add",
                            on_click=lambda: abrir_modal_nueva_coleccion(),
                        ).props("outlined dense no-caps size=sm")

                cont_lista_colecciones = ui.column().classes("w-full gap-3")

            # =============================================================== #
            # PESTAÑA 3: ENTORNOS
            # =============================================================== #
            with ui.tab_panel(tab_entornos).classes("p-0 gap-4 column"):
                with ui.row().classes(
                    "w-full items-center justify-between flex-wrap gap-2"
                ):
                    ui.label("Entornos y Variables").classes("text-lg font-bold")
                    with ui.row().classes("gap-2"):
                        ui.button(
                            "Nuevo entorno",
                            icon="add",
                            on_click=lambda: abrir_modal_nuevo_entorno(),
                        ).props("outlined dense no-caps size=sm")
                        ui.button(
                            "Guardar cambios",
                            icon="save",
                            on_click=lambda: guardar_todos_los_entornos(),
                        ).props("unelevated dense no-caps color=primary size=sm")

                ui.label(
                    "Usa {{variable}} en URLs, headers o bodies. Las variables se interpolan al enviar."
                ).classes("text-xs text-gray-400 -mt-2")

                with ui.row().classes(
                    "w-full items-center gap-3 bg-slate-50 p-3 rounded border"
                ):
                    ui.label("Editando:").classes("text-xs font-bold text-gray-600")
                    select_entorno_editor = (
                        ui.select(
                            [e.nombre for e in estado["entornos"]],
                            value=(
                                estado["entornos"][0].nombre
                                if estado["entornos"]
                                else None
                            ),
                        )
                        .classes("w-60")
                        .props("dense outlined")
                    )

                cont_tabla_variables = ui.column().classes("w-full gap-2")

            # =============================================================== #
            # PESTAÑA 4: HISTORIAL
            # =============================================================== #
            with ui.tab_panel(tab_historial).classes("p-0 gap-4 column"):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Historial de peticiones").classes("text-lg font-bold")
                    ui.button(
                        "Limpiar",
                        icon="delete_sweep",
                        on_click=lambda: vaciar_historial(),
                    ).props("flat dense no-caps color=negative size=sm")

                cont_tabla_historial = ui.column().classes("w-full gap-1")

    # ----------------------------------------------------------------------- #
    # Lógica de Envío y Renderizado de Respuestas
    # ----------------------------------------------------------------------- #

    async def ejecutar_envio():
        raw_url = (url_input.value or "").strip()
        if not raw_url:
            ui.notify("Ingresa una URL", type="warning")
            return

        if estado["enviando"]:
            return
        estado["enviando"] = True
        btn_enviar.disable()
        btn_enviar.props("loading=true")

        # Ocultar estado vacío
        cont_resp_empty.set_visibility(False)

        # 1. Compilar Petición desde la UI
        params_dict = {}
        for k_elem, v_elem, fila in estado["params_filas"]:
            try:
                k = (k_elem.value or "").strip()
                v = (v_elem.value or "").strip()
                if k:
                    params_dict[k] = v
            except Exception:
                pass

        headers_dict = {}
        for k_elem, v_elem, fila in estado["headers_filas"]:
            try:
                k = (k_elem.value or "").strip()
                v = (v_elem.value or "").strip()
                if k:
                    headers_dict[k] = v
            except Exception:
                pass

        auth_t = auth_tipo_select.value
        auth_d = {}
        if auth_t == "bearer":
            for child in cont_auth_campos.default_slot.children:
                if hasattr(child, "value"):
                    auth_d["token"] = getattr(child, "value", "") or ""
                    break
        elif auth_t == "basic":
            vals = [
                getattr(c, "value", "")
                for c in cont_auth_campos.default_slot.children
                if hasattr(c, "value")
            ]
            if len(vals) >= 2:
                auth_d["usuario"] = vals[0]
                auth_d["password"] = vals[1]
        elif auth_t == "api_key":
            vals = [
                getattr(c, "value", "")
                for c in cont_auth_campos.default_slot.children
                if hasattr(c, "value")
            ]
            if len(vals) >= 3:
                auth_d["nombre"] = vals[0]
                auth_d["valor"] = vals[1]
                auth_d["ubicacion"] = vals[2]

        pet = Peticion(
            metodo=metodo_select.value,
            url=raw_url,
            params=params_dict,
            headers=headers_dict,
            body_tipo=body_tipo_select.value,
            body_contenido=body_editor.value or "",
            auth_tipo=auth_t,
            auth_datos=auth_d,
            assertions=list(estado["peticion_actual"].assertions),
        )
        estado["peticion_actual"] = pet

        # 2. Interpolar con entorno activo
        ent = estado["entorno_activo"]
        url_final = interpolar(pet.url, ent)
        params_final = {
            interpolar(k, ent): interpolar(v, ent) for k, v in pet.params.items()
        }
        headers_final = {
            interpolar(k, ent): interpolar(v, ent) for k, v in pet.headers.items()
        }
        body_final = interpolar(pet.body_contenido, ent)
        auth_datos_final = {
            interpolar(k, ent): interpolar(v, ent) for k, v in pet.auth_datos.items()
        }

        try:
            res: ResultadoPeticion = await enviar_peticion(
                metodo=pet.metodo,
                url=url_final,
                params=params_final,
                headers=headers_final,
                body_tipo=pet.body_tipo,
                body_contenido=body_final,
                auth_tipo=pet.auth_tipo,
                auth_datos=auth_datos_final,
                timeout=float(num_timeout.value or 30),
                verificar_ssl=chk_ssl.value,
                seguir_redirecciones=chk_redirects.value,
                assertions=pet.assertions,
            )
            estado["ultima_respuesta"] = res
            renderizar_respuesta(res)
            _guardar_en_historial(pet, res, url_final)
            recargar_tabla_historial()
        except Exception as e:
            ui.notify(f"Error: {e}", type="negative")
        finally:
            estado["enviando"] = False
            btn_enviar.enable()
            btn_enviar.props("loading=false")

    def renderizar_respuesta(res: ResultadoPeticion):
        # 1. Badge de Status con píldoras coloreadas
        if res.error:
            badge_status.content = (
                '<span class="stat-pill stat-pill-error">ERROR</span>'
            )
            ui.notify(f"Fallo de conexión: {res.error}", type="negative")
        else:
            reason = _HTTP_REASON.get(res.status, "")
            texto_status = f"{res.status} {reason}".strip()
            if 200 <= res.status < 300:
                css = "stat-pill-ok"
            elif 300 <= res.status < 400:
                css = "stat-pill-redirect"
            else:
                css = "stat-pill-error"
            badge_status.content = (
                f'<span class="stat-pill {css}">{texto_status}</span>'
            )

        # Tiempo
        t = res.tiempo_ms
        css_t = (
            "stat-pill-ok"
            if t < 300
            else ("stat-pill-redirect" if t < 1000 else "stat-pill-error")
        )
        lbl_tiempo.content = f'<span class="stat-pill {css_t}">{t} ms</span>'

        # Tamaño
        lbl_tam.content = (
            f'<span class="stat-pill stat-pill-neutral">{res.tamaño_kb} KB</span>'
        )
        lbl_proto.text = res.http_version

        # 2. Resumen de Tests
        if res.assertions:
            pasadas = res.total_assertions_pasadas
            total_a = len(res.assertions)
            css_test = "stat-pill-ok" if pasadas == total_a else "stat-pill-error"
            badge_tests_summary.content = (
                f'<span class="stat-pill {css_test}">{pasadas}/{total_a} tests</span>'
            )
        else:
            badge_tests_summary.content = (
                '<span class="stat-pill stat-pill-neutral">Sin tests</span>'
            )

        # 3. Body de respuesta
        cont_resp_body.clear()
        cont_resp_body_raw.value = res.body_text or (res.error or "")

        with cont_resp_body:
            if res.json_data is not None:
                try:
                    ui.json_editor({"content": {"json": res.json_data}}).classes(
                        "w-full"
                    ).style("min-height: 250px")
                except Exception:
                    ui.code(
                        json.dumps(res.json_data, indent=2), language="json"
                    ).classes("w-full text-xs font-mono")
            else:
                ui.code(
                    res.body_text or (res.error or "Sin contenido"), language="html"
                ).classes("w-full text-xs font-mono")

        # Toggle Formateado / Raw
        def alternar_vista_body(e):
            if e.value == "Raw":
                cont_resp_body.set_visibility(False)
                cont_resp_body_raw.set_visibility(True)
            else:
                cont_resp_body.set_visibility(True)
                cont_resp_body_raw.set_visibility(False)

        radio_vista_body.on_value_change(alternar_vista_body)

        # 4. Headers de respuesta
        cont_resp_headers.clear()
        with cont_resp_headers:
            if res.headers:
                for k, v in res.headers.items():
                    with ui.row().classes(
                        "w-full items-start gap-2 p-2 border-b text-xs"
                    ):
                        ui.label(k).classes("font-bold text-gray-700 min-w-[180px]")
                        ui.label(v).classes("font-mono text-gray-500 break-all")
            else:
                ui.label("Sin cabeceras recibidas").classes("text-xs text-gray-400 p-4")

        # 5. Cookies
        cont_resp_cookies.clear()
        with cont_resp_cookies:
            if res.cookies:
                for k, v in res.cookies.items():
                    with ui.row().classes(
                        "w-full items-start gap-2 p-2 border-b text-xs"
                    ):
                        ui.label(k).classes("font-bold text-gray-700 min-w-[180px]")
                        ui.label(v).classes("font-mono text-gray-500 break-all")
            else:
                ui.label("Sin cookies recibidas").classes("text-xs text-gray-400 p-4")

        # 6. Tests / Assertions
        cont_resp_tests.clear()
        with cont_resp_tests:
            if res.assertions:
                for a in res.assertions:
                    exito = a.exito
                    with ui.row().classes(
                        f"w-full items-center gap-3 p-2.5 rounded border "
                        f"{'bg-green-50 border-green-200' if exito else 'bg-red-50 border-red-200'}"
                    ):
                        ui.icon("check_circle" if exito else "cancel").classes(
                            f"text-lg {'text-green-600' if exito else 'text-red-600'}"
                        )
                        with ui.column().classes("gap-0 flex-grow"):
                            ui.label(a.nombre).classes(
                                "text-xs font-mono font-bold text-gray-800"
                            )
                            ui.label(f"Obtenido: {a.obtenido}").classes(
                                "text-[11px] text-gray-500"
                            )
                        ui.html(
                            f'<span class="stat-pill {"stat-pill-ok" if exito else "stat-pill-error"}">'
                            f'{"PASÓ" if exito else "FALLÓ"}</span>'
                        )
            else:
                ui.label("No se definieron pruebas.").classes(
                    "text-xs text-gray-400 p-4"
                )

        # 7. Redirecciones
        cont_resp_redirs.clear()
        with cont_resp_redirs:
            if res.redirects:
                for idx, r in enumerate(res.redirects, 1):
                    with ui.row().classes(
                        "w-full items-center gap-2 p-2 border-b text-xs"
                    ):
                        ui.badge(f"{idx}", color="blue").props("dense")
                        ui.badge(f"{r['status']}", color="orange").props("dense")
                        ui.label(r["url"]).classes("font-mono text-blue-700 truncate")
            else:
                ui.label("Sin redirecciones.").classes("text-xs text-gray-400 p-4")

    # ----------------------------------------------------------------------- #
    # Cargar Petición en el Builder
    # ----------------------------------------------------------------------- #

    def cargar_peticion_en_builder(p: Peticion):
        estado["peticion_actual"] = p
        metodo_select.value = p.metodo
        url_input.value = p.url
        body_tipo_select.value = p.body_tipo
        body_editor.value = p.body_contenido
        auth_tipo_select.value = p.auth_tipo

        # Limpiar y rellenar params
        for _, _, f in estado["params_filas"]:
            try:
                f.delete()
            except Exception:
                pass
        estado["params_filas"].clear()
        for k, v in p.params.items():
            agregar_fila_param(k, v)

        # Limpiar y rellenar headers
        for _, _, f in estado["headers_filas"]:
            try:
                f.delete()
            except Exception:
                pass
        estado["headers_filas"].clear()
        for k, v in p.headers.items():
            agregar_fila_header(k, v)

        render_auth_campos()
        renderizar_assertions()
        actualizar_url_previsualizada()

        tabs.set_value(tab_peticion)
        ui.notify(f"'{p.nombre}' cargada", type="positive")

    # ----------------------------------------------------------------------- #
    # Modales
    # ----------------------------------------------------------------------- #

    def abrir_modal_guardar():
        dialog = ui.dialog()
        with dialog, ui.card().classes("w-96 p-4 gap-3"):
            ui.label("Guardar en colección").classes("text-base font-bold")
            nom_inp = (
                ui.input(
                    "Nombre de la petición",
                    value=estado["peticion_actual"].nombre,
                )
                .props("dense outlined")
                .classes("w-full")
            )

            cols = listar_colecciones_locales()
            nombres_cols = [c["nombre"] for c in cols] + ["+ Nueva colección"]
            col_select = (
                ui.select(
                    nombres_cols,
                    value=(
                        nombres_cols[0]
                        if len(nombres_cols) > 1
                        else "+ Nueva colección"
                    ),
                    label="Colección destino",
                )
                .props("dense outlined")
                .classes("w-full")
            )
            nueva_col_inp = (
                ui.input("Nombre de la nueva colección", value="Mi API")
                .props("dense outlined")
                .classes("w-full")
            )
            nueva_col_inp.set_visibility(col_select.value == "+ Nueva colección")
            col_select.on_value_change(
                lambda e: nueva_col_inp.set_visibility(e.value == "+ Nueva colección")
            )

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancelar", on_click=dialog.close).props("flat dense no-caps")

                def confirmar_guardado():
                    p = estado["peticion_actual"]
                    p.nombre = nom_inp.value.strip() or "Petición"
                    if col_select.value == "+ Nueva colección":
                        c = Coleccion(
                            nombre=nueva_col_inp.value.strip() or "Colección",
                            peticiones_raiz=[p],
                        )
                    else:
                        target = next(
                            (
                                x["coleccion"]
                                for x in cols
                                if x["nombre"] == col_select.value
                            ),
                            None,
                        )
                        if target:
                            c = target
                            c.peticiones_raiz.append(p)
                        else:
                            c = Coleccion(nombre=col_select.value, peticiones_raiz=[p])
                    guardar_coleccion(c)
                    dialog.close()
                    recargar_vista_colecciones()
                    ui.notify(f"Guardado en '{c.nombre}'", type="positive")

                ui.button("Guardar", icon="check", on_click=confirmar_guardado).props(
                    "dense unelevated color=primary no-caps"
                )
        dialog.open()

    def abrir_modal_importar_curl():
        dialog = ui.dialog()
        with dialog, ui.card().classes("w-full max-w-xl p-4 gap-3"):
            ui.label("Importar desde cURL").classes("text-base font-bold")
            ui.label(
                "Pega un comando curl para extraer método, URL, headers y body."
            ).classes("text-xs text-gray-500")

            curl_txt = (
                ui.textarea(
                    placeholder="curl -X POST https://api.ejemplo.com/data -H 'Content-Type: application/json' -d '{\"foo\":\"bar\"}'"
                )
                .classes("w-full font-mono text-xs")
                .props("outlined")
                .style("min-height:120px")
            )

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancelar", on_click=dialog.close).props("flat dense no-caps")

                def parsear_y_cargar():
                    raw = curl_txt.value.strip()
                    if not raw:
                        return
                    try:
                        pet = importar_desde_curl(raw)
                        cargar_peticion_en_builder(pet)
                        dialog.close()
                        ui.notify("cURL importado", type="positive")
                    except Exception as err:
                        ui.notify(f"Error: {err}", type="negative")

                ui.button("Importar", icon="check", on_click=parsear_y_cargar).props(
                    "dense unelevated color=primary no-caps"
                )
        dialog.open()

    def abrir_modal_importar_mapeador():
        dialog = ui.dialog()
        with dialog, ui.card().classes("w-full max-w-2xl max-h-[85vh] p-4 gap-3"):
            ui.label("Importar desde Mapeador de URLs").classes("text-lg font-bold")
            ui.label(
                "Selecciona un sitio rastreado para generar una colección."
            ).classes("text-xs text-gray-500")

            chk_solo_apis = ui.checkbox(
                "Solo endpoints de API (/api/, /v1/, .json)",
                value=False,
            )

            archivos_mapeador = (
                list(CARPETA_MAPEADOR.glob("*.json"))
                if CARPETA_MAPEADOR.exists()
                else []
            )

            if not archivos_mapeador:
                ui.label(
                    "No hay rastreos disponibles. Usa primero el Mapeador de URLs."
                ).classes("text-sm text-gray-400 p-4")
            else:
                with ui.column().classes(
                    "w-full max-h-72 overflow-y-auto gap-1 border p-2 rounded bg-slate-50"
                ):
                    for arch in archivos_mapeador:
                        with ui.row().classes(
                            "w-full items-center justify-between p-2 "
                            "bg-white rounded border hover:bg-blue-50 cursor-pointer"
                        ):
                            ui.label(arch.stem).classes(
                                "font-mono text-xs font-bold text-blue-800"
                            )

                            def importar_este(ruta=arch):
                                try:
                                    col = importar_desde_mapeador(
                                        ruta, solo_apis=chk_solo_apis.value
                                    )
                                    guardar_coleccion(col)
                                    dialog.close()
                                    recargar_vista_colecciones()
                                    tabs.set_value(tab_colecciones)
                                    ui.notify(
                                        f"'{col.nombre}' creada con {col.total_peticiones} peticiones",
                                        type="positive",
                                    )
                                except Exception as err:
                                    ui.notify(f"Error: {err}", type="negative")

                            ui.button(
                                "Importar",
                                icon="download",
                                on_click=importar_este,
                            ).props("dense outline size=sm no-caps")

            with ui.row().classes("w-full justify-end mt-2"):
                ui.button("Cerrar", on_click=dialog.close).props("flat dense no-caps")
        dialog.open()

    def abrir_modal_nueva_coleccion():
        dialog = ui.dialog()
        with dialog, ui.card().classes("w-96 p-4 gap-3"):
            ui.label("Nueva colección").classes("text-base font-bold")
            nom_inp = (
                ui.input("Nombre", value="Mi API")
                .props("dense outlined")
                .classes("w-full")
            )
            desc_inp = (
                ui.input("Descripción (opcional)")
                .props("dense outlined")
                .classes("w-full")
            )

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancelar", on_click=dialog.close).props("flat dense no-caps")

                def crear():
                    c = Coleccion(
                        nombre=nom_inp.value.strip() or "Colección",
                        descripcion=desc_inp.value.strip(),
                    )
                    guardar_coleccion(c)
                    dialog.close()
                    recargar_vista_colecciones()
                    ui.notify("Colección creada", type="positive")

                ui.button("Crear", on_click=crear).props(
                    "dense unelevated color=primary no-caps"
                )
        dialog.open()

    def abrir_modal_codigo():
        dialog = ui.dialog()
        with dialog, ui.card().classes("w-full max-w-2xl max-h-[85vh] p-4 gap-3"):
            ui.label("Exportar código").classes("text-lg font-bold")
            pet = estado["peticion_actual"]
            ent = estado["entorno_activo"]

            with ui.tabs().classes("w-full").props("dense") as cod_tabs:
                tab_c = ui.tab("cURL")
                tab_py_h = ui.tab("Python httpx")
                tab_py_r = ui.tab("Python requests")
                tab_js = ui.tab("JavaScript fetch")

            with ui.tab_panels(cod_tabs, value=tab_c).classes("w-full p-2"):
                with ui.tab_panel(tab_c).classes("p-0 gap-2 column"):
                    c_txt = a_curl(pet, ent)
                    ui.code(c_txt, language="bash").classes(
                        "w-full text-xs font-mono max-h-64 overflow-y-auto"
                    )
                    ui.button(
                        "Copiar",
                        icon="content_copy",
                        on_click=lambda: ui.clipboard.write(c_txt)
                        or ui.notify("Copiado", type="positive"),
                    ).props("flat dense size=sm no-caps")

                with ui.tab_panel(tab_py_h).classes("p-0 gap-2 column"):
                    py_h_txt = a_python_httpx(pet, ent)
                    ui.code(py_h_txt, language="python").classes(
                        "w-full text-xs font-mono max-h-64 overflow-y-auto"
                    )
                    ui.button(
                        "Copiar",
                        icon="content_copy",
                        on_click=lambda: ui.clipboard.write(py_h_txt)
                        or ui.notify("Copiado", type="positive"),
                    ).props("flat dense size=sm no-caps")

                with ui.tab_panel(tab_py_r).classes("p-0 gap-2 column"):
                    py_r_txt = a_python_requests(pet, ent)
                    ui.code(py_r_txt, language="python").classes(
                        "w-full text-xs font-mono max-h-64 overflow-y-auto"
                    )
                    ui.button(
                        "Copiar",
                        icon="content_copy",
                        on_click=lambda: ui.clipboard.write(py_r_txt)
                        or ui.notify("Copiado", type="positive"),
                    ).props("flat dense size=sm no-caps")

                with ui.tab_panel(tab_js).classes("p-0 gap-2 column"):
                    js_txt = a_javascript_fetch(pet, ent)
                    ui.code(js_txt, language="javascript").classes(
                        "w-full text-xs font-mono max-h-64 overflow-y-auto"
                    )
                    ui.button(
                        "Copiar",
                        icon="content_copy",
                        on_click=lambda: ui.clipboard.write(js_txt)
                        or ui.notify("Copiado", type="positive"),
                    ).props("flat dense size=sm no-caps")

            with ui.row().classes("w-full justify-end mt-2"):
                ui.button("Cerrar", on_click=dialog.close).props("flat dense no-caps")
        dialog.open()

    def abrir_modal_descubrimiento():
        dialog = ui.dialog()
        with dialog, ui.card().classes(
            "w-full max-w-3xl max-h-[90vh] p-4 gap-3 flex flex-col"
        ):
            with ui.row().classes("w-full items-center justify-between border-b pb-2"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("radar").classes("text-2xl text-blue-600")
                    with ui.column().classes("gap-0"):
                        ui.label("Auto-Descubrimiento de APIs y Rutas").classes(
                            "text-base font-bold"
                        )
                        ui.label(
                            "Escanea un sitio web o API para detectar todos los endpoints y métodos (GET, POST, PUT, DELETE)"
                        ).classes("text-[11px] text-gray-400")
                ui.button(icon="close", on_click=dialog.close).props(
                    "flat dense round size=sm color=grey"
                )

            # Input de URL a escanear
            url_actual = (url_input.value or "").strip() or "http://localhost:8000"
            if "{{base_url}}" in url_actual and estado["entorno_activo"]:
                url_actual = interpolar(url_actual, estado["entorno_activo"])

            with ui.row().classes("w-full items-center gap-2"):
                inp_url_scan = (
                    ui.input(
                        "URL del sitio o API a escanear",
                        value=url_actual,
                        placeholder="https://ejemplo.com o http://localhost:8000",
                    )
                    .classes("flex-grow font-mono text-xs")
                    .props("dense outlined")
                )

                btn_iniciar_scan = ui.button(
                    "Escanear",
                    icon="search",
                ).props("unelevated dense color=primary no-caps px-4")

            with ui.row().classes(
                "w-full items-center gap-3 text-xs text-gray-500 -mt-1"
            ):
                chk_ssl_scan = ui.checkbox("Verificar SSL", value=True).props("dense")
                ui.label(
                    "• Detecta WordPress (/wp-json), OpenAPI/Swagger y sondeo REST"
                ).classes("text-[11px] text-gray-400")

            # Contenedor dinámico de resultados
            cont_resultados = ui.column().classes(
                "w-full flex-grow overflow-y-auto gap-2 min-h-[220px]"
            )

            # Estado inicial informativo
            with cont_resultados:
                with ui.column().classes("resp-empty py-8"):
                    ui.icon("travel_explore").classes("text-5xl text-gray-300")
                    ui.label(
                        "Ingresa la URL y haz clic en Escanear para descubrir rutas automáticamente."
                    ).classes("text-xs text-gray-400")

            # Contenedor inferior de acciones
            cont_acciones_pie = ui.row().classes(
                "w-full items-center justify-between border-t pt-2 mt-auto"
            )
            cont_acciones_pie.set_visibility(False)

            estado_scan = {"resultado": None}

            async def ejecutar_escaneo():
                u = inp_url_scan.value.strip()
                if not u:
                    ui.notify("Ingresa una URL válida", type="warning")
                    return

                btn_iniciar_scan.disable()
                btn_iniciar_scan.props("loading=true")
                cont_resultados.clear()
                cont_acciones_pie.set_visibility(False)

                with cont_resultados:
                    with ui.column().classes(
                        "w-full items-center justify-center py-10 gap-2"
                    ):
                        ui.spinner("dots", size="lg", color="primary")
                        ui.label(
                            "Escaneando sitio y analizando rutas, namespaces y métodos..."
                        ).classes("text-xs text-gray-500 font-mono")

                try:
                    res: ResultadoDescubrimiento = await descubrir_rutas_api(
                        url=u,
                        verificar_ssl=chk_ssl_scan.value,
                        timeout=15.0,
                    )
                    estado_scan["resultado"] = res
                    renderizar_resultados_scan(res)
                except Exception as err:
                    cont_resultados.clear()
                    with cont_resultados:
                        with ui.card().classes(
                            "w-full p-3 bg-red-50 border border-red-200 text-xs text-red-800"
                        ):
                            ui.label(f"Error durante el escaneo: {err}").classes(
                                "font-bold"
                            )
                finally:
                    btn_iniciar_scan.enable()
                    btn_iniciar_scan.props("loading=false")

            btn_iniciar_scan.on_click(ejecutar_escaneo)

            def renderizar_resultados_scan(res: ResultadoDescubrimiento):
                cont_resultados.clear()
                if not res.exito:
                    with cont_resultados:
                        with ui.card().classes(
                            "w-full p-4 bg-amber-50 border border-amber-200 text-xs text-amber-900 gap-1"
                        ):
                            ui.label("No se encontraron rutas de API públicas").classes(
                                "font-bold text-sm"
                            )
                            ui.label(
                                res.error
                                or "El sitio no expuso endpoints públicos detectables."
                            ).classes("text-xs")
                            ui.label(
                                "Consejo: Verifica si la URL requiere 'http://' o 'https://', o si la API está en un subdirectorio como /api o /wp-json."
                            ).classes("text-[11px] text-gray-500 mt-2")
                    return

                with cont_resultados:
                    # Resumen
                    with ui.card().classes(
                        "w-full p-3 bg-slate-50 border rounded-lg gap-2"
                    ):
                        with ui.row().classes(
                            "w-full items-center justify-between flex-wrap gap-2"
                        ):
                            with ui.row().classes("items-center gap-2"):
                                ui.badge(res.tipo_detectado, color="green").props(
                                    "dense"
                                )
                                ui.label(res.titulo_sitio or res.url_base).classes(
                                    "font-bold text-sm text-gray-800"
                                )
                            ui.label(f"{res.tiempo_ms} ms").classes(
                                "text-[11px] font-mono text-gray-400"
                            )

                        # Métricas en píldoras
                        with ui.row().classes(
                            "w-full items-center gap-2 flex-wrap text-xs"
                        ):
                            ui.html(
                                f'<span class="stat-pill stat-pill-ok">{res.total_rutas} rutas detectadas</span>'
                            )
                            for m, cant in res.total_metodos.items():
                                color_m = _BADGE_METODO.get(m, "grey")
                                ui.html(
                                    f'<span class="stat-pill stat-pill-neutral">{cant} {m}</span>'
                                )

                    # Filtros de búsqueda y método
                    with ui.row().classes(
                        "w-full items-center justify-between gap-2 mt-1"
                    ):
                        inp_filtro = (
                            ui.input(
                                placeholder="Filtrar por ruta o palabra clave...",
                            )
                            .props("dense outlined size=sm")
                            .classes("flex-grow text-xs")
                        )

                        sel_filtro_m = (
                            ui.select(
                                ["Todos"] + list(res.total_metodos.keys()),
                                value="Todos",
                            )
                            .props("dense outlined size=sm")
                            .classes("w-28 text-xs")
                        )

                    cont_lista_rutas = ui.column().classes(
                        "w-full max-h-64 overflow-y-auto gap-1 border p-1 rounded bg-white"
                    )

                    def refrescar_lista_filtrada():
                        cont_lista_rutas.clear()
                        txt = (inp_filtro.value or "").strip().lower()
                        filtro_m = sel_filtro_m.value

                        rutas_visibles = res.rutas
                        if filtro_m and filtro_m != "Todos":
                            rutas_visibles = [
                                r for r in rutas_visibles if r.metodo == filtro_m
                            ]
                        if txt:
                            rutas_visibles = [
                                r
                                for r in rutas_visibles
                                if txt in r.ruta.lower()
                                or txt in r.resumen.lower()
                                or txt in r.grupo.lower()
                            ]

                        with cont_lista_rutas:
                            if not rutas_visibles:
                                ui.label(
                                    "No hay rutas que coincidan con el filtro."
                                ).classes("text-xs text-gray-400 p-3")
                                return

                            for r in rutas_visibles:
                                color_b = _BADGE_METODO.get(r.metodo, "grey")
                                with ui.row().classes(
                                    "w-full items-center justify-between p-1.5 border-b hover:bg-slate-50 text-xs"
                                ):
                                    with ui.row().classes(
                                        "items-center gap-2 flex-grow truncate"
                                    ):
                                        ui.badge(r.metodo, color=color_b).props("dense")
                                        ui.label(r.ruta).classes(
                                            "font-mono font-bold text-gray-800"
                                        )
                                        if r.grupo and r.grupo != "General":
                                            ui.badge(r.grupo, color="grey").props(
                                                "dense outline"
                                            )
                                        if r.parametros:
                                            ui.label(
                                                f"params: {', '.join(r.parametros[:3])}"
                                            ).classes(
                                                "text-[10px] text-gray-400 truncate"
                                            )

                                    def probar_esta_ruta(ruta_obj=r):
                                        metodo_select.value = ruta_obj.metodo
                                        url_input.value = ruta_obj.url_completa
                                        actualizar_url_previsualizada()
                                        tabs.set_value(tab_peticion)
                                        dialog.close()
                                        ui.notify(
                                            f"Cargado en constructor: {ruta_obj.metodo} {ruta_obj.ruta}",
                                            type="positive",
                                        )

                                    ui.button(
                                        "Probar",
                                        icon="send",
                                        on_click=probar_esta_ruta,
                                    ).props("flat dense size=xs color=primary no-caps")

                    inp_filtro.on_value_change(lambda _: refrescar_lista_filtrada())
                    sel_filtro_m.on_value_change(lambda _: refrescar_lista_filtrada())
                    refrescar_lista_filtrada()

                # Mostrar pie de acciones
                cont_acciones_pie.clear()
                cont_acciones_pie.set_visibility(True)
                with cont_acciones_pie:
                    nom_defecto = (
                        f"API: {res.titulo_sitio or urlparse(res.url_base).netloc}"
                    )
                    inp_nombre_col = (
                        ui.input(
                            value=nom_defecto,
                            label="Nombre de la colección",
                        )
                        .props("dense outlined size=sm")
                        .classes("w-72 text-xs")
                    )

                    with ui.row().classes("gap-2"):
                        ui.button("Cerrar", on_click=dialog.close).props(
                            "flat dense no-caps size=sm"
                        )

                        def guardar_como_coleccion_completa():
                            nom = inp_nombre_col.value.strip() or nom_defecto
                            nueva_col = convertir_a_coleccion(res, nombre_coleccion=nom)
                            guardar_coleccion(nueva_col)
                            dialog.close()
                            recargar_vista_colecciones()
                            tabs.set_value(tab_colecciones)
                            ui.notify(
                                f"Colección '{nueva_col.nombre}' creada con {nueva_col.total_peticiones} peticiones",
                                type="positive",
                            )

                        ui.button(
                            "Importar como Colección",
                            icon="save",
                            on_click=guardar_como_coleccion_completa,
                        ).props("unelevated dense color=primary no-caps size=sm")

        dialog.open()

    # ----------------------------------------------------------------------- #
    # Renderizado de la Pestaña de Colecciones
    # ----------------------------------------------------------------------- #

    def recargar_vista_colecciones():
        cont_lista_colecciones.clear()
        cols = listar_colecciones_locales()

        with cont_lista_colecciones:
            if not cols:
                with ui.column().classes("resp-empty"):
                    ui.icon("folder_open").classes("text-5xl text-gray-300")
                    ui.label(
                        "Sin colecciones. Guarda o importa peticiones para empezar."
                    ).classes("text-sm text-gray-400")
                return

            for c_info in cols:
                col: Coleccion = c_info["coleccion"]
                with ui.expansion(
                    f"{col.nombre}  ({col.total_peticiones})",
                    icon="folder",
                ).classes("w-full bg-white border rounded-lg"):

                    with ui.row().classes(
                        "w-full justify-between items-center px-2 py-1.5 "
                        "bg-slate-50 rounded mb-2 flex-wrap gap-2"
                    ):
                        ui.label(col.descripcion or "Colección local").classes(
                            "text-xs text-gray-400 italic"
                        )
                        ui.button(
                            "Ejecutar todo",
                            icon="play_arrow",
                            on_click=lambda c=col: ejecutar_coleccion_ui(c),
                        ).props("dense unelevated color=primary size=sm no-caps")

                    for p in col.peticiones_raiz:
                        renderizar_item_peticion_coleccion(p)

                    for carp in col.carpetas:
                        with ui.expansion(
                            f"{carp.nombre} ({len(carp.peticiones)})",
                            icon="folder_open",
                        ).classes("w-full bg-white border rounded my-1"):
                            for p in carp.peticiones:
                                renderizar_item_peticion_coleccion(p)

    def renderizar_item_peticion_coleccion(p: Peticion):
        color_badge = _BADGE_METODO.get(p.metodo, "grey")

        with ui.row().classes(
            "w-full items-center justify-between p-2 border-b hover:bg-slate-50 "
            "text-xs cursor-pointer"
        ):
            with ui.row().classes("items-center gap-2 flex-grow truncate"):
                ui.badge(p.metodo, color=color_badge).props("dense")
                ui.label(p.nombre).classes("font-bold text-gray-800")
                ui.label(p.url).classes("font-mono text-gray-400 truncate max-w-[50%]")

            ui.button(
                icon="open_in_new",
                on_click=lambda pet=p: cargar_peticion_en_builder(pet),
            ).props("flat dense round size=sm color=primary").tooltip(
                "Cargar en el constructor"
            )

    async def ejecutar_coleccion_ui(col: Coleccion):
        dialog = ui.dialog()
        with dialog, ui.card().classes("w-full max-w-3xl max-h-[85vh] p-4 gap-3"):
            ui.label(f"Ejecutando: {col.nombre}").classes("text-lg font-bold")
            spinner = ui.spinner("dots", size="lg").classes("mx-auto my-4")
            cont_res_lote = ui.column().classes("w-full max-h-96 overflow-y-auto gap-2")

        dialog.open()

        from .motor import ejecutar_coleccion_lote

        resumen = await ejecutar_coleccion_lote(
            col, estado["entorno_activo"], verbose=False
        )
        spinner.set_visibility(False)

        with cont_res_lote:
            # Resumen en píldoras
            with ui.row().classes(
                "w-full gap-3 items-center p-3 bg-slate-50 rounded mb-2"
            ):
                ui.html(
                    f'<span class="stat-pill stat-pill-neutral">{resumen["total_peticiones"]} total</span>'
                )
                ui.html(
                    f'<span class="stat-pill stat-pill-ok">{resumen["exitosas"]} exitosas</span>'
                )
                if resumen["fallidas"] > 0:
                    ui.html(
                        f'<span class="stat-pill stat-pill-error">{resumen["fallidas"]} fallidas</span>'
                    )
                if resumen["total_assertions"] > 0:
                    css_a = (
                        "stat-pill-ok"
                        if resumen["assertions_pasadas"] == resumen["total_assertions"]
                        else "stat-pill-error"
                    )
                    ui.html(
                        f'<span class="stat-pill {css_a}">'
                        f'{resumen["assertions_pasadas"]}/{resumen["total_assertions"]} tests</span>'
                    )

            for r in resumen["resultados"]:
                ok = 200 <= r["status"] < 300
                with ui.row().classes(
                    f"w-full items-center justify-between p-2 rounded border text-xs "
                    f"{'bg-green-50 border-green-100' if ok else 'bg-red-50 border-red-100'}"
                ):
                    with ui.row().classes("items-center gap-2 truncate"):
                        ui.badge(
                            f"{r['status']}", color="green" if ok else "red"
                        ).props("dense")
                        ui.label(r["metodo"]).classes("font-bold")
                        ui.label(r["url"]).classes(
                            "font-mono text-gray-600 truncate max-w-[350px]"
                        )
                    with ui.row().classes("items-center gap-2"):
                        ui.label(f"{r['tiempo_ms']} ms").classes(
                            "font-mono text-gray-400"
                        )
                        if r["assertions_total"] > 0:
                            ok_a = r["assertions_pasadas"] == r["assertions_total"]
                            ui.badge(
                                f"{r['assertions_pasadas']}/{r['assertions_total']}",
                                color="green" if ok_a else "red",
                            ).props("dense")

    # ----------------------------------------------------------------------- #
    # Renderizado de Entornos y Variables
    # ----------------------------------------------------------------------- #

    def recargar_tabla_variables():
        cont_tabla_variables.clear()
        nom_sel = select_entorno_editor.value
        ent = next((x for x in estado["entornos"] if x.nombre == nom_sel), None)
        if not ent:
            return

        with cont_tabla_variables:
            # Encabezados de columna
            with ui.row().classes(
                "w-full items-center gap-2 px-1 text-[10px] text-gray-400 uppercase tracking-wide"
            ):
                ui.label("Clave").classes("w-48")
                ui.label("Valor").classes("flex-grow")
                ui.label("Secreto").classes("w-16")
                ui.label("").classes("w-8")

            for idx, v in enumerate(ent.variables):
                with ui.row().classes(
                    "w-full items-center gap-2 kv-row p-1 rounded"
                ) as fila:
                    k_inp = (
                        ui.input(
                            placeholder="base_url",
                            value=v.clave,
                        )
                        .classes("w-48")
                        .props("dense outlined")
                    )
                    val_inp = (
                        ui.input(
                            placeholder="https://api.ejemplo.com",
                            value=v.valor,
                        )
                        .classes("flex-grow font-mono")
                        .props(f"dense outlined {'type=password' if v.secreto else ''}")
                    )
                    chk_sec = ui.checkbox("", value=v.secreto).classes("w-16")

                    def guardar_cambio_var(
                        v_ref=v, k_i=k_inp, val_i=val_inp, sec_i=chk_sec
                    ):
                        v_ref.clave = k_i.value.strip()
                        v_ref.valor = val_i.value.strip()
                        v_ref.secreto = sec_i.value

                    k_inp.on_value_change(guardar_cambio_var)
                    val_inp.on_value_change(guardar_cambio_var)
                    chk_sec.on_value_change(guardar_cambio_var)

                    def borrar_var(i=idx):
                        ent.variables.pop(i)
                        recargar_tabla_variables()

                    ui.button(
                        icon="close",
                        on_click=borrar_var,
                    ).props("flat dense round size=xs color=grey")

            def agregar_var():
                ent.variables.append(Variable(clave="nueva_variable", valor=""))
                recargar_tabla_variables()

            ui.button(
                "Agregar variable",
                icon="add",
                on_click=agregar_var,
            ).props("flat dense size=sm no-caps color=primary")

    select_entorno_editor.on_value_change(lambda _: recargar_tabla_variables())

    def abrir_modal_nuevo_entorno():
        dialog = ui.dialog()
        with dialog, ui.card().classes("w-96 p-4 gap-3"):
            ui.label("Nuevo entorno").classes("text-base font-bold")
            nom_inp = (
                ui.input("Nombre", value="Staging")
                .props("dense outlined")
                .classes("w-full")
            )

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancelar", on_click=dialog.close).props("flat dense no-caps")

                def crear():
                    n = nom_inp.value.strip()
                    if not n:
                        return
                    nuevo = Entorno(
                        nombre=n,
                        variables=[
                            Variable(clave="base_url", valor="https://api.ejemplo.com")
                        ],
                    )
                    estado["entornos"].append(nuevo)
                    guardar_entornos(estado["entornos"])
                    dialog.close()

                    select_entorno_editor.options = [
                        e.nombre for e in estado["entornos"]
                    ]
                    select_entorno_editor.value = n
                    select_entorno.options = ["Sin entorno"] + [
                        e.nombre for e in estado["entornos"]
                    ]
                    recargar_tabla_variables()
                    ui.notify(f"Entorno '{n}' creado", type="positive")

                ui.button("Crear", on_click=crear).props(
                    "dense unelevated color=primary no-caps"
                )
        dialog.open()

    def guardar_todos_los_entornos():
        guardar_entornos(estado["entornos"])
        ui.notify("Entornos guardados", type="positive")

    # ----------------------------------------------------------------------- #
    # Renderizado del Historial
    # ----------------------------------------------------------------------- #

    def recargar_tabla_historial():
        cont_tabla_historial.clear()
        items = _leer_historial()

        with cont_tabla_historial:
            if not items:
                with ui.column().classes("resp-empty"):
                    ui.icon("history").classes("text-5xl text-gray-300")
                    ui.label(
                        "Sin historial. Las peticiones enviadas aparecerán aquí."
                    ).classes("text-sm text-gray-400")
                return

            for it in items[:60]:
                st = it.get("status", 0)
                metodo = it.get("metodo", "GET")
                color_st = (
                    "green"
                    if 200 <= st < 300
                    else ("orange" if 300 <= st < 400 else "red")
                )
                color_met = _BADGE_METODO.get(metodo, "grey")

                with ui.row().classes(
                    "w-full items-center justify-between p-2 border-b "
                    "hover:bg-slate-50 text-xs flex-wrap gap-1"
                ):
                    with ui.row().classes("items-center gap-2 flex-grow truncate"):
                        ui.badge(metodo, color=color_met).props("dense")
                        ui.badge(f"{st}", color=color_st).props("dense")
                        ui.label(it.get("url", "")).classes(
                            "font-mono text-gray-700 truncate max-w-[400px]"
                        )
                        ui.label(
                            f"{it.get('tiempo_ms', 0)} ms · {it.get('fecha', '')}"
                        ).classes("text-[10px] text-gray-400")

                    def restaurar(p_data=it.get("peticion", {})):
                        if p_data:
                            pet = Peticion.from_dict(p_data)
                            cargar_peticion_en_builder(pet)

                    ui.button(
                        icon="restore",
                        on_click=restaurar,
                    ).props(
                        "flat dense round size=sm color=primary"
                    ).tooltip("Restaurar petición")

    def vaciar_historial():
        if ARCHIVO_HISTORIAL.exists():
            ARCHIVO_HISTORIAL.unlink()
        recargar_tabla_historial()
        ui.notify("Historial vaciado", type="positive")

    # ----------------------------------------------------------------------- #
    # Inicialización
    # ----------------------------------------------------------------------- #
    recargar_vista_colecciones()
    recargar_tabla_variables()
    recargar_tabla_historial()
