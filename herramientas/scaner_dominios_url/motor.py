#!/usr/bin/env python3
"""
motor.py — Motor de consulta de dominios (RDAP + WHOIS clásico).

Estrategia (la misma que usa ICANN internamente):
  1. RDAP (Registration Data Access Protocol) — protocolo moderno, JSON.
     Se usa el archivo de "bootstrap" de IANA (data.iana.org/rdap/dns.json)
     para saber a qué servidor RDAP preguntar según el TLD del dominio.
  2. WHOIS clásico (puerto 43) — si RDAP falla o el TLD no tiene servidor
     RDAP registrado, se cae a WHOIS: primero se pregunta a whois.iana.org
     cuál es el whois autoritativo del TLD, y luego se consulta ese server.

Modos de uso:

  Individual (interactivo, imprime en stdout):
    python3 motor.py ejemplo.com
    python3 motor.py ejemplo.com --json      # JSON RDAP crudo
    python3 motor.py ejemplo.com --whois     # forzar WHOIS clásico

  Batch (usado por la GUI — pagina.py):
    python3 motor.py --batch dominios.txt -o salida --concurrencia 4 --delay 0.3
    Escribe resultados incrementalmente en "salida.jsonl" (uno por línea),
    y el progreso/errores en stderr para que la GUI los muestre en vivo.

Sin dependencias externas: solo librería estándar.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

IANA_RDAP_BOOTSTRAP = "https://data.iana.org/rdap/dns.json"
IANA_WHOIS_SERVER = "whois.iana.org"
SOCKET_TIMEOUT = 10
USER_AGENT = "consulta-dominios/1.0"

_bootstrap_cache: dict | None = None


def log(msg: str) -> None:
    """Escribe una línea de log a stderr (la GUI la muestra en el panel técnico)."""
    print(msg, file=sys.stderr, flush=True)


def limpiar_dominio_o_url(entrada: str) -> tuple[str, str]:
    """
    Normaliza una URL o dominio introducido por el usuario.
    Devuelve: (dominio_limpio, punycode).

    Ejemplos:
      "https://www.google.com/search?q=test" -> ("google.com", "google.com")
      "http://diseño.es/portafolio"          -> ("diseño.es", "xn--diseo-rta.es")
      "xn--diseo-rta.es"                    -> ("diseño.es", "xn--diseo-rta.es")
    """
    texto = (entrada or "").strip()
    if not texto:
        return "", ""

    if "://" in texto or texto.startswith("//"):
        parsed = urllib.parse.urlsplit(texto)
        netloc = parsed.netloc
    elif "/" in texto or "?" in texto or "#" in texto or ":" in texto:
        parsed = urllib.parse.urlsplit("//" + texto)
        netloc = parsed.netloc
    else:
        netloc = texto

    if "@" in netloc:
        netloc = netloc.split("@")[-1]

    if ":" in netloc:
        netloc = netloc.split(":")[0]

    netloc = netloc.rstrip(".").lower()

    partes = netloc.split(".")
    if len(partes) > 2 and partes[0] == "www":
        netloc = ".".join(partes[1:])

    dominio = netloc

    try:
        if any(p.startswith("xn--") for p in partes):
            dominio_unicode = dominio.encode("ascii").decode("idna")
            punycode = dominio
            dominio = dominio_unicode
        else:
            punycode = dominio.encode("idna").decode("ascii")
    except Exception:
        punycode = dominio

    return dominio, punycode


def get_tld(domain: str) -> str:
    return domain.rstrip(".").split(".")[-1].lower()



# --------------------------------------------------------------------------- #
# RDAP
# --------------------------------------------------------------------------- #

def _fetch_rdap_bootstrap() -> dict:
    global _bootstrap_cache
    if _bootstrap_cache is not None:
        return _bootstrap_cache
    req = urllib.request.Request(IANA_RDAP_BOOTSTRAP, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=SOCKET_TIMEOUT) as resp:
        _bootstrap_cache = json.loads(resp.read().decode("utf-8"))
    return _bootstrap_cache


def _find_rdap_base_urls(tld: str, bootstrap: dict) -> list[str]:
    for tlds, urls in bootstrap.get("services", []):
        if tld in (t.lower() for t in tlds):
            return urls
    return []


def _query_rdap_sync(domain: str) -> tuple[str, dict | None]:
    """
    Consulta RDAP de forma bloqueante (se llama desde un executor).
    Devuelve (resultado, data) donde resultado es "ok" | "no_encontrado" | "error".
    """
    try:
        bootstrap = _fetch_rdap_bootstrap()
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        log(f"[!] {domain}: no se pudo descargar el bootstrap RDAP de IANA ({e})")
        return "error", None

    tld = get_tld(domain)
    base_urls = _find_rdap_base_urls(tld, bootstrap)

    if not base_urls:
        log(f"[!] {domain}: IANA no tiene servidor RDAP registrado para .{tld}")
        return "error", None

    for base in base_urls:
        url = base.rstrip("/") + f"/domain/{domain}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=SOCKET_TIMEOUT) as resp:
                return "ok", json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "no_encontrado", None
            log(f"[!] {domain}: HTTP {e.code} consultando {url}")
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            log(f"[!] {domain}: error consultando {url} ({e})")

    return "error", None


def _vcard_field(vcard_array, name: str) -> str | None:
    """Extrae valores de estructura jCard RFC 7095."""
    if not vcard_array or len(vcard_array) < 2:
        return None
    for campo in vcard_array[1]:
        if campo[0] == name:
            val = campo[3]
            if isinstance(val, list):
                if name == "adr" and len(val) >= 7:
                    # En jCard: [pobox, ext, street, locality, region, code, country]
                    return val[6] or val[4] or None
                return ", ".join(str(x) for x in val if x)
            val_str = str(val).strip()
            if name == "tel" and val_str.lower().startswith("tel:"):
                val_str = val_str[4:]
            return val_str
    return None


def _extract_iana_id(entity: dict) -> str | None:
    """Extrae el IANA ID numérico del registrador de la entidad."""
    for pub in entity.get("publicIds", []):
        t = (pub.get("type") or "").lower()
        if "iana" in t or "registrar" in t:
            return str(pub.get("identifier") or "").strip()
    return None


def _scan_entities(entities: list[dict]) -> dict:
    """Recorre las entidades RDAP recursivamente y extrae contactos organizados."""
    res = {
        "registrador": None,
        "registrar_iana_id": None,
        "registrar_url": None,
        "abuse_email": None,
        "abuse_tel": None,
        "registrant_name": None,
        "registrant_org": None,
        "registrant_email": None,
        "registrant_phone": None,
        "registrant_country": None,
        "admin_name": None,
        "admin_email": None,
        "admin_phone": None,
        "tech_name": None,
        "tech_email": None,
        "tech_phone": None,
    }

    def procesar(ent: dict):
        roles = [r.lower() for r in ent.get("roles", [])]
        vcard = ent.get("vcardArray")
        fn = _vcard_field(vcard, "fn")
        org = _vcard_field(vcard, "org")
        email = _vcard_field(vcard, "email")
        tel = _vcard_field(vcard, "tel")
        country = _vcard_field(vcard, "adr")

        if "registrar" in roles:
            if fn and not res["registrador"]:
                res["registrador"] = fn
            iana_id = _extract_iana_id(ent)
            if iana_id and not res["registrar_iana_id"]:
                res["registrar_iana_id"] = iana_id
            for link in ent.get("links", []):
                if link.get("rel") == "self" or not res["registrar_url"]:
                    res["registrar_url"] = link.get("href")

        if "registrant" in roles:
            if fn and not res["registrant_name"]:
                res["registrant_name"] = fn
            if org and not res["registrant_org"]:
                res["registrant_org"] = org
            if email and not res["registrant_email"]:
                res["registrant_email"] = email
            if tel and not res["registrant_phone"]:
                res["registrant_phone"] = tel
            if country and not res["registrant_country"]:
                res["registrant_country"] = country

        if any(r in roles for r in ("administrative", "admin")):
            if fn and not res["admin_name"]:
                res["admin_name"] = fn
            if email and not res["admin_email"]:
                res["admin_email"] = email
            if tel and not res["admin_phone"]:
                res["admin_phone"] = tel

        if any(r in roles for r in ("technical", "tech")):
            if fn and not res["tech_name"]:
                res["tech_name"] = fn
            if email and not res["tech_email"]:
                res["tech_email"] = email
            if tel and not res["tech_phone"]:
                res["tech_phone"] = tel

        if "abuse" in roles:
            if email and not res["abuse_email"]:
                res["abuse_email"] = email
            if tel and not res["abuse_tel"]:
                res["abuse_tel"] = tel

        for sub in ent.get("entities", []):
            procesar(sub)

    for e in entities:
        procesar(e)

    return res


def parse_rdap(data: dict) -> dict:
    """Extrae todos los campos relevantes y estructurados de un JSON RDAP."""
    events = {e.get("eventAction"): e.get("eventDate") for e in data.get("events", [])}
    nameservers = [ns.get("ldhName") for ns in data.get("nameservers", []) if ns.get("ldhName")]

    info_entidades = _scan_entities(data.get("entities", []))

    # Buscar URL del registro en los links principales
    registro_url = None
    for link in data.get("links", []):
        if link.get("rel") == "self":
            registro_url = link.get("href")

    # Estados EPP limpios
    status = []
    for s in data.get("status", []):
        clean_status = s.replace("https://icann.org/epp#", "").replace("http://icann.org/epp#", "")
        status.append(clean_status)

    # DNSSEC
    dnssec = data.get("secureDNS", {})
    dnssec_status = "Signed" if dnssec.get("signed") else "Unsigned"

    # Fecha de actualización de la base de datos
    last_update = events.get("last update of RDAP database") or events.get("last changed")

    resultado = {
        "dominio": data.get("ldhName", ""),
        "registry_domain_id": data.get("handle", ""),
        "handle": data.get("handle", ""),
        "fecha_registro": events.get("registration"),
        "fecha_actualizacion": events.get("last changed"),
        "fecha_expiracion": events.get("expiration"),
        "fecha_expiracion_registrador": events.get("expiration"),
        "nameservers": nameservers,
        "estado_dominio": status,
        "estado_raw": data.get("status", []),
        "dnssec": dnssec_status,
        "registro_url": registro_url,
        "rdap_url_registro": registro_url,
        "rdap_url_registrador": info_entidades.get("registrar_url"),
        "last_update": last_update,
        "rdap_fecha_actualizacion": last_update,
        "raw_data": data,
    }
    resultado.update(info_entidades)
    return resultado


def format_resumen(info: dict) -> str:
    """Formatea la ficha completa en texto legible agrupado por categorías."""
    lines = [
        "======================================================================",
        f"  FICHA DE DOMINIO: {info.get('dominio') or 'Desconocido'}",
        "======================================================================",
        "",
        "[ Dominio ]",
        f"  • Domain Name:             {info.get('dominio') or '—'}",
        f"  • Registry Domain ID:      {info.get('registry_domain_id') or info.get('handle') or '—'}",
        f"  • Punycode:                {info.get('punycode') or info.get('dominio') or '—'}",
        "",
        "[ Propietario ]",
        f"  • Registrant Name:         {info.get('registrant_name') or '—'}",
        f"  • Registrant Organization: {info.get('registrant_org') or '—'}",
        f"  • Registrant Email:        {info.get('registrant_email') or '—'}",
        f"  • Registrant Phone:        {info.get('registrant_phone') or '—'}",
        f"  • Registrant Country:      {info.get('registrant_country') or '—'}",
        "",
        "[ Administración ]",
        f"  • Admin Name:              {info.get('admin_name') or '—'}",
        f"  • Admin Email:             {info.get('admin_email') or '—'}",
        f"  • Admin Phone:             {info.get('admin_phone') or '—'}",
        "",
        "[ Técnico ]",
        f"  • Tech Name:               {info.get('tech_name') or '—'}",
        f"  • Tech Email:              {info.get('tech_email') or '—'}",
        f"  • Tech Phone:              {info.get('tech_phone') or '—'}",
        "",
        "[ Registrador ]",
        f"  • Registrar:               {info.get('registrador') or '—'}",
        f"  • Registrar IANA ID:       {info.get('registrar_iana_id') or '—'}",
        f"  • Registrar URL:           {info.get('registrar_url') or '—'}",
        f"  • Abuse Email:             {info.get('abuse_email') or '—'}",
        f"  • Abuse Phone:             {info.get('abuse_tel') or '—'}",
        "",
        "[ Fechas ]",
        f"  • Creation Date:           {info.get('fecha_registro') or '—'}",
        f"  • Updated Date:            {info.get('fecha_actualizacion') or '—'}",
        f"  • Expiration Date:         {info.get('fecha_expiracion') or '—'}",
        "",
        "[ Servidores DNS & Seguridad ]",
        f"  • DNSSEC:                  {info.get('dnssec') or 'Unsigned'}",
        "  • Name Servers:",
    ]

    ns_list = info.get("nameservers") or []
    if ns_list:
        for ns in ns_list:
            lines.append(f"      - {ns}")
    else:
        lines.append("      - (Sin servidores detectados)")

    lines.extend([
        "",
        "[ Estado del Dominio ]",
    ])
    estados = info.get("estado_dominio") or []
    if estados:
        for est in estados:
            lines.append(f"  • {est}")
    else:
        lines.append("  • (Sin estados registrados)")

    lines.extend([
        "",
        "[ Quién responde ]",
        f"  • Fuente:                  {info.get('fuente') or '—'}",
        f"  • Last update of database: {info.get('last_update') or info.get('rdap_fecha_actualizacion') or '—'}",
        "======================================================================",
    ])

    return "\n".join(lines)


def format_rdap(data: dict) -> str:
    """Compatibilidad con versiones previas."""
    info = parse_rdap(data)
    info["fuente"] = "RDAP"
    return format_resumen(info)



# --------------------------------------------------------------------------- #
# WHOIS clásico (puerto 43)
# --------------------------------------------------------------------------- #

_WHOIS_NO_MATCH = re.compile(
    r"(no match|not found|no data found|no entries found|status:\s*free|"
    r"no se encontr|domain not found|available for registration)",
    re.IGNORECASE,
)

_WHOIS_PATTERNS = {
    "dominio": re.compile(r"^\s*Domain Name:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "registry_domain_id": re.compile(r"^\s*Registry Domain ID:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "registrador": re.compile(r"^\s*Registrar:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "registrar_iana_id": re.compile(r"^\s*Registrar IANA ID:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "registrar_url": re.compile(r"^\s*(?:Registrar URL|Referral URL):\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "abuse_email": re.compile(r"^\s*Registrar Abuse Contact Email:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "abuse_tel": re.compile(r"^\s*Registrar Abuse Contact Phone:\s*(.+)$", re.IGNORECASE | re.MULTILINE),

    "registrant_name": re.compile(r"^\s*Registrant Name:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "registrant_org": re.compile(r"^\s*Registrant Organization:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "registrant_email": re.compile(r"^\s*Registrant (?:Email|Contact Email):\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "registrant_phone": re.compile(r"^\s*Registrant Phone:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "registrant_country": re.compile(r"^\s*Registrant Country:\s*(.+)$", re.IGNORECASE | re.MULTILINE),

    "admin_name": re.compile(r"^\s*Admin Name:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "admin_email": re.compile(r"^\s*Admin (?:Email|Contact Email):\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "admin_phone": re.compile(r"^\s*Admin Phone:\s*(.+)$", re.IGNORECASE | re.MULTILINE),

    "tech_name": re.compile(r"^\s*Tech Name:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "tech_email": re.compile(r"^\s*Tech (?:Email|Contact Email):\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "tech_phone": re.compile(r"^\s*Tech Phone:\s*(.+)$", re.IGNORECASE | re.MULTILINE),

    "fecha_registro": re.compile(
        r"^\s*(?:Creation Date|Registered on|created|Registration Time):\s*(.+)$", re.IGNORECASE | re.MULTILINE
    ),
    "fecha_actualizacion": re.compile(
        r"^\s*(?:Updated Date|Last Updated|changed|Modified|Last Modified):\s*(.+)$", re.IGNORECASE | re.MULTILINE
    ),
    "fecha_expiracion": re.compile(
        r"^\s*(?:Registry Expiry Date|Expiration Date|Registrar Registration Expiration Date|paid-till|expire|Expiry Date):\s*(.+)$",
        re.IGNORECASE | re.MULTILINE,
    ),
    "dnssec": re.compile(r"^\s*DNSSEC:\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    "last_update": re.compile(
        r"(?:>>>\s*Last update of (?:WHOIS|whois) database:\s*|Last update of whois database:\s*)(.+?)(?:<<<|$)",
        re.IGNORECASE | re.MULTILINE,
    ),
}

_WHOIS_STATUS = re.compile(r"^\s*Domain Status:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_WHOIS_NS = re.compile(r"^\s*(?:Name Server|nserver):\s*(\S+)", re.IGNORECASE | re.MULTILINE)


def _whois_query_sync(server: str, query: str, port: int = 43) -> str:
    with socket.create_connection((server, port), timeout=SOCKET_TIMEOUT) as sock:
        sock.sendall((query + "\r\n").encode("utf-8"))
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks).decode("utf-8", errors="replace")


def _find_whois_referral(response: str) -> str | None:
    for line in response.splitlines():
        if line.lower().startswith("whois:"):
            valor = line.split(":", 1)[1].strip()
            if valor:
                return valor
    return None


def _find_registrar_whois(texto: str) -> str | None:
    """Busca si el servidor del registro devuelve un servidor WHOIS específico del registrador."""
    for line in texto.splitlines():
        if "registrar whois server:" in line.lower():
            partes = line.split(":", 1)
            if len(partes) > 1:
                serv = partes[1].strip()
                if serv:
                    return serv
    return None


def _query_whois_sync(domain: str) -> tuple[str, str]:
    """Devuelve (resultado, texto_crudo) con resultado en "ok" | "no_encontrado" | "error"."""
    tld = get_tld(domain)

    try:
        iana_resp = _whois_query_sync(IANA_WHOIS_SERVER, tld)
    except OSError as e:
        log(f"[!] {domain}: no se pudo contactar a {IANA_WHOIS_SERVER} ({e})")
        return "error", ""

    referral = _find_whois_referral(iana_resp)
    if not referral:
        log(f"[!] {domain}: IANA no devolvió un servidor WHOIS para .{tld}")
        return "error", iana_resp

    try:
        texto_registry = _whois_query_sync(referral, domain)
    except OSError as e:
        log(f"[!] {domain}: no se pudo contactar a {referral} ({e})")
        return "error", ""

    if _WHOIS_NO_MATCH.search(texto_registry):
        return "no_encontrado", texto_registry

    # Thick WHOIS: si el registro remite a un servidor específico del registrador
    servidor_registrador = _find_registrar_whois(texto_registry)
    if servidor_registrador and servidor_registrador.lower() != referral.lower():
        try:
            texto_registrar = _whois_query_sync(servidor_registrador, domain)
            if texto_registrar and not _WHOIS_NO_MATCH.search(texto_registrar):
                return "ok", texto_registry + "\n" + texto_registrar
        except OSError:
            pass

    return "ok", texto_registry


def parse_whois(texto: str) -> dict:
    """Extracción best-effort de campos WHOIS en texto libre."""

    def buscar(patron: re.Pattern) -> str | None:
        m = patron.search(texto)
        if not m:
            return None
        val = m.group(1).strip()
        val = re.sub(r"\(https?://\S+\)", "", val).strip()
        return val if val else None

    raw_status = [m.group(1).strip() for m in _WHOIS_STATUS.finditer(texto)]
    clean_status = sorted(
        set(
            re.sub(r"\(https?://\S+\)", "", s).strip()
            for s in raw_status
            if s and not s.startswith("http")
        )
    )

    dnssec_val = buscar(_WHOIS_PATTERNS["dnssec"])
    if dnssec_val:
        dnssec_limpio = "Signed" if "signed" in dnssec_val.lower() else "Unsigned"
    else:
        dnssec_limpio = "Unsigned"

    resultado = {}
    for campo, patron in _WHOIS_PATTERNS.items():
        if campo == "dnssec":
            resultado[campo] = dnssec_limpio
        else:
            resultado[campo] = buscar(patron)

    resultado["handle"] = resultado.get("registry_domain_id")
    resultado["estado_dominio"] = clean_status
    resultado["nameservers"] = sorted({m.group(1).lower() for m in _WHOIS_NS.finditer(texto)})

    return resultado


# --------------------------------------------------------------------------- #
# Consulta unificada de un dominio / URL
# --------------------------------------------------------------------------- #

async def consultar_dominio(domain_or_url: str, forzar_whois: bool, loop: asyncio.AbstractEventLoop) -> dict:
    """Consulta un dominio o URL: RDAP primero (salvo que se fuerce WHOIS), con fallback a WHOIS."""
    dominio, punycode = limpiar_dominio_o_url(domain_or_url)
    if not dominio:
        return {
            "dominio": domain_or_url,
            "punycode": "",
            "status": "error",
            "error_msg": "Entrada vacía o inválida",
        }

    base = {
        "dominio": dominio,
        "punycode": punycode,
        "status": "error",
        "registry_domain_id": None,
        "handle": None,

        # Propietario
        "registrant_name": None,
        "registrant_org": None,
        "registrant_email": None,
        "registrant_phone": None,
        "registrant_country": None,

        # Administración
        "admin_name": None,
        "admin_email": None,
        "admin_phone": None,

        # Técnico
        "tech_name": None,
        "tech_email": None,
        "tech_phone": None,

        # Registrador
        "registrador": None,
        "registrar_iana_id": None,
        "registrar_url": None,
        "registro_url": None,
        "abuse_email": None,
        "abuse_tel": None,

        # Fechas
        "fecha_registro": None,
        "fecha_actualizacion": None,
        "fecha_expiracion": None,
        "fecha_expiracion_registrador": None,

        # Servidores DNS & Seguridad
        "nameservers": [],
        "dnssec": "Unsigned",

        # Estado
        "estado_dominio": [],

        # Quién responde
        "last_update": None,
        "fuente": None,
        "raw_data": None,
    }

    consulta_target = punycode if punycode else dominio

    if not forzar_whois:
        resultado, data = await loop.run_in_executor(None, _query_rdap_sync, consulta_target)

        if resultado == "ok" and data is not None:
            parsed = parse_rdap(data)
            base.update({k: v for k, v in parsed.items() if v is not None or k in ("nameservers", "estado_dominio")})
            base["dominio"] = dominio
            base["punycode"] = punycode
            base["status"] = "ok"
            base["fuente"] = "RDAP"
            log(f"[+] {dominio} -> ok (RDAP)")
            return base

        if resultado == "no_encontrado":
            base["status"] = "no_encontrado"
            base["fuente"] = "RDAP"
            log(f"[-] {dominio} -> libre (RDAP)")
            return base

        log(f"[i] {dominio}: RDAP no disponible, probando WHOIS...")

    resultado, texto = await loop.run_in_executor(None, _query_whois_sync, consulta_target)

    if resultado == "ok":
        parsed = parse_whois(texto)
        base.update({k: v for k, v in parsed.items() if v is not None or k in ("nameservers", "estado_dominio")})
        base["dominio"] = dominio
        base["punycode"] = punycode
        base["status"] = "ok"
        base["fuente"] = "WHOIS"
        log(f"[+] {dominio} -> ok (WHOIS)")
    elif resultado == "no_encontrado":
        base["status"] = "no_encontrado"
        base["fuente"] = "WHOIS"
        log(f"[-] {dominio} -> libre (WHOIS)")
    else:
        base["status"] = "error"
        base["fuente"] = "WHOIS"
        log(f"[!] {dominio} -> error")

    return base


# --------------------------------------------------------------------------- #
# Modo batch (el que usa la GUI)
# --------------------------------------------------------------------------- #

async def ejecutar_batch(
    dominios: list[str],
    salida: Path,
    concurrencia: int,
    delay: float,
    forzar_whois: bool,
) -> None:
    """Consulta varios dominios en paralelo (limitado por semáforo) y escribe
    cada resultado en 'salida' (.jsonl) apenas está listo, para que la GUI
    pueda leerlo incrementalmente mientras el proceso sigue corriendo."""

    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text("", encoding="utf-8")  # truncar si ya existía

    semaforo = asyncio.Semaphore(max(1, concurrencia))
    lock_archivo = asyncio.Lock()
    loop = asyncio.get_running_loop()

    async def trabajar(domain: str) -> None:
        async with semaforo:
            if delay > 0:
                await asyncio.sleep(delay)

            resultado = await consultar_dominio(domain, forzar_whois, loop)

            async with lock_archivo:
                with open(salida, "a", encoding="utf-8") as f:
                    f.write(json.dumps(resultado, ensure_ascii=False) + "\n")
                    f.flush()

    tareas = [asyncio.create_task(trabajar(d)) for d in dominios]

    try:
        await asyncio.gather(*tareas)
    except asyncio.CancelledError:
        for t in tareas:
            t.cancel()
        raise


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Consulta de dominios y URLs vía RDAP con fallback a WHOIS clásico."
    )
    parser.add_argument("domain", nargs="?", help="Dominio o URL a consultar, ej: ejemplo.com o https://sitio.com")
    parser.add_argument("--whois", action="store_true", help="Forzar WHOIS clásico (saltear RDAP)")
    parser.add_argument("--json", action="store_true", help="Imprimir el JSON crudo (modo individual)")

    parser.add_argument("--batch", metavar="ARCHIVO", help="Archivo con un dominio o URL por línea")
    parser.add_argument("-o", "--output", metavar="BASE", help="Ruta base de salida (escribe BASE.jsonl)")
    parser.add_argument("--concurrencia", type=int, default=4, help="Consultas simultáneas en modo batch")
    parser.add_argument("--delay", type=float, default=0.3, help="Pausa en segundos antes de cada consulta")

    args = parser.parse_args(argv)

    # ------------------------------------------------------------- #
    # Modo batch
    # ------------------------------------------------------------- #
    if args.batch:
        if not args.output:
            parser.error("--batch requiere -o/--output")

        ruta_dominios = Path(args.batch)
        if not ruta_dominios.exists():
            log(f"[!] No existe el archivo de dominios: {ruta_dominios}")
            return 1

        dominios = [
            l.strip()
            for l in ruta_dominios.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        if not dominios:
            log("[!] El archivo de dominios está vacío")
            return 1

        salida = Path(f"{args.output}.jsonl")
        log(f"[i] {len(dominios)} dominios/URLs, concurrencia={args.concurrencia}, delay={args.delay}s")

        try:
            asyncio.run(ejecutar_batch(dominios, salida, args.concurrencia, args.delay, args.whois))
        except KeyboardInterrupt:
            log("[i] Interrumpido por el usuario")
            return 130

        log(f"[i] Terminado. Resultados en {salida}")
        return 0

    # ------------------------------------------------------------- #
    # Modo individual (CLI interactiva)
    # ------------------------------------------------------------- #
    if not args.domain:
        parser.error("se requiere un dominio/URL, o usar --batch")

    entrada = args.domain.strip()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        resultado = loop.run_until_complete(consultar_dominio(entrada, args.whois, loop))
    finally:
        loop.close()

    if args.json:
        print(json.dumps(resultado, indent=2, ensure_ascii=False))
    else:
        print(format_resumen(resultado))

    return 0


if __name__ == "__main__":
    sys.exit(main())