# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


root = Path(SPECPATH).resolve().parent
frontend = root / "sau_frontend" / "dist"
icon_path = root / "desktop" / "geo-icon.png"
build_info_path = root / ".build" / "desktop-build-info.json"
if not (frontend / "index.html").is_file():
    raise SystemExit("缺少 sau_frontend/dist，请先构建 Vue 前端")
if not icon_path.is_file():
    raise SystemExit("缺少 desktop/geo-icon.png")
if not build_info_path.is_file():
    raise SystemExit("缺少 .build/desktop-build-info.json，请通过构建脚本打包")

datas = [
    (str(frontend), "frontend"),
    (str(root / "utils" / "stealth.min.js"), "utils"),
    (str(build_info_path), "."),
]
datas += collect_data_files("webview")

hiddenimports = collect_submodules("webview")

a = Analysis(
    [str(root / "geo_desktop.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "cv2",
        "numpy",
        "patchright",
        "playwright",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GEO-Content-Platform",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(icon_path)],
    version=str(root / "desktop" / "version_info.txt"),
)
