"""One place the app hears that the user's color tables changed.

tcviz.colortable_library is Qt-free, so it cannot signal anything itself; whoever calls it
from the app (the Manage window, the picker's right-click menu) calls notify() afterwards,
and every page that shows tables -- the OptionsPage's palette grid -- listens on
events().changed and refreshes, keeping what is ticked.
"""
from collections import namedtuple

from PySide6.QtCore import QObject, Signal

Change = namedtuple("Change", "action name new_name", defaults=(None, None))
Change.__doc__ = """What changed: `action` is "saved", "renamed", "duplicated", "hidden",
"shown", "deleted" or "restored"; `name` the table it happened to; `new_name` its name
afterwards when that differs (a rename, a copy, a restore under a free name)."""


class LibraryEvents(QObject):
    changed = Signal(object)    # a Change


_events = None


def events():
    """The app-wide LibraryEvents, made on first use."""
    global _events
    if _events is None:
        _events = LibraryEvents()
    return _events


def notify(action, name=None, new_name=None):
    events().changed.emit(Change(action, name, new_name))
