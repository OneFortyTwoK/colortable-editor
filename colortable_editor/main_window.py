"""Colortable Editor's main window: the Manage colortables view as its content -- the list of
tables with New, Edit, Duplicate, Rename, Delete (and Restore), Import and Export -- under a
menu bar (File, View, Help), the About and How to use windows, and at start the offer of any
table an editor left unsaved when the program last closed (offer_draft_recovery).

It opens the size it was left (tcviz.window_layout, "main_window" in layout.json, kept as it
closes), and View -> Reset window layout forgets that and the editor's layout both."""
import contextlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QTextBrowser, QVBoxLayout,
)

from colortable_editor import APP_NAME, AUTHOR, LICENSE, SAMPLES_CREDIT, __version__, about
from colortable_editor.icon import pixmap
from tcviz import colortable_registry, user_colortables, window_layout
from tcviz_gui import colortable_widgets
from tcviz_gui.pages import draft_recovery_dialog
from tcviz_gui.pages.manage_colortables_dialog import ManageColortablesDialog

# The main window's record in layout.json, and the size it first opens at (no bigger than
# the screen).
_LAYOUT_KEY = "main_window"
_DEFAULT_SIZE = (900, 560)


class ManagePanel(ManageColortablesDialog):
    """The Manage colortables window as a main window's content: no Close button, and
    Escape -- which would hide a dialog -- leaves it where it is."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Widget)
        self.close_box.hide()

    def reject(self):
        pass                         # Escape: the main window stays whole


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.panel = ManagePanel(self)
        self.setCentralWidget(self.panel)
        self._build_menus()
        self._restore_layout()

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("&File")
        self.new_action = self._action(file_menu, "&New table…", self.panel._on_new, QKeySequence.StandardKey.New,
                                       "Make a new color table of your own.")
        self.import_action = self._action(file_menu, "&Import…", self.panel._on_import, "Ctrl+I",
                                          "Bring in color tables from files or from text you paste.")
        self.export_action = self._action(file_menu, "&Export…", self.panel._on_export, "Ctrl+E",
                                          "Save the chosen table to a file, ready to send to a friend.")
        file_menu.addSeparator()
        self.quit_action = self._action(file_menu, "&Quit", self.close, QKeySequence.StandardKey.Quit,
                                        f"Close {APP_NAME}.")
        view_menu = self.menuBar().addMenu("&View")
        self.reset_layout_action = self._action(
            view_menu, "Reset window &layout", self.reset_layout, None,
            "Put the windows back to the size they first open at, with the editor's color panel shown.")
        help_menu = self.menuBar().addMenu("&Help")
        self.how_to_action = self._action(help_menu, "&How to use", self.show_how_to_use,
                                          QKeySequence.StandardKey.HelpContents, "A short guide to the editor.")
        self.about_action = self._action(help_menu, f"&About {APP_NAME}", self.show_about, None,
                                         f"Who made {APP_NAME}, and where its tables and pictures come from.")

    def _action(self, menu, text, slot, keys, tip):
        action = QAction(text, self)
        if keys is not None:
            action.setShortcut(QKeySequence(keys))
        action.setStatusTip(tip)
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)
        return action

    # ------------------------------------------------------------------ the window's layout

    def _restore_layout(self):
        """Open the size the window was left (window_layout), made to fit this screen, and
        maximized if it was; with nothing saved, the size it first opens at."""
        kept = window_layout.get(_LAYOUT_KEY)
        room = colortable_widgets.screen_room(self)
        self.resize(*(window_layout.fit_size(kept.get("size"), room) or window_layout.fit_size(_DEFAULT_SIZE, room)))
        # the layout as the window is shown (showEvent): closing keeps it only if it changed
        self._layout_base = None
        if kept.get("maximized") is True:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def reset_layout(self):
        """View -> Reset window layout: forget every window's layout -- layout.json goes, the
        editor's too -- and put this window back to the size it first opens at."""
        window_layout.reset()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMaximized)
        self.resize(*window_layout.fit_size(_DEFAULT_SIZE, colortable_widgets.screen_room(self)))
        if self._layout_base is not None:
            # back as it first opens is nothing to keep
            self._layout_base = self._layout_record()

    def _layout_record(self):
        """The window's layout as window_layout keeps it: its size (when maximized, the size
        it goes back to) and whether it is maximized."""
        maximized = self.isMaximized()
        size = self.normalGeometry().size() if maximized else self.size()
        if size.isEmpty():
            size = self.size()
        return {"size": [size.width(), size.height()], "maximized": maximized}

    def showEvent(self, event):
        super().showEvent(event)
        if self._layout_base is None:
            self._layout_base = self._layout_record()

    def closeEvent(self, event):
        # kept for next time only if it changed while open: a window never shown (the smoke
        # run) chose no layout, and keeps none; and a layout that can't be kept never keeps
        # the program from closing
        with contextlib.suppress(Exception):
            self._remember_layout()
        super().closeEvent(event)

    def _remember_layout(self):
        if self._layout_base is not None:
            now = self._layout_record()
            if now != self._layout_base:
                window_layout.update(_LAYOUT_KEY, now)
            self._layout_base = now

    # ------------------------------------------------------------------ Help

    def about_dialog(self):
        """The About window: about.about_lines, laid out."""
        parts = [f"<p>by {_escaped(AUTHOR)}<br>{_escaped(LICENSE)}</p>", f"<p>{_escaped(about.BLURB)}</p>"]
        name, _colon, rest = SAMPLES_CREDIT.partition(": ")
        parts.append(f"<p><b>{_escaped(name)}</b>: {_escaped(rest)}</p>")
        return TextWindow(self, f"About {APP_NAME}", "".join(parts), heading=f"{APP_NAME} {__version__}",
                          size=(560, 300))

    def how_to_use_dialog(self):
        """The How to use window: about.HOW_TO_USE, and where the tables are kept."""
        parts = [f"<p><b>{_escaped(heading)}</b><br>{_escaped(text)}</p>" for heading, text in about.HOW_TO_USE]
        parts.append(f"<p><b>Where your tables are kept</b><br>"
                     f"{_escaped(str(user_colortables.store_path().parent))}<br>"
                     f"{_escaped(about.kept_files_text())}</p>")
        return TextWindow(self, "How to use", "".join(parts), heading=f"How to use {APP_NAME}", size=(660, 640))

    def show_about(self):
        self.about_dialog().exec()

    def show_how_to_use(self):
        self.how_to_use_dialog().exec()

    # ------------------------------------------------------------------ at start

    def startup_problems(self):
        """One sentence per table that could not be loaded, or about the tables file itself."""
        return colortable_registry.user_table_problems()

    def say_startup_problems(self):
        problems = self.startup_problems()
        if problems:
            QMessageBox.warning(self, "Some color tables could not be loaded", "\n\n".join(problems))

    def offer_draft_recovery(self):
        """The tables an editor left unsaved when the program last closed, offered back in one
        window (tcviz_gui.pages.draft_recovery_dialog); nothing when there are none. A table
        saved from there is chosen in the list. The window shown, or None."""
        return draft_recovery_dialog.offer(self, on_saved=self._on_draft_saved)

    def _on_draft_saved(self, name):
        self.panel.refresh(select=name)
        self.panel._say(f"Saved '{name}'.")

    def _run(self, dialog):
        return dialog.exec()


class TextWindow(QDialog):
    """A short read: the About text, or How to use -- the icon and a heading, then the text
    (simple HTML: paragraphs, bold, links that open in the browser)."""

    def __init__(self, parent, title, html, heading, size=(620, 460)):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        picture = QLabel()
        picture.setPixmap(pixmap(48))
        top.addWidget(picture)
        self.heading = QLabel(heading)
        font = self.heading.font()
        font.setPointSizeF(font.pointSizeF() * 1.5)
        font.setBold(True)
        self.heading.setFont(font)
        top.addWidget(self.heading, 1)
        layout.addLayout(top)
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(True)
        self.text.setHtml(html)
        layout.addWidget(self.text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(*size)

    def plain_text(self):
        """The heading and the text, as a person reads them."""
        return f"{self.heading.text()}\n{self.text.toPlainText()}"


def _escaped(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
