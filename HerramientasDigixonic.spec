# -*- mode: python ; coding: utf-8 -*-
#
# Build: pyinstaller --noconfirm HerramientasDigixonic.spec
#
# Onedir, NO onefile: la GUI relanza este mismo ejecutable como subproceso
# cada vez que una herramienta hace un trabajo pesado (ver
# core/procesos.py:comando_worker). Con --onefile, CADA relanzamiento
# reextrae el bundle completo (~60 MB) a una carpeta temporal nueva antes de
# poder arrancar: eso hacia que la app pareciera colgada. En onedir, el
# subproceso arranca directo desde la carpeta ya descomprimida: mismo
# arranque en frio que "python app.py".
#
# Nota: Scrapy consulta metadata de paquetes en tiempo de ejecucion
# (importlib.metadata.version(...)) para loguear versiones al arrancar el
# crawl. Si falta el .dist-info de alguno de estos paquetes en el bundle,
# el crawl truena con PackageNotFoundError. Por eso el copy_metadata largo.
#
# Al agregar una herramienta nueva que traiga dependencias propias (aparte de
# scrapy/nicegui/pywebview, que ya estan cubiertas), sumar aqui su
# collect_all/copy_metadata igual que las demas.

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = []
binaries = []
hiddenimports = []

for paquete in ('scrapy', 'twisted', 'nicegui', 'pywebview',
                'pymupdf', 'docx', 'yaml'):
    d, b, h = collect_all(paquete)
    datas += d
    binaries += b
    hiddenimports += h

for paquete in (
    'lxml', 'cssselect', 'parsel', 'w3lib', 'Twisted', 'pyOpenSSL',
    'cryptography', 'Scrapy', 'itemadapter', 'itemloaders', 'queuelib',
    'protego', 'PyDispatcher', 'zope.interface', 'service-identity',
    'tldextract', 'packaging', 'defusedxml', 'Brotli', 'nicegui',
    # Herramienta 'Tours a Markdown'.
    'pymupdf', 'python-docx', 'PyYAML',
):
    datas += copy_metadata(paquete)

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HerramientasDigixonic',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='HerramientasDigixonic',
)
