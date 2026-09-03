"""
lastmod.py — Resolución del campo <lastmod> para el sitemap.

Dos modos:
  - HEAD_REAL: petición HEAD a cada URL para leer el header Last-Modified.
  - FECHA_RASTREO: usa la fecha de modificación del archivo .json del Mapeador.
"""

from __future__ import annotations

import enum
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import parsedate_to_datetime

import requests


class ModoLastmod(enum.Enum):
    """Estrategia para resolver <lastmod>."""
    HEAD_REAL = "head"
    FECHA_RASTREO = "rastreo"


# ----------------------------------------------------------------------- #
# Resolución individual
# ----------------------------------------------------------------------- #

def obtener_lastmod_head(
    url: str,
    session: requests.Session,
    timeout: int = 10,
) -> str | None:
    """
    Hace una petición HEAD y retorna la fecha Last-Modified como YYYY-MM-DD.
    Retorna None si el servidor no envía el header o la petición falla.
    """
    try:
        resp = session.head(url, timeout=timeout, allow_redirects=True)
        valor = resp.headers.get("Last-Modified")
        if not valor:
            return None
        dt = parsedate_to_datetime(valor)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def obtener_lastmod_rastreo(fecha_json: datetime) -> str:
    """Retorna la fecha de modificación del archivo .json como YYYY-MM-DD."""
    return fecha_json.strftime("%Y-%m-%d")


# ----------------------------------------------------------------------- #
# Resolución en lote (paralela)
# ----------------------------------------------------------------------- #

def resolver_lastmod_lote(
    items: list[dict],
    modo: ModoLastmod,
    session: requests.Session | None = None,
    timeout: int = 10,
    fecha_json: datetime | None = None,
    n_workers: int = 8,
) -> dict[str, str]:
    """
    Resuelve el <lastmod> para todas las URLs de los items.

    Retorna: {url: "YYYY-MM-DD"}.
    Si no se puede determinar la fecha de una URL, no aparece en el dict
    (la URL se incluirá en el sitemap sin <lastmod>).
    """
    urls = [it["url"] for it in items]
    resultado: dict[str, str] = {}
    total = len(urls)

    if modo == ModoLastmod.FECHA_RASTREO:
        if fecha_json is None:
            fecha_json = datetime.now()
        fecha_str = obtener_lastmod_rastreo(fecha_json)
        for url in urls:
            resultado[url] = fecha_str
        print(f"LASTMOD {total}/{total} (fecha del rastreo: {fecha_str})", flush=True)
        return resultado

    # Modo HEAD_REAL: peticiones paralelas
    if session is None:
        session = requests.Session()

    completadas = 0
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futuros = {
            pool.submit(obtener_lastmod_head, url, session, timeout): url
            for url in urls
        }
        for futuro in as_completed(futuros):
            url = futuros[futuro]
            completadas += 1
            fecha = futuro.result()
            if fecha:
                resultado[url] = fecha

            if completadas % 50 == 0 or completadas == total:
                print(f"LASTMOD {completadas}/{total}", flush=True)

    resueltas = len(resultado)
    print(f"LASTMOD completado: {resueltas}/{total} URLs con fecha", flush=True)
    return resultado
