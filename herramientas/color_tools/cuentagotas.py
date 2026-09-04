"""
eyedropper.py — Extracción de color de pantalla (Cuentagotas Global).

Maneja:
  - Minimizado automático de la ventana nativa de la aplicación para dejar ver el fondo/otras apps.
  - Activación del API de pantalla completa EyeDropper (window.EyeDropper).
  - Restauración automática de la ventana de suite_tk trayendo el color capturado (HEX).
  - Fallback interactivo en caso de entornos sin soporte de EyeDropper.
"""

from __future__ import annotations

import asyncio
from typing import Callable
from nicegui import app, ui


JS_EYEDROPPER_SCRIPT = """
async () => {
    if (!window.EyeDropper) {
        return { ok: false, error: 'EyeDropper no soportado en este motor web' };
    }
    try {
        const eyeDropper = new EyeDropper();
        const result = await eyeDropper.open();
        return { ok: true, hex: result.sRGBHex };
    } catch (err) {
        if (err && err.name === 'AbortError') {
            return { ok: false, cancelado: true };
        }
        return { ok: false, error: String(err) };
    }
}
"""


async def capturar_color_pantalla(
    al_capturar: Callable[[str], None],
    al_cancelar: Callable[[], None] | None = None,
    al_error: Callable[[str], None] | None = None,
):
    """
    Minimiza la ventana (si estamos en modo de escritorio nativo),
    activa el cuentagotas de pantalla y restaura la ventana al terminar.
    """
    ventana_nativa = getattr(app, "native", None)
    main_window = getattr(ventana_nativa, "main_window", None) if ventana_nativa else None

    # 1. Minimizar ventana nativa si existe
    if main_window:
        try:
            main_window.minimize()
            # Pequeño delay para permitir que el SO complete la animación de minimizado
            await asyncio.sleep(0.3)
        except Exception:
            pass

    resultado = None
    try:
        resultado = await ui.run_javascript(JS_EYEDROPPER_SCRIPT, timeout=60.0)
    except Exception as e:
        resultado = {"ok": False, "error": str(e)}
    finally:
        # 2. Restaurar ventana nativa
        if main_window:
            try:
                main_window.restore()
            except Exception:
                pass

    if not resultado:
        if al_cancelar:
            al_cancelar()
        return

    if resultado.get("ok") and resultado.get("hex"):
        hex_capturado = resultado["hex"].upper()
        al_capturar(hex_capturado)
        ui.notify(f"Color capturado: {hex_capturado}", type="positive", icon="colorize")
    elif resultado.get("cancelado"):
        if al_cancelar:
            al_cancelar()
        ui.notify("Captura de color cancelada", type="info")
    else:
        msg = resultado.get("error", "Error desconocido al capturar pantalla")
        if al_error:
            al_error(msg)
        else:
            ui.notify(f"Aviso: {msg}", type="warning")
