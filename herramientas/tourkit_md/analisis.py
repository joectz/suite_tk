"""
analisis.py — Lineas del documento -> tours estructurados.

Dos pasos, deliberadamente separados:

    1. segmentar()  parte el documento en un TourCrudo por cada "N. TITULO" y,
       dentro de cada uno, reparte las lineas en secciones (overview,
       itinerario, incluye, ...) usando SOLO las tablas de idiomas.py. Aqui no
       se interpreta nada: se recorta y se agrupa.

    2. a_campos()   traduce un TourCrudo a los campos que espera TourKit
       (duration_days, itinerary[], faq[], ...). Aqui viven las heuristicas.

La separacion importa porque el paso 1 es el fragil (depende de como quedo el
PDF) y el paso 2 es el opinable (como se reparte "que llevar" entre title y
desc). Poder mirarlos por separado hace que un tour que salga mal se
diagnostique rapido.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from . import idiomas
from .extraccion import Linea

# Una pregunta de FAQ: termina en "?" y no es demasiado larga.
RE_PREGUNTA = re.compile(r"^[¿¡]?.{5,180}\?\s*$")
# Linea "Clave: valor" de la lista de estadisticas.
RE_CLAVE_VALOR = re.compile(r"^([^:]{2,40}):\s*(.+)$")


# --------------------------------------------------------------------------- #
# Estructuras intermedias
# --------------------------------------------------------------------------- #

@dataclass
class DiaCrudo:
    numero: int
    titulo: str = ""
    parrafos: list[str] = field(default_factory=list)
    stats: dict[str, str] = field(default_factory=dict)


@dataclass
class TourCrudo:
    """Un tour tal como venia en el documento, ya troceado pero sin interpretar."""

    numero: int
    idioma: str
    titulo: str = ""
    keywords: list[str] = field(default_factory=list)
    overview: list[str] = field(default_factory=list)
    dias: list[DiaCrudo] = field(default_factory=list)
    precio: str = ""
    moneda: str = ""
    incluye: list[str] = field(default_factory=list)
    excluye: list[str] = field(default_factory=list)
    llevar: list[str] = field(default_factory=list)
    faq: list[tuple[str, str]] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Utilidades de texto
# --------------------------------------------------------------------------- #

def sin_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def slug(texto: str, maximo: int = 90) -> str:
    """Convierte un titulo en slug ASCII apto para URL de WordPress."""
    base = sin_tildes(texto).lower()
    base = base.replace("&", " y ")
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if len(base) <= maximo:
        return base
    # Cortar en el ultimo guion completo para no partir una palabra.
    recorte = base[:maximo]
    return recorte.rsplit("-", 1)[0] if "-" in recorte else recorte


# Palabras que en un titulo van en minuscula salvo que abran el titulo o vengan
# despues de un signo de apertura. Se juntan los dos idiomas: ninguna de estas
# palabras es un nombre propio en el otro, asi que mezclarlas no hace dano.
MENORES = {
    # Espanol
    "de", "del", "la", "las", "el", "los", "y", "e", "o", "u", "en", "con",
    "por", "para", "a", "al", "desde", "hasta", "sobre", "entre", "tras", "sin",
    # Ingles
    "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with",
    "by", "from", "into", "over", "under", "via", "per", "as", "but", "nor",
}

# Nombres propios y siglas que deben conservar su forma exacta al destitular.
EXCEPCIONES_TITULO = {
    "peru": "Perú", "unesco": "UNESCO", "usd": "USD", "pen": "PEN",
    "vip": "VIP", "atv": "ATV", "suv": "SUV", "4x4": "4x4",
}


def _capitalizar(palabra: str) -> str:
    """
    Pone en mayuscula la primera LETRA, respetando "(uros" o "«taquile".

    Una palabra que lleva digitos se deja entera en mayusculas: son codigos de
    duracion como "2D/1N" o "12D/11N", y capitalizar solo la primera letra los
    dejaria en "12D/11n".
    """
    if any(c.isdigit() for c in palabra):
        return palabra.upper()
    for indice, caracter in enumerate(palabra):
        if caracter.isalpha():
            return palabra[:indice] + caracter.upper() + palabra[indice + 1:].lower()
    return palabra


def titulo_bonito(texto: str) -> str:
    """
    Convierte un titulo que viene TODO EN MAYUSCULAS del PDF a Title Case, que
    es como lo espera WordPress y como esta en la estructura de referencia.

    Si el titulo ya viene con mayusculas y minusculas mezcladas se deja tal
    cual: quien lo escribio ya decidio como queria que se viera.
    """
    letras = [c for c in sin_tildes(texto) if c.isalpha()]
    if not letras or sum(c.isupper() for c in letras) / len(letras) < 0.8:
        return texto.strip()

    palabras = texto.split()
    salida: list[str] = []
    for indice, palabra in enumerate(palabras):
        nucleo = re.sub(r"[^\w]", "", sin_tildes(palabra)).lower()
        if nucleo in EXCEPCIONES_TITULO:
            salida.append(palabra.replace(palabra.strip("()[],.;:"), EXCEPCIONES_TITULO[nucleo]))
            continue
        # La primera palabra, y la que sigue a un signo de apertura o a un
        # guion de separacion, siempre van en mayuscula.
        anterior = palabras[indice - 1] if indice else ""
        abre = indice == 0 or anterior.endswith(("(", "[", ":", "–", "—", "-", "/"))
        if not abre and nucleo in MENORES:
            salida.append(palabra.lower())
        else:
            salida.append(_capitalizar(palabra))
    return " ".join(salida)


# Palabras demasiado genericas para funcionar como etiqueta de WordPress.
RUIDO_TAGS = {
    "tour", "tours", "travel", "trip", "day", "full", "the", "and", "of", "in",
    "to", "a", "an", "de", "del", "la", "las", "el", "los", "y", "en", "con",
    "por", "dia", "dias",
}


def tags_desde(keywords: list[str]) -> list[str]:
    """
    Convierte las keywords SEO en etiquetas de una sola palabra.

    Las keywords vienen como frases ("Uros Floating Islands Peru"), y una
    etiqueta de WordPress con cuatro palabras no sirve de nada. Se parte en
    palabras, se quita el relleno y se conserva el orden de aparicion.
    """
    vistas: dict[str, None] = {}
    for frase in keywords:
        for palabra in re.split(r"[^\w]+", sin_tildes(frase).lower()):
            if len(palabra) > 2 and palabra not in RUIDO_TAGS and not palabra.isdigit():
                vistas.setdefault(palabra, None)
    return list(vistas)


def recortar(texto: str, maximo: int) -> str:
    """Recorta respetando el final de frase o de palabra, sin dejar el texto a medias."""
    texto = texto.strip()
    if len(texto) <= maximo:
        return texto
    ventana = texto[:maximo]
    corte = max(ventana.rfind(". "), ventana.rfind("; "))
    if corte > maximo * 0.5:
        return ventana[: corte + 1].strip()
    return ventana.rsplit(" ", 1)[0].rstrip(" ,;:-") + "..."


def texto_o_vacio(valor) -> str:
    """
    str() de un valor de perfil, pero None se convierte en "" y no en "None".

    Sin esto, un ajuste sin rellenar acababa emitido como group_min: "None",
    que WordPress importaria como texto literal.
    """
    return "" if valor is None else str(valor)


def html_parrafos(parrafos: list[str]) -> str:
    """Envuelve parrafos en <p>, que es como TourKit espera el texto largo."""
    return "".join(f"<p>{p.strip()}</p>" for p in parrafos if p.strip())


# --------------------------------------------------------------------------- #
# Paso 1: segmentacion
# --------------------------------------------------------------------------- #

def _volcar(destino: list[str], buffer: list[str]) -> None:
    if buffer:
        destino.append(" ".join(buffer).strip())
        buffer.clear()


def segmentar(lineas: list[Linea], codigo_idioma: str) -> list[TourCrudo]:
    """
    Reparte las lineas en tours y, dentro de cada tour, en secciones.

    El agrupado de parrafos se apoya en Linea.bloque: lineas consecutivas del
    mismo bloque son el mismo parrafo. Cuando cambia el bloque, se cierra el
    parrafo en curso.
    """
    tabla = idiomas.IDIOMAS[codigo_idioma]
    tours: list[TourCrudo] = []

    tour: TourCrudo | None = None
    seccion = ""           # seccion en curso dentro del tour
    dia: DiaCrudo | None = None
    buffer: list[str] = []  # lineas del parrafo que se esta acumulando
    bloque_buffer = -1
    bloque_keywords = -1
    pregunta: str | None = None

    def cerrar_parrafo() -> None:
        nonlocal pregunta
        if not buffer:
            return
        texto = " ".join(buffer).strip()
        buffer.clear()
        if not tour or not texto:
            return
        if seccion == "overview":
            tour.overview.append(texto)
        elif seccion == "itinerario" and dia is not None:
            dia.parrafos.append(texto)
        elif seccion == "faq" and pregunta is not None:
            tour.faq.append((pregunta, texto))
            pregunta = None

    def cerrar_tour() -> None:
        cerrar_parrafo()

    for linea in lineas:
        texto = linea.texto

        # --- inicio de un tour nuevo -------------------------------------- #
        encabezado_tour = idiomas.RE_TITULO_TOUR.match(texto)
        # Solo cuenta como titulo de tour si es la primera linea de su bloque y
        # el texto va en mayusculas: asi no confundimos "1. Lo primero que..."
        # dentro de un parrafo con el encabezado de un tour.
        es_titulo_tour = bool(
            encabezado_tour
            and linea.bloque != bloque_buffer
            and not linea.vineta
            and _parece_titulo(encabezado_tour.group(2))
        )
        if es_titulo_tour:
            cerrar_tour()
            tour = TourCrudo(numero=int(encabezado_tour.group(1)), idioma=codigo_idioma)
            tour.titulo = idiomas.limpiar(encabezado_tour.group(2))
            tours.append(tour)
            seccion, dia, pregunta = "", None, None
            bloque_buffer = linea.bloque
            continue

        if tour is None:
            continue  # portada, indice, cualquier cosa antes del primer tour

        # Continuacion de un titulo partido en dos lineas ("... FLOATING\nISLANDS").
        if linea.bloque == bloque_buffer and not seccion and not tour.keywords:
            if _parece_titulo(texto) and len(tour.titulo) < 90:
                tour.titulo = idiomas.limpiar(f"{tour.titulo} {texto}")
                continue

        # --- encabezado de seccion ---------------------------------------- #
        nueva = idiomas.seccion_de(codigo_idioma, texto) if not linea.vineta else None
        if nueva:
            cerrar_parrafo()
            # "ESTADISTICAS" cuelga del dia en curso, no reinicia el itinerario.
            seccion = nueva
            if nueva not in {"estadisticas"}:
                pregunta = None
            if nueva == "itinerario":
                dia = None
            bloque_buffer = linea.bloque
            continue

        # --- linea de precio (puede aparecer en cualquier punto) ---------- #
        if not tour.precio:
            precio = tabla["re_precio"].search(texto)
            if precio:
                cerrar_parrafo()
                tour.precio = precio.group(1).replace(",", "")
                tour.moneda = _moneda(precio.group(2))
                bloque_buffer = linea.bloque
                continue

        # --- keywords ------------------------------------------------------ #
        claves = tabla["re_keywords"].match(texto)
        if claves and not tour.keywords:
            cerrar_parrafo()
            tour.keywords = _partir_keywords(claves.group(1))
            bloque_keywords = linea.bloque
            bloque_buffer = linea.bloque
            continue
        # La lista de keywords suele envolver a dos lineas dentro del mismo
        # bloque; sin esto la ultima keyword se cortaria por la mitad.
        if tour.keywords and linea.bloque == bloque_keywords:
            tour.keywords = _partir_keywords(
                " / ".join(tour.keywords) + " " + texto
            )
            continue

        # --- encabezado de dia --------------------------------------------- #
        # Se comprueba desde CUALQUIER seccion menos las FAQ, no solo desde el
        # itinerario. Dos motivos, los dos vistos en documentos reales:
        #   - el bloque de estadisticas de un dia va justo antes del encabezado
        #     del siguiente, asi que DIA 02 llega estando en "estadisticas";
        #   - los paquetes de varios dias llevan un "Inclusiones:" DENTRO de
        #     cada dia, asi que el dia siguiente llega estando en "incluye". Sin
        #     esto, el primer "Inclusiones:" de un paquete se tragaba el resto
        #     del tour entero (200+ items) y el itinerario se quedaba en 2 dias.
        # Se excluyen las FAQ porque ahi el texto es libre y una respuesta
        # podria empezar por algo parecido a un encabezado de dia.
        if seccion != "faq" and not linea.vineta:
            encabezado_dia = tabla["re_dia"].match(texto)
            if encabezado_dia:
                cerrar_parrafo()
                seccion = "itinerario"
                dia = DiaCrudo(numero=int(encabezado_dia.group(1)),
                               titulo=idiomas.limpiar(encabezado_dia.group(2)))
                tour.dias.append(dia)
                bloque_buffer = linea.bloque
                continue

        # --- contenido segun seccion --------------------------------------- #
        if seccion == "itinerario":
            if dia is None:
                # Tour de un solo dia (full day): la ruta hace de titulo.
                cerrar_parrafo()
                dia = DiaCrudo(numero=1, titulo=idiomas.limpiar(texto))
                tour.dias.append(dia)
                bloque_buffer = linea.bloque
                continue

        elif seccion == "estadisticas":
            clave_valor = RE_CLAVE_VALOR.match(texto)
            if clave_valor and dia is not None:
                campo = idiomas.etiqueta_stat(codigo_idioma, clave_valor.group(1))
                if campo:
                    dia.stats[campo] = idiomas.limpiar(clave_valor.group(2))
                    bloque_buffer = linea.bloque
                    continue

        elif seccion in {"incluye", "excluye", "llevar"}:
            cerrar_parrafo()
            getattr(tour, seccion).append(texto.rstrip(" .;"))
            bloque_buffer = linea.bloque
            continue

        elif seccion == "faq":
            if RE_PREGUNTA.match(texto):
                cerrar_parrafo()
                pregunta = texto
                bloque_buffer = linea.bloque
                continue
            if pregunta is None:
                continue  # texto suelto antes de la primera pregunta

        # --- acumular en el parrafo en curso ------------------------------- #
        if linea.bloque != bloque_buffer:
            cerrar_parrafo()
            bloque_buffer = linea.bloque
        buffer.append(texto)

    cerrar_tour()
    return tours


def _partir_keywords(bruto: str) -> list[str]:
    return [k.strip() for k in re.split(r"[/|,;]", bruto) if k.strip()]


def _parece_titulo(texto: str) -> bool:
    """
    True si el texto tiene pinta de encabezado de tour: predominantemente en
    mayusculas. Evita confundir una lista numerada dentro de un parrafo con el
    inicio de un tour nuevo.
    """
    letras = [c for c in sin_tildes(texto) if c.isalpha()]
    if len(letras) < 4:
        return False
    return sum(c.isupper() for c in letras) / len(letras) > 0.8


def _moneda(bruta: str | None) -> str:
    if not bruta:
        return ""
    bruta = bruta.upper().strip()
    return {"SOLES": "PEN", "S/": "PEN"}.get(bruta, bruta)


# --------------------------------------------------------------------------- #
# Paso 2: TourCrudo -> campos TourKit
# --------------------------------------------------------------------------- #

def _altitud(stats: dict[str, str], sufijo: str) -> str:
    """Une altura minima y maxima en el formato "3.810 - 4.150 m" de la referencia."""
    minima = _solo_cifra(stats.get("alt_min", ""))
    maxima = _solo_cifra(stats.get("alt_max", ""))
    if minima and maxima:
        return f"{minima} – {maxima} {sufijo}"
    return minima or maxima or ""


def _solo_cifra(texto: str) -> str:
    encontrada = re.search(r"[\d.,]+", texto)
    return encontrada.group(0).rstrip(".,") if encontrada else ""


def _comidas(texto: str, tabla_comidas: dict[str, str]) -> list[str]:
    """Detecta desayuno/almuerzo/cena mencionados en la descripcion del dia."""
    plano = sin_tildes(texto).lower()
    orden = ["breakfast", "lunch", "dinner"]
    halladas = {
        token for palabra, token in tabla_comidas.items()
        if re.search(rf"\b{re.escape(sin_tildes(palabra).lower())}", plano)
    }
    return [t for t in orden if t in halladas]


def _partir_llevar(texto: str, tabla: dict) -> tuple[str, str]:
    """
    Parte "Ropa abrigadora en capas para el dia y ropa mas gruesa..." en
    title="Ropa abrigadora en capas" y desc="Para el dia y ropa mas gruesa...".

    Si el texto es corto no se parte: un item de tres palabras no gana nada
    repartido en dos campos.
    """
    texto = texto.strip().rstrip(".")
    if len(texto) <= 38:
        return texto, ""
    corte = tabla["re_llevar_corte"].search(texto, 12)
    if not corte:
        coma = texto.find(", ")
        if coma > 12:
            return texto[:coma].strip(), texto[coma + 2:].strip().capitalize() + "."
        return texto, ""
    titulo = texto[: corte.start()].strip()
    resto = texto[corte.start():].strip()
    return titulo, resto[:1].upper() + resto[1:] + "."


def _requerido(texto: str, palabras: list[str]) -> int:
    plano = sin_tildes(texto).lower()
    return int(any(sin_tildes(p).lower() in plano for p in palabras))


def _sin_repetir(items: list[str]) -> list[str]:
    """
    Quita duplicados conservando el orden de aparicion.

    Importa en los paquetes de varios dias, donde cada dia repite su propio
    "Inclusiones:" con las mismas lineas de siempre (recojo, transporte, guia
    profesional...). Al juntarlas todas en la lista del tour salen decenas de
    entradas identicas. En un tour normal no hay repetidos y no cambia nada.
    """
    salida: list[str] = []
    vistos: set[str] = set()
    for item in items:
        clave = sin_tildes(item).lower().strip(" .;,")
        if clave and clave not in vistos:
            vistos.add(clave)
            salida.append(item)
    return salida


def _duracion_del_titulo(titulo: str) -> tuple[str, str] | None:
    """
    Lee "12D/11N" o "2D/1N" del titulo y devuelve (dias, noches).

    Los paquetes anuncian su duracion en el propio titulo, y es mas fiable que
    contar encabezados de dia: si uno se pierde en la extraccion, el titulo
    sigue diciendo la verdad.
    """
    encontrado = re.search(r"\b(\d{1,2})\s*D\s*/\s*(\d{1,2})\s*N\b", titulo, re.I)
    if encontrado:
        return encontrado.group(1), encontrado.group(2)
    return None


def _dificultad(tour: TourCrudo, tabla: dict) -> str:
    for dia in tour.dias:
        bruta = dia.stats.get("dificultad", "")
        if bruta:
            return tabla["dificultades"].get(idiomas.normalizar(bruta), bruta.lower())
    return ""


def a_campos(tour: TourCrudo, perfil: dict) -> dict:
    """
    Convierte un TourCrudo en el diccionario de campos de TourKit.

    Solo rellena lo que se puede deducir del documento; el resto (categorias,
    puntos de inicio/fin, tamano de grupo, ...) sale de `perfil`, que es la
    configuracion editable de la herramienta. Los campos que necesitan criterio
    humano (highlights, galeria, mapa) quedan vacios a proposito.
    """
    tabla = idiomas.IDIOMAS[tour.idioma]
    por_idioma = perfil.get("por_idioma", {}).get(tour.idioma, {})

    titulo = titulo_bonito(tour.titulo)
    dias = tour.dias or [DiaCrudo(numero=1)]

    itinerario = []
    for dia in dias:
        descripcion = html_parrafos(dia.parrafos)
        itinerario.append({
            "day": dia.numero,
            "title": dia.titulo,
            "description": descripcion,
            "meals": _comidas(" ".join(dia.parrafos), tabla["comidas"]),
            "accommodation": "",
            "altitude": _altitud(dia.stats, tabla["sufijo_altura"]),
            "distance": dia.stats.get("distancia", ""),
            "image": "",
        })

    llevar = []
    for bruto in _sin_repetir(tour.llevar):
        titulo_item, descripcion_item = _partir_llevar(bruto, tabla)
        llevar.append({
            "icon": "fa:user",
            "title": titulo_item,
            "desc": descripcion_item,
            "required": _requerido(bruto, tabla["llevar_requerido"]),
        })

    resumen = recortar(tour.overview[0], 240) if tour.overview else ""

    # El titulo manda sobre el conteo de dias cuando declara la duracion
    # ("PAQUETE LIMA - CUSCO 8D/7N"): es un dato explicito del documento, no
    # una deduccion. validacion.py avisa si ambos no coinciden.
    declarada = _duracion_del_titulo(tour.titulo)
    dias_txt, noches_txt = declarada or (str(len(dias)), str(max(0, len(dias) - 1)))

    return {
        # Identidad. `id` y `sku` los completa emparejado.py, que es quien sabe
        # de la pareja ES/EN y del numero inicial elegido por el usuario.
        "id": "",
        "title": titulo,
        "slug": slug(titulo),
        "status": perfil.get("status", "publish"),
        "menu_order": 0,
        "parent": "",
        "language": tour.idioma,
        "translation_of": "",
        "featured_image": "",
        "categories": por_idioma.get("categories", []),
        "tags": tags_desde(tour.keywords),
        "activities": [],
        "difficulty_terms": [],
        "subtitle": "",
        "sku": "",
        "tour_type": perfil.get("tour_type", "group"),
        "duration_days": dias_txt,
        "duration_nights": noches_txt,
        "duration_hours": "",
        "difficulty": _dificultad(tour, tabla),
        "guide_languages": [tour.idioma],
        "group_min": texto_o_vacio(perfil.get("group_min", "")),
        "group_max": texto_o_vacio(perfil.get("group_max", "")),
        "age_min": "",
        "start_point": por_idioma.get("start_point", ""),
        "end_point": por_idioma.get("end_point", ""),
        "short_description": resumen,
        "highlights": [],
        "includes": [{"icon": "", "title": t + "."} for t in _sin_repetir(tour.incluye)],
        "excludes": [{"icon": "", "title": t + "."} for t in _sin_repetir(tour.excluye)],
        "important_notes": "",
        "itinerary": itinerario,
        "what_to_bring": llevar,
        "what_to_bring_note": "",
        "price_base": tour.precio,
        "currency": tour.moneda or perfil.get("currency", "USD"),
        "price_child": "",
        "child_age_max": perfil.get("child_age_max", 11),
        "deposit_percent": texto_o_vacio(perfil.get("deposit_percent", "0")),
        "tax_included": texto_o_vacio(perfil.get("tax_included", "1")),
        "season_prices": [],
        "group_prices": [],
        "accepted_gateways": [],
        "availability_type": perfil.get("availability_type", "daily"),
        "departure_days": [],
        "fixed_dates": [],
        "blocked_dates": [],
        "seats_per_departure": "",
        "min_advance_days": perfil.get("min_advance_days", 1),
        "featured_image_mobile": "",
        "video_url": "",
        "gallery": [],
        "map": {"lat": "", "lng": "", "zoom": "", "address": ""},
        "gpx_file": "",
        "brochure": "",
        "inherit_global": perfil.get("inherit_global", 1),
        "override_global": [],
        "shared_policies": [],
        "custom_policies": [],
        "faq": [{"question": p, "answer": f"<p>{r}</p>"} for p, r in tour.faq],
        "destinations": [],
        "related_tours": [],
        "badge": "",
        "featured_order": "",
        "seo_title": recortar(titulo, 60),
        "seo_description": recortar(resumen, 160),
        # No es un campo de TourKit: es el cuerpo del post, lo que va debajo
        # del front-matter.
        "_cuerpo": html_parrafos(tour.overview),
    }
