# -*- mode: python ; coding: utf-8 -*-

import json
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules


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
build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
if not isinstance(build_info, dict) or build_info.get("schema_version") != 1:
    raise SystemExit("desktop-build-info.json 格式或版本无效")
publishing_profile = str(build_info.get("publishing_profile") or "safe").strip()
if publishing_profile not in {"safe", "authorized_real"}:
    raise SystemExit("desktop-build-info.json 包含不受支持的 publishing_profile")
real_publish_build = publishing_profile == "authorized_real"

datas = [
    (str(frontend), "frontend"),
    (str(root / "utils" / "stealth.min.js"), "utils"),
    (str(build_info_path), "."),
]
datas += collect_data_files("webview")

hiddenimports = collect_submodules("webview")
binaries = []
excludes = [
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "tkinter",
]
if real_publish_build:
    for package_name in ("playwright", "patchright"):
        package_datas, package_binaries, package_hiddenimports = collect_all(
            package_name
        )
        required_driver_files = {
            f"{package_name}\\driver\\node.exe",
            f"{package_name}\\driver\\package\\cli.js",
        }
        collected_driver_files = {
            str(Path(source_path).resolve()).lower()
            for source_path, _ in package_datas
        }
        missing_driver_files = [
            relative_path
            for relative_path in required_driver_files
            if not any(
                source_path.endswith(relative_path.lower())
                for source_path in collected_driver_files
            )
        ]
        if missing_driver_files:
            raise SystemExit(
                f"{package_name} 桌面驱动文件未完整收集: "
                + ", ".join(sorted(missing_driver_files))
            )
        datas += package_datas
        binaries += package_binaries
        hiddenimports += package_hiddenimports
    hiddenimports += collect_submodules("uploader")
    hiddenimports += collect_submodules("myUtils")
else:
    excludes += ["cv2", "numpy", "patchright", "playwright"]

a = Analysis(
    [str(root / "geo_desktop.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
