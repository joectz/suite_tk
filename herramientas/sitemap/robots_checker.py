"""
robots_checker.py — Lector y parser de robots.txt del sitio objetivo.

Descarga el robots.txt, extrae las directivas Disallow para User-agent: *
y provee una función para verificar si una URL está bloqueada.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import requests


# Regex para detectar líneas de directiva (case-insensitive)
_RE_USER_AGENT = re.compile(r"^\s*user-agent\s*:\s*(.+)", re.IGNORECASE)
_RE_DISALLOW = re.compile(r"^\s*disallow\s*:\s*(.*)", re.IGNORECASE)


def obtener_reglas_robots(
    url_base: str,
    session: requests.Session | None = None,
    timeout: int = 10,
) -> list[str]:
    """
    Descarga robots.txt del sitio y extrae las rutas Disallow para
    User-agent: * (el agente genérico).

    Retorna una lista de prefijos/rutas bloqueadas (ej. ['/admin/', '/tmp/']).
    Si el robots.txt no existe o falla la descarga, retorna [] (ninguna
    ruta bloqueada — se incluyen todas).
    """
    parsed = urlparse(url_base)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    if session is None:
        session = requests.Session()

    try:
        resp = session.get(robots_url, timeout=timeout)
        if resp.status_code != 200:
            print(f"ROBOTS robots.txt no encontrado ({resp.status_code})", flush=True)
            return []
    except requests.RequestException as e:
        print(f"ROBOTS error al descargar robots.txt: {e}", flush=True)
        return []

    return _parsear_robots(resp.text)


def _parsear_robots(contenido: str) -> list[str]:
    """
    Parsea el texto de un robots.txt y extrae las directivas Disallow
    del bloque User-agent: * (el agente comodín).
    """
    reglas: list[str] = []
    en_bloque_global = False

    for linea in contenido.splitlines():
        # Quitar comentarios
        linea = linea.split("#", 1)[0].strip()
        if not linea:
            continue

        m_ua = _RE_USER_AGENT.match(linea)
        if m_ua:
            agente = m_ua.group(1).strip()
            en_bloque_global = (agente == "*")
            continue

        if en_bloque_global:
            m_dis = _RE_DISALLOW.match(linea)
            if m_dis:
                ruta = m_dis.group(1).strip()
                if ruta:  # Disallow vacío ("Disallow: ") significa permitir todo
                    reglas.append(ruta)

    return reglas


def ruta_bloqueada(url: str, reglas: list[str]) -> bool:
    """
    Comprueba si la ruta de `url` coincide con alguna regla Disallow.

    Maneja:
      - Prefijos simples: /admin/ bloquea /admin/dashboard
      - Comodín al final: /search* bloquea /search?q=foo
      - Ruta exacta con $: /secret$ bloquea solo /secret, no /secret/page
    """
    if not reglas:
        return False

    ruta = urlparse(url).path

    for regla in reglas:
        if not regla:
            continue

        # Regla con $ al final → coincidencia exacta
        if regla.endswith("$"):
            if ruta == regla[:-1]:
                return True
            continue

        # Regla con * → dividir en partes y verificar secuencialmente
        if "*" in regla:
            partes = regla.split("*")
            posicion = 0
            coincide = True
            for parte in partes:
                if not parte:
                    continue
                idx = ruta.find(parte, posicion)
                if idx == -1:
                    coincide = False
                    break
                posicion = idx + len(parte)
            if coincide:
                return True
            continue

        # Prefijo simple
        if ruta.startswith(regla):
            return True

    return False
