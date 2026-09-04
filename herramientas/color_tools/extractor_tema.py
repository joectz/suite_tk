"""
extractor_tema.py — Extracción estricta de tema y estilos web (UI real, sin imágenes ni SVGs).

Filtros estrictos aplicados:
  - Ignora por completo elementos SVG (<svg>, <path>, <circle>, <polygon>, etc.) e imágenes (<img>, <picture>).
  - Ignora atributos y propiedades de vectores gráficos como 'fill', 'stroke', 'stop-color' o 'flood-color'.
  - Elimina referencias a data URIs e imágenes dentro de 'url(...)'.
  - Extrae EXCLUSIVAMENTE colores de la interfaz web real:
      * Variables CSS de diseño (:root { --primary: ...; --background: ...; })
      * Propiedades UI de fondo: background-color, background (sin url)
      * Propiedades UI de texto: color
      * Propiedades UI de bordes: border-color, border, outline-color
      * Meta tag directo de la página: <meta name="theme-color">
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .calculo_color import (
    RGB,
    HSL,
    es_hex_valido,
    hex_a_hsl,
    hex_a_rgb,
    hsl_a_hex,
    normalizar_hex,
    ratio_contraste,
    rgb_a_hex,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,text/css,*/*;q=0.9",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# 1. Regex para eliminar cualquier url(...) o imagen embebida en CSS
URL_EMBEBIDA_REGEX = re.compile(r"url\s*\([^)]*\)", re.I | re.S)

# 2. Regex para detectar ÚNICAMENTE propiedades de interfaz (UI), descartando fill, stroke, etc.
PROPIEDADES_UI_REGEX = re.compile(
    r"(?:(--[\w-]+)|background-color|background|(?<![\w-])color|border-color|border|outline-color)\s*:\s*([^;}{]+)",
    re.I,
)

# 3. Formatos válidos de color
HEX_REGEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")
RGB_REGEX = re.compile(r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})(?:\s*[/,]\s*[\d.]+%?)?\s*\)", re.I)
HSL_REGEX = re.compile(r"hsla?\(\s*(\d{1,3}(?:\.\d+)?)[\s,]+(\d{1,3}(?:\.\d+)?%)[\s,]+(\d{1,3}(?:\.\d+)?%)(?:\s*[/,]\s*[\d.]+%?)?\s*\)", re.I)
SHADCN_HSL_REGEX = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s+(\d{1,3}(?:\.\d+)?%)\s+(\d{1,3}(?:\.\d+)?%)\s*$")

NOMBRES_COLOR_UI = {
    "white": "#FFFFFF",
    "black": "#000000",
    "red": "#EF4444",
    "blue": "#3B82F6",
    "green": "#10B981",
    "yellow": "#F59E0B",
    "cyan": "#06B6D4",
    "gray": "#6B7280",
    "grey": "#6B7280",
}


@dataclass
class TemasSitio:
    dominio: str
    urls_analizadas: list[str]
    primary: str = "#2563EB"
    secondary: str = "#4F46E5"
    background: str = "#FFFFFF"
    surface: str = "#F8FAFC"
    text_primary: str = "#0F172A"
    text_muted: str = "#64748B"
    variables_css: dict[str, str] = field(default_factory=dict)
    paleta_completa: list[dict] = field(default_factory=list)


def parsear_valor_color(valor_str: str) -> str | None:
    """Extrae un código HEX limpio de un valor CSS de UI, descartando gradientes complejos o no-colores."""
    v = valor_str.strip().strip("'\"").lower()
    if not v or any(omitir in v for omitir in ("none", "transparent", "inherit", "initial", "currentcolor", "unset", "auto")):
        return None

    if v in NOMBRES_COLOR_UI:
        return NOMBRES_COLOR_UI[v]

    # Formato HEX (#FFF o #FFFFFF)
    m_hex = HEX_REGEX.search(v)
    if m_hex:
        try:
            return normalizar_hex(m_hex.group(0))
        except Exception:
            pass

    # Formato RGB/RGBA
    m_rgb = RGB_REGEX.search(v)
    if m_rgb:
        r, g, b = int(m_rgb.group(1)), int(m_rgb.group(2)), int(m_rgb.group(3))
        return rgb_a_hex(RGB(r, g, b))

    # Formato HSL/HSLA
    m_hsl = HSL_REGEX.search(v)
    if m_hsl:
        h = float(m_hsl.group(1))
        s = float(m_hsl.group(2).rstrip("%"))
        l = float(m_hsl.group(3).rstrip("%"))
        return hsl_a_hex(HSL(h, s, l))

    # Formato Shadcn / Tailwind HSL crudo ("221.2 83.2% 53.3%")
    m_shadcn = SHADCN_HSL_REGEX.match(v)
    if m_shadcn:
        h = float(m_shadcn.group(1))
        s = float(m_shadcn.group(2).rstrip("%"))
        l = float(m_shadcn.group(3).rstrip("%"))
        return hsl_a_hex(HSL(h, s, l))

    return None


def extraer_colores_de_css(css_texto: str) -> tuple[list[str], dict[str, str]]:
    """
    Extrae ÚNICAMENTE colores pertenecientes a propiedades de interfaz web,
    eliminando cualquier imagen, data URI SVG o propiedades de gráficos vectoriales.
    """
    colores: list[str] = []
    variables: dict[str, str] = {}

    # 1. Eliminar referencias url(...) completas
    css_sin_imagenes = URL_EMBEBIDA_REGEX.sub("", css_texto)

    # 2. Analizar solo declaraciones de propiedades UI
    for m in PROPIEDADES_UI_REGEX.finditer(css_sin_imagenes):
        var_nom = m.group(1)
        valor_raw = m.group(2).strip()

        # Si es una variable CSS
        if var_nom:
            hex_c = parsear_valor_color(valor_raw)
            if hex_c:
                variables[var_nom.strip()] = hex_c
                colores.append(hex_c)
            continue

        # Si es una propiedad UI estándar (background, color, border)
        hex_c = parsear_valor_color(valor_raw)
        if hex_c:
            colores.append(hex_c)

    return colores, variables


def extraer_tema_de_sitio(
    urls: list[str],
    timeout: int = 15,
    max_urls_css: int = 8,
    callback_progreso: Callable[[str], None] | None = None,
) -> TemasSitio:
    """
    Rastrea y consolida los colores de la interfaz web real,
    descartando absolutamente imágenes, SVGs y vectores.
    """
    if not urls:
        raise ValueError("Se debe proporcionar al menos una URL para analizar.")

    primera_url = urls[0]
    parsed_primera = urlparse(primera_url)
    dominio = parsed_primera.netloc.replace("www.", "")

    def notificar(msg: str):
        if callback_progreso:
            try:
                callback_progreso(msg)
            except Exception:
                pass

    sesion = requests.Session()
    sesion.headers.update(HEADERS)

    conteo_colores: Counter[str] = Counter()
    todas_variables: dict[str, str] = {}
    meta_theme_color: str | None = None
    urls_css_procesadas: set[str] = set()

    total_urls = min(len(urls), max_urls_css)
    notificar(f"Iniciando análisis de {total_urls} página(s) para {dominio}...")

    for idx, url in enumerate(urls[:max_urls_css], 1):
        notificar(f"[{idx}/{total_urls}] Conectando a {url}...")
        try:
            try:
                resp = sesion.get(url, timeout=timeout, verify=True)
            except requests.exceptions.SSLError:
                resp = sesion.get(url, timeout=timeout, verify=False)

            if resp.status_code != 200:
                notificar(f"  Aviso: HTTP {resp.status_code} en {url}")
                continue

            html = resp.text
            soup = BeautifulSoup(html, "html.parser")

            # --------------------------------------------------------------- #
            # ELIMINAR COMPLETAMENTE SVGS, IMÁGENES Y MULTIMEDIA DEL DOM
            # --------------------------------------------------------------- #
            for no_ui in soup.find_all(["svg", "img", "picture", "canvas", "video", "audio", "iframe"]):
                no_ui.decompose()

            # 1. Meta theme-color directo en HTML
            tag_theme = soup.find("meta", attrs={"name": "theme-color"})
            if tag_theme and tag_theme.get("content"):
                hex_theme = parsear_valor_color(tag_theme["content"])
                if hex_theme:
                    meta_theme_color = hex_theme
                    conteo_colores[hex_theme] += 12
                    notificar(f"  Color de marca detectado en meta: {hex_theme}")

            # 2. Bloques <style> de la página
            tags_style = soup.find_all("style")
            if tags_style:
                notificar(f"  Analizando {len(tags_style)} bloque(s) <style> de la página...")
                for style_tag in tags_style:
                    if style_tag.string:
                        cols, vars_css = extraer_colores_de_css(style_tag.string)
                        conteo_colores.update(cols)
                        todas_variables.update(vars_css)

            # 3. Atributos inline style (solo de elementos UI que quedaron tras borrar SVGs)
            tags_inline = soup.find_all(attrs={"style": True})
            for el in tags_inline[:80]:
                style_str = el.get("style", "")
                cols, _ = extraer_colores_de_css(style_str)
                conteo_colores.update(cols)

            # 4. Hojas de estilo externas (<link rel="stylesheet">)
            links_css = soup.find_all("link", rel=lambda r: r and "stylesheet" in r)
            for link in links_css:
                href = link.get("href")
                if not href:
                    continue
                css_url = urljoin(url, href)
                if css_url in urls_css_procesadas:
                    continue
                urls_css_procesadas.add(css_url)

                if len(urls_css_procesadas) > 6:
                    break

                notificar(f"  Descargando hoja CSS externa: {urlparse(css_url).path.split('/')[-1] or css_url}")
                try:
                    r_css = sesion.get(css_url, timeout=timeout)
                    if r_css.status_code == 200:
                        cols, vars_css = extraer_colores_de_css(r_css.text)
                        conteo_colores.update(cols)
                        todas_variables.update(vars_css)
                except Exception:
                    pass

        except Exception as e:
            notificar(f"  Error conectando a {url}: {e}")
            continue

    notificar("Clasificando roles del tema web...")

    # Filtrar colores válidos
    colores_validos: Counter[str] = Counter({
        hex_c: cant for hex_c, cant in conteo_colores.items() if es_hex_valido(hex_c)
    })

    candidatos_primario: list[tuple[str, float]] = []
    candidatos_fondo: list[tuple[str, int]] = []
    candidatos_texto: list[tuple[str, int]] = []

    # Priorizar variables explícitas de marca
    primario_detectado: str | None = meta_theme_color
    for var_nom, var_val in todas_variables.items():
        nom_low = var_nom.lower()
        if any(k in nom_low for k in ("primary", "brand", "accent", "theme", "main")):
            primario_detectado = var_val
            break

    for hex_c, cant in colores_validos.items():
        hsl = hex_a_hsl(hex_c)
        l = hsl.l
        s = hsl.s

        if l > 85.0:
            candidatos_fondo.append((hex_c, cant))
        elif l < 22.0:
            candidatos_texto.append((hex_c, cant))
        else:
            if s > 15.0:
                candidatos_primario.append((hex_c, cant * (1.0 + (s / 100.0))))

    candidatos_primario.sort(key=lambda x: x[1], reverse=True)
    candidatos_fondo.sort(key=lambda x: x[1], reverse=True)
    candidatos_texto.sort(key=lambda x: x[1], reverse=True)

    # 1. Color Primario
    if not primario_detectado:
        primario_detectado = candidatos_primario[0][0] if candidatos_primario else "#2563EB"

    # 2. Color Secundario
    secundario_detectado = "#4F46E5"
    for c, _ in candidatos_primario:
        if c != primario_detectado and ratio_contraste(c, primario_detectado) > 1.2:
            secundario_detectado = c
            break

    # 3. Fondo y Superficie
    fondo_detectado = candidatos_fondo[0][0] if candidatos_fondo else "#FFFFFF"
    superficie_detectada = "#F8FAFC"
    for f, _ in candidatos_fondo:
        if f != fondo_detectado:
            superficie_detectada = f
            break

    # 4. Textos
    texto_detectado = candidatos_texto[0][0] if candidatos_texto else "#0F172A"
    texto_muted_detectado = "#64748B"

    # 5. Paleta de la web (colores más repetidos en la interfaz)
    paleta_top = [
        {"hex": c, "conteo": cnt}
        for c, cnt in colores_validos.most_common(24)
    ]

    notificar(f"¡Extracción finalizada! Primario: {primario_detectado}")

    return TemasSitio(
        dominio=dominio,
        urls_analizadas=urls,
        primary=primario_detectado,
        secondary=secundario_detectado,
        background=fondo_detectado,
        surface=superficie_detectada,
        text_primary=texto_detectado,
        text_muted=texto_muted_detectado,
        variables_css=todas_variables,
        paleta_completa=paleta_top,
    )
