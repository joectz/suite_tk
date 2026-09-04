"""
idiomas.py — Vocabulario por idioma para reconocer las secciones de un tour.

Por que esta aparte y en un diccionario: el analizador (analisis.py) no sabe
nada de espanol ni de ingles; solo pregunta a estas tablas "esta linea es el
encabezado de la seccion X?". Agregar portugues manana es agregar una entrada
aqui, sin tocar el resto del modulo.

Todo el emparejado se hace sobre texto NORMALIZADO (ver normalizar()): en
mayusculas, sin tildes, sin dos puntos finales y con los espacios colapsados.
Asi "ESTADISTICAS DEL DIA", "Estadisticas del Dia:" y "ESTADÍSTICAS DEL DÍA"
son la misma cosa y no hay que listar cada variante ortografica.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------- #
# Normalizacion
# --------------------------------------------------------------------------- #

# Caracteres que los PDF exportados de Google Docs meten por todos lados y que
# rompen cualquier comparacion de texto si no se limpian primero.
INVISIBLES = dict.fromkeys(map(ord, "​‌‍﻿­"), None)


def limpiar(texto: str) -> str:
    """Quita invisibles y normaliza espacios raros, conservando mayusculas y tildes."""
    texto = texto.translate(INVISIBLES)
    texto = texto.replace(" ", " ").replace("\t", " ")
    return re.sub(r" {2,}", " ", texto).strip()


def normalizar(texto: str) -> str:
    """
    Forma canonica para COMPARAR encabezados: mayusculas, sin tildes y sin la
    puntuacion de los extremos.

    Los signos de interrogacion se quitan porque los documentos alternan
    "QUE LLEVAR" con "¿QUE LLEVAR?" para el mismo encabezado.
    """
    texto = limpiar(texto)
    descompuesto = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in descompuesto if not unicodedata.combining(c))
    return sin_tildes.upper().strip(" :.-–—¿?¡!").strip()


# --------------------------------------------------------------------------- #
# Tablas por idioma
# --------------------------------------------------------------------------- #
#
# secciones:      clave interna -> encabezados que la anuncian (ya normalizados).
# etiquetas_stat: campo interno -> etiquetas de la lista "ESTADISTICAS DEL DIA".
# comidas:        palabra del texto -> token que espera TourKit. Siempre en
#                 ingles, para que ES y EN produzcan el mismo valor y el par
#                 sea comparable por el validador.

IDIOMAS: dict[str, dict] = {
    "es": {
        "etiqueta": "Espanol",
        "secciones": {
            "overview": ["OVERVIEW", "RESUMEN", "DESCRIPCION", "INTRODUCCION", "EL TOUR"],
            "itinerario": ["ITINERARIO", "PROGRAMA"],
            "estadisticas": [
                "ESTADISTICAS DEL DIA", "ESTADISTICAS DIA", "ESTADISTICAS",
                "ESTADISTICAS DEL TOUR", "ESTADISTICAS DE LA RUTA",
                "ESTADISTICAS DE LA CAMINATA", "ESTADISTICAS DEL RECORRIDO",
                "DATOS DEL DIA", "FICHA TECNICA",
            ],
            "incluye": ["INCLUSIONES", "INCLUYE", "EL SERVICIO INCLUYE", "QUE INCLUYE"],
            "excluye": ["EXCLUSIONES", "NO INCLUYE", "EL SERVICIO NO INCLUYE", "QUE NO INCLUYE"],
            "llevar": ["QUE LLEVAR", "QUE TRAER", "QUE DEBES LLEVAR", "RECOMENDACIONES"],
            "faq": ["PREGUNTAS FRECUENTES", "FAQ", "PREGUNTAS"],
        },
        "etiquetas_stat": {
            "ruta": ["RUTA", "RECORRIDO"],
            "distancia": ["DISTANCIA"],
            "alt_max": ["ALTURA MAXIMA", "ALTITUD MAXIMA", "PUNTO MAS ALTO"],
            "alt_min": ["ALTURA MINIMA", "ALTITUD MINIMA", "PUNTO MAS BAJO"],
            "dificultad": ["DIFICULTAD", "NIVEL"],
            "duracion": ["DURACION"],
        },
        "re_dia": re.compile(r"^D[IÍ]A\s*0?(\d{1,2})\s*[:.–—-]\s*(.+)$", re.I),
        "re_precio": re.compile(
            r"PRECIO\s*[:.]?\s*(?:US\$|\$)?\s*([\d.,]+)\s*(USD|PEN|EUR|SOLES|S/)?", re.I
        ),
        "re_keywords": re.compile(r"^KEYWORDS?\s*[:.]\s*(.+)$", re.I),
        "comidas": {"desayuno": "breakfast", "almuerzo": "lunch", "cena": "dinner"},
        "sufijo_altura": "m",
        "no_aplica": "No aplica",
        "dificultades": {
            "FACIL": "facil", "MODERADA": "moderada", "MODERADO": "moderada",
            "DIFICIL": "dificil", "EXIGENTE": "dificil",
        },
        # Palabras que marcan un item de "que llevar" como imprescindible.
        "llevar_requerido": [
            "ropa abrigadora", "casaca", "bloqueador", "calzado", "zapatillas",
            "mochila", "botella", "dinero", "efectivo", "documento", "pasaporte", "dni",
        ],
        # Conectores por los que se parte "titulo + descripcion" en que-llevar.
        "re_llevar_corte": re.compile(r"\s+(?:especialmente para|para|por|con)\s+", re.I),
    },
    "en": {
        "etiqueta": "Ingles",
        "secciones": {
            "overview": ["OVERVIEW", "SUMMARY", "DESCRIPTION", "INTRODUCTION", "THE TOUR"],
            "itinerario": ["ITINERARY", "PROGRAM", "PROGRAMME"],
            "estadisticas": [
                "STATISTICS", "STATISTICS DAY", "DAY STATISTICS",
                "TOUR STATISTICS", "STATISTICS OF THE TOUR",
                "ROUTE STATISTICS", "STATISTICS OF THE ROUTE",
                "TRIP STATISTICS", "QUICK FACTS",
            ],
            "incluye": ["INCLUSIONS", "INCLUDES", "WHAT IS INCLUDED", "THE SERVICE INCLUDES"],
            "excluye": [
                "EXCLUSIONS", "EXCLUDES", "DOES NOT INCLUDE", "NOT INCLUDED",
                "WHAT IS NOT INCLUDED",
            ],
            "llevar": ["WHAT TO BRING", "WHAT TO PACK", "RECOMMENDATIONS"],
            "faq": ["FREQUENTLY ASKED QUESTIONS", "FAQ", "FAQS", "QUESTIONS"],
        },
        "etiquetas_stat": {
            "ruta": ["ROUTE"],
            "distancia": ["DISTANCE"],
            "alt_max": ["MAXIMUM ALTITUDE", "MAX ALTITUDE", "HIGHEST POINT"],
            "alt_min": ["MINIMUM ALTITUDE", "MIN ALTITUDE", "LOWEST POINT"],
            "dificultad": ["DIFFICULTY", "LEVEL"],
            "duracion": ["DURATION"],
        },
        "re_dia": re.compile(r"^DAY\s*0?(\d{1,2})\s*[:.–—-]\s*(.+)$", re.I),
        "re_precio": re.compile(
            r"PRICE\s*[:.]?\s*(?:US\$|\$)?\s*([\d.,]+)\s*(USD|PEN|EUR|SOLES|S/)?", re.I
        ),
        "re_keywords": re.compile(r"^KEYWORDS?\s*[:.]\s*(.+)$", re.I),
        "comidas": {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner"},
        "sufijo_altura": "m",
        "no_aplica": "N/A",
        "dificultades": {
            "EASY": "easy", "MODERATE": "moderate", "MODERATE-HIGH": "moderate",
            "HARD": "hard", "DIFFICULT": "hard", "CHALLENGING": "hard",
        },
        "llevar_requerido": [
            "warm clothing", "layered", "jacket", "sunscreen", "shoes", "boots",
            "backpack", "bottle", "cash", "money", "passport", "document", "id",
        ],
        "re_llevar_corte": re.compile(r"\s+(?:especially for|for|to|with)\s+", re.I),
    },
}

# Titulo de tour: "1. LO QUE SEA" al inicio de un bloque. El patron es identico
# en todos los idiomas, por eso vive fuera de la tabla.
RE_TITULO_TOUR = re.compile(r"^(\d{1,3})\s*[.)]\s+(.+)$", re.S)


def codigos() -> list[str]:
    return list(IDIOMAS)


def etiqueta(codigo: str) -> str:
    return IDIOMAS[codigo]["etiqueta"]


def parece_encabezado(linea: str) -> bool:
    """
    True si la linea TIENE FORMA de encabezado, mirando solo su aspecto.

    Hace falta porque los parrafos vienen cortados a lo ancho de la pagina y la
    ultima linea de uno puede coincidir por accidente con el nombre de una
    seccion: "...local handicrafts during stops on this / route." deja una
    linea suelta "route." que, sin este filtro, se tomaba por el encabezado
    ITINERARY y reseteaba el itinerario a mitad del tour.

    Un encabezado de verdad en estos documentos va TODO EN MAYUSCULAS
    ("INCLUSIONES", "¿QUÉ LLEVAR?") o termina en dos puntos ("Inclusiones:").
    Una cola de parrafo en minusculas no cumple ninguna de las dos.
    """
    linea = limpiar(linea)
    if linea.endswith(":"):
        return True
    letras = [c for c in linea if c.isalpha()]
    if not letras:
        return False
    return sum(c.isupper() for c in letras) / len(letras) > 0.85


def seccion_de(codigo_idioma: str, linea: str) -> str | None:
    """
    Devuelve la clave interna de seccion si `linea` es su encabezado, o None.

    Se acepta el encabezado con cola numerica ("STATISTICS DAY 01") porque
    varios documentos numeran el encabezado de estadisticas por dia.
    """
    if not parece_encabezado(linea):
        return None
    objetivo = normalizar(linea)
    if not objetivo or len(objetivo) > 60:
        return None
    for clave, encabezados in IDIOMAS[codigo_idioma]["secciones"].items():
        for encabezado in encabezados:
            if objetivo == encabezado:
                return clave
            cola = objetivo[len(encabezado):]
            if objetivo.startswith(encabezado) and re.fullmatch(r"[\s\d:.–—-]*", cola):
                return clave
    return None


def etiqueta_stat(codigo_idioma: str, clave_texto: str) -> str | None:
    """Mapea la etiqueta de una linea "Clave: valor" de estadisticas a su campo interno."""
    objetivo = normalizar(clave_texto)
    for campo, etiquetas in IDIOMAS[codigo_idioma]["etiquetas_stat"].items():
        if objetivo in etiquetas:
            return campo
    return None
