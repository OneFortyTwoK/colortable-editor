"""The color tables there are, by name: what the color table editor, the Manage window and
the importer look tables up in, without loading tcviz's whole collection to do it.

Light on purpose: numpy, matplotlib.colors and tcviz.user_colortables, nothing else at
import. It used to live in tcviz.colormaps, which holds the code and numbers of every one of
tcviz's 229 infrared and water-vapor tables (and its microwave, wind and radar ones);
Colortable Editor, the editor published on its own, must neither carry those nor wait for
them, and has six built-in tables of its own.

So the registry's working parts are here, and its contents -- "a registry" below -- are
whichever one the edition running (tcviz.edition) uses, looked up on every call:

* tcviz: tcviz.colormaps itself. Its module-level names are the registry, exactly as they
  always were (_REGISTERED_COLORTABLES, PALETTES, PALETTE_DESCRIPTIONS, generation, ...),
  so every `colormaps.X` in tcviz and the bot -- and every test that patches one -- sees
  and changes the same thing as before. Asking for it imports colormaps, which registers
  every built-in and then the person's own tables, as it always has.
* Colortable Editor: a Tables object with those same names, holding the six tables of
  colortable_editor/builtin_tables.json and then the person's own tables.

get, palettes, register_user_colortable and the rest below work on the registry in use;
the functions that take a registry (register_user, snapshot_builtins, load_user_tables,
get_registered) are what colormaps runs on itself at import.
"""
import itertools
import json

from . import edition, user_colortables

# Each Tables gets its own number, for cache_key: id() can be handed to a new one once an
# old one is gone.
_SERIALS = itertools.count(1)


def _fix_duplicate_stops(stops):
    """matplotlib >=3.9 requires strictly increasing positions; nudge exact
    duplicates by an epsilon so repeated stops still produce a near-hard color-step edge.
    """
    eps = 1e-6
    fixed = []
    prev = -1.0
    for pos, color in stops:
        if pos <= prev:
            pos = prev + eps
        fixed.append((pos, color))
        prev = pos
    return fixed


class Tables:
    """A registry of its own, under the names tcviz.colormaps keeps its in (see the
    module): Colortable Editor's."""

    def __init__(self):
        self._REGISTERED_COLORTABLES = {}   # name -> builder() -> (cmap, vmax, vmin)
        self._REGISTERED_DESCRIPTIONS = {}
        self._REGISTERED_CATEGORIES = {}
        self.PALETTES = []
        self.PALETTE_DESCRIPTIONS = {}
        self.generation = 0
        self._BUILTIN_NAMES = frozenset()
        self._BUILTIN_REGISTRY = {}
        self._BUILTIN_DESCRIPTIONS = {}
        self.USER_TABLE_PROBLEMS = []
        # name -> the built-in's entry, as builtin_tables.json gives it (normalized)
        self.BUILTIN_ENTRIES = {}
        self.serial = next(_SERIALS)

    def get(self, name, vmin=None, vmax=None):
        """colormaps.get for this registry: (cmap, None, vmax, vmin)."""
        found = get_registered(self, name, vmin, vmax)
        if found is None:
            raise ValueError(f"Unknown palette {name!r}; expected one of {sorted(self.PALETTES)}")
        return found


_edition_tables = None


def space():
    """The registry in use (see the module): tcviz.colormaps, or Colortable Editor's own,
    filled the first time it is asked for."""
    if not edition.is_editor():
        from . import colormaps
        return colormaps
    global _edition_tables
    if _edition_tables is None:
        _edition_tables = _load_edition_tables()
    return _edition_tables


def forget_edition_tables():
    """Drop Colortable Editor's registry, so the next look reads the built-ins and the
    person's tables again (a test that moved the tables file)."""
    global _edition_tables
    _edition_tables = None


# ------------------------------------------------------------------- registering

def register_builder(tables, name, builder, description="", category="Temperature (IR)"):
    """Put one table into `tables` -- what colormaps' @colortable does."""
    tables._REGISTERED_COLORTABLES[name] = builder
    tables._REGISTERED_DESCRIPTIONS[name] = description
    tables._REGISTERED_CATEGORIES[name] = category


def register_user(tables, entry):
    """Register one stored user colortable (a tcviz.user_colortables entry dict) into the
    same registry the built-in curves use, so it's live immediately -- used both at import
    and by the GUI right after a new one is saved, so it shows up without a restart. Also
    folds its name into PALETTES/PALETTE_DESCRIPTIONS.

    A hidden table (`hidden`, or the legacy `category: null`) registers with category None,
    which keeps it out of every picker (products.categorize_palettes, hidden_palettes) while
    get() still draws it by name. It used to register as "Temperature (IR)" -- so the
    dialog's "Hidden" hid nothing."""
    entry = user_colortables.normalize(entry)
    name = entry["name"]

    def builder(e=entry):
        return user_colortables.build_cmap(e)
    builder.__name__ = name
    # the qualname it had when this lived in colormaps.register_user_colortable:
    # tcviz.colortable_export (and its tests) tell a user table's builder by it
    builder.__qualname__ = "register_user_colortable.<locals>.builder"
    # marks this registration as a user table, the only kind unregister_user removes
    builder.user_entry = entry
    category = None if entry["hidden"] else entry["category"]
    register_builder(tables, name, builder, entry.get("description", ""), category)
    tables.PALETTE_DESCRIPTIONS[name] = entry.get("description", "")
    if name not in tables.PALETTES:
        tables.PALETTES.append(name)
    tables.generation += 1
    return name


def unregister_user(tables, name):
    """Take a user table out of the registry again -- for one deleted or renamed while the
    app runs. Returns True if one was removed.

    A user table saved under a built-in's name (a few of the user's own were later built in
    under the same name) shadowed that built-in; the built-in comes back exactly as it was
    registered at import. Anything that is not a user table -- a built-in, an unknown name
    -- is left alone and False is returned."""
    if getattr(tables._REGISTERED_COLORTABLES.get(name), "user_entry", None) is None:
        return False
    if name in tables._BUILTIN_REGISTRY:
        builder, description, category = tables._BUILTIN_REGISTRY[name]
        tables._REGISTERED_COLORTABLES[name] = builder
        tables._REGISTERED_DESCRIPTIONS[name] = description
        tables._REGISTERED_CATEGORIES[name] = category
    else:
        for registry in (tables._REGISTERED_COLORTABLES, tables._REGISTERED_DESCRIPTIONS,
                         tables._REGISTERED_CATEGORIES):
            registry.pop(name, None)
    if name in tables._BUILTIN_DESCRIPTIONS:
        tables.PALETTE_DESCRIPTIONS[name] = tables._BUILTIN_DESCRIPTIONS[name]
    else:
        tables.PALETTE_DESCRIPTIONS.pop(name, None)
    if name not in tables._BUILTIN_NAMES and name in tables.PALETTES:
        tables.PALETTES.remove(name)
    tables.generation += 1
    return True


def snapshot_builtins(tables):
    """Record what the program itself defines (_BUILTIN_NAMES and the rest) before any user
    table can register over it."""
    tables._BUILTIN_NAMES = frozenset(tables.PALETTES) | frozenset(tables._REGISTERED_COLORTABLES)
    tables._BUILTIN_REGISTRY.clear()
    tables._BUILTIN_REGISTRY.update({name: (builder, tables._REGISTERED_DESCRIPTIONS.get(name, ""),
                                            tables._REGISTERED_CATEGORIES.get(name))
                                     for name, builder in tables._REGISTERED_COLORTABLES.items()})
    tables._BUILTIN_DESCRIPTIONS.clear()
    tables._BUILTIN_DESCRIPTIONS.update(tables.PALETTE_DESCRIPTIONS)


def user_table_failure(error):
    """What went wrong with one stored table, in words for the person using the app."""
    if isinstance(error, KeyError):
        missing = {"stops": "color stops", "name": "name", "vmin_c": "cold end",
                   "vmax_c": "warm end", "vmin": "cold end", "vmax": "warm end"}
        key = error.args[0] if error.args else ""
        return f"it has no {missing.get(key, repr(key))}"
    if isinstance(error, ValueError) and str(error):
        return str(error).rstrip(".")
    return "its settings could not be read"


def load_user_tables(tables):
    """Register every colortable the user saved from the GUI (tcviz.user_colortables) into
    `tables`, so they're available as normal palettes. Isolated per-entry so one bad stored
    table can't break the rest of the registry -- and each one that fails is named in
    USER_TABLE_PROBLEMS, with why, instead of vanishing without a word. Each table is
    built here, not only on first use: matplotlib checks stops only when it first draws,
    so a broken table would otherwise register fine and fail with a picture.

    In Colortable Editor a stored table of a kind it does not offer (a winds or radar table
    copied in by hand) is left out, the way the Manage list leaves it out."""
    tables.USER_TABLE_PROBLEMS.clear()
    try:
        report = user_colortables.load_report()
    except Exception as e:
        tables.USER_TABLE_PROBLEMS.append(f"Your own color tables could not be loaded: {user_table_failure(e)}.")
        return
    tables.USER_TABLE_PROBLEMS.extend(report.problems)
    for number, entry in enumerate(report.entries, 1):
        name = entry.get("name") if isinstance(entry, dict) else None
        try:
            if not isinstance(entry, dict):
                raise ValueError("it is not a color table")
            user_colortables.build_cmap(entry)
            if isinstance(tables, Tables) and not offered(entry):
                continue
            register_user(tables, entry)
        except Exception as e:
            which = f"The table '{name}'" if isinstance(name, str) and name else f"Table number {number}"
            tables.USER_TABLE_PROBLEMS.append(f"{which} could not be loaded: {user_table_failure(e)}.")


def get_registered(tables, name, vmin=None, vmax=None):
    """(cmap, None, vmax, vmin) for a table registered in `tables`, or None. vmin/vmax, if
    given, stand in for the table's own range."""
    builder = tables._REGISTERED_COLORTABLES.get(name)
    if builder is None:
        return None
    cmap, own_vmax, own_vmin = builder()
    return cmap, None, (own_vmax if vmax is None else vmax), (own_vmin if vmin is None else vmin)


# ------------------------------------------------------------- Colortable Editor's own

def builtin_table_file():
    """colortable_editor/builtin_tables.json, read: {"tables": [entry, ...], ...}."""
    return json.loads(edition.builtin_tables_path().read_text(encoding="utf-8"))


def entry_kind(entry):
    """"ir", "wv", "wind" or "radar" for a stored table: its units say winds or radar, else
    its category says water vapor or (anything else) infrared."""
    entry = user_colortables.normalize(entry)
    units = {"kt": "wind", "dBZ": "radar"}.get(entry["units"])
    return units or ("wv" if entry["category"] == "Water Vapor" else "ir")


def offered(entry):
    """Whether a table of this kind is one the edition running offers."""
    return entry_kind(entry) in edition.kinds()


def _load_edition_tables():
    tables = Tables()
    try:
        data = builtin_table_file()
        entries = data["tables"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        entries = []
        tables.USER_TABLE_PROBLEMS.append(f"The built-in color tables could not be read ({e}).")
    for raw in entries:
        entry = user_colortables.normalize(raw)
        name = entry["name"]

        def builder(e=entry):
            return user_colortables.build_cmap(e)
        builder.__name__ = name
        register_builder(tables, name, builder, entry.get("description", ""), entry["category"])
        tables.PALETTE_DESCRIPTIONS[name] = entry.get("description", "")
        tables.PALETTES.append(name)
        tables.BUILTIN_ENTRIES[name] = entry
    snapshot_builtins(tables)
    problems = list(tables.USER_TABLE_PROBLEMS)
    load_user_tables(tables)
    tables.USER_TABLE_PROBLEMS[:0] = problems
    return tables


# ------------------------------------------------------------------- looking things up

def get(name, vmin=None, vmax=None):
    """(cmap, norm_or_None, vmax, vmin) for a table name, from the registry in use: in tcviz
    exactly colormaps.get (every table and look tcviz has), in Colortable Editor its own."""
    return space().get(name, vmin=vmin, vmax=vmax)


def palettes():
    """Every table name, built-in and the person's own, in the pickers' order (the
    registry's PALETTES list itself)."""
    return space().PALETTES


def descriptions():
    """{name: one-line description} (the registry's PALETTE_DESCRIPTIONS itself)."""
    return space().PALETTE_DESCRIPTIONS


def registered_builders():
    """{name: builder} of every registered table (the registry's own dict)."""
    return space()._REGISTERED_COLORTABLES


def registered_colortables():
    """{name: category} for every registered table -- consumed by categorize_palettes so a
    newly-registered colortable appears in a picker with no edit anywhere else."""
    return dict(space()._REGISTERED_CATEGORIES)


def builtin_names():
    """Every table name the program itself defines (a frozenset), as it stood before the
    user's own tables were loaded."""
    return space()._BUILTIN_NAMES


def builtin_entries():
    """{name: entry} of Colortable Editor's built-in tables, in its order; {} in tcviz,
    whose built-ins are code."""
    found = space()
    return dict(found.BUILTIN_ENTRIES) if isinstance(found, Tables) else {}


def hidden_palettes():
    """Registered tables kept out of every picker: the user's hidden tables, and built-ins
    registered with no category (the joke curve). get() still draws each one by name."""
    return {name for name, category in space()._REGISTERED_CATEGORIES.items() if not category}


def user_table_units():
    """{name: units} for every table of the user's in the registry, hidden ones included:
    "C" for infrared and water vapor, "kt" for winds, "dBZ" for radar."""
    return {name: builder.user_entry.get("units", "C") for name, builder in space()._REGISTERED_COLORTABLES.items()
            if getattr(builder, "user_entry", None) is not None}


def user_table_names():
    """Every name the registry draws from the user's own store, hidden tables included."""
    return {name for name, builder in space()._REGISTERED_COLORTABLES.items()
            if getattr(builder, "user_entry", None) is not None}


def user_table_problems():
    """One plain-words sentence per stored table that could not be loaded."""
    return list(space().USER_TABLE_PROBLEMS)


def generation():
    """A number that changes whenever a table is registered or taken out, so whatever
    caches by name (a swatch, a picker list) knows to look again."""
    return space().generation


def cache_key():
    """(which registry, generation): what a cache by table name is good for. Two registries
    can be at the same generation (a test switching editions in one process)."""
    found = space()
    return getattr(found, "serial", 0), found.generation


def bump():
    """Move generation on, for a change that registered nothing (a table that cannot be
    drawn)."""
    space().generation += 1


def register_user_colortable(entry):
    """register_user on the registry in use; returns the name."""
    return register_user(space(), entry)


def unregister_user_colortable(name):
    """unregister_user on the registry in use; True if one was removed."""
    return unregister_user(space(), name)


# ------------------------------------------------------------------ groups and names

def palette_categories():
    """{category: names} of the picker groups tables are listed under beyond their own
    registered category: tcviz.products.PALETTE_CATEGORIES in tcviz, none in Colortable
    Editor (every table it has is registered with its category)."""
    if edition.is_editor():
        return {}
    from . import products
    return products.PALETTE_CATEGORIES


def group_palettes(allowed, categories, registered):
    """Group `allowed` into an ordered {category: [names]} dict, in `categories`' order,
    dropping any category with nothing available.

    `registered` is {name: category} (registered_colortables): those palettes are folded
    into their declared category automatically -- so a newly added colortable shows up in
    the GUI picker with no edit anywhere. A registration whose category is falsy (the joke
    curve, a user table marked hidden: hidden_palettes) is not shown in any group."""
    allowed_set = set(allowed)
    # registered colortable name -> category; group them so each category's extras are
    # appended after its hardcoded members.
    extra = {}
    for name, cat in registered.items():
        if cat:
            extra.setdefault(cat, []).append(name)
    ordered_cats = list(categories) + [c for c in extra if c not in categories]
    out = {}
    for cat in ordered_cats:
        names = list(categories.get(cat, ()))
        names += [n for n in extra.get(cat, []) if n not in names]
        present = [n for n in names if n in allowed_set]
        if present:
            out[cat] = present
    return out


def categorize_palettes(allowed):
    """products.categorize_palettes for the registry in use."""
    return group_palettes(allowed, palette_categories(), registered_colortables())


def reserved_names():
    """Every name a NEW table of the person's may not take: in tcviz every name its own
    tables and looks answer to (products.reserved_palette_names), in Colortable Editor its
    built-in tables'."""
    if edition.is_editor():
        return frozenset(builtin_names())
    from . import products
    return products.reserved_palette_names()
