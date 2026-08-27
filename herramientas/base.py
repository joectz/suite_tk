"""Definicion de que es una "herramienta" dentro del menu principal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Herramienta:
    """
    Una entrada del menu principal.

    - id: identificador corto usado en "--worker <id>" y en el registro.
    - ruta: pagina NiceGUI donde vive esta herramienta (ej. "/mapeador-urls").
    - worker_main: punto de entrada que corre en el subproceso lanzado por
      core.procesos.comando_worker(id), si esta herramienta lo necesita.
      None si la herramienta no usa subprocesos.
    """

    id: str
    nombre: str
    descripcion: str
    icono: str
    ruta: str
    worker_main: Callable[[list[str] | None], int] | None = None
