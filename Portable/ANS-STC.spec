# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build recipe for the ANS-STC robust steganography GUI.

Build a standalone, dependency-free application (no Python install required on
the target machine)::

    pip install pyinstaller
    pyinstaller ANS-STC.spec

Artifacts land in ``dist/``:

* **macOS**   -> ``dist/ANS-STC.app`` (double-click) and ``dist/ANS-STC/``
* **Windows** -> ``dist/ANS-STC/ANS-STC.exe``
* **Linux**   -> ``dist/ANS-STC/ANS-STC``

PyInstaller does not cross-compile: run this spec once on each target OS to get
that platform's binary.  The ``.spec`` itself is portable and unchanged between
platforms.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

# ---- third-party assets & hidden imports ---------------------------------- #
# customtkinter ships its colour themes and widget assets as *data* files that
# must travel inside the bundle, plus a few sub-modules PyInstaller can miss.
ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")

hiddenimports = [
    "reedsolo",              # optional compiled ext + pure-python fallback
    "brotli",                # text entropy coding for the robust watermark
    "PIL._tkinter_finder",   # lets Pillow locate Tk for CTkImage
]
hiddenimports += ctk_hidden
hiddenimports += collect_submodules("scipy.fft")      # DCT backend
hiddenimports += collect_submodules("scipy.special")  # pulled in by scipy.fft
hiddenimports += collect_submodules("scipy.ndimage")  # perceptual watermark mask
hiddenimports += collect_submodules("scipy.stats")    # steganalysis ROC/rank stats

datas = list(ctk_datas)
binaries = list(ctk_binaries)


a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # keep dev-only / heavyweight libraries out of the shipped bundle
    excludes=["pytest", "matplotlib", "IPython", "tkinter.test", "test"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ANS-STC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX is off: it can corrupt numpy/scipy shared libs
    console=False,           # windowed GUI app (no terminal)
    disable_windowed_traceback=False,
    argv_emulation=True,     # macOS: files dropped on the .app arrive in argv
    target_arch=None,        # build for the host arch (arm64 or x86_64)
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ANS-STC",
)

# macOS application bundle (ignored on Windows/Linux).
app = BUNDLE(
    coll,
    name="ANS-STC.app",
    icon=None,
    bundle_identifier="edu.teoinfo.ansstc",
    info_plist={
        "CFBundleDisplayName": "ANS-STC",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSHighResolutionCapable": True,
    },
)
