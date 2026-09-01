"""utilidades.py — Funciones auxiliares y de sanitización para el Descargador Multimedia."""

import hashlib
import mimetypes
import os
import re
from urllib.parse import urljoin, urlparse, unquote

from .configuracion import EXT_A_CARPETA


def extension_de(url: str) -> str:
    """Devuelve la extensión (sin punto, en minúsculas) de una URL."""
    path = urlparse(url).path
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return ext


def es_media(url: str, extensiones: set[str]) -> bool:
    """Verifica si la URL corresponde a un tipo multimedia válido."""
    ext = extension_de(url)
    if ext:
        return ext in extensiones
    # Sin extensión: puede ser un CDN tipo /image?id=123. Se filtra por Content-Type al descargar.
    return True


def subcarpeta_de_ruta(url_pagina: str) -> str:
    """
    Convierte la ruta de una URL en subcarpetas jerárquicas limpias.
    Ejemplos:
      'https://www.newenergyspa.com/'                    -> ''
      'https://www.newenergyspa.com/2017'                -> '2017'
      'https://www.newenergyspa.com/2017/12'             -> '2017/12'
      'https://www.newenergyspa.com/2017/12/index.html'   -> '2017/12'
    """
    parsed = urlparse(url_pagina)
    path = parsed.path.strip("/")
    if not path:
        return ""

    partes = [p for p in path.split("/") if p]

    # Quitar el nombre del archivo si es una extensión de documento web (.html, .php, etc.)
    if partes and re.search(r"\.(html?|php|asp|jsp|cgi)$", partes[-1], re.IGNORECASE):
        partes.pop()

    partes_limpias = [re.sub(r"[^\w.\-]+", "_", p) for p in partes]
    return os.path.join(*partes_limpias) if partes_limpias else ""


def nombre_seguro(url: str, contenido: bytes | None = None,
                  content_type: str | None = None) -> str:
    """Genera un nombre de archivo limpio y único a partir de la URL."""
    parsed = urlparse(url)
    base = os.path.basename(unquote(parsed.path)) or "archivo"
    base = re.sub(r"[^\w.\-]+", "_", base).strip("._") or "archivo"

    nombre, ext = os.path.splitext(base)
    nombre = nombre[:80]  # Evitar nombres kilométricos

    if not ext and content_type:
        adivinada = mimetypes.guess_extension(content_type.split(";")[0].strip())
        ext = adivinada or ""

    # Hash corto de la URL para evitar colisiones entre rutas distintas
    firma = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{nombre}_{firma}{ext}"


def parsear_srcset(valor: str) -> list[str]:
    """Extrae todas las URLs de un atributo srcset."""
    urls = []
    for parte in valor.split(","):
        parte = parte.strip()
        if parte:
            urls.append(parte.split()[0])
    return urls


def carpeta_por_extension(ext: str, content_type: str = "") -> str:
    """Determina la subcarpeta de destino según la extensión o Content-Type."""
    if ext in EXT_A_CARPETA:
        return EXT_A_CARPETA[ext]
    tipo = content_type.split("/")[0] if content_type else ""
    mapa_ct = {"image": "imagenes", "video": "videos", "audio": "audio", "font": "fuentes"}
    return mapa_ct.get(tipo, "otros")
