"""The Share card: one picture to post, made by the color table editor's Share card…

On the dark plate of the editor's side-by-side picture (colortable_preview.side_by_side),
lettered in tcviz's own font (tcviz.fonts):

* the title across the top -- the table's name, unless the person changed it -- wrapped
  onto a second line, then made smaller, rather than run off the card;
* the storm drawn with the table, at a size fit to post: the saved picture's own values,
  colored by colorize.rgba with the bad color black (render.render's call, as
  colortable_preview.full_picture makes it), then shown at whole numbers -- a big picture
  thinned by a whole step (every 3rd pixel across and down of a 3,859-pixel VIIRS
  picture), a small one enlarged by a whole number (each pixel of a 619-pixel GOES
  picture a 2 x 2 square) -- so no pixel blends with its neighbors and every one is
  exactly the color the picture gives it (fit);
* beside it the table's color scale, as tall as the picture, warm at the top as the scale
  inside a tcviz picture and the editor's own bar read, labeled at the values that scale
  labels (picture_overlays._colorbar_ticks: every 10 °C on round tens, a wider step only
  where ten would crowd). Each row of the bar is the color the picture gives the value at
  its middle, colorize.rgba's again, so the bar too is the table byte for byte;
* under the storm, its title and data line (caption_lines: the lines the editor shows
  under the picture -- for a bundled sample its description and credit, which for the
  GCOM-C SGLI one is JAXA's required sentence), wrapped to the card;
* a small footer naming the program ("Made with Colortable Editor").

A card holds one picture or several (CardPicture each). Comparing, it shows what the editor
shows: two pictures side by side, each with its own table's scale and name; or one picture
swiped between two tables -- the left table's scale on the left and the other's on the
right, small marks above and below the picture where the line is (marks on the plate:
not one pixel of the picture is changed). Pictures drawn with the same table at the same
range (the storm grid's storms) share one scale, beside the whole grid; a grid of
infrared and water-vapor storms is two grids side by side, each with its scale, since a
water-vapor picture is drawn at 0 to -90 °C whatever the table's range. A grid three or
more pictures across is a wider card (GRID_TARGET_WIDTH), its pictures in the middle of
their cells and each one's lines centered under it, in smaller letters
(GRID_CAPTION_SIZE). With no picture, the card is the title and the scale.

How much room each picture gets depends only on how many there are and on the tables'
scales (picture_room), so a big picture can be thinned to it before the card is made --
the storm grid reads its storms one at a time and keeps only that -- and still come out
pixel for pixel the same.

Qt-free: numpy, Pillow, tcviz.colorize, tcviz.fonts and picture_overlays' choice of ticks.
"""
import collections
import math

import numpy as np
from matplotlib.colors import Normalize
from PIL import Image, ImageDraw

from . import colorize, fonts, picture_overlays

PLATE = (18, 18, 18)             # colortable_preview.side_by_side's
TEXT = (240, 240, 240)
DIM = (170, 170, 170)            # the data line, the scale's unit
FAINT = (125, 125, 125)          # the footer
FRAME = (96, 96, 96)             # round the bar, so a black end still shows on the plate

MARGIN = 56
# How wide the card's pictures and scales may be together (the card is this and its margins
# at most): about as wide as a screen, so the card is seen whole when opened, and the text
# stays big enough to read there.
TARGET_WIDTH = 1800
# ...and for a grid three or more pictures across (the storm grid's): wider, so each picture
# keeps more of its pixels. Its pictures then get about 650 pixels each, where the bundled
# samples come out near one size -- a GOES picture (480 to 630 pixels) whole, a VIIRS one
# (3,859) 1 in 6 and the SGLI one (5,789) 1 in 9 -- and the card stays under 2,400 across.
GRID_TARGET_WIDTH = 2200
# The narrowest the title and the lines under the picture are set in, for a card with no
# picture (only the scale) or very small pictures.
MIN_TEXT_WIDTH = 1000
PANEL_GAP = 48                   # between two pictures side by side, each with its scale
CELL_GAP = 16                    # between the pictures of a grid sharing one scale
BAR_WIDTH = 64
BAR_GAP = 24                     # between a picture and its scale
TICK = 14
TICK_GAP = 8                     # between a tick and its label
NO_PICTURE_HEIGHT = 900          # the scale's height on a card with no picture

TITLE_SIZE, TITLE_MIN_SIZE, TITLE_LINES = 72, 40, 2
LABEL_SIZE = 34                  # the scale's numbers, the unit and the tables' names
CAPTION_SIZE = 32
# the lines under each picture of a grid three or more across, whose cells are about 650
# pixels wide or less: at CAPTION_SIZE a storm's title took three lines there and JAXA's
# sentence five
GRID_CAPTION_SIZE = 24
FOOTER_SIZE = 24

_UNIT_WORDS = {"C": "°C"}

CardPicture = collections.namedtuple("CardPicture", "values tables caption split", defaults=((), None))
CardPicture.__doc__ = """One picture on a card.

`values` the saved picture's own values (2-D, in the engine's units: K, m/s, dBZ), or None
for no picture; `tables` the table it is drawn in, a tuple of one
colortable_preview.PictureTable (cmap, norm, vmax, vmin -- the range it is drawn at -- and
name), or of two for a swipe: the left one, then the right one; `caption` the lines under
it (caption_lines); `split`, for a swipe, the fraction of its width left of the line (its
columns left of round(split x width) are the left table's, as
colortable_preview.swiped_picture splits them)."""


def caption_lines(source):
    """The lines the editor shows under a picture (a colortable_preview.PreviewSource): its
    note, one line each -- a bundled sample's title and data line -- or else its label (a
    saved picture of the person's own: what took it, when, and the storm)."""
    if source is None:
        return ()
    note = str(source.note or "").strip()
    if note:
        return tuple(line.strip() for line in note.splitlines() if line.strip())
    return (str(source.label),) if source.label else ()


def fit(shape, room):
    """(step, factor) to show a picture of `shape` (rows, columns) in at most room x room
    pixels, at whole numbers: every step-th pixel across and down, each drawn factor x factor.
    A picture bigger than the room is thinned (factor 1), one smaller enlarged (step 1) --
    as far as it goes in the room."""
    longest = max(int(shape[0]), int(shape[1]), 1)
    room = max(1, int(room))
    if longest > room:
        return math.ceil(longest / room), 1
    return 1, room // longest


def colored(values, table):
    """RGB bytes of `values` through a table (PictureTable): colorize.rgba with its bad
    color black, as full_picture colors a picture."""
    norm = table.norm if table.norm is not None else Normalize(vmin=table.vmin, vmax=table.vmax)
    return np.ascontiguousarray(colorize.rgba(values, table.cmap.with_extremes(bad="black"), norm)[..., :3])


def picture_pixels(picture, room):
    """The picture of a CardPicture as the card shows it, in at most room x room pixels:
    (RGB bytes, step, factor, the column of the swipe's line in them or None). See fit;
    for a swipe, the thinned columns whose saved column is left of the line are the left
    table's."""
    values = np.asarray(picture.values)
    step, factor = fit(values.shape, room)
    shown = np.ascontiguousarray(values[::step, ::step])
    tables = picture.tables
    rgb = colored(shown, tables[0])
    line = None
    if len(tables) > 1:
        width = values.shape[1]
        fraction = 0.5 if picture.split is None else float(picture.split)
        split = min(width, max(0, round(fraction * width)))
        # thinned column j is saved column j * step: left of the line while that is
        left = min(shown.shape[1], math.ceil(split / step))
        if left < shown.shape[1]:
            rgb[:, left:] = colored(np.ascontiguousarray(shown[:, left:]), tables[1])
        line = left * factor
    if factor > 1:
        rgb = np.repeat(np.repeat(rgb, factor, axis=0), factor, axis=1)
    return rgb, step, factor, line


def bar_values(table, height):
    """The value each of a scale's `height` rows shows, warm (vmax) at the top: the value at
    the row's middle, vmax - (row + 0.5) / height x (vmax - vmin)."""
    rows = (np.arange(height, dtype=np.float64) + 0.5) / height
    return table.vmax - rows * (table.vmax - table.vmin)


def bar_pixels(table, height, width=BAR_WIDTH):
    """The scale's bar as RGB bytes (height, width, 3): each row the color the picture gives
    its value (bar_values)."""
    column = colored(bar_values(table, height)[:, None], table)
    return np.ascontiguousarray(np.repeat(column, width, axis=1))


def tick_label(value, units):
    """A scale's number for a value in the engine's units, in the table's ("C", "kt",
    "dBZ"): the unit itself goes once above the scale (unit_words)."""
    if units == "C":
        value = value - 273.15
    elif units == "kt":
        value = value * picture_overlays._KT_PER_MS
    text = f"{value:.0f}"
    return "0" if text == "-0" else text


def unit_words(units):
    return _UNIT_WORDS.get(units, units or "")


# ------------------------------------------------------------------------ lettering

def _font(size):
    return fonts.pil(size)


def _line_height(font):
    ascent, descent = font.getmetrics()
    return ascent + descent


def _width(font, text):
    return math.ceil(font.getlength(text)) if text else 0


def wrap(text, font, width):
    """`text` in lines no wider than `width` pixels in `font`, broken between words (and
    inside a word only when one is wider than a whole line)."""
    lines, line = [], ""
    for word in str(text).split():
        trial = f"{line} {word}" if line else word
        if _width(font, trial) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        while len(word) > 1 and _width(font, word) > width:
            cut = max(1, max((n for n in range(1, len(word)) if _width(font, word[:n]) <= width), default=1))
            lines.append(word[:cut])
            word = word[cut:]
        line = word
    if line:
        lines.append(line)
    return lines


def title_lines(title, width):
    """(font size, lines) of the title in `width` pixels: at TITLE_SIZE when it fits on
    TITLE_LINES lines, else smaller, down to TITLE_MIN_SIZE -- and there, a title that still
    does not fit ends its last line in "…"."""
    title = " ".join(str(title or "").split())
    if not title:
        return TITLE_SIZE, []
    for size in range(TITLE_SIZE, TITLE_MIN_SIZE - 1, -4):
        lines = wrap(title, _font(size), width)
        if len(lines) <= TITLE_LINES:
            return size, lines
    font = _font(TITLE_MIN_SIZE)
    last = " ".join(lines[TITLE_LINES - 1:])
    while last and _width(font, last + "…") > width:
        last = last[:-1]
    return TITLE_MIN_SIZE, lines[:TITLE_LINES - 1] + [last.rstrip() + "…"]


# ------------------------------------------------------------------------ the card

_Panel = collections.namedtuple("_Panel", "pictures tables")


def _label_width(table, units, font):
    """The widest number a scale of this table could carry: every 10 of its units, the
    most it is ever labeled."""
    ticks = picture_overlays._colorbar_ticks(table.vmin, table.vmax, units, None, 10 ** 6, 1)
    texts = [tick_label(v, units) for v in (*ticks, table.vmin, table.vmax)]
    return max(_width(font, t) for t in texts)


def _bar_block_width(table, units, font):
    """A scale's width beside its picture: the bar in its frame, the ticks and the numbers."""
    return 1 + BAR_WIDTH + 1 + TICK + TICK_GAP + _label_width(table, units, font)


def _columns(count):
    """How many pictures across a grid of `count` holds: all of up to 3 in a row, then
    about as many across as down."""
    return count if count <= 3 else math.ceil(math.sqrt(count))


def _panels(pictures):
    """The card's panels, left to right: the pictures drawn in the same tables together in
    one (a grid with one scale beside it), in the order their tables first come."""
    panels = []
    for picture in pictures:
        panel = next((p for p in panels if p.tables == picture.tables), None)
        if panel is None:
            panels.append(_Panel([picture], picture.tables))
        else:
            panel.pictures.append(picture)
    return panels


def _bar_widths(panels, units, font):
    return [[_bar_block_width(t, units, font) for t in panel.tables] for panel in panels]


def _room(panels, bar_widths):
    """The pixels across and down each picture gets: TARGET_WIDTH (GRID_TARGET_WIDTH for
    three or more pictures across) less the scales and the gaps, shared among the pictures
    across all the panels."""
    fixed = sum(sum(w + BAR_GAP for w in widths) for widths in bar_widths) + PANEL_GAP * (len(panels) - 1)
    across = [_columns(len(panel.pictures)) for panel in panels]
    target = GRID_TARGET_WIDTH if sum(across) >= 3 else TARGET_WIDTH
    return max(64, (target - fixed - CELL_GAP * sum(a - 1 for a in across)) // max(1, sum(across)))


def picture_room(pictures, units="C"):
    """The most pixels across and down share_card shows each of `pictures` (CardPicture each)
    in, the same for them all. Only how many there are and their tables count, never their
    values -- a CardPicture with None for its values stands for one -- so a big picture can
    be thinned by fit(its shape, this room) first, and the card comes out the same."""
    panels = _panels(list(pictures))
    return _room(panels, _bar_widths(panels, units, _font(LABEL_SIZE)))


def share_card(pictures, title="", footer="", units="C", captions=True, regions=None):
    """The card (a PIL RGB image) for `pictures` (CardPicture each; see the module),
    `title` across the top, `footer` in small letters at the bottom right. `units` is the
    tables' ("C", "kt", "dBZ"), which the scales are labeled in. With `captions`, each
    picture's caption lines under it -- once under them all, when they are the same.

    `regions`, a dict when given, is filled with where everything went, for tests:
    "title" (box, size, lines), "names" [(text, box)], "units" [(text, box)], "bars"
    [{"name", "box", "side", "ticks": [(value, y)], "labels"}], "pictures" [{"box", "step",
    "factor", "line"}] ("line" the swipe line's x on the card, or None), "captions"
    [(box, lines)] and "footer" (box, text); a box is (left, top, right, bottom), right and
    bottom outside it."""
    pictures = list(pictures)
    if not pictures:
        raise ValueError("a card needs at least one picture, or one table with no picture")
    label_font, caption_font = _font(LABEL_SIZE), _font(CAPTION_SIZE)

    # one scale beside the pictures drawn in the same tables (a grid); a panel of its own
    # for each other table
    panels = _panels(pictures)
    bar_widths = _bar_widths(panels, units, label_font)
    room = _room(panels, bar_widths)

    drawn = []                   # per panel: [(rgb, step, factor, line) or None]
    for panel in panels:
        drawn.append([picture_pixels(p, room) if p.values is not None else None for p in panel.pictures])
    shared_caption = None
    if captions:
        all_captions = [tuple(p.caption) for p in pictures]
        if all(c == all_captions[0] for c in all_captions):
            shared_caption = all_captions[0]

    # each panel's grid: cells as wide as its widest picture, rows as tall as their tallest
    grids = []
    # each picture's own lines in smaller letters on a grid three or more across, whose
    # cells are narrower
    own_font = _font(GRID_CAPTION_SIZE) if sum(_columns(len(p.pictures)) for p in panels) >= 3 else caption_font
    for panel, images in zip(panels, drawn):
        cols = _columns(len(images))
        cell_w = max((im[0].shape[1] for im in images if im is not None), default=0)
        rows = [images[i:i + cols] for i in range(0, len(images), cols)]
        row_h = [max((im[0].shape[0] for im in row if im is not None), default=0) for row in rows]
        # each picture's own lines, when they are not the card's shared ones: (lines wrapped
        # to its cell, how many of them are its first line)
        own = [_wrapped(p.caption if captions and shared_caption is None else (), own_font, max(cell_w, 200))
               for p in panel.pictures]
        caption_h = [max((len(own[i][0]) for i in range(r * cols, min(len(own), (r + 1) * cols))), default=0)
                     * _line_height(own_font) for r in range(len(rows))]
        picture_h = sum(row_h) + CELL_GAP * (len(rows) - 1) + sum(h + (12 if h else 0) for h in caption_h)
        grid_w = cell_w * cols + CELL_GAP * (cols - 1) if cell_w else 0
        grids.append({"cols": cols, "cell_w": cell_w, "row_h": row_h, "captions": own, "caption_h": caption_h,
                      "w": grid_w, "h": picture_h, "font": own_font})
    panel_widths = [g["w"] + sum(w + (BAR_GAP if g["w"] else 0) for w in widths)
                    for g, widths in zip(grids, bar_widths)]
    content_w = sum(panel_widths) + PANEL_GAP * (len(panels) - 1)
    text_w = max(content_w, MIN_TEXT_WIDTH)
    panels_h = max(g["h"] for g in grids) or NO_PICTURE_HEIGHT

    # the parts top to bottom
    size, lines = title_lines(title, text_w)
    title_font = _font(size)
    title_h = len(lines) * _line_height(title_font)
    names = _names(panels, title)
    head_h = _line_height(label_font)
    under, bright = _wrapped(shared_caption or (), caption_font, text_w)
    under_h = len(under) * _line_height(caption_font)
    footer_font = _font(FOOTER_SIZE)
    footer_h = _line_height(footer_font) if footer else 0

    # the gap under the names is room for the top number, which stands half over the bar's
    # top, and for a swipe's mark
    gap_title, gap_head, gap_under, gap_footer = 16, 30, 40, 28
    height = (MARGIN + (title_h + gap_title if lines else 0) + head_h + gap_head + panels_h + gap_under
              + (under_h + gap_footer if under else 0) + footer_h + MARGIN * 3 // 4)
    card = Image.new("RGB", (text_w + 2 * MARGIN, height), PLATE)
    draw = ImageDraw.Draw(card)
    found = {"names": [], "units": [], "bars": [], "pictures": [], "captions": []}

    y = MARGIN
    found["title"] = ((MARGIN, y, MARGIN + max((_width(title_font, t) for t in lines), default=0), y + title_h),
                      size, lines)
    for i, line in enumerate(lines):
        draw.text((MARGIN, y + i * _line_height(title_font)), line, font=title_font, fill=TEXT, anchor="la")
    if lines:
        y += title_h + gap_title
    head_y = y
    top = y + head_h + gap_head

    x = MARGIN
    for panel, images, grid, widths, panel_w in zip(panels, drawn, grids, bar_widths, panel_widths):
        tables = panel.tables
        bar_h = (sum(grid["row_h"]) + CELL_GAP * (len(grid["row_h"]) - 1)
                 + sum(h + (12 if h else 0) for h in grid["caption_h"][:-1])) if grid["w"] else NO_PICTURE_HEIGHT
        gx = x
        if len(tables) > 1:
            # a swipe: the left table's scale left of the picture, mirrored
            bar_x = x + widths[0] - 1 - BAR_WIDTH
            found["bars"].append(_draw_bar(card, draw, tables[0], units, bar_x, top, bar_h, "left", label_font))
            # the unit over the numbers, at the card's outer side, clear of the name
            _unit(draw, found, units, bar_x - 2 - TICK - TICK_GAP, head_y, label_font, "ra")
            gx = x + widths[0] + BAR_GAP
        _draw_grid(card, draw, images, grid, gx, top, found)
        bar_x = gx + grid["w"] + (BAR_GAP if grid["w"] else 0) + 1
        found["bars"].append(_draw_bar(card, draw, tables[-1], units, bar_x, top, bar_h, "right", label_font))
        _unit(draw, found, units, bar_x + BAR_WIDTH + 1 + TICK + TICK_GAP, head_y, label_font, "la")
        for text, where in names.get(id(panel), ()):
            anchor = "ra" if where == "right" else "la"
            at = gx + grid["w"] if where == "right" else gx
            box = draw.textbbox((at, head_y), text, font=label_font, anchor=anchor)
            draw.text((at, head_y), text, font=label_font, fill=TEXT, anchor=anchor)
            found["names"].append((text, box))
        x += panel_w + PANEL_GAP

    y = top + panels_h + gap_under
    if under:
        for i, line in enumerate(under):
            draw.text((MARGIN, y + i * _line_height(caption_font)), line, font=caption_font,
                      fill=TEXT if i < bright else DIM, anchor="la")
        found["captions"].append(((MARGIN, y, MARGIN + max(_width(caption_font, t) for t in under), y + under_h),
                                  under))
        y += under_h + gap_footer
    if footer:
        right = MARGIN + text_w
        box = draw.textbbox((right, y), footer, font=footer_font, anchor="ra")
        draw.text((right, y), footer, font=footer_font, fill=FAINT, anchor="ra")
        found["footer"] = (box, footer)
    if regions is not None:
        regions.update(found)
    return card


def _wrapped(caption, font, width):
    """(a picture's caption lines wrapped to `width`, how many of them its first line -- the
    picture's title -- takes): those are drawn bright, the data line under them dimmer."""
    parts = [wrap(line, font, width) for line in caption]
    return [part for lines in parts for part in lines], (len(parts[0]) if parts else 0)


def _names(panels, title):
    """{id(panel): [(name, "left" or "right")]} -- the tables' names over their pictures:
    always when the card holds more than one table (a swipe's two over the picture's two
    sides), and over a lone table when the title is not its name."""
    many = len(panels) > 1 or any(len(p.tables) > 1 for p in panels)
    title = " ".join(str(title or "").split())
    out = {}
    for panel in panels:
        tables = panel.tables
        if len(tables) > 1:
            named = [(tables[0].name, "left"), (tables[-1].name, "right")]
        elif many or tables[0].name != title:
            named = [(tables[0].name, "left")]
        else:
            named = []
        out[id(panel)] = [(name, side) for name, side in named if name]
    return out


def _unit(draw, found, units, x, y, font, anchor):
    text = unit_words(units)
    if text:
        found["units"].append((text, draw.textbbox((x, y), text, font=font, anchor=anchor)))
        draw.text((x, y), text, font=font, fill=DIM, anchor=anchor)


def _draw_bar(card, draw, table, units, bar_x, top, height, side, font):
    """One scale: the bar at bar_x (its left column), `top`, `height` rows tall, in a frame
    just outside it, and the ticks and numbers right of it (`side` "right") or left of it
    ("left"). Where it went, for share_card's `regions`."""
    card.paste(Image.fromarray(bar_pixels(table, height)), (bar_x, top))
    draw.rectangle([bar_x - 1, top - 1, bar_x + BAR_WIDTH, top + height], outline=FRAME)
    ticks = picture_overlays._colorbar_ticks(table.vmin, table.vmax, units, None, height, LABEL_SIZE)
    span = (table.vmax - table.vmin) or 1.0
    placed, labels = [], []
    for value in ticks:
        y = round(top + (table.vmax - value) / span * height)
        text = tick_label(value, units)
        if side == "right":
            x0 = bar_x + BAR_WIDTH + 1
            draw.line([x0, y, x0 + TICK - 1, y], fill=TEXT, width=2)
            draw.text((x0 + TICK + TICK_GAP, y), text, font=font, fill=TEXT, anchor="lm")
        else:
            x0 = bar_x - 2
            draw.line([x0 - TICK + 1, y, x0, y], fill=TEXT, width=2)
            draw.text((x0 - TICK - TICK_GAP, y), text, font=font, fill=TEXT, anchor="rm")
        placed.append((value, y))
        labels.append(text)
    return {"name": table.name, "box": (bar_x, top, bar_x + BAR_WIDTH, top + height), "side": side, "ticks": placed,
            "labels": labels}


def _draw_grid(card, draw, images, grid, left, top, found):
    """A panel's pictures, row by row, each in the middle of its cell -- a picture smaller
    than the row's tallest in the middle of the row too -- with its own caption lines under
    the row (in grid["font"]), each line centered under its picture, when they are not the
    card's shared ones; a swipe's line marked above and below the picture."""
    cols, cell_w, caption_font = grid["cols"], grid["cell_w"], grid["font"]
    line_h = _line_height(caption_font)
    y = top
    for r, row_h in enumerate(grid["row_h"]):
        for c in range(cols):
            i = r * cols + c
            if i >= len(images) or images[i] is None:
                continue
            rgb, step, factor, line = images[i]
            h, w = rgb.shape[:2]
            cell_x = left + c * (cell_w + CELL_GAP)
            px, py = cell_x + (cell_w - w) // 2, y + (row_h - h) // 2
            card.paste(Image.fromarray(rgb), (px, py))
            at = None
            if line is not None and 0 < line < w:
                at = px + line
                # two marks pointing at the line, on the plate just above and below the picture
                for tip, base in ((py - 5, py - 23), (py + h + 4, py + h + 22)):
                    draw.polygon([(at - 12, base), (at + 12, base), (at, tip)], fill=TEXT)
            found["pictures"].append({"box": (px, py, px + w, py + h), "step": step, "factor": factor, "line": at})
            lines, bright = grid["captions"][i]
            middle, ty = cell_x + cell_w // 2, y + row_h + 12
            for k, text in enumerate(lines):
                draw.text((middle, ty + k * line_h), text, font=caption_font, fill=TEXT if k < bright else DIM,
                          anchor="ma")
            if lines:
                widest = max(_width(caption_font, t) for t in lines)
                found["captions"].append(((middle - widest // 2, ty, middle - widest // 2 + widest,
                                           ty + len(lines) * line_h), lines))
        y += row_h + grid["caption_h"][r] + (12 if grid["caption_h"][r] else 0) + CELL_GAP
