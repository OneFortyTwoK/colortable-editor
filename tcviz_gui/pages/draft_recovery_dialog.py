"""The window that offers back, when the program starts, the color tables an editor left
unsaved when the program last closed (tcviz.colortable_drafts): Colortable Editor's and
tcviz's main windows both show it, and only when there is something to show.

One row per table, newest first, each with Open -- the editor on the unsaved table, marked
changed, so Save keeps it and Cancel asks before letting it go -- and Discard. "Decide
later" closes the window and keeps every draft for the next start. A row goes once its
table is saved or let go; the window closes by itself when no row is left.

A file in the drafts folder that does not read as a draft is said in a sentence and
removed: it can never be opened.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from tcviz import colortable_drafts, colortable_history, edition


def recovery_window(parent=None):
    """The window for the drafts left behind, or None when there are none. Damaged files
    are removed here, and the window says so."""
    damaged = []
    drafts = colortable_drafts.leftovers(damaged)
    if not drafts and not damaged:
        return None
    for item in damaged:
        try:
            colortable_drafts.discard(item)
        except OSError:
            pass                     # said all the same; tried again at the next start
    return DraftRecoveryDialog(parent, drafts, damaged)


class DraftRecoveryDialog(QDialog):
    saved = Signal(str)             # a table opened here was saved, under this name

    def __init__(self, parent, drafts, damaged=()):
        super().__init__(parent)
        self.setWindowTitle("Unsaved color tables")
        self.drafts = list(drafts)
        self.rows = {}                      # draft id -> its row
        app = edition.app_name()
        layout = QVBoxLayout(self)
        if self.drafts:
            some = "these tables were" if len(self.drafts) > 1 else "this table was"
            self.heading = QLabel(f"{app} closed before {some} saved:")
        else:
            self.heading = QLabel(f"{app} closed before some changes were saved.")
        font = self.heading.font()
        font.setBold(True)
        self.heading.setFont(font)
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        for draft in self.drafts:
            row = self._row(draft)
            self.rows[draft.id] = row
            layout.addWidget(row)
        self.damaged_lines = []
        for item in damaged:
            line = QLabel(f"{item.text} The file was removed.")
            line.setWordWrap(True)
            layout.addWidget(line)
            self.damaged_lines.append(line)
        self.hint = QLabel(f"Open shows a table in the editor with your changes; press Save there to keep them. "
                           f"Discard throws the changes away. Decide later keeps them for the next time {app} "
                           "starts.")
        self.hint.setWordWrap(True)
        self.hint.setVisible(bool(self.drafts))
        layout.addSpacing(6)
        layout.addWidget(self.hint)
        buttons = QDialogButtonBox()
        self.later_btn = buttons.addButton("Decide later" if self.drafts else "Close",
                                           QDialogButtonBox.ButtonRole.RejectRole)
        self.later_btn.setToolTip(f"Close this window and keep the unsaved tables for the next time {app} starts."
                                  if self.drafts else "Close this window.")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(560, self.sizeHint().height())

    def _row(self, draft):
        row = QFrame()
        row.setFrameShape(QFrame.Shape.StyledPanel)
        outer = QHBoxLayout(row)
        words = QVBoxLayout()
        # the table in bold, what to know about it under it in smaller type
        row.label = QLabel(colortable_drafts.describe(draft))
        row.label.setWordWrap(True)
        bold = row.label.font()
        bold.setBold(True)
        row.label.setFont(bold)
        words.addWidget(row.label)
        row.remark = QLabel(colortable_drafts.remark(draft))
        row.remark.setWordWrap(True)
        row.remark.setVisible(bool(row.remark.text()))
        small = row.remark.font()
        small.setPointSizeF(small.pointSizeF() * 0.9)
        row.remark.setFont(small)
        words.addWidget(row.remark)
        outer.addLayout(words, 1)
        row.open_btn = QPushButton("Open")
        row.open_btn.setToolTip("Open this table in the editor with your unsaved changes; Save there keeps them.")
        row.open_btn.clicked.connect(lambda _checked=False, d=draft: self.open_draft(d))
        row.discard_btn = QPushButton("Discard")
        row.discard_btn.setToolTip("Throw these unsaved changes away.")
        row.discard_btn.clicked.connect(lambda _checked=False, d=draft: self.discard_draft(d))
        for button in (row.open_btn, row.discard_btn):
            outer.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def row_texts(self):
        """Each row's words, as a person reads them (for tests and screenshots)."""
        return [(row.label.text(), row.remark.text()) for row in self.rows.values()]

    def open_draft(self, draft):
        """The editor on `draft`, marked changed. The row goes once the table is saved or
        let go there (the editor removes the draft either way)."""
        from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
        # the earlier drafts of the same table are older states of this one
        try:
            colortable_drafts.drop_older(draft)
        except OSError:
            pass
        dialog = ColortableEditorDialog(self, entry=draft.entry, mode=draft.mode, source=draft.source, draft=draft)
        self._run(dialog)
        if dialog.entry:
            self.saved.emit(dialog.entry["name"])
        if not draft.path.exists():
            self._done(draft)
        dialog.deleteLater()

    def discard_draft(self, draft):
        """Throw the unsaved table away, once the person says so."""
        if not self._confirm_discard(draft):
            return
        try:
            colortable_drafts.discard(draft)
        except OSError as e:
            self._warn("Couldn't discard the changes",
                       f"The unsaved changes could not be removed: {colortable_history.plain_reason(e)}. They are "
                       "offered again the next time the program starts.")
            return
        self._done(draft)

    def _done(self, draft):
        row = self.rows.pop(draft.id, None)
        if row is not None:
            row.hide()
            row.deleteLater()
        if not self.rows:
            self.accept()

    # The pop-ups, each in one place so a test can answer it.

    def _confirm_discard(self, draft):
        what = f"'{draft.original}'" if draft.mode == "edit" else "this new table"
        answer = QMessageBox.question(
            self, "Discard unsaved changes?", f"Throw away the unsaved changes to {what}? This can't be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _warn(self, title, text):
        QMessageBox.warning(self, title, text)

    def _run(self, dialog):
        return dialog.exec()


def offer(window, on_saved=None):
    """Show the offer over `window` (a main window with a `_run(dialog)`), if there is
    anything to offer; `on_saved(name)` is told each table saved from it. The window shown,
    or None."""
    recovery = recovery_window(window)
    if recovery is None:
        return None
    if on_saved is not None:
        recovery.saved.connect(on_saved)
    window._run(recovery)
    return recovery
