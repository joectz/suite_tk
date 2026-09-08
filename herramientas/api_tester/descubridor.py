"""
descubridor.py — Motor de auto-descubrimiento de APIs y rutas HTTP.

Detecta automáticamente todos los endpoints y métodos permitidos (GET, POST, PUT, DELETE)
utilizando tres estrategias:
  1. Descubrimiento nativo de WordPress REST API (/wp-json).
  2. Especificaciones estándar OpenAPI / Swagger (/openapi.json, /swagger.json).
  3. Sondeo HTTP (OPTIONS / HEAD y cabeceras 'Allow') en rutas comunes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from .colecciones import CarpetaPeticiones, Coleccion, Peticion


@dataclass
class RutaDescubierta:
    """Representa un endpoint y método HTTP descubierto automáticamente."""
    ruta: str                   # Ej: "/wp/v2/posts" o "/api/users"
    metodo: str                 # "GET", "POST", "PUT", "DELETE", etc.
    url_completa: str           # "https://sitio.com/wp-json/wp/v2/posts"
    resumen: str = ""           # Título o descripción del endpoint
    grupo: str = "General"      # Namespace o Tag (ej: "Artículos", "wp/v2")
    parametros: list[str] = field(default_factory=list)
    requiere_auth: bool = False
    body_ejemplo: str = ""


@dataclass
class ResultadoDescubrimiento:
    """Resultado global del proceso de escaneo de un sitio o API."""
    url_base: str
    exito: bool = False
    tipo_detectado: str = "No detectado"  # "WordPress REST API", "OpenAPI / Swagger", "Sondeo HTTP"
    titulo_sitio: str = ""
    descripcion_sitio: str = ""
    rutas: list[RutaDescubierta] = field(default_factory=list)
    namespaces_o_tags: list[str] = field(default_factory=list)
    tiempo_ms: float = 0.0
    error: str | None = None

    @property
    def total_rutas(self) -> int:
        return len(self.rutas)

    @property
    def total_metodos(self) -> dict[str, int]:
        conteo: dict[str, int] = {}
        for r in self.rutas:
            conteo[r.metodo] = conteo.get(r.metodo, 0) + 1
        return conteo


# --------------------------------------------------------------------------- #
# Helpers de Limpieza de Expresiones Regulares en Rutas
# --------------------------------------------------------------------------- #

def _simplificar_ruta_regex(ruta: str) -> str:
    """
    Convierte rutas con regex estilo WordPress:
      '/wp/v2/posts/(?P<id>[\\d]+)' -> '/wp/v2/posts/{id}'
      '/wp/v2/categories/(?P<id>\\d+)' -> '/wp/v2/categories/{id}'
    """
    # Reemplazar (?P<param>...) por {param}
    ruta_limpia = re.sub(r"\(\?P<(\w+)>[^)]+\)", r"{\1}", ruta)
    # Reemplazar agrupaciones comunes tipo (\\d+)
    ruta_limpia = re.sub(r"\([^)]+\)", "{id}", ruta_limpia)
    return ruta_limpia


def _normalizar_url(url: str) -> str:
    """Asegura que la URL tenga esquema http o https y remueve barras finales."""
    u = url.strip()
    if not u:
        return ""
    if not (u.startswith("http://") or u.startswith("https://")):
        u = "https://" + u
    return u.rstrip("/")


# --------------------------------------------------------------------------- #
# 1. Estrategia: Descubrimiento Nativo de WordPress (/wp-json)
# --------------------------------------------------------------------------- #

async def _escanear_wordpress(
    cliente: httpx.AsyncClient, url_base: str
) -> ResultadoDescubrimiento | None:
    """
    Intenta consultar el endpoint /wp-json de WordPress.
    Si responde con la estructura estándar de 'routes', extrae todos los endpoints.
    """
    candidatas = [
        urljoin(url_base + "/", "wp-json"),
        urljoin(url_base + "/", "?rest_route=/"),
    ]

    for url_wp in candidatas:
        try:
            resp = await cliente.get(url_wp, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                continue

            try:
                data = resp.json()
            except Exception:
                continue

            if isinstance(data, dict) and "routes" in data and isinstance(data["routes"], dict):
                titulo = data.get("name") or "Sitio WordPress"
                desc = data.get("description") or "WordPress REST API"
                namespaces = list(data.get("namespaces", []))

                rutas_descubiertas: list[RutaDescubierta] = []

                for path_raw, info_ruta in data["routes"].items():
                    if not isinstance(info_ruta, dict):
                        continue

                    path_legible = _simplificar_ruta_regex(path_raw)
                    url_full = urljoin(url_wp.split("?")[0].rstrip("/") + "/", path_legible.lstrip("/"))

                    # Determinar grupo / namespace
                    grupo = "General"
                    if "namespace" in info_ruta and info_ruta["namespace"]:
                        grupo = info_ruta["namespace"]
                    elif path_legible.startswith("/wp/v2/"):
                        # Agrupar por el recurso principal (/wp/v2/posts -> Posts)
                        partes = path_legible.split("/")
                        if len(partes) >= 4:
                            grupo = partes[3].capitalize()
                        else:
                            grupo = "wp/v2"

                    # Obtener métodos permitidos
                    metodos_permitidos = info_ruta.get("methods", [])
                    if not metodos_permitidos:
                        # Si no están directos, revisar en 'endpoints'
                        endpoints = info_ruta.get("endpoints", [])
                        for ep in endpoints:
                            if isinstance(ep, dict):
                                metodos_permitidos.extend(ep.get("methods", []))

                    # Limpiar y normalizar métodos (GET, POST, etc.)
                    metodos_unicos = []
                    for m in metodos_permitidos:
                        m_clean = str(m).upper().strip()
                        if m_clean in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                            if m_clean not in metodos_unicos:
                                metodos_unicos.append(m_clean)

                    if not metodos_unicos:
                        metodos_unicos = ["GET"]

                    # Extraer nombres de argumentos
                    params = []
                    for ep in info_ruta.get("endpoints", []):
                        if isinstance(ep, dict) and "args" in ep:
                            params.extend(list(ep["args"].keys()))
                    params = list(dict.fromkeys(params))[:8]  # deduplicar y limitar

                    for m in metodos_unicos:
                        rutas_descubiertas.append(
                            RutaDescubierta(
                                ruta=path_legible,
                                metodo=m,
                                url_completa=url_full,
                                resumen=f"{m} {path_legible}",
                                grupo=grupo,
                                parametros=params,
                                requiere_auth=(m in ("POST", "PUT", "PATCH", "DELETE")),
                            )
                        )

                if rutas_descubiertas:
                    return ResultadoDescubrimiento(
                        url_base=url_base,
                        exito=True,
                        tipo_detectado="WordPress REST API",
                        titulo_sitio=titulo,
                        descripcion_sitio=desc,
                        rutas=rutas_descubiertas,
                        namespaces_o_tags=namespaces or ["wp/v2"],
                    )

        except Exception:
            continue

    return None


# --------------------------------------------------------------------------- #
# 2. Estrategia: Detección OpenAPI / Swagger (/openapi.json, etc.)
# --------------------------------------------------------------------------- #

async def _escanear_openapi(
    cliente: httpx.AsyncClient, url_base: str
) -> ResultadoDescubrimiento | None:
    """
    Busca archivos de especificación OpenAPI o Swagger y extrae endpoints.
    """
    rutas_candidatas = [
        "/openapi.json",
        "/swagger.json",
        "/api/openapi.json",
        "/api/swagger.json",
        "/api/v1/openapi.json",
        "/api/v1/swagger.json",
        "/v2/swagger.json",
    ]

    for c in rutas_candidatas:
        url_doc = urljoin(url_base + "/", c.lstrip("/"))
        try:
            resp = await cliente.get(url_doc, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                continue

            try:
                data = resp.json()
            except Exception:
                continue

            if isinstance(data, dict) and "paths" in data and isinstance(data["paths"], dict):
                info = data.get("info", {})
                titulo = info.get("title", "API REST")
                desc = info.get("description", "Documentación OpenAPI detectada")

                rutas_descubiertas: list[RutaDescubierta] = []
                tags_detectados = set()

                for path_url, path_item in data["paths"].items():
                    if not isinstance(path_item, dict):
                        continue

                    for metodo_key in ("get", "post", "put", "patch", "delete", "head", "options"):
                        if metodo_key in path_item:
                            op = path_item[metodo_key]
                            if not isinstance(op, dict):
                                continue

                            m_upper = metodo_key.upper()
                            tags = op.get("tags") or ["General"]
                            grupo = str(tags[0]) if tags else "General"
                            tags_detectados.add(grupo)

                            resumen = op.get("summary") or op.get("description") or f"{m_upper} {path_url}"
                            if len(resumen) > 60:
                                resumen = resumen[:57] + "..."

                            # Parametros
                            params_nombres = []
                            for p in op.get("parameters", []):
                                if isinstance(p, dict) and "name" in p:
                                    params_nombres.append(p["name"])

                            url_full = urljoin(url_base + "/", path_url.lstrip("/"))

                            rutas_descubiertas.append(
                                RutaDescubierta(
                                    ruta=path_url,
                                    metodo=m_upper,
                                    url_completa=url_full,
                                    resumen=resumen,
                                    grupo=grupo,
                                    parametros=params_nombres[:8],
                                    requiere_auth=bool(op.get("security")),
                                )
                            )

                if rutas_descubiertas:
                    return ResultadoDescubrimiento(
                        url_base=url_base,
                        exito=True,
                        tipo_detectado="OpenAPI / Swagger",
                        titulo_sitio=titulo,
                        descripcion_sitio=desc,
                        rutas=rutas_descubiertas,
                        namespaces_o_tags=sorted(list(tags_detectados)),
                    )

        except Exception:
            continue

    return None


# --------------------------------------------------------------------------- #
# 3. Estrategia: Sondeo HTTP y Rutas Comunes con OPTIONS / HEAD
# --------------------------------------------------------------------------- #

RUTAS_COMUNES_SONDEO = [
    "/api",
    "/api/v1",
    "/api/v2",
    "/api/posts",
    "/api/articulos",
    "/api/users",
    "/api/usuarios",
    "/api/login",
    "/api/auth",
    "/health",
    "/status",
    "/ping",
]


async def _escanear_sondeo_comun(
    cliente: httpx.AsyncClient, url_base: str
) -> ResultadoDescubrimiento | None:
    """
    Sondea rutas habituales de APIs con OPTIONS o GET para ver cuáles responden
    y qué métodos declaran en la cabecera 'Allow'.
    """
    rutas_encontradas: list[RutaDescubierta] = []

    for r_path in RUTAS_COMUNES_SONDEO:
        url_dest = urljoin(url_base + "/", r_path.lstrip("/"))
        try:
            # Primero intentar OPTIONS para ver los métodos soportados
            resp = await cliente.options(url_dest)
            metodos = []

            allow_header = resp.headers.get("allow") or resp.headers.get("access-control-allow-methods")
            if allow_header:
                for m in allow_header.split(","):
                    m_clean = m.strip().upper()
                    if m_clean in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
                        metodos.append(m_clean)

            # Si no devolvió cabecera Allow pero el status no fue 404
            if not metodos:
                resp_get = await cliente.get(url_dest)
                if resp_get.status_code in (200, 201, 204, 401, 403):
                    metodos = ["GET"]

            for m in metodos:
                rutas_encontradas.append(
                    RutaDescubierta(
                        ruta=r_path,
                        metodo=m,
                        url_completa=url_dest,
                        resumen=f"Endpoint detectado ({m})",
                        grupo="API Detectada",
                    )
                )
        except Exception:
            continue

    if rutas_encontradas:
        return ResultadoDescubrimiento(
            url_base=url_base,
            exito=True,
            tipo_detectado="Sondeo HTTP Activo",
            titulo_sitio=f"API en {urlparse(url_base).netloc}",
            descripcion_sitio="Endpoints descubiertos mediante sondeo HTTP y cabeceras Allow",
            rutas=rutas_encontradas,
            namespaces_o_tags=["API Detectada"],
        )

    return None


# --------------------------------------------------------------------------- #
# Función Principal de Auto-Descubrimiento
# --------------------------------------------------------------------------- #

async def descubrir_rutas_api(
    url: str,
    verificar_ssl: bool = True,
    timeout: float = 12.0,
) -> ResultadoDescubrimiento:
    """
    Ejecuta el auto-descubrimiento secuencial en la URL provista:
      1. Intenta WordPress REST API (/wp-json).
      2. Si no es WP, intenta OpenAPI / Swagger.
      3. Si no, ejecuta sondeo HTTP en rutas comunes.
    """
    import time

    url_base = _normalizar_url(url)
    if not url_base:
        return ResultadoDescubrimiento(
            url_base=url,
            exito=False,
            error="La URL ingresada no es válida.",
        )

    t0 = time.perf_counter()

    async with httpx.AsyncClient(
        verify=verificar_ssl,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "APITester-Suite/1.0 (Descubridor)"},
    ) as cliente:

        # 1. Intentar WordPress
        res_wp = await _escanear_wordpress(cliente, url_base)
        if res_wp:
            res_wp.tiempo_ms = round((time.perf_counter() - t0) * 1000, 1)
            return res_wp

        # 2. Intentar OpenAPI / Swagger
        res_oa = await _escanear_openapi(cliente, url_base)
        if res_oa:
            res_oa.tiempo_ms = round((time.perf_counter() - t0) * 1000, 1)
            return res_oa

        # 3. Intentar Sondeo Activo
        res_sondeo = await _escanear_sondeo_comun(cliente, url_base)
        if res_sondeo:
            res_sondeo.tiempo_ms = round((time.perf_counter() - t0) * 1000, 1)
            return res_sondeo

    t_total = round((time.perf_counter() - t0) * 1000, 1)
    return ResultadoDescubrimiento(
        url_base=url_base,
        exito=False,
        tiempo_ms=t_total,
        error=(
            "No se detectó una API pública reconocida. El sitio no parece ser WordPress, "
            "no expone OpenAPI/Swagger públicamente ni respondió en rutas de API estándar."
        ),
    )


# --------------------------------------------------------------------------- #
# Conversión a Colección de la Suite
# --------------------------------------------------------------------------- #

def convertir_a_coleccion(
    resultado: ResultadoDescubrimiento,
    nombre_coleccion: str | None = None,
    rutas_seleccionadas: list[RutaDescubierta] | None = None,
) -> Coleccion:
    """
    Transforma los endpoints descubiertos en un objeto Coleccion completo,
    organizando las peticiones en Carpetas según su Namespace o Tag.
    """
    rutas_a_procesar = rutas_seleccionadas if rutas_seleccionadas is not None else resultado.rutas

    # Agrupar rutas por su atributo 'grupo'
    grupos: dict[str, list[Peticion]] = {}

    for r in rutas_a_procesar:
        p = Peticion(
            nombre=f"{r.metodo} {r.ruta}",
            metodo=r.metodo,
            url=r.url_completa,
            descripcion=r.resumen,
            headers={"Accept": "application/json"} if r.metodo == "GET" else {"Content-Type": "application/json"},
            body_tipo="json" if r.metodo in ("POST", "PUT", "PATCH") else "none",
            body_contenido=r.body_ejemplo or ("{\n  \n}" if r.metodo in ("POST", "PUT", "PATCH") else ""),
            assertions=[
                {"tipo": "status", "operador": "==", "valor": "200" if r.metodo == "GET" else "201", "propiedad": ""}
            ],
        )
        if r.grupo not in grupos:
            grupos[r.grupo] = []
        grupos[r.grupo].append(p)

    carpetas: list[CarpetaPeticiones] = []
    peticiones_raiz: list[Peticion] = []

    for nombre_grupo, lista_pets in grupos.items():
        if nombre_grupo == "General" and len(grupos) == 1:
            peticiones_raiz = lista_pets
        else:
            carpetas.append(CarpetaPeticiones(nombre=nombre_grupo, peticiones=lista_pets))

    nom = nombre_coleccion or f"API: {resultado.titulo_sitio or urlparse(resultado.url_base).netloc}"

    return Coleccion(
        nombre=nom,
        descripcion=f"Colección generada por Auto-Descubrimiento ({resultado.tipo_detectado}) para {resultado.url_base}",
        carpetas=carpetas,
        peticiones_raiz=peticiones_raiz,
    )
