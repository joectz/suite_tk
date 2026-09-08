"""
exportador.py — Generador de código equivalente (cURL, Python httpx/requests, JavaScript fetch).

Permite copiar al instante el snippet de código listo para ejecutar en terminal
o integrar en proyectos de frontend/backend.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

from .colecciones import Peticion
from .entornos import Entorno, interpolar


def _preparar_peticion(peticion: Peticion, entorno: Entorno | None = None) -> tuple[str, str, dict, dict, str]:
    """Interpola variables de entorno y resuelve método, url, params, headers y body."""
    url = interpolar(peticion.url, entorno)
    metodo = peticion.metodo.upper()
    headers = {interpolar(k, entorno): interpolar(v, entorno) for k, v in peticion.headers.items()}
    params = {interpolar(k, entorno): interpolar(v, entorno) for k, v in peticion.params.items()}
    body = interpolar(peticion.body_contenido, entorno)

    # Inyectar autenticación en headers/params si aplica
    if peticion.auth_tipo == "bearer" and peticion.auth_datos.get("token"):
        tok = interpolar(peticion.auth_datos["token"], entorno)
        headers["Authorization"] = f"Bearer {tok}"
    elif peticion.auth_tipo == "api_key" and peticion.auth_datos:
        k_nom = interpolar(peticion.auth_datos.get("nombre", "X-API-Key"), entorno)
        k_val = interpolar(peticion.auth_datos.get("valor", ""), entorno)
        if peticion.auth_datos.get("ubicacion") == "query":
            params[k_nom] = k_val
        else:
            headers[k_nom] = k_val

    return metodo, url, params, headers, body


def a_curl(peticion: Peticion, entorno: Entorno | None = None) -> str:
    """Genera el comando cURL equivalente listo para la terminal."""
    metodo, url, params, headers, body = _preparar_peticion(peticion, entorno)

    # Si hay query params, agregarlos a la URL
    if params:
        separador = "&" if "?" in url else "?"
        url = f"{url}{separador}{urlencode(params)}"

    lineas = [f"curl -X {metodo} '{url}'"]

    for k, v in headers.items():
        lineas.append(f"  -H '{k}: {v}'")

    if metodo not in ("GET", "HEAD") and body:
        # Escapar comillas simples
        body_escapado = body.replace("'", "'\\''")
        lineas.append(f"  -d '{body_escapado}'")

    return " \\\n".join(lineas)


def a_python_httpx(peticion: Peticion, entorno: Entorno | None = None) -> str:
    """Genera código Python asíncrono con httpx."""
    metodo, url, params, headers, body = _preparar_peticion(peticion, entorno)

    params_str = json.dumps(params, indent=4) if params else "None"
    headers_str = json.dumps(headers, indent=4) if headers else "None"

    body_code = ""
    body_arg = ""
    if metodo not in ("GET", "HEAD") and body:
        if peticion.body_tipo == "json":
            try:
                parsed = json.loads(body)
                body_code = f"payload = {json.dumps(parsed, indent=4)}\n"
                body_arg = "json=payload"
            except Exception:
                body_code = f"payload = {repr(body)}\n"
                body_arg = "content=payload"
        else:
            body_code = f"payload = {repr(body)}\n"
            body_arg = "data=payload"

    auth_code = ""
    auth_arg = ""
    if peticion.auth_tipo == "basic" and peticion.auth_datos:
        u = interpolar(peticion.auth_datos.get("usuario", ""), entorno)
        p = interpolar(peticion.auth_datos.get("password", ""), entorno)
        auth_code = f"auth = httpx.BasicAuth('{u}', '{p}')\n"
        auth_arg = "auth=auth"

    args = [f"'{metodo}'", f"'{url}'"]
    if params:
        args.append("params=params")
    if headers:
        args.append("headers=headers")
    if body_arg:
        args.append(body_arg)
    if auth_arg:
        args.append(auth_arg)

    llamada_args = ", ".join(args)

    return f"""import httpx
import asyncio

async def main():
    headers = {headers_str}
    params = {params_str}
    {body_code}{auth_code}
    async with httpx.AsyncClient() as client:
        response = await client.request({llamada_args})
        print(f"Status: {{response.status_code}}")
        print(response.text)

if __name__ == "__main__":
    asyncio.run(main())
"""


def a_python_requests(peticion: Peticion, entorno: Entorno | None = None) -> str:
    """Genera código Python síncrono con requests."""
    metodo, url, params, headers, body = _preparar_peticion(peticion, entorno)

    params_str = json.dumps(params, indent=4) if params else "None"
    headers_str = json.dumps(headers, indent=4) if headers else "None"

    body_code = ""
    body_arg = ""
    if metodo not in ("GET", "HEAD") and body:
        if peticion.body_tipo == "json":
            try:
                parsed = json.loads(body)
                body_code = f"payload = {json.dumps(parsed, indent=4)}\n"
                body_arg = "json=payload"
            except Exception:
                body_code = f"payload = {repr(body)}\n"
                body_arg = "data=payload"
        else:
            body_code = f"payload = {repr(body)}\n"
            body_arg = "data=payload"

    auth_code = ""
    auth_arg = ""
    if peticion.auth_tipo == "basic" and peticion.auth_datos:
        u = interpolar(peticion.auth_datos.get("usuario", ""), entorno)
        p = interpolar(peticion.auth_datos.get("password", ""), entorno)
        auth_code = f"auth = ('{u}', '{p}')\n"
        auth_arg = "auth=auth"

    args = [f"'{url}'"]
    if params:
        args.append("params=params")
    if headers:
        args.append("headers=headers")
    if body_arg:
        args.append(body_arg)
    if auth_arg:
        args.append(auth_arg)

    llamada_args = ", ".join(args)
    metodo_fn = metodo.lower()

    return f"""import requests

url = '{url}'
headers = {headers_str}
params = {params_str}
{body_code}{auth_code}
response = requests.{metodo_fn}({llamada_args})
print(f"Status: {{response.status_code}}")
print(response.text)
"""


def a_javascript_fetch(peticion: Peticion, entorno: Entorno | None = None) -> str:
    """Genera código JavaScript moderno con fetch."""
    metodo, url, params, headers, body = _preparar_peticion(peticion, entorno)

    if params:
        separador = "&" if "?" in url else "?"
        url = f"{url}{separador}{urlencode(params)}"

    opts = {
        "method": metodo,
        "headers": headers,
    }
    if metodo not in ("GET", "HEAD") and body:
        opts["body"] = body

    opts_json = json.dumps(opts, indent=2)

    return f"""async function enviarPeticion() {{
  const url = '{url}';
  const options = {opts_json};

  try {{
    const response = await fetch(url, options);
    const data = await response.text();
    console.log(`Status: ${{response.status}}`);
    console.log(data);
  }} catch (error) {{
    console.error('Error:', error);
  }}
}}

enviarPeticion();
"""
