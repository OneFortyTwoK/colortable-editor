"""Unsaved color tables, kept in case the program closes before they are saved: one draft
file per open editor, in a drafts folder beside the tables file
(user_colortables.store_path().parent / "drafts": Colortable Editor's own folder, or
~/.config/tcviz in tcviz).

The editor (tcviz_gui.pages.colortable_editor_dialog) writes its draft a moment after each
change while the table differs from what was saved, and removes it on Save, on a confirmed
Cancel, and once an undo takes the table back to what was saved. So a draft still there
when the program starts was left by an editor that never closed -- the program crashed, was
killed, or the computer lost power -- and the main window offers it back
(tcviz_gui.pages.draft_recovery_dialog).

A draft is drafts/draft-<id>.json, <id> the editor's own uuid4 hex, so editors open at the
same time never write each other's file:

    {"format": 1, "app": "Colortable Editor", "app_version": "1.0.0",
     "saved_at": "2026-10-03T14:32:05+00:00", "mode": "edit", "original": "my_storm",
     "source": null, "base": "3f2a...", "entry": {the table, as the tables file keeps one}}

`mode` is the editor's: "add" (a new table), "edit" (one of the person's, `original` its
saved name, whatever the name box says now) or "copy" (a new table copied from `source`).
`base` is a fingerprint of the saved table when the editor opened (fingerprint), so the
offer can say when that table has been saved again since. `saved_at` is UTC.

Every draft is written whole and atomically (user_colortables._atomic_write_text), so a
file is always a complete draft or none. The folder is made only when a draft is written
and goes when its last draft does. Reading never fails: a file that is not a readable draft
is passed over, with a sentence saying so (Damaged). Qt-free.
"""
import contextlib
import datetime
import hashlib
import json
import re
import uuid
from collections import namedtuple

from . import edition, user_colortables

FORMAT = 1
FOLDER_NAME = "drafts"
MODES = ("add", "edit", "copy")
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_FILE_RE = re.compile(r"^draft-([0-9a-f]{32})\.json$")

Draft = namedtuple("Draft", "id path entry mode original source when app app_version base older")
Draft.__doc__ = """One table left unsaved: `entry` the table as the editor had it, `mode`,
`original` and `source` as the editor was opened (see the module), `when` the moment it was
last written (an aware datetime in UTC), `base` the saved table's fingerprint then (None for
a new table), and `older` the paths of earlier drafts of the same saved table, which this
one supersedes (the newest wins)."""

Damaged = namedtuple("Damaged", "path text")
Damaged.__doc__ = """A file in the drafts folder that does not read as a draft: its path, and
a sentence for the person."""


# --------------------------------------------------------------------------- where

def folder():
    """The drafts folder beside the tables file, wherever that is (tcviz's own, or
    Colortable Editor's folder). Not made until a draft is written."""
    return user_colortables.store_path().parent / FOLDER_NAME


def new_id():
    """A fresh id for one editor's draft."""
    return uuid.uuid4().hex


def path_for(draft_id):
    if not isinstance(draft_id, str) or not _ID_RE.match(draft_id):
        raise ValueError(f"not a draft id: {draft_id!r}")
    return folder() / f"draft-{draft_id}.json"


def fingerprint(entry):
    """A short fingerprint of a saved table: the same for two entries that draw and read
    the same (name aside), different once its stops, range, kind, description or hiding
    changed. None for an entry that does not read."""
    try:
        e = user_colortables.normalize(entry)
        fields = {"stops": [[float(p), str(c).lower()] for p, c in e["stops"]], "units": str(e["units"]),
                  "vmin": float(e["vmin"]), "vmax": float(e["vmax"]), "reversed": bool(e.get("reversed", True)),
                  "levels": e["levels"], "description": str(e.get("description") or ""),
                  "category": e["category"], "hidden": bool(e["hidden"])}
        text = json.dumps(fields, sort_keys=True)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


# --------------------------------------------------------------------------- writing

def write(draft_id, entry, mode, original=None, source=None, base=None, now=None):
    """Keep `entry`, an editor's unsaved table, as the draft `draft_id`; returns its path.
    Raises OSError when it cannot be written (and ValueError or TypeError for an entry
    that is not a table): the editor says so once and carries on."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if not (isinstance(entry, dict) and isinstance(entry.get("stops"), (list, tuple))):
        raise ValueError("a draft keeps a table entry")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    data = {"format": FORMAT, "app": edition.app_name(), "app_version": _app_version(),
            "saved_at": now.astimezone(datetime.timezone.utc).isoformat(timespec="seconds"), "mode": mode,
            "original": original if mode == "edit" else None, "source": source, "base": base,
            "entry": entry}
    text = json.dumps(data, indent=1, allow_nan=False) + "\n"
    path = path_for(draft_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    user_colortables._atomic_write_text(path, text)
    return path


def remove(draft_id):
    """The editor's table was saved or let go: its draft goes (and the folder, once empty)."""
    with contextlib.suppress(FileNotFoundError):
        path_for(draft_id).unlink()
    _tidy()


def discard(item):
    """Remove a Draft -- with the earlier drafts it supersedes -- or a Damaged file."""
    paths = (item.path, *getattr(item, "older", ()))
    for path in paths:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
    _tidy()


def drop_older(draft):
    """The earlier drafts of the same saved table go: `draft` is the one being kept."""
    for path in draft.older:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
    _tidy()


def _tidy():
    """The drafts folder goes once nothing is left in it."""
    with contextlib.suppress(OSError):
        folder().rmdir()


# --------------------------------------------------------------------------- reading

def leftovers(damaged=None):
    """The drafts left behind, newest first: one per saved table (the newest wins; the
    others are in its `older`), and every new, never-saved table on its own. A file that
    does not read as a draft is passed over; when `damaged` is a list, a Damaged for it is
    added there. [] when there is no drafts folder."""
    try:
        files = sorted(p for p in folder().iterdir() if _FILE_RE.match(p.name) and p.is_file())
    except OSError:
        return []
    drafts = []
    for path in files:
        draft = _read(path)
        if draft is None:
            if damaged is not None:
                damaged.append(Damaged(path, f"The unsaved changes in {path.name} could not be read, so they "
                                             "can't be opened."))
            continue
        drafts.append(draft)
    oldest = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    drafts.sort(key=lambda d: (d.when or oldest, d.id), reverse=True)
    out, seen = [], {}
    for draft in drafts:
        key = ("edit", draft.original) if draft.mode == "edit" else ("new", draft.id)
        if key in seen:
            kept = out[seen[key]]
            out[seen[key]] = kept._replace(older=kept.older + (draft.path, *draft.older))
            continue
        seen[key] = len(out)
        out.append(draft)
    return out


def _read(path):
    """The Draft at `path`, or None when it does not read as one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return None
    entry, mode, original = data.get("entry"), data.get("mode"), data.get("original")
    if mode not in MODES or not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
        return None
    if not isinstance(entry.get("stops"), list) or not entry["stops"]:
        return None
    if mode == "edit" and not (isinstance(original, str) and original):
        return None
    try:
        user_colortables.normalize(entry)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    source, base = data.get("source"), data.get("base")
    return Draft(_FILE_RE.match(path.name).group(1), path, entry, mode, original if mode == "edit" else None,
                 source if isinstance(source, str) and source else None, _when(data.get("saved_at"), path),
                 str(data.get("app") or ""), str(data.get("app_version") or ""),
                 base if isinstance(base, str) and base else None, ())


def _when(text, path):
    """The draft's time, from the file (UTC); its file's own time if that does not read."""
    try:
        when = datetime.datetime.fromisoformat(text)
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        return when.astimezone(datetime.timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.timezone.utc)
    except OSError:
        return None


def saved_since(draft):
    """For a draft of a saved table: "changed" when that table has been saved again since
    the editor opened on it, "gone" when no table of the person's has that name any more,
    else None (a new table, or the table as it was)."""
    if draft.mode != "edit" or not draft.original:
        return None
    try:
        stored = user_colortables.load()
    except Exception:  # noqa: BLE001 -- only a remark in the offer: the store's own problems are said at start
        return None
    for raw in stored:
        if isinstance(raw, dict) and raw.get("name") == draft.original:
            if draft.base is None:
                return None
            return None if fingerprint(raw) == draft.base else "changed"
    return "gone"


# --------------------------------------------------------------------------- words

def when_words(when, now=None):
    """"today at 14:32", "yesterday at 21:05" or "on 2026-09-30 at 10:11", on this
    computer's clock (the file keeps UTC). `now` is for tests."""
    if when is None:
        return "at an unknown time"
    local = when.astimezone()
    today = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone().date()
    clock = f"{local.hour:02d}:{local.minute:02d}"
    if local.date() == today:
        return f"today at {clock}"
    if local.date() == today - datetime.timedelta(days=1):
        return f"yesterday at {clock}"
    return f"on {local.year:04d}-{local.month:02d}-{local.day:02d} at {clock}"


def describe(draft, now=None):
    """The draft in a few words, as the offer lists it: "my_storm (changed today at
    14:32)", "my_storm, renamed storm2 (...)" or "new table 'my_table' (...)"."""
    name = draft.entry.get("name", "").strip()
    changed = f"(changed {when_words(draft.when, now)})"
    if draft.mode == "edit":
        if name and name != draft.original:
            return f"{draft.original}, renamed {name} {changed}"
        return f"{draft.original} {changed}"
    if not name:
        return f"new table with no name yet {changed}"
    return f"new table '{name}' {changed}"


def remark(draft):
    """A sentence about the saved table since the draft was made, or "" (saved_since)."""
    since = saved_since(draft)
    if since == "changed":
        return (f"{draft.original} has been saved again since these changes were made. Saving them replaces "
                "that newer version, which History keeps.")
    if since == "gone":
        return f"There is no longer a table called {draft.original} in your list; Save adds these changes as a new one."
    return ""


def _app_version():
    """The editor's version: the same code runs in tcviz and in Colortable Editor."""
    try:
        from colortable_editor import __version__
    except ImportError:
        return ""
    return __version__
