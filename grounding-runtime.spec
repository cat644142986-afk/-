# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, copy_metadata


block_cipher = None
datas = []
binaries = []
hiddenimports = [
    'PIL.Image',
    'requests',
    'httpx',
    'torch',
    'transformers',
    'transformers.models.grounding_dino',
    'transformers.models.grounding_dino.modeling_grounding_dino',
    'transformers.models.grounding_dino.processing_grounding_dino',
    'transformers.models.swin.configuration_swin',
    'transformers.models.swin.modeling_swin',
    'transformers.models.bert.configuration_bert',
    'transformers.models.bert.modeling_bert',
    'transformers.models.bert.tokenization_bert',
    'torchvision.io.image',
    'torchvision.transforms.v2.functional',
    'safetensors.torch',
]

# Transformers exposes model implementations through lazy import maps. Keep
# those dynamic imports explicit and bounded to this one detector, its Swin
# image backbone, and its BERT text backbone. A broad package sweep would
# silently drag unrelated audio, web-server, ONNX and training stacks into the
# optional runtime.
for package in [
    'transformers.models.grounding_dino',
    'transformers.models.swin',
    'transformers.models.bert',
    'tokenizers',
]:
    hiddenimports += collect_submodules(package)

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
        'tensorflow', 'pandas', 'sklearn', 'sqlalchemy', 'scipy',
        'onnxruntime', 'onnx', 'cv2', 'av', 'torchaudio', 'timm',
        'fastapi', 'uvicorn', 'pydantic', 'opentelemetry',
        'torch.utils.tensorboard',
        'numba', 'llvmlite', 'pydub', 'faiss', 'accelerate',
        'aiohttp', 'sentencepiece', 'typer',
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
    # `--probe` is a machine-readable JSON contract consumed through stdout.
    # The parent process still launches the worker with CREATE_NO_WINDOW.
    console=True,
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
