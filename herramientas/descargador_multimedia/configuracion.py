"""configuracion.py — Constantes y configuraciones para el Descargador Multimedia."""

import re

CATEGORIAS = {
    "imagenes": {"jpg", "jpeg", "png", "gif", "webp", "avif", "bmp",
                 "tiff", "tif", "apng", "jfif", "heic"},
    "svg": {"svg"},
    "iconos": {"ico"},
    "videos": {"mp4", "webm", "ogv", "mov", "avi", "mkv", "m4v"},
    "audio": {"mp3", "wav", "ogg", "oga", "m4a", "aac", "flac", "opus"},
    "fuentes": {"woff", "woff2", "ttf", "otf", "eot"},
}

DEFAULT_WORKERS = 6
DEFAULT_TIMEOUT = 20
DEFAULT_MIN_SIZE = 0

MEDIA_EXT = {
    # imagenes
    "jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "tiff", "tif",
    "svg", "ico", "apng", "jfif", "heic",
    # video
    "mp4", "webm", "ogv", "mov", "avi", "mkv", "m4v",
    # audio
    "mp3", "wav", "ogg", "oga", "m4a", "aac", "flac", "opus",
    # fuentes
    "woff", "woff2", "ttf", "otf", "eot",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# url(...) dentro de CSS o de atributos style="..."
CSS_URL_RE = re.compile(r"""url\(\s*['"]?(?!data:)([^'")]+)['"]?\s*\)""", re.I)
# data:image/png;base64,....
DATA_URI_RE = re.compile(r"^data:([\w./+-]+);base64,(.*)$", re.I | re.S)
# Extensiones a carpetas
EXT_A_CARPETA = {ext: cat for cat, exts in CATEGORIAS.items() for ext in exts}

ATRIBUTOS = [
    ("img", "src"), ("img", "data-src"), ("img", "data-original"),
    ("img", "data-lazy-src"), ("img", "data-srcset"),
    ("source", "src"), ("source", "srcset"),
    ("video", "src"), ("video", "poster"),
    ("audio", "src"),
    ("embed", "src"), ("iframe", "src"),
    ("object", "data"),
    ("input", "src"),
    ("track", "src"),
]
