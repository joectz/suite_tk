"""
extraccion.py — Documento (PDF o DOCX) -> lista plana de Lineas.

Este modulo es deliberadamente TONTO: no sabe que es un tour, ni que idioma
esta leyendo, ni que significa "OVERVIEW". Solo entrega lineas de texto
limpias, cada una etiquetada con el bloque al que pertenecia en el original y
con si venia como vineta. Toda la interpretacion vive en analisis.py.

Por que "bloques" y no lineas sueltas:
    En un PDF los parrafos vienen cortados a lo ancho de la pagina, y no hay
    linea en blanco entre uno y otro. Si uniesemos por lineas en blanco, todo
    OVERVIEW saldria como un solo parrafo gigante. PyMuPDF expone
    page.get_text("blocks"), y en estos documentos cada bloque coincide
    exactamente con un parrafo, un encabezado o un grupo de vinetas. Guardando
    el numero de bloque, analisis.py puede volver a unir las lineas de un mismo
    parrafo sin adivinar donde termina.

Por que PyMuPDF y no pypdf:
    Estos PDF salen exportados de Google Docs, que posiciona el texto palabra
    por palabra. pypdf no reagrupa y devuelve una palabra por linea (medido:
    texto inservible, y ademas 40x mas lento). PyMuPDF reconstruye parrafos
    correctamente. Nota de licencia: PyMuPDF es AGPL-3.0; para uso interno no
    hay problema, pero si algun dia se distribuye el .exe a terceros habria que
    revisarlo o cambiar a pdfplumber (mas lento, licencia permisiva).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .idiomas import limpiar

# Marcadores de vineta que aparecen al inicio de linea en estos documentos.
VINETAS = "•●○▪■‣⁃·∙*–—"
RE_VINETA = re.compile(rf"^\s*[{re.escape(VINETAS)}]+\s*")

EXTENSIONES_PDF = {".pdf"}
EXTENSIONES_DOCX = {".docx"}


class ErrorExtraccion(RuntimeError):
    """El documento no se pudo leer o no tiene texto extraible."""


@dataclass(frozen=True)
class Linea:
    """
    Una linea de texto del documento original.

    - texto:  ya limpio de caracteres invisibles y de la vineta inicial.
    - bloque: indice global del bloque (parrafo) del que salio. Dos lineas con
              el mismo `bloque` pertenecen al mismo parrafo.
    - vineta: True si la linea venia precedida de un marcador de lista.
    """

    texto: str
    bloque: int
    vineta: bool


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #

def _lineas_pdf(ruta: Path) -> list[Linea]:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise ErrorExtraccion(
            "Falta la libreria PyMuPDF. Instalala con: pip install pymupdf"
        ) from exc

    lineas: list[Linea] = []
    n_bloque = 0
    with pymupdf.open(ruta) as documento:
        for pagina in documento:
            for bruto in pagina.get_text("blocks"):
                # get_text("blocks") -> (x0, y0, x1, y1, texto, n_bloque, tipo).
                # tipo 1 son imagenes; las saltamos.
                if len(bruto) > 6 and bruto[6] != 0:
                    continue
                nuevas = _partir_bloque(bruto[4], n_bloque)
                if nuevas:
                    lineas.extend(nuevas)
                    n_bloque += 1
    if not lineas:
        raise ErrorExtraccion(
            f"'{ruta.name}' no tiene texto extraible. "
            "Si es un PDF escaneado hace falta OCR, que esta herramienta no hace."
        )
    return lineas


# --------------------------------------------------------------------------- #
# DOCX
# --------------------------------------------------------------------------- #

def _lineas_docx(ruta: Path) -> list[Linea]:
    """
    En DOCX cada parrafo ya viene delimitado por el propio formato, asi que
    cada parrafo es un bloque y no hay que reconstruir nada. Ademas el estilo
    ("List Bullet", "List Paragraph") nos dice si es vineta sin mirar el texto.
    """
    try:
        import docx
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise ErrorExtraccion(
            "Falta la libreria python-docx. Instalala con: pip install python-docx"
        ) from exc

    lineas: list[Linea] = []
    n_bloque = 0
    for parrafo in docx.Document(str(ruta)).paragraphs:
        texto = limpiar(parrafo.text)
        if not texto:
            continue
        estilo = (parrafo.style.name or "").lower() if parrafo.style else ""
        es_lista = "list" in estilo
        for linea in _partir_bloque(texto, n_bloque, vineta_forzada=es_lista):
            lineas.append(linea)
        n_bloque += 1
    if not lineas:
        raise ErrorExtraccion(f"'{ruta.name}' no contiene parrafos con texto.")
    return lineas


# --------------------------------------------------------------------------- #
# Comun
# --------------------------------------------------------------------------- #

def _partir_bloque(bruto: str, n_bloque: int, vineta_forzada: bool = False) -> list[Linea]:
    """Convierte el texto crudo de un bloque en Lineas limpias, detectando vinetas."""
    salida: list[Linea] = []
    for cruda in bruto.splitlines():
        sin_vineta, marcas = RE_VINETA.subn("", limpiar(cruda), count=1)
        texto = limpiar(sin_vineta)
        if not texto:
            continue
        salida.append(Linea(texto, n_bloque, vineta_forzada or bool(marcas)))
    return salida


def extraer(ruta: str | Path) -> list[Linea]:
    """Lee un .pdf o .docx y devuelve sus lineas. Lanza ErrorExtraccion si no puede."""
    ruta = Path(ruta)
    if not ruta.is_file():
        raise ErrorExtraccion(f"No existe el archivo: {ruta}")

    sufijo = ruta.suffix.lower()
    if sufijo in EXTENSIONES_PDF:
        return _lineas_pdf(ruta)
    if sufijo in EXTENSIONES_DOCX:
        return _lineas_docx(ruta)
    raise ErrorExtraccion(
        f"Formato no soportado: '{sufijo}'. Se aceptan .pdf y .docx "
        "(el .doc antiguo hay que guardarlo antes como .docx)."
    )
