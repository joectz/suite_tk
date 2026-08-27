# -*- mode: python ; coding: utf-8 -*-
#
# Build: pyinstaller MapeadorURLs.spec
#
# Onedir, NO onefile: la GUI relanza este mismo ejecutable como subproceso
# cada vez que se hace clic en "Rastrear" (ver comando_worker() en app.py).
# Con --onefile, CADA click reextrae el bundle completo (~60 MB) a una
# carpeta temporal nueva antes de poder arrancar Scrapy: eso es lo que hacia
# que "tardara mucho" y diera la impresion de que no rastreaba nada (el
# usuario cerraba/paraba el crawl mientras el subproceso todavia se estaba
# descomprimiendo). En onedir, el subproceso arranca directo desde la carpeta
# ya descomprimida: mismo arranque en frio que "python app.py".
#
# Nota: Scrapy consulta metadata de paquetes en tiempo de ejecucion
# (importlib.metadata.version(...)) para loguear versiones al arrancar el
# crawl. Si falta el .dist-info de alguno de estos paquetes en el bundle,
# el crawl truena con PackageNotFoundError. Por eso el copy_metadata largo.

from PyInstaller.utils.hooks import collect_all, copy_metadata

datas = []
binaries = []
hiddenimports = ['scraper']

for paquete in ('scrapy', 'twisted', 'nicegui', 'pywebview'):
    d, b, h = collect_all(paquete)
    datas += d
    binaries += b
    hiddenimports += h

for paquete in (
    'lxml', 'cssselect', 'parsel', 'w3lib', 'Twisted', 'pyOpenSSL',
    'cryptography', 'Scrapy', 'itemadapter', 'itemloaders', 'queuelib',
    'protego', 'PyDispatcher', 'zope.interface', 'service-identity',
    'tldextract', 'packaging', 'defusedxml', 'Brotli', 'nicegui',
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
    name='MapeadorURLs',
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
    name='MapeadorURLs',
)
