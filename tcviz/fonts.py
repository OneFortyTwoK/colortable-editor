"""The one typeface every tcviz picture is lettered in.

JetBrains Mono Nerd Font Mono Bold, the font the user's own satellites.py draws with:
captions, color bars, gridline labels, the radar curtains and the pass, swath and orbit
maps all use it. The bundled copy comes first because the bot's container has no system
fonts at all, nor need a PC the packaged app runs on; the system copy is the same file.
"""
import sys
from pathlib import Path

FONT = "/usr/share/fonts/TTF/JetBrainsMonoNerdFontMono-Bold.ttf"
# Beside the package in a checkout; inside a frozen app, where PyInstaller unpacks the
# program's files (sys._MEIPASS), as colortable_preview.samples_dir finds its samples.
_RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
_BUNDLED = _RESOURCES / "vendor" / "fonts" / "JetBrainsMonoNerdFontMono-Bold.ttf"
CANDIDATES = (str(_BUNDLED), FONT)

_matplotlib_ready = False


def path():
    """The font file to use, or None when neither copy exists."""
    for candidate in CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def pil(size):
    """A PIL font at `size` pixels, falling back to PIL's own only if the file is missing."""
    from PIL import ImageFont

    found = path()
    if found:
        try:
            return ImageFont.truetype(found, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def use_in_matplotlib():
    """Make every matplotlib figure letter in this font from now on. Safe to call often."""
    global _matplotlib_ready
    if _matplotlib_ready:
        return
    found = path()
    if not found:
        return
    import matplotlib
    from matplotlib import font_manager

    font_manager.fontManager.addfont(found)
    name = font_manager.FontProperties(fname=found).get_name()
    matplotlib.rcParams["font.family"] = name
    matplotlib.rcParams["font.weight"] = "bold"
    matplotlib.rcParams["axes.titleweight"] = "bold"
    matplotlib.rcParams["axes.labelweight"] = "bold"
    _matplotlib_ready = True
