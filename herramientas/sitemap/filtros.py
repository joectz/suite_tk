"""
filtros.py — Reglas de inclusión/exclusión y segmentación por tipo.

Decide cuáles de las URLs del JSON del Mapeador deben aparecer en el
sitemap y cuáles se descartan (status != 200, binarios, etc.).
"""

from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlparse


# ----------------------------------------------------------------------- #
# Extensiones y content-types que NO son páginas indexables
# ----------------------------------------------------------------------- #

EXTENSIONES_BINARIAS: set[str] = {
    # Documentos
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods",
    # Imágenes
    "jpg", "jpeg", "png", "gif", "webp", "svg", "ico", "bmp", "tiff", "avif",
    # Audio/Video
    "mp3", "mp4", "avi", "mov", "mkv", "webm", "ogg", "wav", "flac", "m4a",
    # Archivos comprimidos
    "zip", "rar", "7z", "tar", "gz", "bz2",
    # Fuentes
    "woff", "woff2", "ttf", "otf", "eot",
    # Otros
    "exe", "dmg", "apk", "iso", "bin", "css", "js", "json", "xml",
}

CONTENT_TYPES_PAGINA: set[str] = {
    "text/html",
    "application/xhtml+xml",
}


# ----------------------------------------------------------------------- #
# Clasificación por tipo
# ----------------------------------------------------------------------- #

def _extension_de_url(url: str) -> str:
    """Extrae la extensión (sin punto) de la ruta de una URL."""
    ruta = urlparse(url).path.rstrip("/")
    if "." in ruta.split("/")[-1]:
        return ruta.rsplit(".", 1)[-1].lower()
    return ""


def clasificar_tipo(item: dict) -> str:
    """
    Clasifica un item del Mapeador en una categoría de contenido.

    Retorna: 'html', 'pdf', 'imagen' u 'otro'.
    """
    ct = (item.get("content_type") or "").lower().split(";")[0].strip()
    ext = _extension_de_url(item.get("url", ""))

    # HTML
    if ct in CONTENT_TYPES_PAGINA or (not ct and ext in ("", "html", "htm", "php", "asp", "aspx")):
        return "html"

    # PDF
    if ct == "application/pdf" or ext == "pdf":
        return "pdf"

    # Imágenes
    if ct.startswith("image/") or ext in (
        "jpg", "jpeg", "png", "gif", "webp", "svg", "ico", "bmp", "tiff", "avif",
    ):
        return "imagen"

    return "otro"


# ----------------------------------------------------------------------- #
# Filtros de inclusión/exclusión
# ----------------------------------------------------------------------- #

def debe_incluir(item: dict) -> bool:
    """
    True si el item debe aparecer en el sitemap.

    Criterios:
      - status == 200
      - No es un binario/asset (por content_type o extensión de URL)
    """
    if item.get("status") != 200:
        return False

    ct = (item.get("content_type") or "").lower().split(";")[0].strip()
    ext = _extension_de_url(item.get("url", ""))

    # Si tiene un content-type de página conocida, incluir
    if ct in CONTENT_TYPES_PAGINA:
        return True

    # Si no tiene content-type, decidir por extensión
    if not ct:
        return ext not in EXTENSIONES_BINARIAS

    # Si el content-type es de un binario conocido, incluir igualmente
    # (el usuario eligió segmentar, así que PDF, imágenes, etc. pueden
    # entrar si el tipo de segmento los quiere).
    # Lo único que siempre se excluye: assets de código (css, js, json, fuentes).
    if ext in ("css", "js", "woff", "woff2", "ttf", "otf", "eot"):
        return False

    return True


def aplicar_filtros(items: list[dict]) -> list[dict]:
    """Retorna solo los items que deben incluirse en el sitemap."""
    return [it for it in items if debe_incluir(it)]


# ----------------------------------------------------------------------- #
# Segmentación por tipo de contenido
# ----------------------------------------------------------------------- #

def segmentar_por_tipo(items: list[dict]) -> dict[str, list[dict]]:
    """
    Divide los items en categorías por tipo de contenido.

    Retorna: {"html": [...], "pdf": [...], "imagen": [...], "otro": [...]}
    Solo incluye las claves que tengan al menos un item.
    """
    segmentos: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        segmentos[clasificar_tipo(it)].append(it)
    return dict(segmentos)


# ----------------------------------------------------------------------- #
# Filtro cruzado con robots.txt
# ----------------------------------------------------------------------- #

def filtrar_por_robots(items: list[dict], reglas: list[str]) -> list[dict]:
    """
    Descarta items cuya ruta esté bloqueada por alguna regla Disallow
    de robots.txt (ya parseada por robots_checker.py).

    Cada regla es un prefijo de ruta (ej. '/admin/').
    """
    if not reglas:
        return items

    resultado = []
    for it in items:
        ruta = urlparse(it.get("url", "")).path
        bloqueada = False
        for regla in reglas:
            if not regla:
                continue
            # Comodín * al final: el prefijo basta
            if regla.endswith("*"):
                if ruta.startswith(regla[:-1]):
                    bloqueada = True
                    break
            elif ruta.startswith(regla):
                bloqueada = True
                break
        if not bloqueada:
            resultado.append(it)
    return resultado
