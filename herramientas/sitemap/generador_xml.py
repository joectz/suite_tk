"""
generador_xml.py — Construcción y serialización del XML del sitemap.

Genera:
  - Un <urlset> simple si hay <= 50 000 URLs y < 50 MB.
  - Un <sitemapindex> + fragmentos (sitemap-1.xml, sitemap-2.xml…) si se
    supera el límite.
  - Opcionalmente, sitemaps segmentados por tipo de contenido.

Solo usa xml.etree.ElementTree (stdlib) — sin dependencias externas.
No incluye <priority> ni <changefreq> — Google los ignora.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from . import filtros as _filtros

NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
LIMITE_URLS = 50_000
LIMITE_BYTES = 50 * 1024 * 1024  # 50 MB


# ----------------------------------------------------------------------- #
# Generación de un <urlset> en memoria
# ----------------------------------------------------------------------- #

def generar_urlset(
    items: list[dict],
    lastmod_dict: dict[str, str],
) -> bytes:
    """
    Construye un <urlset> XML con <url><loc> y opcionalmente <lastmod>.

    Retorna los bytes del XML (UTF-8, con declaración <?xml ...?>).
    """
    raiz = ET.Element("urlset")
    raiz.set("xmlns", NAMESPACE)

    for it in items:
        url_str = it["url"]
        elem_url = ET.SubElement(raiz, "url")

        loc = ET.SubElement(elem_url, "loc")
        loc.text = url_str

        fecha = lastmod_dict.get(url_str)
        if fecha:
            lastmod = ET.SubElement(elem_url, "lastmod")
            lastmod.text = fecha

    return _serializar(raiz)


# ----------------------------------------------------------------------- #
# Generación del <sitemapindex>
# ----------------------------------------------------------------------- #

def generar_sitemap_indice(
    nombres_fragmentos: list[str],
    url_base: str,
    fecha_hoy: str | None = None,
) -> bytes:
    """
    Construye un <sitemapindex> que apunta a cada fragmento.

    nombres_fragmentos: nombres de archivo relativos (ej. ["sitemap-1.xml"])
    url_base: URL raíz del sitio para formar las <loc> absolutas
    """
    if fecha_hoy is None:
        fecha_hoy = date.today().isoformat()

    url_base = url_base.rstrip("/")

    raiz = ET.Element("sitemapindex")
    raiz.set("xmlns", NAMESPACE)

    for nombre in nombres_fragmentos:
        elem = ET.SubElement(raiz, "sitemap")

        loc = ET.SubElement(elem, "loc")
        loc.text = f"{url_base}/{nombre}"

        lastmod = ET.SubElement(elem, "lastmod")
        lastmod.text = fecha_hoy

    return _serializar(raiz)


# ----------------------------------------------------------------------- #
# Punto de entrada principal
# ----------------------------------------------------------------------- #

def guardar_sitemap(
    items: list[dict],
    url_base: str,
    carpeta: Path,
    segmentar: bool,
    lastmod_dict: dict[str, str],
) -> list[Path]:
    """
    Genera y guarda los archivos XML del sitemap en `carpeta`.

    Lógica:
      1. Si segmentar=False:
         - <= LIMITE_URLS → un único sitemap.xml
         - > LIMITE_URLS  → sitemap-1.xml, sitemap-2.xml… + sitemap_index.xml
      2. Si segmentar=True:
         - Un sitemap por tipo (sitemap_html.xml, sitemap_pdf.xml…)
         - Cada segmento se fragmenta si supera el límite
         - Un sitemap.xml maestro (sitemapindex) que apunta a todos

    Retorna la lista de archivos generados.
    """
    carpeta.mkdir(parents=True, exist_ok=True)
    archivos_generados: list[Path] = []

    if not segmentar:
        # Modo unificado
        archivos_generados.extend(
            _guardar_con_fragmentacion(items, "sitemap", url_base, carpeta, lastmod_dict)
        )
    else:
        # Modo segmentado por tipo de contenido
        segmentos = _filtros.segmentar_por_tipo(items)
        todos_los_nombres: list[str] = []

        for tipo, items_tipo in sorted(segmentos.items()):
            prefijo = f"sitemap_{tipo}"
            rutas = _guardar_con_fragmentacion(
                items_tipo, prefijo, url_base, carpeta, lastmod_dict,
            )
            archivos_generados.extend(rutas)
            todos_los_nombres.extend(r.name for r in rutas if "index" not in r.name)

        # Índice maestro que apunta a todos los segmentos
        if len(todos_los_nombres) > 1 or any("index" in a.name for a in archivos_generados):
            indice = generar_sitemap_indice(todos_los_nombres, url_base)
            ruta_indice = carpeta / "sitemap.xml"
            ruta_indice.write_bytes(indice)
            archivos_generados.insert(0, ruta_indice)
            print(f"XML sitemap.xml (índice maestro, {len(todos_los_nombres)} segmentos)", flush=True)

    return archivos_generados


# ----------------------------------------------------------------------- #
# Fragmentación interna
# ----------------------------------------------------------------------- #

def _guardar_con_fragmentacion(
    items: list[dict],
    prefijo: str,
    url_base: str,
    carpeta: Path,
    lastmod_dict: dict[str, str],
) -> list[Path]:
    """
    Guarda un conjunto de items como uno o varios archivos XML.

    Si cabe en un solo archivo (≤ LIMITE_URLS, < LIMITE_BYTES) se genera
    un único <prefijo>.xml. Si no, se fragmenta en <prefijo>-1.xml, etc.
    con un <prefijo>_index.xml como sitemapindex.
    """
    archivos: list[Path] = []

    if len(items) <= LIMITE_URLS:
        xml_bytes = generar_urlset(items, lastmod_dict)
        if len(xml_bytes) < LIMITE_BYTES:
            ruta = carpeta / f"{prefijo}.xml"
            ruta.write_bytes(xml_bytes)
            archivos.append(ruta)
            print(f"XML {ruta.name} ({len(items)} URLs, {len(xml_bytes):,} bytes)", flush=True)
            return archivos

    # Necesita fragmentación
    fragmentos: list[str] = []
    for i in range(0, len(items), LIMITE_URLS):
        lote = items[i : i + LIMITE_URLS]
        n = (i // LIMITE_URLS) + 1
        nombre = f"{prefijo}-{n}.xml"
        xml_bytes = generar_urlset(lote, lastmod_dict)
        ruta = carpeta / nombre
        ruta.write_bytes(xml_bytes)
        archivos.append(ruta)
        fragmentos.append(nombre)
        print(f"XML {nombre} ({len(lote)} URLs, {len(xml_bytes):,} bytes)", flush=True)

    # Generar el sitemapindex para estos fragmentos
    indice = generar_sitemap_indice(fragmentos, url_base)
    ruta_indice = carpeta / f"{prefijo}_index.xml"
    ruta_indice.write_bytes(indice)
    archivos.insert(0, ruta_indice)
    print(f"XML {ruta_indice.name} (índice, {len(fragmentos)} fragmentos)", flush=True)

    return archivos


# ----------------------------------------------------------------------- #
# Utilidad de serialización
# ----------------------------------------------------------------------- #

def _serializar(raiz: ET.Element) -> bytes:
    """Serializa un Element a bytes UTF-8 con declaración XML."""
    ET.indent(raiz, space="  ")
    arbol = ET.ElementTree(raiz)

    import io
    buf = io.BytesIO()
    arbol.write(buf, encoding="UTF-8", xml_declaration=True)
    return buf.getvalue()
