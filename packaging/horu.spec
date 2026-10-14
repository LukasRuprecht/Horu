# PyInstaller spec for Horu.exe: a windowed app in one folder (dist\Horu). Run through
# packaging\build.ps1, which sets up the build environment and fills in BUILD.
# ruff: noqa
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

BUILD = Path(os.environ["HORU_BUILD"])
ICON = str(Path(SPECPATH).parent / "src" / "horu" / "resources" / "horu.ico")

datas = [
    (str(BUILD / "config.example.toml"), "."),
    *collect_data_files("horu"),  # the UI, fonts, icon, JLPT list
    *collect_data_files("rapidocr"),  # its settings and the OCR models (downloaded at build time)
    *collect_data_files("sudachipy"),
    *collect_data_files("sudachidict_full"),
    *collect_data_files("fastanki"),  # Anki's protobuf descriptors
    *copy_metadata("horu"),
    *copy_metadata("fastanki"),
]
hiddenimports = [
    *collect_submodules("horu"),  # providers and text sources are imported by name
    "pystray._win32",
    "keyring.backends.Windows",
]

a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    datas=datas,
    hiddenimports=hiddenimports,
    # Not used by Horu.exe: the optional manga-ocr engine pulls in PyTorch.
    excludes=["manga_ocr", "torch", "transformers", "tkinter", "matplotlib", "IPython", "pytest"],
)
# The Visual C++ runtime: PyInstaller can pick up an old copy (from Python's own folder), and
# packages built with a newer compiler then crash inside it (std::mutex). Bundle the build
# machine's installed runtime instead, which is the newest.
_SYSTEM = Path(os.environ["SystemRoot"]) / "System32"
_RUNTIME = {"msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"}
a.binaries = [
    (dest, str(_SYSTEM / dest) if dest.lower() in _RUNTIME and (_SYSTEM / dest).exists() else src, kind)
    for dest, src, kind in a.binaries
]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Horu",
    icon=ICON,
    console=False,
    upx=False,
    version=str(BUILD / "version.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Horu", upx=False)
