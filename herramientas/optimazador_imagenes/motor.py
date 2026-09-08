#!/usr/bin/env python3
"""
motor.py — Motor de compresión y optimización de imágenes .

Proporciona:
  - Optimización en memoria ultrarrápida para vista previa interactiva en tiempo real.
  - Optimización a disco para procesamiento individual y por lotes.
  - Soporte para WebP, AVIF, JPEG y PNG con control granular de calidad,
    algoritmos de remuestreo (Lanczos, Bicubic, etc.), redimensionamiento,
    modo sin pérdida (lossless), eliminación de metadatos y preservación de canal alfa.
  - Modo CLI/worker ("python app.py --worker optimizador_imagenes ...") para ejecución headless.
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Sequence

from PIL import Image, ImageOps

# Inicializar y verificar soporte de formatos en Pillow
Image.init()

FORMATOS_SOPORTADOS = ["webp", "avif", "jpeg", "png"]
EXTENSIONES_IMAGENES = {
    ".jpg", ".jpeg", ".png", ".webp", ".avif",
    ".bmp", ".tiff", ".tif", ".gif", ".ico"
}

FILTROS_RESAMPLE = {
    "lanczos": Image.Resampling.LANCZOS,
    "bicubic": Image.Resampling.BICUBIC,
    "bilinear": Image.Resampling.BILINEAR,
    "nearest": Image.Resampling.NEAREST,
}


# --------------------------------------------------------------------------- #
# Clases de Datos
# --------------------------------------------------------------------------- #

@dataclass
class OpcionesOptimizacion:
    """Configuración para el proceso de compresión de una imagen."""
    formato: str = "webp"            # webp, avif, jpeg, png
    calidad: int = 80                # 1 - 100
    sin_perdida: bool = False        # Lossless (WebP / AVIF)
    esfuerzo: int = 4                # 0 a 6 (WebP/AVIF speed/method)
    submuestreo: str = "4:2:0"       # "4:2:0" o "4:4:4" (JPEG/WebP)
    eliminar_metadata: bool = True   # Limpiar EXIF, GPS, etc.
    cuantizar_colores: int = 0       # 0 = desactivado, 2..256 para paleta reducida (PNG/WebP)

    # Redimensionamiento
    redimensionar: bool = False
    modo_resize: str = "none"        # "none", "scale", "fit"
    escala: float = 100.0            # Porcentaje si modo_resize == "scale"
    ancho_max: int = 0               # Píxeles máximos si modo_resize == "fit"
    alto_max: int = 0
    mantener_aspecto: bool = True
    filtro_resample: str = "lanczos" # lanczos, bicubic, bilinear, nearest


@dataclass
class ResultadoFormato:
    """Resultado de la optimización para un formato específico."""
    formato: str
    ruta_salida: str
    peso_bytes: int
    peso_original_bytes: int
    ahorro_bytes: int
    ahorro_porcentaje: float
    ancho: int = 0
    alto: int = 0
    error: str | None = None


@dataclass
class ResultadoOptimizacion:
    """Resultado completo de optimización de una imagen."""
    ruta_original: str
    peso_original_bytes: int
    ancho_original: int
    alto_original: int
    ancho_final: int
    alto_final: int
    resultados: list[ResultadoFormato] = field(default_factory=list)
    error: str | None = None


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #

def log(msg: str) -> None:
    """Escribe a stderr para captura en tiempo real por el proceso padre."""
    print(msg, file=sys.stderr, flush=True)


def es_avif_disponible() -> bool:
    """Comprueba si el formato AVIF está soportado por Pillow en el entorno."""
    try:
        return "AVIF" in Image.SAVE
    except Exception:
        return False


def formatear_bytes(b: int) -> str:
    """Formatea bytes a representación legible (ej. 1.2 MB, 450 KB, 80 B)."""
    if b < 0:
        return "0 B"
    val = float(b)
    for unidad in ["B", "KB", "MB", "GB"]:
        if val < 1024.0 or unidad == "GB":
            return f"{val:.0f} B" if unidad == "B" else f"{val:.1f} {unidad}"
        val /= 1024.0
    return f"{b} B"


def buscar_imagenes(rutas: list[str]) -> list[str]:
    """
    Busca recursivamente archivos de imagen válidos dados archivos o carpetas.
    Devuelve lista de rutas absolutas normalizadas sin duplicados.
    """
    encontradas: list[str] = []
    vistas: set[str] = set()

    for item in rutas:
        p = Path(item).expanduser().resolve()
        if not p.exists():
            continue

        if p.is_file() and p.suffix.lower() in EXTENSIONES_IMAGENES:
            str_p = str(p)
            if str_p not in vistas:
                vistas.add(str_p)
                encontradas.append(str_p)
        elif p.is_dir():
            for root, _, files in os.walk(p):
                for f in files:
                    suf = Path(f).suffix.lower()
                    if suf in EXTENSIONES_IMAGENES:
                        fp = str(Path(root, f).resolve())
                        if fp not in vistas:
                            vistas.add(fp)
                            encontradas.append(fp)

    return encontradas


def calcular_nuevas_dimensiones(
    ancho_orig: int,
    alto_orig: int,
    opciones: OpcionesOptimizacion
) -> tuple[int, int]:
    """Calcula las nuevas dimensiones según la configuración de redimensionado."""
    if not opciones.redimensionar or opciones.modo_resize == "none":
        return ancho_orig, alto_orig

    if opciones.modo_resize == "scale":
        pct = max(1.0, float(opciones.escala)) / 100.0
        w = max(1, int(round(ancho_orig * pct)))
        h = max(1, int(round(alto_orig * pct)))
        return w, h

    if opciones.modo_resize in ("fit", "exact"):
        max_w = opciones.ancho_max
        max_h = opciones.alto_max

        if not max_w and not max_h:
            return ancho_orig, alto_orig

        if not opciones.mantener_aspecto:
            w = max(1, max_w if max_w else ancho_orig)
            h = max(1, max_h if max_h else alto_orig)
            return w, h

        if max_w and not max_h:
            ratio = max_w / ancho_orig
            return max(1, max_w), max(1, int(round(alto_orig * ratio)))
        elif max_h and not max_w:
            ratio = max_h / alto_orig
            return max(1, int(round(ancho_orig * ratio))), max(1, max_h)
        else:
            ratio = min(max_w / ancho_orig, max_h / alto_orig)
            return max(1, int(round(ancho_orig * ratio))), max(1, int(round(alto_orig * ratio)))

    return ancho_orig, alto_orig


def preparar_imagen(
    im: Image.Image,
    formato: str,
    opciones: OpcionesOptimizacion
) -> Image.Image:
    """
    Aplica corrección de orientación EXIF, redimensionamiento y adaptación
    de canales de color según el formato de destino.
    """
    # 1. Transponer según EXIF para que las fotos de móviles no queden rotadas
    im = ImageOps.exif_transpose(im)

    # 2. Redimensionar si está configurado
    w_target, h_target = calcular_nuevas_dimensiones(im.width, im.height, opciones)
    if (w_target, h_target) != (im.width, im.height):
        filtro = FILTROS_RESAMPLE.get(opciones.filtro_resample.lower(), Image.Resampling.LANCZOS)
        im = im.resize((w_target, h_target), resample=filtro)

    fmt = formato.lower()

    # 3. Tratamiento de canales de color
    if fmt in {"jpeg", "jpg"}:
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            fondo = Image.new("RGB", im.size, (255, 255, 255))
            im_rgba = im.convert("RGBA")
            fondo.paste(im_rgba, mask=im_rgba.split()[3])
            im = fondo
        elif im.mode != "RGB":
            im = im.convert("RGB")
    elif fmt in {"webp", "avif", "png"}:
        if im.mode not in ("RGB", "RGBA"):
            if "transparency" in im.info or im.mode in ("P", "LA"):
                im = im.convert("RGBA")
            else:
                im = im.convert("RGB")

    # 4. Reducción opcional de paleta (cuantización de colores)
    if opciones.cuantizar_colores and 2 <= opciones.cuantizar_colores <= 256:
        if fmt in {"png", "webp"}:
            im = im.quantize(colors=opciones.cuantizar_colores, method=Image.Quantize.MEDIANCUT)

    return im


# --------------------------------------------------------------------------- #
# Núcleo de Optimización
# --------------------------------------------------------------------------- #

def optimizar_en_memoria(
    entrada: str | bytes | Path | Image.Image,
    opciones: OpcionesOptimizacion,
    formato: str | None = None
) -> tuple[bytes, dict]:
    """
    Optimiza una imagen directamente en memoria devolviendo los bytes resultantes
    y un diccionario de métricas. Ideal para live preview al estilo Squoosh.
    """
    fmt = (formato or opciones.formato).lower()
    if fmt == "jpg":
        fmt = "jpeg"

    # Obtener bytes originales y tamaño
    peso_original = 0
    if isinstance(entrada, (str, Path)):
        p = Path(entrada)
        peso_original = p.stat().st_size
        with Image.open(p) as img_abierta:
            im = img_abierta.copy()
    elif isinstance(entrada, bytes):
        peso_original = len(entrada)
        with Image.open(io.BytesIO(entrada)) as img_abierta:
            im = img_abierta.copy()
    elif isinstance(entrada, Image.Image):
        im = entrada.copy()
        buf_orig = io.BytesIO()
        im.save(buf_orig, format="PNG")
        peso_original = len(buf_orig.getvalue())
    else:
        raise ValueError(f"Tipo de entrada no soportado: {type(entrada)}")

    # Preparar imagen (resample, canales, orientacion)
    im_procesada = preparar_imagen(im, fmt, opciones)
    w_final, h_final = im_procesada.width, im_procesada.height

    # Codificar en el formato especificado
    salida_buf = io.BytesIO()
    calidad = max(1, min(100, int(opciones.calidad)))

    if fmt == "webp":
        save_kwargs = {
            "format": "WEBP",
            "quality": calidad,
            "lossless": opciones.sin_perdida,
            "method": max(0, min(6, opciones.esfuerzo)),
        }
        im_procesada.save(salida_buf, **save_kwargs)
        mime = "image/webp"

    elif fmt == "avif":
        if not es_avif_disponible():
            raise RuntimeError("El formato AVIF no está disponible en este entorno.")
        save_kwargs = {
            "format": "AVIF",
            "quality": calidad,
            "speed": max(0, min(10, 10 - opciones.esfuerzo)),
        }
        im_procesada.save(salida_buf, **save_kwargs)
        mime = "image/avif"

    elif fmt == "jpeg":
        subsampling = 0 if opciones.submuestreo == "4:4:4" else 2
        save_kwargs = {
            "format": "JPEG",
            "quality": calidad,
            "optimize": True,
            "progressive": True,
            "subsampling": subsampling,
        }
        im_procesada.save(salida_buf, **save_kwargs)
        mime = "image/jpeg"

    elif fmt == "png":
        save_kwargs = {
            "format": "PNG",
            "optimize": True,
            "compress_level": 9,
        }
        im_procesada.save(salida_buf, **save_kwargs)
        mime = "image/png"

    else:
        raise ValueError(f"Formato no soportado: {fmt}")

    bytes_opt = salida_buf.getvalue()
    peso_opt = len(bytes_opt)
    ahorro_b = peso_original - peso_opt
    ahorro_pct = round((ahorro_b / peso_original) * 100, 1) if peso_original > 0 else 0.0

    b64_str = base64.b64encode(bytes_opt).decode("ascii")
    data_url = f"data:{mime};base64,{b64_str}"

    info = {
        "formato": fmt,
        "mime": mime,
        "peso_bytes": peso_opt,
        "peso_original": peso_original,
        "ahorro_bytes": ahorro_b,
        "ahorro_porcentaje": ahorro_pct,
        "ancho": w_final,
        "alto": h_final,
        "data_url": data_url,
    }

    return bytes_opt, info


def optimizar_imagen(
    ruta: str,
    formatos: list[str] | Sequence[str] = ("webp",),
    calidad: int = 80,
    ancho: int | None = None,
    alto: int | None = None,
    escala: float | None = None,
    forzar_tamano: bool = False,
    sin_perdida: bool = False,
    mantener_metadata: bool = False,
    carpeta_salida: str | None = None,
    sufijo: str = "_opt",
    sobrescribir: bool = True,
    esfuerzo: int = 4,
    submuestreo: str = "4:2:0",
) -> ResultadoOptimizacion:
    """
    Optimiza una imagen en disco para uno o varios formatos, guardando los resultados
    en la carpeta de salida configurada.
    """
    p_orig = Path(ruta)
    if not p_orig.exists():
        return ResultadoOptimizacion(
            ruta_original=ruta,
            peso_original_bytes=0,
            ancho_original=0,
            alto_original=0,
            ancho_final=0,
            alto_final=0,
            error=f"El archivo no existe: {ruta}",
        )

    peso_original = p_orig.stat().st_size
    dir_salida = Path(carpeta_salida) if carpeta_salida else p_orig.parent
    dir_salida.mkdir(parents=True, exist_ok=True)

    modo_resize = "none"
    if escala is not None and escala > 0 and escala != 100:
        modo_resize = "scale"
    elif (ancho and ancho > 0) or (alto and alto > 0):
        modo_resize = "fit"

    opciones = OpcionesOptimizacion(
        calidad=calidad,
        sin_perdida=sin_perdida,
        esfuerzo=esfuerzo,
        submuestreo=submuestreo,
        eliminar_metadata=not mantener_metadata,
        redimensionar=(modo_resize != "none"),
        modo_resize=modo_resize,
        escala=escala or 100.0,
        ancho_max=ancho or 0,
        alto_max=alto or 0,
        mantener_aspecto=not forzar_tamano,
    )

    try:
        with Image.open(p_orig) as im_orig:
            w_orig, h_orig = im_orig.width, im_orig.height
    except Exception as e:
        return ResultadoOptimizacion(
            ruta_original=ruta,
            peso_original_bytes=peso_original,
            ancho_original=0,
            alto_original=0,
            ancho_final=0,
            alto_final=0,
            error=f"No se pudo leer la imagen: {e}",
        )

    resultados_formatos: list[ResultadoFormato] = []
    w_final_reportado = w_orig
    h_final_reportado = h_orig

    stem = p_orig.stem

    for fmt in formatos:
        fmt_clean = fmt.lower().strip()
        ext = ".jpg" if fmt_clean == "jpeg" else f".{fmt_clean}"
        nombre_salida = f"{stem}{sufijo}{ext}"
        p_dest = dir_salida / nombre_salida

        if p_dest.exists() and not sobrescribir:
            peso_existente = p_dest.stat().st_size
            ahorro_b = peso_original - peso_existente
            ahorro_pct = round((ahorro_b / peso_original) * 100, 1) if peso_original > 0 else 0.0
            resultados_formatos.append(ResultadoFormato(
                formato=fmt_clean,
                ruta_salida=str(p_dest),
                peso_bytes=peso_existente,
                peso_original_bytes=peso_original,
                ahorro_bytes=ahorro_b,
                ahorro_porcentaje=ahorro_pct,
                ancho=w_orig,
                alto=h_orig,
            ))
            continue

        try:
            bytes_opt, info = optimizar_en_memoria(ruta, opciones, formato=fmt_clean)
            with open(p_dest, "wb") as f_out:
                f_out.write(bytes_opt)

            w_final_reportado = info["ancho"]
            h_final_reportado = info["alto"]

            resultados_formatos.append(ResultadoFormato(
                formato=fmt_clean,
                ruta_salida=str(p_dest),
                peso_bytes=info["peso_bytes"],
                peso_original_bytes=peso_original,
                ahorro_bytes=info["ahorro_bytes"],
                ahorro_porcentaje=info["ahorro_porcentaje"],
                ancho=info["ancho"],
                alto=info["alto"],
            ))
        except Exception as e:
            resultados_formatos.append(ResultadoFormato(
                formato=fmt_clean,
                ruta_salida=str(p_dest),
                peso_bytes=0,
                peso_original_bytes=peso_original,
                ahorro_bytes=0,
                ahorro_porcentaje=0.0,
                error=str(e),
            ))

    return ResultadoOptimizacion(
        ruta_original=ruta,
        peso_original_bytes=peso_original,
        ancho_original=w_orig,
        alto_original=h_orig,
        ancho_final=w_final_reportado,
        alto_final=h_final_reportado,
        resultados=resultados_formatos,
    )


# --------------------------------------------------------------------------- #
# CLI / Worker Principal
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    """
    Punto de entrada CLI para ejecución autónoma o como subproceso worker
    ("python app.py --worker optimizador_imagenes ...").
    """
    parser = argparse.ArgumentParser(
        description="Optimizador de imágenes de alta eficiencia (estilo Squoosh)."
    )
    parser.add_argument("rutas", nargs="*", help="Archivos o carpetas con imágenes a optimizar.")
    parser.add_argument("-o", "--salida", help="Carpeta de destino para los archivos optimizados.")
    parser.add_argument("-f", "--formatos", default="webp", help="Formatos separados por coma (ej. webp,avif,jpeg,png).")
    parser.add_argument("-q", "--calidad", type=int, default=80, help="Calidad de compresión de 1 a 100 (defecto: 80).")
    parser.add_argument("--scale", type=float, help="Redimensionar por porcentaje de escala (ej. 50 para 50%%).")
    parser.add_argument("--max-width", type=int, help="Ancho máximo en píxeles.")
    parser.add_argument("--max-height", type=int, help="Alto máximo en píxeles.")
    parser.add_argument("--lossless", action="store_true", help="Compresión sin pérdida (WebP/AVIF).")
    parser.add_argument("--suffix", default="_opt", help="Sufijo para los archivos generados (defecto: _opt).")

    args = parser.parse_args(argv)

    if not args.rutas:
        parser.print_help()
        return 0

    formatos = [f.strip().lower() for f in args.formatos.split(",") if f.strip()]
    imagenes = buscar_imagenes(args.rutas)

    if not imagenes:
        log("No se encontraron imágenes en las rutas indicadas.")
        return 1

    log(f"--- Iniciando optimización de {len(imagenes)} imágenes a: {', '.join(formatos)} ---")

    peso_total_orig = 0
    peso_total_opt = 0

    for idx, img_path in enumerate(imagenes, 1):
        res = optimizar_imagen(
            ruta=img_path,
            formatos=formatos,
            calidad=args.calidad,
            ancho=args.max_width,
            alto=args.max_height,
            escala=args.scale,
            sin_perdida=args.lossless,
            carpeta_salida=args.salida,
            sufijo=args.suffix,
        )

        peso_total_orig += res.peso_original_bytes
        detalles = []
        for f in res.resultados:
            if not f.error:
                peso_total_opt += f.peso_bytes
                detalles.append(f"{f.formato.upper()}: {formatear_bytes(f.peso_bytes)} (-{f.ahorro_porcentaje}%)")
            else:
                detalles.append(f"{f.formato.upper()}: ERROR ({f.error})")

        log(f"[{idx}/{len(imagenes)}] {Path(img_path).name} -> {', '.join(detalles)}")

    ahorro_total = peso_total_orig - peso_total_opt
    pct_total = (ahorro_total / peso_total_orig * 100) if peso_total_orig > 0 else 0
    log(f"Completado. Original: {formatear_bytes(peso_total_orig)} | Optimizado: {formatear_bytes(peso_total_opt)} | Ahorro: -{pct_total:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))