"""
colecciones.py — Manejo de Colecciones, Carpetas, Peticiones e importación del Mapeador.

Sigue la convención del proyecto:
  - Guarda en ./colecciones_api/<nombre>.json en el directorio de trabajo local.
  - Permite importar directamente el historial de páginas rastreadas por el Mapeador de URLs.
  - Soporta importación de comandos cURL y exportación compatible con Postman v2.1.
"""

from __future__ import annotations

import json
import re
import shlex
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

CARPETA_DEFAULT = Path.cwd() / "colecciones_api"


@dataclass
class Peticion:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    nombre: str = "Nueva Petición"
    metodo: str = "GET"
    url: str = ""
    params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body_tipo: str = "none"  # "none", "json", "form", "raw"
    body_contenido: str = ""
    auth_tipo: str = "none"  # "none", "bearer", "basic", "api_key", "oauth2"
    auth_datos: dict[str, str] = field(default_factory=dict)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    descripcion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Peticion:
        return cls(
            id=d.get("id", str(uuid.uuid4())[:8]),
            nombre=d.get("nombre", "Petición"),
            metodo=d.get("metodo", "GET").upper(),
            url=d.get("url", ""),
            params=dict(d.get("params", {})),
            headers=dict(d.get("headers", {})),
            body_tipo=d.get("body_tipo", "none"),
            body_contenido=str(d.get("body_contenido", "")),
            auth_tipo=d.get("auth_tipo", "none"),
            auth_datos=dict(d.get("auth_datos", {})),
            assertions=list(d.get("assertions", [])),
            descripcion=d.get("descripcion", ""),
        )


@dataclass
class CarpetaPeticiones:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    nombre: str = "Carpeta"
    peticiones: list[Peticion] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "peticiones": [p.to_dict() for p in self.peticiones],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CarpetaPeticiones:
        pets = [Peticion.from_dict(p) for p in d.get("peticiones", [])]
        return cls(
            id=d.get("id", str(uuid.uuid4())[:8]),
            nombre=d.get("nombre", "Carpeta"),
            peticiones=pets,
        )


@dataclass
class Coleccion:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    nombre: str = "Mi Colección de APIs"
    descripcion: str = ""
    carpetas: list[CarpetaPeticiones] = field(default_factory=list)
    peticiones_raiz: list[Peticion] = field(default_factory=list)

    @property
    def total_peticiones(self) -> int:
        return len(self.peticiones_raiz) + sum(len(c.peticiones) for c in self.carpetas)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "carpetas": [c.to_dict() for c in self.carpetas],
            "peticiones_raiz": [p.to_dict() for p in self.peticiones_raiz],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Coleccion:
        carps = [CarpetaPeticiones.from_dict(c) for c in d.get("carpetas", [])]
        pets = [Peticion.from_dict(p) for p in d.get("peticiones_raiz", [])]
        return cls(
            id=d.get("id", str(uuid.uuid4())[:8]),
            nombre=d.get("nombre", "Colección"),
            descripcion=d.get("descripcion", ""),
            carpetas=carps,
            peticiones_raiz=pets,
        )


def _sanitizar_nombre_archivo(nombre: str) -> str:
    s = re.sub(r"[^\w\s-]", "", nombre.lower()).strip()
    return re.sub(r"[-\s]+", "_", s) or "coleccion"


def guardar_coleccion(
    coleccion: Coleccion, ruta_destino: Path | str | None = None
) -> Path:
    """Guarda una colección en formato JSON en ./colecciones_api/<nombre>.json."""
    if ruta_destino:
        ruta = Path(ruta_destino)
    else:
        CARPETA_DEFAULT.mkdir(parents=True, exist_ok=True)
        nom_file = _sanitizar_nombre_archivo(coleccion.nombre)
        ruta = CARPETA_DEFAULT / f"{nom_file}.json"

    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps(coleccion.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return ruta


def cargar_coleccion(ruta_archivo: Path | str) -> Coleccion:
    """Lee y carga una colección desde un archivo JSON."""
    ruta = Path(ruta_archivo)
    data = json.loads(ruta.read_text(encoding="utf-8"))
    return Coleccion.from_dict(data)


def listar_colecciones_locales(
    carpeta: Path | str | None = None,
) -> list[dict[str, Any]]:
    """
    Lista las colecciones guardadas en ./colecciones_api/*.json
    omitiendo entornos.json e historial.json.
    """
    base = Path(carpeta) if carpeta else CARPETA_DEFAULT
    if not base.exists():
        return []

    resultado = []
    for f in base.glob("*.json"):
        if f.name in ("entornos.json", "historial.json"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "nombre" in data and ("carpetas" in data or "peticiones_raiz" in data):
                col = Coleccion.from_dict(data)
                resultado.append(
                    {
                        "nombre": col.nombre,
                        "ruta": str(f.resolve()),
                        "total_peticiones": col.total_peticiones,
                        "coleccion": col,
                    }
                )
        except Exception:
            pass

    return sorted(resultado, key=lambda x: x["nombre"].lower())


# --------------------------------------------------------------------------- #
# Integración con el Mapeador de URLs
# --------------------------------------------------------------------------- #

PATRONES_API = ("api", "v1", "v2", "v3", "graphql", "rest", "data", "json", "endpoint")


def importar_desde_mapeador(
    ruta_json_mapeador: Path | str, solo_apis: bool = False
) -> Coleccion:
    """
    Lee el archivo generado por el Mapeador de URLs (TMPDIR/mapeador_urls/<dominio>.json)
    y lo convierte en una Colección organizada de peticiones HTTP.
    """
    ruta = Path(ruta_json_mapeador)
    nombre_sitio = ruta.stem
    items: list[dict] = []

    if ruta.suffix == ".jsonl":
        with open(ruta, "r", encoding="utf-8") as f:
            for linea in f:
                if linea.strip():
                    try:
                        items.append(json.loads(linea))
                    except Exception:
                        pass
    else:
        try:
            items = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception:
            items = []

    peticiones_api: list[Peticion] = []
    peticiones_generales: list[Peticion] = []

    for item in items:
        url = item.get("url", "")
        if not url:
            continue

        titulo = item.get("titulo") or ""
        path = urlparse(url).path.lower()

        # Determinar si parece un endpoint de API
        es_api = any(pat in path for pat in PATRONES_API)
        if solo_apis and not es_api:
            continue

        nombre_peticion = titulo if titulo and len(titulo) < 40 else (path or "/")
        if es_api:
            nombre_peticion = f"[API] {path}"

        p = Peticion(
            nombre=nombre_peticion,
            metodo="GET",
            url=url,
            descripcion=f"Importado del Mapeador de URLs (Estado previo: {item.get('status', 200)})",
            assertions=[
                {"tipo": "status", "operador": "==", "valor": "200", "propiedad": ""}
            ],
        )

        if es_api:
            peticiones_api.append(p)
        else:
            peticiones_generales.append(p)

    carpetas = []
    if peticiones_api:
        carpetas.append(
            CarpetaPeticiones(nombre="Endpoints de API", peticiones=peticiones_api)
        )
    if peticiones_generales:
        carpetas.append(
            CarpetaPeticiones(nombre="Páginas Web", peticiones=peticiones_generales)
        )

    return Coleccion(
        nombre=f"Rastreo: {nombre_sitio}",
        descripcion=f"Colección generada automáticamente desde el Mapeador de URLs para {nombre_sitio}",
        carpetas=carpetas,
        peticiones_raiz=[],
    )


# --------------------------------------------------------------------------- #
# Importación desde cURL
# --------------------------------------------------------------------------- #


def importar_desde_curl(comando_curl: str) -> Peticion:
    """
    Parsea un comando curl y genera una Peticion con método, headers, URL y body.
    """
    tokens = shlex.split(comando_curl.replace("\\\n", " ").replace("\\\r\n", " "))
    metodo = "GET"
    url = ""
    headers = {}
    body_contenido = ""
    body_tipo = "none"

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("-X", "--request") and i + 1 < len(tokens):
            metodo = tokens[i + 1].upper()
            i += 2
        elif token in ("-H", "--header") and i + 1 < len(tokens):
            hdr_str = tokens[i + 1]
            if ":" in hdr_str:
                k, v = hdr_str.split(":", 1)
                headers[k.strip()] = v.strip()
            i += 2
        elif token in ("-d", "--data", "--data-raw", "--data-binary") and i + 1 < len(
            tokens
        ):
            body_contenido = tokens[i + 1]
            body_tipo = (
                "json"
                if "application/json" in headers.get("Content-Type", "").lower()
                else "raw"
            )
            if metodo == "GET":
                metodo = "POST"
            i += 2
        elif token.startswith("http://") or token.startswith("https://"):
            url = token
            i += 1
        else:
            i += 1

    # Extraer parámetros de la URL si existen
    params = {}
    if "?" in url:
        u_base, q_str = url.split("?", 1)
        for k, v_list in parse_qs(q_str).items():
            params[k] = v_list[0] if v_list else ""
        url = u_base

    return Peticion(
        nombre=f"{metodo} {urlparse(url).path or '/'}",
        metodo=metodo,
        url=url,
        params=params,
        headers=headers,
        body_tipo=body_tipo,
        body_contenido=body_contenido,
    )


# --------------------------------------------------------------------------- #
# Exportación Postman v2.1
# --------------------------------------------------------------------------- #


def exportar_postman(coleccion: Coleccion) -> dict[str, Any]:
    """Genera un archivo JSON compatible con Postman Collection v2.1."""

    def _peticion_a_postman(p: Peticion) -> dict:
        headers_list = [
            {"key": k, "value": v, "type": "text"} for k, v in p.headers.items()
        ]
        body_obj: dict[str, Any] = {"mode": "raw", "raw": ""}
        if p.body_tipo == "json":
            body_obj = {
                "mode": "raw",
                "raw": p.body_contenido,
                "options": {"raw": {"language": "json"}},
            }
        elif p.body_tipo == "form":
            body_obj = {"mode": "urlencoded", "urlencoded": []}

        return {
            "name": p.nombre,
            "request": {
                "method": p.metodo,
                "header": headers_list,
                "body": body_obj,
                "url": {
                    "raw": p.url,
                    "host": [urlparse(p.url).netloc],
                    "path": [x for x in urlparse(p.url).path.split("/") if x],
                },
                "description": p.descripcion,
            },
        }

    items = []
    for p in coleccion.peticiones_raiz:
        items.append(_peticion_a_postman(p))

    for c in coleccion.carpetas:
        items.append(
            {
                "name": c.nombre,
                "item": [_peticion_a_postman(p) for p in c.peticiones],
            }
        )

    return {
        "info": {
            "_postman_id": coleccion.id,
            "name": coleccion.nombre,
            "description": coleccion.descripcion,
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
    }
