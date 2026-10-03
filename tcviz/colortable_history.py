"""Earlier versions of the person's own color tables: colortables.history.json beside the
tables file.

Each time one of the person's tables is saved over (colortable_library.save), the version
it replaces is kept here first -- only when it differs from the new one -- so the editor's
History window can show it and put it back. KEEP versions per table, newest first; the
oldest go. Each gets a label, v1, v2, ..., counting up per table and never given twice,
even after the oldest have gone.

The file is {table name: [version, ...]}, newest first. A version is {"id": a uuid4 hex,
"saved_at": when it was saved over (ISO 8601, UTC), "label": "v7", "stops", "vmin", "vmax",
"units", "reversed", "levels", "description", "category"}: the table's own fields as
user_colortables.normalize reads them, without its name or whether it is hidden. The tables
file is never changed for this -- the bot and older apps share its entries -- and this file
is only written once there is a version to keep: a table saved once has none.

A deleted table's versions wait under "deleted/<its id in the recently deleted list>" (no
table name has a slash), so a new table under the old name starts with none, and a restore
brings them back under whatever name the table comes back as. They go when the table
leaves the recently deleted list.

Like that list, this file only ever holds history, never a table in use: one that does not
read counts as empty. One that is damaged is copied aside
(colortables.history.damaged-<when>.json) before a fresh one is started; one that cannot be
opened at all is left as it is, and nothing is kept until it can be. A table's own save
never waits on this file (colortable_library catches what goes wrong here). Qt-free.
"""
import datetime
import json
import math
import re
import shutil
import time
import uuid
from collections import namedtuple

from . import user_colortables

KEEP = 20
_PARKED = "deleted/"
_LABEL_RE = re.compile(r"^v([1-9][0-9]*)$")
# A version's own fields, in the file's order.
FIELDS = ("stops", "vmin", "vmax", "units", "reversed", "levels", "description", "category")

Version = namedtuple("Version", "id label when entry")
Version.__doc__ = """One kept version: `label` "v7"; `when` the moment it was saved over, an
aware datetime in UTC (None if the file's time does not read); `entry` the table as it was,
as the tables file keeps a table, under the name it belongs to now -- ready for the editor
(ColortableModel.from_entry) or for drawing (user_colortables.build_cmap)."""

# What before_save and the other changes hand back, for undo_save to put the file back as
# it was: its text then, or None when there was no file.
_Before = namedtuple("_Before", "text")


# --------------------------------------------------------------------------- reading

def history_path():
    """colortables.history.json beside the tables file, wherever that is (tcviz's own, or
    Colortable Editor's folder)."""
    store = user_colortables.store_path()
    return store.with_name(f"{store.stem}.history{store.suffix}")


def versions(name):
    """The kept versions of the table `name`, newest first ([] for none, and while the file
    does not read)."""
    return [_as_version(name, r) for r in _read().get(name, [])]


def has_versions(name):
    return bool(name) and bool(_read().get(name))


def same_table(a, b):
    """Whether two table entries draw the same and say the same: the fields a version keeps,
    the colors compared without regard to capitals. Neither name nor hiding counts."""
    first, second = _fields(a), _fields(b)
    return first is not None and second is not None and _key(first) == _key(second)


def when_text(when, now=None):
    """"Today 14:32", "Yesterday 21:05" or "2026-09-30 10:11": a version's time as the
    History window lists it, on this computer's clock (the file keeps UTC). `now` is for
    tests; the current time otherwise."""
    if when is None:
        return "an unknown time"
    local = when.astimezone()
    today = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone().date()
    clock = f"{local.hour:02d}:{local.minute:02d}"
    if local.date() == today:
        return f"Today {clock}"
    if local.date() == today - datetime.timedelta(days=1):
        return f"Yesterday {clock}"
    return f"{local.year:04d}-{local.month:02d}-{local.day:02d} {clock}"


def plain_reason(error):
    """Why the history file could not be kept, in a few plain words."""
    if isinstance(error, PermissionError):
        return "its file is open in another program, or the folder can't be written to"
    if isinstance(error, OSError):
        return str(error.strerror or "the file could not be written").rstrip(".")
    return "something unexpected went wrong"


# --------------------------------------------------------------------------- changing

def before_save(name, replaced, entry, *, now=None):
    """Keep what a save of `entry` under `name` replaces, just before the tables file is
    written. `replaced` is [(stored name, stored entry), ...] of the tables the save takes
    the place of: the table itself when it is saved over, and the one it is renamed from.

    Their versions carry on under `name`, and each one that differs from `entry` is kept as
    a new version (same_table). A save that replaces nothing is a new table, which starts
    with no versions: any left under its name by a table that went without telling this
    file are dropped.

    Returns what undo_save needs to put the file back should the tables file then fail to
    save, or None when this file was not touched. Raises OSError when it cannot be written
    (or a damaged one set aside, or one that is there opened) -- the caller saves the table
    anyway."""
    now = now or datetime.datetime.now(datetime.timezone.utc)

    def change(data):
        carried = []
        for old_name, _old in replaced:
            carried += data.pop(old_name, [])
        data.pop(name, None)                  # carried just now, or a stale list
        if len({old_name for old_name, _old in replaced}) > 1:
            carried.sort(key=_saved_at, reverse=True)
        number = max((_number(r) for r in carried), default=0)
        new = _fields(entry)
        for _old_name, old in replaced:
            fields = _fields(old)
            if fields is None or (new is not None and _key(fields) == _key(new)):
                continue
            number += 1
            carried.insert(0, {"id": uuid.uuid4().hex, "saved_at": _iso(now), "label": f"v{number}", **fields})
        if carried:
            data[name] = carried[:KEEP]
        return data
    return _change(change)


def undo_save(before):
    """Put the file back as before_save found it (the tables file could not be saved)."""
    if before is None:
        return
    path = history_path()
    if before.text is None:
        path.unlink(missing_ok=True)
    else:
        user_colortables._atomic_write_text(path, before.text)


def move(old, new):
    """The table `old` is called `new` now: its versions go with it."""
    def change(data):
        moved = data.pop(old, None)
        data.pop(new, None)
        if moved:
            data[new] = moved
        return data
    return _change(change)


def set_aside(name, deleted_id, keep_ids=()):
    """The table `name` was deleted as `deleted_id` of the recently deleted list: its
    versions wait under that id. Those of tables no longer in that list (`keep_ids`, the
    ones still there) go."""
    key = _PARKED + deleted_id

    def change(data):
        moved = data.pop(name, None)
        if moved:
            data[key] = moved
        keep = {_PARKED + i for i in keep_ids} | {key}
        return {k: v for k, v in data.items() if not k.startswith(_PARKED) or k in keep}
    return _change(change)


def bring_back(deleted_id, name):
    """The table deleted as `deleted_id` is back, called `name`: its versions too."""
    key = _PARKED + deleted_id

    def change(data):
        moved = data.pop(key, None)
        if moved:
            data[name] = moved
        return data
    return _change(change)


def forget(name):
    """Drop any versions under `name`: a new table (a copy, say) starts with none."""
    def change(data):
        data.pop(name, None)
        return data
    return _change(change)


# --------------------------------------------------------------------------- inside

def _read():
    """{key: [good version records]}, or {} when there is no file or it does not read."""
    try:
        return _parse(history_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def _parse(text):
    """The file's text as {key: [version records]}, the records that do not read dropped.
    ValueError when it is not a history file at all."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the file does not hold a history")
    out = {}
    for key, records in data.items():
        if not isinstance(key, str) or not isinstance(records, list):
            continue
        good = [r for r in (_record(r) for r in records) if r is not None]
        if good:
            out[key] = good
    return out


def _change(edit):
    """Read the file, `edit` a copy of what it holds, and write it back if anything changed;
    a _Before for undo_save, or None when nothing was written."""
    path = history_path()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        text = None
    # any other OSError: the file is there but can't be opened, and is left as it is
    damaged = False
    data = {}
    if text is not None:
        try:
            data = _parse(text)
        except ValueError:
            damaged = True
    changed = edit({k: list(v) for k, v in data.items()})
    if changed == data:
        # nothing to keep -- and a damaged file stays exactly as it is until there is
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    if damaged:
        # a copy, kept, before a fresh file takes its place
        shutil.copy2(path, _damaged_path())
    user_colortables._atomic_write_text(path, json.dumps(changed, indent=1))
    return _Before(text)


def _damaged_path():
    """colortables.history.damaged-<when>.json beside the file, never one that exists."""
    path = history_path()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem = path.name[:-len(path.suffix)] if path.suffix else path.name
    out = path.with_name(f"{stem}.damaged-{stamp}{path.suffix}")
    number = 1
    while out.exists():
        number += 1
        out = path.with_name(f"{stem}.damaged-{stamp}-{number}{path.suffix}")
    return out


def _fields(entry):
    """A table entry's version fields, as plain JSON values; None when they don't read."""
    try:
        e = user_colortables.normalize(entry)
        out = {"stops": [[float(p), str(c)] for p, c in e["stops"]], "vmin": float(e["vmin"]),
               "vmax": float(e["vmax"]), "units": str(e["units"]), "reversed": bool(e.get("reversed", True)),
               "levels": e["levels"], "description": str(e.get("description") or ""), "category": e["category"]}
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    if not _good_fields(out):
        return None
    return out


def _good_fields(r):
    stops = r.get("stops")
    if not isinstance(stops, list) or len(stops) < 2:
        return False
    for stop in stops:
        if not (isinstance(stop, (list, tuple)) and len(stop) == 2 and _number_ok(stop[0])
                and isinstance(stop[1], str)):
            return False
    levels = r.get("levels")
    return (_number_ok(r.get("vmin")) and _number_ok(r.get("vmax"))
            and r.get("units") in user_colortables.KNOWN_UNITS and isinstance(r.get("reversed"), bool)
            and isinstance(levels, int) and not isinstance(levels, bool)
            and isinstance(r.get("description"), str) and isinstance(r.get("category"), str))


def _number_ok(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _record(raw):
    """One version as the file holds it, checked; None for one that does not read."""
    if not (isinstance(raw, dict) and isinstance(raw.get("id"), str) and isinstance(raw.get("label"), str)
            and _LABEL_RE.match(raw["label"]) and isinstance(raw.get("saved_at"), str)):
        return None
    fields = {k: raw.get(k) for k in FIELDS}
    if not _good_fields(fields):
        return None
    fields["stops"] = [[float(p), c] for p, c in fields["stops"]]
    return {"id": raw["id"], "saved_at": raw["saved_at"], "label": raw["label"], **fields}


def _key(fields):
    """What two versions are compared by: the stops in the order they are drawn (sorted by
    position, a hard step's two in the order stored), colors without capitals."""
    stops = tuple((p, c.lower()) for p, c in sorted(((float(p), c) for p, c in fields["stops"]),
                                                     key=lambda s: s[0]))
    return (stops, *(fields[k] for k in FIELDS[1:]))


def _number(record):
    return int(_LABEL_RE.match(record["label"]).group(1))


def _iso(when):
    return when.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def _parse_time(text):
    try:
        when = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when.astimezone(datetime.timezone.utc)


def _saved_at(record):
    return _parse_time(record["saved_at"]) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)


def _as_version(name, record):
    entry = {"name": name, "stops": [(p, c) for p, c in record["stops"]], "units": record["units"],
             "vmin": record["vmin"], "vmax": record["vmax"], "description": record["description"],
             "category": record["category"], "reversed": record["reversed"], "levels": record["levels"],
             "hidden": False}
    if record["units"] == user_colortables.DEFAULT_UNITS:
        entry.update(vmin_c=record["vmin"], vmax_c=record["vmax"])
    return Version(record["id"], record["label"], _parse_time(record["saved_at"]), entry)
