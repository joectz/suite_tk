"""
cliente.py — Motor de peticiones HTTP puro con httpx y evaluador de assertions.

No contiene lógica de interfaz gráfica; puede ser usado tanto por la UI de NiceGUI
como por el motor CLI (motor.py) y scripts de automatización.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

try:
    import h2

    SOPORTA_HTTP2 = True
except ImportError:
    SOPORTA_HTTP2 = False


@dataclass
class ResultadoAssertion:
    """Resultado individual de evaluar una assertion contra la respuesta HTTP."""

    nombre: str
    exito: bool
    esperado: str
    obtenido: str
    mensaje: str


@dataclass
class ResultadoPeticion:
    """Contenedor de todos los datos y métricas devueltos por una petición HTTP."""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body_text: str = ""
    json_data: Any | None = None
    tiempo_ms: float = 0.0
    tamaño_bytes: int = 0
    http_version: str = "HTTP/1.1"
    cookies: dict[str, str] = field(default_factory=dict)
    redirects: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    assertions: list[ResultadoAssertion] = field(default_factory=list)

    @property
    def exito(self) -> bool:
        return self.error is None and 200 <= self.status < 400

    @property
    def tamaño_kb(self) -> float:
        return round(self.tamaño_bytes / 1024, 2)

    @property
    def total_assertions_pasadas(self) -> int:
        return sum(1 for a in self.assertions if a.exito)


def _obtener_por_ruta_json(datos: Any, ruta: str) -> tuple[bool, Any]:
    """
    Navega una estructura JSON con notación de puntos (ej: 'user.role', 'data.items.0.id').
    Retorna (encontrado: bool, valor: Any).
    """
    if not ruta or ruta == "$":
        return True, datos

    partes = ruta.split(".")
    actual = datos
    for p in partes:
        if isinstance(actual, dict):
            if p in actual:
                actual = actual[p]
            else:
                return False, None
        elif isinstance(actual, list):
            try:
                idx = int(p)
                actual = actual[idx]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, actual


def evaluar_assertions(
    assertions: list[dict[str, Any]],
    status: int,
    headers: dict[str, str],
    body_text: str,
    json_data: Any | None,
    tiempo_ms: float,
    tamaño_bytes: int,
) -> list[ResultadoAssertion]:
    """
    Evalúa una lista de assertions contra la respuesta HTTP recibida.
    """
    resultados = []
    headers_lower = {k.lower(): v for k, v in headers.items()}

    for item in assertions:
        tipo = item.get(
            "tipo", "status"
        )  # status, header, body_json, body_texto, tiempo, tamaño
        op = item.get("operador", "==")  # ==, !=, >, <, >=, <=, contiene, existe
        valor_esp = str(item.get("valor", "")).strip()
        nombre_prop = item.get("propiedad", "").strip()

        exito = False
        obtenido = ""
        desc = ""

        try:
            if tipo == "status":
                desc = f"Status Code {op} {valor_esp}"
                obtenido = str(status)
                val_num = int(valor_esp)
                if op == "==":
                    exito = status == val_num
                elif op == "!=":
                    exito = status != val_num
                elif op == ">=":
                    exito = status >= val_num
                elif op == "<=":
                    exito = status <= val_num
                elif op == ">":
                    exito = status > val_num
                elif op == "<":
                    exito = status < val_num

            elif tipo == "tiempo":
                desc = f"Tiempo de respuesta {op} {valor_esp} ms"
                obtenido = f"{round(tiempo_ms, 1)} ms"
                val_num = float(valor_esp)
                if op == "<":
                    exito = tiempo_ms < val_num
                elif op == "<=":
                    exito = tiempo_ms <= val_num
                elif op == ">":
                    exito = tiempo_ms > val_num
                elif op == ">=":
                    exito = tiempo_ms >= val_num

            elif tipo == "tamaño":
                desc = f"Tamaño {op} {valor_esp} bytes"
                obtenido = f"{tamaño_bytes} B"
                val_num = int(valor_esp)
                if op == "<":
                    exito = tamaño_bytes < val_num
                elif op == "<=":
                    exito = tamaño_bytes <= val_num
                elif op == ">":
                    exito = tamaño_bytes > val_num

            elif tipo == "header":
                nom_h = nombre_prop.lower()
                desc = f"Header '{nombre_prop}' {op} '{valor_esp}'"
                val_h = headers_lower.get(nom_h)
                obtenido = str(val_h) if val_h is not None else "(no presente)"
                if op == "existe":
                    exito = nom_h in headers_lower
                elif op == "==":
                    exito = val_h == valor_esp
                elif op == "contiene":
                    exito = val_h is not None and valor_esp.lower() in val_h.lower()

            elif tipo == "body_json":
                desc = f"JSON '{nombre_prop}' {op} '{valor_esp}'"
                if json_data is None:
                    exito = False
                    obtenido = "(cuerpo no es JSON válido)"
                else:
                    hallado, val_j = _obtener_por_ruta_json(json_data, nombre_prop)
                    if not hallado:
                        exito = False
                        obtenido = "(campo no encontrado en JSON)"
                    else:
                        obtenido = str(val_j)
                        if op == "existe":
                            exito = True
                        elif op == "==":
                            # Comparar como string o tipo original
                            exito = str(val_j).strip().lower() == valor_esp.lower()
                        elif op == "!=":
                            exito = str(val_j).strip().lower() != valor_esp.lower()
                        elif op == "contiene":
                            exito = valor_esp.lower() in str(val_j).lower()

            elif tipo == "body_texto":
                desc = f"Cuerpo contiene '{valor_esp}'"
                obtenido = f"{len(body_text)} caracteres"
                if op == "contiene":
                    exito = valor_esp in body_text
                elif op == "no_contiene":
                    exito = valor_esp not in body_text

        except Exception as e:
            exito = False
            obtenido = f"Error: {e}"

        msg = f"{'PASÓ' if exito else 'FALLÓ'}: {desc} (obtenido: {obtenido})"
        resultados.append(
            ResultadoAssertion(
                nombre=desc,
                exito=exito,
                esperado=valor_esp,
                obtenido=obtenido,
                mensaje=msg,
            )
        )

    return resultados


async def enviar_peticion(
    metodo: str,
    url: str,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    body_tipo: str = "none",
    body_contenido: str | dict | None = None,
    auth_tipo: str = "none",
    auth_datos: dict[str, str] | None = None,
    timeout: float = 30.0,
    verificar_ssl: bool = True,
    seguir_redirecciones: bool = True,
    assertions: list[dict[str, Any]] | None = None,
) -> ResultadoPeticion:
    """
    Envía una petición HTTP asíncrona usando httpx sin bloquear el event loop.
    """
    metodo_limpio = metodo.strip().upper()
    url_limpia = url.strip()
    if not urlparse(url_limpia).scheme:
        url_limpia = f"https://{url_limpia}"

    hdrs = dict(headers or {})
    prms = dict(params or {})
    auth_obj = None

    # Configurar Autenticación
    if auth_tipo == "bearer" and auth_datos and auth_datos.get("token"):
        hdrs["Authorization"] = f"Bearer {auth_datos['token'].strip()}"
    elif auth_tipo == "basic" and auth_datos:
        auth_obj = httpx.BasicAuth(
            username=auth_datos.get("usuario", ""),
            password=auth_datos.get("password", ""),
        )
    elif auth_tipo == "api_key" and auth_datos:
        k_nom = auth_datos.get("nombre", "X-API-Key").strip()
        k_val = auth_datos.get("valor", "").strip()
        k_ubic = auth_datos.get("ubicacion", "header")  # "header" o "query"
        if k_ubic == "query":
            prms[k_nom] = k_val
        else:
            hdrs[k_nom] = k_val

    # Preparar el cuerpo según el tipo
    content = None
    data = None
    json_body = None

    if body_tipo == "json" and body_contenido:
        if isinstance(body_contenido, str):
            try:
                json_body = json.loads(body_contenido)
            except json.JSONDecodeError:
                content = body_contenido.encode("utf-8")
                if "content-type" not in {k.lower() for k in hdrs}:
                    hdrs["Content-Type"] = "application/json"
        else:
            json_body = body_contenido

    elif body_tipo == "form" and body_contenido:
        if isinstance(body_contenido, dict):
            data = body_contenido
        elif isinstance(body_contenido, str):
            # Líneas con formato clave=valor
            form_dict = {}
            for linea in body_contenido.splitlines():
                if "=" in linea:
                    k, v = linea.split("=", 1)
                    form_dict[k.strip()] = v.strip()
            data = form_dict

    elif body_tipo == "raw" and body_contenido:
        content = str(body_contenido).encode("utf-8")

    t_inicio = time.perf_counter()
    redirects_hist = []

    try:
        async with httpx.AsyncClient(
            verify=verificar_ssl,
            follow_redirects=seguir_redirecciones,
            timeout=httpx.Timeout(timeout),
            http2=SOPORTA_HTTP2,
        ) as client:
            resp = await client.request(
                method=metodo_limpio,
                url=url_limpia,
                params=prms,
                headers=hdrs,
                content=content,
                data=data,
                json=json_body,
                auth=auth_obj,
            )
            t_fin = time.perf_counter()
            elapsed_ms = (t_fin - t_inicio) * 1000.0

            # Guardar historial de redirecciones
            for r in resp.history:
                redirects_hist.append(
                    {
                        "url": str(r.url),
                        "status": r.status_code,
                    }
                )

            body_text = resp.text
            parsed_json = None
            try:
                parsed_json = resp.json()
            except Exception:
                pass

            resp_headers = dict(resp.headers)
            resp_cookies = dict(resp.cookies)
            resp_bytes = len(resp.content)
            http_version = getattr(resp, "http_version", "HTTP/1.1")

            # Evaluar assertions si fueron provistas
            res_assertions = []
            if assertions:
                res_assertions = evaluar_assertions(
                    assertions=assertions,
                    status=resp.status_code,
                    headers=resp_headers,
                    body_text=body_text,
                    json_data=parsed_json,
                    tiempo_ms=elapsed_ms,
                    tamaño_bytes=resp_bytes,
                )

            return ResultadoPeticion(
                status=resp.status_code,
                headers=resp_headers,
                body_text=body_text,
                json_data=parsed_json,
                tiempo_ms=round(elapsed_ms, 2),
                tamaño_bytes=resp_bytes,
                http_version=http_version,
                cookies=resp_cookies,
                redirects=redirects_hist,
                error=None,
                assertions=res_assertions,
            )

    except httpx.HTTPError as e:
        t_fin = time.perf_counter()
        elapsed_ms = (t_fin - t_inicio) * 1000.0
        return ResultadoPeticion(
            status=0,
            tiempo_ms=round(elapsed_ms, 2),
            error=str(e),
        )
    except Exception as e:
        t_fin = time.perf_counter()
        elapsed_ms = (t_fin - t_inicio) * 1000.0
        return ResultadoPeticion(
            status=0,
            tiempo_ms=round(elapsed_ms, 2),
            error=f"Error inesperado: {e}",
        )
