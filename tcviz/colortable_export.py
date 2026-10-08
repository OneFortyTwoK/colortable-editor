"""Every IR and water-vapor color table as the Python block that defines it, checked.

The block is the one the built-in curves are written in, and the one people share:

    def bd():
        newcmp = LinearSegmentedColormap.from_list("", [
            (0/120, "#000000"),
            (21/120, "#ffffff"),
            (21/120, "#6d6d6d"),
            ...
            (120/120, "#555555")
        ])
        vmax = 30 + 273.15
        vmin = -90 + 273.15
        return newcmp.reversed(), vmax, vmin

A repeated position is a hard step (user_colortables.build_cmap separates the pair by
1e-6, as the curves in colormaps.py and extra_cmaps.py do). `a/120` is a degrees below
the warm end of a 120-degree range. Every position is written that way, always over the
table's own range (2026-10-07): a stop that is not on a whole degree is the fraction with
the fewest decimals on top that still draws every color the same (31.15/135), never a bare
decimal or another bottom number (_in_fractions). A table with other than 256 levels says so on the closing line,
`], N=57)`, so it comes back with the same number of colors.

Nothing is handed out on trust. Every block is read back with
user_colortables.parse_shareable, rebuilt with build_cmap, and compared with the table
the app draws with: the same 8-bit rows (colorize.table: every color, then under, over
and bad) and the same vmax/vmin in Kelvin, bit for bit. A block that does not reproduce
its table is never returned; the next way of writing it is tried, and a table no way
reproduces raises ExportError. The ways, in order:

* "stops" -- a table of yours: the stops you saved, as you saved them.
* "structural" -- a built-in LinearSegmentedColormap: its own stops, read from its
  segment data. Positions that the curves' authors nudged 1e-6 apart to make a hard step
  are written as the repeated position they mean, at the whole degree they sit on where
  that rebuilds the table. Each stop's #rrggbb is the byte its color draws as, unless a
  neighbor needs it one or two off (a curve anchored on 0.33333 draws 84, not 85).
* "table" -- anything else (the ListedColormaps, the six MetPy tables, and the few
  curves no stops of their own can reproduce in 8 bits): the table itself. A stop at
  every row does NOT come back (matplotlib truncates, and about 12% of the channels
  interpolated onto a row land one level low), so each row instead sits inside a flat
  run or a ramp whose ends lie between rows, where nothing is sampled; ramps are only
  kept where they give the same bytes, with a margin no rounding can cross.

The app reads a repeated position the way the curves in colormaps.py and extra_cmaps.py
were built, with the second stop 1e-6 later. Plain matplotlib 3.11 takes it as an exact
step instead, so pasted into a script of your own a row that falls exactly on a hard step
(70/150 on a 256-row table, say) can show the color from the other side.

A winds or radar table of yours is written the same way, in its own units, and says so on
a line of its own (an infrared or water-vapor block is exactly as before):

    def gusts():
        # units: kt
        newcmp = LinearSegmentedColormap.from_list("", [
            (0/100, "#000000"),
            (34/100, "#00ff00"),
            (100/100, "#ffffff")
        ])
        vmax = 100 / 1.9438452
        vmin = 0 / 1.9438452
        return newcmp, vmax, vmin

`100 / 1.9438452` is 100 kt: read back, it is the number written; run as Python, it is the
m/s every wind field is drawn in. A radar table's bounds are plain dBZ (`vmax = 75`).

Two things a block cannot carry, by design:

* The MetPy tables are drawn through a BoundaryNorm (one band per color, evenly across
  the range). The block is a plain table with N set to their color count; with an even
  Normalize over the same range that draws the same color for every value except one
  that lands exactly on a band edge, where the two may pick neighbors.
* Water-vapor bands are always drawn at 0 to -90 C, whatever range a table carries
  (engine.common.drawing._wv_palette_range). A block carries the table's OWN range, the
  one it would be drawn at on any other band.

Qt-free: the app's Export buttons call export_block / export_many / exportable_names.
"""
import datetime
import math
from collections import namedtuple

import numpy as np
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, to_hex, to_rgba

from . import colorize, colortable_registry, user_colortables

KIND_CATEGORIES = {"ir": "Temperature (IR)", "wv": "Water Vapor",
                   "wind": user_colortables.UNIT_CATEGORIES["kt"], "radar": user_colortables.UNIT_CATEGORIES["dBZ"]}
KIND_BANNERS = {"ir": "Infrared", "wv": "Water vapor", "wind": "Winds", "radar": "Radar"}
KIND_UNITS = {"ir": "C", "wv": "C", "wind": "kt", "radar": "dBZ"}

# build_cmap's nudge for a repeated position; a gap this small between two stops of a
# built-in curve is that nudge (or extra_cmaps._dedup's, the same number), not a choice.
_NUDGE = 1e-6
_NUDGE_SEEN = 1.5e-6
# How far inside a byte's [k, k + 1) an interpolated row must land before the table tier
# trusts a ramp with it. The fit works from the written positions, matplotlib from 1 - q
# of them after .reversed(); the two differ by rounding alone, ~1e-13 of a level.
_MARGIN = 1e-4

Exported = namedtuple("Exported", "name text tier")
_decimal = user_colortables._decimal


class ExportError(ValueError):
    """A table no block reproduces exactly (the message says why)."""


# ------------------------------------------------------------------------- which tables

def exportable_names(kind):
    """Every IR ("ir") or water-vapor ("wv") table the app has, built-in and yours, in the
    picker's order. Your tables hidden from the pickers are included (a hidden table is an
    IR one unless it says Water Vapor).

    "wind" and "radar" give the tables of those kinds the same way: the built-in ones (the
    SAR and scatterometer wind tables, and the radar one -- registered after
    colormaps.PALETTES was made, so looked up in the registry too), which the editor
    compares a table with, then yours, which export like any other. A built-in one is exported only where a block can carry it
    exactly (the scatterometer table's 5-knot bands cannot be: export raises ExportError)."""
    if kind not in KIND_CATEGORIES:
        raise ValueError(f"kind must be one of {sorted(KIND_CATEGORIES)}, not {kind!r}")
    registry = colortable_registry
    category = KIND_CATEGORIES[kind]
    pool = registry.palettes()
    if kind in ("wind", "radar"):
        pool = list(dict.fromkeys(list(registry.palettes()) + list(registry.registered_colortables())))
    names = list(registry.categorize_palettes(pool).get(category, []))
    for name, entry in _user_entries().items():
        if name not in names and _user_kind(entry) == kind:
            names.append(name)
    return names


def _user_kind(entry):
    units = entry.get("units", "C")
    if units in ("kt", "dBZ"):
        return {"kt": "wind", "dBZ": "radar"}[units]
    return "wv" if entry.get("category") == "Water Vapor" else "ir"


def _user_entries():
    """{name: stored entry} for every table in the store that is live in the registry as
    the store's table (and so is what the app draws under that name)."""
    out = {}
    for name, builder in colortable_registry.registered_builders().items():
        if "register_user_colortable" not in getattr(builder, "__qualname__", ""):
            continue
        entry = next((d for d in (builder.__defaults__ or ()) if isinstance(d, dict) and "stops" in d), None)
        if entry is None:
            entry = next((e for e in user_colortables.load() if e.get("name") == name), None)
        if entry is not None:
            out[name] = entry
    return out


# ------------------------------------------------------------------------------ the API

def export(name, include_notes=False):
    """Exported(name, text, tier) for one table; ExportError if none reproduces it."""
    target = _target(name)
    return _first_exact(target, _notes(name, target) if include_notes else {})


def _first_exact(target, notes):
    problems = []
    for tier, draft in _drafts(target):
        try:
            text = _render(target.name, draft, notes)
        except ExportError as e:
            problems.append(f"{tier}: {e}")
            continue
        why = _mismatch(text, target)
        if why is None:
            try:
                text = _in_fractions(target.name, draft, notes, lambda t: _mismatch(t, target) is None)
            except ExportError as e:
                problems.append(f"{tier}: {e}")
                continue
            return Exported(target.name, text, tier)
        problems.append(f"{tier}: {why}")
    raise ExportError(f"{target.name}: no block reproduces it exactly "
                      f"({'; '.join(problems) or 'nothing to try'})")


def _in_fractions(name, draft, notes, reproduces):
    """The block of `draft` with every position a fraction of its range, a/span: a shared
    table is ALWAYS written in fractions (2026-10-07), as the built-in curves are.

    A position the tiers wrote as a bare decimal (0.23073974074074055 in bd, a stop of yours
    saved as 0.006667) becomes the fraction with the fewest decimals on top that still draws
    every color the same -- `reproduces(text)`, the same check every block passes (31.15/135,
    1/150) -- tried first all at once, then a repeated position at a time from the warm end,
    the ones not yet tried left as they were (which reproduce). A range that is not a whole
    number is written as it is (x/100.846688976 for the SAR wind table's 100.846688976 kt).

    A position moves at most _LEEWAY of the way to the stops either side of it: with 256
    colors over 150 degrees, five stops 0.07 degrees apart all draw the same written as 0/150,
    and the table's own stops would come back as one. ExportError if a position has no
    fraction that draws the same."""
    span, span_text = _span(draft)
    stops = list(draft.stops)
    groups, start = [], 0
    for i in range(1, len(stops) + 1):
        if i == len(stops) or stops[i][0] != stops[start][0]:
            groups.append((start, i))           # a repeated position (a hard step) moves as one
            start = i
    todo = [g for g in groups if "/" not in stops[g[0]][0]]
    if not todo:
        return _render(name, draft, notes)
    values = [stops[a][1] for a, _b in groups]
    options = {}
    for k, g in enumerate(groups):
        if g in todo:
            gaps = [abs(values[k] - values[j]) for j in (k - 1, k + 1)
                    if 0 <= j < len(values) and values[j] != values[k]]
            room = _LEEWAY * min(gaps) if gaps else math.inf
            options[g] = [o for o in _fraction_candidates(values[k], span, span_text)
                          if abs(o[1] - values[k]) <= room]
            if not options[g]:
                raise ExportError(f"the stop at {values[k]!r} has no fraction of {span_text} "
                                  f"close enough to it")

    def block(chosen):
        out = list(stops)
        for (a, b), (text, value) in chosen.items():
            out[a:b] = [(text, value, rgb) for _t, _v, rgb in stops[a:b]]
        return _render(name, draft._replace(stops=out), notes)

    text = block({g: options[g][0] for g in todo})
    if reproduces(text):
        return text
    chosen = {}
    for g in todo:
        for option in options[g]:
            text = block({**chosen, g: option})
            if reproduces(text):
                chosen[g] = option
                break
        else:
            raise ExportError(f"the stop at {stops[g[0]][1]!r} has no fraction of {span_text} "
                              f"that draws the same colors")
    return block(chosen)


# How far toward the stops either side of it a position may move to become a tidier fraction.
_LEEWAY = 0.25


def _span(draft):
    """(span, its text): the range a block's fractions are of, in its units -- the whole
    number the tiers' own fractions use when it is one (135 for 40 to -94.99999999999997),
    else the range exactly as it is."""
    vmax, vmin = float(draft.vmax_text), float(draft.vmin_text)
    whole = _whole_span(vmax, vmin)
    if whole:
        return float(whole), str(whole)
    span = vmax - vmin
    if not (math.isfinite(span) and span > 0):
        raise ExportError(f"its range ({draft.vmin_text} to {draft.vmax_text}) has no width "
                          f"to write positions over")
    return span, _decimal(span)


def _fraction_candidates(value, span, span_text):
    """[(text, value)] for a position as a/span, tidiest first: the top number in whole
    units, then tenths, hundredths and on, then the numbers that give the position exactly.

    Always over the range itself, never another bottom number (2026-10-07: a scale from 50
    to -100 shows its fractions out of 150 only). Where no top number gives the position to
    the last bit and the colors need it there (a curve anchored at 6/13 of a 100-degree
    range), this way of writing the table fails and the next is tried."""
    out, seen = [], set()
    x = value * span
    for digits in range(18):
        num = f"{round(x, digits):.{digits}f}"
        if "." in num:
            num = num.rstrip("0").rstrip(".")
        if num in ("-0", ""):
            num = "0"
        if num not in seen:
            seen.add(num)
            out.append((f"{num}/{span_text}", float(num) / span))
    for candidate in (x, *_neighbors(x, 8)):
        num = _decimal(candidate)
        if num not in seen and float(num) / span == value:
            seen.add(num)
            out.append((f"{num}/{span_text}", value))
    return out


def export_block(name, include_notes=False):
    """One table as its verified Python block (with a trailing newline)."""
    return export(name, include_notes).text


def export_many(names, include_notes=False, failures=None):
    """Several tables, one verified block each, separated by a blank line.

    A table that cannot be exported raises ExportError naming every one that failed --
    unless `failures` is a list, which then collects (name, reason) for each and the rest
    are exported without them."""
    blocks, failed = [], []
    for name in names:
        try:
            blocks.append(export_block(name, include_notes))
        except Exception as e:  # one bad table must not cost the rest
            failed.append((name, str(e)))
    if failed and failures is None:
        raise ExportError("; ".join(reason for _n, reason in failed))
    if failures is not None:
        failures.extend(failed)
    return "\n".join(blocks)


def export_all(include_notes=False, failures=None, when=None, only=None):
    """The whole collection as one text file: a short header, then every IR table and
    every water-vapor table, each kind under its own banner.

    `only`, a collection of names, keeps just those, in the same order (the Manage
    window's "Export many…" checklist); a kind with none of them is left out. Your winds
    and radar tables come only this way, under banners of their own after water vapor."""
    when = when or datetime.date.today()
    only = None if only is None else set(only)
    from . import edition
    by = "Colortable Editor" if edition.is_editor() else "tcviz (tcviz/colortable_export.py)"
    head = [f"# {edition.app_name()} color tables, written as Python",
            f"# Exported {when.isoformat()} by {by}. Every block was read",
            "# back, rebuilt and compared with the table the app draws with before it was written",
            "# here: the same 8-bit colors and the same vmax/vmin.",
            "# Infrared tables come first, then water vapor. Water-vapor bands are drawn at",
            "# 0 to -90 C whatever range a table carries; each block keeps its table's own range."]
    parts = ["\n".join(head) + "\n"]
    for kind in ("ir", "wv") if only is None else ("ir", "wv", "wind", "radar"):
        names = exportable_names(kind)
        if only is not None:
            names = [n for n in names if n in only]
            if not names:
                continue
        body = export_many(names, include_notes, failures)
        parts.append(f"# ---- {KIND_BANNERS[kind]} ----\n\n{body}")
    return "\n".join(parts)


def entry_block(entry, include_notes=False):
    """A stored (or not yet saved) user table as its block: user_colortables.to_shareable.

    An entry that builds is verified like any other export. One that does not yet build
    (the Add dialog copies whatever it holds) cannot be compared with a table, so its
    block is checked to give back the same stops, range and direction instead."""
    name = entry.get("name") or "untitled"
    notes = _entry_notes(entry) if include_notes else {}
    try:
        cmap, vmax, vmin = user_colortables.build_cmap(entry)
    except Exception:
        text = _in_fractions(name, _entry_draft(entry), notes, lambda t: _gives_back(t, entry))
        _check_stops_only(text, entry)
        return text
    target = _Target(name, cmap, None, vmax, vmin, colorize.table(cmap), cmap.N, entry)
    return _first_exact(target, notes).text


def rebuild(text):
    """(cmap, vmax_K, vmin_K) that a block builds when it is pasted back: the same path
    the Add dialog and the self-check take."""
    fields = user_colortables.parse_shareable(text)
    return user_colortables.build_cmap(_entry_from_fields(fields))


def lut_stops(rows, vmax_c=50.0, vmin_c=-100.0):
    """([(position, "#rrggbb")], levels) for a table that draws exactly the colors `rows`,
    one level per row -- how the importer stores a lookup table (a MetPy .tbl, a list of
    hundreds of colors): matplotlib cannot be handed one stop per color and draw the same
    colors back (see "table" above), so the same flat runs and ramps are used, checked the
    same way.

    `rows` is an (n, 3) array of 8-bit colors in a block's order: the first row is the warm
    end, as a reversed block lists its stops. The stops are for a reversed block with
    `levels` = n and the range vmin_c..vmax_c."""
    from matplotlib.colors import ListedColormap

    rows = np.asarray(rows)
    if rows.ndim != 2 or rows.shape[1] != 3 or len(rows) < 2:
        raise ExportError("a table needs at least two colors, each three numbers from 0 to 255")
    if len(rows) > user_colortables._MAX_LEVELS:
        raise ExportError(f"a table can have at most {user_colortables._MAX_LEVELS} colors, "
                          f"not {len(rows)}")
    if not np.array_equal(rows, np.clip(np.round(rows), 0, 255)):
        raise ExportError("every color number must be a whole number from 0 to 255")
    rows = rows.astype(np.uint8)
    # The table as the app would draw it: its last row (the cold end) at vmin.
    cmap = ListedColormap(rows[::-1] / 255.0)
    vmax, vmin = float(vmax_c) + 273.15, float(vmin_c) + 273.15
    target = _Target("imported", cmap, None, vmax, vmin, colorize.table(cmap), len(rows), None)
    fields = user_colortables.parse_shareable(_first_exact(target, {}).text)
    return fields["stops"], fields.get("levels", user_colortables.DEFAULT_LEVELS)


# ------------------------------------------------------------------------- the target

_Target = namedtuple("_Target", "name cmap norm vmax vmin table N entry")


def _target(name):
    cmap, norm, vmax, vmin = colortable_registry.get(name)
    if norm is not None:
        _check_even_bands(name, cmap, norm, vmax, vmin)
    entry = _user_entries().get(name)
    return _Target(name, cmap, norm, vmax, vmin, colorize.table(cmap), cmap.N, entry)


def _check_even_bands(name, cmap, norm, vmax, vmin):
    """A BoundaryNorm the block can stand in for: one band per color, evenly across
    vmin..vmax, nothing clipped or extended -- the MetPy tables' norm exactly."""
    ok = (type(norm) is BoundaryNorm and not norm.clip and norm.extend == "neither"
          and norm.Ncmap == cmap.N == norm.N - 1
          and np.array_equal(np.asarray(norm.boundaries), np.linspace(vmin, vmax, cmap.N + 1)))
    if not ok:
        raise ExportError(f"{name}: drawn through a {type(norm).__name__} that a block cannot carry")


def _notes(name, target):
    registry = colortable_registry
    if target.entry is not None:
        return _entry_notes(target.entry)
    category = registry.registered_colortables().get(name)
    if category is None:
        category = next((c for c, names in registry.palette_categories().items() if name in names), None)
    return {"description": registry.descriptions().get(name, ""),
            "category": category or "Temperature (IR)"}


def _entry_notes(entry):
    # A winds or radar table's category is its kind's, which its units line says already.
    category = entry.get("category", "Temperature (IR)")
    if entry.get("units", "C") != "C":
        category = "Temperature (IR)"
    return {"description": entry.get("description") or "", "category": category}


# ---------------------------------------------------------------------- the drafts

# One way of writing a table: [(position text, position value, (r, g, b) bytes)], the
# bounds as texts in its units (Celsius, knots, dBZ), whether the block reverses, its level
# count, and its units.
_Draft = namedtuple("_Draft", "stops vmax_text vmin_text reversed levels units", defaults=("C",))


def _drafts(target):
    """(tier, draft) in the order they are tried: the tidiest first."""
    if target.entry is not None:
        try:
            own = _entry_draft(target.entry)
        except (ExportError, ValueError):
            own = None  # a stored color a block cannot write (see-through, say); the table can be
        if own is not None:
            yield "stops", own
    units = _target_units(target)
    bounds = _bounds_text(target.vmax, units), _bounds_text(target.vmin, units)
    if None in bounds:
        raise ExportError(f"{target.name}: its range ({target.vmin} to {target.vmax}) cannot be "
                          f"written exactly in {_UNIT_WORDS[units]}")
    span = _whole_span(float(bounds[0]), float(bounds[1]))
    curve = _curve_stops(target.cmap)
    if curve is not None:
        for side in ("below", "above"):
            stops = _structural_stops(curve, span, side, target)
            if stops is not None:
                yield "structural", _Draft(stops, *bounds, True, target.N, units)
    rows = np.asarray(target.table[:target.N])[::-1]  # the block's order: warm end first
    for margin in (_MARGIN, 0.01, None):
        stops = _table_stops(rows, span, margin)
        yield "table", _Draft(stops, *bounds, True, target.N, units)


_UNIT_WORDS = {"C": "Celsius + 273.15", "kt": "knots", "dBZ": "dBZ"}


def _target_units(target):
    """The units a table's block is written in: a table of yours says, a built-in one's
    category does (the SAR Wind tables are in knots, the radar ones in dBZ)."""
    if target.entry is not None:
        return user_colortables.normalize(target.entry)["units"]
    registry = colortable_registry
    category = registry.registered_colortables().get(target.name) or next(
        (c for c, names in registry.palette_categories().items() if target.name in names), None)
    return next((u for u, c in user_colortables.UNIT_CATEGORIES.items() if c == category), "C")


def _entry_draft(entry):
    """The stored stops, in the order they were saved, at their exact positions."""
    entry = user_colortables.normalize(entry)
    vmax, vmin = float(entry["vmax"]), float(entry["vmin"])
    span = _whole_span(vmax, vmin)
    stops = []
    for p, color in entry["stops"]:
        p = float(p)
        text = user_colortables._fraction(p, span) if span else _decimal(p)
        stops.append((text, p, _rgb_bytes(color)))
    return _Draft(stops, _decimal(vmax), _decimal(vmin), bool(entry.get("reversed", True)),
                  user_colortables._levels(entry), entry["units"])


def _rgb_bytes(color):
    r, g, b, a = to_rgba(color)
    if a != 1.0:
        raise ExportError(f"the color {color!r} is see-through")
    return tuple(int(round(255 * v)) for v in (r, g, b))


def _curve_stops(cmap):
    """[(x, (r, g, b) floats)]: the curve's own stops in its own order, read off its
    segment data, or None when it has none to read (a ListedColormap, a function)."""
    if not isinstance(cmap, LinearSegmentedColormap) or getattr(cmap, "_gamma", 1.0) != 1.0:
        return None
    data = cmap._segmentdata
    try:
        rgb = [np.asarray(data[k], dtype=float) for k in ("red", "green", "blue")]
    except (KeyError, TypeError, ValueError):
        return None  # a function, not a table of stops
    if any(c.ndim != 2 or c.shape[1] != 3 for c in rgb):
        return None
    x = rgb[0][:, 0]
    if any(not np.array_equal(c[:, 0], x) for c in rgb[1:]):
        return None
    if "alpha" in data:
        alpha = np.asarray(data["alpha"], dtype=float)
        if alpha.ndim != 2 or not (alpha[:, 1:] == 1.0).all():
            return None
    out, last = [], len(x) - 1
    for k in range(len(x)):
        left = tuple(c[k, 1] for c in rgb)
        right = tuple(c[k, 2] for c in rgb)
        if k == 0:
            out.append((x[k], right))          # matplotlib never reads y0 of the first row
        elif k == last:
            out.append((x[k], left))           # nor y1 of the last
        else:
            out.append((x[k], left))
            if right != left:
                out.append((x[k], right))      # a step drawn in the data itself
    return out


def _structural_stops(curve, span, side, target):
    """The curve's stops written for a block that ends in .reversed(), as the built-in
    curves do: listed from the warm end, at 1 - x.

    Two stops 1e-6 apart are the nudge a curve's author used for a hard step, so they are
    written as the repeated position they mean. Each position is first written tidily --
    the nearest whole degree (or tenth, or short decimal) within 1e-9 -- and the colors
    are chosen, byte by byte, to give every row of the table exactly (_fit_bytes). Where
    no colors do, the stops around that stretch move to exactly where the curve has them,
    or, when no number reaches that through .reversed()'s 1 - x (as for many positions
    under 0.5), to just below or just above it (`side`), until the fit holds.

    None if it never does. That happens to a curve anchored on colors between whole bytes
    (0.88 rather than 224/255) whose rows a ramp between whole bytes cannot follow, and to
    a curve built without .reversed() when a row's exact value is a whole level and the
    last bit of a position under 0.5 decides between two bytes. Stops added inside such a
    stretch do not rescue it -- a ramp between whole bytes cannot follow a fractional
    slope through a whole level either -- so those curves are written by the table tier."""
    block = [(1.0 - x, x, color) for x, color in reversed(curve)]
    groups, group = [], None
    for i, (e, _x, _c) in enumerate(block):
        if group is not None and e != 1.0 and 0.0 <= e - block[i - 1][0] < _NUDGE_SEEN:
            group.append(i)                       # the nudge undone: a hard step
            continue
        group = [i]
        groups.append(group)
    group_of = {i: g for g, members in enumerate(groups) for i in members}
    tidy = [_tidy_place(block, members, span) for members in groups]
    exact = [_exact_place(block, members, span, side) for members in groups]
    pinned = set()
    colors = np.array([c for _e, _x, c in reversed(block)], dtype=float)  # in the curve's order
    last = len(block) - 1
    while True:
        written = []
        for g, members in enumerate(groups):
            written.extend([exact[g] if g in pinned else tidy[g]] * len(members))
        values = [v for _t, v in written]
        ordered = values[0] == 0.0 and values[-1] == 1.0 and all(b >= a for a, b in zip(values, values[1:]))
        fitted, stuck = _fit_bytes(values, colors, target) if ordered else (None, None)
        if fitted is not None:
            return [(text, value, tuple(int(v) for v in fitted[last - j]))
                    for j, (text, value) in enumerate(written)]
        # the stretch between the rebuilt rows stuck - 1 and stuck: block stops last - stuck
        # and last - stuck + 1; pin both, or failing that the nearest unpinned one
        if stuck is None:
            near = [g for g in range(len(groups)) if g not in pinned]
        else:
            ends = {group_of[last - stuck], group_of[last - stuck + 1]}
            near = sorted((g for g in range(len(groups)) if g not in pinned),
                          key=lambda g: (g not in ends, min(abs(g - e) for e in ends)))
        if not near:
            return None
        pinned.update(near[:2] if stuck is not None and near[0] in ends else near[:1])


def _tidy_place(block, members, span):
    for i in members:
        hit = _tidy_fraction(block[i][0], span)
        if hit:
            return hit
    return _tidy_decimal(block[members[0]][0])


def _exact_place(block, members, span, side):
    q = _reaching(block[members[0]][1], side)
    if span:
        for digits in range(4):
            num = f"{round(q * span, digits):.{digits}f}"
            if "." in num:
                num = num.rstrip("0").rstrip(".")
            if float(num) / span == q:
                return f"{num}/{span}", q
    return _decimal(q), q


def _tidy_fraction(e, span):
    """e as a/span with a whole number of degrees (or tenths, hundredths, thousandths),
    within 1e-9, or None."""
    if not span:
        return None
    for digits in range(4):
        num = f"{round(e * span, digits):.{digits}f}"
        if "." in num:
            num = num.rstrip("0").rstrip(".")
        value = float(num) / span
        if abs(value - e) <= 1e-9:
            return f"{num}/{span}", value
    return None


def _tidy_decimal(e):
    for digits in range(1, 12):
        text = f"{e:.{digits}f}".rstrip("0").rstrip(".") or "0"
        if abs(float(text) - e) <= 1e-9:
            return text, float(text)
    return _decimal(e), float(e)


def _reaching(x, side):
    """The q that .reversed() turns into exactly x (1.0 - q == x), or the nearest one
    whose 1.0 - q lies just below ("below") or just above ("above") it."""
    q0 = 1.0 - x
    landed = [(1.0 - q, q) for q in (q0, *_neighbors(q0, 8)) if 0.0 <= q <= 1.0]
    exact = [q for got, q in landed if got == x]
    if exact:
        return exact[0]
    if side == "below":
        under = [(got, q) for got, q in landed if got < x]
        return max(under)[1] if under else q0
    over = [(got, q) for got, q in landed if got > x]
    return min(over)[1] if over else q0


def _fit_bytes(values, colors, target):
    """((M, 3) 8-bit colors for the curve's stops -- in the curve's order, as its
    reversed rebuild reads them -- that give exactly the target's table rows, None), or
    (None, k) when no colors fit the stretch between its stops k - 1 and k (k None when
    nothing fits for another reason).

    The rebuild's geometry is taken from the rebuild itself (build_cmap on the written
    positions), and every row is computed as matplotlib computes it
    (colors._create_lookup_table, then x 255 truncated), so a row whose exact value is a
    whole level -- where a position one bit off decides between two bytes -- is decided
    the same way here as there. Each channel is a chain: a stop's byte serves the rows on
    both sides of it, so the bytes are chosen by dynamic programming, as close as the rows
    allow to the byte the curve's own color draws as (int(255 * y): exactly the #rrggbb,
    for a curve written in them; 84 for a curve's 0.33333, as the app has always drawn it)."""
    n = target.N
    if (target.table[:n, 3] != 255).any():
        return None, None
    try:
        rebuilt, _vmax, _vmin = user_colortables.build_cmap(
            {"stops": [(v, "#000000") for v in values], "vmin_c": 0.0, "vmax_c": 1.0,
             "reversed": True, "levels": n})
    except ValueError:
        return None, None
    x = np.asarray(rebuilt._segmentdata["red"], dtype=float)[:, 0]
    m = len(x)
    if m != len(colors):
        return None, None
    xs = x * (n - 1)
    xind = (n - 1) * np.linspace(0, 1, n) ** 1.0
    ind = np.searchsorted(xs, xind)[1:-1]
    dist = (xind[1:-1] - xs[ind - 1]) / (xs[ind] - xs[ind - 1])
    rows_of = [np.flatnonzero(ind == k) for k in range(m)]  # interior table row r + 1
    out = np.zeros((m, 3), dtype=int)
    for c in range(3):
        want = target.table[:n, c].astype(int)
        prefer = (np.clip(colors[:, c], 0.0, 1.0) * 255).astype(np.uint8).astype(int)
        options = []
        for k in range(m):
            if k == 0:
                options.append([want[0]])            # the first row is the first stop's color
            elif k == m - 1:
                options.append([want[n - 1]])        # the last row, the last stop's
            else:
                options.append(sorted({min(max(prefer[k] + d, 0), 255) for d in (0, -1, 1, -2, 2)},
                                      key=lambda v, p=prefer[k]: (abs(v - p), v)))
        chain, stuck = _chain(options, prefer, rows_of, dist, want)
        if chain is None:
            return None, stuck
        out[:, c] = chain
    return out, None


def _chain(options, prefer, rows_of, dist, want):
    """(the cheapest byte per stop -- cost: distance from `prefer` -- for which every
    table row between each pair of neighbors comes out as `want`, None), or (None, the
    first stop no byte reaches)."""
    cost = {v: abs(v - prefer[0]) for v in options[0]}
    back = [{}]
    for k in range(1, len(options)):
        rows = rows_of[k]
        frac, wanted = dist[rows], want[rows + 1]
        new_cost, links = {}, {}
        for b in options[k]:
            yb = b / 255
            best = None
            for a, so_far in sorted(cost.items(), key=lambda kv: kv[1]):
                if rows.size:
                    ya = a / 255
                    got = (np.clip(frac * (yb - ya) + ya, 0.0, 1.0) * 255).astype(np.uint8)
                    if not np.array_equal(got, wanted):
                        continue
                best = (so_far, a)
                break
            if best is not None:
                new_cost[b] = best[0] + abs(b - prefer[k])
                links[b] = best[1]
        if not new_cost:
            return None, k
        cost = new_cost
        back.append(links)
    v = min(cost, key=cost.get)
    chain = [v]
    for k in range(len(options) - 1, 0, -1):
        v = back[k][v]
        chain.append(v)
    return chain[::-1], None


def _table_stops(rows, span, margin):
    """The table's rows (block order) as flat runs and ramps whose ends sit between rows.

    margin=None gives flat runs only: every row lands on its own color exactly, since a
    stop's k/255 times 255 truncates back to k for every byte."""
    n = len(rows)
    if n < 2:
        raise ExportError("a table of one color has no stops to write")
    if (rows[:, 3] != 255).any():
        raise ExportError("it has see-through colors")
    values = rows[:, :3].astype(float)
    q = np.arange(n) / (n - 1)  # where each row is sampled, warm end at 0
    gaps = [_gap_position(j, n, span) for j in range(n - 1)]
    segments, a, prev = [], 0, None
    while a < n:
        b, fit = a, _fit(values, q, gaps, a, a, n, margin, prev)
        while b + 1 < n:
            nxt = _fit(values, q, gaps, a, b + 1, n, margin, prev)
            if nxt is None:
                break
            b, fit = b + 1, nxt
        segments.append((a, b, *fit))
        prev, a = fit[1], b + 1
    ends = (("0" if not span else f"0/{span}"), 0.0), (("1" if not span else f"{span}/{span}"), 1.0)
    stops = []
    for a, b, u, v, joined in segments:
        left = ends[0] if a == 0 else gaps[a - 1]
        right = ends[1] if b == n - 1 else gaps[b]
        if not joined:
            stops.append((left[0], left[1], u))
        stops.append((right[0], right[1], v))
    return stops


def _gap_position(j, n, span):
    """(text, value): a tidy position between rows j and j + 1 -- a whole degree, else
    the shortest decimal -- kept within the middle half of the gap, well clear of both."""
    step = 1.0 / (n - 1)
    lo, hi, mid = (j + 0.25) * step, (j + 0.75) * step, (j + 0.5) * step
    if span:
        a_lo, a_hi = math.ceil(lo * span), math.floor(hi * span)
        if a_lo <= a_hi:
            a = min(range(a_lo, a_hi + 1), key=lambda a: abs(a / span - mid))
            return f"{a}/{span}", a / span
        for digits in range(1, 17):
            num = f"{mid * span:.{digits}f}"
            value = float(num) / span
            if lo <= value <= hi:
                return f"{num}/{span}", value
    for digits in range(1, 17):
        text = f"{mid:.{digits}f}"
        if lo <= float(text) <= hi:
            return text, float(text)
    return _decimal(mid), mid


def _fit(values, q, gaps, a, b, n, margin, prev):
    """(left (r, g, b), right (r, g, b), joined) of one straight ramp that gives rows a..b
    their exact bytes, or None.

    The ends sit between rows (on the table's own ends for its first and last row). The
    ramp first tries to carry on from the color the one before it ended on (`prev`), which
    needs a single stop there; otherwise it starts with a hard step, whose second stop
    sits where build_cmap's nudge puts it, 1e-6 later. A channel that is flat across the
    run is written flat, which is exact; one that changes must land every row at least
    `margin` inside its byte. margin=None allows flat runs only."""
    right = 1.0 if b == n - 1 else gaps[b][1]
    seg, qs = values[a:b + 1], q[a:b + 1]
    if prev is not None:
        fit = _fit_ends(seg, qs, gaps[a - 1][1], right, False, b == n - 1, margin, prev)
        if fit is not None:
            return (*fit, True)
    left = 0.0 if a == 0 else gaps[a - 1][1] + _NUDGE
    fit = _fit_ends(seg, qs, left, right, a == 0, b == n - 1, margin, None)
    return None if fit is None else (*fit, False)


def _fit_ends(seg, qs, left, right, starts_table, ends_table, margin, fixed_left):
    d = (qs - left) / (right - left)
    u, v = [], []
    for c in range(3):
        t = seg[:, c]
        pinned = None if fixed_left is None else fixed_left[c]
        if (t == t[0]).all() and pinned in (None, t[0]):
            u.append(int(t[0]))
            v.append(int(t[0]))
            continue
        if margin is None:
            return None
        pair = _fit_channel(t, d, starts_table, ends_table, margin, pinned)
        if pair is None:
            return None
        u.append(pair[0])
        v.append(pair[1])
    return tuple(u), tuple(v)


_LEVELS = np.arange(256, dtype=float)


def _fit_channel(t, d, starts_table, ends_table, margin, pinned=None):
    """Integer end bytes (u, v) with t[j] + margin <= u + (v - u) * d[j] <= t[j] + 1 - margin
    for every row inside the ramp; a row ON an end of the table takes that end's color
    exactly (matplotlib reads it straight off the stop), so its byte pins the end, as
    `pinned` pins the left one when the ramp carries on from the one before."""
    inside = (d > 0) & (d < 1)
    if pinned is not None:
        us = np.array([float(pinned)])
    elif starts_table and d[0] == 0:
        us = np.array([t[0]])
    else:
        us = _LEVELS
    di, ti = d[inside], t[inside]
    if di.size == 0:
        if starts_table and ends_table and d[0] == 0 and d[-1] == 1:
            return int(us[0]), int(t[-1])
        return None
    # u * (1 - d) + v * d within [lo, hi]  ->  v within [(lo - u (1 - d)) / d, (hi - u (1 - d)) / d]
    lo = (ti + margin)[None, :] - us[:, None] * (1 - di)[None, :]
    hi = (ti + 1 - margin)[None, :] - us[:, None] * (1 - di)[None, :]
    v_lo = np.max(lo / di[None, :], axis=1)
    v_hi = np.min(hi / di[None, :], axis=1)
    if ends_table and d[-1] == 1:
        fixed = t[-1]
        ok = (v_lo <= fixed) & (fixed <= v_hi)
        if not ok.any():
            return None
        width = np.where(ok, np.minimum(fixed - v_lo, v_hi - fixed), -np.inf)
        i = int(np.argmax(width))
        return int(us[i]), int(fixed)
    v_lo_i = np.maximum(np.ceil(v_lo), 0)
    v_hi_i = np.minimum(np.floor(v_hi), 255)
    ok = v_lo_i <= v_hi_i
    if not ok.any():
        return None
    width = np.where(ok, v_hi - v_lo, -np.inf)
    i = int(np.argmax(width))
    v = min(max(round((v_lo[i] + v_hi[i]) / 2), v_lo_i[i]), v_hi_i[i])
    return int(us[i]), int(v)


# ------------------------------------------------------------------------ the text

def _render(name, draft, notes):
    function = user_colortables._python_name(name)
    if not function.isidentifier():
        raise ExportError(f"{name!r} cannot name a Python function: use letters, digits and _")
    lines = [f"def {function}():"]
    if notes:
        # A #rrggbb in the prose would be read as that line's color by parse_stops, so it
        # cannot survive into the docstring. Backslashes and double quotes are escaped: a
        # "\N" or "\x" is otherwise a broken escape, and a closing quote ends the docstring.
        description = user_colortables._HEX_RE.sub("", notes.get("description") or "").strip()
        if description:
            escaped = description.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'    """{escaped}"""')
        # Written for every category but the default, None included: a hidden table with
        # no line would paste back as whatever the dialog is showing.
        category = notes.get("category", "Temperature (IR)")
        if category != "Temperature (IR)":
            lines.append(f"    # category: {'none' if category is None else category}")
    if draft.units != "C":
        # what the bounds below are in; without it they would be read as temperatures
        lines.append(f"    # units: {draft.units}")
    if len(draft.stops) < 2:
        raise ExportError("fewer than two stops")
    lines.append('    newcmp = LinearSegmentedColormap.from_list("", [')
    body = [f'        ({text}, "{_hex(rgb)}")' for text, _value, rgb in draft.stops]
    lines.extend(f"{line}," for line in body[:-1])
    lines.append(body[-1])
    lines.append("    ])" if draft.levels == 256 else f"    ], N={draft.levels})")
    lines.append(f"    vmax = {_bound_expression(draft.vmax_text, draft.units)}")
    lines.append(f"    vmin = {_bound_expression(draft.vmin_text, draft.units)}")
    lines.append("    return newcmp" + (".reversed()" if draft.reversed else "") + ", vmax, vmin")
    return "\n".join(lines) + "\n"


def _bound_expression(text, units):
    """A bound as the block writes it: Celsius + 273.15, knots / 1.9438452 (the m/s it is
    drawn at, when run), or plain dBZ."""
    if units == "C":
        return f"{text} + 273.15"
    if units == "kt":
        return f"{text} / {_decimal(user_colortables.KT_PER_MS)}"
    return text


def _hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _kelvin_bounds_text(kelvin):
    """The shortest Celsius text c with float(c) + 273.15 == kelvin exactly, as build_cmap
    adds it, or None. 30 for 303.15; but -94.99999999999997 for a range written as 178.15
    K, since -95 + 273.15 is 178.14999999999998 -- a hair off, and the check is to the bit."""
    kelvin = float(kelvin)
    if not math.isfinite(kelvin):
        return None
    c = kelvin - 273.15
    for digits in range(0, 17):
        text = f"{c:.{digits}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        if text in ("-0", ""):
            text = "0"
        if float(text) + 273.15 == kelvin:
            return text
    for candidate in (c, *_neighbors(c, 8)):
        if candidate + 273.15 == kelvin:
            return _decimal(candidate)
    return None


def _bounds_text(value, units):
    """The shortest text that is exactly `value` (as the engine draws it) in a block of
    these units, or None: Celsius for Kelvin (_kelvin_bounds_text), knots for m/s
    (float(text) / KT_PER_MS == value), dBZ as it is."""
    if units == "C":
        return _kelvin_bounds_text(value)
    value = float(value)
    if not math.isfinite(value):
        return None
    if units == "dBZ":
        return _decimal(value)
    knots = value * user_colortables.KT_PER_MS
    for digits in range(0, 17):
        text = f"{knots:.{digits}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        if text in ("-0", ""):
            text = "0"
        if float(text) / user_colortables.KT_PER_MS == value:
            return text
    for candidate in (knots, *_neighbors(knots, 8)):
        if candidate / user_colortables.KT_PER_MS == value:
            return _decimal(candidate)
    return None


def _neighbors(x, k):
    out, lo, hi = [], x, x
    for _ in range(k):
        lo, hi = np.nextafter(lo, -np.inf), np.nextafter(hi, np.inf)
        out += [float(lo), float(hi)]
    return out


def _whole_span(vmax_c, vmin_c):
    """The range in whole degrees, for a/span positions, or 0 if it is not whole."""
    span = vmax_c - vmin_c
    if math.isfinite(span) and span > 0 and abs(span - round(span)) < 1e-9:
        return int(round(span))
    return 0


# ---------------------------------------------------------------------- the self-check

def _entry_from_fields(fields):
    """What add() would store from parse_shareable's fields, without storing it."""
    entry = {"name": fields.get("name"), "stops": [(float(p), c) for p, c in fields["stops"]],
             "reversed": bool(fields.get("reversed_", True))}
    if fields.get("units", "C") == "C":
        entry.update(vmin_c=float(fields["vmin_c"]), vmax_c=float(fields["vmax_c"]))
    else:
        entry.update(units=fields["units"], vmin=float(fields["vmin"]), vmax=float(fields["vmax"]))
    if fields.get("levels", 256) != 256:
        entry["levels"] = int(fields["levels"])
    return entry


def _mismatch(text, target):
    """None when the block rebuilds exactly the target, else what differs."""
    try:
        fields = user_colortables.parse_shareable(text)
    except ValueError as e:
        return f"does not read back ({e})"
    if fields.get("name") != target.name:
        return f"reads back as {fields.get('name')!r}"
    keys = ("vmax_c", "vmin_c") if fields.get("units", "C") == "C" else ("vmax", "vmin")
    if not all(k in fields for k in keys):
        return "its range does not read back"
    try:
        cmap, vmax, vmin = user_colortables.build_cmap(_entry_from_fields(fields))
    except Exception as e:
        return f"does not build ({e})"
    if (vmax, vmin) != (target.vmax, target.vmin):
        return f"range reads back as {vmin}..{vmax} K, not {target.vmin}..{target.vmax} K"
    if cmap.N != target.N:
        return f"{cmap.N} levels, not {target.N}"
    table = colorize.table(cmap)
    if not np.array_equal(table, target.table):
        rows = np.flatnonzero((table != target.table).any(axis=1))
        return f"{rows.size} of {len(table)} table rows differ (first: row {rows[0]})"
    return None


def _check_stops_only(text, entry):
    """For an entry that does not build: the block must give back its stops, range and
    direction, so pasting it back changes nothing. A position may come back within
    _STOP_SLACK of where it was: written as a fraction it cannot always be the same number
    to the last bit, and such a table has no colors yet to compare instead."""
    fields = user_colortables.parse_shareable(text)
    want = [(float(p), to_hex(c)) for p, c in entry["stops"]]
    got = [(p, to_hex(c)) for p, c in fields["stops"]]
    stored = user_colortables.normalize(entry)
    keys = ("vmax_c", "vmin_c") if stored["units"] == "C" else ("vmax", "vmin")
    same_range = (fields.get(keys[0]), fields.get(keys[1])) == (float(stored["vmax"]), float(stored["vmin"]))
    same_range = same_range and fields.get("units", "C") == stored["units"]
    same_stops = len(got) == len(want) and all(
        c1 == c2 and abs(p1 - p2) <= _STOP_SLACK for (p1, c1), (p2, c2) in zip(got, want))
    if not same_stops or not same_range or fields.get("reversed_") != bool(entry.get("reversed", True)):
        raise ExportError(f"{entry.get('name')}: the block does not give back the same stops")


# How far a stop of a table that does not build yet may come back from where it was.
_STOP_SLACK = 1e-12


def _gives_back(text, entry):
    try:
        _check_stops_only(text, entry)
    except (ExportError, ValueError):
        return False
    return True
