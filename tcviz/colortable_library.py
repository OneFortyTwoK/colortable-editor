"""The user's own color tables as a library: save, rename, duplicate, hide, delete, restore.

Every call changes the tables file (tcviz.user_colortables: one atomic save, leaving the
version before it as the .bak) and the live registry (colortable_registry.register_user_colortable /
unregister_user_colortable) together, and bumps its generation, so whatever caches by palette
name -- the picker's swatches -- knows to look again. The app's Manage window and the picker
call these, then tell the rest of the app (tcviz_gui.library_events).

A delete is not final at once: the table goes to colortables.deleted.json beside the
store, which keeps the DELETED_KEEP most recent, and restore() puts it back where it was.
That is the Manage window's Undo, so a delete needs no "Are you sure?".

A rename also renames the table in the favorites file (favorite_palettes.json in
paths.config_dir(); in Colortable Editor, beside its tables file), so a starred table keeps
its star; a deleted one keeps its entry there, so a restored favorite comes back starred.

Saving over a table keeps the version it replaces in colortables.history.json beside the
store (tcviz.colortable_history), for the editor's History window: a rename takes the
versions along, a delete keeps them for a restore, and a copy starts with none. That file
never holds a table in use, so nothing that goes wrong with it stops a save: the table is
saved, and `history_problems`, when given a list, gets a sentence saying what could not be
kept.

In Colortable Editor (tcviz.edition) the registry is its own -- six built-in tables and the
person's -- and only infrared and water-vapor tables are listed.

Some long-kept user tables carry the name of a built-in that was later baked from them
(user_colortables.add lets names already in the store through). Taking one of those out
of the registry -- a rename or a delete -- puts the built-in back under that name
(colortable_registry.unregister_user_colortable), and the name cannot be taken again
afterwards: it is a built-in's now.

Qt-free.
"""
import contextlib
import datetime
import itertools
import json
import re
import uuid
from collections import namedtuple

from . import colortable_history
from . import colortable_registry as registry
from . import edition, user_colortables

DELETED_KEEP = 25
FAVORITES_FILE = "favorite_palettes.json"
KIND_LABELS = {"ir": "Infrared", "wv": "Water vapor", "wind": "Winds", "radar": "Radar"}
# What each kind's range is in, as a person reads it.
KIND_UNIT_LABELS = {"ir": "°C", "wv": "°C", "wind": "kt", "radar": "dBZ"}
_KIND_OF_CATEGORY = {"Temperature (IR)": "ir", "Water Vapor": "wv",
                     user_colortables.UNIT_CATEGORIES["kt"]: "wind", user_colortables.UNIT_CATEGORIES["dBZ"]: "radar"}
_CATEGORY_OF_KIND = {kind: category for category, kind in _KIND_OF_CATEGORY.items()}
_KIND_OF_UNITS = {"kt": "wind", "dBZ": "radar"}

# validate_name's own sentence is shared with the bot, which shows it as it is; the app's
# wording is this one.
_NAME_RULE = "A name can use only lowercase letters, digits and _, and has to start with a letter."


def __getattr__(name):
    """NameTaken and NameReserved, the ones user_colortables raises: looked up when asked
    for, not copied at import, so they stay the same classes even if that module is
    reloaded (the dialog tests reload it)."""
    if name in ("NameTaken", "NameReserved"):
        return getattr(user_colortables, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class NotFound(LookupError):
    """No table of the user's goes by that name (or no deleted one has that id); the
    message says so in plain words."""


Table = namedtuple("Table", "name entry kind hidden problem shadows_builtin builtin", defaults=(False,))
Table.__doc__ = """One table in the store, as the Manage window lists it: `entry` normalized,
`kind` "ir", "wv", "wind" or "radar", `problem` None or why it cannot be drawn (in plain
words), and `shadows_builtin` whether a built-in table of the same name is used once this
one goes. `builtin` marks one of Colortable Editor's built-in tables (builtin_tables), which
can be copied and exported but not changed."""


def entry_kind(entry):
    """"ir", "wv", "wind" or "radar" for a stored table: its units say winds or radar, else
    its category says water vapor or (anything else) infrared."""
    entry = user_colortables.normalize(entry)
    return _KIND_OF_UNITS.get(entry["units"]) or _KIND_OF_CATEGORY.get(entry["category"], "ir")

Deleted = namedtuple("Deleted", "id name entry when")
Deleted.__doc__ = """One recently deleted table: `id` to restore it by, `when` a local
datetime."""


# --------------------------------------------------------------------------- reading

def tables():
    """Every table in the store, in the store's order, as Table rows.

    A table that cannot be drawn is listed too, with why, so it can be fixed or deleted;
    only an entry with no name at all is skipped (the startup message names it). In
    Colortable Editor a table of a kind it does not offer (tcviz.edition.kinds) is left out:
    a winds or radar table copied in from tcviz has no place there."""
    builtin = registry.builtin_names()
    kinds = edition.kinds()
    out = []
    for raw in user_colortables.load():
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            continue
        entry = user_colortables.normalize(raw)
        if entry_kind(entry) not in kinds:
            continue
        try:
            user_colortables.build_cmap(entry)
            problem = None
        except Exception as e:
            problem = registry.user_table_failure(e)
        out.append(Table(entry["name"], entry, entry_kind(entry), entry["hidden"], problem,
                         entry["name"] in builtin))
    return out


def builtin_tables():
    """Colortable Editor's built-in tables as Table rows (`builtin` True), in its order, for
    its main list; [] in tcviz, whose Manage window lists only the person's own tables."""
    out = []
    for name, entry in registry.builtin_entries().items():
        out.append(Table(name, entry, entry_kind(entry), False, None, False, True))
    return out


def get(name):
    """The stored table `name`, normalized (NotFound if there is none)."""
    stored = user_colortables.load()
    return user_colortables.normalize(stored[_index(stored, name)])


def user_table_names():
    """Every name the registry draws from the user's own store, hidden tables included."""
    return registry.user_table_names()


def kind_of(name):
    """"ir", "wv", "wind" or "radar" for a table of one of those kinds the app has --
    built-in or yours, hidden ones of yours included -- else None (a visible look, a
    microwave table...)."""
    entry = getattr(registry.registered_builders().get(name), "user_entry", None)
    if entry is not None:
        return entry_kind(entry)
    from . import colortable_export
    for kind in ("ir", "wv", "wind", "radar"):
        if name in colortable_export.exportable_names(kind):
            return kind
    return None


def export_groups():
    """The Manage window's "Export many…" choices: {"mine": your tables -- infrared, water
    vapor, winds and radar --, "ir": every built-in IR table, "wv": every built-in
    water-vapor table}, each in the picker's order. A table of yours under a built-in's
    name counts as yours: it is the one drawn under that name."""
    from . import colortable_export
    mine = user_table_names()
    ir, wv = colortable_export.exportable_names("ir"), colortable_export.exportable_names("wv")
    wind, radar = colortable_export.exportable_names("wind"), colortable_export.exportable_names("radar")
    return {"mine": [n for n in ir + wv + wind + radar if n in mine],
            "ir": [n for n in ir if n not in mine],
            "wv": [n for n in wv if n not in mine]}


# --------------------------------------------------------------------------- changing

def save(name, stops, *, original=None, replace=False, history_problems=None, **fields):
    """Save one table and make it live; returns the stored entry.

    `fields` are user_colortables.add's (vmin_c, vmax_c, description, category, reversed_,
    levels, hidden; a winds or radar table's units, vmin and vmax). `original` is the name
    of the table being edited, if any: saved under
    that same name it is replaced without asking, and saved under a new one it is renamed
    -- the old name leaves the store, the registry and the favorites in the same save.
    Any other name already in the store raises NameTaken unless `replace`; a new name
    tcviz itself uses raises NameReserved.

    A table saved over keeps the version it replaces, if it differs, in its history
    (colortable_history.before_save), written just before the tables file. If that cannot
    be kept the table is saved all the same, and `history_problems` (a list) gets a
    sentence saying so."""
    _check_name(name)
    entry = user_colortables.make_entry(name, stops, **fields)
    stored = user_colortables.load()
    names = _names(stored)
    editing = original is not None and original in names
    if name in names and not (editing and name == original) and not replace:
        raise user_colortables.NameTaken(name)
    if name not in names and name in user_colortables._reserved_names():
        raise user_colortables.NameReserved(name)
    # the new version takes the place of the first of the tables it replaces
    drop = {name, original} if editing else {name}
    out, placed, replaced = [], False, []
    for raw, n in zip(stored, names):
        if n in drop:
            replaced.append((n, raw))
            if not placed:
                out.append(entry)
                placed = True
            continue
        out.append(raw)
    if not placed:
        out.append(entry)
    kept = None
    try:
        kept = colortable_history.before_save(name, replaced, entry)
    except Exception as e:
        # a new table had nothing to keep; only a version that could not be kept is news
        if replaced and history_problems is not None:
            history_problems.append(f"'{name}' was saved, but the version it replaced could not be kept in "
                                    f"History: {colortable_history.plain_reason(e)}.")
    try:
        user_colortables._write(out)
    except BaseException:
        with contextlib.suppress(Exception):
            colortable_history.undo_save(kept)
        raise
    if editing and original != name:
        registry.unregister_user_colortable(original)
        _rename_favorite(original, name)
    _register(entry)
    _bump()
    return entry


def rename(old, new, history_problems=None):
    """Give the table `old` the name `new`; returns its entry. Its place in the list, its
    colors, its star in Favorites and its earlier versions stay as they were (a history
    that cannot be moved is said in `history_problems`, as save says it)."""
    _check_name(new)
    stored = user_colortables.load()
    names = _names(stored)
    at = _index(stored, old)
    if new == old:
        return user_colortables.normalize(stored[at])
    if new in names:
        raise user_colortables.NameTaken(new)
    if new in user_colortables._reserved_names():
        raise user_colortables.NameReserved(new)
    entry = dict(stored[at], name=new)
    stored[at] = entry
    user_colortables._write(stored)
    registry.unregister_user_colortable(old)
    _register(entry)
    _rename_favorite(old, new)
    _history(history_problems, f"'{new}' was renamed, but its earlier versions could not be moved to the new name",
             colortable_history.move, old, new)
    _bump()
    return entry


def duplicate_draft(name, new_name=None):
    """A copy of the table `name` under a free name (or `new_name`), as entry fields, NOT
    saved -- what "Duplicate and edit" opens the editor with.

    A table of yours is copied as it is stored, its description too. A built-in table is
    copied from the block tcviz.colortable_export writes for it, which the exporter has
    already read back, rebuilt and compared with the built-in: the copy draws the same 8-bit
    colors over the same range (a 57-color MetPy table stays 57 colors, the SAR wind table
    keeps its 0-100.85 kt). Its description starts empty: the copy is the person's own table
    now, theirs to describe (a "Copy of ..." line only had to be deleted by hand)."""
    stored = user_colortables.load()
    if name in _names(stored):
        draft = user_colortables.normalize(stored[_index(stored, name)])
    else:
        kind = kind_of(name)
        if kind is None:
            raise ValueError(f"'{name}' is not an infrared, water-vapor, wind or radar color table, so it "
                             "cannot be copied into one of yours.")
        from . import colortable_export
        fields = user_colortables.parse_shareable(colortable_export.export_block(name))
        if fields.get("units", "C") == "C":
            draft = {"vmin_c": fields["vmin_c"], "vmax_c": fields["vmax_c"]}
        else:
            draft = {"units": fields["units"], "vmin": fields["vmin"], "vmax": fields["vmax"]}
        draft.update({"stops": fields["stops"], "reversed": fields.get("reversed_", True),
                      "levels": fields.get("levels", user_colortables.DEFAULT_LEVELS),
                      "description": "",
                      "category": _CATEGORY_OF_KIND[kind], "hidden": False})
    draft["name"] = new_name or free_name(name)
    return draft


def duplicate(name, new_name=None):
    """Save a copy of the table `name` (yours or a built-in IR / water-vapor one) under a
    free name, or `new_name`; returns the copy's entry. A copy of one of yours goes right
    after it in the list."""
    draft = duplicate_draft(name, new_name)
    _check_name(draft["name"])
    entry = _make(draft)
    stored = user_colortables.load()
    names = _names(stored)
    if entry["name"] in names:
        raise user_colortables.NameTaken(entry["name"])
    if entry["name"] in user_colortables._reserved_names():
        raise user_colortables.NameReserved(entry["name"])
    stored.insert(names.index(name) + 1 if name in names else len(stored), entry)
    user_colortables._write(stored)
    # a copy starts with no earlier versions, whatever a gone table of its name left
    _history(None, "", colortable_history.forget, entry["name"])
    _register(entry)
    _bump()
    return entry


def set_hidden(name, hidden):
    """Hide the table `name` from every picker, or show it again; returns its entry. A
    hidden table still draws by name."""
    stored = user_colortables.load()
    at = _index(stored, name)
    entry = dict(stored[at])
    if entry.get("category", "") is None:
        # the legacy spelling of a hidden IR table: once shown, it has to be an IR one
        entry["category"] = "Temperature (IR)"
    entry["hidden"] = bool(hidden)
    stored[at] = entry
    user_colortables._write(stored)
    _register(entry)
    _bump()
    return entry


def delete(name):
    """Take the table `name` out of the store and the registry, keeping it in the
    recently deleted list; returns its Deleted record. A built-in it was standing in for
    is used again under that name."""
    stored = user_colortables.load()
    at = _index(stored, name)
    before = _read_deleted()
    record = {"id": uuid.uuid4().hex, "deleted_at": datetime.datetime.now().isoformat(timespec="seconds"),
              "position": at, "entry": stored[at]}
    # the deleted list first: whatever fails after this, the table is still somewhere
    _write_deleted((before + [record])[-DELETED_KEEP:])
    try:
        user_colortables._write(stored[:at] + stored[at + 1:])
    except BaseException:
        with contextlib.suppress(OSError):
            _write_deleted(before)
        raise
    # its earlier versions wait for a restore (a new table of its name starts with none)
    _history(None, "", colortable_history.set_aside, name, record["id"],
             [r["id"] for r in (before + [record])[-DELETED_KEEP:]])
    registry.unregister_user_colortable(name)
    _bump()
    return _as_deleted(record)


def deleted_tables():
    """The recently deleted tables, newest first."""
    return [_as_deleted(r) for r in reversed(_read_deleted())]


def restore(deleted_id=None, history_problems=None):
    """Put a deleted table back where it was in the list -- the newest one, or the one with
    this id; returns its entry. If a table of that name has been made since, the restored
    one is named <name>_restored (then _restored2, ...) instead of replacing it. Its
    earlier versions come back with it, under the name it comes back as."""
    records = _read_deleted()
    if not records:
        raise NotFound("There is no recently deleted table to bring back.")
    if deleted_id is None:
        record = records[-1]
    else:
        record = next((r for r in records if r["id"] == deleted_id), None)
        if record is None:
            raise NotFound("That table is no longer in the recently deleted list.")
    stored = user_colortables.load()
    names = _names(stored)
    entry = dict(record["entry"])
    if entry["name"] in names:
        entry["name"] = _free(entry["name"], "restored", set(names))
    stored.insert(min(int(record.get("position", len(stored))), len(stored)), entry)
    user_colortables._write(stored)
    with contextlib.suppress(OSError):
        _write_deleted([r for r in records if r is not record])
    _history(history_problems, f"'{entry['name']}' is back, but its earlier versions could not be brought back "
             "with it", colortable_history.bring_back, record["id"], entry["name"])
    _register(entry)
    _bump()
    return entry


def free_name(base):
    """A name for a copy of `base` that no table, yours or built in, has: <base>_copy, then
    <base>_copy2, ... A name a table cannot have (one with capitals) is lowercased and tidied
    first, and a copy of a copy counts on from the original (bd_copy -> bd_copy2)."""
    stem = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_")
    stem = re.sub(r"_copy\d*$", "", stem) or "table"
    if not stem[0].isalpha():
        stem = f"t_{stem}"
    return _free(stem, "copy", set(_names(user_colortables.load())))


# --------------------------------------------------------------------------- favorites

def _favorites_path():
    """paths.config_dir()'s favorite_palettes.json in tcviz, as it always was; beside the
    tables file in Colortable Editor, whose things are all in one folder of its own."""
    if edition.is_editor():
        return user_colortables.store_path().with_name(FAVORITES_FILE)
    # tcviz's own folders, imported here: Colortable Editor ships without tcviz.paths, which
    # also knows where tcviz keeps its sign-in file
    from . import paths
    return paths.config_dir() / FAVORITES_FILE


def load_favorites():
    """The starred palette names, or None when nothing has been starred yet (no file, or
    one that does not read): the picker then stars the default table itself."""
    try:
        names = json.loads(_favorites_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return [str(n) for n in names] if isinstance(names, list) else None


def save_favorites(names):
    path = _favorites_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    user_colortables._atomic_write_text(path, json.dumps(list(names)))


def _rename_favorite(old, new):
    names = load_favorites()
    if not names or old not in names:
        return
    renamed = []
    for n in names:
        n = new if n == old else n
        if n not in renamed:
            renamed.append(n)
    # the table itself is already renamed; a star that could not follow it is not worth
    # failing the rename over
    with contextlib.suppress(OSError):
        save_favorites(renamed)


# --------------------------------------------------------------------------- inside

def _check_name(name):
    try:
        user_colortables.validate_name(name)
    except ValueError:
        raise ValueError(_NAME_RULE) from None


def _names(stored):
    return [e.get("name") if isinstance(e, dict) else None for e in stored]


def _index(stored, name):
    names = _names(stored)
    if name not in names:
        raise NotFound(f"There is no table of yours called '{name}'.")
    return names.index(name)


def _make(draft):
    units = user_colortables.normalize(draft)["units"]
    span = ({"vmin_c": draft["vmin_c"], "vmax_c": draft["vmax_c"]} if units == "C"
            else {"units": units, "vmin": draft["vmin"], "vmax": draft["vmax"]})
    return user_colortables.make_entry(
        draft["name"], draft["stops"],
        description=draft.get("description", ""), category=draft.get("category", "Temperature (IR)"),
        reversed_=draft.get("reversed", True), levels=draft.get("levels", user_colortables.DEFAULT_LEVELS),
        hidden=draft.get("hidden", False), **span)


def _register(entry):
    """Make a stored table live -- or, one that cannot be drawn, take any older version of
    it out of the registry, as the startup load leaves such a table out."""
    try:
        user_colortables.build_cmap(entry)
    except Exception:
        registry.unregister_user_colortable(entry.get("name"))
        return False
    registry.register_user_colortable(entry)
    return True


def _bump():
    # register/unregister bump it too; this makes sure every call does, even one that
    # touched no registration (a table that cannot be drawn)
    registry.bump()


def _history(problems, sentence, change, *args):
    """One change to the history file (colortable_history), after the table's own change is
    saved: whatever goes wrong there is not worth failing that over. `problems`, when a
    list, gets `sentence` and why."""
    try:
        change(*args)
    except Exception as e:
        if problems is not None:
            problems.append(f"{sentence}: {colortable_history.plain_reason(e)}.")


def _free(stem, word, stored_names):
    taken = (stored_names | set(registry.palettes()) | set(registry.registered_builders())
             | set(user_colortables._reserved_names()))
    for n in itertools.count(1):
        candidate = f"{stem}_{word}" if n == 1 else f"{stem}_{word}{n}"
        if candidate not in taken:
            return candidate


def _deleted_path():
    store = user_colortables.store_path()
    return store.with_name(f"{store.stem}.deleted{store.suffix}")


def _read_deleted():
    """The deleted list, oldest first. One that does not read is started over: it only
    holds the undo history, never a table that is still in use."""
    try:
        records = json.loads(_deleted_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict) and isinstance(r.get("entry"), dict)
            and isinstance(r["entry"].get("name"), str) and isinstance(r.get("id"), str)]


def _write_deleted(records):
    path = _deleted_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    user_colortables._atomic_write_text(path, json.dumps(records, indent=2))


def _as_deleted(record):
    try:
        when = datetime.datetime.fromisoformat(record.get("deleted_at", ""))
    except (TypeError, ValueError):
        when = None
    return Deleted(record["id"], record["entry"]["name"], record["entry"], when)
