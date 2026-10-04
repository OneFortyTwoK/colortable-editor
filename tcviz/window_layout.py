"""The window layout the color table editor remembers: layout.json beside the tables file
(tcviz's ~/.config/tcviz, or Colortable Editor's own folder, edition.editor_config_dir).

tcviz keeps no settings between runs, but for the editor it does (the author's choice,
2026-10-03): the editor window's size and whether it was maximized, where its panels were
left (the color panel folded away or not), whether it showed one storm or several (its
storm grid), Colortable Editor's main window size, and the storms ticked under the storm
grid's Storms… for each kind of table. A window that opens again opens as it was left.

The file is one JSON object, a record per window or part under its own key -- "editor",
"main_window", "storm_grid", and whatever else comes to be remembered (get, update) -- so
each reads and writes only its own:

    {"editor": {"size": [1480, 820], "maximized": false, "splitter": [262, 940], "view": "several"},
     "main_window": {"size": [900, 560], "maximized": false},
     "storm_grid": {"ir": ["melissa-2025", "polo-2026-viirs"], "wv": []}}

It only ever holds how windows were left, never anything a person made, so it is
forgiving every way: a file that is missing, damaged or cannot be read is no layout (each
window opens as it first did), one that cannot be written means the layout is just not
remembered this time, and neither is ever an error. The folder is made only when there is
something to write; reset deletes the file. Sizes read back are checked by the window
that uses them (fit_size, fit_sizes): a layout left on a big screen opens sensibly on a
laptop. Qt-free.
"""
import contextlib
import json
import math

from . import user_colortables

FILE_NAME = "layout.json"
# Room a window leaves around itself on the screen, (across, down): its frame, and the
# taskbar or panel some systems do not count out of the screen's available space.
SCREEN_MARGIN = (40, 60)


def layout_path():
    """layout.json beside the tables file, wherever that is."""
    return user_colortables.store_path().with_name(FILE_NAME)


def get(key):
    """A copy of the record kept under `key` (a dict), or {} when there is none -- no file,
    a damaged one, or one that can't be read."""
    record = _read().get(key)
    return dict(record) if isinstance(record, dict) else {}


def update(key, values):
    """Keep `values` (a dict of plain JSON values) in the record under `key`, over what it
    held; the other records are left as they are. The file is written only when that
    changes it. Whether it was written: a folder that can't be written to is never an
    error, the layout just isn't remembered."""
    data = _read()
    record = dict(data.get(key)) if isinstance(data.get(key), dict) else {}
    record.update(values)
    if data.get(key) == record:
        return False
    data[key] = record
    path = layout_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        user_colortables._atomic_write_text(path, json.dumps(data, indent=1))
    except OSError:
        return False
    return True


def reset():
    """Forget every window's layout: layout.json is deleted. Whether there was one and it
    is gone."""
    try:
        layout_path().unlink()
    except OSError:
        return False
    return True


def fit_size(size, room, margin=SCREEN_MARGIN):
    """A saved (width, height) made to fit the screen: no wider or taller than `room` (the
    screen's available (width, height), or None when it is not known) less `margin`. None
    when `size` is not two whole numbers above zero."""
    if not (isinstance(size, (list, tuple)) and len(size) == 2 and all(_whole(v) and v > 0 for v in size)):
        return None
    width, height = size
    if room is not None:
        width = min(width, max(1, room[0] - margin[0]))
        height = min(height, max(1, room[1] - margin[1]))
    return int(width), int(height)


def fit_sizes(sizes, least, total, collapsible=(), rest=-1):
    """Saved splitter sizes (one per panel) that still fit, or None to keep the panels as
    they open. The panel `rest` (the last, by default) takes whatever room the others
    leave, so its own saved size counts for nothing; each other panel is its saved size,
    or 0 -- folded away -- where `collapsible` (a panel's index in it) allows. They fit
    when they are whole numbers, as many as `least` (each panel's least width), each
    unfolded panel at least its least, and those panels with the `rest` one at its least
    fit in `total`. The sizes returned have the room left over in the `rest` panel."""
    if not (isinstance(sizes, (list, tuple)) and len(sizes) == len(least) and all(_whole(v) and v >= 0
                                                                                  for v in sizes)):
        return None
    rest %= len(least)
    out = [int(v) for v in sizes]
    for i, size in enumerate(out):
        if i == rest:
            continue
        if size == 0 and i not in collapsible:
            return None
        if 0 < size < least[i]:
            return None
    others = sum(v for i, v in enumerate(out) if i != rest)
    if others + least[rest] > total:
        return None
    out[rest] = total - others
    return out


# --------------------------------------------------------------------------- inside

def _read():
    """{key: record} from the file, or {} when there is none or it does not read."""
    with contextlib.suppress(OSError, ValueError):
        data = json.loads(layout_path().read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            return data
    return {}


def _whole(value):
    """A whole number as JSON gives one back (not true/false, which Python counts as 1 and 0)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return math.isfinite(value) and value == int(value)
    return isinstance(value, int)
