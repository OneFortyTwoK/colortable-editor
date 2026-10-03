"""Colortable Editor's six built-in tables draw exactly tcviz's colors.

colortable_editor/builtin_tables.json is written from tcviz's own tables by
tools/make_editor_builtins.py. Two checks hold it there:

* always (the public copy too): every table rebuilds to the fingerprint recorded beside it
  -- the SHA-256 of its rows as colorize.table makes them, its number of colors and its
  range in Kelvin, bit for bit;
* in tcviz: every table draws the same bytes as tcviz's built-in of that name through
  colorize.rgba -- on a ramp across every temperature, NaN and infinities included, and on
  the sample pictures -- and the editor's preview of it is those bytes too.

Needs only the edition's own modules, numpy, matplotlib and pytest (tcviz.colormaps for the
second check, which is skipped without it).
"""
import contextlib
import hashlib
import os
from pathlib import Path

import numpy as np
import pytest
from matplotlib.colors import Normalize

from tcviz import colorize, colortable_preview, colortable_registry, edition, user_colortables

SIX = ["grayscale", "ott2", "bd05", "crys10v4", "irg", "wv"]
WATER_VAPOR = {"wv"}


@pytest.fixture
def editor_edition(tmp_path, monkeypatch):
    """This process runs as Colortable Editor, its folder a scratch one with no tables."""
    monkeypatch.setenv(edition.CONFIG_DIR_ENV, str(tmp_path / "editor"))
    monkeypatch.setattr(edition, "_current", edition.COLORTABLE_EDITOR)
    colortable_registry.forget_edition_tables()
    yield
    colortable_registry.forget_edition_tables()


def _ramp():
    """Every temperature a picture can hold, a hundredth of a degree apart, and the values
    that are no temperature at all."""
    values = np.arange(140.0, 360.0, 0.01)
    return np.concatenate([values, [np.nan, np.inf, -np.inf, 0.0, 1e6]]).reshape(1, -1)


def _samples():
    return [colortable_preview.load_sample(kind) for kind in ("ir", "wv")]


def test_the_file_holds_the_six_tables_and_nothing_said_about_them():
    """No description and no credit for any table (the author's call, 2026-10-03), and only
    these six."""
    data = colortable_registry.builtin_table_file()
    assert [t["name"] for t in data["tables"]] == SIX
    assert "credits" not in data
    assert all(t["description"] == "" and "credit" not in t for t in data["tables"])
    for table in data["tables"]:
        kind = colortable_registry.entry_kind(table)
        assert kind == ("wv" if table["name"] in WATER_VAPOR else "ir")


def test_crys10v4_is_tcviz_s_sixteen_stops_over_150_degrees_reversed():
    """The definition its author confirmed: 16 stops /150, reversed, -100 to 50 °C."""
    entry = next(t for t in colortable_registry.builtin_table_file()["tables"] if t["name"] == "crys10v4")
    assert len(entry["stops"]) == 16 and entry["reversed"] is True
    assert (entry["vmin_c"], entry["vmax_c"]) == (-100.0, 50.0)
    whole = [round(p * 150, 9) for p, _c in entry["stops"]]
    assert whole == [0, 20, 42, 60, 65, 73, 95, 102, 107, 116, 119, 125, 130, 133, 143, 150]


@pytest.mark.parametrize("name", SIX)
def test_each_table_rebuilds_to_its_recorded_fingerprint(name, editor_edition):
    entry = next(t for t in colortable_registry.builtin_table_file()["tables"] if t["name"] == name)
    cmap, norm, vmax, vmin = colortable_registry.get(name)
    assert norm is None
    check = entry["check"]
    assert hashlib.sha256(colorize.table(cmap).tobytes()).hexdigest() == check["table_sha256"]
    assert (cmap.N, vmax, vmin) == (check["levels"], check["vmax_k"], check["vmin_k"])


def _tcviz_builtin(colormaps, name):
    """(cmap, norm, vmax, vmin) of tcviz's own table `name` -- never a stored table of the
    person's that happens to carry the name (some long-kept ones do)."""
    if name in colormaps._BUILTIN_REGISTRY:
        cmap, vmax, vmin = colormaps._BUILTIN_REGISTRY[name][0]()
        return cmap, None, vmax, vmin
    from unittest import mock
    with mock.patch.dict(colormaps._REGISTERED_COLORTABLES):
        colormaps._REGISTERED_COLORTABLES.pop(name, None)
        return colormaps.get(name)


@contextlib.contextmanager
def _numpy_and_native():
    """Both ways colorize.rgba draws in tcviz: tcviz_native when it is built, and numpy."""
    from tcviz import native
    saved = os.environ.get("TCVIZ_NO_NATIVE")
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("TCVIZ_NO_NATIVE", None)
        else:
            os.environ["TCVIZ_NO_NATIVE"] = saved
        native.reset()


@pytest.mark.parametrize("name", SIX)
def test_each_table_draws_tcviz_s_bytes_on_a_ramp_and_on_the_samples(name, monkeypatch):
    colormaps = pytest.importorskip("tcviz.colormaps")
    from tcviz import native
    theirs, their_norm, their_vmax, their_vmin = _tcviz_builtin(colormaps, name)
    assert their_norm is None

    monkeypatch.setenv(edition.CONFIG_DIR_ENV, str(Path(os.environ.get("TMPDIR", "/tmp")) / "no-such-editor-folder"))
    monkeypatch.setattr(edition, "_current", edition.COLORTABLE_EDITOR)
    colortable_registry.forget_edition_tables()
    try:
        ours, our_norm, our_vmax, our_vmin = colortable_registry.get(name)
    finally:
        colortable_registry.forget_edition_tables()
        monkeypatch.setattr(edition, "_current", edition.TCVIZ)
    assert our_norm is None and (our_vmax, our_vmin) == (their_vmax, their_vmin) and ours.N == theirs.N
    assert np.array_equal(colorize.table(ours), colorize.table(theirs))

    pictures = [("ramp", _ramp(), their_vmax, their_vmin)]
    for sample in _samples():
        vmax, vmin = sample.drawing_range(their_vmax, their_vmin)
        pictures.append((sample.label, sample.full, vmax, vmin))
    with _numpy_and_native():
        for no_native in ("1", ""):
            os.environ["TCVIZ_NO_NATIVE"] = no_native
            native.reset()
            for label, values, vmax, vmin in pictures:
                norm = Normalize(vmin=vmin, vmax=vmax)
                want = colorize.rgba(values, theirs.with_extremes(bad="black"), norm)
                got = colorize.rgba(values, ours.with_extremes(bad="black"), norm)
                assert np.array_equal(got, want), f"{name} on {label} (native {'off' if no_native else 'on'})"


@pytest.mark.parametrize("name", SIX)
def test_the_editor_s_preview_of_each_table_is_tcviz_s_picture(name):
    """What the editor shows (colortable_preview: a row image looked up in the table) is the
    picture tcviz draws with its own table, byte for byte."""
    colormaps = pytest.importorskip("tcviz.colormaps")
    theirs, _norm, vmax_k, vmin_k = _tcviz_builtin(colormaps, name)
    entry = next(t for t in colortable_registry.builtin_table_file()["tables"] if t["name"] == name)
    ours, our_vmax, our_vmin = user_colortables.build_cmap(entry)
    assert (our_vmax, our_vmin) == (vmax_k, vmin_k)
    for sample in _samples():
        vmax, vmin = sample.drawing_range(vmax_k, vmin_k)
        norm = Normalize(vmin=vmin, vmax=vmax)
        shown = sample.apply(colortable_preview.lut_for(ours), norm, ours.N)
        tcviz_draws = colorize.rgba(sample.values, theirs.with_extremes(bad="black"), norm)
        assert np.array_equal(shown, tcviz_draws), f"{name} on {sample.label}"
