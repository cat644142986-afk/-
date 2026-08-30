# -*- mode: python ; coding: utf-8 -*-
import os

from PyInstaller.utils.hooks import collect_all, copy_metadata


block_cipher = None
datas = []
binaries = []
hiddenimports = [
    'PIL.Image',
    'requests',
    'torch',
    'transformers',
    'transformers.models.grounding_dino',
    'transformers.models.grounding_dino.modeling_grounding_dino',
    'transformers.models.grounding_dino.processing_grounding_dino',
]

for package in ['torch', 'transformers', 'tokenizers', 'safetensors', 'huggingface_hub']:
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

for package in ['torch', 'transformers', 'tokenizers', 'safetensors', 'huggingface-hub', 'Pillow']:
    try:
        datas += copy_metadata(package)
    except Exception:
        pass

a = Analysis(
    ['python/grounding_runtime_worker.py'],
    pathex=['python'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'notebook', 'IPython', 'pytest',
        'tensorflow', 'pandas', 'sklearn', 'sqlalchemy',
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
    name='grounding-runtime',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='grounding-runtime',
)

