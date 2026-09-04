"""
emision.py — Campos -> Markdown con el formato de importacion de TourKit.

Por que un emisor propio y no yaml.dump():
    El formato de referencia mezcla estilos a proposito y PyYAML no reproduce
    esa mezcla. En el mismo documento conviven listas en linea
    (categories: [Adventure, Cultural]), listas de objetos indentadas
    (highlights, itinerary), numeros entrecomillados a proposito
    (duration_days: "2", que es texto para el plugin) y numeros crudos
    (menu_order: 0). yaml.dump aplicaria un solo criterio a todo y ademas
    reordenaria o reindentaria a su gusto.

    Como el esquema es fijo y conocido, describirlo en una tabla (ESQUEMA) y
    escribirlo a mano da control exacto y salida estable, que se diffea limpio
    entre corridas. PyYAML se sigue usando, pero para VALIDAR: validacion.py
    relee lo que emitimos y confirma que es YAML correcto.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

VERSION_FORMATO = "0.6.0"

# Tipos de campo:
#   txt    texto plano (se entrecomilla solo si YAML lo necesita)
#   num    numero que el plugin espera como TEXTO -> "100"; vacio -> ''
#   int    numero crudo -> 0
#   flow   lista en una linea -> [a, b] / []
#   objs   lista de objetos indentada; el extra describe sus campos
#   mapa   mapa indentado; el extra describe sus campos
ESQUEMA: list[tuple] = [
    ("id", "int"), ("title", "txt"), ("slug", "txt"), ("status", "txt"),
    ("menu_order", "int"), ("parent", "txt"), ("language", "txt"),
    ("translation_of", "txt"), ("featured_image", "txt"),
    ("categories", "flow"), ("tags", "flow"), ("activities", "flow"),
    ("difficulty_terms", "flow"), ("subtitle", "txt"), ("sku", "txt"),
    ("tour_type", "txt"), ("duration_days", "num"), ("duration_nights", "num"),
    ("duration_hours", "num"), ("difficulty", "txt"),
    ("guide_languages", "flow"), ("group_min", "num"), ("group_max", "num"),
    ("age_min", "num"), ("start_point", "txt"), ("end_point", "txt"),
    ("short_description", "txt"),
    ("highlights", "objs", [("icon", "txt"), ("title", "txt"), ("text", "txt")]),
    ("includes", "objs", [("icon", "txt"), ("title", "txt")]),
    ("excludes", "objs", [("icon", "txt"), ("title", "txt")]),
    ("important_notes", "txt"),
    ("itinerary", "objs", [
        ("day", "int"), ("title", "txt"), ("description", "txt"),
        ("meals", "flow"), ("accommodation", "txt"), ("altitude", "txt"),
        ("distance", "txt"), ("image", "txt"),
    ]),
    ("what_to_bring", "objs", [
        ("icon", "txt"), ("title", "txt"), ("desc", "txt"), ("required", "int"),
    ]),
    ("what_to_bring_note", "txt"), ("price_base", "num"), ("currency", "txt"),
    ("price_child", "num"), ("child_age_max", "int"), ("deposit_percent", "num"),
    ("tax_included", "num"), ("season_prices", "flow"), ("group_prices", "flow"),
    ("accepted_gateways", "flow"), ("availability_type", "txt"),
    ("departure_days", "flow"), ("fixed_dates", "flow"), ("blocked_dates", "flow"),
    ("seats_per_departure", "num"), ("min_advance_days", "int"),
    ("featured_image_mobile", "txt"), ("video_url", "txt"), ("gallery", "flow"),
    ("map", "mapa", [("lat", "txt"), ("lng", "txt"), ("zoom", "txt"), ("address", "txt")]),
    ("gpx_file", "txt"), ("brochure", "txt"), ("inherit_global", "int"),
    ("override_global", "flow"), ("shared_policies", "flow"),
    ("custom_policies", "flow"),
    ("faq", "objs", [("question", "txt"), ("answer", "txt")]),
    ("destinations", "flow"), ("related_tours", "flow"), ("badge", "txt"),
    ("featured_order", "num"), ("seo_title", "txt"), ("seo_description", "txt"),
]

# Palabras que YAML interpreta como booleano o nulo si van sin comillas.
RESERVADAS = {
    "true", "false", "yes", "no", "on", "off", "null", "none", "~",
    "y", "n",
}
RE_NUMERO = re.compile(r"^[-+]?(\d[\d_]*(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")


def _necesita_comillas(texto: str) -> bool:
    if texto == "" or texto != texto.strip():
        return True
    if texto[0] in "-?:,[]{}#&*!|>'\"%@`":
        return True
    # ": " abriria un mapa y " #" abriria un comentario.
    if ": " in texto or " #" in texto or texto.endswith(":"):
        return True
    if texto.lower() in RESERVADAS or RE_NUMERO.match(texto):
        return True
    return "\n" in texto


def _txt(valor) -> str:
    """Escalar de texto: entrecomillado solo cuando YAML lo exige."""
    texto = "" if valor is None else str(valor)
    texto = texto.replace("\n", " ").replace("\r", " ").strip()
    if _necesita_comillas(texto):
        return "'" + texto.replace("'", "''") + "'"
    return texto


def _num(valor) -> str:
    """Numero que el plugin quiere como texto: "100". Vacio -> ''."""
    texto = "" if valor is None else str(valor).strip()
    return "''" if texto == "" else '"' + texto.replace('"', '\\"') + '"'


def _int(valor) -> str:
    """Numero crudo. Si no es convertible se emite como texto para no romper el YAML."""
    texto = "" if valor is None else str(valor).strip()
    if texto == "":
        return "''"
    try:
        return str(int(float(texto)))
    except ValueError:
        return _txt(texto)


def _flow(valor) -> str:
    """Lista en una linea. En contexto flow la coma separa, asi que hay que escaparla."""
    if not valor:
        return "[]"
    piezas = []
    for elemento in valor:
        texto = str(elemento).strip()
        if _necesita_comillas(texto) or re.search(r"[,\[\]{}]", texto):
            texto = "'" + texto.replace("'", "''") + "'"
        piezas.append(texto)
    return "[" + ", ".join(piezas) + "]"


_ESCALARES = {"txt": _txt, "num": _num, "int": _int, "flow": _flow}


def _objetos(clave: str, valor, campos: list[tuple[str, str]]) -> list[str]:
    if not valor:
        return [f"{clave}: []"]
    lineas = [f"{clave}:"]
    for elemento in valor:
        primero = True
        for sub_clave, sub_tipo in campos:
            rendido = _ESCALARES[sub_tipo](elemento.get(sub_clave, ""))
            guion = "  - " if primero else "    "
            lineas.append(f"{guion}{sub_clave}: {rendido}")
            primero = False
    return lineas


def _mapa(clave: str, valor, campos: list[tuple[str, str]]) -> list[str]:
    valor = valor or {}
    lineas = [f"{clave}:"]
    for sub_clave, sub_tipo in campos:
        lineas.append(f"  {sub_clave}: {_ESCALARES[sub_tipo](valor.get(sub_clave, ''))}")
    return lineas


def front_matter(campos: dict) -> str:
    """Serializa el diccionario de campos como el bloque YAML entre '---'."""
    lineas: list[str] = []
    for entrada in ESQUEMA:
        clave, tipo = entrada[0], entrada[1]
        valor = campos.get(clave, "")
        if tipo == "objs":
            lineas.extend(_objetos(clave, valor, entrada[2]))
        elif tipo == "mapa":
            lineas.extend(_mapa(clave, valor, entrada[2]))
        else:
            lineas.append(f"{clave}: {_ESCALARES[tipo](valor)}")
    return "\n".join(lineas)


def item(campos: dict) -> str:
    """Un tour completo: marcador, front-matter y cuerpo del post."""
    cuerpo = (campos.get("_cuerpo") or "").strip()
    return f"<!-- tourkit:item -->\n---\n{front_matter(campos)}\n---\n\n{cuerpo}\n"


def documento(tours: list[dict], idioma_base: str, entidad: str = "tours") -> str:
    """
    Arma el .md completo: cabecera de exportacion + un item por tour.

    En `language` va el idioma base del lote. El archivo puede contener varios
    idiomas (cada item declara el suyo en su propio campo `language`), asi que
    la cabecera indica cual es el original de la exportacion.
    """
    marca = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    cabecera = (
        f'<!-- tourkit:export entity="{entidad}" language="{idioma_base}" '
        f'version="{VERSION_FORMATO}" exported="{marca}" count="{len(tours)}" -->'
    )
    return cabecera + "\n\n" + "\n".join(item(t) for t in tours)
