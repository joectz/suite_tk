"""
emparejado.py — Relaciona el tour en un idioma con su traduccion y asigna
identificadores.

Como se emparejan:
    Por posicion ordinal. Los documentos vienen numerados 1..N en el mismo
    orden en ambos idiomas, asi que el tour #3 del PDF en espanol es la
    traduccion del tour #3 del PDF en ingles. Es automatico, pero no ciego: la
    pantalla de la herramienta muestra las parejas y permite recolocarlas antes
    de exportar.

Como quedan enlazadas para TourKit (siguiendo la estructura de referencia):
    - Los dos idiomas COMPARTEN el mismo `sku`, derivado del titulo del idioma
      base.
    - El idioma base lleva `translation_of: ''` (es el original).
    - La traduccion lleva `translation_of: <sku del original>`.

Como se numeran los `id`:
    Consecutivos a partir de un numero que elige el usuario, y contiguos por
    pareja: el original toma N y su traduccion N+1. Asi cada par queda junto y
    se lee de un vistazo, igual que el 281/282 de la referencia.

    Ojo con el numero inicial: en WordPress el ID es un AUTO_INCREMENT
    compartido por posts, revisiones, adjuntos y auto-drafts, asi que crece
    mas rapido de lo que parece. Conviene contrastar el rango que reporta
    rango_ids() con SELECT MAX(ID) FROM wp_posts antes de importar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .analisis import sin_tildes

# Palabras que no aportan nada a un SKU y solo lo alargan.
RUIDO_SKU = {
    "TOUR", "TOURS", "FULL", "DAY", "DAYS", "THE", "AND", "OF", "IN", "TO",
    "DE", "DEL", "LA", "LAS", "EL", "LOS", "Y", "A", "EN", "CON", "POR",
    "ISLAND", "ISLANDS", "ISLA", "ISLAS", "TRAVEL", "PERU",
}


@dataclass
class Pareja:
    """Un tour y su traduccion. `traduccion` es None cuando no tiene par."""

    base: dict
    traduccion: dict | None = None
    sku: str = ""
    avisos: list[str] = field(default_factory=list)


def sku_desde(titulo: str, maximo_palabras: int = 4) -> str:
    """
    Deriva un SKU legible del titulo: mayusculas, sin tildes, sin palabras de
    relleno y con guiones. "TOUR ISLAS FLOTANTES PUNO (UROS - AMANTANI)" ->
    "FLOTANTES-PUNO-UROS-AMANTANI".

    Es una propuesta, no una verdad: el SKU es la clave que une los idiomas y
    conviene revisarlo en pantalla antes de exportar.
    """
    limpio = re.sub(r"[^\w\s-]", " ", sin_tildes(titulo).upper())
    palabras = [p for p in limpio.split() if p and p not in RUIDO_SKU and not p.isdigit()]
    if not palabras:
        palabras = [p for p in limpio.split() if p] or ["TOUR"]
    return "-".join(palabras[:maximo_palabras])


def _unicos(skus: list[str]) -> list[str]:
    """Anade sufijo -2, -3... a los SKU repetidos, que romperian el enlace ES/EN."""
    vistos: dict[str, int] = {}
    salida = []
    for sku in skus:
        vistos[sku] = vistos.get(sku, 0) + 1
        salida.append(sku if vistos[sku] == 1 else f"{sku}-{vistos[sku]}")
    return salida


def emparejar(base: list[dict], traducciones: list[dict]) -> list[Pareja]:
    """
    Empareja por posicion y avisa de lo que quede suelto.

    Recibe y devuelve los diccionarios de campos que produce analisis.a_campos.
    """
    parejas: list[Pareja] = []
    for indice, tour_base in enumerate(base):
        par = traducciones[indice] if indice < len(traducciones) else None
        pareja = Pareja(base=tour_base, traduccion=par)
        if par is None and traducciones:
            pareja.avisos.append(
                f"'{tour_base['title']}' se queda sin traduccion: "
                f"el segundo documento tiene menos tours."
            )
        parejas.append(pareja)

    for sobrante in traducciones[len(base):]:
        pareja = Pareja(base=sobrante)
        pareja.avisos.append(
            f"'{sobrante['title']}' aparece solo en el segundo documento, "
            f"sin equivalente en el idioma base."
        )
        parejas.append(pareja)

    for pareja, sku in zip(parejas, _unicos([sku_desde(p.base["title"]) for p in parejas])):
        pareja.sku = sku
    return parejas


def aplicar(parejas: list[Pareja], id_inicial: int) -> list[dict]:
    """
    Escribe sku / translation_of / id sobre las parejas y devuelve la lista
    plana de tours lista para emitir, con el original siempre antes que su
    traduccion.
    """
    salida: list[dict] = []
    siguiente = int(id_inicial)

    for pareja in parejas:
        pareja.base["sku"] = pareja.sku
        pareja.base["translation_of"] = ""
        pareja.base["id"] = str(siguiente)
        salida.append(pareja.base)
        siguiente += 1

        if pareja.traduccion is not None:
            pareja.traduccion["sku"] = pareja.sku
            pareja.traduccion["translation_of"] = pareja.sku
            pareja.traduccion["id"] = str(siguiente)
            salida.append(pareja.traduccion)
            siguiente += 1

    return salida


def rango_ids(parejas: list[Pareja], id_inicial: int) -> tuple[int, int]:
    """Primer y ultimo ID que ocupara la exportacion, para contrastarlo con la BD."""
    total = sum(1 + (p.traduccion is not None) for p in parejas)
    if total == 0:
        return (id_inicial, id_inicial)
    return (id_inicial, id_inicial + total - 1)
