"""extractor.py — Extracción de enlaces de medios desde HTML/CSS y renderizado JS con Playwright."""

import base64
import re
import sys
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright
import requests
from bs4 import BeautifulSoup

from .configuracion import HEADERS, ATRIBUTOS, DATA_URI_RE, CSS_URL_RE
from .utilidades import parsear_srcset


def parece_bloqueado(html: str, titulo: str = "") -> bool:
    """Detecta señales típicas de un challenge de Cloudflare/anti-bot."""
    marcadores = [
        "just a moment", "attention required", "checking your browser",
        "cf-browser-verification", "cf-challenge", "__cf_chl_",
        "verifying you are human", "ddos protection by cloudflare",
    ]
    texto = (html or "").lower() + " " + (titulo or "").lower()
    return any(m in texto for m in marcadores)


def obtener_html_renderizado(url: str, timeout: int, scroll: bool = True) -> tuple[str, str]:
    """Abre la página en Chromium headless, espera que cargue el JS,
    hace scroll para disparar lazy-load, y devuelve (html_final, url_final)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        contexto = browser.new_context(user_agent=HEADERS["User-Agent"])
        page = contexto.new_page()
        page.goto(url, timeout=timeout * 1000)

        if scroll:
            altura_previa = 0
            for _ in range(15):
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(400)
                altura_actual = page.evaluate("document.body.scrollHeight")
                if altura_actual == altura_previa:
                    break
                altura_previa = altura_actual

        page.wait_for_timeout(500)
        html = page.content()
        titulo = page.title()
        
        if parece_bloqueado(html, titulo):
            browser.close()
            raise RuntimeError("Detección de Cloudflare o bloqueo anti-bot detectado.")
            
        url_final = page.url
        browser.close()
        return html, url_final


def extraer_urls(html: str, base_url: str, incluir_css: bool,
                 sesion: requests.Session, timeout: int, on_status: callable = None) -> tuple[set[str], list[tuple[str, bytes]]]:
    """
    Devuelve (urls_absolutas, imagenes_en_base64).
    """
    soup = BeautifulSoup(html, "html.parser")
    urls: set[str] = set()
    inline: list[tuple[str, bytes]] = []

    def añadir(valor: str | None):
        if not valor:
            return
        valor = valor.strip()
        if not valor or valor.startswith(("javascript:", "mailto:", "#")):
            return
        m = DATA_URI_RE.match(valor)
        if m:
            mime, b64 = m.groups()
            try:
                inline.append((mime, base64.b64decode(b64, validate=False)))
            except Exception:
                pass
            return
        # Quitar el fragmento (#icono) típico de los sprites SVG
        urls.add(urljoin(base_url, valor).split("#")[0])

    if on_status:
        on_status("Analizando atributos HTML...")
        
    # 1. Atributos estándar y de lazy-loading
    for tag, attr in ATRIBUTOS:
        for el in soup.find_all(tag):
            valor = el.get(attr)
            if not valor:
                continue
            if "srcset" in attr:
                for u in parsear_srcset(valor):
                    añadir(u)
            else:
                añadir(valor)

    # 2. srcset de <img>
    for el in soup.find_all("img"):
        if el.get("srcset"):
            for u in parsear_srcset(el["srcset"]):
                añadir(u)

    # 3. <link rel="icon|apple-touch-icon|preload">
    for el in soup.find_all("link"):
        rel = " ".join(el.get("rel") or []).lower()
        if any(k in rel for k in ("icon", "image", "preload", "manifest")):
            añadir(el.get("href"))

    # 4. Open Graph / Twitter cards
    for el in soup.find_all("meta"):
        prop = (el.get("property") or el.get("name") or "").lower()
        if "image" in prop or "video" in prop:
            añadir(el.get("content"))

    # 5. SVG referenciados con <use xlink:href>
    for el in soup.find_all("use"):
        añadir(el.get("href") or el.get("xlink:href"))

    # 6. url(...) dentro de style="..." y <style>
    for el in soup.find_all(style=True):
        for m in CSS_URL_RE.findall(el["style"]):
            añadir(m)
    for el in soup.find_all("style"):
        for m in CSS_URL_RE.findall(el.get_text()):
            añadir(m)

    # 7. Hojas de estilo externas (opcional)
    if incluir_css:
        if on_status:
            on_status("Analizando hojas CSS externas...")
        hojas = {
            urljoin(base_url, el["href"])
            for el in soup.find_all("link", href=True)
            if "stylesheet" in " ".join(el.get("rel") or []).lower()
        }
        for hoja in sorted(hojas):
            try:
                r = sesion.get(hoja, timeout=timeout)
                r.raise_for_status()
                for m in CSS_URL_RE.findall(r.text):
                    urls.add(urljoin(hoja, m.strip()))
            except requests.RequestException as e:
                print(f"  [css] no se pudo leer {hoja}: {e}", file=sys.stderr)

    return urls, inline
