"""Small color strips of a palette, for the picker and the Manage window.

The picker refills its grid on every keystroke in its search box, and drew each strip
afresh with colormaps.get every time -- 200-odd colormaps built per letter typed. A strip
only changes when the table under its name does, which is exactly what the registry's
generation counts, so strips are kept by (name, generation) and the whole store is dropped
the moment the generation moves on. The tables are looked up through
tcviz.colortable_registry, so Colortable Editor draws its own seven without tcviz's whole
collection.
"""
import numpy as np
from PySide6.QtGui import QImage, QPixmap

from tcviz import colortable_registry

WIDTH, HEIGHT = 72, 14

_pixmaps = {}           # (name, generation, width, height) -> QPixmap, or None for no strip
_generation = None


def palette_swatch(name, width=WIDTH, height=HEIGHT):
    """A strip of `name`'s colormap with the vmax side on the LEFT and the vmin side on the
    right -- so a temperature palette reads warmest to coldest left to right, matching the
    picture's color scale (which puts warm at the top). None for the RGB-composite looks
    (true_color, geocolor, the Day/Night Band...), which colormaps.get cannot sample."""
    global _generation
    generation = colortable_registry.cache_key()
    if generation != _generation:
        _pixmaps.clear()
        _generation = generation
    key = (name, generation, width, height)
    if key not in _pixmaps:
        try:
            cmap = colortable_registry.get(name)[0]
        except Exception:
            _pixmaps[key] = None
        else:
            _pixmaps[key] = colormap_swatch(cmap, width, height)
    return _pixmaps[key]


def colormap_swatch(cmap, width=WIDTH, height=HEIGHT):
    """The same strip for a colormap in hand (not cached)."""
    xs = np.linspace(1.0, 0.0, width)                    # vmax/warm on the left
    row = (cmap(xs)[:, :3] * 255).astype(np.uint8)       # (W, 3)
    arr = np.ascontiguousarray(np.broadcast_to(row, (height, width, 3)))
    image = QImage(arr.data, width, height, 3 * width, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(image.copy())               # .copy() detaches from arr's buffer
