# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Product Atelier Python backend (onedir mode)
import os, sys
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

block_cipher = None

# Only collect needed rembg sessions (birefnet ONNX only)
hidden = [
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'onnxruntime',
    'onnxruntime.capi._pybind_state',
    'onnxruntime.capi.onnxruntime_inference_collection',
    'rembg',
    'rembg.sessions.birefnet_general',
    'rembg.sessions.base',
    'rembg.session_factory',
    'PIL._tkinter_finder',
    'multipart',
    'multipart.multipart',
    'multipart.exceptions',
    'starlette',
    'anyio',
    'PIL.Image',
    'PIL.ImageEnhance',
    'PIL.ImageFilter',
    'PIL.ImageOps',
    'numpy',
    'scipy',
    'scipy.ndimage',
    'skimage',
    'skimage.morphology',
    'pymatting',
    'pymatting.alpha',
    'pymatting.alpha.estimate_alpha_cf',
    'pymatting.foreground',
    'pymatting.foreground.estimate_foreground_ml',
    'pymatting.util',
    'pymatting.util.util',
    'numba',
    'numba._devicearray',
    'certifi',
    'requests',
    'requests.adapters',
    'urllib3',
    'charset_normalizer',
    'idna',
    'numba.core',
    'llvmlite',
]

datas = []
for entry in collect_data_files('rembg'):
    datas.append(entry)
datas += collect_data_files('onnxruntime')
datas += collect_data_files('scipy')
datas += collect_data_files('skimage')
datas += collect_data_files('numba')
datas += collect_data_files('llvmlite')
datas += collect_data_files('pymatting')
datas += collect_data_files('certifi')
datas += collect_data_files('requests')
datas += collect_data_files('urllib3')
# Runtime-owned offline query contract. `server.py` imports semantic_query as a
# top-level module in the sidecar, so keep the JSON at the PyInstaller internal
# root where `Path(__file__).with_name(...)` resolves it.
datas.append((os.path.join('python', 'semantic_query_lexicon.json'), '.'))
# Pinned optional model-pack contract. The 689 MB weights remain external;
# only this small manifest is embedded so the slim sidecar can verify them.
datas.append((os.path.join('docs', 'model-artifacts', 'grounding-dino-tiny.json'), 'model-artifacts'))
# Package metadata needed by pymatting/numba/llvmlite version checks
for pkg in ['pymatting', 'numba', 'llvmlite', 'rembg', 'onnxruntime', 'scipy', 'scikit-image',
           'PIL', 'numpy', 'fastapi', 'uvicorn', 'python-multipart', 'starlette', 'anyio',
           'certifi', 'requests', 'urllib3', 'charset_normalizer', 'idna']:
    try:
        datas += copy_metadata(pkg)
    except: pass

excludes = [
    'tkinter', 'matplotlib', 'notebook', 'IPython',
    # Heavy ML packages not needed (BiRefNet uses ONNX runtime)
    'torch', 'torchvision', 'torchaudio', 'torchtext',
    'transformers', 'timm', 'tokenizers', 'sentencepiece', 'hf_xet', 'huggingface_hub',
    'av', 'imageio', 'imageio_ffmpeg',
    'sympy', 'networkx', 'mpmath',
    'jinja2', 'markupsafe',
    'triton', 'pytorch_lightning', 'lightning',
    'tensorboard', 'wandb',
    'pytest',
    'pydub',
    'grounding_runtime_worker',
    'cv2', 'opencv_python', 'opencv_python_headless',
    # Runtime does not use the developer/tooling surfaces that recursive
    # collect_submodules previously pulled into the portable sidecar.
    'pandas', 'sklearn', 'sqlalchemy', 'dask', 'cupy', 'tensorflow',
    'onnxruntime.tools', 'onnxruntime.quantization', 'onnxruntime.transformers',
    'numba.tests', 'numba.cuda', 'llvmlite.tests',
]

a = Analysis(
    ['python/server.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
    name='python-server',
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
    name='python-server',
)






