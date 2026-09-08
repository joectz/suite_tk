"""
entornos.py — Gestión de variables de entorno e interpolación {{variable}}.

Permite definir entornos como 'Local', 'Staging' o 'Producción' y reemplazar
automáticamente marcadores de posición en URLs, headers, body y parámetros.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CARPETA_DEFAULT = Path.cwd() / "colecciones_api"
ARCHIVO_ENTORNOS_DEFAULT = CARPETA_DEFAULT / "entornos.json"

REGEX_INTERPOLACION = re.compile(r"\{\{([\w\.-]+)\}\}")


@dataclass
class Variable:
    clave: str
    valor: str
    secreto: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"clave": self.clave, "valor": self.valor, "secreto": self.secreto}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Variable:
        return cls(
            clave=data.get("clave", ""),
            valor=data.get("valor", ""),
            secreto=bool(data.get("secreto", False)),
        )


@dataclass
class Entorno:
    nombre: str
    variables: list[Variable] = field(default_factory=list)

    def a_diccionario(self) -> dict[str, str]:
        return {v.clave: v.valor for v in self.variables if v.clave}

    def to_dict(self) -> dict[str, Any]:
        return {
            "nombre": self.nombre,
            "variables": [v.to_dict() for v in self.variables],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entorno:
        vars_list = [Variable.from_dict(v) for v in data.get("variables", [])]
        return cls(nombre=data.get("nombre", "Nuevo Entorno"), variables=vars_list)


def interpolar(texto: str, entorno: Entorno | dict[str, str] | None) -> str:
    """
    Reemplaza todas las ocurrencias de '{{variable}}' con su valor en el entorno activo.
    Si la variable no está definida, conserva el texto original '{{variable}}'.
    """
    if not texto or not entorno:
        return texto

    mapa = entorno.a_diccionario() if isinstance(entorno, Entorno) else entorno

    def reemplazo(match: re.Match) -> str:
        clave = match.group(1).strip()
        return mapa.get(clave, match.group(0))

    return REGEX_INTERPOLACION.sub(reemplazo, texto)


def entornos_por_defecto() -> list[Entorno]:
    """Crea una lista de entornos base para arrancar rápidamente."""
    return [
        Entorno(
            nombre="Local",
            variables=[
                Variable(clave="base_url", valor="http://localhost:8000"),
                Variable(clave="token", valor="mi-token-local-123", secreto=True),
            ],
        ),
        Entorno(
            nombre="Pruebas (JSONPlaceholder)",
            variables=[
                Variable(clave="base_url", valor=""),
                Variable(clave="post_id", valor="1"),
            ],
        ),
        Entorno(
            nombre="Producción",
            variables=[
                Variable(clave="base_url", valor="https://api.ejemplo.com"),
                Variable(clave="api_key", valor="sk_live_abc123", secreto=True),
            ],
        ),
    ]


def guardar_entornos(
    entornos: list[Entorno], ruta_archivo: Path | str | None = None
) -> Path:
    """Guarda los entornos en un archivo JSON en ./colecciones_api/entornos.json."""
    ruta = Path(ruta_archivo) if ruta_archivo else ARCHIVO_ENTORNOS_DEFAULT
    ruta.parent.mkdir(parents=True, exist_ok=True)

    data = [e.to_dict() for e in entornos]
    ruta.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return ruta


def cargar_entornos(ruta_archivo: Path | str | None = None) -> list[Entorno]:
    """Carga los entornos desde JSON; si no existe, devuelve y crea los entornos por defecto."""
    ruta = Path(ruta_archivo) if ruta_archivo else ARCHIVO_ENTORNOS_DEFAULT
    if not ruta.exists():
        defs = entornos_por_defecto()
        try:
            guardar_entornos(defs, ruta)
        except Exception:
            pass
        return defs

    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [Entorno.from_dict(item) for item in data]
    except Exception:
        pass

    return entornos_por_defecto()
