"""
motor.py — Motor de línea de comandos (CLI) y worker_main para API Tester.

Permite ejecutar pruebas de APIs y colecciones completas en lote desde la terminal,
mostrando el estado de cada endpoint, tiempos de respuesta y resultados de assertions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from .cliente import ResultadoPeticion, enviar_peticion
from .colecciones import Coleccion, Peticion, cargar_coleccion
from .entornos import Entorno, cargar_entornos, interpolar


async def ejecutar_peticion_con_entorno(peticion: Peticion, entorno: Entorno | None = None) -> tuple[Peticion, ResultadoPeticion]:
    """Interpola variables del entorno y envía la petición."""
    url_final = interpolar(peticion.url, entorno)
    params_final = {interpolar(k, entorno): interpolar(v, entorno) for k, v in peticion.params.items()}
    headers_final = {interpolar(k, entorno): interpolar(v, entorno) for k, v in peticion.headers.items()}
    body_final = interpolar(peticion.body_contenido, entorno)

    auth_datos_final = {interpolar(k, entorno): interpolar(v, entorno) for k, v in peticion.auth_datos.items()}

    res = await enviar_peticion(
        metodo=peticion.metodo,
        url=url_final,
        params=params_final,
        headers=headers_final,
        body_tipo=peticion.body_tipo,
        body_contenido=body_final,
        auth_tipo=peticion.auth_tipo,
        auth_datos=auth_datos_final,
        assertions=peticion.assertions,
    )
    return peticion, res


async def ejecutar_coleccion_lote(
    coleccion: Coleccion,
    entorno: Entorno | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Ejecuta todas las peticiones de una colección secuencialmente y recopila métricas.
    """
    todas_peticiones: list[Peticion] = []
    todas_peticiones.extend(coleccion.peticiones_raiz)
    for c in coleccion.carpetas:
        todas_peticiones.extend(c.peticiones)

    total = len(todas_peticiones)
    resultados = []
    exitosas = 0
    fallidas = 0
    total_assertions = 0
    assertions_pasadas = 0

    if verbose:
        print(f"\n=======================================================")
        print(f" EJECUTANDO COLECCIÓN: {coleccion.nombre} ({total} peticiones)")
        if entorno:
            print(f" Entorno activo: {entorno.nombre}")
        print(f"=======================================================\n")

    for i, pet in enumerate(todas_peticiones, 1):
        p, res = await ejecutar_peticion_con_entorno(pet, entorno)

        status_str = f"{res.status}" if res.status > 0 else "ERROR"
        if res.exito:
            exitosas += 1
        else:
            fallidas += 1

        total_assertions += len(res.assertions)
        pasadas = sum(1 for a in res.assertions if a.exito)
        assertions_pasadas += pasadas

        item_res = {
            "peticion_id": p.id,
            "nombre": p.nombre,
            "metodo": p.metodo,
            "url": interpolar(p.url, entorno),
            "status": res.status,
            "tiempo_ms": res.tiempo_ms,
            "tamaño_bytes": res.tamaño_bytes,
            "error": res.error,
            "assertions_total": len(res.assertions),
            "assertions_pasadas": pasadas,
            "detalles_assertions": [
                {"nombre": a.nombre, "exito": a.exito, "obtenido": a.obtenido} for a in res.assertions
            ],
        }
        resultados.append(item_res)

        if verbose:
            tag_status = "✓" if res.exito else "✗"
            print(f"[{i}/{total}] {tag_status} {p.metodo} {interpolar(p.url, entorno)}")
            print(f"      Status: {status_str} | Tiempo: {res.tiempo_ms} ms | Tamaño: {res.tamaño_kb} KB")
            if res.error:
                print(f"      ERROR: {res.error}")

            if res.assertions:
                print(f"      Pruebas: {pasadas}/{len(res.assertions)} pasaron")
                for a in res.assertions:
                    simb = "✓" if a.exito else "✗"
                    print(f"        {simb} {a.nombre} -> {a.obtenido}")
            print()

    resumen = {
        "coleccion": coleccion.nombre,
        "total_peticiones": total,
        "exitosas": exitosas,
        "fallidas": fallidas,
        "total_assertions": total_assertions,
        "assertions_pasadas": assertions_pasadas,
        "resultados": resultados,
    }

    if verbose:
        print(f"=======================================================")
        print(f" RESUMEN: {total} peticiones | {exitosas} OK | {fallidas} fallidas")
        if total_assertions > 0:
            print(f" PRUEBAS: {assertions_pasadas}/{total_assertions} assertions pasadas")
        print(f"=======================================================\n")

    return resumen


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de línea de comandos para el worker_main."""
    parser = argparse.ArgumentParser(
        description="API Tester — Ejecución de peticiones y colecciones HTTP en lote.",
        prog="api_tester",
    )
    parser.add_argument("--coleccion", "-c", help="Ruta al archivo JSON de la colección a ejecutar.")
    parser.add_argument("--url", "-u", help="URL para ejecutar una petición individual directa.")
    parser.add_argument("--metodo", "-m", default="GET", help="Método HTTP para petición directa (default: GET).")
    parser.add_argument("--entorno", "-e", help="Nombre del entorno a aplicar (ej: 'Local', 'Producción').")
    parser.add_argument("--json", action="store_true", help="Salida en formato JSON estructurado.")
    parser.add_argument("--salida", "-o", help="Ruta de archivo para guardar el reporte final.")

    args = parser.parse_args(argv)

    entorno_activo = None
    if args.entorno:
        entornos = cargar_entornos()
        entorno_activo = next((e for e in entornos if e.nombre.lower() == args.entorno.lower()), None)
        if not entorno_activo:
            print(f"Aviso: Entorno '{args.entorno}' no encontrado. Ejecutando sin variables.", file=sys.stderr)

    # 1. Modo Colección
    if args.coleccion:
        ruta_col = Path(args.coleccion)
        if not ruta_col.exists():
            print(f"ERROR: No se encontró la colección en {args.coleccion}", file=sys.stderr)
            return 1

        try:
            col = cargar_coleccion(ruta_col)
        except Exception as e:
            print(f"ERROR al leer colección: {e}", file=sys.stderr)
            return 1

        resumen = asyncio.run(ejecutar_coleccion_lote(col, entorno_activo, verbose=not args.json))

        if args.json:
            print(json.dumps(resumen, indent=2, ensure_ascii=False))

        if args.salida:
            Path(args.salida).write_text(json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Reporte guardado en: {args.salida}")

        return 0 if resumen["fallidas"] == 0 else 1

    # 2. Modo Petición individual
    if args.url:
        pet = Peticion(
            nombre="Petición Directa",
            metodo=args.metodo.upper(),
            url=args.url,
            assertions=[{"tipo": "status", "operador": "==", "valor": "200"}],
        )
        col = Coleccion(
            nombre="Petición Directa",
            peticiones_raiz=[pet],
        )
        resumen = asyncio.run(ejecutar_coleccion_lote(col, entorno_activo, verbose=not args.json))

        if args.json:
            print(json.dumps(resumen, indent=2, ensure_ascii=False))

        return 0 if resumen["fallidas"] == 0 else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
