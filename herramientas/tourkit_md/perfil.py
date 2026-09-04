"""
perfil.py — Valores que no estan en el PDF y se repiten en todos los tours.

El documento de origen no dice cual es el tamano de grupo, ni las categorias,
ni desde donde se recoge al pasajero. Son datos de la agencia, iguales para los
26 tours, y escribirlos a mano 26 veces es justo lo que esta herramienta viene
a evitar. Se editan una vez en la pantalla de la herramienta y quedan
guardados en JSON para la proxima corrida.

Se guarda junto al ejecutable/proyecto en .tourkit-perfil.json. Si el archivo
no existe o esta corrupto se usan los valores por defecto sin fallar: perder el
perfil no debe impedir convertir.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ARCHIVO = Path(".tourkit-perfil.json")

POR_DEFECTO: dict[str, Any] = {
    # Numeracion de IDs de WordPress. Cada pareja de idiomas ocupa dos IDs
    # consecutivos (el idioma base y su traduccion), igual que en la
    # estructura de referencia.
    "id_inicial": 2000,
    "status": "publish",
    "tour_type": "group",
    "currency": "USD",
    "group_min": 2,
    "group_max": 16,
    "child_age_max": 11,
    "deposit_percent": 0,
    "tax_included": 1,
    "availability_type": "daily",
    "min_advance_days": 1,
    "inherit_global": 1,
    # Lo que cambia de un idioma a otro y no se puede deducir del texto.
    "por_idioma": {
        "es": {
            "categories": ["Aventura", "Cultural"],
            "start_point": "Hotel en Puno",
            "end_point": "Plaza de Armas de Puno",
        },
        "en": {
            "categories": ["Adventure", "Cultural"],
            "start_point": "Hotel in Puno",
            "end_point": "Plaza de Armas in Puno",
        },
    },
}


def cargar(ruta: Path | None = None) -> dict[str, Any]:
    """Lee el perfil guardado, completando con los valores por defecto lo que falte."""
    ruta = ruta or ARCHIVO
    perfil = json.loads(json.dumps(POR_DEFECTO))  # copia profunda barata
    try:
        guardado = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return perfil

    for clave, valor in guardado.items():
        if clave == "por_idioma" and isinstance(valor, dict):
            for idioma, campos in valor.items():
                destino = perfil["por_idioma"].setdefault(idioma, {})
                for campo, contenido in (campos or {}).items():
                    if contenido is not None:
                        destino[campo] = contenido
        elif valor is not None:
            # Un null en el archivo se ignora y gana el valor por defecto. Sin
            # esto, un perfil guardado a medias (por ejemplo con los campos
            # numericos vacios) dejaba group_min en None y acababa escrito como
            # la cadena "None" en el Markdown.
            perfil[clave] = valor
    return perfil


def guardar(perfil: dict[str, Any], ruta: Path | None = None) -> None:
    ruta = ruta or ARCHIVO
    ruta.write_text(
        json.dumps(perfil, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
