# -*- mode: python ; coding: utf-8 -*-
"""How PyInstaller builds Colortable Editor:

    pyinstaller colortable_editor.spec --noconfirm

A folder, dist/ColortableEditor/, on both systems (with no console window on Windows). The release
workflow zips it into ColortableEditor-Windows.zip on Windows; on Linux packaging/build_appimage.sh wraps it into
ColortableEditor-x86_64.AppImage. COLORTABLE_EDITOR_ONEFILE=1 builds one self-unpacking file instead, which is
slower to start: it unpacks everything, the sample storms included, on every start.

The program finds what ships with it -- the six built-in tables, the sample pictures and
the font -- under sys._MEIPASS, where PyInstaller unpacks them, at the same paths as in this
folder (tcviz.edition.resource_root, colortable_preview.samples_dir, tcviz.fonts).
"""
import os
import sys

from PyInstaller.utils.hooks import copy_metadata

HERE = SPECPATH  # noqa: F821 -- PyInstaller's: this file's folder
NAME = "ColortableEditor"
WINDOWS = sys.platform == "win32"
ONEFILE = os.environ.get("COLORTABLE_EDITOR_ONEFILE", "0").strip() == "1"


def here(*parts):
    return os.path.join(HERE, *parts)


datas = [
    (here("colortable_editor", "builtin_tables.json"), "colortable_editor"),
    (here("vendor", "editor_samples"), os.path.join("vendor", "editor_samples")),
    (here("vendor", "samples"), os.path.join("vendor", "samples")),
    (here("vendor", "fonts"), os.path.join("vendor", "fonts")),
    (here("LICENSE"), "."),
    (here("THIRD_PARTY_NOTICES.md"), "."),
    (here("licenses"), "licenses"),
]
# each library's own license files travel with it (its dist-info folder)
for dist in ('PySide6_Essentials', 'shiboken6', 'numpy', 'matplotlib', 'contourpy', 'cycler', 'fonttools', 'kiwisolver', 'packaging', 'pillow', 'pyparsing', 'python-dateutil', 'six', 'platformdirs', 'defusedxml'):
    datas += copy_metadata(dist)

# Only QtCore, QtGui and QtWidgets are used, and matplotlib only for its color tables: the
# rest is left out to keep the program small (WebEngine alone is hundreds of MB).
excludes = [
    "tkinter", "PyQt5", "PyQt6", "PySide2", "IPython", "jupyter", "notebook", "pytest", "scipy", "pandas",
    "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSvgWidgets", "PySide6.QtXml",
    "PySide6.QtConcurrent", "PySide6.QtDBus", "PySide6.QtUiTools", "PySide6.QtStateMachine",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
    "PySide6.QtHttpServer", "PySide6.QtNfc", "PySide6.QtAxContainer",
]

a = Analysis(
    [here("colortable_editor", "__main__.py")],
    pathex=[HERE],
    datas=datas,
    hooksconfig={"matplotlib": {"backends": "Agg"}},
    excludes=excludes,
)


def _qt_translation(dest):
    """Qt's own words in other languages: the program is in English only."""
    parts = dest.replace("\\", "/").split("/")
    return "PySide6" in parts and "translations" in parts and dest.endswith(".qm")


a.datas = [entry for entry in a.datas if not _qt_translation(entry[0])]

# Linux: Qt plugins the program has no use for -- screens it never runs on (EGL full
# screen, the Linux frame buffer, VNC, Vulkan displays) and the GTK theme, which brings the
# building system's GTK, cairo, pango and a second ICU (about 60 MB) -- and then every
# library nothing left needs. The program draws in its own dark Fusion style either way.
UNUSED_PLUGINS = ("platformthemes/libqgtk3", "platforms/libqeglfs", "platforms/libqlinuxfb",
                  "platforms/libqminimalegl", "platforms/libqvkkhrdisplay", "platforms/libqvnc",
                  "egldeviceintegrations/")


def _prunable(dest):
    """A library kept only if something kept links to it: one at the top of the bundle
    (taken from the system), or one of Qt's own."""
    folder = os.path.dirname(dest).replace("\\", "/")
    return folder == "" or folder.endswith("PySide6/Qt/lib")


def _without_unused(binaries):
    from PyInstaller.depend.bindepend import get_imports

    kept = [b for b in binaries if not any(p in b[0].replace("\\", "/") for p in UNUSED_PLUGINS)]
    by_name = dict()                 # file name -> the prunable entries of that name
    for entry in kept:
        if _prunable(entry[0]):
            by_name.setdefault(os.path.basename(entry[0]), []).append(entry)
    needed = set()
    queue = [s for d, s, t in kept if not _prunable(d) and t in ("BINARY", "EXTENSION")]

    def need(entry):
        if entry[0] in needed:
            return
        needed.add(entry[0])
        if entry[2] == "SYMLINK":    # src is the link, relative to the entry's folder
            target = os.path.normpath(os.path.join(os.path.dirname(entry[0]), entry[1]))
            for other in by_name.get(os.path.basename(target), []):
                if os.path.normpath(other[0]) == target:
                    need(other)
        elif entry[2] in ("BINARY", "EXTENSION"):
            queue.append(entry[1])

    try:
        for name, entries in by_name.items():
            if name.startswith("libpython"):
                for entry in entries:
                    need(entry)
        while queue:
            for name, _path in get_imports(queue.pop()):
                for entry in by_name.get(os.path.basename(name), []):
                    need(entry)
    except Exception as e:  # noqa: BLE001 -- a library not read: keep them all, only bigger
        print("colortable_editor.spec: keeping every library (%s: %s)" % (type(e).__name__, e))
        return kept
    return [b for b in kept if not _prunable(b[0]) or b[0] in needed]


def _without_dangling_links(entries, present):
    """PyInstaller keeps its links (a top-level name for a library in a package folder)
    with the data files: none may point at a library that went."""
    return [e for e in entries
            if e[2] != "SYMLINK" or os.path.normpath(os.path.join(os.path.dirname(e[0]), e[1])) in present]


if sys.platform.startswith("linux"):
    a.binaries = _without_unused(a.binaries)
    present = set(os.path.normpath(e[0]) for e in a.binaries + a.datas)
    a.binaries = _without_dangling_links(a.binaries, present)
    a.datas = _without_dangling_links(a.datas, present)
pyz = PYZ(a.pure)
icon = here("packaging", "ColortableEditor.ico") if WINDOWS else None
version = here("packaging", "version_info.txt") if WINDOWS else None

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=NAME, icon=icon, version=version, console=False,
              upx=False, strip=False)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME, icon=icon, version=version, console=False,
              upx=False, strip=False)
    coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False, strip=False)
