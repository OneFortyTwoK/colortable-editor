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
    """The files under `folder`, as paths with forward slashes on every system (Windows
    writes "drafts\\draft-....json", which the tests compare with "drafts/..."; the friends'
    Windows run, 2026-10-03)."""
    return sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob("*") if p.is_file()) \
        if Path(folder).exists() else []


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


def _draft_file(folder, draft_id, entry, mode, original=None, saved_at="2026-10-02T21:05:00+00:00"):
    """A draft as an editor leaves one in `folder`/drafts (tcviz.colortable_drafts)."""
    path = Path(folder) / "drafts" / f"draft-{draft_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": 1, "app": "Colortable Editor", "app_version": "1.0.0", "saved_at": saved_at,
                                "mode": mode, "original": original, "source": None, "base": None, "entry": entry}),
                    encoding="utf-8")
    return path


def _shown_editor(*args, **kwargs):
    """The editor on screen (offscreen), as a person has it: only an editor on screen keeps
    a draft. Its draft timer is stopped; each test starts it with a change."""
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    d = ColortableEditorDialog(*args, **kwargs)
    d._searched = True
    d.show()
    d._draft_timer.stop()
    return d


def _fire(d):
    """The draft timer a change started runs out now, rather than in its 2 s."""
    assert d._draft_timer.isActive() and d._draft_timer.interval() == 2000
    d._draft_timer.stop()
    d._draft_timer.timeout.emit()


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
    # a table left unsaved by an earlier run: the smoke run must not offer it (a window would
    # wait forever for an answer) nor touch it
    left = _draft_file(tmp_path / "editor", "a" * 32, {"name": "left_over", "stops": _STOPS, "vmin_c": -90.0,
                                                        "vmax_c": 30.0}, "add")
    before = left.read_bytes()
    run = subprocess.run([sys.executable, "-m", "colortable_editor"], cwd=ROOT, env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=180)
    assert run.returncode == 0, run.stderr[-3000:]
    line = run.stdout.strip().splitlines()[-1]
    assert line.startswith("Colortable Editor 1.1.0 smoke: 6 tables listed"), line
    assert "drawn through ott2" in line and str(tmp_path / "editor") in line
    # the older samples and the font were found where the program keeps them
    assert "and 2 older ones" in line and "font JetBrainsMonoNerdFontMono-Bold.ttf" in line
    # the Windows program has no console: the same line goes to the file a build check reads
    assert (tmp_path / "smoke.txt").read_text(encoding="utf-8") == line + "\n"
    assert _files_under(tmp_path / "editor") == [f"drafts/{left.name}"], "the smoke run wrote something"
    assert left.read_bytes() == before


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
    assert colortable_editor.__version__ == "1.1.0"
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

def test_tables_favorites_and_deleted_tables_go_only_to_the_edition_folder(qapp, editor_edition):
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
    # the earlier versions (History) only once a table is saved over, in the same folder
    colortable_library.save("ours", _STOPS, original="ours", vmin_c=-90.0, vmax_c=30.0)
    assert "colortables.history.json" not in _files_under(folder)
    colortable_library.save("ours", _STOPS, original="ours", vmin_c=-80.0, vmax_c=30.0)
    kept = {"colortables.json", "colortables.json.bak", "colortables.deleted.json", "favorite_palettes.json",
            "colortables.history.json"}
    assert set(_files_under(folder)) == kept
    # a table with unsaved changes in the editor waits in drafts/ (a moment after the change),
    # which goes once it is saved
    d = _shown_editor(entry=colortable_library.get("ours"), mode="edit")
    d.add_stop(-40.0, "#ff0000")
    _fire(d)
    assert set(_files_under(folder)) == kept | {f"drafts/draft-{d._draft_id}.json"}
    d._on_save()
    assert set(_files_under(folder)) == kept and not (folder / "drafts").exists()
    d.deleteLater()
    # how the windows were left, in layout.json, once a window on screen was changed and
    # closed -- the editor above was not changed, so it left none
    d = _shown_editor(entry=colortable_library.get("ours"), mode="edit")
    d.resize(d.width() + 40, d.height())
    d.reject()
    d.deleteLater()
    assert set(_files_under(folder)) == kept | {"layout.json"}
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
    # a copy of a built-in starts with no description (it used to say "Copy of bd05")
    assert opened[0].desc_edit.text() == ""
    opened[0].deleteLater()
    manage._on_duplicate()
    assert manage.selected_name() == "bd05_copy" and not manage._tables["bd05_copy"].builtin
    assert manage._tables["bd05_copy"].entry["description"] == ""
    manage.close()


# ------------------------------------------------------------- the picture box

def _wait_for(qapp, until, seconds=20):
    import time

    from PySide6.QtCore import QEventLoop
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        qapp.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        time.sleep(0.01)
    assert until()


def test_the_picture_box_lists_the_bundled_samples(qapp, editor_edition, monkeypatch):
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    manifest = _manifest(editor_edition["editor"].parent / "samples", SAMPLES)
    monkeypatch.setattr(edition, "samples_manifest_path", lambda: manifest)
    d = ColortableEditorDialog()
    items = [d.picture_combo.itemText(i) for i in range(d.picture_combo.count()) if d.picture_combo.itemText(i)]
    assert items == ["Melissa 2025 · GOES-19 IR", "Melissa 2025 · GOES-19 water vapor"]
    assert d.picture_combo.currentData() == "bundled:melissa_ir"
    assert d.picture_combo.itemData(0, 3) == SAMPLES[0]["description"]          # Qt.ToolTipRole
    assert d.picture_note.text().startswith(SAMPLES[0]["description"] + "\nPicture: NOAA GOES-19.")
    assert d._preview.full_shape == (40, 40) and not d._preview.water_vapor
    d._on_picture_combo(1)                      # read in the background, the box naming it at once
    assert d.picture_combo.currentData() == "bundled:melissa_wv" and d.picture_pane.message == "Loading the picture…"
    _wait_for(qapp, lambda: d._loading is None)
    assert d._picture_choice == "bundled:melissa_wv" and d._preview.water_vapor
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


def test_swipe_shows_a_built_in_table_on_the_same_picture(qapp, editor_edition):
    """Swipe in the edition: a built-in table drawn into the editor's own picture right of
    the line, exactly, its name over that side; the swiped picture to copy or save; and How
    to use says how."""
    from matplotlib.colors import Normalize

    from colortable_editor import about
    from tcviz import colorize, colortable_preview
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    d = ColortableEditorDialog()
    assert not d.swipe_check.isVisibleTo(d)
    d.compare_combo.setCurrentIndex(d.compare_combo.findData("bd05"))
    d.flush()
    assert d.swipe_check.isVisibleTo(d) and d.compare_pane.holder.isVisibleTo(d)
    d.swipe_check.setChecked(True)
    d.flush()
    pane = d.picture_pane
    assert pane.swiping and not d.compare_pane.holder.isVisibleTo(d) and d.swipe_caption.text() == "bd05"
    cmap, norm, vmax, vmin = colortable_registry.get("bd05")
    norm = norm or Normalize(vmin=vmin, vmax=vmax)
    source = d._preview
    assert np.array_equal(pane.swipe_rgba(), colorize.rgba(source.values, cmap.with_extremes(bad="black"), norm))
    pane.resize(400, 400)
    pane.set_divider(0.25)
    d.scale_check.setChecked(False)
    image, factor = d.shared_picture()
    own, own_max, own_min = d.model.build()
    mine, _f = colortable_preview.full_picture(source, own, Normalize(vmin=own_min, vmax=own_max), own_max, own_min, "C",
                                               scale=False)
    theirs, _f = colortable_preview.full_picture(source, cmap, norm, vmax, vmin, "C", scale=False)
    split = pane.swipe_column() * factor
    got = np.asarray(image)
    assert 0 < split < image.width and image.size == mine.size
    assert np.array_equal(got[:, :split], np.asarray(mine)[:, :split])
    assert np.array_equal(got[:, split:], np.asarray(theirs)[:, split:])
    d.swipe_check.setChecked(False)
    d.flush()
    assert not pane.swiping and d.compare_pane.holder.isVisibleTo(d)
    assert "Swipe" in about.how_to_use_text()
    d.deleteLater()


def test_share_card_makes_one_picture_of_the_table_and_the_storm(qapp, editor_edition, tmp_path, monkeypatch):
    """Share card… in the edition: a card of the table's name, its scale -- the table's own
    colors, row for row -- and the sample with its two lines under it, made by the program
    by name; saved as a PNG file; and How to use says how."""
    from matplotlib.colors import Normalize
    from PIL import Image

    from colortable_editor import about
    from tcviz import colorize, share_card
    from tcviz_gui.pages import colortable_editor_dialog
    from tcviz_gui.pages.share_card_dialog import ShareCardDialog
    manifest = _manifest(editor_edition["editor"].parent / "samples", SAMPLES)
    monkeypatch.setattr(edition, "samples_manifest_path", lambda: manifest)
    monkeypatch.setattr(colortable_editor_dialog, "_last_save_folder", None)
    d = colortable_editor_dialog.ColortableEditorDialog()
    d.compare_combo.setCurrentIndex(d.compare_combo.findData("ott2"))
    d.flush()
    windows = []
    d._run = lambda window: windows.append(window) or 0
    window = d.open_share_card()
    assert isinstance(window, ShareCardDialog) and windows == [window]
    name = d.name_edit.text()
    assert window.title_edit.text() == f"{name} vs ott2"
    pictures = d.card_pictures()
    assert [p.tables[0].name for p in pictures] == [name, "ott2"]
    regions = {}
    card = share_card.share_card(pictures, window.title_edit.text(), "Made with Colortable Editor", regions=regions)
    assert np.array_equal(np.asarray(window.current_card()), np.asarray(card))
    assert [n for n, _box in regions["names"]] == [name, "ott2"]
    assert regions["captions"][0][1] == [SAMPLES[0]["description"], SAMPLES[0]["credit"]]
    assert regions["footer"][1] == "Made with Colortable Editor"
    # ott2's scale is ott2's colors, row for row
    cmap, norm, vmax, vmin = colortable_registry.get("ott2")
    left, top, right, bottom = regions["bars"][1]["box"]
    values = vmax - (np.arange(bottom - top) + 0.5) / (bottom - top) * (vmax - vmin)
    want = colorize.rgba(values[:, None], cmap.with_extremes(bad="black"), norm or Normalize(vmin=vmin, vmax=vmax))
    assert np.array_equal(np.asarray(card)[top:bottom, left:right], np.broadcast_to(want[..., :3], (bottom - top,
                                                                                                    right - left, 3)))
    d._ask_save_path = lambda suggested: str(tmp_path / "card")
    path = window.save_card()
    assert path == tmp_path / "card.png" and window.status.text().startswith("Saved the card, ")
    with Image.open(path) as saved:
        assert np.array_equal(np.asarray(saved), np.asarray(card))
    assert "Share card" in about.how_to_use_text()
    d.undo_stack.setClean()
    d.deleteLater()


def test_several_storms_shows_the_table_on_several_storms_at_once(qapp, editor_edition, tmp_path, monkeypatch):
    """Several storms in the edition: the storm grid in the picture's place, the storms of
    the table's kind ticked under Storms…, the water-vapor ones listed after them; each drawn
    in the table exactly (water vapor at 0 to -90 °C), read on a background thread; what is
    ticked, and the view, kept in the edition's own layout.json; a click on a storm bringing it
    to the front, where a click chooses the stop that colors it, and Back to grid showing them
    all alike again; a double-click showing it on its own; Save grid… writing one picture of
    them all; and How to use saying how."""
    import time

    from matplotlib.colors import Normalize
    from PIL import Image
    from PySide6.QtCore import QEventLoop

    from colortable_editor import about
    from tcviz import colorize, colortable_preview, window_layout
    from tcviz_gui.pages import colortable_editor_dialog
    from tcviz_gui.storm_grid import StormGrid
    manifest = _manifest(editor_edition["editor"].parent / "samples", SAMPLES)
    monkeypatch.setattr(edition, "samples_manifest_path", lambda: manifest)
    monkeypatch.setattr(colortable_editor_dialog, "_last_save_folder", None)

    def wait(until):
        deadline = time.monotonic() + 20
        while not until() and time.monotonic() < deadline:
            qapp.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            time.sleep(0.01)
        assert until()

    d = colortable_editor_dialog.ColortableEditorDialog()
    assert d.view() == "one" and d.several_storms_btn.text() == "Several storms" and d.several_storms_btn.isEnabled()
    assert d.set_view("several") and d.view() == "several"
    g = d.storm_grid
    assert isinstance(g, StormGrid) and g.isVisibleTo(d) and not d.one_page.isVisibleTo(d)
    assert g.ticked() == ["melissa_ir"] and list(g.checks) == ["melissa_ir", "melissa_wv"]
    g.checks["melissa_wv"].setChecked(True)
    wait(lambda: not g.busy())
    g.redraw()
    cmap, vmax, vmin = d.drawn_table()
    for sample_id, (top, bottom) in (("melissa_ir", (vmax, vmin)),
                                     ("melissa_wv", (colortable_preview.WV_VMAX_K, colortable_preview.WV_VMIN_K))):
        values = colortable_preview.load_bundled(d._bundled[sample_id]).full
        want = colorize.rgba(values, cmap.with_extremes(bad="black"), Normalize(vmin=bottom, vmax=top))
        assert np.array_equal(g.cell(sample_id).pane.rgba(), want), sample_id
    assert window_layout.get("storm_grid") == {"ir": ["melissa_ir", "melissa_wv"]}
    assert window_layout.get("editor") == {"view": "several"}
    assert _files_under(editor_edition["editor"]) == ["layout.json"]
    # a click on a storm brings it to the front; there a click chooses the stop that colors
    # that spot, as on the one picture
    values = colortable_preview.load_bundled(d._bundled["melissa_ir"]).full
    selected = d.selected
    g.cell("melissa_ir").pane.clicked.emit(5, 7, False)
    assert g.front == "melissa_ir" and d.selected == selected and g.back_btn.isEnabled()
    g.cell("melissa_ir").pane.clicked.emit(5, 7, False)
    assert d.selected == d.model.stop_for_value(float(values[5, 7]) - 273.15)
    wait(lambda: not g.busy())                          # the storm in front, read whole
    g.back_btn.click()
    assert g.front is None and not g.back_btn.isEnabled()
    # a double-click: the storm on its own
    assert g.open_storm("melissa_wv") and d.view() == "one" and d.picture_combo.currentData() == "bundled:melissa_wv"
    wait(lambda: d._loading is None)
    assert d._picture_choice == "bundled:melissa_wv"
    assert d.set_view("several")
    d._ask_save_path = lambda suggested: str(tmp_path / "storms.png")
    assert g.save_grid()
    wait(lambda: g.last_handover is not None and not g.busy())
    assert d.status.text().startswith("Saved the grid, ")
    with Image.open(tmp_path / "storms.png") as image:
        assert image.size == g.card.size and image.width <= 2400
    assert "Several storms" in about.how_to_use_text()
    # tcviz's own places were never touched
    assert not editor_edition["tcviz_store"].parent.exists()
    d.undo_stack.setClean()
    d.deleteLater()


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


# ------------------------------------------------------------------- History

def test_earlier_versions_load_back_into_the_editor_unsaved(qapp, editor_edition, monkeypatch):
    """Saved over, a table keeps the version it replaced; the editor's History… lists it, and
    Restore this version loads it as one undo step, writing nothing until Save."""
    from PySide6.QtWidgets import QMessageBox

    from tcviz import colortable_history, colortable_library
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    from tcviz_gui.pages.colortable_history_dialog import ColortableHistoryDialog
    from tcviz_gui.pages.manage_colortables_dialog import ManageColortablesDialog
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    folder = editor_edition["editor"]
    colortable_library.save("mine", _STOPS, description="first", vmin_c=-90.0, vmax_c=30.0)
    fresh = ColortableEditorDialog(entry=colortable_library.get("mine"), mode="edit")
    assert not fresh.history_btn.isEnabled()                  # saved once: no versions yet
    fresh.deleteLater()
    colortable_library.save("mine", [(0.0, "#000000"), (0.5, "#ff0000"), (1.0, "#ffffff")], original="mine",
                            description="second", vmin_c=-90.0, vmax_c=30.0)
    files = {f: (folder / f).read_bytes() for f in _files_under(folder)}

    d = ColortableEditorDialog(entry=colortable_library.get("mine"), mode="edit")
    assert d.history_btn.isEnabled()
    windows = []

    def answer(window):
        windows.append(window)
        window.version_list.setCurrentRow(0)
        window.action_btn.click()
        return window.result()
    d._run = answer
    d._on_history()
    assert isinstance(windows[0], ColortableHistoryDialog)
    assert windows[0].version_list.item(0).text().startswith("v1 · Today ")
    assert d.status.text() == "Version v1 loaded; press Save to keep it."
    assert [c for _v, c in d.model.stops] == ["#000000", "#808080", "#ffffff"] and d.desc_edit.text() == "first"
    assert {f: (folder / f).read_bytes() for f in _files_under(folder)} == files      # nothing written
    d.undo_stack.undo()
    assert [c for _v, c in d.model.stops] == ["#000000", "#ff0000", "#ffffff"] and d.desc_edit.text() == "second"
    d.undo_stack.redo()
    d._on_save()
    assert colortable_library.get("mine")["description"] == "first"
    assert [v.label for v in colortable_history.versions("mine")] == ["v2", "v1"]
    d.deleteLater()

    # the main window's list: right-click a table for History…; a built-in one has none
    manage = ManageColortablesDialog()
    _menu, actions = manage.row_menu("mine")
    assert {a.text(): a.isEnabled() for a in actions}["History…"]
    _menu, actions = manage.row_menu("bd05")
    assert not {a.text(): a.isEnabled() for a in actions}["History…"]
    assert "Hide" not in [a.text() for a in actions]
    manage.close()


# --------------------------------------------------------- closed before saving

def _offer(window, answer):
    """window.offer_draft_recovery(), the recovery window answered by `answer(recovery)`."""
    shown = []

    def run(dialog):
        shown.append(dialog)
        return answer(dialog)
    window._run = run
    result = window.offer_draft_recovery()
    assert result is (shown[0] if shown else None)
    return result


def test_a_table_left_unsaved_by_a_crash_is_offered_at_start_and_saved(qapp, editor_edition, monkeypatch):
    """The editor keeps an unsaved table in drafts/; the program dies with it open; at the
    next start the main window offers it, Open puts it in the editor marked changed, and
    Save keeps it -- a new, never-saved table as well as one of the person's own."""
    from PySide6.QtWidgets import QMessageBox

    from colortable_editor.main_window import MainWindow
    from tcviz import colortable_library
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda _p, _t, text, *a: asked.append(text)
                        or QMessageBox.StandardButton.No)
    folder = editor_edition["editor"]
    colortable_library.save("my_storm", _STOPS, description="before", vmin_c=-90.0, vmax_c=30.0)
    edited = _shown_editor(entry=colortable_library.get("my_storm"), mode="edit")
    edited.add_stop(-40.0, "#ff0000")
    edited.desc_edit.setText("after")
    _fire(edited)
    new = _shown_editor()
    new.name_edit.setText("brand_new")
    _fire(new)
    wanted = {"my_storm": edited._entry_now()["stops"], "brand_new": new._entry_now()["stops"]}
    for d in (edited, new):             # the program dies: nothing closes, the drafts stay
        d._autosaving = False
        d.hide()
        d.undo_stack.setClean()
        d.deleteLater()
    assert len(_files_under(folder / "drafts")) == 2

    window = MainWindow()
    opened = []

    def answer_editor(dialog):
        assert isinstance(dialog, ColortableEditorDialog) and dialog.has_changes() and dialog.save_btn.isEnabled()
        opened.append(dialog._original)
        dialog.reject()                                 # closing asks first; No keeps it open
        assert asked[-1] == "Close the editor and lose the changes you made to this table?"
        dialog._on_save()
        return 1

    def answer(recovery):
        assert recovery.heading.text() == "Colortable Editor closed before these tables were saved:"
        labels = sorted(label for label, _remark in recovery.row_texts())
        assert labels[0].startswith("my_storm (changed today at ")
        assert labels[1].startswith("new table 'brand_new' (changed today at ")
        recovery._run = answer_editor
        for row in list(recovery.rows.values()):
            row.open_btn.click()
        assert not recovery.rows and recovery.result() == 1     # closed by itself
        return 1
    _offer(window, answer)
    assert sorted(opened, key=str) == sorted(["my_storm", None], key=str)
    for name, stops in wanted.items():
        assert [tuple(s) for s in colortable_library.get(name)["stops"]] == [tuple(s) for s in stops], name
    assert colortable_library.get("my_storm")["description"] == "after"
    assert not (folder / "drafts").exists()
    assert window.panel.selected_name() in wanted         # the list shows what was saved
    # nothing left: the next start offers nothing
    assert _offer(window, lambda _d: pytest.fail("a window was shown")) is None
    window.close()


def test_discard_decide_later_and_damaged_drafts(qapp, editor_edition, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from colortable_editor.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    folder = editor_edition["editor"]
    entry = {"name": "kept_one", "stops": _STOPS, "vmin_c": -90.0, "vmax_c": 30.0}
    keep = _draft_file(folder, "b" * 32, entry, "add")
    drop = _draft_file(folder, "c" * 32, dict(entry, name="dropped_one"), "add", saved_at="2026-10-01T10:00:00+00:00")
    (folder / "drafts" / f"draft-{'d' * 32}.json").write_text("{damaged", encoding="utf-8")
    window = MainWindow()

    def answer(recovery):
        assert [line.text() for line in recovery.damaged_lines] == [
            f"The unsaved changes in draft-{'d' * 32}.json could not be read, so they can't be opened. The file "
            "was removed."]
        recovery.rows["c" * 32].discard_btn.click()
        assert list(recovery.rows) == ["b" * 32]
        assert recovery.later_btn.text() == "Decide later"
        recovery.later_btn.click()
        return recovery.result()
    _offer(window, answer)
    assert keep.exists() and not drop.exists()
    assert _files_under(folder / "drafts") == [keep.name]
    window.close()


# ------------------------------------------------- the real program, its buttons pressed

# Every other test answers the windows itself (_run and the pop-ups replaced): quick, but no
# real QDialog.exec() ever runs, and when one returns PySide6 6.11 hands the window to Python
# to keep or let go. Discard in the window offered at start crashed the whole program that
# way (2026-10-03) with every test green. These start the real program in a process of its
# own and press its real buttons, one step at a time from its event loop, as a person would:
# a crash fails the test with Python's trace of where it happened.
_PERSON = r'''
import faulthandler, gc, json, sys, weakref
faulthandler.enable()
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from colortable_editor import __main__ as program

STEPS = {
    "discard": ["recovery: Discard", "ask: Yes", "gone: DraftRecoveryDialog", "settle", "report"],
    "open-save": ["recovery: Open", "editor: Save", "gone: ColortableEditorDialog", "gone: DraftRecoveryDialog",
                  "settle", "report"],
    "open-close": ["recovery: Open", "editor: close", "ask: Yes", "gone: ColortableEditorDialog",
                   "gone: DraftRecoveryDialog", "settle", "report"],
    "new-close": ["main: New table", "editor: close", "gone: ColortableEditorDialog", "main: New table",
                  "editor: close", "gone: ColortableEditorDialog", "settle", "report"],
}[sys.argv[1]]
state = {"step": 0, "waited": 0, "turns": 0}
editors = []                     # every editor seen, to tell that a closed one was let go


def shown(kind):
    return [w for w in QApplication.topLevelWidgets() if type(w).__name__ == kind and w.isVisible()]


def press(do):
    QTimer.singleShot(0, do)     # from the event loop, as a click comes; not inside this step


def report():
    from tcviz import edition
    folder = edition.editor_config_dir()
    drafts = sorted(p.name for p in (folder / "drafts").glob("*.json")) if (folder / "drafts").is_dir() else []
    store = folder / "colortables.json"
    tables = [t["name"] for t in json.loads(store.read_text(encoding="utf-8"))] if store.exists() else []
    gc.collect()
    print("REPORT " + json.dumps({"drafts": drafts, "tables": tables, "main_window": len(shown("MainWindow")),
                                  "editors_seen": len(editors),
                                  "editors_kept": sum(ref() is not None for ref in editors)}), flush=True)


def step(what, arg):
    if what == "recovery":
        windows = shown("DraftRecoveryDialog")
        if windows:
            row = next(iter(windows[0].rows.values()))
            press((row.open_btn if arg == "Open" else row.discard_btn).click)
        return bool(windows)
    if what == "editor":
        windows = shown("ColortableEditorDialog")
        if windows:
            editors.append(weakref.ref(windows[0]))
            press(windows[0].save_btn.click if arg == "Save" else windows[0].reject)
        return bool(windows)
    if what == "ask":
        boxes = [w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()]
        if boxes:
            # clicked here and now: a pop-up's button clicked from a timer later was found
            # deleted (a pop-up only closes, so the step waits on nothing)
            boxes[0].button(getattr(QMessageBox.StandardButton, arg)).click()
        return bool(boxes)
    if what == "main":
        windows = shown("MainWindow")
        if windows:
            press(windows[0].new_action.trigger)
        return bool(windows)
    if what == "gone":
        return not shown(arg)
    if what == "settle":         # a few turns of the event loop: what was let go is deleted by now
        state["turns"] += 1
        return state["turns"] > 5
    if what == "report":
        report()
        for window in shown("MainWindow"):
            window.close()
        return True


def tick():
    if state["step"] >= len(STEPS):
        return
    what, _, arg = STEPS[state["step"]].partition(": ")
    if step(what, arg):
        state["step"] += 1
        state["waited"] = 0
        return
    state["waited"] += 1
    if state["waited"] > 150:
        print("STUCK at " + STEPS[state["step"]], flush=True)
        QApplication.exit(3)


_make_app = program._make_app


def make_app(argv):
    app = _make_app(argv)
    app._person = QTimer(app)
    app._person.timeout.connect(tick)
    app._person.start(100)
    return app


program._make_app = make_app
code = program.main(["ColortableEditor"])
print(f"EXIT {code}", flush=True)
sys.exit(code)
'''


def _as_a_person(tmp_path, scenario):
    """Colortable Editor started for real (not a smoke run) and used through `scenario`
    (_PERSON's STEPS); what it left behind."""
    env = _smoke_env(tmp_path)
    env.pop("COLORTABLE_EDITOR_SMOKE")
    run = subprocess.run([sys.executable, "-X", "faulthandler", "-c", _PERSON, scenario], cwd=ROOT, env=env,
                         capture_output=True, text=True, encoding="utf-8", timeout=180)
    said = f"exit code {run.returncode}\n{run.stdout[-2000:]}\n{run.stderr[-6000:]}"
    assert run.returncode == 0 and "EXIT 0" in run.stdout, said
    return json.loads(next(line for line in run.stdout.splitlines() if line.startswith("REPORT "))[7:])


def _left_over(tmp_path):
    return _draft_file(tmp_path / "editor", "e" * 32, {"name": "left_over", "stops": _STOPS, "vmin_c": -90.0,
                                                       "vmax_c": 30.0}, "add")


def test_discard_at_start_throws_the_table_away_and_the_program_carries_on(tmp_path):
    _left_over(tmp_path)
    report = _as_a_person(tmp_path, "discard")
    assert report["drafts"] == [] and report["tables"] == []
    assert report["main_window"] == 1


def test_open_at_start_then_save_keeps_the_table(tmp_path):
    _left_over(tmp_path)
    report = _as_a_person(tmp_path, "open-save")
    assert report["drafts"] == [] and report["tables"] == ["left_over"]
    assert report["main_window"] == 1
    assert report["editors_seen"] == 1 and report["editors_kept"] == 0


def test_open_at_start_then_close_without_saving_lets_it_go(tmp_path):
    _left_over(tmp_path)
    report = _as_a_person(tmp_path, "open-close")
    assert report["drafts"] == [] and report["tables"] == []
    assert report["main_window"] == 1
    assert report["editors_seen"] == 1 and report["editors_kept"] == 0


def test_a_closed_editor_is_let_go(tmp_path):
    """Each editor holds its pictures (13 MB for the first sample, far more for a VIIRS one);
    one kept after it closed was memory the program never got back."""
    report = _as_a_person(tmp_path, "new-close")
    assert report["editors_seen"] == 2 and report["editors_kept"] == 0
    assert report["drafts"] == [] and report["main_window"] == 1


# --------------------------------------------------------- the windows' layout

def test_the_windows_open_as_they_were_left_and_view_resets_them(qapp, editor_edition, monkeypatch):
    """layout.json in the edition's folder: the main window and the editor open the size
    they were left, fitted to the screen, the editor's color panel folded away or not;
    View -> Reset window layout (and the editor's own Reset layout) forget it all."""
    from colortable_editor.main_window import MainWindow
    from tcviz import window_layout
    from tcviz_gui import colortable_widgets
    from tcviz_gui.pages.colortable_editor_dialog import _help
    folder = editor_edition["editor"]
    room = [(1920, 1050)]
    monkeypatch.setattr(colortable_widgets, "screen_room", lambda _widget: room[0])
    assert window_layout.layout_path() == folder / "layout.json"

    # nothing kept: as the windows first open, and closing one that never showed keeps nothing
    window = MainWindow()
    assert window.size().toTuple() == (900, 560)
    window.close()
    assert not folder.exists()
    # shown, made bigger, closed: kept; and opened again at that size
    window = MainWindow()
    window.show()
    # a size the window can take whatever the fonts: its least grows with them (Windows'
    # are bigger than this PC's)
    least = window.minimumSizeHint()
    size = (max(1000, least.width() + 100), max(640, least.height() + 80))
    window.resize(*size)
    window.close()
    assert window_layout.get("main_window") == {"size": list(size), "maximized": False}
    assert _files_under(folder) == ["layout.json"]
    assert MainWindow().size().toTuple() == size
    # a size left on a big screen, on a laptop's: fitted to it
    window_layout.update("main_window", {"size": [2400, 1300]})
    room[0] = (1366, 728)
    assert MainWindow().size().toTuple() == (1326, 668)

    # the editor: its own record beside the main window's, the color panel folded away
    d = _shown_editor()
    assert d.help_popup.text() == _help(d.model) and d.help_btn.toolTip() == "How to use this window"
    d.fold_colors(True)
    d.resize(1300, 700)
    d.reject()
    d.deleteLater()
    assert window_layout.get("editor")["splitter"][0] == 0 and window_layout.get("main_window")["size"] == [2400, 1300]
    d = _shown_editor()
    # (fitted to the laptop's screen: 700 down is more than it has room for)
    assert d.colors_folded() and d.width() >= 1300 and d.height() == 668
    d.hide()
    d.deleteLater()

    # View -> Reset window layout: the file goes, and the main window is back as it first opens
    window = MainWindow()
    window.show()
    assert window.size().toTuple() == (1326, 668)
    window.reset_layout_action.trigger()
    # (on screen, never smaller than its least size, which bigger fonts make wider)
    first = (max(900, window.minimumWidth()), max(560, window.minimumHeight()))
    assert not (folder / "layout.json").exists() and window.size().toTuple() == first
    window.close()                      # as it first opens: nothing to keep
    assert not (folder / "layout.json").exists()
    d = _shown_editor()
    # as it first opens: the color panel shown -- unless the laptop's screen is too narrow
    # for the whole window, as bigger fonts make it (then it opens folded, to fit)
    folded = d.colors_folded()
    d.fold_colors(False)
    assert folded == (d.minimumSizeHint().width() > 1366 - window_layout.SCREEN_MARGIN[0])
    d.hide()
    d.deleteLater()
    # tcviz's own places were never touched
    assert not editor_edition["tcviz_store"].parent.exists()
    assert _files_under(editor_edition["tcviz_config"]) == []


# --------------------------------------------------------- About and How to use

def test_about_says_who_made_what(qapp, editor_edition):
    from colortable_editor import about
    from colortable_editor.main_window import MainWindow
    lines = about.about_lines()
    credit = ("Sample pictures: NOAA GOES, and NOAA-20, NOAA-21 and Suomi NPP VIIRS, from NOAA Open Data "
              "Dissemination; GOES-12 (Katrina 2005) from NOAA NCEI's GridSat-GOES; NASA Terra and Aqua "
              "MODIS and Suomi NPP VIIRS (Haiyan 2013) from NASA LAADS DAAC; GCOM-C SGLI (Yutu 2018) from "
              "JAXA G-Portal. Original data for this value added data product was provided by Japan "
              "Aerospace Exploration Agency")
    assert lines == ["Colortable Editor 1.1.0", "by OneFortyTwoK", "MIT License", "", about.BLURB, "", credit]

    window = MainWindow()
    shown = window.about_dialog().plain_text()
    for words in ("Colortable Editor 1.1.0", "by OneFortyTwoK", "MIT License", credit):
        assert words in shown, words
    how = window.how_to_use_dialog().plain_text()
    assert "Making a table" in how and str(editor_edition["editor"]) in how
    for file in ("colortables.json", "favorite_palettes.json", "colortables.deleted.json", "colortables.history.json",
                 "drafts folder", "layout.json"):
        assert file in how, file
    assert "If the program closes unexpectedly" in how
    assert "More room for the picture" in how and "The ? button" in how
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
    # View, since 2026-10-03: Reset window layout (layout.json)
    assert menus == {"File": ["New table…", "Import…", "Export…", "Quit"], "View": ["Reset window layout"],
                     "Help": ["How to use", "About Colortable Editor"]}
    window.close()
