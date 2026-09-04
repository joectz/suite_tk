"""
validacion.py — Revisa el lote antes de exportar.

Este es el modulo que mas trabajo ahorra. Convertir 26 tours a mano no falla
por el tour que sale mal de forma evidente, sino por el que sale mal de forma
silenciosa: un dia que se perdio en un idioma, un precio que no coincide entre
ES e EN, dos tours que generan el mismo slug. Eso se descubre despues de
importar a WordPress, cuando ya cuesta caro.

Se distinguen dos niveles:
    error   la importacion va a salir mal o incompleta. Hay que arreglarlo.
    aviso   probablemente esta bien, pero conviene mirarlo.

`revisar` no modifica nada: solo mira y reporta.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import yaml

from .emision import front_matter
from .emparejado import Pareja

# Campos sin los cuales el tour no sirve para importar.
OBLIGATORIOS = ("title", "slug", "sku", "language", "short_description")


@dataclass(frozen=True)
class Hallazgo:
    nivel: str      # "error" | "aviso"
    tour: str       # titulo del tour afectado
    mensaje: str


def _comparar_pareja(pareja: Pareja) -> list[Hallazgo]:
    """Contrasta original y traduccion: deben describir el MISMO tour."""
    if pareja.traduccion is None:
        return []

    base, otro = pareja.base, pareja.traduccion
    nombre = base["title"]
    hallazgos: list[Hallazgo] = []

    if base["language"] == otro["language"]:
        hallazgos.append(Hallazgo(
            "error", nombre,
            f"El original y su traduccion declaran el mismo idioma "
            f"('{base['language']}'). Revisa que cada PDF tenga su idioma bien asignado.",
        ))

    dias_base, dias_otro = len(base["itinerary"]), len(otro["itinerary"])
    if dias_base != dias_otro:
        hallazgos.append(Hallazgo(
            "error", nombre,
            f"El itinerario no coincide entre idiomas: {dias_base} dia(s) en "
            f"'{base['language']}' contra {dias_otro} en '{otro['language']}'. "
            f"Suele significar que un encabezado de dia no se reconocio.",
        ))

    if base["price_base"] and otro["price_base"] and base["price_base"] != otro["price_base"]:
        hallazgos.append(Hallazgo(
            "aviso", nombre,
            f"Los precios difieren entre idiomas: {base['price_base']} "
            f"contra {otro['price_base']}.",
        ))

    for campo, etiqueta in (("includes", "inclusiones"),
                            ("excludes", "exclusiones"),
                            ("what_to_bring", "items de que llevar"),
                            ("faq", "preguntas frecuentes")):
        cuantos_base, cuantos_otro = len(base[campo]), len(otro[campo])
        if cuantos_base != cuantos_otro:
            hallazgos.append(Hallazgo(
                "aviso", nombre,
                f"Distinto numero de {etiqueta}: {cuantos_base} contra {cuantos_otro}. "
                f"Puede ser correcto, pero conviene comprobarlo.",
            ))
    return hallazgos


def _revisar_tour(campos: dict) -> list[Hallazgo]:
    nombre = campos.get("title") or "(tour sin titulo)"
    hallazgos: list[Hallazgo] = []

    for campo in OBLIGATORIOS:
        if not campos.get(campo):
            hallazgos.append(Hallazgo("error", nombre, f"Falta el campo obligatorio '{campo}'."))

    if not campos.get("price_base"):
        hallazgos.append(Hallazgo(
            "aviso", nombre,
            "No se encontro el precio. Comprueba que el documento diga "
            "'PRECIO: <n> USD' / 'PRICE: <n> USD'.",
        ))
    if not campos.get("itinerary"):
        hallazgos.append(Hallazgo("error", nombre, "El tour se quedo sin itinerario."))
    if not campos.get("includes"):
        hallazgos.append(Hallazgo("aviso", nombre, "El tour se quedo sin inclusiones."))
    if not campos.get("faq"):
        hallazgos.append(Hallazgo("aviso", nombre, "El tour se quedo sin preguntas frecuentes."))

    for indice, dia in enumerate(campos.get("itinerary", []), 1):
        if not dia.get("description"):
            hallazgos.append(Hallazgo(
                "aviso", nombre, f"El dia {indice} no tiene descripcion."))

    # El front-matter tiene que volver a leerse como YAML valido; si no, el
    # plugin no podra importarlo. Es la red de seguridad del emisor propio.
    try:
        releido = yaml.safe_load(front_matter(campos))
        if not isinstance(releido, dict):
            raise ValueError("el front-matter no produce un mapa")
    except Exception as exc:  # noqa: BLE001 - cualquier fallo de YAML es un error
        hallazgos.append(Hallazgo(
            "error", nombre, f"El front-matter generado no es YAML valido: {exc}"))
    return hallazgos


def revisar(parejas: list[Pareja], tours: list[dict]) -> list[Hallazgo]:
    """
    Revisa el lote completo. `tours` es la lista plana que devuelve
    emparejado.aplicar (con id, sku y translation_of ya escritos).
    """
    hallazgos: list[Hallazgo] = []

    for pareja in parejas:
        for aviso in pareja.avisos:
            hallazgos.append(Hallazgo("aviso", pareja.base["title"], aviso))
        hallazgos.extend(_comparar_pareja(pareja))

    for campos in tours:
        hallazgos.extend(_revisar_tour(campos))

    # Unicidad: WordPress no admite dos tours del mismo idioma con igual slug,
    # y dos SKU repetidos enlazarian traducciones equivocadas.
    slugs = Counter((t.get("language"), t.get("slug")) for t in tours)
    for (idioma, slug_), veces in slugs.items():
        if veces > 1 and slug_:
            hallazgos.append(Hallazgo(
                "error", slug_,
                f"El slug '{slug_}' se repite {veces} veces en el idioma '{idioma}'. "
                f"WordPress le anadiria un sufijo y romperia el enlace entre idiomas.",
            ))

    ids = Counter(t.get("id") for t in tours)
    for identificador, veces in ids.items():
        if veces > 1:
            hallazgos.append(Hallazgo(
                "error", str(identificador), f"El id {identificador} se repite {veces} veces."))

    return hallazgos


def resumen(hallazgos: list[Hallazgo]) -> tuple[int, int]:
    """Devuelve (errores, avisos)."""
    errores = sum(h.nivel == "error" for h in hallazgos)
    return errores, len(hallazgos) - errores
