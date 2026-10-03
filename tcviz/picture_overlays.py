"""The furniture tcviz letters onto a finished picture -- the caption in a translucent box
at a corner and the color scale inside the picture -- and how much a small picture is
enlarged before it gets them.

Moved out of tcviz.render, which re-exports every name here, so the color table editor can
draw a picture exactly as tcviz does (colortable_preview.full_picture, the editor's color
scale) without render's maps, coastlines and eye finder: this module needs only numpy,
Pillow and tcviz.fonts.
"""
import math

import numpy as np
from PIL import Image, ImageChops, ImageDraw

from . import fonts


# ----------------------------------------------------------------- picture furniture
#
# How a finished picture is dressed: a translucent black panel with white text at a
# corner for the caption, and the color scale drawn INSIDE the image at bottom-right
# rather than as a strip bolted onto the side. This is the look of the user's own
# satellites.py (its caption()/colorbar()), ported here so the desktop app, the bot and
# that script all produce the same picture.
#
# The proportions are satellites.py's, as fractions of image HEIGHT, so furniture scales
# with the picture instead of being a fixed pixel size on a 400px microwave grid and a
# 5000px full disk alike.
_STYLE_FONT_FRAC, _STYLE_PAD_FRAC, _STYLE_INSET_FRAC = 0.0139, 0.0056, 0.010
# The color bar's own proportions. satellites.py draws a 0.009-of-height bar with 0.8x
# text over 40% of the picture; on these renders that read as a thin sliver against a
# 1900px image, so the bar is wider, taller and labeled at full size. Still fractions
# of image height, so it holds up on a 400px microwave grid and a full disk alike.
_BAR_WIDTH_FRAC = 0.018
_BAR_HEIGHT_FRAC = 0.46
_BAR_FONT_FACTOR = 1.0
# JetBrains Mono Bold is what satellites.py draws with -- see tcviz.fonts, the one place
# every picture takes its font from.


def _style_font(size):
    return fonts.pil(size)


def _style_metrics(height, scale=1.0, font_factor=1.0):
    """(font, size, pad, inset) for furniture drawn on an image this tall."""
    size = max(8, round(height * _STYLE_FONT_FRAC * font_factor * scale))
    pad = max(2, round(height * _STYLE_PAD_FRAC * scale))
    inset = max(2, round(height * _STYLE_INSET_FRAC * scale))
    return _style_font(size), size, pad, inset


def overlay_caption(img, text, scale=1.0, corner="bl", fg=(255, 255, 255),
                    bg=(0, 0, 0), bg_alpha=0.66):
    """satellites.py's caption(): one line in a translucent box at a corner (tl/bl/tr/br).

    Returns a new RGB image rather than drawing in place, because compositing the panel
    is what makes it translucent.
    """
    width, height = img.size
    font, _size, pad, inset = _style_metrics(height, scale)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = right - left, bottom - top
    x = inset if corner[1] == "l" else width - inset - text_w - 2 * pad
    y = inset if corner[0] == "t" else height - inset - text_h - 2 * pad
    draw.rectangle([x, y, x + text_w + 2 * pad, y + text_h + 2 * pad],
                   fill=(*bg, round(255 * bg_alpha)))
    draw.text((x + pad - left, y + pad - top), text, font=font, fill=(*fg, 255))
    return _laid_over(img, layer)


def _laid_over(img, layer):
    """Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB") for a translucent
    panel `layer` drawn the size of the picture -- worked over only the part the panel
    covers. Alpha compositing hands a picture pixel under a fully transparent one through
    untouched, so everywhere else the answer is the picture itself: the same pixels without
    turning the whole frame to RGBA and back: each caption and color bar on a 4000 x 4000
    picture 33-37 -> 17-22 ms, and 200 -> 80-90 MB at its peak (2026-09-29)."""
    box = layer.getbbox()
    if img.mode not in ("RGB", "RGBA") or box is None:
        return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    out = img.convert("RGB")
    panel = Image.alpha_composite(img.crop(box).convert("RGBA"), layer.crop(box))
    out.paste(panel.convert("RGB"), box[:2])
    return out


_MIN_CAPTION_FONT_SIZE = 10


def caption_reserve_px(height):
    """Height at the bottom of an image the caption occupies.

    Used by the gridline labels, which would otherwise have their bottom entries hidden
    under it. Measures the overlay caption (see overlay_caption): its font, its padding
    top and bottom, and the inset holding it off the image edge.
    """
    _font, size, pad, inset = _style_metrics(height)
    return size + 2 * pad + inset


def overlay_colorbar(img, cmap, vmin, vmax, units="", ticks=None, scale=1.0,
                     extra_line=None, mark=None, mark_label=None,
                     height=_BAR_HEIGHT_FRAC, fg=(255, 255, 255), bg=(0, 0, 0),
                     bg_alpha=0.66):
    """satellites.py's colorbar(): the scale INSIDE the picture, bottom-right, warm at top.

    Drawn on a translucent panel rather than bolted to the side, so the image keeps its
    own dimensions -- a storm crop stays square instead of growing a white strip.

    Which values get labeled is still tcviz's own _colorbar_ticks, so an explicit tick
    list (colormaps.PALETTE_TICKS' published microwave breakpoints) and the units
    conversions (Kelvin shown in C, m/s shown in knots) behave exactly as before.

    mark/mark_label put an arrow on the bar at one value and name it above -- the eye
    temperature, as satellites.py marks it. extra_line ("Max: 42 kt") sits under that.
    """
    width, img_h = img.size
    font, size, pad, inset = _style_metrics(img_h, scale, font_factor=_BAR_FONT_FACTOR)
    bar_w = max(5, round(img_h * _BAR_WIDTH_FRAC * scale))
    tick_len = max(3, bar_w // 2)
    arrow = size if mark is not None else 0

    bar_h = round(img_h * height * scale)
    tick_values = _colorbar_ticks(vmin, vmax, units, ticks, bar_h, size)
    # The unit rides on each label rather than sitting alone at the top of the bar: one
    # "K" above a column of numbers is easy to read past, and the top line is worth more
    # as the eye temperature or the wind maximum. Celsius is the exception -- satellites.py
    # heads an IR bar "°C" and leaves the numbers bare, which is the look being matched.
    if units == "C":
        labels = [f"{v - 273.15:.0f}" for v in tick_values]
    elif units == "kt":
        labels = [f"{v * _KT_PER_MS:.0f}kt" for v in tick_values]
    else:
        labels = [f"{v:.0f}{units}" for v in tick_values]
    extra = extra_line if isinstance(extra_line, (list, tuple)) else [extra_line]
    header = [s for s in (mark_label, *extra) if s]
    if units == "C" and not header:
        header = ["\u00b0C"]

    probe = ImageDraw.Draw(img)
    label_w = math.ceil(max(probe.textlength(t, font=font) for t in labels)) if labels else 0
    head_w = math.ceil(max(probe.textlength(t, font=font) for t in header)) if header else 0
    line_h = size + pad // 2

    y1 = img_h - inset - pad                      # bar bottom, just above the image edge
    y0 = y1 - bar_h
    box_r = width - inset
    body_w = arrow + bar_w + tick_len + pad // 2 + label_w
    box_l = box_r - pad - max(body_w, head_w) - pad
    bar_x0 = box_r - pad - label_w - pad // 2 - tick_len - bar_w
    bar_x1 = bar_x0 + bar_w
    top = y0 - len(header) * line_h - pad

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rectangle([box_l, top - pad, box_r, y1 + pad], fill=(*bg, round(255 * bg_alpha)))

    frac = np.linspace(1, 0, max(1, y1 - y0))     # top row = vmax (warm), bottom = vmin
    rgb = (cmap(frac)[:, :3] * 255).round().astype(np.uint8)
    strip = Image.fromarray(np.repeat(rgb[:, None, :], bar_w, axis=1), "RGB").convert("RGBA")
    layer.paste(strip, (bar_x0, y0))

    span = (vmax - vmin) or 1.0
    for value, text in zip(tick_values, labels):
        y = y0 + (vmax - value) / span * (y1 - y0)
        draw.line([bar_x1, y, bar_x1 + tick_len - 1, y], fill=(*fg, 255),
                  width=max(1, size // 12))
        left, t, _r, b = draw.textbbox((0, 0), text, font=font)
        draw.text((bar_x1 + tick_len + pad // 2 - left, y - (b - t) / 2 - t), text,
                  font=font, fill=(*fg, 255))
    for i, text in enumerate(header):
        left, t, _r, _b = draw.textbbox((0, 0), text, font=font)
        draw.text((box_l + pad - left, top + i * line_h - t), text, font=font,
                  fill=(*fg, 255))
    if mark is not None and np.isfinite(mark):
        y = y0 + (vmax - min(max(mark, vmin), vmax)) / span * (y1 - y0)
        draw.line([bar_x0, y, bar_x1 - 1, y], fill=(0, 0, 0, 255), width=3)
        draw.line([bar_x0, y, bar_x1 - 1, y], fill=(*fg, 255), width=1)
        draw.polygon([(bar_x0 - 1, y), (bar_x0 - arrow, y - arrow / 2),
                      (bar_x0 - arrow, y + arrow / 2)], fill=(*fg, 255),
                     outline=(0, 0, 0, 255))
    return _laid_over(img, layer)


def colorbar_preview(cmap, norm, vmax, vmin, units="", size=1600, ticks=None, plate=(40, 40, 40)):
    """The color scale exactly as a picture `size` pixels tall carries it, cut out on its
    own: overlay_colorbar -- the call render() makes -- drawn on a dark plate, and the
    plate trimmed to the scale's panel with the picture's own margin around it. The color
    table editor shows this beside the table, so what it shows cannot drift from the
    picture.

    The arguments are render()'s (cmap, norm, vmax, vmin, units). `norm` changes nothing
    here: the scale in the picture samples the colormap evenly from vmax down to vmin, and
    so does this one. The plate is narrower than the picture -- the scale's place depends
    only on the picture's height and right edge -- which keeps a redraw quick enough to
    follow every edit."""
    del norm
    width = max(64, size // 3)
    base = Image.new("RGB", (width, size), plate)
    drawn = overlay_colorbar(base, cmap, vmin, vmax, units=units, ticks=ticks)
    box = ImageChops.difference(drawn, base).getbbox()
    if box is None:
        return drawn
    _font, _size, _pad, inset = _style_metrics(size)
    left, top, right, bottom = box
    return drawn.crop((max(0, left - inset), max(0, top - inset), min(width, right + inset), min(size, bottom + inset)))


_KT_PER_MS = 1.9438452

# satellites.py's MIN_PX/UNDERSHOOT, and for the same reason: furniture is sized as a
# fraction of image height, so a small grid is enlarged first or the caption and the
# color bar are illegible on it.
_MIN_RENDER_PX = 2000  # a coarse microwave channel's grid can still be well under this
# How far below _MIN_RENDER_PX a whole-numbered enlargement may land before going up to
# the next factor instead -- a tenth either way does not threaten legibility.
_UPSCALE_UNDERSHOOT = 0.9


# The most labels a dBZ scale carries (_colorbar_ticks).
_DBZ_MAX_TICKS = 6


def _colorbar_ticks(vmin, vmax, units, ticks, bar_height, font_size):
    """The values to label on the color bar.

    An explicit `ticks` list always wins -- that is a palette's own published
    breakpoints (see colormaps.PALETTE_TICKS), and inventing round numbers over the top
    of them would describe a scale the image is not using.

    A Celsius scale otherwise gets every 10 degrees, on round tens. The old six
    evenly-spaced values landed on 30-degree steps across an IR range, so reading a
    cloud-top temperature meant interpolating between marks that sat nowhere near the
    thresholds anyone looks for. The step widens only when 10 physically cannot fit --
    labels need room, and a small image with a wide range would stack them.

    A knots scale gets every 10 knots, for the same reason: a scatterometer is read
    against round wind speeds (34, 50, 64 kt), and six evenly-spaced values landed on
    marks like 20/40/61/81/101 that sit nowhere near any of them.

    An m/s scale likewise gets round tens, as NOAA's own SAR wind bar is marked: six
    evenly-spaced values put "21", "31" and "42" at 20.75, 31.1 and 41.5 m/s on the
    0-51.88 m/s SAR wind scale (Sentinel-1D over Polo, 2026-09-27).

    A dBZ scale gets round tens too -- 20, 40, 60 -- but at most six of them, the six the
    radar scale has always carried: on the NEXRAD table's 0-100 dBZ those are the very six
    evenly-spaced values it had, and a radar table of the user's at -10 to 70 dBZ reads
    0/20/40/60 rather than -10/6/22/38/54/70.

    Everything else keeps the six evenly-spaced values: a reflectance percentage has no
    equivalent of "round tens matter here".
    """
    if ticks is not None:
        return [t for t in ticks if vmin <= t <= vmax]
    if units == "dBZ":
        step = 10.0
        while step < (vmax - vmin):
            count = math.floor(vmax / step) - math.ceil(vmin / step) + 1
            if count <= 1 or (count <= _DBZ_MAX_TICKS
                              and bar_height / max(1, count - 1) >= font_size * 1.4):
                break
            step += 10.0
        out, v = [], math.ceil(vmin / step) * step
        while v <= vmax + 1e-6:
            out.append(v)
            v += step
        return out
    if units == "kt":
        # chosen in knots, kept in the data's own m/s -- the labels convert, the
        # positions must not (see overlay_colorbar)
        lo_kt, hi_kt = vmin * _KT_PER_MS, vmax * _KT_PER_MS
        step_kt = 10.0
        while step_kt < (hi_kt - lo_kt):
            count = math.floor(hi_kt / step_kt) - math.ceil(lo_kt / step_kt) + 1
            if count <= 1 or bar_height / max(1, count - 1) >= font_size * 1.4:
                break
            step_kt += 10.0
        out, kt = [], math.ceil(lo_kt / step_kt) * step_kt
        while kt <= hi_kt + 1e-6:
            out.append(kt / _KT_PER_MS)
            kt += step_kt
        return out
    if units == "m/s":
        step = 10.0
        while step < (vmax - vmin):
            count = math.floor(vmax / step) - math.ceil(vmin / step) + 1
            if count <= 1 or bar_height / max(1, count - 1) >= font_size * 1.4:
                break
            step += 10.0
        out, v = [], math.ceil(vmin / step) * step
        while v <= vmax + 1e-6:
            out.append(v)
            v += step
        return out
    if units == "C":
        lo_c, hi_c = vmin - 273.15, vmax - 273.15
        step_c = 10.0
        while step_c < (hi_c - lo_c):
            count = math.floor(hi_c / step_c) - math.ceil(lo_c / step_c) + 1
            if count <= 1 or bar_height / max(1, count - 1) >= font_size * 1.4:
                break
            step_c += 10.0
        out, c = [], math.ceil(lo_c / step_c) * step_c
        while c <= hi_c + 1e-6:
            out.append(c + 273.15)
            c += step_c
        return out
    n_ticks = 6
    return [vmin + (vmax - vmin) * i / (n_ticks - 1) for i in range(n_ticks)]


def _upscale_factor(current_px, resample, target_px=None):
    """How much to enlarge a native-resolution array so it is at least target_px across.

    Whole-numbered when the resample is NEAREST, and that is the whole point. A
    fractional NEAREST enlargement cannot give every source pixel the same size on
    screen: it has to round each one up or down, so the grid comes out with cells of two
    different widths. Measured on a real Meteosat-6 MVIRI crop -- 245 samples enlarged by
    8.1633 -- a middle row held 68 runs of 8px against 14 of 9px, and the uneven grid is
    exactly what reads as a gritty, noisy texture rather than clean pixels. Against a
    reference render of the same scene, whose cells are all one size, ours looked
    speckled for no reason but this.

    A smooth resample is blending neighbors anyway, so a fractional factor costs it
    nothing and it keeps the smaller image.
    """
    target_px = _MIN_RENDER_PX if target_px is None else target_px
    scale = target_px / current_px
    if scale <= 1.0:
        return 1.0
    if resample != Image.NEAREST:
        return scale
    # Rounding DOWN where that still lands near the target, up otherwise. Always rounding
    # up would be uniform too, but it costs four times the pixels for an array just under
    # the target -- 1900 native would become 3800 to avoid a 1.05x enlargement whose
    # unevenness is barely visible in the first place. Rounding down there gives the same
    # uniform grid at 1900, which is no worse an image.
    down = math.floor(scale)
    return float(down if down * current_px >= target_px * _UPSCALE_UNDERSHOOT else math.ceil(scale))
