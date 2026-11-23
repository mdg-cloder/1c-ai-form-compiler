# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: Windows console ONEDIR build of one-c-form-compiler.
# Build with packaging/build_release.ps1 (or: pyinstaller packaging/one-c-form-compiler.spec).
# The package and v8unpack must be installed in the building Python environment.
# The bundled executable runs v8unpack by starting itself with --run-bundled-v8unpack.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

HERE = Path(SPECPATH).resolve()
ROOT = HERE.parent

datas = collect_data_files("one_c_form_compiler") + copy_metadata("v8unpack")
hiddenimports = collect_submodules("v8unpack")

a = Analysis(
    [str(HERE / "entry.py")],
    # Analyse the package installed in the build environment (not src/, which may hold stale caches).
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # anthropic is not bundled: the `ai` command works with ONE_C_FORM_AI_PROVIDER=openai.
    excludes=["tkinter", "unittest", "pydoc_data", "anthropic"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="one-c-form-compiler",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="one-c-form-compiler",
)
