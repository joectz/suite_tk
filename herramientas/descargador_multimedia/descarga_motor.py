"""descarga_motor.py — Motor de descarga multihilo e imágenes incrustadas (base64) con deduplicación."""

import hashlib
import mimetypes
import os
import requests

from .utilidades import extension_de, nombre_seguro, carpeta_por_extension


def descargar(url: str, carpeta_base: str, sesion: requests.Session,
              timeout: int, min_size: int, subcarpeta_ruta: str = "") -> tuple[str, str]:
    """
    Descarga un recurso individual desde su URL y lo almacena en la jerarquía de subcarpetas correspondiente.
    Si el archivo ya existe en disco con contenido, omite la descarga automáticamente.
    """
    try:
        sub_cat = carpeta_por_extension(extension_de(url))
        destino_dir = os.path.join(carpeta_base, subcarpeta_ruta, sub_cat) if subcarpeta_ruta else os.path.join(carpeta_base, sub_cat)
        nombre_estimado = nombre_seguro(url)
        destino_estimado = os.path.join(destino_dir, nombre_estimado)

        # Filtro de deduplicación previa: Si ya existe en disco, se omite de inmediato
        if os.path.exists(destino_estimado) and os.path.getsize(destino_estimado) > 0:
            return "omitido", f"{url} (ya existe previamente)"

        r = sesion.get(url, timeout=timeout, stream=True)
        r.raise_for_status()

        ctype = r.headers.get("Content-Type", "")
        if ctype.startswith("text/html"):
            return "omitido", f"{url} (es HTML, no media)"

        contenido = r.content
        if len(contenido) < min_size:
            return "omitido", f"{url} ({len(contenido)} bytes < mínimo)"

        nombre = nombre_seguro(url, contenido, ctype)
        sub = carpeta_por_extension(extension_de(url), ctype)
        destino_dir = os.path.join(carpeta_base, subcarpeta_ruta, sub) if subcarpeta_ruta else os.path.join(carpeta_base, sub)
        os.makedirs(destino_dir, exist_ok=True)
        destino = os.path.join(destino_dir, nombre)

        # Verificación por si cambió la extensión final
        if os.path.exists(destino) and os.path.getsize(destino) > 0:
            return "omitido", f"{url} (ya existe en disco)"

        with open(destino, "wb") as f:
            f.write(contenido)
        
        ruta_relativa = os.path.relpath(destino, carpeta_base)
        return "ok", f"{ruta_relativa}  ({len(contenido)//1024} KB)"

    except requests.RequestException as e:
        return "error", f"{url} -> {e}"
    except OSError as e:
        return "error", f"{url} -> no se pudo escribir: {e}"


def guardar_inline(inline: list[tuple[str, bytes]], carpeta_base: str,
                   min_size: int, subcarpeta_ruta: str = "") -> int:
    """Decodifica y almacena imágenes codificadas en Base64 (data:image/...) en la subcarpeta asignada."""
    guardados = 0
    for i, (mime, datos) in enumerate(inline, 1):
        if len(datos) < min_size:
            continue
        ext = (mimetypes.guess_extension(mime) or ".bin").lstrip(".")
        sub = carpeta_por_extension(ext, mime)
        destino_dir = os.path.join(carpeta_base, subcarpeta_ruta, sub) if subcarpeta_ruta else os.path.join(carpeta_base, sub)
        os.makedirs(destino_dir, exist_ok=True)
        firma = hashlib.sha1(datos).hexdigest()[:8]
        destino = os.path.join(destino_dir, f"inline_{i:03d}_{firma}.{ext}")

        if os.path.exists(destino) and os.path.getsize(destino) > 0:
            continue

        with open(destino, "wb") as f:
            f.write(datos)
        guardados += 1
    return guardados
