"""Color tables from elsewhere, read into drafts that the Import window shows and saves.

parse_any(text or path) returns a list of Draft: one per table found. It reads

* tcviz's own shared blocks -- one ``def name(): ... from_list ... return newcmp...``
  or many, as in the file "Export many..." writes (user_colortables.parse_shareable
  reads each block, so a block comes back exactly as the exporter's rebuild() builds it).
  The export file's ``# ---- Water vapor ----`` banner marks the blocks after it as
  water-vapor tables;
* stop lists as the Add dialog takes them (user_colortables.parse_stops), with the
  optional ``# range:``, ``# description:`` and ``# reversed:`` lines of the bot's
  palettes folder;
* GRLevelX / Baron ``.pal`` files (palfile.parse_pal): radar tables, on the file's own
  dBZ scale;
* MetPy ``.tbl`` files (palfile.parse_tbl, the reader colormaps uses for its own six):
  one color per level, kept as exactly those levels;
* GMT ``.cpt`` files: ``z0 color z1 color`` slices with the colors as ``r g b``,
  ``r/g/b``, ``#hex``, names, gray levels or hue-saturation-value; the B, F and N lines
  (background, foreground, no data) are not used, since tcviz picks those itself; a
  slice that does not start with the color the one before it ended on is a hard step;
* tcviz's own store, colortables.json (as copied from another computer, or into the
  bot's palettes folder): every table in it, with all it carries;
* plain lists and CSV: one color per line as ``#hex``, ``rgb(...)``, ``r,g,b`` (0-255
  or 0-1) or a color name, each optionally after a position or a value; with none, the
  colors are spread evenly.

The range comes from the file where it has one. A value scale is read as Kelvin when
every value is above 150 and as degrees C when some are below zero (no Celsius bound here
comes near 150 -- the rule user_colortables._bound_celsius uses); anything else is a
guess, and the draft says so. A list of more than 256 colors is a lookup table: it keeps
one level per color, written as the flat runs and ramps the exporter uses
(colortable_export.lut_stops), since one stop per color does not draw the same colors.

Nothing is dropped unseen: a line that is not a color becomes a plain-words warning on the
draft ("Line 7 isn't a color: ..."). Radar tables (dBZ) and wind tables (knots) are saved
like any other, in their own units; a table in units tcviz does not know says so instead.

Qt-free. The Import window (tcviz_gui/pages/import_colortables_dialog.py) saves a draft
with save(), under a name free_name() picked from taken_names().
"""
import colorsys
import contextlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from matplotlib.colors import to_hex, to_rgb

from . import colortable_registry, edition, palfile, user_colortables

KIND_LABELS = {"ir": "Infrared", "wv": "Water vapor", "radar": "Radar", "wind": "Winds"}
KIND_UNITS = {"ir": "C", "wv": "C", "radar": "dBZ", "wind": "kt"}
KIND_CATEGORIES = {"ir": "Temperature (IR)", "wv": "Water Vapor",
                   "radar": user_colortables.UNIT_CATEGORIES["dBZ"], "wind": user_colortables.UNIT_CATEGORIES["kt"]}
UNIT_LABELS = {"C": "°C", "dBZ": "dBZ", "kt": "kt"}

# The range a table is given when its source has none: the one a new table of its kind
# starts with in the editor.
DEFAULT_RANGES = user_colortables.DEFAULT_RANGES
# A color table is a few KB; anything this big is something else, and reading it line by
# line would only produce a wall of "isn't a color".
MAX_FILE_BYTES = 4 << 20
MAX_WARNINGS = 12
SUFFIXES = (".txt", ".py", ".pal", ".tbl", ".cpt", ".csv", ".json")
_KELVIN_CUTOFF = user_colortables._KELVIN_CUTOFF_C


class ImportProblem(ValueError):
    """Nothing in a file or paste could be read as a color table (the message says why,
    in plain words)."""


@dataclass
class Draft:
    """One table read from a file or a paste, not saved yet.

    `stops` are [(position 0-1, "#rrggbb")] as a block lists them; `vmin`/`vmax` the range
    in `units` ("C" for infrared and water vapor, "dBZ" for radar, "kt" for winds);
    `levels` how many colors it is drawn with. `warnings` are plain-words notes about
    anything that was not read or had to be guessed; `problem`, when set, is why it
    cannot be saved. A reader leaves the range None when its source gives none, and
    parse_any then gives it the default one for its kind, with a warning saying so."""
    name: str
    stops: list = field(default_factory=list)
    vmin: float = None
    vmax: float = None
    kind: str = "ir"
    reversed: bool = True
    levels: int = user_colortables.DEFAULT_LEVELS
    description: str = ""
    hidden: bool = False
    warnings: list = field(default_factory=list)
    problem: str = None
    source: str = ""

    @property
    def units(self):
        return KIND_UNITS[self.kind]

    @property
    def vmin_c(self):
        """The cold end in degrees C, for an infrared or water-vapor table (else None)."""
        return self.vmin if self.units == "C" else None

    @property
    def vmax_c(self):
        return self.vmax if self.units == "C" else None

    @property
    def category(self):
        return KIND_CATEGORIES.get(self.kind)

    @property
    def can_save(self):
        return self.problem is None

    def entry(self, name=None):
        """The entry user_colortables.add would store under `name` (the draft's own by
        default) -- for a preview or a check; nothing is saved."""
        entry = {"name": name or self.name, "stops": list(self.stops),
                 "description": self.description, "category": self.category or "Temperature (IR)",
                 "reversed": self.reversed, "units": self.units, "vmin": float(self.vmin),
                 "vmax": float(self.vmax), "levels": self.levels, "hidden": self.hidden}
        if self.units == "C":
            entry["vmin_c"], entry["vmax_c"] = entry["vmin"], entry["vmax"]
        return entry

    def cmap(self):
        """The colormap the table draws with."""
        return user_colortables.build_cmap(self.entry())[0]

    def range_text(self):
        return f"{_number(self.vmin)} to {_number(self.vmax)} {UNIT_LABELS[self.units]}"


# ------------------------------------------------------------------------------ the API

def parse_any(source, *, name=None):
    """[Draft] for every table in `source`: a path (str or Path) or the text itself.

    `name` overrides the name a table without one of its own gets (a file's is its file
    name). Raises ImportProblem when nothing in it reads as a color table."""
    text, label, suffix, stem = _read(source)
    base = _safe_name(name or stem or "") or "imported"
    if suffix == ".pal" or (suffix not in (".tbl", ".cpt") and _looks_pal(text)):
        drafts = [_from_pal(text, base, label)]
    elif suffix == ".tbl":
        drafts = [_from_tbl(text, base, label)]
    elif (stored := _store_entries(text)) is not None:
        drafts = _from_store(stored, base)
    elif user_colortables.looks_shareable(text):
        drafts = _from_blocks(text, base, label)
    elif suffix == ".cpt" or _looks_cpt(text):
        drafts = [_from_cpt(text, base, label)]
    else:
        drafts = [_from_list(text, base, label)]
    for draft in drafts:
        _finish(draft, label)
    return _offered(drafts, label)


def _offered(drafts, label):
    """The drafts of kinds the edition running offers (tcviz.edition.kinds): all of them in
    tcviz. Colortable Editor works with infrared and water-vapor tables only, so a winds or
    radar table (a .pal file is one) is left out -- and said so, on a table that stays or,
    with none left, as the ImportProblem."""
    kinds = edition.kinds()
    kept = [d for d in drafts if d.kind in kinds]
    left_out = len(drafts) - len(kept)
    if not left_out:
        return drafts
    if not kept:
        raise ImportProblem(f"{_cap(label)} holds no infrared or water-vapor color table, the only kinds "
                            f"{edition.app_name()} works with.")
    kept[0].warnings.append(f"{left_out} other table{'s' if left_out != 1 else ''} in it "
                            f"{'are' if left_out != 1 else 'is'} not an infrared or water-vapor table, "
                            f"so {'they were' if left_out != 1 else 'it was'} left out.")
    return kept


def taken_names():
    """Every name a new table cannot be saved under: the user's own tables, every table and
    look the program has (colortable_registry.reserved_names), and anything registered."""
    registry = colortable_registry
    names = {e.get("name") for e in user_colortables.load() if isinstance(e, dict)}
    names |= set(registry.reserved_names())
    names |= set(registry.palettes()) | set(registry.registered_builders())
    names.discard(None)
    return names


def free_name(name, taken):
    """`name` if no table has it, else the first free of name_copy, name_copy2, ... -- the
    numbering the Manage window gives its copies (colortable_library.free_name), checked
    here against `taken`, which also holds the names other rows in an import will take."""
    if name not in taken:
        return name
    number = 1
    while (candidate := f"{name}_copy" + (str(number) if number > 1 else "")) in taken:
        number += 1
    return candidate


def save(draft, name=None):
    """Store `draft` under `name` (its own by default) with user_colortables.add and make
    it live in colormaps; returns the stored entry. Raises what add raises (NameTaken,
    NameReserved, ValueError), and ValueError for a draft that cannot be saved."""
    if draft.problem:
        raise ValueError(draft.problem)
    # a winds or radar table's range is in its own units; an infrared one's in degrees C
    span = ({"vmin_c": draft.vmin, "vmax_c": draft.vmax} if draft.units == "C"
            else {"units": draft.units, "vmin": draft.vmin, "vmax": draft.vmax})
    entry = user_colortables.add(
        name or draft.name, draft.stops, description=draft.description, category=draft.category,
        reversed_=draft.reversed, levels=draft.levels, hidden=draft.hidden, **span)
    colortable_registry.register_user_colortable(entry)
    return entry


# ----------------------------------------------------------------------- the source

def _read(source):
    """(text, label, suffix, file stem) -- the label is the file name, or "the pasted text"."""
    path = None
    if isinstance(source, os.PathLike):
        path = Path(source)
    elif isinstance(source, str) and "\n" not in source and source.strip():
        candidate = Path(source.strip())
        with contextlib.suppress(RuntimeError):     # no home folder to put for "~" (Windows can)
            candidate = candidate.expanduser()
        if _is_file(candidate):
            path = candidate
        elif candidate.suffix.lower() in SUFFIXES and not re.search(r"#[0-9a-fA-F]{3}", source):
            raise ImportProblem(f"There is no file {source.strip()}.")
    if path is None:
        return str(source), "the pasted text", "", None
    if path.is_dir():
        raise ImportProblem(f"{path.name} is a folder, not a file.")
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ImportProblem(f"{path.name} is too big to be a color table "
                                f"({size / 1e6:.0f} MB).")
        data = path.read_bytes()
    except OSError as e:
        raise ImportProblem(f"{path.name} could not be opened: {e.strerror or e}.") from e
    if b"\0" in data[:8192]:
        raise ImportProblem(f"{path.name} is not a text file, so it holds no color table "
                            f"{edition.app_name()} can read.")
    return data.decode("utf-8-sig", errors="replace"), path.name, path.suffix.lower(), path.stem


def _is_file(path):
    try:
        return path.is_file()
    except (OSError, ValueError):   # a name too long, or with a NUL in it
        return False


def _safe_name(text):
    """`text` as a table name: lowercase letters, digits and _, starting with a letter."""
    name = re.sub(r"[^a-z0-9_]+", "_", text.strip().lower())
    name = re.sub(r"_+", "_", name).strip("_")
    if name and not name[0].isalpha():
        name = f"table_{name}"
    return name


def _finish(draft, label):
    """The checks every draft gets, whatever it was read from."""
    draft.source = label
    if draft.vmin is None or draft.vmax is None:
        draft.vmin, draft.vmax = DEFAULT_RANGES[draft.units]
        if draft.problem is None:
            draft.warnings.append(_no_range_warning(draft.kind))
    if draft.stops and draft.problem is None:
        try:
            user_colortables._check_range(draft.units, float(draft.vmin), float(draft.vmax))
            draft.cmap()
        except ValueError as e:
            draft.problem = f"Can't be used: {_sentence(str(e))}"
    # A file that is mostly not colors would bury the notes that matter under a line each;
    # past MAX_WARNINGS such lines are counted instead of listed.
    unread = [w for w in draft.warnings if w.startswith("Line ")]
    if len(unread) > MAX_WARNINGS:
        others = [w for w in draft.warnings if not w.startswith("Line ")]
        more = len(unread) - (MAX_WARNINGS - 1)
        draft.warnings = unread[:MAX_WARNINGS - 1] + [f"...and {more} more lines that aren't colors."] + others


def _sentence(text):
    text = text.strip().rstrip(".")
    return (text[:1].upper() + text[1:] + ".") if text else ""


def _number(x):
    """A range end as people write it: 50, -90, 0.5, -99.85."""
    x = float(x)
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.6g}"


def _unread(number, line, why=None):
    """The warning for a line that could not be read."""
    because = f" ({why})" if why else ""
    return f"Line {number} isn't a color{because}: '{_snippet(line)}'"


def _snippet(line):
    snippet = line.strip()
    return snippet[:45] + "..." if len(snippet) > 48 else snippet


# Lines that are a block's code or punctuation rather than a stop: the def, the from_list,
# the closing bracket, the bounds, the return, imports, decorators, and comments.
_CODE_RE = re.compile(r"^\s*(def\s|@|newcmp\s*=|vmax\s*=|vmin\s*=|return\b|import\s|from\s+\S+\s+import\s"
                      r"|#|//|;|\"\"\"|'''|\]\)?\s*,\s*N\s*=\s*\d+\s*\)\s*$)")
_PUNCTUATION_RE = re.compile(r"^[\s\[\](){},;'\"`]*$")


def _is_noise(line):
    return bool(_PUNCTUATION_RE.match(line) or _CODE_RE.match(line))


def _docstring_lines(lines):
    """Indexes of the lines inside (or opening or closing) a triple-quoted string."""
    inside, out = False, set()
    for i, line in enumerate(lines):
        quotes = line.count('"""') + line.count("'''")
        if inside or quotes:
            out.add(i)
        if quotes % 2:
            inside = not inside
    return out


# ---------------------------------------------------------- ranges and positions

def _celsius(kelvin):
    c = round(float(kelvin) - 273.15, 6)
    return 0.0 if c == 0 else c


def _range_for(lo, hi, kind):
    """(vmin, vmax, warning or None, is_value_scale) for a table whose numbers run lo..hi;
    the range is None when the numbers do not give one.

    For infrared and water vapor the numbers are temperatures when their size says which
    unit: every one above 150 is Kelvin, some below zero (and none above 150) is degrees
    C. Numbers from 0 to 1 are positions, not temperatures; anything else is a guess."""
    if kind in ("radar", "wind"):
        return lo, hi, None, True
    if lo > _KELVIN_CUTOFF:
        return _celsius(lo), _celsius(hi), None, True
    if lo < 0 and hi <= _KELVIN_CUTOFF:
        return lo, hi, None, True
    if lo >= 0 and hi <= 1:
        return None, None, None, False
    if lo >= 0 and hi <= _KELVIN_CUTOFF:
        return lo, hi, (f"Its numbers run from {_number(lo)} to {_number(hi)} with none below "
                        "zero, so they were taken as °C. That is a guess."), True
    return None, None, (f"Its numbers run from {_number(lo)} to {_number(hi)}, which is neither "
                        "a °C nor a Kelvin range, so they were used only to place the colors."), False


def _no_range_warning(kind="ir"):
    lo, hi = DEFAULT_RANGES[KIND_UNITS[kind]]
    return (f"It doesn't give a range of its own, so it was given {_number(lo)} to "
            f"{_number(hi)} {UNIT_LABELS[KIND_UNITS[kind]]}.")


def _placed(points, kind, numbers_are_values):
    """(stops, vmin, vmax, reversed, warning) for [(number, "#rrggbb")] in file order.

    On a value scale, an infrared or water-vapor table is written the way the built-in
    curves are -- reversed, its warm end at position 0 -- and a radar or wind table runs
    from its low end. Numbers that are positions (a plain list's) count from the warm end,
    as the Add dialog's do. Two colors at one number are a hard step: the pair keeps the
    order the file gives it, read from its cold or low side. The range is None when the
    numbers do not give one."""
    numbers = [n for n, _c in points]
    lo, hi = min(numbers), max(numbers)
    if not hi > lo:
        raise ImportProblem("All of its colors sit at the same number, so there is no range "
                            "to spread them over.")
    vmin, vmax, warning, is_values = _range_for(lo, hi, kind)
    span = hi - lo
    if not is_values and not numbers_are_values:
        stops = [((n - lo) / span, c) for n, c in points]                  # positions
        reverse = True
    elif kind in ("radar", "wind"):
        stops = [((n - lo) / span, c) for n, c in points]
        reverse = False
    else:
        stops = [((hi - n) / span, c) for n, c in points]                  # warm end first
        reverse = True
    if stops[0][0] > stops[-1][0]:
        stops.reverse()             # so a pair at one number keeps its order along the table
    stops.sort(key=lambda s: s[0])
    # A second color at the very end has no room after it (and is never seen).
    while len(stops) > 2 and stops[-1][0] == stops[-2][0] == 1.0:
        stops.pop()
    return stops, vmin, vmax, reverse, warning


def _hex(rgb):
    """0-1 floats -> "#rrggbb", each channel rounded to the nearest byte."""
    return to_hex(tuple(min(max(float(v), 0.0), 1.0) for v in rgb))


# ----------------------------------------------------------- the store's own JSON

def _store_entries(text):
    """The tables in a colortables.json (a list of them, or one), or None when the text is
    not one -- a JSON list of color strings is a plain list, read further on."""
    if not text.lstrip().startswith(("[", "{")):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    entries = data if isinstance(data, list) else [data]
    if not entries or not all(isinstance(e, dict) and "stops" in e for e in entries):
        return None
    return entries


_STORE_KINDS = {"kt": "wind", "dBZ": "radar"}


def _from_store(entries, base):
    drafts = []
    for entry in entries:
        entry = user_colortables.normalize(entry)
        name, warnings = _name_from(str(entry.get("name") or base), base)
        kind = _STORE_KINDS.get(entry["units"]) or ("wv" if entry["category"] == "Water Vapor" else "ir")
        draft = Draft(name=name, kind=kind, reversed=bool(entry.get("reversed", True)),
                      description=str(entry.get("description") or ""), hidden=entry["hidden"],
                      warnings=warnings)
        try:
            draft.stops = [(float(p), str(c)) for p, c in entry["stops"]]
            draft.vmin, draft.vmax = float(entry["vmin"]), float(entry["vmax"])
        except (KeyError, TypeError, ValueError):
            draft.problem = "Can't be read: its stops or its range are not all there."
        try:
            draft.levels = user_colortables._levels(entry)
        except ValueError as e:     # _levels says what is wrong, in words
            draft.problem = draft.problem or f"Can't be read: {_sentence(str(e))}"
        if entry["units"] not in ("C", "kt", "dBZ"):
            draft.problem = (f"Can't be read: it is measured in {entry['units']}, which {edition.app_name()} "
                             "does not know.")
        drafts.append(draft)
    return drafts


# ------------------------------------------------------------------- shared blocks

_DEF_LINE = re.compile(r"^\s*def\s+([A-Za-z_]\w*)\s*\(")
_BANNER_RE = re.compile(r"^\s*#\s*-{2,}\s*(.+?)\s*-{2,}\s*$")
_BANNER_KINDS = {"infrared": "ir", "water vapor": "wv", "winds": "wind", "wind": "wind",
                 "radar": "radar"}
_UNITS_COMMENT_RE = re.compile(r"^\s*#\s*units\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_DECORATOR_CATEGORY_RE = re.compile(r"category\s*=\s*['\"]([^'\"]+)['\"]")
_CATEGORY_KINDS = {"Temperature (IR)": "ir", "Water Vapor": "wv", "Radar (dBZ)": "radar",
                   "SAR Wind": "wind"}
_UNIT_KINDS = {"kt": "wind", "kts": "wind", "knots": "wind", "dbz": "radar"}


def _from_blocks(text, base, label):
    """One draft per ``def name(): ...`` block."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if _DEF_LINE.match(line)]
    heads = []
    for i in starts:            # decorators right above a def belong to its block
        j = i
        while j > 0 and lines[j - 1].lstrip().startswith("@"):
            j -= 1
        heads.append(j)
    banner = None
    stray = []
    for i in range(heads[0]):
        banner = _banner_kind(lines[i]) or banner
        if not _is_noise(lines[i]):
            stray.append(f"Line {i + 1} is outside every table and was not read: '{_snippet(lines[i])}'")
    drafts = []
    for b, (head, start) in enumerate(zip(heads, starts)):
        end = heads[b + 1] if b + 1 < len(heads) else len(lines)
        block = lines[head:end]
        drafts.append(_from_block(block, head, lines[start], banner, base))
        for line in block:      # a banner at the end of a block is for the ones after it
            banner = _banner_kind(line) or banner
    drafts[0].warnings[:0] = stray
    return drafts


def _banner_kind(line):
    m = _BANNER_RE.match(line)
    return _BANNER_KINDS.get(m.group(1).strip().lower()) if m else None


def _from_block(block, offset, def_line, banner, base):
    text = "\n".join(block)
    written = _DEF_LINE.match(def_line).group(1)
    name, warnings = _name_from(user_colortables._table_name(written), base)
    skipped = []
    try:
        fields = user_colortables.parse_shareable(text, skipped)
    except ValueError as e:
        return Draft(name=name, warnings=warnings, problem=f"Can't be read: {_sentence(str(e))}")
    in_docstring = _docstring_lines(block)
    for number, line in skipped:
        if number - 1 not in in_docstring and not _is_noise(line):
            warnings.append(_unread(offset + number, line))

    kind, hidden = banner or "ir", False
    decorator = next((line for line in block if line.lstrip().startswith("@")), "")
    m = _DECORATOR_CATEGORY_RE.search(decorator)
    if m and m.group(1) in _CATEGORY_KINDS:
        kind = _CATEGORY_KINDS[m.group(1)]
    if "category" in fields:
        if fields["category"] is None:
            kind, hidden = "ir", True
        elif fields["category"] in _CATEGORY_KINDS:
            kind = _CATEGORY_KINDS[fields["category"]]
        else:
            warnings.append(f"Its category '{fields['category']}' isn't one {edition.app_name()} knows, so it "
                            f"was read as {KIND_LABELS[kind].lower()}.")
    units = _UNITS_COMMENT_RE.search(text)
    if units:
        unit = units.group(1).strip().lower()
        if unit in _UNIT_KINDS:
            kind = _UNIT_KINDS[unit]
        elif unit not in ("c", "°c", "k"):
            warnings.append(f"Its units '{units.group(1)}' aren't ones {edition.app_name()} knows; it was read as "
                            f"{KIND_LABELS[kind].lower()}.")

    vmin = vmax = None
    if KIND_UNITS[kind] == "C":
        if "vmin_c" in fields and "vmax_c" in fields:
            vmin, vmax = fields["vmin_c"], fields["vmax_c"]
    else:
        # A wind or radar bound is the number as written (100 / 1.9438452 is 100 kt), never
        # a Kelvin temperature however large it is.
        bounds = {k: p.search(text) for k, p in user_colortables._BOUND_RE.items()}
        if all(bounds.values()):
            vmin, vmax = float(bounds["vmin_c"].group(1)), float(bounds["vmax_c"].group(1))
    return Draft(name=name, stops=fields["stops"], vmin=vmin, vmax=vmax, kind=kind,
                 reversed=fields.get("reversed_", True),
                 levels=fields.get("levels", user_colortables.DEFAULT_LEVELS),
                 description=fields.get("description", ""), hidden=hidden, warnings=warnings)


def _name_from(written, base):
    """(a valid name, [a warning if it had to change]) for a name found in the text."""
    try:
        return user_colortables.validate_name(written), []
    except ValueError:
        safe = _safe_name(written) or base
        return safe, [f"Its name '{written}' was changed to '{safe}': names use lowercase "
                      "letters, digits and _."]


# ------------------------------------------------------------------- GRLevelX .pal

def _looks_pal(text):
    return len(re.findall(r"^\s*(?:solid)?color4?\s*:\s*-?\d", text, re.IGNORECASE | re.MULTILINE)) >= 2


def _from_pal(text, base, label):
    problems = []
    anchors, units = palfile.parse_pal(text, problems)
    warnings = [_unread(n, line) for n, line in problems]
    if len(anchors) < 2:
        raise ImportProblem(f"{_cap(label)} has fewer than two COLOR lines {edition.app_name()} can read, so "
                            "there is no table in it.")
    if re.search(r"^\s*(?:solid)?color4\s*:", text, re.IGNORECASE | re.MULTILINE):
        warnings.append(f"Some of its colors are partly see-through; {edition.app_name()} draws every color "
                        "solid.")
    if any(not 0 <= v <= 255 for _value, rgb in anchors for v in rgb):
        warnings.append("Some of its color numbers are outside 0 to 255 and were cut to fit.")
    if units and units.strip().lower() != "dbz":
        warnings.append(f"Its units are {units.strip()}, not dBZ; it was read as a radar table anyway.")
    points = [(value, _hex(tuple(v / 255 for v in rgb))) for value, rgb in anchors]
    stops, vmin, vmax, reverse, _warning = _placed(points, "radar", True)
    description = f"Imported from {label}" + (f" ({units.strip()})" if units else "")
    return Draft(name=base, stops=stops, vmin=vmin, vmax=vmax, kind="radar", reversed=reverse,
                 description=description, warnings=warnings)


def _cap(text):
    return text[:1].upper() + text[1:]


# ---------------------------------------------------------------------- MetPy .tbl

def _from_tbl(text, base, label):
    from matplotlib.colors import ListedColormap

    from . import colorize, colortable_export
    problems = []
    colors = palfile.parse_tbl(text, problems)
    warnings = [_unread(n, line, why) for n, line, why in problems]
    if len(colors) < 2:
        raise ImportProblem(f"{_cap(label)} has {'no colors' if not colors else 'only one color'} "
                            f"{edition.app_name()} can read; a table needs at least two.")
    # The bytes a MetPy table draws as (matplotlib truncates: 0.070588 is 17, not 18), so
    # a vendored table comes back exactly as colormaps draws it.
    rows = colorize.table(ListedColormap(colors))[:len(colors), :3]
    kind = _kind_hint(base) or "ir"
    vmin, vmax = DEFAULT_RANGES["C"]
    stops, levels = colortable_export.lut_stops(rows, vmax, vmin)
    warnings.append(f"A .tbl file has no range of its own, so it was given {_number(vmin)} to "
                    f"{_number(vmax)} °C, " + ("the usual range of an infrared table." if edition.is_editor()
                                               else "the range tcviz draws its MetPy tables at."))
    return Draft(name=base, stops=stops, vmin=vmin, vmax=vmax, kind=kind, reversed=True,
                 levels=levels, description=f"Imported from {label}", warnings=warnings)


_HINTS = (("radar", re.compile(r"(?<![a-z])dbz(?![a-z])|reflectivity")),
          ("wind", re.compile(r"(?<![a-z])(knots?|kts?|winds?)(?![a-z])")),
          ("wv", re.compile(r"(?<![a-z])wv(?![a-z])|water.?vapou?r")))


def _kind_hint(text):
    """The kind a name or a comment points to, or None."""
    low = text.lower()
    return next((kind for kind, pattern in _HINTS if pattern.search(low)), None)


# ------------------------------------------------------------------------ GMT .cpt

_CPT_MODEL_RE = re.compile(r"^\s*#\s*COLOR_MODEL\s*=\s*\+?\s*(\w+)", re.IGNORECASE)
_CPT_BFN_RE = re.compile(r"^\s*[BFN](\s|$)")
_NUMBER_RE = re.compile(r"^[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$")


def _looks_cpt(text):
    """A COLOR_MODEL comment, a B/F/N line, or slices: ``z color z color``."""
    slices = data = 0
    for line in text.splitlines():
        if _CPT_MODEL_RE.match(line) or _CPT_BFN_RE.match(line):
            return True
        tokens = line.split(";", 1)[0].split()
        if not tokens or tokens[0].startswith(("#", "//")):
            continue
        data += 1
        if tokens[-1] in ("L", "U", "B"):
            tokens = tokens[:-1]
        numeric = [bool(_NUMBER_RE.match(t)) for t in tokens]
        # z r g b z r g b, or z color z color with each color one word (r/g/b, #hex, a
        # name): "0.5 255 0 0", a value and an r g b, is a plain list, not a slice
        if (len(tokens) == 8 and all(numeric) and float(tokens[4]) >= float(tokens[0])) or \
                (len(tokens) == 4 and numeric == [True, False, True, False]):
            slices += 1
    return data > 0 and slices * 2 > data


class _NotRead(ValueError):
    """A line (or a part of one) that is not what it should be; the message says why
    (or there is none, when the line itself says it best)."""


def _why(error):
    return error.args[0] if error.args and error.args[0] else None


def _from_cpt(text, base, label):
    model, warnings, points = "rgb", [], []
    comments = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = _CPT_MODEL_RE.match(line)
            if m:
                model = m.group(1).lower()
            comments.append(line)
            continue
        if _CPT_BFN_RE.match(line):
            continue    # background, foreground and no-data colors: tcviz draws its own
        try:
            z0, c0, z1, c1 = _cpt_slice(line, model)
        except _NotRead as e:
            warnings.append(_unread(number, line, _why(e)))
            continue
        if not points or points[-1] != (z0, c0):
            points.append((z0, c0))     # at the same z as the last one: a hard step
        if (z1, c1) != (z0, c0):
            points.append((z1, c1))
    if len(points) < 2:
        raise _nothing("No color slices were found in", label, warnings)
    if model == "hsv":
        warnings.append(f"It blends its colors by hue, saturation and value; {edition.app_name()} blends them "
                        "as red, green and blue, so the shades between its stops can differ a little.")
    zs = [z for z, _c in points]
    if any(b < a for a, b in zip(zs, zs[1:])):
        warnings.append("Its slices are not in order of value; they were put in order.")
    kind = _kind_hint(base + " " + " ".join(comments)) or "ir"
    stops, vmin, vmax, reverse, warning = _placed(points, kind, True)   # vmin None: no range
    if warning:
        warnings.append(warning)
    return Draft(name=base, stops=stops, vmin=vmin, vmax=vmax, kind=kind, reversed=reverse,
                 description=f"Imported from {label}" if label != "the pasted text" else "",
                 warnings=warnings)


def _nothing(what, label, warnings):
    """The ImportProblem for a source with too little in it, naming the first line that
    could not be read."""
    first = f" ({warnings[0][:1].lower()}{warnings[0][1:]})" if warnings else ""
    return ImportProblem(f"{what} {label}{first}.")


def _cpt_slice(line, model):
    tokens = line.split(";", 1)[0].split()       # ";label" is the slice's legend label
    if tokens and tokens[-1] in ("L", "U", "B"):
        tokens = tokens[:-1]                      # which side gets a label: nothing to draw
    width = 4 if model == "cmyk" else 3
    if len(tokens) == 2 + 2 * width:
        z0, c0, z1, c1 = tokens[0], tokens[1:1 + width], tokens[1 + width], tokens[2 + width:]
    elif len(tokens) == 4:
        z0, c0, z1, c1 = tokens[0], [tokens[1]], tokens[2], [tokens[3]]
    elif len(tokens) == 2:
        z0, c0, z1, c1 = tokens[0], [tokens[1]], tokens[0], [tokens[1]]   # one color at one value
    else:
        raise _NotRead("it doesn't read as 'value color value color'")
    z0, z1 = _cpt_number(z0), _cpt_number(z1)
    if z1 < z0:
        raise _NotRead("its second value is below its first")
    return z0, _cpt_color(c0, model), z1, _cpt_color(c1, model)


def _cpt_number(token):
    if not _NUMBER_RE.match(token):
        raise _NotRead(f"'{token}' isn't a number")
    value = float(token)
    if not math.isfinite(value):
        raise _NotRead(f"'{token}' isn't a number")
    return value


def _cpt_color(tokens, model):
    """One color, as "#rrggbb"."""
    if len(tokens) == 1:
        token = tokens[0]
        if re.match(r"^[pP]\d", token):
            raise _NotRead("a fill pattern can't be drawn")
        if token.startswith("#"):
            return _named(token)
        if "/" in token:
            return _cpt_numbers(token.split("/"), model)
        if re.fullmatch(r"\d+(\.\d+)?-\d+(\.\d+)?-\d+(\.\d+)?", token):
            return _cpt_numbers(token.split("-"), "hsv")     # h-s-v is always hue, saturation, value
        if _NUMBER_RE.match(token):
            gray = float(token)
            if not 0 <= gray <= 255:
                raise _NotRead("a gray level must be from 0 to 255")
            return _hex((gray / 255,) * 3)
        return _named(token)
    return _cpt_numbers(tokens, model)


def _cpt_numbers(parts, model):
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise _NotRead(f"'{'/'.join(parts)}' isn't a color") from None
    if len(nums) == 4:           # c/m/y/k, in percent
        c, m, y, k = (min(max(v, 0.0), 100.0) / 100 for v in nums)
        return _hex(((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)))
    if len(nums) != 3:
        raise _NotRead(f"'{'/'.join(parts)}' isn't a color")
    if model == "hsv" and 0 <= nums[1] <= 1 and 0 <= nums[2] <= 1:
        return _hex(colorsys.hsv_to_rgb((nums[0] % 360) / 360, nums[1], nums[2]))
    if not all(0 <= v <= 255 for v in nums):
        raise _NotRead("color numbers must be from 0 to 255")
    return _hex(tuple(v / 255 for v in nums))


def _named(text):
    """A color name or #hex as "#rrggbb" (matplotlib's names: CSS, tab:, xkcd:)."""
    for candidate in (text, text.replace(" ", ""), f"xkcd:{text}"):
        try:
            return _hex(to_rgb(candidate))
        except ValueError:
            continue
    raise _NotRead(f"'{text}' isn't a color {edition.app_name()} knows")


# ------------------------------------------------------------------ lists and CSV

# The bot's palettes folder takes these at the top of a .txt stop list.
_META_RE = re.compile(r"^\s*#\s*(range|description|reversed|units)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_TRUTHY = {"yes", "true", "1", "on"}
_HEADER_WORDS = {"value", "values", "position", "pos", "r", "g", "b", "a", "red", "green", "blue",
                 "alpha", "color", "colour", "hex", "rgb", "temperature", "temp", "level", "index",
                 "dbz", "kt", "knots", "name", "label"}
_RGB_FUNC_RE = re.compile(r"rgba?\s*\(([^)]*)\)", re.IGNORECASE)
_HEX_ANY_RE = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z])")
_FRACTION_RE = re.compile(r"^([-+]?\d+(?:\.\d+)?)\s*/\s*([-+]?\d+(?:\.\d+)?)$")


def _from_list(text, base, label):
    meta, body = {}, []
    for line in text.splitlines():
        m = _META_RE.match(line)
        if m:
            meta[m.group(1).lower()] = m.group(2)
            body.append("")             # kept, so line numbers in warnings stay right
        else:
            body.append(line)
    comments = " ".join(line for line in body if line.lstrip().startswith(("#", "//"))
                        and not _HEX_ANY_RE.match(line.strip()))
    kind = _UNIT_KINDS.get(meta.get("units", "").strip().lower()) or \
        _kind_hint(base + " " + comments) or "ir"
    warnings = []
    vmin = vmax = None
    if "range" in meta:
        try:
            vmin, vmax = (float(p) for p in re.split(r"[\s,]+", meta["range"].strip()))
        except ValueError:
            warnings.append(f"Its range line '{meta['range']}' needs two numbers, the cold end "
                            "and then the warm end; it was not used.")
    reverse = meta.get("reversed", "yes").strip().lower() in _TRUTHY
    description = meta.get("description", "")

    draft = _from_stop_list("\n".join(body), base, kind, warnings)
    if draft is None:
        draft = _from_plain(body, base, label, kind, warnings)
    else:
        draft.reversed = reverse
    if vmin is not None:
        if not vmax > vmin:
            draft.warnings.append("Its range line gives the warm end first; it was not used.")
        elif draft.vmin is None:
            draft.vmin, draft.vmax = vmin, vmax
        else:
            draft.warnings.append("Its range line was not used: the numbers beside its colors "
                                  "give the range.")
    draft.description = description or draft.description
    return draft


def _from_stop_list(body, base, kind, warnings):
    """A Draft when the text is a stop list user_colortables.parse_stops reads in full
    (every color with a position before it, from 0 to 1 or 0 to N), else None."""
    skipped = []
    try:
        stops = user_colortables.parse_stops(body, skipped)
    except ValueError:
        return None
    positions = [p for p, _c in stops]
    if min(positions) != 0.0 or max(positions) != 1.0:
        return None
    unread = []
    for number, line in skipped:
        if _is_noise(line):
            continue
        try:
            _plain_line(line)
        except _NotRead:
            unread.append(_unread(number, line))
            continue
        return None             # a color written another way: the plain reader takes it all
    return Draft(name=base, stops=stops, kind=kind, warnings=warnings + unread)


def _from_plain(body, base, label, kind, warnings):
    entries = []        # (line number, position or None, ("hex", "#..") or ("raw", (a, b, c)))
    header_seen = False
    for number, raw in enumerate(body, 1):
        line = raw.strip()
        if not line or _is_comment(line):
            continue
        try:
            several = _several_colors(line)
            if several:
                entries += [(number, None, ("hex", color)) for color in several]
            else:
                entries.append((number, *_plain_line(line)))
        except _NotRead as e:
            if not header_seen and not entries and _is_header(line):
                header_seen = True      # a CSV's column names
                continue
            warnings.append(_unread(number, line, _why(e)))
    if not entries:
        raise _nothing("No colors were found in", label, warnings)
    if len(entries) < 2:
        raise ImportProblem(f"Only one color was found in {label}; a table needs at least two.")

    # r, g, b numbers are 0-255 if any of them is above 1, else 0-1: decided for the whole
    # list, so a 0-255 list with a near-black (0, 0, 1) row is not read as 0-1 there.
    raws = [c[1] for _n, _p, c in entries if c[0] == "raw"]
    scale = 255.0 if any(v > 1 for rgb in raws for v in rgb) else 1.0
    colors = []
    kept = []
    for number, position, (form, value) in entries:
        if form == "raw":
            if not all(0 <= v <= scale for v in value):
                warnings.append(_unread(number, body[number - 1],
                                        f"color numbers must be from 0 to {_number(scale)}"))
                continue
            value = _hex(tuple(v / scale for v in value))
        colors.append(value)
        kept.append((number, position))
    if len(colors) < 2:
        raise _nothing("Fewer than two colors could be read in", label, warnings)

    with_position = [p is not None for _n, p in kept]
    if all(with_position):
        points = [(p, c) for (_n, p), c in zip(kept, colors)]
        stops, vmin, vmax, reverse, warning = _placed(points, kind, False)
        if warning:
            warnings.append(warning)
        return Draft(name=base, stops=stops, vmin=vmin, vmax=vmax, kind=kind, reversed=reverse,
                     warnings=warnings)
    if any(with_position):
        warnings.append("Some lines give a position and some don't, so the positions were left "
                        "out and the colors spread evenly.")
    n = len(colors)
    if n > user_colortables.DEFAULT_LEVELS:
        # More colors than a table's usual 256 levels: a lookup table, one level per color.
        # Its stops are positions, so they hold whatever range it is given later.
        from . import colortable_export
        rows = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in colors])
        stops, levels = colortable_export.lut_stops(rows)
        return Draft(name=base, stops=stops, kind=kind, levels=levels, warnings=warnings)
    stops = [(i / (n - 1), c) for i, c in enumerate(colors)]
    return Draft(name=base, stops=stops, kind=kind, warnings=warnings)


def _several_colors(line):
    """The colors of a line that holds nothing else -- "#ff0000, #00ff00", a Python list of
    them, rgb() after rgb(), or color names between commas -- else None."""
    for pattern in (_HEX_ANY_RE, _RGB_FUNC_RE):
        found = list(pattern.finditer(line))
        if len(found) >= 2 and _PUNCTUATION_RE.match(pattern.sub("", line)):
            return [_plain_line(m.group(0))[1][1] for m in found]
    parts = [p.strip(" \t'\"[](){}") for p in line.split(",")]
    parts = [p for p in parts if p]
    if len(parts) >= 2 and not any(_NUMBER_RE.match(p) or _FRACTION_RE.match(p) for p in parts):
        try:
            return [_named(p) for p in parts]
        except _NotRead:
            return None
    return None


def _is_comment(line):
    """A comment line: //, ;, or # followed by something that is not a hex color."""
    if line.startswith(("//", ";")):
        return True
    return line.startswith("#") and not _HEX_ANY_RE.match(line)


def _is_header(line):
    words = [w for w in re.split(r"[\s,;\t]+", line.strip().lower()) if w]
    return len(words) >= 2 and all(re.fullmatch(r"[a-z_()%]+", w) for w in words) and \
        any(w.strip("()%") in _HEADER_WORDS for w in words)


def _plain_line(line):
    """(position or None, ("hex", "#rrggbb") or ("raw", (a, b, c))) for one line of a plain
    list; _NotRead when it holds no color."""
    m = _RGB_FUNC_RE.search(line)
    if m:
        parts = [p for p in re.split(r"[\s,/]+", m.group(1).strip()) if p]
        if len(parts) < 3:
            raise _NotRead("rgb() needs three numbers")
        rgb = []
        for p in parts[:3]:
            try:
                rgb.append(float(p[:-1]) / 100 if p.endswith("%") else float(p) / 255)
            except ValueError:
                raise _NotRead(f"'{p}' isn't a number") from None
        if not all(0 <= v <= 1 for v in rgb):
            raise _NotRead("rgb() numbers must be from 0 to 255")
        return _position(line[:m.start()]), ("hex", _hex(rgb))
    m = _HEX_ANY_RE.search(line)
    if m:
        return _position(line[:m.start()]), ("hex", _hex(to_rgb(m.group(0))))
    tokens = [t for t in re.split(r"[\s,;]+", re.sub(r"[()\[\]{}'\"]", " ", line)) if t]
    numbers = []
    while tokens and (_NUMBER_RE.match(tokens[0]) or _FRACTION_RE.match(tokens[0])):
        numbers.append(tokens.pop(0))
    if not tokens:
        if len(numbers) in (3, 4):
            if any(_FRACTION_RE.match(t) for t in numbers[-3:]):
                raise _NotRead("red, green and blue must be plain numbers, not fractions")
            rgb = tuple(float(t) for t in numbers[-3:])
            return (_position(numbers[0]) if len(numbers) == 4 else None), ("raw", rgb)
        raise _NotRead(f"it has {len(numbers)} number{'s' if len(numbers) != 1 else ''}; a color "
                       "needs three (red, green, blue)")
    if len(numbers) > 1:
        raise _NotRead(None)
    name = " ".join(tokens)
    try:
        color = _named(name)
    except _NotRead:
        if numbers:
            raise
        raise _NotRead(None) from None     # the line is the name: saying so again adds nothing
    return (_position(numbers[0]) if numbers else None), ("hex", color)


def _position(text):
    """The number before a color: None when there is none."""
    text = re.sub(r"[()\[\]{}'\",;:=\s]+", " ", text).strip()
    if not text:
        return None
    m = _FRACTION_RE.match(text.replace(" ", ""))
    if m:
        num, den = float(m.group(1)), float(m.group(2))
        if not den:
            raise _NotRead("its position divides by zero")
        return num / den
    if _NUMBER_RE.match(text):
        value = float(text)
        if math.isfinite(value):
            return value
    raise _NotRead(f"'{text}' before the color isn't a position")


