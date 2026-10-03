"""Colortable Editor, the color table editor published on its own (colortable_editor,
tcviz.edition): that it starts light, keeps its things in a folder of its own, offers
infrared and water-vapor tables only, shows its bundled samples, survives Windows' file
rules, and says who made what.

Self-contained, so it ships with the public copy (tools/export_colortable_editor.py): it
needs only the edition's own modules, numpy, matplotlib, PySide6, Pillow, platformdirs and
pytest. The few tests of tcviz's own side of the switch are skipped there. Every test runs
in a scratch folder; nothing is written anywhere else (the store tests check).
"""
import datetime
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tcviz import colortable_registry, edition, user_colortables

ROOT = Path(__file__).resolve().parent.parent
# What Colortable Editor must never load: tcviz's picture engine, its whole table collection,
# and tcviz.paths (tcviz's folders, its sign-in file among them).
HEAVY = ("dask", "pandas", "scipy", "pyresample", "shapely", "pyproj", "pycoast", "satpy", "xarray", "h5py",
         "netCDF4", "cartopy", "tcviz_native", "tcviz.colormaps", "tcviz.paths")
# tcviz itself, rather than the public copy of Colortable Editor, which has the edition's
# modules only: the tests of how tcviz behaves need its tables and folders.
IN_TCVIZ = importlib.util.find_spec("tcviz.colormaps") is not None
tcviz_only = pytest.mark.skipif(not IN_TCVIZ, reason="tcviz's own tables and folders are not in this copy")
SIX = ["grayscale", "ott2", "bd05", "crys10v4", "irg", "wv"]
_STOPS = [(0.0, "#000000"), (0.5, "#808080"), (1.0, "#ffffff")]


# ------------------------------------------------------------------------ fixtures

@pytest.fixture(scope="session")
def qapp():
    """One offscreen QApplication for the tests that build windows."""
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QtWidgets.QApplication([])
    return app


@pytest.fixture(autouse=True)
def _no_real_windows(monkeypatch):
    """A modal window opened for real would wait forever on an offscreen display."""
    from PySide6.QtWidgets import QDialog, QFileDialog, QMenu

    def refuse(*_a, **_k):
        raise AssertionError("a test opened a real window")
    monkeypatch.setattr(QDialog, "exec", refuse)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", refuse)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", refuse)
    monkeypatch.setattr(QMenu, "exec", refuse)


@pytest.fixture
def folders(tmp_path, monkeypatch):
    """Scratch folders for everything: Colortable Editor's own, and the ones tcviz would use
    (which the edition must leave alone)."""
    out = {"editor": tmp_path / "editor", "tcviz_store": tmp_path / "tcviz" / "colortables.json",
           "tcviz_config": tmp_path / "tcviz_config", "samples": tmp_path / "no_samples" / "samples.json"}
    out["tcviz_config"].mkdir()
    monkeypatch.setenv(edition.CONFIG_DIR_ENV, str(out["editor"]))
    monkeypatch.setattr(user_colortables, "STORE_PATH", out["tcviz_store"])
    if IN_TCVIZ:
        from tcviz import paths
        monkeypatch.setattr(paths, "config_dir", lambda: out["tcviz_config"])
    monkeypatch.setattr(edition, "samples_manifest_path", lambda: out["samples"])
    return out


@pytest.fixture
def editor_edition(folders, monkeypatch):
    """This process runs as Colortable Editor, with a registry of its own read afresh."""
    monkeypatch.setattr(edition, "_current", edition.COLORTABLE_EDITOR)
    colortable_registry.forget_edition_tables()
    yield folders
    colortable_registry.forget_edition_tables()


def _files_under(folder):
    return sorted(str(p.relative_to(folder)) for p in Path(folder).rglob("*") if p.is_file()) if Path(folder).exists() \
        else []


def _manifest(folder, entries):
    """A tiny samples.json with tiny pictures (40 x 40, tcviz's saved-picture format)."""
    from tcviz import saved_pictures
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, entry in enumerate(entries):
        values = np.linspace(190.0, 300.0, 1600, dtype=np.float32).reshape(40, 40) + i
        saved_pictures.save_raw(folder / entry["file"], values, 18.0, -77.0, 4.0, entry["satellite"], "ABI",
                                "WV-Mid" if entry["kind"] == "wv" else "IR",
                                datetime.datetime(2025, 10, 28, 12, i, tzinfo=datetime.timezone.utc))
        rows.append({"storm": "Melissa", "year": 2025, "basin": "AL", "instrument": "ABI",
                     "band_words": "a test band", "obs_time_utc": f"2025-10-28T12:0{i}:00Z", "pixel_km": 2.0,
                     "coldest_c": -83.0, "warmest_c": 26.9, "source": "https://example.invalid/", **entry})
    (folder / "samples.json").write_text(json.dumps(rows), encoding="utf-8")
    return folder / "samples.json"


SAMPLES = [
    {"id": "melissa_ir", "file": "melissa_ir.npz", "kind": "ir", "satellite": "GOES-19", "band": "C13",
     "description": "Hurricane Melissa over Jamaica, its eye ringed by cloud tops colder than -80 °C.",
     "credit": "Picture: NOAA GOES-19."},
    {"id": "melissa_wv", "file": "melissa_wv.npz", "kind": "wv", "satellite": "GOES-19", "band": "C09",
     "description": "The same storm in water vapor.", "credit": "Picture: NOAA GOES-19."},
]


def _smoke_env(tmp_path):
    env = dict(os.environ, COLORTABLE_EDITOR_SMOKE="1", QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    env[edition.CONFIG_DIR_ENV] = str(tmp_path / "editor")
    env.pop("TCVIZ_NO_NATIVE", None)            # the edition turns the Rust layer off itself
    return env


# ---------------------------------------------------------------- starting light

_GUARD = """
import json, runpy, sys
code = 0
try:
    runpy.run_module("colortable_editor", run_name="__main__", alter_sys=True)
except SystemExit as e:
    code = e.code
print("MODULES " + json.dumps(sorted(sys.modules)))
sys.exit(code)
"""


def test_the_editor_starts_without_tcviz_s_engine_or_its_table_collection(tmp_path):
    """Started as the packaged build starts it (the smoke run: main window, one sample through
    one table), nothing of tcviz's picture engine is loaded -- no dask, pandas, scipy,
    pyresample, shapely, pyproj, pycoast, satpy, xarray, h5py, netCDF4, cartopy, Rust layer --
    and not tcviz.colormaps, which holds the code and numbers of all 229 tables."""
    run = subprocess.run([sys.executable, "-c", _GUARD], cwd=ROOT, env=_smoke_env(tmp_path), capture_output=True,
                         text=True, encoding="utf-8", timeout=180)
    assert run.returncode == 0, run.stderr[-3000:]
    modules = json.loads(next(line for line in run.stdout.splitlines() if line.startswith("MODULES "))[8:])
    loaded = [name for name in HEAVY if name in modules]
    assert loaded == [], f"Colortable Editor loaded {loaded}"
    ours = [m for m in modules if m.split(".")[0] in ("tcviz", "tcviz_gui", "colortable_editor")]
    assert "tcviz_gui.pages.colortable_editor_dialog" in ours and len(ours) < 45, ours


def test_the_smoke_run_draws_a_sample_through_a_table_and_exits_0(tmp_path):
    env = dict(_smoke_env(tmp_path), COLORTABLE_EDITOR_SMOKE_FILE=str(tmp_path / "smoke.txt"))
    run = subprocess.run([sys.executable, "-m", "colortable_editor"], cwd=ROOT, env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=180)
    assert run.returncode == 0, run.stderr[-3000:]
    line = run.stdout.strip().splitlines()[-1]
    assert line.startswith("Colortable Editor 1.0.0 smoke: 6 tables listed"), line
    assert "drawn through ott2" in line and str(tmp_path / "editor") in line
    # the older samples and the font were found where the program keeps them
    assert "and 2 older ones" in line and "font JetBrainsMonoNerdFontMono-Bold.ttf" in line
    # the Windows program has no console: the same line goes to the file a build check reads
    assert (tmp_path / "smoke.txt").read_text(encoding="utf-8") == line + "\n"
    assert _files_under(tmp_path / "editor") == [], "the smoke run wrote something"


def test_a_program_without_a_console_can_still_run_jobs(monkeypatch):
    """The Windows program is windowed: sys.stdout and sys.stderr are None there. The jobs'
    output router (installed by the first job) must swallow what nothing reads rather than
    fail on it."""
    from tcviz_gui import worker
    router = worker._OutputRouter("stdout", None)
    assert router.write("a line nobody reads\n") == len("a line nobody reads\n")
    router.flush()


# ------------------------------------------------------------------- the switch

def test_tcviz_is_the_default_and_only_the_two_editions_exist(monkeypatch):
    assert edition.current() == edition.TCVIZ and not edition.is_editor()
    assert edition.kinds() == ("ir", "wv", "wind", "radar") and edition.app_name() == "tcviz"
    assert edition.opens_picture_files() and edition.has_pickers()
    with pytest.raises(ValueError):
        edition.use("something else")
    monkeypatch.setattr(edition, "_current", edition.TCVIZ)
    edition.use(edition.COLORTABLE_EDITOR)
    assert edition.kinds() == ("ir", "wv") and edition.app_name() == "Colortable Editor"
    assert not edition.opens_picture_files() and not edition.has_pickers()


def test_importing_the_package_does_not_switch_the_edition():
    import colortable_editor
    assert colortable_editor.__version__ == "1.0.0"
    assert edition.current() == edition.TCVIZ


def test_the_edition_has_exactly_its_six_built_in_tables_and_no_words_about_them(editor_edition):
    assert list(colortable_registry.palettes()) == SIX
    assert colortable_registry.builtin_names() == frozenset(SIX)
    assert colortable_registry.registered_colortables() == {**{n: "Temperature (IR)" for n in SIX[:5]},
                                                            "wv": "Water Vapor"}
    for name in ("rainbow", "ir"):                  # tcviz's, not the edition's
        with pytest.raises(ValueError):
            colortable_registry.get(name)
    assert colortable_registry.reserved_names() == frozenset(SIX)
    # no description, no credit: the author's call (2026-10-03)
    assert {colortable_registry.descriptions()[n] for n in SIX} == {""}
    assert all(not entry["description"] and "credit" not in entry
               for entry in colortable_registry.builtin_entries().values())


# ------------------------------------------------------------------- the store

def test_tables_favorites_and_deleted_tables_go_only_to_the_edition_folder(editor_edition):
    from tcviz import colortable_library
    folder = editor_edition["editor"]
    colortable_library.save("mine", _STOPS, vmin_c=-90.0, vmax_c=30.0)
    colortable_library.save_favorites(["mine"])
    colortable_library.rename("mine", "ours")
    colortable_library.duplicate("bd05")
    colortable_library.delete("bd05_copy")
    assert set(_files_under(folder)) == {"colortables.json", "colortables.json.bak", "colortables.deleted.json",
                                         "favorite_palettes.json"}
    assert colortable_library.load_favorites() == ["ours"]
    assert [t.name for t in colortable_library.tables()] == ["ours"]
    assert colortable_library.restore()["name"] == "bd05_copy"
    # tcviz's own places were never touched
    assert not editor_edition["tcviz_store"].parent.exists()
    assert _files_under(editor_edition["tcviz_config"]) == []


@tcviz_only
def test_tcviz_keeps_its_own_store_and_favorites_exactly_where_they_were(folders):
    from tcviz import colortable_library, paths
    assert user_colortables.store_path() == user_colortables.STORE_PATH == folders["tcviz_store"]
    assert colortable_library._favorites_path() == paths.config_dir() / "favorite_palettes.json"
    assert colortable_library._deleted_path() == folders["tcviz_store"].with_name("colortables.deleted.json")
    # and, unpatched, the store is still ~/.config/tcviz/colortables.json
    run = subprocess.run([sys.executable, "-c", "from tcviz import user_colortables as u; print(u.store_path())"],
                         cwd=ROOT, env={k: v for k, v in os.environ.items() if k != "TCVIZ_COLORTABLES_PATH"},
                         capture_output=True, text=True, timeout=60)
    assert run.stdout.strip() == str(Path.home() / ".config" / "tcviz" / "colortables.json"), run.stderr


def test_the_edition_folder_is_the_platform_s_own_config_folder(monkeypatch):
    import platformdirs
    monkeypatch.delenv(edition.CONFIG_DIR_ENV, raising=False)
    want = Path(platformdirs.user_config_dir("Colortable Editor", appauthor=False, roaming=True))
    assert edition.editor_config_dir() == want and want.name == "Colortable Editor"
    monkeypatch.setattr(edition, "_current", edition.COLORTABLE_EDITOR)
    assert user_colortables.store_path() == want / "colortables.json"


# ------------------------------------------------------------------- the kinds

def test_only_infrared_and_water_vapor_are_offered_anywhere(qapp, editor_edition):
    from tcviz import colortable_import, colortable_library
    from tcviz_gui.pages.add_colortable_dialog import AddColortableDialog
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    from tcviz_gui.pages.manage_colortables_dialog import _KIND, ManageColortablesDialog

    # a store copied in from tcviz, with a winds and a radar table in it
    store = user_colortables.store_path()
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps([
        {"name": "my_ir", "stops": _STOPS, "vmin_c": -90.0, "vmax_c": 30.0, "category": "Temperature (IR)"},
        {"name": "my_wv", "stops": _STOPS, "vmin_c": -90.0, "vmax_c": 0.0, "category": "Water Vapor"},
        {"name": "my_wind", "stops": _STOPS, "units": "kt", "vmin": 0.0, "vmax": 100.0, "category": "SAR Wind"},
        {"name": "my_radar", "stops": _STOPS, "units": "dBZ", "vmin": -10.0, "vmax": 70.0, "category": "Radar (dBZ)"},
    ]), encoding="utf-8")
    colortable_registry.forget_edition_tables()

    editor = ColortableEditorDialog()
    assert [editor.kind_combo.itemText(i) for i in range(editor.kind_combo.count())] == ["Infrared", "Water vapor"]
    assert not editor.hidden_check.isVisibleTo(editor)
    editor.deleteLater()
    add = AddColortableDialog(mode="text")
    assert [add.category_combo.itemText(i) for i in range(add.category_combo.count())] == \
        ["Temperature (IR)", "Water Vapor"]
    add.stops_edit.setPlainText('def gusts():\n    # units: kt\n    newcmp = LinearSegmentedColormap.from_list("", [\n'
                                '        (0/100, "#000000"),\n        (100/100, "#ffffff")\n    ])\n'
                                '    vmax = 100 / 1.9438452\n    vmin = 0 / 1.9438452\n    return newcmp, vmax, vmin\n')
    assert not add._save_btn.isEnabled() and "not an infrared or water-vapor table" in add.status.text()
    add.deleteLater()

    manage = ManageColortablesDialog()
    rows = [(manage.table_list.topLevelItem(i).text(0), manage.table_list.topLevelItem(i).text(_KIND))
            for i in range(manage.table_list.topLevelItemCount())]
    assert rows == [("my_ir", "Infrared"), ("my_wv", "Water vapor"), ("grayscale", "Infrared"), ("ott2", "Infrared"),
                    ("bd05", "Infrared"), ("crys10v4", "Infrared"), ("irg", "Infrared"), ("wv", "Water vapor")]
    assert not manage.hide_btn.isVisibleTo(manage)
    manage.close()
    assert sorted(colortable_registry.user_table_names()) == ["my_ir", "my_wv"]
    assert sorted(colortable_library.export_groups()["mine"]) == ["my_ir", "my_wv"]

    # the importer: a radar .pal is refused in plain words, a store keeps only its IR/WV tables
    pal = editor_edition["editor"] / "radar.pal"
    pal.write_text("Product: BR\nUnits: dBZ\nColor: 10 0 0 255\nColor: 60 255 0 0\n", encoding="utf-8")
    with pytest.raises(colortable_import.ImportProblem, match="holds no infrared or water-vapor color table"):
        colortable_import.parse_any(pal)
    drafts = colortable_import.parse_any(store)
    assert [(d.name, d.kind) for d in drafts] == [("my_ir", "ir"), ("my_wv", "wv")]
    assert drafts[0].warnings[-1] == "2 other tables in it are not an infrared or water-vapor table, so they were left out."


@tcviz_only
def test_tcviz_still_offers_all_four_kinds(qapp, folders):
    from tcviz_gui.pages.add_colortable_dialog import AddColortableDialog
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    editor = ColortableEditorDialog()
    assert editor.kind_combo.count() == 4 and editor.hidden_check.isVisibleTo(editor)
    editor.deleteLater()
    add = AddColortableDialog(mode="text")
    assert add.category_combo.count() == 4
    add.deleteLater()


# ------------------------------------------------------------- the built-in rows

def test_built_in_tables_are_copied_not_changed(qapp, editor_edition):
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    from tcviz_gui.pages.manage_colortables_dialog import ManageColortablesDialog
    manage = ManageColortablesDialog()
    manage.select("bd05")
    assert manage.duplicate_btn.isEnabled() and manage.export_btn.isEnabled() and manage.edit_btn.isEnabled()
    assert not (manage.rename_btn.isEnabled() or manage.delete_btn.isEnabled())
    opened = []
    manage._run = lambda dialog: opened.append(dialog) or 0
    manage._on_edit()
    assert len(opened) == 1 and isinstance(opened[0], ColortableEditorDialog)
    assert opened[0]._mode == "copy" and opened[0]._source == "bd05" and opened[0].name_edit.text() == "bd05_copy"
    opened[0].deleteLater()
    manage._on_duplicate()
    assert manage.selected_name() == "bd05_copy" and not manage._tables["bd05_copy"].builtin
    manage.close()


# ------------------------------------------------------------- the picture box

def test_the_picture_box_lists_the_bundled_samples(qapp, editor_edition, monkeypatch):
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    manifest = _manifest(editor_edition["editor"].parent / "samples", SAMPLES)
    monkeypatch.setattr(edition, "samples_manifest_path", lambda: manifest)
    d = ColortableEditorDialog()
    items = [d.picture_combo.itemText(i) for i in range(d.picture_combo.count()) if d.picture_combo.itemText(i)]
    assert items == ["Melissa 2025 · GOES-19 IR", "Melissa 2025 · GOES-19 water vapor"]
    assert d.picture_combo.currentData() == "bundled:melissa_ir"
    assert d.picture_combo.itemData(0, 3) == SAMPLES[0]["description"]          # Qt.ToolTipRole
    assert d.picture_note.text().startswith(SAMPLES[0]["description"] + " Picture: NOAA GOES-19.")
    assert d._preview.full_shape == (40, 40) and not d._preview.water_vapor
    d._on_picture_combo(1)
    assert d.picture_combo.currentData() == "bundled:melissa_wv" and d._preview.water_vapor
    d.set_kind("wv")                                        # a water-vapor table opens on its own sample
    assert d.picture_combo.currentData() == "bundled:melissa_wv"
    d.undo_stack.setClean()
    d.deleteLater()


def test_without_a_manifest_the_edition_shows_its_two_samples(qapp, editor_edition):
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    d = ColortableEditorDialog()
    items = [d.picture_combo.itemText(i) for i in range(d.picture_combo.count()) if d.picture_combo.itemText(i)]
    assert items == ["Sample infrared picture (GOES-19, Genevieve)", "Sample water-vapor picture (GOES-19, Genevieve)"]
    assert d._preview is not None
    d.deleteLater()


@tcviz_only
def test_tcviz_opens_on_the_bundled_samples_then_lists_its_own_and_the_recent_ones(qapp, folders, monkeypatch):
    """The author's call (2026-10-03): tcviz's editor opens on the bundled samples too, in the
    manifest's order; Genevieve's samples and the person's saved pictures follow."""
    from tcviz import colortable_preview
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    manifest = _manifest(folders["editor"].parent / "samples", SAMPLES)
    monkeypatch.setattr(edition, "samples_manifest_path", lambda: manifest)
    d = ColortableEditorDialog()
    recent = colortable_preview.RecentPicture(folders["editor"] / "x_IR_data.npz", "GOES-16 ABI IR", "ir")
    d._on_recent_found({"ir": [recent], "wv": [], "wind": [], "radar": []})
    items = [d.picture_combo.itemText(i) for i in range(d.picture_combo.count()) if d.picture_combo.itemText(i)]
    assert items == ["Melissa 2025 · GOES-19 IR", "Melissa 2025 · GOES-19 water vapor",
                     "Sample infrared picture (GOES-19, Genevieve)", "Sample water-vapor picture (GOES-19, Genevieve)",
                     "Recent: GOES-16 ABI IR", "Choose a saved picture…"]
    assert d.picture_combo.currentData() == "bundled:melissa_ir" and d._preview.label == "Melissa 2025 · GOES-19 IR"
    d.set_kind("wv")                                        # a water-vapor table: the bundled water-vapor one
    assert d.picture_combo.currentData() == "bundled:melissa_wv"
    d.set_kind("wind")                                      # no bundled wind pictures: tcviz's own
    assert d.picture_combo.currentData() == "sample:wind"
    d.set_kind("ir")
    assert d.picture_combo.currentData() == "bundled:melissa_ir"
    d.undo_stack.setClean()
    d.deleteLater()


def test_a_missing_or_broken_manifest_is_no_samples(tmp_path):
    from tcviz import colortable_preview
    assert colortable_preview.bundled_samples(tmp_path / "nothing.json") == []
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert colortable_preview.bundled_samples(tmp_path / "bad.json") == []
    manifest = _manifest(tmp_path / "s", SAMPLES)
    (tmp_path / "s" / "melissa_wv.npz").unlink()               # an entry whose file is gone is left out
    assert [s.id for s in colortable_preview.bundled_samples(manifest)] == ["melissa_ir"]


# ------------------------------------------------------------------ Windows

def test_the_atomic_write_works_where_os_has_no_fchmod(tmp_path, monkeypatch):
    """Windows before Python 3.13 has no os.fchmod at all."""
    monkeypatch.delattr(os, "fchmod", raising=False)
    path = tmp_path / "colortables.json"
    user_colortables._atomic_write_text(path, "[1]\n")
    user_colortables._atomic_write_text(path, "[2]\n")
    assert path.read_bytes() == b"[2]\n"
    assert [p.name for p in tmp_path.iterdir()] == ["colortables.json"]


def test_a_file_held_open_on_windows_is_tried_again_then_explained(tmp_path, monkeypatch):
    import types
    path = tmp_path / "colortables.json"
    path.write_text("[]", encoding="utf-8")
    real = os.replace
    tries = []

    def held_open_twice(src, dst):
        tries.append(dst)
        if len(tries) <= 2:
            raise PermissionError(13, "Access is denied")
        return real(src, dst)
    fake_os = types.SimpleNamespace(**{k: getattr(os, k) for k in dir(os) if not k.startswith("__")})
    fake_os.name, fake_os.replace = "nt", held_open_twice
    monkeypatch.setattr(user_colortables, "os", fake_os)
    monkeypatch.setattr(user_colortables, "_REPLACE_WAIT_S", 0.0)
    user_colortables._atomic_write_text(path, "[3]")
    assert path.read_text(encoding="utf-8") == "[3]" and len(tries) == 3

    message = user_colortables.plain_save_error(PermissionError(13, "Access is denied"))
    assert "open in another program" in message and "Nothing was changed." in message
    assert "Errno" not in message and "denied" not in message


def test_a_save_refused_by_windows_gets_a_plain_message(qapp, editor_edition, monkeypatch):
    from tcviz_gui.pages import colortable_editor_dialog, manage_colortables_dialog
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    warned = []
    for module in (colortable_editor_dialog, manage_colortables_dialog):
        monkeypatch.setattr(module.QMessageBox, "warning", lambda _p, title, text, *a: warned.append((title, text)))

    def refused(src, dst):
        raise PermissionError(13, "[WinError 5] Access is denied")
    monkeypatch.setattr(os, "replace", refused)
    d = ColortableEditorDialog()
    d.name_edit.setText("blocked")
    d._on_save()
    assert d.entry is None and warned
    title, text = warned[-1]
    assert title == "Couldn't save colortable" and "open in another program" in text and "WinError" not in text
    d.undo_stack.setClean()
    d.deleteLater()
    assert manage_colortables_dialog.ManageColortablesDialog._plain(PermissionError(13, "x")) == \
        user_colortables.plain_save_error(PermissionError(13, "x"))


def test_a_tables_file_saved_by_notepad_still_reads(editor_edition):
    """Windows' Notepad has put a byte-order mark at the start of every UTF-8 file it saved;
    the tables file must not read as damaged for it."""
    from tcviz import colortable_library
    store = user_colortables.store_path()
    store.parent.mkdir(parents=True)
    store.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"name": "mine", "stops": _STOPS, "vmin_c": -90.0,
                                                      "vmax_c": 30.0}]).encode("utf-8"))
    report = user_colortables.load_report()
    assert report.problems == [] and [e["name"] for e in report.entries] == ["mine"]
    assert [t.name for t in colortable_library.tables()] == ["mine"]


def test_dates_in_the_manage_window_need_no_glibc_formats():
    """strftime's %-I and %-d are glibc's; Windows raises ValueError on them."""
    from tcviz_gui.pages.manage_colortables_dialog import _when_text
    now = datetime.datetime.now()
    assert _when_text(now.replace(hour=15, minute=7)) == "today 3:07 PM"
    assert _when_text(now.replace(hour=0, minute=5)) == "today 12:05 AM"
    assert _when_text(datetime.datetime(2026, 10, 2, 9, 30)) == "Oct 2, 9:30 AM"


def test_save_picture_offers_the_system_pictures_folder(qapp, editor_edition, tmp_path, monkeypatch):
    from PySide6.QtCore import QStandardPaths

    from tcviz_gui.pages import colortable_editor_dialog
    pictures = tmp_path / "My Pictures"
    pictures.mkdir()
    monkeypatch.setattr(QStandardPaths, "standardLocations", lambda _where: [str(pictures)])
    monkeypatch.setattr(colortable_editor_dialog, "_last_save_folder", None)
    d = colortable_editor_dialog.ColortableEditorDialog()
    d.name_edit.setText("my table")
    assert d._suggested_file() == pictures / "my_table.png"
    monkeypatch.setattr(QStandardPaths, "standardLocations", lambda _where: [])
    assert d._suggested_file() == Path.home() / "my_table.png"
    d.undo_stack.setClean()
    d.deleteLater()


# --------------------------------------------------------- About and How to use

def test_about_says_who_made_what(qapp, editor_edition):
    from colortable_editor import about
    from colortable_editor.main_window import MainWindow
    lines = about.about_lines()
    credit = ("Sample pictures: NOAA GOES and VIIRS, from NOAA Open Data Dissemination; "
              "Katrina 2005 from NOAA NCEI's GridSat-GOES")
    assert lines == ["Colortable Editor 1.0.0", "by OneFortyTwoK", "MIT License", "", about.BLURB, "", credit]

    window = MainWindow()
    shown = window.about_dialog().plain_text()
    for words in ("Colortable Editor 1.0.0", "by OneFortyTwoK", "MIT License", credit):
        assert words in shown, words
    how = window.how_to_use_dialog().plain_text()
    assert "Making a table" in how and str(editor_edition["editor"]) in how
    # no color table is credited anywhere (the author's call, 2026-10-03): About says exactly
    # the lines above, and How to use names no table and thanks no one. (The names tcviz
    # credits its tables to are looked for in every file of the public copy by
    # tools/export_colortable_editor.py.) The How to use window also names the tables
    # folder, which here is under a scratch folder called tcviz.
    for text in (shown, about.how_to_use_text()):
        for words in ("tcviz", "credit", "Credit", "courtesy", "thanks", "Thanks", *SIX):
            assert words not in text, words
    menus = {menu.title().replace("&", ""): [a.text().replace("&", "") for a in menu.actions() if a.text()]
             for menu in (a.menu() for a in window.menuBar().actions())}
    assert menus == {"File": ["New table…", "Import…", "Export…", "Quit"],
                     "Help": ["How to use", "About Colortable Editor"]}
    window.close()
