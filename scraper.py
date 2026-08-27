#!/usr/bin/env python3
"""
scraper.py — Mapea todas las URLs de un sitio web.

Uso:
    python scraper.py https://www.nomades.com
    python scraper.py https://www.nomades.com --max-paginas 500 --profundidad 5
    python scraper.py https://www.nomades.com --ignorar-query --delay 2
    python scraper.py https://www.nomades.com --obedecer-robots

Salida:
    paginas.jsonl  -> se escribe linea a linea (sobrevive a un Ctrl-C)
    paginas.json   -> JSON completo, se genera al cerrar el spider
    paginas.csv    -> opcional con --csv

Requisitos:
    pip install scrapy
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.http import HtmlResponse
from scrapy.linkextractors import LinkExtractor
from scrapy.spidermiddlewares.httperror import HttpError
from scrapy.utils.log import configure_logging


# --------------------------------------------------------------------------- #
# Normalizacion de URLs
# --------------------------------------------------------------------------- #

# Parametros de tracking que no cambian el contenido de la pagina
PARAMS_BASURA = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "ref", "_ga",
}


def normalizar(url: str, ignorar_query: bool = False) -> str:
    """
    Evita que la misma pagina se cuente varias veces:
      https://sitio.com  ==  https://sitio.com/
      /pagina#seccion    ==  /pagina
      /pagina?utm_source=fb == /pagina
    """
    p = urlparse(url)

    esquema = p.scheme.lower()
    netloc = p.netloc.lower()
    # Quitar puerto por defecto
    if (esquema == "http" and netloc.endswith(":80")) or \
       (esquema == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]

    ruta = p.path or "/"
    # /index.html y / son la misma pagina
    for indice in ("index.html", "index.htm", "index.php", "default.html"):
        if ruta.endswith("/" + indice):
            ruta = ruta[: -len(indice)]

    if ignorar_query:
        query = ""
    else:
        pares = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if k.lower() not in PARAMS_BASURA]
        query = urlencode(sorted(pares))

    # Se descarta siempre el fragmento (#...): no es una pagina distinta
    return urlunparse((esquema, netloc, ruta, p.params, query, ""))


# --------------------------------------------------------------------------- #
# Spider
# --------------------------------------------------------------------------- #

# Detecta lineas "Sitemap: <url>" dentro de robots.txt (formato estandar,
# usado por WordPress/Yoast/RankMath y practicamente cualquier CMS).
_SITEMAP_EN_ROBOTS = re.compile(rb"^\s*sitemap\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


class TodasLasPaginasSpider(scrapy.Spider):
    name = "todas_las_paginas"

    custom_settings = {
        # Reintentar tambien en 403/429, tipicos de proteccion anti-bot
        "RETRY_TIMES": 3,
        "RETRY_HTTP_CODES": [500, 502, 503, 504, 408, 429, 403],
    }

    def __init__(self, url_inicial: str, ignorar_query: bool = False,
                 max_params: int = 3, incluir_sitemap: bool = True, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if not urlparse(url_inicial).scheme:
            url_inicial = "https://" + url_inicial

        self.start_urls = [url_inicial]
        dominio = urlparse(url_inicial).netloc.split(":")[0]
        # Quitar el www para que allowed_domains cubra el dominio y sus subdominios
        self.allowed_domains = [dominio[4:] if dominio.startswith("www.") else dominio]

        self.ignorar_query = ignorar_query
        self.max_params = max_params
        self.incluir_sitemap = incluir_sitemap
        self._sitemap_pedido = False
        self.vistas: set[str] = set()
        self.sitemaps_vistos: set[str] = set()

        # LinkExtractor descarta solo: mailto:, tel:, javascript:, y las
        # extensiones binarias (.pdf, .jpg, .zip, .mp4...). Eso evita el
        # error "Response content isn't text".
        #
        # OJO: no se usa allow_domains aqui porque LinkExtractor compara el
        # netloc completo y no ignora el puerto (localhost:8000 != localhost).
        # El filtrado de dominio se hace en _es_interno().
        self.extractor = LinkExtractor(unique=True, canonicalize=True)

    def _es_interno(self, url: str) -> bool:
        """True si la URL pertenece al dominio objetivo o a un subdominio."""
        host = urlparse(url).netloc.split(":")[0].lower()
        if host.startswith("www."):
            host = host[4:]
        return any(host == d or host.endswith("." + d) for d in self.allowed_domains)

    # ---------------------------------------------------------------- #

    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(url, callback=self.parse,
                                 errback=self.error, dont_filter=True)

    def parse_robots(self, response):
        encontrados = list(_SITEMAP_EN_ROBOTS.finditer(response.body))
        if encontrados:
            for m in encontrados:
                yield from self._pedir_sitemap(m.group(1).decode("utf-8", "ignore"))
        else:
            # robots.txt no declara ningun sitemap: probar las ubicaciones
            # convencionales antes de darse por vencido.
            base = urlparse(response.url)
            raiz = f"{base.scheme}://{base.netloc}"
            yield from self._pedir_sitemap(f"{raiz}/sitemap.xml")
            yield from self._pedir_sitemap(f"{raiz}/wp-sitemap.xml")

    def _pedir_sitemap(self, url: str):
        if url in self.sitemaps_vistos:
            return
        self.sitemaps_vistos.add(url)
        yield scrapy.Request(url, callback=self.parse_sitemap, errback=self._ignorar_error,
                             dont_filter=True)

    def parse_sitemap(self, response):
        # Sirve tanto para un <sitemapindex> (referencia a mas sitemaps)
        # como para un <urlset> (paginas reales) sin distinguir el
        # namespace XML, que varia segun el generador del sitemap.
        for loc in response.xpath('//*[local-name()="loc"]/text()').getall():
            loc = loc.strip()
            if not loc:
                continue
            if loc.lower().endswith(".xml"):
                yield from self._pedir_sitemap(loc)
                continue
            if not self._es_interno(loc):
                continue

            destino = normalizar(loc, self.ignorar_query)
            n_params = len(parse_qsl(urlparse(destino).query))
            if n_params > self.max_params or destino in self.vistas:
                continue
            self.vistas.add(destino)
            yield scrapy.Request(loc, callback=self.parse, errback=self.error)

    def _ignorar_error(self, failure):
        """robots.txt/sitemap.xml ausentes (404) no son un error del rastreo."""

    def parse(self, response):
        # Disparado desde aqui (no desde start_requests) porque Scrapy no
        # garantiza agotar el generador de start_requests cuando el
        # seguimiento de enlaces ya mantiene el scheduler ocupado: la
        # peticion a robots.txt se quedaba sin pedir nunca. Yield desde un
        # callback normal si esta garantizado.
        if self.incluir_sitemap and not self._sitemap_pedido:
            self._sitemap_pedido = True
            base = urlparse(response.url)
            raiz = f"{base.scheme}://{base.netloc}"
            yield scrapy.Request(f"{raiz}/robots.txt", callback=self.parse_robots,
                                 errback=self._ignorar_error, dont_filter=True)

        # Solo procesar HTML. Si el servidor devolvio un binario pese al
        # filtro de extensiones, se registra y no se intenta parsear.
        tipo = response.headers.get("Content-Type", b"").decode("utf-8", "ignore")
        es_html = isinstance(response, HtmlResponse)

        item = {
            "url": normalizar(response.url, self.ignorar_query),
            "status": response.status,
            "titulo": "",
            "profundidad": response.meta.get("depth", 0),
            "origen": response.request.headers.get("Referer", b"").decode("utf-8", "ignore"),
            "content_type": tipo.split(";")[0].strip(),
        }

        if not es_html:
            item["titulo"] = "(no es HTML)"
            yield item
            return

        titulo = response.css("title::text").get()
        item["titulo"] = titulo.strip() if titulo else ""
        yield item

        # Marcar la propia URL como vista: evita volver a pedir alias de la
        # misma pagina (ej. "/" y "/index.html" apuntan al mismo sitio).
        self.vistas.add(item["url"])

        # Seguir los enlaces internos
        for link in self.extractor.extract_links(response):
            if not self._es_interno(link.url):
                continue  # enlace externo (facebook, google...)
            destino = normalizar(link.url, self.ignorar_query)

            # Anti-trampa: paginas de calendario o filtros generan URLs
            # infinitas del tipo ?dia=1&mes=2&categoria=3...
            n_params = len(parse_qsl(urlparse(destino).query))
            if n_params > self.max_params:
                continue

            if destino in self.vistas:
                continue
            self.vistas.add(destino)

            yield scrapy.Request(destino, callback=self.parse, errback=self.error)

    def error(self, failure):
        """Registra las URLs que fallaron en vez de perderlas en silencio."""
        request = failure.request
        if failure.check(HttpError):
            estado = failure.value.response.status
            motivo = f"HTTP {estado}"
        else:
            estado = 0
            motivo = failure.value.__class__.__name__

        self.logger.warning(f"Fallo: {request.url} -> {motivo}")
        yield {
            "url": normalizar(request.url, self.ignorar_query),
            "status": estado,
            "titulo": f"(error: {motivo})",
            "profundidad": request.meta.get("depth", 0),
            "origen": request.headers.get("Referer", b"").decode("utf-8", "ignore"),
            "content_type": "",
        }


# --------------------------------------------------------------------------- #
# Pipeline de escritura incremental
# --------------------------------------------------------------------------- #

class JsonlPipeline:
    """
    Escribe una linea por pagina y hace flush() inmediato.

    Por que no usar FEEDS: el exportador de Scrapy mantiene el archivo en un
    buffer y solo lo vuelca al cerrar el spider. Durante el crawl el .jsonl
    queda en 0 bytes, asi que ni se puede seguir el progreso en vivo ni se
    salva nada si el proceso muere de golpe. Con flush() por item, cada
    pagina queda en disco en el momento.
    """

    def __init__(self, ruta: str):
        self.ruta = ruta
        self.f = None
        self.n = 0

    @classmethod
    def from_crawler(cls, crawler):
        return cls(ruta=crawler.settings.get("JSONL_PATH", "paginas.jsonl"))

    def open_spider(self, spider):
        self.f = open(self.ruta, "w", encoding="utf-8")

    def process_item(self, item, spider):
        self.f.write(json.dumps(dict(item), ensure_ascii=False) + "\n")
        self.f.flush()          # visible al instante para otros procesos
        self.n += 1
        # Progreso legible por una GUI o por consola
        print(f"PROGRESO {self.n} {item['url']}", flush=True)
        return item

    def close_spider(self, spider):
        if self.f:
            self.f.close()


# --------------------------------------------------------------------------- #
# Conversion final jsonl -> json
# --------------------------------------------------------------------------- #

def consolidar(jsonl: str, salida_json: str, salida_csv: str | None) -> int:
    if not os.path.exists(jsonl):
        return 0

    items, vistos = [], set()
    with open(jsonl, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                d = json.loads(linea)
            except json.JSONDecodeError:
                continue  # linea a medias por un corte brusco
            if d["url"] not in vistos:
                vistos.add(d["url"])
                items.append(d)

    items.sort(key=lambda d: d["url"])

    with open(salida_json, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    if salida_csv:
        import csv
        with open(salida_csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(items[0].keys()) if items else
                               ["url", "status", "titulo", "profundidad", "origen", "content_type"])
            w.writeheader()
            w.writerows(items)

    return len(items)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    p = argparse.ArgumentParser(
        description="Mapea todas las URLs de un sitio web.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("url", help="URL inicial del sitio")
    p.add_argument("-o", "--output", default="paginas",
                   help="Nombre base de los archivos de salida (def. paginas)")
    p.add_argument("--max-paginas", type=int, default=2000,
                   help="Detenerse tras N paginas (def. 2000, 0 = sin limite)")
    p.add_argument("--profundidad", type=int, default=10,
                   help="Profundidad maxima de clics desde la raiz (def. 10, 0 = sin limite)")
    p.add_argument("--delay", type=float, default=1.0,
                   help="Segundos entre peticiones (def. 1.0)")
    p.add_argument("--concurrencia", type=int, default=4,
                   help="Peticiones simultaneas al dominio (def. 4)")
    p.add_argument("--timeout-total", type=int, default=0,
                   help="Detenerse tras N segundos (0 = sin limite)")
    p.add_argument("--ignorar-query", action="store_true",
                   help="Tratar /pagina?a=1 y /pagina como la misma URL")
    p.add_argument("--max-params", type=int, default=3,
                   help="Descartar URLs con mas de N parametros (def. 3)")
    p.add_argument("--obedecer-robots", action="store_true",
                   help="Respetar el robots.txt del sitio")
    p.add_argument("--sin-sitemap", action="store_true",
                   help="No leer sitemap.xml (por defecto se usa para encontrar "
                        "paginas huerfanas, sin enlaces internos apuntandoles)")
    p.add_argument("--csv", action="store_true", help="Generar tambien un CSV")
    p.add_argument("-v", "--verbose", action="store_true", help="Log detallado")
    args = p.parse_args()

    base = args.output
    jsonl = f"{base}.jsonl"
    if os.path.exists(jsonl):
        os.remove(jsonl)

    configure_logging()

    ajustes = {
        # CLAVE: pipeline propio con flush() por pagina. No se usa FEEDS
        # porque bufferiza y el archivo queda vacio hasta el final del crawl.
        "ITEM_PIPELINES": {f"{__name__}.JsonlPipeline": 300},
        "JSONL_PATH": jsonl,
        "FEED_EXPORT_ENCODING": "utf-8",   # tildes y ñ legibles, no \u00f1

        "ROBOTSTXT_OBEY": args.obedecer_robots,
        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        },

        # Ritmo: autothrottle se adapta a lo rapido que responda el servidor
        "DOWNLOAD_DELAY": args.delay,
        "CONCURRENT_REQUESTS_PER_DOMAIN": args.concurrencia,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": args.delay,
        "AUTOTHROTTLE_MAX_DELAY": 15.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": float(args.concurrencia),

        # Frenos de emergencia: sin esto el crawl puede no terminar nunca
        "DEPTH_LIMIT": args.profundidad,
        "CLOSESPIDER_PAGECOUNT": args.max_paginas,
        "CLOSESPIDER_TIMEOUT": args.timeout_total,

        "DOWNLOAD_TIMEOUT": 30,
        "DOWNLOAD_MAXSIZE": 10 * 1024 * 1024,   # no bajar archivos enormes
        "COOKIES_ENABLED": True,
        "HTTPERROR_ALLOW_ALL": False,           # los errores van al errback
        "REDIRECT_ENABLED": True,
        "LOG_LEVEL": "DEBUG" if args.verbose else "INFO",

        # Scrapy inspecciona el codigo fuente de los callbacks (inspect.
        # getsource) para advertir sobre un "return" con valor dentro de un
        # generador. Congelado con PyInstaller no hay .py en disco que leer:
        # inspect.getsource() lanza OSError, que Scrapy no atrapa, y el
        # callback entero aborta a mitad de camino (se pierde el item Y los
        # enlaces que quedaban por seguir en esa pagina). Es solo un lint;
        # se desactiva para que no tumbe el callback en el .exe.
        "WARN_ON_GENERATOR_RETURN_VALUE": False,
    }

    proceso = CrawlerProcess(settings=ajustes)
    proceso.crawl(TodasLasPaginasSpider,
                  url_inicial=args.url,
                  ignorar_query=args.ignorar_query,
                  max_params=args.max_params,
                  incluir_sitemap=not args.sin_sitemap)

    print(f"\nRastreando {args.url} ...")
    print("Puedes cortar con Ctrl-C: lo rastreado queda en "
          f"{jsonl}\n")

    try:
        proceso.start()
    except KeyboardInterrupt:
        print("\nInterrumpido. Consolidando lo que se alcanzo a rastrear...",
              file=sys.stderr)

    total = consolidar(jsonl, f"{base}.json",
                       f"{base}.csv" if args.csv else None)

    print("\n" + "-" * 55)
    print(f"Paginas unicas encontradas : {total}")
    print(f"JSON                       : {os.path.abspath(base + '.json')}")
    print(f"JSONL (crudo, incremental) : {os.path.abspath(jsonl)}")
    if args.csv:
        print(f"CSV                        : {os.path.abspath(base + '.csv')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
