#!/usr/bin/env python3
"""
motor.py — Motor ejecutor del Descargador Multimedia (Worker de subproceso).
Soporta procesamiento de múltiples URLs simultáneas con jerarquía por ruta de carpetas y deduplicación.

Ejemplos CLI:
    python -m herramientas.descargador_multimedia.motor https://ejemplo.com
    python -m herramientas.descargador_multimedia.motor https://ejemplo.com/pagina1 https://ejemplo.com/pagina2 -o ./salida
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests

from .configuracion import HEADERS, MEDIA_EXT, DEFAULT_WORKERS, DEFAULT_TIMEOUT, DEFAULT_MIN_SIZE
from .utilidades import es_media, subcarpeta_de_ruta
from .extractor import obtener_html_renderizado, extraer_urls, parece_bloqueado
from .descarga_motor import descargar, guardar_inline


def procesar_lista_urls(raw_urls: list[str]) -> list[str]:
    """Limpia y aplana una lista de URLs que pueden venir separadas por comas o espacios."""
    resultado = []
    for item in raw_urls:
        # Reemplazar comas y saltos de línea por espacios
        limpio = item.replace(",", " ").replace("\n", " ")
        for parte in limpio.split():
            parte = parte.strip()
            if parte:
                if not urlparse(parte).scheme:
                    parte = "https://" + parte
                if parte not in resultado:
                    resultado.append(parte)
    return resultado


def main_cli(args: argparse.Namespace) -> int:
    """Flujo de descarga por línea de comandos o worker con soporte de múltiples URLs."""
    urls_entrada = procesar_lista_urls(args.urls)

    if not urls_entrada:
        print("ERROR: No se proporcionó ninguna URL válida.", file=sys.stderr, flush=True)
        return 1

    primer_dominio = urlparse(urls_entrada[0]).netloc
    carpeta_base = args.output or f"media_{primer_dominio.replace(':', '_')}"
    extensiones = {e.lower().lstrip(".") for e in args.ext} if args.ext else MEDIA_EXT

    print(f"==================================================", flush=True)
    print(f"📦 Procesando {len(urls_entrada)} páginas objetivo en: {carpeta_base}", flush=True)
    print(f"==================================================", flush=True)

    sesion = requests.Session()
    sesion.headers.update(HEADERS)

    total_ok_global = 0
    total_omitidos_global = 0
    total_fallos_global = 0
    urls_vistas_batch: set[str] = set()

    for idx, url_pag in enumerate(urls_entrada, 1):
        print(f"\n[{idx}/{len(urls_entrada)}] 🌐 Analizando página: {url_pag}", flush=True)
        sub_ruta = subcarpeta_de_ruta(url_pag)
        if sub_ruta:
            print(f"   📂 Ruta de subcarpeta asignada: /{sub_ruta}", flush=True)

        sesion.headers["Referer"] = url_pag

        if args.render:
            try:
                html, url_final = obtener_html_renderizado(url_pag, args.timeout)
            except Exception as e:
                print(f"   ❌ ERROR: No se pudo renderizar {url_pag}: {e}", file=sys.stderr, flush=True)
                total_fallos_global += 1
                continue
        else:
            try:
                r = sesion.get(url_pag, timeout=args.timeout)
                r.raise_for_status()
            except requests.RequestException as e:
                print(f"   ❌ ERROR: No se pudo cargar {url_pag}: {e}", file=sys.stderr, flush=True)
                total_fallos_global += 1
                continue
            r.encoding = r.encoding or r.apparent_encoding
            html, url_final = r.text, r.url

        if parece_bloqueado(html):
            print("   ⚠ Advertencia: La página parece estar protegida (Cloudflare/anti-bot).", file=sys.stderr, flush=True)

        urls_extraidas, inline = extraer_urls(html, url_final, not args.no_css, sesion, args.timeout)
        urls_media = {u for u in urls_extraidas if urlparse(u).scheme in ("http", "https")}
        urls_media = {u for u in urls_media if es_media(u, extensiones)}
        if args.same_domain:
            urls_media = {u for u in urls_media if urlparse(u).netloc == urlparse(url_pag).netloc}

        # Deduplicar con el lote actual
        nuevas_urls = set()
        for u in urls_media:
            if u in urls_vistas_batch:
                total_omitidos_global += 1
            else:
                urls_vistas_batch.add(u)
                nuevas_urls.add(u)

        print(f"   ✨ Encontradas {len(nuevas_urls)} URLs de media nuevas ({len(urls_media) - len(nuevas_urls)} duplicadas)", flush=True)

        if args.dry_run:
            for u in sorted(nuevas_urls):
                print(f"      [DRY-RUN] -> {u}", flush=True)
            continue

        if not nuevas_urls and not inline:
            print("   ℹ Sin recursos nuevos para descargar en esta página.", flush=True)
            continue

        dir_destino_pagina = os.path.join(carpeta_base, sub_ruta) if sub_ruta else carpeta_base
        os.makedirs(dir_destino_pagina, exist_ok=True)

        ok = fallos = omitidos = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futuros = {
                pool.submit(descargar, u, carpeta_base, sesion, args.timeout, args.min_size, sub_ruta): u
                for u in nuevas_urls
            }
            for i, fut in enumerate(as_completed(futuros), 1):
                estado, detalle = fut.result()
                if estado == "ok":
                    ok += 1
                    print(f"   [{i}/{len(futuros)}] OK   {detalle}", flush=True)
                elif estado == "omitido":
                    omitidos += 1
                    print(f"   [{i}/{len(futuros)}] --   {detalle}", flush=True)
                else:
                    fallos += 1
                    print(f"   [{i}/{len(futuros)}] FALLO {detalle}", file=sys.stderr, flush=True)

        embebidas = guardar_inline(inline, carpeta_base, args.min_size, sub_ruta) if inline else 0

        total_ok_global += ok + embebidas
        total_omitidos_global += omitidos
        total_fallos_global += fallos

    print("\n" + "=" * 55, flush=True)
    print(f"🏁 RESUMEN TOTAL DE DESCARGA MULTIMEDIA", flush=True)
    print(f"==================================================", flush=True)
    print(f"Descargados exitosos : {total_ok_global}", flush=True)
    print(f"Omitidos / Existentes: {total_omitidos_global}", flush=True)
    print(f"Fallos               : {total_fallos_global}", flush=True)
    print(f"Carpeta Raíz         : {os.path.abspath(carpeta_base)}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada invocado por el despachador de workers o CLI."""
    p = argparse.ArgumentParser(
        description="Descarga toda la media de una o varias páginas web.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("urls", nargs="+", help="URLs de las páginas a procesar (separadas por espacio o comas)")
    p.add_argument("-o", "--output", default=None, help="Carpeta destino")
    p.add_argument("-w", "--workers", type=int, default=DEFAULT_WORKERS, help=f"Descargas en paralelo (def. {DEFAULT_WORKERS})")
    p.add_argument("-t", "--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"Timeout por petición en segundos (def. {DEFAULT_TIMEOUT})")
    p.add_argument("--ext", nargs="+", default=None, help="Solo estas extensiones, ej: --ext jpg png svg")
    p.add_argument("--min-size", type=int, default=DEFAULT_MIN_SIZE, help="Ignorar archivos menores a N bytes")
    p.add_argument("--no-css", action="store_true", help="No analizar hojas de estilo externas")
    p.add_argument("--same-domain", action="store_true", help="Solo descargar media del mismo dominio")
    p.add_argument("--dry-run", action="store_true", help="Solo listar las URLs encontradas, sin descargar")
    p.add_argument("--render", action="store_true", help="Renderizar la página con Playwright")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    return main_cli(args)


if __name__ == "__main__":
    sys.exit(main())
