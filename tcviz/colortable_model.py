"""A color table as the visual editor holds it: stops at temperatures.

The store keeps a table as positions from 0 to 1 -- measured from the warm end when the
table is reversed, as nearly every one is (user_colortables.build_cmap) -- plus the warm
and cold ends of its range. The editor shows the same table the way the picture's color
scale does: each stop at a temperature in degrees C, warm at the top. A hard step is two
stops at one temperature, the first carrying the color on the warm side of it.

Under the temperatures the model keeps each stop's stored position, untouched until that
stop is moved, so a table opened and saved again draws exactly the 8-bit colors it drew
before (a position turned into degrees and back can come out one bit off, and a stop
landing on a row of the 256-row table can then change a color). It is also what makes a
change of range stretch the table, as it always has: the positions stay, and every
temperature moves with the ends.

Water-vapor bands are always drawn from 0 to -90 C, whatever range a table carries
(engine.common.drawing._wv_palette_range), so a water-vapor table is held at that range
here: anything else would show temperatures the picture never uses.

A table is one of four kinds: infrared and water vapor, in degrees C; winds, in knots; and
radar, in dBZ (KIND_UNITS). Every value here -- a stop, the range -- is in the kind's own
units; the engine's (Kelvin, m/s) come only from build(), through
user_colortables.build_cmap. A winds or radar table reads high at the top, as its picture's
scale does, and a new one runs from its low end (not reversed), as a GRLevelX .pal does.
Changing the kind keeps the stops in their places and gives the table the range it last
had in the new kind's units (or that kind's default range).

Every change goes through a method that either does it or raises EditRefused with a
sentence for the person using the app; state() and restore() take and put back a
snapshot, which is what the editor's undo keeps. Qt-free.
"""
import bisect
import itertools
import math
from collections import namedtuple

from matplotlib.colors import to_hex, to_rgb

from . import user_colortables

KIND_CATEGORIES = {"ir": "Temperature (IR)", "wv": "Water Vapor",
                   "wind": user_colortables.UNIT_CATEGORIES["kt"],
                   "radar": user_colortables.UNIT_CATEGORIES["dBZ"]}
_KIND_OF_CATEGORY = {category: kind for kind, category in KIND_CATEGORIES.items()}
KIND_UNITS = {"ir": "C", "wv": "C", "wind": "kt", "radar": "dBZ"}
_KIND_OF_UNITS = {"kt": "wind", "dBZ": "radar"}
UNIT_LABELS = {"C": "°C", "kt": "kt", "dBZ": "dBZ"}
# What a value of each unit is, for the sentences a refused change gets.
_QUANTITY = {"C": "temperature", "kt": "wind speed", "dBZ": "reflectivity"}
# (cold, warm) in degrees C: what every water-vapor band is drawn at
WV_RANGE = (-90.0, 0.0)
DEFAULT_RANGE = user_colortables.DEFAULT_RANGES["C"]
# How close a stop that is not an end may come to either end, in degrees C. Two stops at
# the very top position cannot be told apart by the nudge build_cmap gives a hard step
# (there is no room past the end), so the ends keep a little room to themselves.
EDGE_ROOM = 0.01
# Temperatures a person moves a stop to are kept to this many decimals, so a stop moved
# 1 degree from 48.99995 lands on 50 rather than on 49.99995000000001.
_DECIMALS = 4

Stop = namedtuple("Stop", "id pos color")
Stop.__doc__ = """One stop as stored: `pos` 0..1 (from the warm end when reversed), `color`
"#rrggbb", `id` a number that stays with the stop through moves, undo and redo."""

StopView = namedtuple("StopView", "id value color end step")
StopView.__doc__ = """One stop as the editor shows it: `value` in degrees C, `end` whether it is
one of the stops held at the ends of the range, `step` "warm" or "cold" for the two halves
of a hard step (None otherwise)."""

State = namedtuple("State", "stops vmin vmax kind reversed levels ranges")
State.__doc__ = """Everything an undo step puts back: the stops, the range, the kind, the
direction, the number of colors, and the range the table last had in each unit -- what it
returns to when its kind changes back (a water-vapor table made infrared again gets back its
infrared range)."""


def kind_of_entry(entry):
    """"ir", "wv", "wind" or "radar" for a stored table (normalized or not)."""
    entry = user_colortables.normalize(entry)
    return _KIND_OF_UNITS.get(entry["units"]) or _KIND_OF_CATEGORY.get(entry.get("category"), "ir")


class EditRefused(ValueError):
    """A change the table cannot take; the message says why in plain words."""


_ids = itertools.count(1)


def _hex(color):
    """"#rrggbb" for any color matplotlib reads. Exact for every color a table can hold:
    each is 8 bits a channel, so to_rgb and back lands on the same bytes."""
    return to_hex(to_rgb(color))


class ColortableModel:
    """One table being edited. Build one with from_entry (a stored table, or a
    duplicate_draft) or new_table."""

    def __init__(self, stops, vmin, vmax, *, kind="ir", reversed_=True, levels=user_colortables.DEFAULT_LEVELS,
                 name="", description="", hidden=False):
        """`stops` as stored: [(position 0..1, color), ...] in any order (a repeated
        position is a hard step, its stops in the order given)."""
        if kind not in KIND_CATEGORIES:
            raise ValueError(f"kind must be one of {sorted(KIND_CATEGORIES)}")
        self.name, self.description, self.hidden = name, description, bool(hidden)
        self.levels = int(levels)
        self.reversed = bool(reversed_)
        self.kind = kind
        self.vmin, self.vmax = float(vmin), float(vmax)
        # sorted the way build_cmap sorts them -- stably -- so the order of a hard step's
        # two stops is the order they were stored in
        self._stops = [Stop(next(_ids), float(p), _hex(c))
                       for p, c in sorted(((float(p), c) for p, c in stops), key=lambda s: s[0])]
        if len(self._stops) < 2:
            raise ValueError("need at least 2 color stops")
        # bumped by every change; ordered() is asked for on every repaint and every cell of
        # the stop list, so it is worked out once per change
        self.version = 0
        self._ordered = (None, [])
        # the range last had in each unit, for a change of kind (a water-vapor table's is
        # the one it was stored with: an infrared picture would use it)
        self._ranges = {KIND_UNITS[kind]: (self.vmin, self.vmax)}
        # The range a water-vapor table was stored with, when it was not 0 to -90 C and has
        # been put there (the editor says so); None otherwise.
        self.stored_range = None
        if kind == "wv" and (self.vmin, self.vmax) != WV_RANGE:
            self.stored_range = (self.vmin, self.vmax)
            self.vmin, self.vmax = WV_RANGE

    # ------------------------------------------------------------------ making one

    @classmethod
    def from_entry(cls, entry):
        """The model of a stored table (or a duplicate_draft, or any dict with its fields)."""
        entry = user_colortables.normalize(entry)
        return cls(entry["stops"], entry["vmin"], entry["vmax"], kind=kind_of_entry(entry),
                   reversed_=entry.get("reversed", True), levels=entry.get("levels", user_colortables.DEFAULT_LEVELS),
                   name=entry.get("name", ""), description=entry.get("description", ""),
                   hidden=entry.get("hidden", False))

    @classmethod
    def new_table(cls, name="", kind="ir"):
        """A plain starting point: black at the warm end to white at the cold end, the way
        an infrared picture is read (cold cloud tops bright) -- and for winds or radar,
        black at the low end to white at the high end (strong winds, heavy rain bright)."""
        if kind in ("ir", "wv"):
            vmin, vmax = WV_RANGE if kind == "wv" else DEFAULT_RANGE
            return cls([(0.0, "#000000"), (1.0, "#ffffff")], vmin, vmax, kind=kind, name=name)
        vmin, vmax = user_colortables.DEFAULT_RANGES[KIND_UNITS[kind]]
        return cls([(0.0, "#000000"), (1.0, "#ffffff")], vmin, vmax, kind=kind, name=name, reversed_=False)

    @classmethod
    def from_values(cls, stops, vmin, vmax, **fields):
        """A model from stops given as (degrees C, color), for a reversed table."""
        span = vmax - vmin
        return cls([((vmax - v) / span, c) for v, c in stops], vmin, vmax, **fields)

    # ------------------------------------------------------------------ the stored form

    def to_entry(self):
        """The table as user_colortables stores it (and colortable_library.save takes). A
        winds or radar table has its range as vmin/vmax in its units, and no Celsius pair."""
        entry = {"name": self.name, "stops": [(s.pos, s.color) for s in self._stops]}
        if self.units == "C":
            entry.update(vmin_c=self.vmin, vmax_c=self.vmax)
        else:
            entry.update(units=self.units, vmin=self.vmin, vmax=self.vmax)
        entry.update(description=self.description, category=KIND_CATEGORIES[self.kind], reversed=self.reversed,
                     levels=self.levels, hidden=self.hidden)
        return entry

    def save_fields(self):
        """colortable_library.save's keyword fields for this table (all but name and stops)."""
        if self.units == "C":
            fields = {"vmin_c": self.vmin, "vmax_c": self.vmax}
        else:
            fields = {"units": self.units, "vmin": self.vmin, "vmax": self.vmax}
        fields.update(description=self.description, category=KIND_CATEGORIES[self.kind], reversed_=self.reversed,
                      levels=self.levels, hidden=self.hidden)
        return fields

    def stored_stops(self):
        return [(s.pos, s.color) for s in self._stops]

    def build(self):
        """(cmap, vmax_K, vmin_K) exactly as the app will draw this table
        (user_colortables.build_cmap); ValueError when it cannot be drawn."""
        return user_colortables.build_cmap(self.to_entry())

    def problem(self):
        """None, or why the table cannot be drawn, in plain words."""
        warm, cold = self._end_positions()
        if not any(s.pos == warm for s in self._stops):
            return "there is no stop at the top of the range; drag the highest stop up to the top"
        if not any(s.pos == cold for s in self._stops):
            return "there is no stop at the bottom of the range; drag the lowest stop down to the bottom"
        try:
            self.build()
        except ValueError as e:
            return str(e)
        return None

    def _end_positions(self):
        """(position of the warm end, position of the cold end)."""
        return (0.0, 1.0) if self.reversed else (1.0, 0.0)

    # ------------------------------------------------------------------ reading

    @property
    def span(self):
        return self.vmax - self.vmin

    @property
    def units(self):
        """"C", "kt" or "dBZ": what every value of this table is in."""
        return KIND_UNITS[self.kind]

    @property
    def unit_label(self):
        """The units as a person reads them: °C, kt, dBZ."""
        return UNIT_LABELS[self.units]

    def __len__(self):
        return len(self._stops)

    def value_of_pos(self, pos):
        return self.vmax - pos * self.span if self.reversed else self.vmin + pos * self.span

    def pos_of_value(self, value):
        return (self.vmax - value) / self.span if self.reversed else (value - self.vmin) / self.span

    def ordered(self):
        """Every stop, warm to cold, as StopView rows. The two stops of a hard step come
        warm side first."""
        if self._ordered[0] != self.version:
            self._ordered = (self.version, self._views())
        return self._ordered[1]

    def _views(self):
        stops = self._stops if self.reversed else self._stops[::-1]
        out = []
        for i, s in enumerate(stops):
            tied_before = i > 0 and stops[i - 1].pos == s.pos
            tied_after = i + 1 < len(stops) and stops[i + 1].pos == s.pos
            step = "cold" if tied_before else "warm" if tied_after else None
            out.append(StopView(s.id, self.value_of_pos(s.pos), s.color, s.pos in (0.0, 1.0), step))
        return out

    @property
    def stops(self):
        """[(degrees C, "#rrggbb"), ...] warm to cold; a hard step is two at one value."""
        return [(v.value, v.color) for v in self.ordered()]

    def ids(self):
        return [v.id for v in self.ordered()]

    def get(self, sid):
        """The StopView of stop `sid`."""
        for view in self.ordered():
            if view.id == sid:
                return view
        raise KeyError(sid)

    def _touch(self):
        self.version += 1

    def _at(self, sid):
        for i, s in enumerate(self._stops):
            if s.id == sid:
                return i
        raise KeyError(sid)

    def is_end(self, sid):
        return self._stops[self._at(sid)].pos in (0.0, 1.0)

    def color_at(self, value):
        """The color the stops give at this temperature (straight-line between the two
        stops either side, as the table is drawn), as "#rrggbb". On a hard step, the
        cold side's."""
        p = min(max(self.pos_of_value(value), 0.0), 1.0)
        positions = [s.pos for s in self._stops]
        j = bisect.bisect_right(positions, p)
        if j == 0:
            return self._stops[0].color
        if j == len(self._stops):
            return self._stops[-1].color
        a, b = self._stops[j - 1], self._stops[j]
        if b.pos == a.pos:
            return b.color
        t = (p - a.pos) / (b.pos - a.pos)
        ca, cb = to_rgb(a.color), to_rgb(b.color)
        return to_hex(tuple(x + (y - x) * t for x, y in zip(ca, cb)))

    def stop_near(self, value, tolerance):
        """The stop closest to `value` within `tolerance` degrees, else None."""
        best = None
        for view in self.ordered():
            d = abs(view.value - value)
            if d <= tolerance and (best is None or d < best[0]):
                best = (d, view.id)
        return best[1] if best else None

    def stop_for_value(self, value):
        """The stop to change for the color drawn at `value`: the nearer of the two stops
        either side of it. Walking the stops in order keeps a hard step's halves apart: a
        value just above the step gets the half whose color is drawn above it. A value past
        either end of the range takes that end's stop, whose color it is drawn in."""
        views = self.ordered()
        if value >= views[0].value:
            return views[0].id
        if value <= views[-1].value:
            return views[-1].id
        for warm, cold in zip(views, views[1:]):
            if warm.value > cold.value and cold.value <= value <= warm.value:
                return warm.id if warm.value - value <= value - cold.value else cold.id
        return views[-1].id

    # ------------------------------------------------------------------ changing

    def _middle_value(self, value):
        """`value` kept inside the range, clear of the two ends (EDGE_ROOM) -- or onto an
        end that has no stop of its own, which only a damaged table lacks."""
        if not math.isfinite(value):
            raise EditRefused(f"That is not a {_QUANTITY[self.units]}.")
        warm, cold = self._end_positions()
        hi = self.vmax - EDGE_ROOM if any(s.pos == warm for s in self._stops) else self.vmax
        lo = self.vmin + EDGE_ROOM if any(s.pos == cold for s in self._stops) else self.vmin
        if lo > hi:
            raise EditRefused("The range is too narrow for another stop.")
        return min(max(round(value, _DECIMALS), lo), hi)

    def add_stop(self, value, color=None):
        """A new stop at this temperature, in `color` or the color already there; returns
        its id."""
        value = self._middle_value(value)
        color = _hex(color) if color is not None else self.color_at(value)
        pos = self.pos_of_value(value)
        stop = Stop(next(_ids), pos, color)
        at = bisect.bisect_right([s.pos for s in self._stops], pos)
        self._stops.insert(at, stop)
        self._touch()
        return stop.id

    def move_stop(self, sid, value):
        """Move stop `sid` to this temperature (kept inside the range); returns where it
        went. The stops at the two ends stay there: the range moves them."""
        i = self._at(sid)
        old = self._stops[i]
        if old.pos in (0.0, 1.0):
            raise EditRefused("The stops at the top and bottom stay at the ends of the range. "
                              "Change the range to move them.")
        value = self._middle_value(value)
        pos = self.pos_of_value(value)
        if pos == old.pos:
            return self.value_of_pos(pos)
        del self._stops[i]
        positions = [s.pos for s in self._stops]
        # A stop never jumps a stop at the very position it lands on: coming from below
        # it stays below, from above it stays above -- so dragging one half of a hard step
        # away and back puts it back on its own side.
        at = bisect.bisect_left(positions, pos) if pos > old.pos else bisect.bisect_right(positions, pos)
        self._stops.insert(at, old._replace(pos=pos))
        self._touch()
        return self.value_of_pos(pos)

    def set_color(self, sid, color):
        try:
            color = _hex(color)
        except ValueError:
            raise EditRefused(f"'{color}' is not a color. Write it like #ff8800.") from None
        i = self._at(sid)
        self._stops[i] = self._stops[i]._replace(color=color)
        self._touch()

    def delete_stop(self, sid):
        i = self._at(sid)
        stop = self._stops[i]
        if len(self._stops) <= 2:
            raise EditRefused("A table needs at least two stops.")
        if stop.pos in (0.0, 1.0) and sum(s.pos == stop.pos for s in self._stops) == 1:
            raise EditRefused("The stops at the top and bottom can't be deleted; give them another "
                              "color instead.")
        del self._stops[i]
        self._touch()

    def make_hard_step(self, sid):
        """Split stop `sid` into the two halves of a hard step, both in its color for now;
        returns the id of the new, cold-side half."""
        i = self._at(sid)
        stop = self._stops[i]
        if stop.pos in (0.0, 1.0):
            # one half of it would be drawn nowhere but the end itself
            raise EditRefused("A hard step can't be made at the very top or bottom of the range.")
        new = Stop(next(_ids), stop.pos, stop.color)
        # the new half goes on the cold side: after it when reversed (positions run warm to
        # cold), before it otherwise
        self._stops.insert(i + 1 if self.reversed else i, new)
        self._touch()
        return new.id

    def hard_step_at(self, value):
        """A hard step at this temperature (two stops, both in the color there now);
        returns (warm-side id, cold-side id)."""
        sid = self.add_stop(value)
        return sid, self.make_hard_step(sid)

    def set_range(self, vmin, vmax):
        """New ends for the range. The stops keep their places along it, so the whole
        table stretches with the ends, as it always has."""
        vmin, vmax = float(vmin), float(vmax)
        if self.kind == "wv" and (vmin, vmax) != WV_RANGE:
            raise EditRefused("Water-vapor pictures are always drawn from 0 to -90 °C, so a "
                              "water-vapor table keeps that range.")
        if not (math.isfinite(vmin) and math.isfinite(vmax)):
            raise EditRefused(f"That is not a {_QUANTITY[self.units]}.")
        if vmax <= vmin:
            if self.units == "C":
                raise EditRefused("The warm end of the range has to be warmer than the cold end.")
            raise EditRefused("The top of the range has to be above the bottom.")
        if self.units == "kt" and vmin < 0:
            raise EditRefused("A wind speed can't be below 0 kt.")
        self.vmin, self.vmax = vmin, vmax
        self._touch()

    def set_kind(self, kind):
        """Infrared ("ir"), water vapor ("wv"), winds ("wind") or radar ("radar"). The stops
        stay where they are along the table. A water-vapor table takes 0 to -90 °C; any
        other kind gets back the range the table last had in its units (a water-vapor table
        made infrared again, its infrared range), else that kind's default range."""
        if kind not in KIND_CATEGORIES:
            raise ValueError(f"kind must be one of {sorted(KIND_CATEGORIES)}")
        if kind == self.kind:
            return
        if self.kind != "wv":
            # the range a water-vapor table shows is not its own: the one kept stays
            self._ranges[self.units] = (self.vmin, self.vmax)
        units = KIND_UNITS[kind]
        if kind == "wv":
            # made infrared later, it gets the infrared range it had, else the default one
            self._ranges.setdefault(units, DEFAULT_RANGE)
            self.vmin, self.vmax = WV_RANGE
        else:
            self.vmin, self.vmax = self._ranges.get(units, user_colortables.DEFAULT_RANGES[units])
        self.kind = kind
        self._touch()

    def replace_stops(self, stops, *, reversed_=None, levels=None):
        """Every stop at once, as stored ([(position, color), ...]) -- what "Type or paste
        stops" hands back."""
        if len(stops) < 2:
            raise EditRefused("A table needs at least two stops.")
        self._stops = [Stop(next(_ids), float(p), _hex(c))
                       for p, c in sorted(((float(p), c) for p, c in stops), key=lambda s: s[0])]
        if reversed_ is not None:
            self.reversed = bool(reversed_)
        if levels is not None:
            self.levels = int(levels)
        self._touch()

    # ------------------------------------------------------------------ undo

    def state(self):
        return State(tuple(self._stops), self.vmin, self.vmax, self.kind, self.reversed, self.levels,
                     tuple(sorted(self._ranges.items())))

    def restore(self, state):
        self._stops = list(state.stops)
        self.vmin, self.vmax, self.kind = state.vmin, state.vmax, state.kind
        self.reversed, self.levels, self._ranges = state.reversed, state.levels, dict(state.ranges)
        self._touch()
