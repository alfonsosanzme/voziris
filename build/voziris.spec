# -*- mode: python ; coding: utf-8 -*-
"""Receta de PyInstaller para Voziris.

MODO onedir, A PROPÓSITO. Ver la decisión D-05 de la especificación:
`--onefile` descomprime todo en %TEMP% en CADA arranque, y con onnxruntime y
sus DLLs dentro son varios segundos. Voziris arranca con Windows y tiene que
responder a un atajo al instante, así que onedir gana.

Sigue siendo portable en todo lo que importa: se copia, va en un USB, no toca
el registro, no pide administrador. Son varios archivos en una carpeta en vez
de uno.

Para cambiar a archivo único: mover `a.binaries`, `a.datas` y `a.zipfiles` al
EXE() y borrar el COLLECT(). Es un cambio de cinco líneas, deliberadamente
fácil de hacer si Alfon decide que prefiere el archivo único.

EL MODELO NO VA DENTRO en ningún caso: son ~680 MB. Se descarga a ./modelos/
en el primer arranque.

    pyinstaller build/voziris.spec --noconfirm
"""

block_cipher = None

a = Analysis(
    ["../src/voziris/__main__.py"],
    pathex=["../src"],
    binaries=[],
    datas=[
        ("../config.ejemplo.toml", "."),
        ("../ATRIBUCIONES.md", "."),   # obligación de CC-BY-4.0: viaja con el binario
    ],
    hiddenimports=[
        "onnx_asr",
        "onnxruntime",
        "sounddevice",
        "pystray._win32",
        "win32clipboard",
        "win32gui",
        "win32process",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Nada de esto se usa y engorda el paquete de forma notable
        "torch", "tensorflow", "transformers", "matplotlib",
        "scipy", "pandas", "IPython", "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="voziris",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX dispara aún más falsos positivos de antivirus (R2)
    console=False,      # app de bandeja: sin consola
    icon="../assets/voziris.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="voziris",
)
