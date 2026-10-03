"""Which program is running: tcviz itself, or Colortable Editor, the color table editor
published on its own for weather hobbyists (the colortable_editor package at the top of the
repo, `python -m colortable_editor`).

Both run the same editor code. What differs is decided here, and read when it matters
rather than when a module is imported, so the one process the tests run in can be either:

* the kinds of table offered: infrared, water vapor, winds and radar in tcviz; infrared
  and water vapor only in Colortable Editor;
* which built-in tables exist: every table tcviz.colormaps defines in tcviz; six in
  Colortable Editor, read from colortable_editor/builtin_tables.json (written from tcviz's
  own by tools/make_editor_builtins.py, and checked to draw the same bytes);
* where a person's tables, favorites and recently deleted tables are kept: tcviz keeps
  ~/.config/tcviz/colortables.json and paths.config_dir() exactly as before; Colortable
  Editor keeps all three in one folder of its own (editor_config_dir);
* the sample pictures (vendor/editor_samples/samples.json), the program's name in
  messages, and whether picture files can be opened (not in Colortable Editor 1.0).

tcviz is the default. colortable_editor.__main__ switches to Colortable Editor before it
builds a window (use()).
"""
import os
import sys
from pathlib import Path

TCVIZ = "tcviz"
COLORTABLE_EDITOR = "colortable-editor"
EDITIONS = (TCVIZ, COLORTABLE_EDITOR)

_NAMES = {TCVIZ: "tcviz", COLORTABLE_EDITOR: "Colortable Editor"}
_KINDS = {TCVIZ: ("ir", "wv", "wind", "radar"), COLORTABLE_EDITOR: ("ir", "wv")}
# Overrides the folder Colortable Editor keeps a person's tables in: a portable copy on a
# USB stick, or a test that must not touch the real one.
CONFIG_DIR_ENV = "COLORTABLE_EDITOR_CONFIG_DIR"

_SOURCE_ROOT = Path(__file__).resolve().parent.parent
_current = TCVIZ


def current():
    """"tcviz" or "colortable-editor"."""
    return _current


def use(name):
    """Run as `name` from now on (colortable_editor.__main__ does, before anything else)."""
    global _current
    if name not in EDITIONS:
        raise ValueError(f"edition must be one of {EDITIONS}, not {name!r}")
    _current = name


def is_editor():
    """Whether this is Colortable Editor rather than tcviz."""
    return _current == COLORTABLE_EDITOR


def app_name():
    """The program's name, as messages say it ("Paste it before you close ...")."""
    return _NAMES[_current]


def kinds():
    """The kinds of color table offered, in the order the Kind boxes list them."""
    return _KINDS[_current]


def opens_picture_files():
    """Whether a picture file of the person's own can be opened in the editor: tcviz's
    saved pictures in tcviz; nothing in Colortable Editor 1.0, which shows its samples."""
    return _current == TCVIZ


def has_pickers():
    """Whether there are palette pickers for a table to be hidden from. Colortable Editor
    has none, so it offers no "Hide"."""
    return _current == TCVIZ


def resource_root():
    """Where the files that ship with the program are: the checkout, or inside a frozen
    build (PyInstaller unpacks them under sys._MEIPASS)."""
    return Path(getattr(sys, "_MEIPASS", _SOURCE_ROOT))


def builtin_tables_path():
    """colortable_editor/builtin_tables.json: Colortable Editor's six built-in tables."""
    return resource_root() / "colortable_editor" / "builtin_tables.json"


def samples_manifest_path():
    """vendor/editor_samples/samples.json: the sample pictures both editions list."""
    return resource_root() / "vendor" / "editor_samples" / "samples.json"


def editor_config_dir():
    """The one folder Colortable Editor keeps a person's tables, favorites and recently
    deleted tables in: ~/.config/Colortable Editor on Linux, %APPDATA%\\Colortable Editor
    on Windows (platformdirs; no author folder above it), unless COLORTABLE_EDITOR_CONFIG_DIR
    names another. Not made until something is saved there."""
    override = os.environ.get(CONFIG_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    import platformdirs
    return Path(platformdirs.user_config_dir("Colortable Editor", appauthor=False, roaming=True))
