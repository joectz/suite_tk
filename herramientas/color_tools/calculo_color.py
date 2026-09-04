"""
color_math.py — Operaciones matemáticas puras de color, accesibilidad WCAG y generación de escalas.

Soporta:
  - Conversión síncrona HEX <-> RGB <-> HSL <-> HSV.
  - Luminancia relativa y cálculo de contraste WCAG 2.1 (AA / AAA).
  - Generación de escala de 10-11 tonos para Tailwind CSS (50 a 950).
  - Generación automática de tema en Modo Oscuro equilibrado.
  - Armonías de color (Complementarios, Análogos, Triádicos, Split-Complementarios).
"""

from __future__ import annotations

import colorsys
import re
from typing import NamedTuple


class RGB(NamedTuple):
    r: int  # 0-255
    g: int  # 0-255
    b: int  # 0-255


class HSL(NamedTuple):
    h: float  # 0-360 grados
    s: float  # 0-100 porcentaje
    l: float  # 0-100 porcentaje


# --------------------------------------------------------------------------- #
# Validaciones y Conversiones Básicas
# --------------------------------------------------------------------------- #

def normalizar_hex(hex_str: str) -> str:
    """Normaliza un string hexadecimal al formato estándar #RRGGBB en mayúsculas."""
    h = hex_str.strip().lstrip("#").upper()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    elif len(h) == 8:  # ignorar canal alfa si viene en #RRGGBBAA
        h = h[:6]
    elif len(h) != 6:
        raise ValueError(f"Formato hexadecimal inválido: {hex_str}")
    if not re.fullmatch(r"[0-9A-F]{6}", h):
        raise ValueError(f"Caracteres hexadecimales inválidos: {hex_str}")
    return f"#{h}"


def es_hex_valido(hex_str: str) -> bool:
    """Retorna True si el string representa un color hexadecimal válido."""
    try:
        normalizar_hex(hex_str)
        return True
    except Exception:
        return False


def hex_a_rgb(hex_str: str) -> RGB:
    """Convierte un color HEX a tupla RGB (0-255)."""
    h = normalizar_hex(hex_str).lstrip("#")
    return RGB(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_a_hex(rgb: RGB | tuple[int, int, int]) -> str:
    """Convierte una tupla RGB (0-255) a string HEX #RRGGBB."""
    r = max(0, min(255, int(rgb[0])))
    g = max(0, min(255, int(rgb[1])))
    b = max(0, min(255, int(rgb[2])))
    return f"#{r:02X}{g:02X}{b:02X}"


def rgb_a_hsl(rgb: RGB | tuple[int, int, int]) -> HSL:
    """Convierte RGB (0-255) a HSL (H: 0-360, S: 0-100, L: 0-100)."""
    r_norm = rgb[0] / 255.0
    g_norm = rgb[1] / 255.0
    b_norm = rgb[2] / 255.0

    h, l, s = colorsys.rgb_to_hls(r_norm, g_norm, b_norm)
    return HSL(round(h * 360.0, 1), round(s * 100.0, 1), round(l * 100.0, 1))


def hsl_a_rgb(hsl: HSL | tuple[float, float, float]) -> RGB:
    """Convierte HSL (H: 0-360, S: 0-100, L: 0-100) a RGB (0-255)."""
    h_norm = (hsl[0] % 360.0) / 360.0
    s_norm = max(0.0, min(100.0, float(hsl[1]))) / 100.0
    l_norm = max(0.0, min(100.0, float(hsl[2]))) / 100.0

    r, g, b = colorsys.hls_to_rgb(h_norm, l_norm, s_norm)
    return RGB(round(r * 255), round(g * 255), round(b * 255))


def hex_a_hsl(hex_str: str) -> HSL:
    return rgb_a_hsl(hex_a_rgb(hex_str))


def hsl_a_hex(hsl: HSL | tuple[float, float, float]) -> str:
    return rgb_a_hex(hsl_a_rgb(hsl))


# --------------------------------------------------------------------------- #
# Accesibilidad y Luminancia WCAG 2.1
# --------------------------------------------------------------------------- #

def luminancia_relativa(rgb: RGB | tuple[int, int, int]) -> float:
    """
    Calcula la luminancia relativa según la fórmula estándar de la W3C (WCAG 2.1).
    Valores entre 0.0 (negro más oscuro) y 1.0 (blanco más claro).
    """
    def canal_lineal(c_255: int) -> float:
        c = c_255 / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r_lin = canal_lineal(rgb[0])
    g_lin = canal_lineal(rgb[1])
    b_lin = canal_lineal(rgb[2])
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def ratio_contraste(color_a: str, color_b: str) -> float:
    """
    Calcula el ratio de contraste entre dos colores (HEX).
    Resultado entre 1.0 (sin contraste) y 21.0 (máximo contraste, blanco/negro).
    """
    rgb_a = hex_a_rgb(color_a)
    rgb_b = hex_a_rgb(color_b)
    l1 = luminancia_relativa(rgb_a)
    l2 = luminancia_relativa(rgb_b)
    mas_claro = max(l1, l2)
    mas_oscuro = min(l1, l2)
    return round((mas_claro + 0.05) / (mas_oscuro + 0.05), 2)


def evaluar_wcag(ratio: float) -> dict[str, bool | str]:
    """
    Evalúa el cumplimiento de pautas WCAG 2.1 para el ratio dado.
    """
    aa_normal = ratio >= 4.5
    aa_grande = ratio >= 3.0
    aaa_normal = ratio >= 7.0
    aaa_grande = ratio >= 4.5
    ui_componentes = ratio >= 3.0

    if aaa_normal:
        nivel = "AAA Excelente"
    elif aa_normal:
        nivel = "AA Adecuado"
    elif aa_grande:
        nivel = "AA Solo texto grande"
    else:
        nivel = "Fallo (Contraste bajo)"

    return {
        "ratio": ratio,
        "aa_normal": aa_normal,
        "aa_grande": aa_grande,
        "aaa_normal": aaa_normal,
        "aaa_grande": aaa_grande,
        "ui_componentes": ui_componentes,
        "nivel": nivel,
    }


def mejor_color_texto(fondo_hex: str) -> str:
    """Devuelve '#FFFFFF' o '#111827' según cuál tenga mayor contraste sobre el fondo."""
    blanco = "#FFFFFF"
    negro = "#111827"
    return blanco if ratio_contraste(fondo_hex, blanco) >= ratio_contraste(fondo_hex, negro) else negro


# --------------------------------------------------------------------------- #
# Generador de Escalas de Diseño (Tailwind CSS 50 - 950)
# --------------------------------------------------------------------------- #

# Distribución de luminosidad objetivo para cada matiz de Tailwind
OBJETIVO_LUMINOSIDAD_TAILWIND = {
    50: 96.0,
    100: 92.0,
    200: 84.0,
    300: 74.0,
    400: 62.0,
    500: 50.0,
    600: 40.0,
    700: 31.0,
    800: 22.0,
    900: 15.0,
    950: 9.0,
}


def generar_escala_tailwind(color_base_hex: str) -> dict[int, str]:
    """
    Genera una escala armónica de 11 matices (50 a 950) conservando el matiz
    (Hue) y saturación del color base mientras ajusta la luminosidad.
    """
    base_hsl = hex_a_hsl(color_base_hex)
    h, s, l_base = base_hsl

    escala: dict[int, str] = {}
    for matiz, target_l in OBJETIVO_LUMINOSIDAD_TAILWIND.items():
        if matiz == 500:
            # Mantener exacto el color del usuario si su luminosidad está cerca de 50%
            if abs(l_base - 50.0) < 12.0:
                escala[matiz] = normalizar_hex(color_base_hex)
                continue

        # Modulación sutil de saturación: los tonos muy claros y muy oscuros
        # se saturan ligeramente menos para evitar colores estridentes o quemados
        factor_s = 1.0
        if target_l > 80.0:
            factor_s = 0.85
        elif target_l < 20.0:
            factor_s = 0.90

        s_ajustada = max(10.0, min(100.0, s * factor_s))
        escala[matiz] = hsl_a_hex(HSL(h, s_ajustada, target_l))

    return escala


# --------------------------------------------------------------------------- #
# Generador Automático de Modo Oscuro (Dark Mode Flipper)
# --------------------------------------------------------------------------- #

def generar_modo_oscuro(tema_claro: dict[str, str]) -> dict[str, str]:
    """
    Recibe un tema claro estructurado con claves como 'primary', 'background',
    'surface', 'text_primary', 'text_muted' y genera su contraparte equilibrada
    para Modo Oscuro.
    """
    tema_oscuro: dict[str, str] = {}

    # 1. Color Primario: elevar ligeramente su luminosidad si es muy oscuro
    # para asegurar buen contraste contra fondos negros/grises oscuros
    if "primary" in tema_claro and es_hex_valido(tema_claro["primary"]):
        hsl_p = hex_a_hsl(tema_claro["primary"])
        # Para fondo oscuro, el primario debe tener luminosidad entre 55% y 68%
        nueva_l = max(55.0, min(70.0, hsl_p.l + 10.0 if hsl_p.l < 50.0 else hsl_p.l))
        tema_oscuro["primary"] = hsl_a_hex(HSL(hsl_p.h, min(95.0, hsl_p.s * 1.05), nueva_l))

    # 2. Color Secundario / Acento
    if "secondary" in tema_claro and es_hex_valido(tema_claro["secondary"]):
        hsl_s = hex_a_hsl(tema_claro["secondary"])
        nueva_l = max(55.0, min(75.0, hsl_s.l + 10.0 if hsl_s.l < 50.0 else hsl_s.l))
        tema_oscuro["secondary"] = hsl_a_hex(HSL(hsl_s.h, hsl_s.s, nueva_l))

    # 3. Fondos y Superficies (Invertir hacia paleta Slate/Zinc oscura)
    # Fondo base: #0B0F19 o #0F172A
    tema_oscuro["background"] = "#0F172A"
    # Superficie / Cards: #1E293B
    tema_oscuro["surface"] = "#1E293B"
    # Bordes: #334155
    tema_oscuro["border"] = "#334155"

    # 4. Textos
    tema_oscuro["text_primary"] = "#F8FAFC"
    tema_oscuro["text_muted"] = "#94A3B8"

    return tema_oscuro


# --------------------------------------------------------------------------- #
# Armonías de Color
# --------------------------------------------------------------------------- #

def calcular_armonias(hex_color: str) -> dict[str, list[str]]:
    """
    Calcula paletas armónicas a partir de un color base.
    """
    h, s, l = hex_a_hsl(hex_color)

    def tono(offset_h: float, mod_s: float = 0.0, mod_l: float = 0.0) -> str:
        h_nueva = (h + offset_h) % 360.0
        s_nueva = max(5.0, min(100.0, s + mod_s))
        l_nueva = max(5.0, min(95.0, l + mod_l))
        return hsl_a_hex(HSL(h_nueva, s_nueva, l_nueva))

    return {
        "complementario": [normalizar_hex(hex_color), tono(180)],
        "analogo": [tono(-30), normalizar_hex(hex_color), tono(30)],
        "triadico": [normalizar_hex(hex_color), tono(120), tono(240)],
        "split_complementario": [normalizar_hex(hex_color), tono(150), tono(210)],
        "monocromatico": [
            tono(0, mod_l=-25),
            tono(0, mod_l=-12),
            normalizar_hex(hex_color),
            tono(0, mod_l=15),
            tono(0, mod_l=30),
        ],
    }
