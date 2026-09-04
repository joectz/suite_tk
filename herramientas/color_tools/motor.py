#!/usr/bin/env python3
"""
motor.py — Orquestador y ejecutor de Color Tools (modo Worker y CLI independiente).

Permite ejecutar el análisis y extracción de temas desde la terminal o como
subproceso worker lanzado por la suite (--worker color_tools <argumentos>).

Uso independiente:
    python -m herramientas.color_tools.motor https://ejemplo.com
    python -m herramientas.color_tools.motor https://ejemplo.com -o ./mis_colores
    python -m herramientas.color_tools.motor --json /tmp/mapeador_urls/ejemplo.json -o ./mis_colores
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from . import exportador
from . import extractor_tema


def main(argv: list[str] | None = None) -> int:
    """
    Punto de entrada para el worker de Color Tools o ejecución directa CLI.
    """
    p = argparse.ArgumentParser(
        description="Analiza y extrae el tema de color y estilos de una o varias páginas web.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "urls",
        nargs="*",
        help="Una o más URLs a analizar (ej. https://ejemplo.com)",
    )
    p.add_argument(
        "--json",
        dest="json_path",
        default=None,
        help="Ruta al archivo JSON generado por el Mapeador de URLs para extraer sus URLs",
    )
    p.add_argument(
        "-o",
        "--output",
        default=None,
        help="Carpeta de salida para los archivos de color (por defecto: ./colores_<dominio>)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Timeout por petición en segundos (def. 15)",
    )
    p.add_argument(
        "--max-paginas",
        type=int,
        default=8,
        help="Máximo de páginas a procesar si se pasan múltiples URLs (def. 8)",
    )

    args = p.parse_args(argv)

    lista_urls: list[str] = []

    # 1. Cargar URLs desde JSON del Mapeador si se proporcionó
    if args.json_path:
        ruta_json = Path(args.json_path)
        if not ruta_json.exists():
            print(f"ERROR: No se encontró el archivo JSON: {args.json_path}", file=sys.stderr)
            return 1
        try:
            with open(ruta_json, "r", encoding="utf-8") as f:
                datos = json.load(f)
                if isinstance(datos, list):
                    for item in datos:
                        u = item.get("url") if isinstance(item, dict) else str(item)
                        if u and u.startswith(("http://", "https://")):
                            lista_urls.append(u)
            print(f"Cargadas {len(lista_urls)} URLs desde {ruta_json.name}")
        except Exception as e:
            print(f"ERROR al leer JSON del Mapeador: {e}", file=sys.stderr)
            return 1

    # 2. Agregar URLs pasadas por línea de comandos
    if args.urls:
        for u in args.urls:
            u_clean = u.strip()
            if u_clean:
                if not urlparse(u_clean).scheme:
                    u_clean = f"https://{u_clean}"
                if u_clean not in lista_urls:
                    lista_urls.append(u_clean)

    if not lista_urls:
        print("ERROR: Debes proporcionar al menos una URL o una ruta --json.", file=sys.stderr)
        p.print_help(sys.stderr)
        return 1

    print(f"\nIniciando análisis de tema de color...")
    print(f"Total de URLs a muestrear: {min(len(lista_urls), args.max_paginas)} de {len(lista_urls)}")
    for i, u in enumerate(lista_urls[:args.max_paginas], 1):
        print(f"  [{i}] {u}")

    try:
        tema = extractor_tema.extraer_tema_de_sitio(
            lista_urls,
            timeout=args.timeout,
            max_urls_css=args.max_paginas,
            callback_progreso=print,
        )
    except Exception as e:
        print(f"ERROR durante la extracción del tema: {e}", file=sys.stderr)
        return 1

    print("\n" + "=" * 55)
    print(f" TEMA DETECTADO — {tema.dominio}")
    print("=" * 55)
    print(f"Color Primario (Marca) : {tema.primary}")
    print(f"Color Secundario       : {tema.secondary}")
    print(f"Fondo (Background)     : {tema.background}")
    print(f"Superficie (Cards)     : {tema.surface}")
    print(f"Texto Principal        : {tema.text_primary}")
    print(f"Variables CSS halladas : {len(tema.variables_css)}")
    print(f"Colores únicos en web  : {len(tema.paleta_completa)}")

    # Exportar resultados a la carpeta local
    res = exportador.exportar_tema_local(tema, args.output)

    print("\nArchivos generados exitosamente:")
    for arch in res["archivos"]:
        print(f"  ✓ {arch}")
    print(f"\nDirectorio de guardado: {os.path.abspath(res['carpeta'])}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
