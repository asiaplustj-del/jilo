# -*- mode: python ; coding: utf-8 -*-
# Сборка Jilo:  pyinstaller jilo.spec
import sys
from PyInstaller.utils.hooks import collect_data_files

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

datas = [("jilo/ui", "jilo/ui")]
datas += collect_data_files("imageio_ffmpeg")        # встроенный ffmpeg

a = Analysis(
    ["run_jilo.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=["jilo.core", "jilo.server", "jilo.app"],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pandas", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = "assets/jilo.icns" if IS_MAC else "assets/jilo.ico"
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Jilo", console=False, icon=icon,
          upx=False, target_arch=None)
coll = COLLECT(exe, a.binaries, a.datas, name="Jilo", upx=False)
if IS_MAC:
    app = BUNDLE(coll, name="Jilo.app", icon=icon, bundle_identifier="tj.asiaplus.jilo",
                 info_plist={"CFBundleName": "Jilo", "CFBundleDisplayName": "Jilo",
                             "CFBundleShortVersionString": "1.0", "CFBundleVersion": "1.0",
                             "NSHighResolutionCapable": True, "LSMinimumSystemVersion": "11.0"})
