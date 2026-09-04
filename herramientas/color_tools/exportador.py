"""
exportador.py — Exportación estandarizada de paletas y escalas a múltiples formatos.

Genera archivos listos para usar en:
  - JSON (colores.json)
  - CSS nativo (variables.css)
  - JavaScript / TypeScript (colores.js)
  - Python (colores.py)
  - Tailwind CSS (tailwind.config.js)
  - SCSS (variables.scss)

Por defecto se guarda en ./colores_<dominio>/ en la carpeta local de trabajo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .calculo_color import generar_escala_tailwind
from .extractor_tema import TemasSitio


def generar_formato_css(tema: TemasSitio, escala: dict[int, str]) -> str:
    escala_css = "\n  ".join(f"--color-{k}: {v};" for k, v in escala.items())
    return f"""/* ==========================================================================
   Variables de Color — {tema.dominio}
   ========================================================================== */

:root {{
  /* Colores Principales */
  --color-primary: {tema.primary};
  --color-secondary: {tema.secondary};
  --color-bg: {tema.background};
  --color-surface: {tema.surface};
  --color-text: {tema.text_primary};
  --color-text-muted: {tema.text_muted};

  /* Escala de Tonalidades */
  {escala_css}
}}
"""


def generar_formato_tailwind(tema: TemasSitio, escala: dict[int, str]) -> str:
    escala_items = ",\n      ".join(f"'{k}': '{v}'" for k, v in escala.items())
    return f"""/** @type {{import('tailwindcss').Config}} */
// Dominio: {tema.dominio}

module.exports = {{
  theme: {{
    extend: {{
      colors: {{
        brand: {{
          {escala_items}
        }},
        theme: {{
          primary: '{tema.primary}',
          secondary: '{tema.secondary}',
          background: '{tema.background}',
          surface: '{tema.surface}',
          text: '{tema.text_primary}',
          'text-muted': '{tema.text_muted}',
        }}
      }}
    }}
  }}
}};
"""


def generar_formato_js(tema: TemasSitio, escala: dict[int, str]) -> str:
    escala_js = json.dumps(escala, indent=4)
    return f"""// Colores y Escala — {tema.dominio}

export const colores = {{
  principal: {{
    primario: '{tema.primary}',
    secundario: '{tema.secondary}',
    fondo: '{tema.background}',
    superficie: '{tema.surface}',
    texto: '{tema.text_primary}',
    textoSecundario: '{tema.text_muted}',
  }},
  escala: {escala_js}
}};

export default colores;
"""


def generar_formato_python(tema: TemasSitio, escala: dict[int, str]) -> str:
    escala_py = json.dumps(escala, indent=4)
    return f"""# Colores y Escala — {tema.dominio}

COLORES = {{
    "principal": {{
        "primario": "{tema.primary}",
        "secundario": "{tema.secondary}",
        "fondo": "{tema.background}",
        "superficie": "{tema.surface}",
        "texto": "{tema.text_primary}",
        "texto_secundario": "{tema.text_muted}",
    }},
    "escala": {escala_py}
}}
"""


def generar_formato_scss(tema: TemasSitio, escala: dict[int, str]) -> str:
    lineas = [
        f"// Variables SCSS — {tema.dominio}",
        f"$color-primary: {tema.primary};",
        f"$color-secondary: {tema.secondary};",
        f"$color-bg: {tema.background};",
        f"$color-surface: {tema.surface};",
        f"$color-text: {tema.text_primary};",
        f"$color-text-muted: {tema.text_muted};",
        "",
        "// Escala",
    ]
    for k, v in escala.items():
        lineas.append(f"$color-{k}: {v};")
    return "\n".join(lineas) + "\n"


def exportar_tema_local(
    tema: TemasSitio,
    carpeta_destino: str | Path | None = None,
) -> dict[str, Any]:
    """
    Exporta la paleta y escala a todos los formatos estándar en ./colores_<dominio>/.
    """
    dominio_limpio = tema.dominio.replace(":", "_").replace("/", "_")
    if not carpeta_destino or not str(carpeta_destino).strip():
        destino = Path.cwd() / f"colores_{dominio_limpio}"
    else:
        destino = Path(carpeta_destino).expanduser()

    destino.mkdir(parents=True, exist_ok=True)

    escala = generar_escala_tailwind(tema.primary)

    # 1. JSON
    datos_json = {
        "dominio": tema.dominio,
        "colores_principales": {
            "primario": tema.primary,
            "secundario": tema.secondary,
            "fondo": tema.background,
            "superficie": tema.surface,
            "texto": tema.text_primary,
            "texto_secundario": tema.text_muted,
        },
        "escala": escala,
        "colores_detectados": [p["hex"] for p in tema.paleta_completa[:24]],
    }
    ruta_json = destino / "colores.json"
    ruta_json.write_text(json.dumps(datos_json, indent=2, ensure_ascii=False), encoding="utf-8")

    # 2. CSS
    ruta_css = destino / "variables.css"
    ruta_css.write_text(generar_formato_css(tema, escala), encoding="utf-8")

    # 3. JavaScript
    ruta_js = destino / "colores.js"
    ruta_js.write_text(generar_formato_js(tema, escala), encoding="utf-8")

    # 4. Python
    ruta_py = destino / "colores.py"
    ruta_py.write_text(generar_formato_python(tema, escala), encoding="utf-8")

    # 5. Tailwind
    ruta_tw = destino / "tailwind.config.js"
    ruta_tw.write_text(generar_formato_tailwind(tema, escala), encoding="utf-8")

    # 6. SCSS
    ruta_scss = destino / "variables.scss"
    ruta_scss.write_text(generar_formato_scss(tema, escala), encoding="utf-8")

    archivos = [
        str(ruta_json),
        str(ruta_css),
        str(ruta_js),
        str(ruta_py),
        str(ruta_tw),
        str(ruta_scss),
    ]

    return {
        "ok": True,
        "carpeta": str(destino.resolve()),
        "archivos": archivos,
        "total_archivos": len(archivos),
    }
