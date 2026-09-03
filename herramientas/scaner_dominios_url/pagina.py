"""
pagina.py — Interfaz de escritorio del Consultor de Dominios (RDAP/WHOIS).
Diseño moderno tipo panel de control con tarjetas expandibles.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import signal
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from nicegui import app, ui, background_tasks
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak

from core.procesos import comando_worker
from herramientas.scaner_dominios_url.motor import limpiar_dominio_o_url

# --------------------------------------------------------------------------- #
# CONSTANTES
# --------------------------------------------------------------------------- #

ID_HERRAMIENTA = "consulta_dominios"
RUTA = "/scaner_dominios_url"
CARPETA = Path(tempfile.gettempdir()) / "consulta_dominios"
CARPETA.mkdir(exist_ok=True)

# --------------------------------------------------------------------------- #
# ESTADO
# --------------------------------------------------------------------------- #

class Estado:
    """Estado compartido de una consulta por lotes."""
    
    def __init__(self):
        self.proceso: asyncio.subprocess.Process | None = None
        self.filas: list[dict] = []
        self.vistos: set[str] = set()
        self.jsonl: Path | None = None
        self.corriendo = False
        self.inicio: datetime | None = None
        self.total_pedido = 0

E = Estado()


@app.on_shutdown
def _limpiar():
    """No dejar el subproceso huérfano si se cierra la aplicación."""
    if E.proceso and E.proceso.returncode is None:
        try:
            E.proceso.kill()
        except ProcessLookupError:
            pass


# --------------------------------------------------------------------------- #
# INTERFAZ PRINCIPAL
# --------------------------------------------------------------------------- #

@ui.page(RUTA)
def index():
    client = ui.context.client
    filtro_actual = "todas"
    
    # CSS personalizado
    ui.add_head_html("""
    <style>
        .fade-in { animation: fadeIn 0.3s ease-in-out; }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .result-card { transition: all 0.2s ease; border-left: 4px solid transparent; }
        .result-card:hover { transform: translateX(4px); box-shadow: 0 8px 25px rgba(0,0,0,0.1); }
        .result-card.status-ok { border-left-color: #10b981; }
        .result-card.status-error { border-left-color: #ef4444; }
        .result-card.status-free { border-left-color: #f59e0b; }
        .badge-pulse { animation: pulse 2s infinite; }
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.6; }
            100% { opacity: 1; }
        }
        .filter-btn-active { background: #e5e7eb !important; font-weight: 600 !important; }
    </style>
    """)

    with ui.column().classes("w-full max-w-7xl mx-auto p-4 gap-4"):
        
        # HEADER
        with ui.row().classes("w-full items-center justify-between p-4 bg-gradient-to-r from-blue-600 to-indigo-700 rounded-xl shadow-lg"):
            with ui.row().classes("items-center gap-4"):
                ui.icon("public", size="2.5rem").classes("text-white")
                with ui.column().classes("gap-0"):
                    ui.label("Domain Investigator").classes("text-2xl font-bold text-white tracking-wide")
                    ui.label("WHOIS & RDAP Query Tool").classes("text-sm text-blue-200")
            
            with ui.row().classes("gap-2"):
                ui.button(icon="home", on_click=lambda: ui.navigate.to("/")).props("flat round dense").classes("text-white hover:bg-white/20")

        # PANEL DE CONTROL
        with ui.card().classes("w-full mt-2 shadow-md").style("background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%)"):
            with ui.row().classes("w-full gap-4 flex-wrap items-end"):
                with ui.column().classes("flex-1 min-w-[300px]"):
                    ui.label("🔍 Dominios o URLs").classes("text-sm font-semibold text-gray-700")
                    dominios_input = ui.textarea(
                        "",
                        placeholder="ejemplo.com\nhttps://otro-sitio.org\nmi-empresa.pe"
                    ).classes("w-full").props("outlined dense rows=3").mark("dominios_input")
                
                with ui.column().classes("gap-2"):
                    ui.label(" ").classes("text-sm")
                    with ui.row().classes("gap-2 flex-wrap"):
                        boton_iniciar = ui.button("Consultar", icon="search", color="primary").props("push").classes("px-6").mark("boton_iniciar")
                        boton_detener = ui.button("Detener", icon="stop", color="negative").props("push").classes("px-6").mark("boton_detener").disable()
                        ui.button(icon="tune", on_click=lambda: modal_opciones.open()).props("flat round dense color=grey-7").tooltip("Opciones avanzadas")

            # Modal opciones
            with ui.dialog() as modal_opciones:
                with ui.card().classes("w-[500px] p-6"):
                    ui.label("⚙️ Opciones Avanzadas").classes("text-xl font-bold mb-4")
                    with ui.row().classes("w-full gap-4"):
                        concurrencia = ui.number("Concurrencia", value=4, min=1, format="%d").props("outlined dense").classes("w-32")
                        delay = ui.number("Delay (s)", value=0.3, min=0, step=0.1, format="%.1f").props("outlined dense").classes("w-40")
                    forzar_whois = ui.checkbox("Forzar WHOIS clásico (saltar RDAP)")
                    with ui.row().classes("w-full justify-end gap-2 mt-4"):
                        ui.button("Cerrar", on_click=modal_opciones.close).props("flat")

        # ESTADÍSTICAS
        with ui.card().classes("w-full shadow-sm"):
            with ui.row().classes("w-full items-center justify-between flex-wrap gap-2 p-2"):
                with ui.row().classes("items-center gap-4 flex-wrap"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("circle", size="0.75rem").classes("text-green-500")
                        lbl_estado = ui.label("Listo").classes("text-sm font-medium")
                    ui.separator().props("vertical").classes("h-8")
                    with ui.row().classes("items-center gap-3 flex-wrap"):
                        with ui.column().classes("items-center gap-0"):
                            lbl_ok = ui.label("0").classes("text-lg font-bold text-green-600")
                            ui.label("Encontrados").classes("text-xs text-gray-500")
                        with ui.column().classes("items-center gap-0"):
                            lbl_errores = ui.label("0").classes("text-lg font-bold text-red-600")
                            ui.label("Errores").classes("text-xs text-gray-500")
                        with ui.column().classes("items-center gap-0"):
                            lbl_tiempo = ui.label("0s").classes("text-lg font-bold text-gray-700")
                            ui.label("Tiempo").classes("text-xs text-gray-500")
                
                with ui.column().classes("flex-1 min-w-[200px]"):
                    barra = ui.linear_progress(value=0, show_value=False).props("indeterminate=false")
                    barra.set_visibility(False)

        # ÁREA DE RESULTADOS
        with ui.card().classes("w-full shadow-sm p-4"):
            with ui.row().classes("w-full items-center gap-3 mb-3 flex-wrap"):
                filtro = ui.input(placeholder="🔍 Filtrar dominios...").props("outlined dense clearable").classes("flex-1 min-w-[200px]")
                
                with ui.row().classes("gap-1"):
                    btn_ok = ui.button("✓ OK", color="positive").props("flat dense").classes("text-xs filter-btn")
                    btn_error = ui.button("✗ ERROR", color="negative").props("flat dense").classes("text-xs filter-btn")
                    btn_todas = ui.button("📋 Todas", color="primary").props("flat dense").classes("text-xs filter-btn filter-btn-active")
                
                ui.space()
                
                with ui.row().classes("gap-1"):
                    boton_json = ui.button(icon="download", text="JSON").props("outline dense").classes("text-xs")
                    boton_pdf = ui.button(icon="picture_as_pdf", text="PDF").props("outline dense").classes("text-xs")
            
            resultados_container = ui.column().classes("w-full gap-3 mt-2 min-h-[200px]")

        # LOG TÉCNICO
        with ui.expansion("📟 Registro Técnico", icon="terminal").classes("w-full"):
            log_area = ui.log(max_lines=300).classes("w-full h-48 font-mono text-xs bg-gray-900 text-green-400 p-2 rounded")

    # ----------------------------------------------------------------------- #
    # DIÁLOGO DE DETALLE
    # ----------------------------------------------------------------------- #

    with ui.dialog().props("maximized-on-mobile") as dialog:
        dialog_card = ui.card().classes("w-full max-w-5xl p-6 max-h-[92vh] overflow-hidden flex flex-col")

    def mostrar_detalle(row):
        if not row:
            return
        dialog_card.clear()
        with dialog_card:
            # Header
            with ui.row().classes("w-full items-center justify-between pb-4 border-b-2 border-blue-200"):
                with ui.row().classes("items-center gap-3 flex-wrap"):
                    ui.icon("language", size="md").classes("text-blue-600")
                    ui.label(row.get("dominio") or "Dominio").classes("text-2xl font-bold")
                    st = row.get("status")
                    color = "green" if st == "ok" else ("orange" if st == "no_encontrado" else "red")
                    label = "OK" if st == "ok" else ("LIBRE" if st == "no_encontrado" else "ERROR")
                    ui.badge(label).props(f"color={color}")
                    if row.get("fecha_expiracion"):
                        with ui.row().classes("items-center gap-1 ml-2"):
                            ui.icon("schedule", size="sm").classes("text-gray-500")
                            ui.label(f"Expira: {row.get('fecha_expiracion')}").classes("text-sm text-gray-600")
                
                with ui.row().classes("items-center gap-2"):
                    dominio = row.get("dominio")
                    if dominio:
                        ui.button(icon="open_in_new", on_click=lambda: ui.run_javascript(f"window.open('http://{dominio}', '_blank')")).props("flat round dense").tooltip("Abrir en navegador")
                    ui.button(icon="close", on_click=dialog.close).props("flat round dense")

            # Cuerpo con scroll
            with ui.column().classes("w-full overflow-y-auto pr-2 gap-3 flex-grow my-3"):
                # Secciones de detalle
                _render_seccion_detalle(row, "🌐 INFORMACIÓN DEL DOMINIO", [
                    ("Domain Name", "dominio"),
                    ("Registry Domain ID", "registry_domain_id", "handle"),
                    ("Punycode", "punycode"),
                    ("URL Original", "url_original"),
                ], "bg-blue-50 border-blue-200")
                
                _render_seccion_detalle(row, "👤 PROPIETARIO", [
                    ("Nombre", "registrant_name"),
                    ("Organización", "registrant_org"),
                    ("País", "registrant_country"),
                    ("Email", "registrant_email"),
                    ("Teléfono", "registrant_phone"),
                ], "bg-green-50 border-green-200")
                
                _render_seccion_detalle(row, "🔧 ADMINISTRACIÓN", [
                    ("Nombre", "admin_name"),
                    ("Email", "admin_email"),
                    ("Teléfono", "admin_phone"),
                ], "bg-purple-50 border-purple-200")
                
                _render_seccion_detalle(row, "💻 TÉCNICO", [
                    ("Nombre", "tech_name"),
                    ("Email", "tech_email"),
                    ("Teléfono", "tech_phone"),
                ], "bg-cyan-50 border-cyan-200")
                
                _render_seccion_detalle(row, "🏢 REGISTRADOR", [
                    ("Registrador", "registrador"),
                    ("IANA ID", "registrar_iana_id"),
                    ("URL", "registrar_url"),
                    ("Abuse Email", "abuse_email"),
                    ("Abuse Phone", "abuse_tel"),
                ], "bg-amber-50 border-amber-200")
                
                _render_seccion_detalle(row, "📅 FECHAS", [
                    ("Creación", "fecha_registro"),
                    ("Actualización", "fecha_actualizacion"),
                    ("Expiración", "fecha_expiracion"),
                    ("Última actualización BD", "last_update"),
                ], "bg-rose-50 border-rose-200")
                
                # DNS y estados
                with ui.card().classes("w-full bg-indigo-50 p-4 border border-indigo-200"):
                    ui.label("🔐 DNS & SEGURIDAD").classes("text-xs font-bold text-indigo-700 uppercase tracking-wider mb-2")
                    with ui.row().classes("w-full gap-6 items-start"):
                        with ui.column().classes("w-36 gap-0 text-sm"):
                            ui.label("DNSSEC").classes("text-xs text-gray-500")
                            d_sec = row.get("dnssec") or "Unsigned"
                            color_dns = "green" if "signed" in d_sec.lower() else "red" if "unsigned" in d_sec.lower() else "grey"
                            ui.badge(d_sec).props(f"color={color_dns}").classes("font-mono")
                        with ui.column().classes("flex-1 gap-1 text-sm"):
                            ui.label("Name Servers").classes("text-xs text-gray-500")
                            ns_list = row.get("nameservers") or []
                            if ns_list:
                                with ui.row().classes("gap-1 flex-wrap"):
                                    for ns in ns_list:
                                        ui.badge(ns).props("outline color=primary").classes("font-mono text-xs")
                            else:
                                ui.label("No hay servidores DNS configurados").classes("text-sm text-gray-500")

                estados = row.get("estado_dominio") or []
                if estados:
                    with ui.card().classes("w-full bg-gray-50 p-4 border border-gray-200"):
                        ui.label("📊 ESTADO DEL DOMINIO").classes("text-xs font-bold text-gray-700 uppercase tracking-wider mb-2")
                        with ui.row().classes("gap-2 flex-wrap"):
                            for est in estados:
                                color_est = "green" if est.lower() == "ok" else "orange" if "prohibited" in est.lower() else "blue-grey"
                                ui.badge(est).props(f"color={color_est}").classes("font-mono text-xs")

                # JSON crudo
                with ui.expansion("📦 Datos Crudos (JSON)", icon="code").classes("w-full"):
                    ui.code(json.dumps(row, ensure_ascii=False, indent=2), language="json").classes("w-full max-h-96 overflow-auto")
            
            # Pie
            with ui.row().classes("w-full items-center justify-between pt-4 border-t-2 border-gray-200"):
                with ui.row().classes("gap-2 flex-wrap"):
                    ui.button(
                        "Copiar JSON",
                        icon="content_copy",
                        on_click=lambda: ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(json.dumps(row, ensure_ascii=False, indent=2))})").then(lambda: ui.notify("Ficha copiada", type="positive")),
                    ).props("flat dense")
                    dominio = row.get("dominio")
                    if dominio:
                        ui.button("Abrir en navegador", icon="open_in_new", on_click=lambda: ui.run_javascript(f"window.open('http://{dominio}', '_blank')")).props("flat dense")
                ui.button("Cerrar", on_click=dialog.close).props("color=primary")

        dialog.open()

    def _render_seccion_detalle(row, titulo, campos, clase):
        """Renderiza una sección de detalle."""
        with ui.card().classes(f"w-full {clase} p-4 border"):
            ui.label(titulo).classes("text-xs font-bold uppercase tracking-wider mb-2")
            with ui.grid(columns=4).classes("w-full gap-3 text-sm"):
                for campo_info in campos:
                    if len(campo_info) == 2:
                        label, key = campo_info
                        valor = row.get(key) or "—"
                        with ui.column().classes("gap-0"):
                            ui.label(label).classes("text-xs text-gray-500")
                            if key.endswith("email") and valor != "—" and "@" in valor:
                                ui.link(valor, f"mailto:{valor}").classes("text-blue-600 hover:underline break-all")
                            else:
                                ui.label(valor).classes("break-all")
                    else:
                        # Múltiples claves para un mismo campo
                        label, *keys = campo_info
                        for key in keys:
                            valor = row.get(key)
                            if valor:
                                with ui.column().classes("gap-0"):
                                    ui.label(label).classes("text-xs text-gray-500")
                                    ui.label(valor).classes("break-all")
                                break

    # ----------------------------------------------------------------------- #
    # FUNCIONES DE RENDERIZADO
    # ----------------------------------------------------------------------- #

    def actualizar_botones_filtro():
        btn_ok.classes(remove="filter-btn-active" if filtro_actual != "ok" else None, add="filter-btn-active" if filtro_actual == "ok" else None)
        btn_error.classes(remove="filter-btn-active" if filtro_actual != "error" else None, add="filter-btn-active" if filtro_actual == "error" else None)
        btn_todas.classes(remove="filter-btn-active" if filtro_actual != "todas" else None, add="filter-btn-active" if filtro_actual == "todas" else None)

    def renderizar_resultados():
        resultados_container.clear()
        
        filas = E.filas
        
        if filtro_actual == "ok":
            filas = [f for f in filas if f["status"] == "ok"]
        elif filtro_actual == "error":
            filas = [f for f in filas if f["status"] != "ok"]
        
        texto = (filtro.value or "").lower().strip()
        if texto:
            filas = [
                f for f in filas
                if any(texto in str(f.get(k, "")).lower() for k in ["dominio", "punycode", "registrador", "registrant_org", "registrant_name"])
            ]
        
        if not filas:
            with resultados_container:
                with ui.card().classes("w-full p-12 bg-gray-50").style("border: 2px dashed #d1d5db"):
                    with ui.column().classes("items-center gap-3"):
                        ui.icon("search_off", size="4rem").classes("text-gray-400")
                        ui.label("No se encontraron resultados").classes("text-xl font-medium text-gray-500")
                        ui.label("Realiza una consulta o ajusta los filtros").classes("text-sm text-gray-400")
                    return
        
        for row in filas:
            _render_tarjeta_resultado(row)

    def _render_tarjeta_resultado(row):
        """Renderiza una tarjeta de resultado."""
        with resultados_container:
            with ui.card().classes(f"w-full p-4 result-card fade-in status-{row.get('status', 'error')}").style("border-radius: 12px"):
                # Header
                with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
                    with ui.row().classes("items-center gap-3 flex-wrap"):
                        st = row.get("status")
                        if st == "ok":
                            ui.badge("✓ ACTIVO", color="positive").props("outline").classes("text-xs")
                        elif st == "no_encontrado":
                            ui.badge("◯ LIBRE", color="warning").props("outline").classes("text-xs")
                        else:
                            ui.badge("✗ ERROR", color="negative").props("outline").classes("text-xs")
                        
                        dominio = row.get("dominio") or "—"
                        ui.label(dominio).classes("text-xl font-bold text-blue-700 font-mono")
                        if row.get("punycode") and row.get("punycode") != dominio:
                            ui.label(f"({row.get('punycode')})").classes("text-xs text-gray-400 font-mono")
                    
                    with ui.row().classes("gap-1"):
                        ui.button(icon="visibility", on_click=lambda r=row: mostrar_detalle(r)).props("flat round dense color=primary").tooltip("Detalles completos")
                        if dominio and dominio != "—":
                            ui.button(icon="open_in_new", on_click=lambda d=dominio: ui.run_javascript(f"window.open('http://{d}', '_blank')")).props("flat round dense").tooltip("Abrir en navegador")
                        ui.button(icon="content_copy", on_click=lambda d=dominio: ui.run_javascript(f"navigator.clipboard.writeText('{d}')").then(lambda: ui.notify("Dominio copiado", type="positive"))).props("flat round dense").tooltip("Copiar dominio")
                
                # Información principal
                with ui.grid(columns=3).classes("w-full gap-4 mt-3"):
                    with ui.column().classes("gap-0"):
                        ui.label("REGISTRADOR").classes("text-xs font-bold text-gray-400 uppercase tracking-wider")
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("business", size="1rem").classes("text-gray-400")
                            ui.label(row.get("registrador") or "—").classes("text-sm font-medium")
                        if row.get("registrar_iana_id"):
                            ui.label(f"ID: {row.get('registrar_iana_id')}").classes("text-xs text-gray-400 ml-6")
                    
                    with ui.column().classes("gap-0"):
                        ui.label("ORGANIZACIÓN").classes("text-xs font-bold text-gray-400 uppercase tracking-wider")
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("people", size="1rem").classes("text-gray-400")
                            ui.label(row.get("registrant_org") or "—").classes("text-sm font-medium")
                        if row.get("registrant_name"):
                            ui.label(f"Contacto: {row.get('registrant_name')}").classes("text-xs text-gray-400 ml-6")
                    
                    with ui.column().classes("gap-0"):
                        ui.label("FECHAS CLAVE").classes("text-xs font-bold text-gray-400 uppercase tracking-wider")
                        if row.get("fecha_registro"):
                            with ui.row().classes("items-center gap-1"):
                                ui.icon("event", size="0.9rem").classes("text-gray-400")
                                ui.label(f"Creación: {row.get('fecha_registro')}").classes("text-xs")
                        if row.get("fecha_expiracion"):
                            with ui.row().classes("items-center gap-1"):
                                ui.icon("warning", size="0.9rem").classes("text-orange-400")
                                ui.label(f"Expira: {row.get('fecha_expiracion')}").classes("text-xs font-bold text-orange-600")
                
                # Información secundaria
                with ui.row().classes("w-full gap-4 mt-2 pt-2 border-t border-gray-100 flex-wrap"):
                    dnssec = row.get("dnssec") or "Unsigned"
                    color_dns = "green" if "signed" in dnssec.lower() else "red" if "unsigned" in dnssec.lower() else "grey"
                    with ui.row().classes("items-center gap-1"):
                        ui.label("🔐").classes("text-sm")
                        ui.badge(dnssec).props(f"color={color_dns}").classes("text-xs")
                    
                    with ui.row().classes("items-center gap-1"):
                        ui.label("📡").classes("text-sm")
                        ui.badge(row.get("fuente") or "—").props("color=grey-7").classes("text-xs")
                    
                    ns_list = row.get("nameservers") or []
                    if ns_list:
                        with ui.row().classes("items-center gap-1 flex-wrap"):
                            ui.label("🌐").classes("text-sm")
                            for ns in ns_list[:2]:
                                ui.badge(ns).props("outline color=primary").classes("text-xs font-mono")
                            if len(ns_list) > 2:
                                ui.badge(f"+{len(ns_list)-2}").props("color=grey-7").classes("text-xs")
                
                estados = row.get("estado_dominio") or []
                if estados:
                    with ui.row().classes("gap-1 mt-1 flex-wrap"):
                        ui.label("📊").classes("text-sm")
                        for est in estados:
                            color_est = "green" if est.lower() == "ok" else "orange" if "prohibited" in est.lower() else "blue-grey"
                            ui.badge(est).props(f"color={color_est}").classes("text-xs font-mono")

    # ----------------------------------------------------------------------- #
    # LÓGICA DE FILTROS
    # ----------------------------------------------------------------------- #

    def set_filtro(tipo: str):
        nonlocal filtro_actual
        filtro_actual = tipo
        actualizar_botones_filtro()
        renderizar_resultados()

    btn_ok.on_click(lambda: set_filtro("ok"))
    btn_error.on_click(lambda: set_filtro("error"))
    btn_todas.on_click(lambda: set_filtro("todas"))
    filtro.on_value_change(lambda _: renderizar_resultados())

    actualizar_botones_filtro()
    renderizar_resultados()

    # ----------------------------------------------------------------------- #
    # LÓGICA DE PROCESO
    # ----------------------------------------------------------------------- #

    def leer_nuevas_lineas() -> int:
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
                    continue

                if d["dominio"] in E.vistos:
                    continue

                E.vistos.add(d["dominio"])
                E.filas.append(d)
                nuevas += 1

        return nuevas

    async def vigilar():
        while E.corriendo:
            nuevas = leer_nuevas_lineas()

            with client:
                if nuevas:
                    renderizar_resultados()

                ok = sum(1 for f in E.filas if f["status"] == "ok")
                errores = sum(1 for f in E.filas if f["status"] != "ok")
                lbl_ok.text = str(ok)
                lbl_errores.text = str(errores)

                if E.inicio:
                    seg = int((datetime.now() - E.inicio).total_seconds())
                    lbl_tiempo.text = f"{seg // 60}m {seg % 60}s" if seg >= 60 else f"{seg}s"

                if E.total_pedido > 0:
                    barra.value = min(len(E.filas) / E.total_pedido, 1.0)

            await asyncio.sleep(0.4)

    async def leer_stderr():
        if not E.proceso or not E.proceso.stderr:
            return

        async for raw in E.proceso.stderr:
            linea = raw.decode("utf-8", "ignore").rstrip()
            if linea:
                with client:
                    log_area.push(linea)

    async def iniciar():
        texto = (dominios_input.value or "").strip()
        if not texto:
            ui.notify("Escribe al menos un dominio o URL", type="warning")
            return

        dominios = []
        for line in texto.splitlines():
            line = line.strip()
            if not line:
                continue
            dom, _ = limpiar_dominio_o_url(line)
            if dom:
                dominios.append(dom)

        if not dominios:
            ui.notify("No se encontraron dominios o URLs válidos", type="warning")
            return

        # Reset
        E.filas.clear()
        E.vistos.clear()
        E.total_pedido = len(dominios)
        set_filtro("todas")
        renderizar_resultados()
        log_area.clear()

        # Archivos temporales
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = CARPETA / f"lote_{marca}"

        entrada = Path(f"{base}_dominios.txt")
        entrada.write_text("\n".join(dominios), encoding="utf-8")
        E.jsonl = Path(f"{base}.jsonl")

        # Comando
        cmd = [
            *comando_worker(ID_HERRAMIENTA),
            "--batch", str(entrada),
            "-o", str(base),
            "--concurrencia", str(int(concurrencia.value or 4)),
            "--delay", str(float(delay.value or 0.3)),
        ]
        if forzar_whois.value:
            cmd.append("--whois")

        log_area.push("$ " + " ".join(cmd))

        # Subproceso
        E.proceso = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name != "nt"),
        )

        E.corriendo = True
        E.inicio = datetime.now()

        boton_iniciar.disable()
        boton_detener.enable()
        barra.set_visibility(True)
        barra.value = 0
        lbl_estado.text = "Consultando..."
        lbl_estado.classes(replace="text-sm font-medium text-blue-600")

        background_tasks.create(vigilar())
        background_tasks.create(leer_stderr())
        background_tasks.create(esperar_fin())

    async def esperar_fin():
        proceso = E.proceso
        if proceso is None:
            return

        await proceso.wait()
        await asyncio.sleep(0.3)

        E.corriendo = False
        leer_nuevas_lineas()

        with client:
            renderizar_resultados()

            boton_iniciar.enable()
            boton_detener.disable()

            if E.total_pedido > 0:
                barra.value = min(len(E.filas) / E.total_pedido, 1.0)
            elif proceso.returncode == 0:
                barra.value = 1.0

            if proceso.returncode == 0:
                lbl_estado.text = "Completado"
                lbl_estado.classes(replace="text-sm font-medium text-green-600")
                ui.notify(f"Listo: {len(E.filas)} consultas finalizadas", type="positive")
            else:
                lbl_estado.text = "Detenido"
                lbl_estado.classes(replace="text-sm font-medium text-orange-600")
                ui.notify(f"Detenido con {len(E.filas)} dominios consultados", type="warning")

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

        señal(signal.SIGINT)

        try:
            await asyncio.wait_for(E.proceso.wait(), timeout=5)
        except asyncio.TimeoutError:
            log_area.push("Cierre ordenado lento, forzando kill...")
            E.proceso.kill()
            await E.proceso.wait()

    def descargar_json():
        if not E.filas:
            ui.notify("No hay resultados todavía", type="warning")
            return

        contenido = json.dumps(E.filas, ensure_ascii=False, indent=2)
        ui.download(contenido.encode("utf-8"), "dominios.json")

    # ----------------------------------------------------------------------- #
    # GENERACIÓN DE PDF
    # ----------------------------------------------------------------------- #

    def descargar_pdf():
        """Genera un PDF profesional con toda la información disponible."""
        if not E.filas:
            ui.notify("No hay resultados todavía", type="warning")
            return

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            leftMargin=1.7 * cm,
            rightMargin=1.7 * cm,
            topMargin=1.7 * cm,
            bottomMargin=1.7 * cm,
            title="Domain Investigator - Reporte WHOIS / RDAP",
            author="Domain Investigator",
        )

        estilos = getSampleStyleSheet()
        historia = []
        generado = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        total = len(E.filas)
        ok_count = sum(1 for f in E.filas if f.get("status") == "ok")
        libres_count = sum(1 for f in E.filas if f.get("status") == "no_encontrado")
        error_count = sum(1 for f in E.filas if f.get("status") not in ("ok", "no_encontrado"))

        # Estilos
        titulo = estilos["Title"]
        titulo.fontSize = 20
        titulo.leading = 24
        titulo.alignment = 1
        titulo.textColor = colors.HexColor("#1e293b")
        titulo.spaceAfter = 5

        subtitulo = estilos["Normal"]
        subtitulo.fontSize = 10
        subtitulo.leading = 13
        subtitulo.alignment = 1
        subtitulo.textColor = colors.HexColor("#64748b")
        subtitulo.spaceAfter = 15

        encabezado = estilos["Heading2"]
        encabezado.fontSize = 11
        encabezado.leading = 14
        encabezado.textColor = colors.HexColor("#1e40af")
        encabezado.spaceBefore = 8
        encabezado.spaceAfter = 7

        campo = estilos["Normal"]
        campo.fontSize = 8.5
        campo.leading = 11
        campo.textColor = colors.HexColor("#334155")

        valor = estilos["Normal"]
        valor.fontSize = 8.5
        valor.leading = 11
        valor.textColor = colors.HexColor("#0f172a")

        valor_mono = estilos["Code"]
        valor_mono.fontSize = 7.5
        valor_mono.leading = 10
        valor_mono.textColor = colors.HexColor("#0f172a")

        def formatear_valor(valor_original):
            if valor_original is None:
                return "—"
            if isinstance(valor_original, bool):
                return "Sí" if valor_original else "No"
            if isinstance(valor_original, list):
                if not valor_original:
                    return "—"
                elementos = []
                for item in valor_original:
                    if isinstance(item, dict):
                        elementos.append(json.dumps(item, ensure_ascii=False, indent=2))
                    else:
                        elementos.append(str(item))
                return "<br/>".join(f"• {escape(str(item))}" for item in elementos)
            if isinstance(valor_original, dict):
                partes = []
                for clave, valor_dict in valor_original.items():
                    if isinstance(valor_dict, (dict, list)):
                        partes.append(f"<b>{escape(clave)}:</b> {json.dumps(valor_dict, ensure_ascii=False)}")
                    else:
                        partes.append(f"<b>{escape(clave)}:</b> {escape(str(valor_dict))}")
                return "<br/>".join(partes)
            return escape(str(valor_original))

        def crear_tabla_campos(campos_lista, color_fondo="#f8fafc"):
            datos = [[
                Paragraph(f"<b>{escape(nombre)}</b>", campo),
                Paragraph(formatear_valor(valor_original), valor)
            ] for nombre, valor_original in campos_lista if valor_original is not None 
              and not (isinstance(valor_original, str) and not valor_original.strip()) 
              and not (isinstance(valor_original, list) and not valor_original)]

            if not datos:
                return None

            tabla = Table(datos, colWidths=[5.0 * cm, 12.0 * cm], repeatRows=0, hAlign="LEFT")
            tabla.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
                ("BACKGROUND", (1, 0), (1, -1), colors.HexColor(color_fondo)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            return tabla

        def agregar_seccion(titulo_seccion, campos_lista):
            campos_validos = []
            for nombre, valor_original in campos_lista:
                if valor_original is None:
                    continue
                if isinstance(valor_original, str) and not valor_original.strip():
                    continue
                if isinstance(valor_original, list) and not valor_original:
                    continue
                campos_validos.append((nombre, valor_original))

            if not campos_validos:
                return

            historia.append(Paragraph(titulo_seccion, encabezado))
            tabla = crear_tabla_campos(campos_validos)
            if tabla:
                historia.append(tabla)
            historia.append(Spacer(1, 0.25 * cm))

        def pie_pagina(canvas, doc):
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
            canvas.line(1.7 * cm, 1.1 * cm, letter[0] - 1.7 * cm, 1.1 * cm)
            canvas.setFont("Helvetica", 7)
            canvas.setFillColor(colors.HexColor("#64748b"))
            canvas.drawString(1.7 * cm, 0.7 * cm, "Domain Investigator • WHOIS / RDAP")
            canvas.drawRightString(letter[0] - 1.7 * cm, 0.7 * cm, f"Página {doc.page}")
            canvas.restoreState()

        # Portada
        historia.append(Spacer(1, 0.5 * cm))
        historia.append(Paragraph("DOMAIN INVESTIGATOR", titulo))
        historia.append(Paragraph("Reporte completo de consulta WHOIS / RDAP", subtitulo))

        resumen = Table([[
            Paragraph(f"<b>{total}</b><br/>Dominios", valor),
            Paragraph(f"<b>{ok_count}</b><br/>Activos", valor),
            Paragraph(f"<b>{libres_count}</b><br/>Disponibles", valor),
            Paragraph(f"<b>{error_count}</b><br/>Errores", valor),
            Paragraph(f"<b>{escape(generado)}</b><br/>Generado", valor),
        ]], colWidths=[3.0 * cm, 3.0 * cm, 3.0 * cm, 3.0 * cm, 5.0 * cm])

        resumen.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]))
        historia.append(resumen)
        historia.append(Spacer(1, 0.8 * cm))

        # Cada dominio
        campos_conocidos = {
            "dominio", "registry_domain_id", "handle", "punycode", "url_original",
            "status", "fuente", "registrant_name", "registrant_org", "registrant_country",
            "registrant_email", "registrant_phone", "registrant_street", "registrant_city",
            "registrant_state", "registrant_postal", "admin_name", "admin_email",
            "admin_phone", "admin_org", "admin_country", "admin_street", "admin_city",
            "tech_name", "tech_email", "tech_phone", "tech_org", "tech_country",
            "tech_street", "registrador", "registrar_iana_id", "registrar_url",
            "abuse_email", "abuse_tel", "fecha_registro", "fecha_actualizacion",
            "fecha_expiracion", "last_update", "dnssec", "nameservers", "estado_dominio"
        }

        for indice, row in enumerate(E.filas, start=1):
            if indice > 1:
                historia.append(PageBreak())

            dominio = row.get("dominio") or "Dominio no identificado"
            status = row.get("status") or "—"

            if status == "ok":
                estado_texto = "✓ ACTIVO"
                estado_color = "#059669"
            elif status == "no_encontrado":
                estado_texto = "◯ DISPONIBLE"
                estado_color = "#d97706"
            else:
                estado_texto = "✗ ERROR"
                estado_color = "#dc2626"

            historia.append(Paragraph(f"<font size='17'><b>{escape(dominio)}</b></font>", titulo))
            historia.append(Paragraph(f"<font color='{estado_color}' size='10'><b>{estado_texto}</b></font>", subtitulo))

            # Secciones
            agregar_seccion("1. INFORMACIÓN DEL DOMINIO", [
                ("Domain Name", row.get("dominio")),
                ("Registry Domain ID", row.get("registry_domain_id") or row.get("handle")),
                ("Punycode", row.get("punycode")),
                ("URL Original", row.get("url_original")),
                ("Estado de consulta", status),
                ("Fuente", row.get("fuente")),
            ])

            agregar_seccion("2. PROPIETARIO / REGISTRANTE", [
                ("Nombre", row.get("registrant_name")),
                ("Organización", row.get("registrant_org")),
                ("País", row.get("registrant_country")),
                ("Email", row.get("registrant_email")),
                ("Teléfono", row.get("registrant_phone")),
                ("Dirección", row.get("registrant_street")),
                ("Ciudad", row.get("registrant_city")),
                ("Estado / Región", row.get("registrant_state")),
                ("Código Postal", row.get("registrant_postal")),
            ])

            agregar_seccion("3. CONTACTO ADMINISTRATIVO", [
                ("Nombre", row.get("admin_name")),
                ("Email", row.get("admin_email")),
                ("Teléfono", row.get("admin_phone")),
                ("Organización", row.get("admin_org")),
                ("País", row.get("admin_country")),
                ("Dirección", row.get("admin_street")),
                ("Ciudad", row.get("admin_city")),
            ])

            agregar_seccion("4. CONTACTO TÉCNICO", [
                ("Nombre", row.get("tech_name")),
                ("Email", row.get("tech_email")),
                ("Teléfono", row.get("tech_phone")),
                ("Organización", row.get("tech_org")),
                ("País", row.get("tech_country")),
                ("Dirección", row.get("tech_street")),
            ])

            agregar_seccion("5. REGISTRADOR", [
                ("Registrador", row.get("registrador")),
                ("IANA ID", row.get("registrar_iana_id")),
                ("URL", row.get("registrar_url")),
                ("Abuse Email", row.get("abuse_email")),
                ("Abuse Phone", row.get("abuse_tel")),
            ])

            agregar_seccion("6. FECHAS DEL DOMINIO", [
                ("Fecha de creación", row.get("fecha_registro")),
                ("Fecha de actualización", row.get("fecha_actualizacion")),
                ("Fecha de expiración", row.get("fecha_expiracion")),
                ("Última actualización BD", row.get("last_update")),
            ])

            agregar_seccion("7. DNS Y SEGURIDAD", [
                ("DNSSEC", row.get("dnssec")),
                ("Nameservers", row.get("nameservers")),
            ])

            agregar_seccion("8. ESTADOS DEL DOMINIO", [
                ("Estados", row.get("estado_dominio")),
            ])

            # Campos adicionales
            campos_extra = []
            for clave, valor_extra in row.items():
                if clave in campos_conocidos:
                    continue
                if valor_extra is None:
                    continue
                if isinstance(valor_extra, str) and not valor_extra.strip():
                    continue
                campos_extra.append((clave, valor_extra))

            if campos_extra:
                agregar_seccion("9. INFORMACIÓN ADICIONAL", campos_extra)

        doc.build(historia, onFirstPage=pie_pagina, onLaterPages=pie_pagina)
        
        nombre_archivo = f"dominios_reporte_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
        ui.download(buffer.getvalue(), nombre_archivo)
        ui.notify(f"PDF generado correctamente: {total} dominio(s)", type="positive")

    # ----------------------------------------------------------------------- #
    # EVENTOS
    # ----------------------------------------------------------------------- #

    boton_iniciar.on_click(iniciar)
    boton_detener.on_click(detener)
    boton_json.on_click(descargar_json)
    boton_pdf.on_click(descargar_pdf)
    dominios_input.on("keydown.enter.ctrl", iniciar)