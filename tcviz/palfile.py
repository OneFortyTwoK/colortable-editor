"""Color-table files written by other programs: GRLevelX / Baron ".PAL" and MetPy ".tbl".

GRLevelX / Baron ".PAL" -- the format weather-radar software has used for years, and the
one most hand-made reflectivity palettes are distributed in:

    product: BR
    units:   dBZ
    step:    5
    COLOR:  <value> <r> <g> <b>            [<r2> <g2> <b2>]

Each COLOR line anchors a color at a DATA value (dBZ here, not a 0-1 fraction), so the
table carries its own scale -- which is the whole point of it, and why it is parsed
rather than resampled: 40 dBZ is meant to be that specific red. A line with one triple
blends from its color to the next anchor's; a second triple is where the blend arrives
instead, so the next anchor's own color starts with a hard step. SolidColor holds its one
color, unblended, up to the next anchor. Color4 and SolidColor4 carry a fourth number per
color, its opacity, which is not kept: every color here is drawn solid.

Only the pieces that affect the ramp are read. `step` is a display hint for the legend in
the original software and is deliberately ignored, like the other `key: value` settings.

MetPy ".tbl" -- one color per line as a Python literal, normally an (r, g, b) tuple of
0-1 floats; "#" lines are comments. colormaps reads its six vendored MetPy tables with
parse_tbl, and so does the color-table importer, so the two can never disagree.
"""
import ast
import re
from pathlib import Path

_COLOR_RE = re.compile(r"^\s*(solid)?color(4)?\s*:\s*(.+)$", re.IGNORECASE)
# Settings the ramp does not depend on (product:, units:, step:, scale:, RF:, ...), and
# comments: read and set aside, so the importer reports only lines it could not read.
_SETTING_RE = re.compile(r"^\s*[A-Za-z][\w ]*:")
_COMMENT_PREFIXES = (";", "#", "//")


def parse_pal(text, problems=None):
    """[(value, (r, g, b)), ...] sorted by value, 0-255 ints, plus the declared units.

    Returns (stops, units). A two-triple line becomes two stops at the same value -- the
    color arriving and the color leaving -- which is exactly how a hard step is written
    in this project's own colortables (see colormaps._fix_duplicate_stops). A SolidColor
    line gets a second stop of its own color at the next anchor, so it holds until there.

    Lines that are neither a color, a `key: value` setting nor a comment are skipped; when
    `problems` is a list, (line number, line) is appended to it for each one, and for a
    COLOR line whose numbers do not read.
    """
    stops, units, solid = [], None, None
    for number, line in enumerate(text.splitlines(), 1):
        low = line.strip().lower()
        if low.startswith("units"):
            units = line.split(":", 1)[1].strip() if ":" in line else None
            continue
        m = _COLOR_RE.match(line)
        if not m:
            if problems is not None and low and not low.startswith(_COMMENT_PREFIXES) \
                    and not _SETTING_RE.match(line):
                problems.append((number, line.strip()))
            continue
        parts = m.group(3).replace(",", " ").split()
        try:
            nums = [float(x) for x in parts]
        except ValueError:
            nums = []
        width = 4 if m.group(2) else 3          # Color4 colors carry an opacity too
        if len(nums) < 1 + width:
            if problems is not None:
                problems.append((number, line.strip()))
            continue
        value = nums[0]
        if solid is not None:                   # the solid color before this one ends here
            stops.append((value, solid))
            solid = None
        rgb = tuple(int(round(v)) for v in nums[1:4])
        stops.append((value, rgb))
        if m.group(1):
            solid = rgb
        elif len(nums) >= 1 + 2 * width:        # ramp target for the segment that follows
            stops.append((value, tuple(int(round(v)) for v in nums[1 + width:4 + width])))
    stops.sort(key=lambda s: s[0])
    return stops, units


def load_pal(path):
    return parse_pal(Path(path).read_text(encoding="utf-8", errors="replace"))


def pal_colormap(stops, name="pal"):
    """(cmap, vmin, vmax) from parse_pal's stops, keeping the file's own value scale.

    The stops are normalized onto 0-1 by their own min/max, so the returned vmin/vmax are
    what a caller must render with for the colors to sit at the values the file assigns
    them.
    """
    from matplotlib.colors import LinearSegmentedColormap

    if len(stops) < 2:
        raise ValueError("a .PAL needs at least two COLOR entries")
    lo, hi = stops[0][0], stops[-1][0]
    span = (hi - lo) or 1.0
    pairs = [((v - lo) / span, tuple(c / 255.0 for c in rgb)) for v, rgb in stops]
    # Identical consecutive positions are a deliberate hard step; nudge the second so
    # matplotlib accepts a monotonic list without softening the jump.
    fixed, last = [], -1.0
    for pos, color in pairs:
        if pos <= last:
            pos = min(1.0, last + 1e-6)
        fixed.append((pos, color))
        last = pos
    return LinearSegmentedColormap.from_list(name, fixed), lo, hi


def parse_tbl(text, problems=None):
    """[(r, g, b), ...] as 0-1 floats from a MetPy .tbl, read as MetPy reads it: each line
    that is not blank and not a "#" comment is a Python literal handed to matplotlib's
    to_rgb -- normally an (r, g, b) tuple of 0-1 floats.

    A line that is not a color raises ValueError, as MetPy refuses a malformed table --
    unless `problems` is a list: then (line number, line, why) is appended to it for each
    such line and the rest are read."""
    from matplotlib.colors import to_rgb

    colors = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            colors.append(to_rgb(ast.literal_eval(stripped)))
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as e:
            if problems is None:
                raise ValueError(f"line {number} of the table is not a color: {stripped!r}") from e
            problems.append((number, stripped, _why_not_a_color(e)))
    return colors


def _why_not_a_color(error):
    if isinstance(error, SyntaxError):
        return "it is not written as (r, g, b)"
    text = str(error)
    if "0-1" in text:
        return "its numbers must be between 0 and 1"
    return "it is not a color"
