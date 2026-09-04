"""
conversion.py — Orquesta el proceso completo, sin tocar la interfaz.

Es la fachada que usa pagina.py: recibe rutas de archivos y devuelve el
Markdown mas los hallazgos del validador. Estando separada de NiceGUI, todo el
flujo se puede probar desde la linea de comandos sin abrir la ventana (ver
__main__ al final).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import emision, emparejado, validacion
from .analisis import a_campos, segmentar
from .extraccion import ErrorExtraccion, extraer
from .perfil import cargar


@dataclass
class Resultado:
    markdown: str = ""
    tours: list[dict] = field(default_factory=list)
    parejas: list[emparejado.Pareja] = field(default_factory=list)
    hallazgos: list[validacion.Hallazgo] = field(default_factory=list)
    rango_ids: tuple[int, int] = (0, 0)

    @property
    def errores(self) -> int:
        return validacion.resumen(self.hallazgos)[0]

    @property
    def avisos(self) -> int:
        return validacion.resumen(self.hallazgos)[1]


def leer_documento(ruta: str | Path, idioma: str, perfil: dict) -> list[dict]:
    """Un documento -> lista de tours en forma de campos TourKit."""
    crudos = segmentar(extraer(ruta), idioma)
    if not crudos:
        raise ErrorExtraccion(
            f"No se reconocio ningun tour en '{Path(ruta).name}'. Cada tour debe "
            f"empezar con su numero y titulo en mayusculas, por ejemplo "
            f"'1. TOUR ISLAS FLOTANTES PUNO'."
        )
    return [a_campos(t, perfil) for t in crudos]


def convertir(
    ruta_base: str | Path,
    idioma_base: str,
    ruta_traduccion: str | Path | None = None,
    idioma_traduccion: str | None = None,
    perfil: dict | None = None,
) -> Resultado:
    """
    Convierte uno o dos documentos al Markdown de importacion.

    Con un solo documento se exporta ese idioma y `translation_of` queda vacio.
    Con dos, se emparejan por posicion y se enlazan por SKU.
    """
    perfil = perfil or cargar()

    base = leer_documento(ruta_base, idioma_base, perfil)
    traducciones: list[dict] = []
    if ruta_traduccion and idioma_traduccion:
        traducciones = leer_documento(ruta_traduccion, idioma_traduccion, perfil)

    parejas = emparejado.emparejar(base, traducciones)
    id_inicial = int(perfil.get("id_inicial", 2000))
    tours = emparejado.aplicar(parejas, id_inicial)

    return Resultado(
        markdown=emision.documento(tours, idioma_base),
        tours=tours,
        parejas=parejas,
        hallazgos=validacion.revisar(parejas, tours),
        rango_ids=emparejado.rango_ids(parejas, id_inicial),
    )


if __name__ == "__main__":  # pragma: no cover - utilidad de diagnostico
    import sys

    if len(sys.argv) < 3:
        print("uso: python -m herramientas.tourkit_md.conversion "
              "<doc_base> <idioma_base> [<doc_traduccion> <idioma_traduccion>]")
        raise SystemExit(2)

    resultado = convertir(*sys.argv[1:])
    for hallazgo in resultado.hallazgos:
        print(f"[{hallazgo.nivel:5}] {hallazgo.tour[:45]:45} {hallazgo.mensaje}")
    print(f"\n{len(resultado.tours)} tours | ids {resultado.rango_ids[0]}-"
          f"{resultado.rango_ids[1]} | {resultado.errores} errores, "
          f"{resultado.avisos} avisos")
