"""`python -m colortable_editor` -- starts Colortable Editor.

COLORTABLE_EDITOR_SMOKE=1 instead builds the main window off screen, draws one bundled
sample through one built-in table in the editor, prints one line and exits 0 (1 if any of
that fails): the packaged build runs it on Linux and Windows to prove the program starts.
COLORTABLE_EDITOR_SMOKE_FILE names a file the line is written to as well.
"""
import os
import sys
from pathlib import Path

# Run from anywhere: the repo root (or the frozen bundle) on the path, as tcviz_gui does.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SMOKE_ENV = "COLORTABLE_EDITOR_SMOKE"
SMOKE_FILE_ENV = "COLORTABLE_EDITOR_SMOKE_FILE"
# Windows groups a program's windows on the taskbar, and takes their icon, by this id; a
# Python program otherwise shows as python.exe with Python's icon.
_WINDOWS_APP_ID = "OneFortyTwoK.ColortableEditor"


def _smoke_wanted():
    return os.environ.get(SMOKE_ENV, "").strip().lower() not in ("", "0", "false", "no", "off")


def _prepare_process(smoke):
    """What must be settled before Qt starts: the edition, the drawing in numpy (the edition
    ships no Rust module, and drawing is exact without one), and for a smoke run a screen
    that needs no display."""
    from tcviz import edition
    edition.use(edition.COLORTABLE_EDITOR)
    os.environ["TCVIZ_NO_NATIVE"] = "1"
    if smoke:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_WINDOWS_APP_ID)
        except (AttributeError, OSError):
            pass                     # an old Windows: only the taskbar icon is Python's


def _make_app(argv):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from colortable_editor import APP_NAME, AUTHOR, __version__
    from colortable_editor.icon import icon
    from tcviz_gui.theme import apply_dark_theme
    from tcviz_gui.worker import GuiThreadGarbageCollector

    # sharp on a high-DPI screen at 125% or 150% as well as 200%: Qt scales by the exact factor
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(AUTHOR)
    app.setWindowIcon(icon())
    apply_dark_theme(app)
    # cycles freed on this thread only: a window freed on a job's thread crashes the app
    app._garbage = GuiThreadGarbageCollector(app)
    return app


def smoke(window):
    """Draw one bundled sample through one built-in table in the editor; the line to print."""
    from colortable_editor import APP_NAME, __version__
    from tcviz import colortable_library, colortable_preview, colortable_registry, fonts, user_colortables
    from tcviz_gui.pages import add_colortable_dialog, import_colortables_dialog  # noqa: F401 -- loaded, as the menus would
    from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog

    names = list(colortable_registry.palettes())
    table = "ott2" if "ott2" in names else names[0]
    dialog = ColortableEditorDialog(window, entry=colortable_library.duplicate_draft(table), mode="copy", source=table)
    try:
        dialog.flush()
        source = dialog._preview
        if source is None or dialog.picture_pane.message:
            raise RuntimeError(f"the editor shows no picture ({dialog.status.text() or dialog.picture_pane.message})")
        rgba = dialog.picture_pane.rgba()
        colors = len({tuple(c) for c in rgba.reshape(-1, 4)[:, :3].tolist()})
        rows, cols = source.full_shape
        samples = len(colortable_preview.bundled_samples())
    finally:
        dialog.deleteLater()
    # The program's other files, where a packaged build unpacks them: the two older samples
    # the Picture box also lists (vendor/samples) and the font a saved picture's scale is
    # lettered in (vendor/fonts). A build without one would start, and only show it later.
    older = [colortable_preview.load_sample(kind) for kind in ("ir", "wv")]
    if not Path(fonts._BUNDLED).is_file():
        raise RuntimeError(f"the font is missing ({fonts._BUNDLED})")
    listed = window.panel.table_list.topLevelItemCount()
    return (f"{APP_NAME} {__version__} smoke: {listed} tables listed ({len(names)} known), {samples} bundled samples "
            f"and {len(older)} older ones; '{source.label}' drawn through {table}: {cols} x {rows} pixels in {colors} "
            f"colors; font {Path(fonts._BUNDLED).name}; tables kept in {user_colortables.store_path().parent}")


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    wanted = _smoke_wanted()
    _prepare_process(wanted)
    app = _make_app(argv)
    from colortable_editor.main_window import MainWindow
    from tcviz_gui.worker import stop_all_jobs

    window = MainWindow()
    if wanted:
        try:
            _say_smoke(smoke(window), sys.stdout)
        except Exception as e:  # noqa: BLE001 -- the smoke run reports any failure as its exit code
            _say_smoke(f"Colortable Editor smoke FAILED: {type(e).__name__}: {e}", sys.stderr)
            return 1
        finally:
            window.close()
        return 0
    window.show()
    window.say_startup_problems()
    # a table left unsaved when the program last closed (never in a smoke run, which
    # returned above: a build check must not wait on a window)
    window.offer_draft_recovery()
    code = app.exec()
    # a job still running (a picture being read) must not reach Python's own teardown,
    # which aborts on a running QThread
    if not stop_all_jobs(wait_s=3.0):
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:       # None in the Windows program, which has no console
                stream.flush()
        os._exit(code)
    return code


def _say_smoke(line, stream):
    """The smoke run's one line: printed, and written to the file COLORTABLE_EDITOR_SMOKE_FILE
    names -- the Windows program has no console (sys.stdout is None there), so a build check
    reads the file instead."""
    if stream is not None:
        print(line, file=stream, flush=True)
    path = os.environ.get(SMOKE_FILE_ENV, "").strip()
    if path:
        Path(path).write_text(line + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
