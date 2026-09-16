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

EL MODELO NO VA DENTRO en ningún caso: son ~640 MB. Se descarga a ./modelos/
en el primer arranque.

    pyinstaller build/voziris.spec --noconfirm --distpath build/dist --workpath build/work

Las rutas se resuelven desde la carpeta del .spec (SPECPATH), no desde el
directorio de trabajo, para que funcione desde cualquier sitio.
"""

import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

RAIZ = os.path.abspath(os.path.join(SPECPATH, ".."))

# onnx-asr lee su propia versión con importlib.metadata al importarse; sin los
# metadatos del paquete revienta con PackageNotFoundError (visto en VOZ-61).
METADATOS = copy_metadata("onnx-asr") + copy_metadata("huggingface_hub") + copy_metadata("onnxruntime")

# onnx-asr carga sus bancos de filtros (preprocessors/data/fbanks.npz) desde el
# paquete; PyInstaller no los ve porque no son módulos.
DATOS_ONNX_ASR = collect_data_files("onnx_asr")

# VOZ-72: sherpa-onnx trae sus DLL (y su propio onnxruntime) en sherpa_onnx/lib;
# PyAV lleva ffmpeg en av.libs, que recoge el hook de pyinstaller-hooks-contrib.
BINARIOS_SHERPA = collect_dynamic_libs("sherpa_onnx")

block_cipher = None

a = Analysis(
    [os.path.join(RAIZ, "src", "voziris", "__main__.py")],
    pathex=[os.path.join(RAIZ, "src")],
    binaries=BINARIOS_SHERPA,
    datas=[
        (os.path.join(RAIZ, "config.ejemplo.toml"), "."),
        (os.path.join(RAIZ, "ATRIBUCIONES.md"), "."),   # obligación de CC-BY-4.0: viaja con el binario
        (os.path.join(RAIZ, "LICENSE"), "."),
        (os.path.join(RAIZ, "assets"), "assets"),
    ] + METADATOS + DATOS_ONNX_ASR,
    hiddenimports=[
        "onnx_asr",
        "onnx_asr.models",
        "onnxruntime",
        "huggingface_hub",
        "sounddevice",
        "soundfile",
        "sherpa_onnx",
        "av",
        "pystray._win32",
        "win32gui",
        "win32con",
        "win32api",
        "win32process",
        "pythoncom",
        "win32com.client",
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Nada de esto se usa y engorda el paquete de forma notable
        "torch", "tensorflow", "transformers", "matplotlib",
        "scipy", "pandas", "IPython", "pytest", "faster_whisper", "ctranslate2",
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
    icon=os.path.join(RAIZ, "assets", "voziris.ico"),
    uac_admin=False,    # F1: nunca pide administrador
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
