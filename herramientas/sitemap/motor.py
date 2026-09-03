#!/usr/bin/env python3
"""
motor.py — Orquestador del Generador de Sitemap (worker/CLI).

Flujo:
  1. Lee el .json del Mapeador
  2. Aplica filtros de inclusión/exclusión
  3. Descarga y parsea robots.txt (si no --sin-robots)
  4. Filtra URLs bloqueadas por robots.txt
  5. Resuelve <lastmod> (HEAD real o fecha del rastreo)
  6. Genera los archivos XML del sitemap
  7. Imprime resumen

No contiene lógica de filtros, XML ni lastmod — solo orquesta los módulos.

Uso independiente:
    python -m herramientas.sitemap.motor \\
        --json /tmp/mapeador_urls/ejemplo.json \\
        --url-base https://ejemplo.com \\
        --output ./salida
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import filtros
from . import lastmod as _lastmod
from . import robots_checker
from . import generador_xml


def main(argv: list[str] | None = None) -> int:
    """
    Punto de entrada invocado por el despachador de workers (app.py --worker)
    o directamente desde la línea de comandos.
    """
    p = argparse.ArgumentParser(
        description="Genera un sitemap.xml a partir de un JSON del Mapeador de URLs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--json", required=True, dest="json_path",
                   help="Ruta al .json generado por el Mapeador de URLs")
    p.add_argument("--url-base", required=True,
                   help="URL raíz del sitio (ej. https://ejemplo.com)")
    p.add_argument("--output", default=None,
                   help="Carpeta de salida (default: misma carpeta que el JSON)")
    p.add_argument("--lastmod", choices=["head", "rastreo"], default="rastreo",
                   help="Modo de resolución de <lastmod> (default: rastreo)")
    p.add_argument("--workers", type=int, default=8,
                   help="Hilos para peticiones HEAD (default: 8)")
    p.add_argument("--timeout", type=int, default=10,
                   help="Timeout por petición HEAD en segundos (default: 10)")
    p.add_argument("--segmentar", action="store_true",
                   help="Generar sitemaps separados por tipo de contenido")
    p.add_argument("--sin-robots", action="store_true",
                   help="Omitir la verificación con robots.txt")
    args = p.parse_args(argv)

    # ------------------------------------------------------------------- #
    # 1. Leer el JSON del Mapeador
    # ------------------------------------------------------------------- #

    json_path = Path(args.json_path)
    if not json_path.exists():
        print(f"ERROR: No se encontró el archivo {json_path}", file=sys.stderr, flush=True)
        return 1

    try:
        with open(json_path, encoding="utf-8") as f:
            items_raw: list[dict] = json.load(f)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: JSON inválido: {e}", file=sys.stderr, flush=True)
        return 1

    if not isinstance(items_raw, list) or not items_raw:
        print("ERROR: El JSON está vacío o no es una lista de URLs", file=sys.stderr, flush=True)
        return 1

    fecha_json = datetime.fromtimestamp(json_path.stat().st_mtime)
    url_base = args.url_base.rstrip("/")

    print("=" * 55, flush=True)
    print(f"📄 Generador de Sitemap.xml", flush=True)
    print(f"   Fuente  : {json_path.name} ({len(items_raw)} URLs crudas)", flush=True)
    print(f"   Sitio   : {url_base}", flush=True)
    print(f"   Lastmod : {args.lastmod}", flush=True)
    print("=" * 55, flush=True)

    # ------------------------------------------------------------------- #
    # 2. Filtrar URLs válidas
    # ------------------------------------------------------------------- #

    print("\n🔍 Aplicando filtros de inclusión...", flush=True)
    items = filtros.aplicar_filtros(items_raw)
    descartadas = len(items_raw) - len(items)
    print(f"   ✅ {len(items)} URLs incluidas, {descartadas} descartadas", flush=True)

    if not items:
        print("ERROR: No quedan URLs válidas tras aplicar los filtros", file=sys.stderr, flush=True)
        return 1

    # ------------------------------------------------------------------- #
    # 3. Verificar robots.txt
    # ------------------------------------------------------------------- #

    if not args.sin_robots:
        print("\n🤖 Verificando robots.txt...", flush=True)
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; SitemapGenerator/1.0)",
        })
        reglas = robots_checker.obtener_reglas_robots(url_base, session, args.timeout)
        if reglas:
            antes = len(items)
            items = filtros.filtrar_por_robots(items, reglas)
            bloqueadas = antes - len(items)
            print(f"   {len(reglas)} reglas Disallow encontradas", flush=True)
            print(f"   {bloqueadas} URLs bloqueadas y excluidas del sitemap", flush=True)
        else:
            print("   Sin restricciones en robots.txt", flush=True)
    else:
        session = requests.Session()
        print("\n🤖 robots.txt: omitido (--sin-robots)", flush=True)

    if not items:
        print("ERROR: No quedan URLs tras filtrar con robots.txt", file=sys.stderr, flush=True)
        return 1

    # ------------------------------------------------------------------- #
    # 4. Resolver <lastmod>
    # ------------------------------------------------------------------- #

    modo = _lastmod.ModoLastmod(args.lastmod)
    print(f"\n📅 Resolviendo <lastmod> ({modo.value})...", flush=True)

    lastmod_dict = _lastmod.resolver_lastmod_lote(
        items=items,
        modo=modo,
        session=session,
        timeout=args.timeout,
        fecha_json=fecha_json,
        n_workers=args.workers,
    )

    # ------------------------------------------------------------------- #
    # 5. Generar los archivos XML
    # ------------------------------------------------------------------- #

    carpeta = Path(args.output) if args.output else json_path.parent
    carpeta.mkdir(parents=True, exist_ok=True)

    print(f"\n📝 Generando XML en {carpeta}/...", flush=True)
    archivos = generador_xml.guardar_sitemap(
        items=items,
        url_base=url_base,
        carpeta=carpeta,
        segmentar=args.segmentar,
        lastmod_dict=lastmod_dict,
    )

    # ------------------------------------------------------------------- #
    # 6. Resumen
    # ------------------------------------------------------------------- #

    print("\n" + "=" * 55, flush=True)
    print("🏁 RESUMEN", flush=True)
    print("=" * 55, flush=True)
    print(f"URLs en el sitemap : {len(items)}", flush=True)
    print(f"Archivos generados : {len(archivos)}", flush=True)
    for a in archivos:
        tamano = a.stat().st_size
        print(f"  → {a.name}  ({tamano:,} bytes)", flush=True)
    print(f"Carpeta            : {carpeta.resolve()}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
