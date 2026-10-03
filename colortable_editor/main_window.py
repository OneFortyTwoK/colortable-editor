"""Colortable Editor's main window: the Manage colortables view as its content -- the list of
tables with New, Edit, Duplicate, Rename, Delete (and Restore), Import and Export -- under a
menu bar (File, Help), and the About and How to use windows."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QTextBrowser, QVBoxLayout,
)

from colortable_editor import APP_NAME, AUTHOR, LICENSE, SAMPLES_CREDIT, __version__, about
from colortable_editor.icon import pixmap
from tcviz import colortable_registry, user_colortables
from tcviz_gui.pages.manage_colortables_dialog import ManageColortablesDialog


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
        self.resize(900, 560)

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
                     f"{_escaped(str(user_colortables.store_path().parent))}</p>")
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
