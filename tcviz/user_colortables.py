"""Persistent user-defined colortables, addable from the GUI (or by hand).

The @colortable decorator in colormaps.py makes adding a colortable *in code* one step;
this module makes it a runtime, no-code step: colortables the user creates in the GUI
"Add colortable" dialog are stored here as JSON and registered into colormaps at import,
so they become first-class palettes (GUI picker, --palette, colormaps.get()) exactly
like the built-in curves, and persist across sessions.

Kept deliberately free of any tcviz.colormaps import so colormaps can import THIS at load
time without a cycle -- the only heavy dependency is matplotlib, for building the cmap.

The stored schema only ever gains keys (see normalize), so a file written by an older
app, or copied into the bot's palettes folder, reads the same as it always did.
"""
import contextlib
import json
import keyword
import math
import re
import shutil
import stat
import tempfile
import time
from collections import namedtuple
from pathlib import Path

import numpy as np
from matplotlib.colors import LinearSegmentedColormap, to_rgb

# ~/.config/tcviz/colortables.json -- tcviz's own, a plain user config file; overridable via
# TCVIZ_COLORTABLES_PATH for tests. Colortable Editor keeps its tables in a folder of its own
# instead (store_path).
import os

STORE_PATH = Path(os.environ.get("TCVIZ_COLORTABLES_PATH",
                                 Path.home() / ".config" / "tcviz" / "colortables.json"))
STORE_NAME = "colortables.json"


def store_path():
    """The tables file in use: STORE_PATH in tcviz, exactly as it has always been, and
    colortables.json in Colortable Editor's own folder (tcviz.edition.editor_config_dir)
    there. Looked up on every read and write, so the edition decides, not the import."""
    from . import edition
    if edition.is_editor():
        return edition.editor_config_dir() / STORE_NAME
    return STORE_PATH

# None is the legacy "Hidden (CLI only)" spelling: still accepted (the bot's palettes folder
# and old files carry it) and read by normalize as a hidden IR table. These are the
# categories of a table in degrees C; a winds or radar table's category comes with its
# units (UNIT_CATEGORIES).
VALID_CATEGORIES = ("Temperature (IR)", "Water Vapor", None)

# The keys normalize fills in. A table is in degrees C (infrared and water vapor), knots
# (winds) or dBZ (radar reflectivity); build_cmap refuses any other unit rather than
# drawing it on a scale it was never made for. 256 levels is matplotlib's own default for
# from_list, so every table saved before levels existed keeps exactly the colors it had.
DEFAULT_UNITS = "C"
KNOWN_UNITS = ("C", "kt", "dBZ")
DEFAULT_LEVELS = 256
# A winds or radar table belongs to the picker group its built-in tables are in, so
# products.categorize_palettes and an opened file's unit list (localfile.palettes_for_units) find it unchanged.
UNIT_CATEGORIES = {"kt": "SAR Wind", "dBZ": "Radar (dBZ)"}
# The range a new table starts with, and an imported one without a range of its own gets:
# the IR scale every curve here is drawn at, 0-100 kt (the SAR wind table's top is 100.8 kt), and
# -10 to 70 dBZ, from drizzle to the strongest eyewall echo GPM's radar sees.
DEFAULT_RANGES = {"C": (-100.0, 50.0), "kt": (0.0, 100.0), "dBZ": (-10.0, 70.0)}
# Every wind field tcviz draws is stored in m/s, and a picture's knots are only its labels
# (render._colorbar_ticks), so a table written in knots is drawn at its knots / KT_PER_MS --
# the way tcviz's scatterometer wind table puts its 5-knot steps in m/s. The same number as
# colormaps._KT_PER_MS and render._KT_PER_MS (tests/test_wind_radar_tables.py holds the
# three together); not imported from either, since colormaps imports this module.
KT_PER_MS = 1.9438452


class NameTaken(ValueError):
    """add() was asked to save under the name of a table already in the store, without
    replace=True. The dialog catches this to ask before replacing it."""

    def __init__(self, name):
        super().__init__(f"There is already a table called '{name}'.")
        self.name = name


class NameReserved(ValueError):
    """add() was asked to save a NEW table under a name tcviz itself uses. Saved, it would
    silently stand in for that built-in table (or look) everywhere it is drawn."""

    def __init__(self, name):
        super().__init__("That name belongs to a built-in table; pick another.")
        self.name = name


def normalize(entry):
    """`entry` with every optional key filled in, as a new dict (the one given is untouched).

    The file only ever gains keys, so a table written by an older app still reads: `units`
    defaults to "C" (the only kind there was), `vmin`/`vmax` to the Celsius `vmin_c`/`vmax_c`
    every entry carries (and the other way round for a Celsius table written with only the
    new pair), `levels` to 256 and `hidden` to False. The one legacy value with a meaning of
    its own is `category: null`, which the dialog wrote for "Hidden (CLI only)": it reads as
    an IR table kept out of the pickers.

    A winds ("kt") or radar ("dBZ") table carries only `vmin`/`vmax`, in its own units: it
    has no Celsius pair, which is what keeps an older app (and the bot's palettes folder,
    which checks for vmin_c/vmax_c) from drawing it as an infrared table. Its category is
    always its kind's (UNIT_CATEGORIES), whatever the entry says.

    Nothing is written back -- a table takes the new keys on disk only when it is next saved.
    """
    out = dict(entry)
    out.setdefault("units", DEFAULT_UNITS)
    if out["units"] == "C":
        for new, old in (("vmin", "vmin_c"), ("vmax", "vmax_c")):
            if new not in out and old in out:
                out[new] = out[old]
            elif old not in out and new in out:
                out[old] = out[new]
    out.setdefault("levels", DEFAULT_LEVELS)
    legacy_hidden = "category" in out and out["category"] is None
    if out["units"] in UNIT_CATEGORIES:
        out["category"] = UNIT_CATEGORIES[out["units"]]
    elif out.get("category") is None:
        out["category"] = "Temperature (IR)"
    out["hidden"] = bool(out.get("hidden", False)) or legacy_hidden
    return out


def drawing_range(units, vmin, vmax):
    """(vmax, vmin) as the engine draws a table whose range is vmin..vmax in `units`: Kelvin
    for degrees C, m/s for knots (every wind field is stored in m/s), dBZ as they are."""
    if units == "C":
        return vmax + 273.15, vmin + 273.15
    if units == "kt":
        return vmax / KT_PER_MS, vmin / KT_PER_MS
    if units == "dBZ":
        return vmax, vmin
    raise ValueError(f"it is measured in {units}, which this version of {_app_name()} cannot draw")

# Signed like _FLOAT_RE: without the sign "-10/130" read as +10/130, so a stop typed below
# the cold end moved inside it instead of being refused as "-0.077" is.
_FRAC_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*/\s*([-+]?\d+(?:\.\d+)?)")
_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
_FLOAT_RE = re.compile(r"[-+]?\d*\.?\d+")


def parse_stops(text, skipped=None):
    """Parse a pasted stop list into [(position_0_to_1, "#hex"), ...].

    Forgiving of the exact style used to author the built-in curves -- one stop per line,
    each with a position (either an ``a/b`` fraction like ``60/130`` or a plain number)
    and a ``#rrggbb`` color, with surrounding ``( )``, quotes, commas, and trailing
    ``#comment`` labels all ignored (the color is read as the first #-hex on the line, so
    a trailing ``#VWMG``-style comment is safely skipped). If any position exceeds 1 the
    whole set is normalized by its max, so raw 0..N values (not pre-divided) also work.
    Raises ValueError with a clear message on anything unusable.

    A line with no #-hex color is passed over: a stray bracket, a comment, the def and
    return lines of a shared block. So that nothing is dropped unseen, `skipped`, when it
    is a list, gets (line number, line) for every such line that is not blank; the
    importer (colortable_import) tells apart the ones that are only punctuation or code
    and reports the rest."""
    stops = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        color_m = _HEX_RE.search(line)
        if not color_m:
            if skipped is not None:
                skipped.append((number, line))
            continue  # a line with no color (a stray bracket, a comment) -> skip
        color = color_m.group(0)
        before = line[:color_m.start()]  # the position always precedes the color
        frac = _FRAC_RE.search(before)
        if frac:
            num, den = float(frac.group(1)), float(frac.group(2))
            pos = num / den if den else 0.0
        else:
            fm = _FLOAT_RE.search(before)
            if not fm:
                raise ValueError(f"no position number found before the color on line: {line!r}")
            pos = float(fm.group(0))
        stops.append((pos, color))

    if len(stops) < 2:
        raise ValueError("need at least 2 color stops")
    positions = [p for p, _ in stops]
    if not all(math.isfinite(p) for p in positions):
        # a number of 309+ digits reads as inf, and inf/inf is a NaN that every range
        # check below would wave through
        raise ValueError("a stop position is too large a number to use")
    hi = max(positions)
    if hi > 1.0:  # raw 0..N values -> normalize to 0..1
        stops = [(p / hi, c) for p, c in stops]
    if min(p for p, _ in stops) < 0.0 or max(p for p, _ in stops) > 1.0:
        raise ValueError("stop positions must be within 0..1 (or a consistent 0..N range)")
    for _, c in stops:
        to_rgb(c)  # validates every color, raising ValueError on a bad one
    return stops


def _fix_increasing(stops):
    """Sort by position and nudge exact/decreasing duplicates by an epsilon so
    LinearSegmentedColormap accepts them (a repeated position = a hard color step) --
    mirrors colormaps._fix_duplicate_stops."""
    eps = 1e-6
    out, prev = [], -1.0
    for pos, color in sorted(stops, key=lambda s: s[0]):
        if pos <= prev:
            pos = prev + eps
        out.append((pos, to_rgb(color)))
        prev = pos
    return out


def build_cmap(entry):
    """(cmap, vmax, vmin) for a stored colortable entry -- the same tuple every colormaps
    builder returns, so it plugs straight into the @colortable registry. The range is in
    the units the engine draws in (drawing_range): Kelvin for an infrared or water-vapor
    table, m/s for a winds table written in knots, dBZ for a radar table.

    Raises ValueError for stops that cannot be drawn. matplotlib only looks at them the
    first time the colormap is USED, so without this check a table whose stops stop short
    of 0 or 1 built "fine" -- add()'s sanity check passed it, and so did the bot's palettes
    folder -- and failed with its first picture."""
    entry = normalize(entry)
    if entry["units"] not in KNOWN_UNITS:
        raise ValueError(f"it is measured in {entry['units']}, which this version of {_app_name()} "
                         "cannot draw yet")
    levels = _levels(entry)
    positions = [float(p) for p, _c in entry["stops"]]
    if len(positions) < 2:
        raise ValueError("need at least 2 color stops")
    if not all(math.isfinite(p) for p in positions):
        raise ValueError("a stop position is not a number")
    stops = _fix_increasing(entry["stops"])
    # matplotlib's own test, on the positions it will be given
    if stops[0][0] != 0.0 or stops[-1][0] != 1.0:
        lo, hi = min(positions), max(positions)
        if (lo, hi) == (0.0, 1.0):
            # the nudge that separates a repeated position has no room past the far end
            raise ValueError("stop positions repeat at (or just below) 1, where there is no "
                             "room to separate them")
        raise ValueError(f"the stops must run from 0 to 1; these run from {lo:.6g} to {hi:.6g}")
    cmap = LinearSegmentedColormap.from_list("", stops, N=levels)
    if entry.get("reversed", True):
        cmap = cmap.reversed()
    vmax, vmin = drawing_range(entry["units"], entry["vmin"], entry["vmax"])
    return cmap, vmax, vmin


# How many colors a table is drawn with. 256, matplotlib's own default, unless the table
# says otherwise: one exported from a 57-color MetPy table keeps its 57 (colortable_export).
_MAX_LEVELS = 1 << 16


def _levels(entry):
    levels = entry.get("levels", DEFAULT_LEVELS)
    if isinstance(levels, bool) or not isinstance(levels, int) or not 2 <= levels <= _MAX_LEVELS:
        raise ValueError(f"its number of color levels must be a whole number from 2 to {_MAX_LEVELS}, "
                         f"not {levels!r}")
    return levels


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_name(name):
    if not _NAME_RE.match(name or ""):
        raise ValueError("name must be lowercase letters/digits/underscores and start with a letter")
    return name


# ------------------------------------------------------------------ reading and writing

# A damaged store used to read as "no tables", and the next save then wrote a file holding
# only the new one -- every other table gone. So every save is atomic and leaves the
# version before it as colortables.json.bak, and a store that no longer reads is copied
# aside and replaced from that backup instead of being quietly treated as empty.
LoadReport = namedtuple("LoadReport", "entries problems damaged_copy")
LoadReport.__doc__ = """What load_report found: `entries`, the stored tables (unchecked: each
may still fail to build); `problems`, plain-words sentences for the person using the app
about anything wrong with the file itself; `damaged_copy`, the Path a damaged file was
copied to, or None."""


def _backup_path():
    store = store_path()
    return store.with_name(store.name + ".bak")


def _damaged_path():
    """colortables.damaged-<when>.json beside the store, never one that already exists."""
    store = store_path()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = store.with_name(f"{store.stem}.damaged-{stamp}{store.suffix}")
    number = 1
    while path.exists():
        number += 1
        path = store.with_name(f"{store.stem}.damaged-{stamp}-{number}{store.suffix}")
    return path


def _read_list(path):
    """(text, tables) stored at `path`. FileNotFoundError when there is no file, another
    OSError when it cannot be read, ValueError when what is there is not a list of tables.

    A byte-order mark at the start is read past: Windows' Notepad has written one into
    every UTF-8 file it saved, and the store would otherwise read as damaged."""
    text = path.read_text(encoding="utf-8-sig")
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("the file does not hold a list of tables")
    return text, data


# Windows refuses to replace a file another program has open (an editor, a virus scanner
# looking at the file just written, a sync client), and most of those let go within a moment:
# os.replace is tried this many times, this far apart, before the PermissionError stands.
_REPLACE_TRIES = 10
_REPLACE_WAIT_S = 0.05


def _atomic_write_text(path, text):
    """Replace `path` with `text` so that it is always either the old file or the new one.

    The text goes to a temporary file in the same folder (so the rename never crosses
    disks), is forced onto the disk, and only then renamed over `path`. If anything fails
    before the rename, `path` is untouched and the temporary file is removed.

    Written with "\\n" line ends on every system, so a tables file reads the same copied
    between Linux and Windows. On Windows a file another program holds open cannot be
    replaced: the rename is tried again for half a second, then the PermissionError is
    raised for the caller to explain (plain_save_error).
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            _keep_mode(f.fileno(), path)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    # the rename itself lives in the folder, so the folder is flushed too (best effort:
    # not every filesystem allows opening a folder for this, and Windows allows none)
    if os.name == "posix":
        with contextlib.suppress(OSError):
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)


def _keep_mode(fd, path):
    """mkstemp makes the file private (0600); keep what the file being replaced had, or the
    usual 0644 for a new one. Only where file modes mean that: os.fchmod is missing on
    Windows before Python 3.13 and only sets a read-only flag after."""
    if os.name != "posix" or not hasattr(os, "fchmod"):
        return
    try:
        os.fchmod(fd, stat.S_IMODE(path.stat().st_mode))
    except FileNotFoundError:
        os.fchmod(fd, 0o644)


def _replace(tmp, path):
    """os.replace, tried again for a moment on Windows while another program has `path`
    open (see _REPLACE_TRIES)."""
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if os.name != "nt" or attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(_REPLACE_WAIT_S)


def plain_save_error(error, what="Your color tables file"):
    """A sentence for the person using the app about an OSError from saving `what`.

    A PermissionError is the common one on Windows -- the file is open in another program,
    or the folder cannot be written to -- and its own words ("[WinError 5] Access is
    denied") say neither what happened nor what to do."""
    if isinstance(error, PermissionError):
        return (f"{what} could not be saved: it is open in another program, or this folder can't be "
                "written to. Close any program that has it open and try again. Nothing was changed.")
    return f"{what} could not be saved ({error.strerror or error}). Nothing was changed."


def _restore_damaged():
    """The store no longer reads: keep a copy of it, then put the backup in its place (or,
    with no usable backup, take the damaged store away). Returns a LoadReport."""
    try:
        backup_text, backup = _read_list(_backup_path())
    except (OSError, ValueError):
        backup_text, backup = None, None
    for_now = (" The tables in its backup are loaded for now." if backup is not None
               else " Your own tables are not loaded.")
    store = store_path()
    damaged = _damaged_path()
    try:
        # a copy, not a rename: the store itself is only ever replaced atomically
        shutil.copy2(store, damaged)
    except OSError as e:
        return LoadReport(backup or [], [f"Your color tables file ({store}) is damaged and a copy "
                                         f"of it could not be made ({e.strerror or e}), so it was left "
                                         f"as it is.{for_now}"], None)
    try:
        if backup is not None:
            _atomic_write_text(store, backup_text)
        else:
            store.unlink()
    except OSError as e:
        return LoadReport(backup or [], [f"Your color tables file ({store}) is damaged and could not "
                                         f"be repaired ({e.strerror or e}).{for_now} A copy of the "
                                         f"damaged file was kept as {damaged}."], damaged)
    if backup is not None:
        problem = (f"Your color tables file was damaged, so {_app_name()} put back its backup from before "
                   "your last change. Your most recent change to a table may be missing. The "
                   f"damaged file was kept as {damaged}.")
    else:
        problem = ("Your color tables file was damaged and there was no backup of it, so your own "
                   f"tables could not be loaded. The damaged file was kept as {damaged}.")
    return LoadReport(backup or [], [problem], damaged)


def load_report():
    """Every stored table, and what went wrong reading the file, as a LoadReport.

    A store that cannot be parsed is copied to colortables.damaged-<when>.json (kept, never
    overwritten) and replaced by the backup the last save left behind, so the next launch
    starts clean and the next save cannot wipe out every other table. With no usable
    backup the damaged store is moved aside instead and there are no tables to load."""
    store = store_path()
    try:
        return LoadReport(_read_list(store)[1], [], None)
    except FileNotFoundError:
        return LoadReport([], [], None)
    except ValueError:
        return _restore_damaged()
    except OSError as e:
        return LoadReport([], [f"Your color tables file ({store}) could not be opened: "
                               f"{e.strerror or e}. Your own tables are not loaded."], None)


def load():
    """Every stored user colortable as a list of entry dicts (or [] if there are none).

    A damaged store is repaired from its backup on the way (see load_report)."""
    return load_report().entries


def _write(entries):
    """Save `entries` as the whole store, keeping the version it replaces as the .bak.

    The backup is taken only from a store that still reads, so a damaged file can never
    overwrite the last good backup."""
    store = store_path()
    store.parent.mkdir(parents=True, exist_ok=True)
    try:
        current, _tables = _read_list(store)
    except (OSError, ValueError):
        current = None
    if current is not None:
        _atomic_write_text(_backup_path(), current)
    _atomic_write_text(store, json.dumps(entries, indent=2))


def _reserved_names():
    """Names a new table may not take (colortable_registry.reserved_names: in tcviz,
    products.reserved_palette_names). Imported here, not at the top: colormaps imports this
    module while it loads -- by the time anything saves a table, it is long since loaded."""
    from tcviz import colortable_registry
    return colortable_registry.reserved_names()


def _app_name():
    """The program's name for a message: tcviz, or Colortable Editor."""
    from . import edition
    return edition.app_name()


def make_entry(name, stops, vmin_c=-100.0, vmax_c=50.0, description="", category="Temperature (IR)",
               reversed_=True, levels=DEFAULT_LEVELS, *, hidden=False, units=DEFAULT_UNITS, vmin=None,
               vmax=None):
    """The entry add() would store for these fields, checked to build -- without storing it
    or looking at the names already taken. tcviz.colortable_library builds on this to make
    a rename or an edit one save of the file instead of two.

    `units` is "C" for an infrared or water-vapor table, "kt" for winds, "dBZ" for radar.
    The range is `vmin`/`vmax` in those units; a table in degrees C may give it as the
    older `vmin_c`/`vmax_c` instead (and stores both pairs). A winds or radar table stores
    only `vmin`/`vmax` (normalize says why) and its kind's own category, whatever
    `category` says -- except None, the legacy spelling of hidden."""
    validate_name(name)
    if units not in KNOWN_UNITS:
        raise ValueError(f"units must be one of {KNOWN_UNITS}")
    if units == DEFAULT_UNITS:
        if category not in VALID_CATEGORIES:
            raise ValueError(f"category must be one of {VALID_CATEGORIES}")
        vmin = vmin_c if vmin is None else vmin
        vmax = vmax_c if vmax is None else vmax
    else:
        if category is None:
            hidden = True
        category = UNIT_CATEGORIES[units]
        default_lo, default_hi = DEFAULT_RANGES[units]
        vmin = default_lo if vmin is None else vmin
        vmax = default_hi if vmax is None else vmax
    if isinstance(stops, str):
        stops = parse_stops(stops)
    stops = [(float(p), c) for p, c in stops]
    _check_range(units, float(vmin), float(vmax))
    if category is None:
        category, hidden = "Temperature (IR)", True
    entry = {"name": name, "stops": stops}
    if units == DEFAULT_UNITS:
        # The old keys stay, so an older app or the bot reads it as before; the new ones
        # are what normalize would have filled in.
        entry.update(vmin_c=float(vmin), vmax_c=float(vmax))
    entry.update(description=description, category=category, reversed=bool(reversed_), units=units,
                 vmin=float(vmin), vmax=float(vmax), levels=int(levels), hidden=bool(hidden))
    build_cmap(entry)  # final sanity check that it actually builds
    return entry


def _check_range(units, vmin, vmax):
    """ValueError, in plain words, for a range a table of these units cannot have."""
    if not (math.isfinite(vmin) and math.isfinite(vmax)):
        raise ValueError("The ends of the range must be ordinary numbers.")
    if units == DEFAULT_UNITS:
        if vmax <= vmin:
            raise ValueError("The warm end of the range must be warmer than the cold end.")
        return
    if vmax <= vmin:
        raise ValueError("The top of the range must be above the bottom.")
    if units == "kt" and vmin < 0:
        raise ValueError("A wind speed can't be below 0 kt.")


def add(name, stops, vmin_c=-100.0, vmax_c=50.0, description="", category="Temperature (IR)",
        reversed_=True, levels=DEFAULT_LEVELS, *, hidden=False, replace=False, units=DEFAULT_UNITS,
        vmin=None, vmax=None):
    """Validate and persist one colortable; returns the stored entry dict.

    `stops` may be the raw pasted text or an already-parsed [(pos, hex), ...] list.
    `hidden` keeps the table out of every picker while leaving it usable by name; the
    legacy category=None means the same (an IR table, hidden). `units`, `vmin` and `vmax`
    are make_entry's: a winds ("kt") or radar ("dBZ") table gives its range in those.

    A name already in the store raises NameTaken unless `replace` is True -- a same-name
    save used to overwrite without a word. A NEW name that tcviz itself uses (a built-in
    table or look) raises NameReserved; names already in the store are let through, since
    a few of the user's own tables were later built in under the same name.

    Does NOT register it into colormaps -- callers that want it live immediately call
    colormaps.register_user_colortable(entry), or use tcviz.colortable_library.save, which
    does both."""
    entry = make_entry(name, stops, vmin_c, vmax_c, description, category, reversed_, levels,
                       hidden=hidden, units=units, vmin=vmin, vmax=vmax)
    # The name checks come after every other one, so nobody is asked "Replace?" about a
    # table that then fails to save anyway.
    stored = load()
    names = [e.get("name") if isinstance(e, dict) else None for e in stored]
    if name in names:
        if not replace:
            raise NameTaken(name)
    elif name in _reserved_names():
        raise NameReserved(name)
    # a replaced table keeps its place in the list (and in "Load one of yours")
    at = names.index(name) if name in names else len(stored)
    entries = [e for e, n in zip(stored, names) if n != name]
    entries.insert(at, entry)
    _write(entries)
    return entry


def delete(name):
    """Remove a stored colortable by name; returns True if one was removed."""
    entries = load()
    kept = [e for e in entries if not (isinstance(e, dict) and e.get("name") == name)]
    if len(kept) != len(entries):
        _write(kept)
        return True
    return False



# ----------------------------------------------------------------- sharing one of these

# The stored JSON is the wrong thing to hand someone: it is one file holding every table
# you own, its positions are evaluated floats, and nothing in the app reads it back. So a
# colortable travels as the SAME Python that defines the built-in curves in colormaps.py:
#
#     def my_ir():
#         newcmp = LinearSegmentedColormap.from_list("", [
#             (0/150, "#000000"),
#             (60/150, "#ffffff"),
#             (60/150, "#ff0000"),
#             (150/150, "#f2f2f2")
#         ])
#         vmax = 50 + 273.15
#         vmin = -100 + 273.15
#         return newcmp.reversed(), vmax, vmin
#
# That form is readable (10/150 is "10 degrees in from the end the stops start at" -- the
# warm end, for a reversed curve -- where 0.06666666666666667 is nothing), it is what
# anyone working on this project already knows, and it pastes straight back. A repeated
# position is a hard step, as in extra_cmaps.py and the blocks people share; colormaps.py
# itself still wraps such a list in _fix_duplicate_stops([...]), and both forms are read.
# A table drawn with other than 256 colors closes with `], N=57)`. tcviz.colortable_export
# writes the block and checks that it rebuilds the same table. parse_stops needed no
# changes to read it back: it takes the first #hex on a line as that line's color and
# skips lines that have none, so the def, the from_list, the vmax/vmin and the return all
# fall away on their own.
#
# A winds or radar table says so on a line of its own, `# units: kt` or `# units: dBZ`,
# and its bounds are in those units: `vmax = 100 / 1.9438452` (100 kt, which as Python is
# the m/s the engine draws in) or `vmax = 75`. Those bounds are read as the number
# written, never as a Kelvin temperature however large.
_DEF_RE = re.compile(r"^\s*def\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE)
_UNITS_RE = re.compile(r"^\s*#\s*units\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_UNIT_SPELLINGS = {"kt": "kt", "kts": "kt", "knot": "kt", "knots": "kt", "dbz": "dBZ",
                   "c": "C", "°c": "C", "k": "C"}
_BOUND_RE = {
    "vmax_c": re.compile(r"^\s*vmax\s*=\s*(-?\d+(?:\.\d+)?)(.*)$", re.MULTILINE),
    "vmin_c": re.compile(r"^\s*vmin\s*=\s*(-?\d+(?:\.\d+)?)(.*)$", re.MULTILINE),
}
_CATEGORY_RE = re.compile(r"^\s*#\s*category\s*:\s*(.+?)\s*$", re.MULTILINE)
# Up to the first """ that is not escaped: the description goes in with its backslashes
# and double quotes escaped, so one ending in a quote does not end the docstring early.
_DOCSTRING_RE = re.compile(r'^\s*"""((?:\\.|[^\\])*?)"""', re.MULTILINE | re.DOTALL)
_ESCAPED_RE = re.compile(r'\\([\\"])')
# The return line, not ".reversed()" anywhere: a description may say either.
_RETURN_RE = re.compile(r"^\s*return\s+newcmp(\.reversed\(\))?", re.MULTILINE)
# The level count, on the line that closes the stop list: `], N=57)` (or `]), N=57)` after
# a _fix_duplicate_stops wrapper).
_LEVELS_RE = re.compile(r"^\s*\]\)?\s*,\s*N\s*=\s*(\d+)\s*\)", re.MULTILINE)
# Above this a bound cannot be a temperature in Celsius (the dialog's own spin boxes stop
# at 100C), so it is Kelvin and has to come back down.
_KELVIN_CUTOFF_C = 150.0


def _fraction(pos, span):
    """`pos` as an a/b fraction of the degree span, the way the built-ins are written.

    Only when that fraction IS the position, to the last bit; otherwise the shortest
    decimal that is -- better an exact number than a tidy-looking fraction that moves the
    stop, or a rounded decimal that moves it a little less.
    """
    if math.isfinite(span) and span > 0 and abs(span - round(span)) < 1e-9:
        whole = round(span)
        num = round(pos * whole)
        if num / whole == pos:
            return f"{num}/{whole}"
    return _decimal(pos)


def _decimal(x):
    """The shortest plain decimal that reads back as exactly x. Never 1e-06: the position
    and bound readers take digits and a point only."""
    x = float(x)
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))  # -0.0 too: int() drops the sign
    return np.format_float_positional(x, unique=True, trim="-")


def _python_name(name):
    """A table's name as a Python function name. A keyword such as `if` cannot name a
    function, so it gets PEP 8's trailing underscore (`if_`) -- and so does any name that
    already ends in underscores after one (`if_` -> `if__`), so _table_name can undo it."""
    return f"{name}_" if keyword.iskeyword(name.rstrip("_")) else name


def _table_name(name):
    """_python_name undone."""
    return name[:-1] if name.endswith("_") and keyword.iskeyword(name.rstrip("_")) else name


def to_shareable(entry):
    """One colortable as the Python that would define it, description and category
    included -- the Add dialog's "Copy for sharing". Checked to rebuild the same table
    (tcviz.colortable_export.entry_block)."""
    from . import colortable_export
    return colortable_export.entry_block(entry, include_notes=True)


def _bound_celsius(number, rest):
    """A vmax/vmin literal as Celsius, whichever way it was written.

    Three forms are in circulation and all three are read: `50 + 273.15` (Celsius plus the
    offset, as colormaps.py writes it), a bare `50` (Celsius), and a bare `323.15`
    (Kelvin). The offset settles the first; magnitude settles the other two, since no
    Celsius bound here comes near 150.
    """
    if "273.15" in rest:
        return number
    return number - 273.15 if abs(number) > _KELVIN_CUTOFF_C else number


def units_of(text):
    """The units a shared block's `# units:` line names ("C", "kt" or "dBZ"), or None when
    it has none, or names one tcviz does not know."""
    m = _UNITS_RE.search(text)
    return _UNIT_SPELLINGS.get(m.group(1).strip().lower()) if m else None


def parse_shareable(text, skipped=None):
    """The fields of a shared colortable, as kwargs for add().

    Everything except the stops is optional, so a bare list of stops -- or a block someone
    trimmed to just the numbers -- is still valid input. Only the keys the text really
    carried come back, so a caller can leave its own widgets alone for the rest.
    `skipped` collects the lines with no color, as parse_stops does.

    A table in degrees C gives its range as vmin_c/vmax_c, as it always has. A winds or
    radar table -- a `# units: kt` or `# units: dBZ` line, or failing one its category --
    comes back with `units` and its range as vmin/vmax in those units.
    """
    fields = {}
    m = _DEF_RE.search(text)
    if m:
        fields["name"] = _table_name(m.group(1))
    doc = _DOCSTRING_RE.search(text)
    if doc and doc.group(1).strip():
        fields["description"] = " ".join(_ESCAPED_RE.sub(r"\1", doc.group(1)).split())
    cat = _CATEGORY_RE.search(text)
    if cat:
        value = cat.group(1).strip()
        fields["category"] = None if value.lower() == "none" else value
    units = units_of(text)
    if units is None:
        units = next((u for u, c in UNIT_CATEGORIES.items() if c == fields.get("category")), None)
    if units in UNIT_CATEGORIES:
        fields["units"] = units
    for key, pattern in _BOUND_RE.items():
        bm = pattern.search(text)
        if not bm:
            continue
        if units in UNIT_CATEGORIES:
            fields[key.removesuffix("_c")] = float(bm.group(1))
        else:
            fields[key] = _bound_celsius(float(bm.group(1)), bm.group(2))
    from_list = text.find("from_list")
    levels = _LEVELS_RE.search(text, max(from_list, 0))
    if levels:
        fields["levels"] = int(levels.group(1))
    ret = _RETURN_RE.search(text)
    if ret:
        fields["reversed_"] = bool(ret.group(1))
    elif ".reversed()" in text:
        fields["reversed_"] = True
    fields["stops"] = parse_stops(text, skipped)
    return fields


def looks_shareable(text):
    """Whether this is a whole colortable definition rather than a bare stop list, so a
    paste can be recognized and the rest of a dialog filled in from it."""
    return bool(_DEF_RE.search(text)) and "from_list" in text
