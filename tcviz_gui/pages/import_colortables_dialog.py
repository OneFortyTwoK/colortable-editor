"""The Import window: color tables from files or pasted text, looked over before saving.

Files come in by dropping them on the window or through "Choose files...", text through
the paste box. Every table found (tcviz.colortable_import.parse_any) becomes a row with
its name, kind, range, a swatch and a status that says in plain words whether it is
ready, what was guessed or left unread, and under what name it will be saved -- a name
that is already taken by one of your tables or a built-in one gets a free one
(name_copy, name_copy2, ...) instead of replacing anything. Tick the rows to keep, fix
any name by double-clicking it, and "Save selected" stores them
(user_colortables.add) and makes them live (colortable_registry.register_user_colortable).

In Colortable Editor (tcviz.edition) only infrared and water-vapor tables come in
(colortable_import leaves the rest out, and says so), and the window does not offer the
radar formats.

Standalone: the Manage window opens it with open_import_dialog(parent), which returns
the names saved.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QImage, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from tcviz import colortable_import, edition, user_colortables

_SWATCH_W, _SWATCH_H = 140, 16
_FILE_FILTER = ("Color tables (*.txt *.py *.pal *.PAL *.tbl *.cpt *.csv *.json);;"
                "All files (*)")
# Colortable Editor's: no .pal, which holds radar tables
_EDITOR_FILE_FILTER = "Color tables (*.txt *.py *.tbl *.cpt *.csv *.json);;All files (*)"
NAME, KIND, RANGE, COLORS, STATUS = range(5)
_HEADERS = ("Name", "Kind", "Range", "Colors", "Status")


@dataclass
class _Row:
    draft: object
    name: str
    checked: bool
    swatch: object = None
    planned: str = None      # the name it will be saved under, when it will be saved
    saved_as: str = None
    error: str = None


class DropArea(QFrame):
    """Where files (or text) are dropped. A file manager's drop carries both the file's
    address and its text; the file wins."""
    files_dropped = Signal(list)
    text_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropArea")
        self.setAcceptDrops(True)
        self.setMinimumHeight(64)
        self._set_hover(False)

    @staticmethod
    def _accepts(mime):
        return any(url.isLocalFile() for url in mime.urls()) or \
            (mime.hasText() and bool(mime.text().strip()))

    def _set_hover(self, hover):
        color = "palette(highlight)" if hover else "palette(mid)"
        self.setStyleSheet(f"#dropArea {{ border: 2px dashed {color}; border-radius: 6px; }}")

    def dragEnterEvent(self, event):
        if self._accepts(event.mimeData()):
            self._set_hover(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_hover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_hover(False)
        mime = event.mimeData()
        paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
        elif mime.hasText() and mime.text().strip():
            self.text_dropped.emit(mime.text())
        else:
            event.ignore()
            return
        event.acceptProposedAction()


class ImportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import color tables")
        self.saved_names = []
        self._rows = []
        self._taken = None
        self._filling = False
        self._build_ui()
        self.setAcceptDrops(True)       # a drop anywhere on the window counts
        self.resize(900, 600)

    # ------------------------------------------------------------------------ layout

    def _build_ui(self):
        layout = QVBoxLayout(self)
        if edition.is_editor():
            text = ("Bring in infrared and water-vapor color tables from files or from text you paste. "
                    f"{edition.app_name()} reads the tables it shares (def name(): ...), stop lists, MetPy .tbl "
                    "and GMT .cpt files, and plain lists of colors: #hex, rgb(...), red green blue numbers "
                    "or color names, each with or without a position in front.")
        else:
            text = ("Bring in color tables from files or from text you paste. tcviz reads its own "
                    "shared tables (def name(): ...), stop lists, GRLevelX .pal, MetPy .tbl and GMT "
                    ".cpt files, and plain lists of colors: #hex, rgb(...), red green blue numbers "
                    "or color names, each with or without a position in front.")
        intro = QLabel(text)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.drop_area = DropArea()
        drop_row = QHBoxLayout(self.drop_area)
        drop_label = QLabel("Drop color table files here, or")
        drop_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        drop_row.addStretch()
        drop_row.addWidget(drop_label)
        self.choose_button = QPushButton("Choose files...")
        self.choose_button.clicked.connect(self._on_choose)
        drop_row.addWidget(self.choose_button)
        drop_row.addStretch()
        self.drop_area.files_dropped.connect(self.add_files)
        self.drop_area.text_dropped.connect(self.add_text)
        layout.addWidget(self.drop_area)

        paste_row = QHBoxLayout()
        self.paste_edit = QPlainTextEdit()
        self.paste_edit.setPlaceholderText("...or paste a color table here, then press Read")
        self.paste_edit.setFixedHeight(84)
        paste_row.addWidget(self.paste_edit, 1)
        self.read_button = QPushButton("Read")
        self.read_button.setToolTip("Read the color tables in the text above into the list")
        self.read_button.clicked.connect(self._on_read_pasted)
        paste_row.addWidget(self.read_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(paste_row)

        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(QSize(_SWATCH_W, _SWATCH_H))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        for column, width in ((NAME, 190), (KIND, 100), (RANGE, 130), (COLORS, _SWATCH_W + 16)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(STATUS, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._show_notes)
        layout.addWidget(self.table, 1)

        hint = QLabel("Tick the tables to save. Double-click a name to change it. "
                      "Select a row to read its notes.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Everything a draft says about itself, for the row selected: the status cell only
        # has room for what matters most.
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.notes.setFrameShape(QFrame.Shape.StyledPanel)
        self.notes.setMinimumHeight(48)
        self.notes.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.notes.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(self.notes)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        # A whole exported collection is 200-odd rows: one click to start from none of them.
        self.tick_all_button = buttons.addButton("Tick all", QDialogButtonBox.ButtonRole.ActionRole)
        self.tick_all_button.clicked.connect(lambda: self._tick_all(True))
        self.untick_all_button = buttons.addButton("Untick all", QDialogButtonBox.ButtonRole.ActionRole)
        self.untick_all_button.clicked.connect(lambda: self._tick_all(False))
        self.remove_button = buttons.addButton("Remove from list",
                                               QDialogButtonBox.ButtonRole.ActionRole)
        self.remove_button.clicked.connect(self._on_remove)
        self.save_button = buttons.addButton("Save selected", QDialogButtonBox.ButtonRole.ApplyRole)
        self.save_button.clicked.connect(self.save_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    # --------------------------------------------------------------- drops anywhere

    def dragEnterEvent(self, event):
        self.drop_area.dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.drop_area.dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self.drop_area.dragLeaveEvent(event)

    def dropEvent(self, event):
        self.drop_area.dropEvent(event)

    # -------------------------------------------------------------- reading tables in

    def add_files(self, paths):
        """Read every file in `paths` into the list; returns the drafts added."""
        added, problems, files = [], [], 0
        for path in paths:
            try:
                added += colortable_import.parse_any(Path(path))
                files += 1
            except colortable_import.ImportProblem as e:
                problems.append(str(e))
        self._add_drafts(added)
        found = (f"Read {_count(len(added), 'table')} from {_count(files, 'file')}."
                 if added else "")
        self.message.setText(" ".join([found] + problems).strip())
        return added

    def add_text(self, text):
        """Read the tables in `text` into the list; returns the drafts added."""
        try:
            added = colortable_import.parse_any(text)
        except colortable_import.ImportProblem as e:
            self.message.setText(str(e))
            return []
        self._add_drafts(added)
        self.message.setText(f"Read {_count(len(added), 'table')} from the pasted text.")
        return added

    def _on_read_pasted(self):
        text = self.paste_edit.toPlainText()
        if not text.strip():
            self.message.setText("There is nothing in the paste box to read.")
            return
        if self.add_text(text):
            self.paste_edit.clear()

    def _on_choose(self):
        paths, _filter = QFileDialog.getOpenFileNames(self, "Choose color table files", "",
                                                      _EDITOR_FILE_FILTER if edition.is_editor() else _FILE_FILTER)
        if paths:
            self.add_files(paths)

    def _add_drafts(self, drafts):
        first = len(self._rows)
        for draft in drafts:
            self._rows.append(_Row(draft, draft.name, checked=draft.can_save, swatch=_swatch(draft)))
        self._refresh()
        if drafts:
            # the first new row with something to say, so its notes are on show at once
            pick = next((first + i for i, d in enumerate(drafts) if d.warnings or d.problem), first)
            self.table.selectRow(pick)

    def _tick_all(self, ticked):
        for row in self._rows:
            if row.draft.can_save and not row.saved_as:
                row.checked = ticked
        self._refresh()

    def _on_remove(self):
        chosen = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        if not chosen:
            self.message.setText("Select the rows to remove first.")
            return
        for row in chosen:
            del self._rows[row]
        self._refresh()

    # ------------------------------------------------------------------- the rows

    def _taken_names(self):
        if self._taken is None:
            self._taken = colortable_import.taken_names()
        return self._taken

    def _plan(self):
        """Pick the name each ticked row will be saved under: its own, or the next free one.
        Rows earlier in the list get theirs first, so two tables called the same in one
        batch are saved as name and name_copy."""
        used = set(self._taken_names())
        for row in self._rows:
            row.planned = None
            if row.saved_as or not row.checked or not row.draft.can_save or not _valid(row.name):
                continue
            row.planned = colortable_import.free_name(row.name, used)
            used.add(row.planned)

    def _status(self, row):
        """(text, tooltip) for a row's Status cell: what happens to it, then how many notes
        it has (the notes box under the list shows them)."""
        draft = row.draft
        if row.saved_as:
            text = f"Saved as {row.saved_as}"
        elif row.error:
            text = row.error
        elif draft.problem:
            text = draft.problem
        elif not _valid(row.name):
            text = "Pick another name: use lowercase letters, digits and _, starting with a letter"
        elif not row.checked:
            text = "Not ticked, so it won't be saved"
        elif row.planned != row.name:
            text = f"Name taken -- will be saved as {row.planned}"
        else:
            text = "Ready"
        notes = len(draft.warnings)
        if notes and not row.saved_as:
            text = text.rstrip(".") + f" ({_count(notes, 'note')})"
        return text, "\n".join(self._notes_for(row))

    def _notes_for(self, row):
        draft = row.draft
        lines = [f"From {draft.source}."] if draft.source else []
        if draft.problem:
            lines.append(draft.problem)
        lines += draft.warnings
        return lines

    def _show_notes(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if len(rows) != 1 or rows[0] >= len(self._rows):
            self.notes.setText("")
            return
        row = self._rows[rows[0]]
        lines = self._notes_for(row)
        if not row.draft.warnings and not row.draft.problem:
            lines.append("Nothing to note: it was read in full.")
        self.notes.setText(f"{row.name}: " + "\n".join(lines))

    def _refresh(self):
        self._plan()
        self._filling = True
        try:
            self.table.setRowCount(len(self._rows))
            for i, row in enumerate(self._rows):
                self._fill_row(i, row)
        finally:
            self._filling = False
        self._show_notes()
        ready = sum(1 for row in self._rows if row.planned)
        self.save_button.setEnabled(ready > 0)
        self.save_button.setText(f"Save selected ({ready})" if ready else "Save selected")

    def _fill_row(self, i, row):
        draft = row.draft
        # a row that will never be saved reads grayed out, like a disabled control
        group = QPalette.ColorGroup.Normal if draft.can_save else QPalette.ColorGroup.Disabled
        brush = QBrush(self.palette().color(group, QPalette.ColorRole.Text))
        for column in (NAME, KIND, RANGE, STATUS):
            self._item(i, column, read_only=column != NAME).setForeground(brush)
        name_item = self._item(i, NAME)
        name_item.setText(row.name)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if draft.can_save and not row.saved_as:
            flags |= Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable
            name_item.setCheckState(Qt.CheckState.Checked if row.checked else Qt.CheckState.Unchecked)
        else:
            name_item.setData(Qt.ItemDataRole.CheckStateRole, None)
        name_item.setFlags(flags)
        name_item.setToolTip(draft.description or "")

        kind = colortable_import.KIND_LABELS[draft.kind] + (" (hidden)" if draft.hidden else "")
        self._item(i, KIND, read_only=True).setText(kind)
        range_item = self._item(i, RANGE, read_only=True)
        range_item.setText(draft.range_text() if draft.stops else "")

        colors = self._item(i, COLORS, read_only=True)
        colors.setData(Qt.ItemDataRole.DecorationRole, row.swatch)
        if draft.stops:
            high = "warm" if draft.units == "C" else "high"
            colors.setToolTip(f"{len(draft.stops)} stops, drawn with {draft.levels} colors. "
                              f"The {high} end is on the left.")
        text, tip = self._status(row)
        status = self._item(i, STATUS, read_only=True)
        status.setText(text)
        status.setToolTip(tip)

    def _item(self, i, column, read_only=False):
        item = self.table.item(i, column)
        if item is None:
            item = QTableWidgetItem()
            if read_only:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(i, column, item)
        return item

    def _on_item_changed(self, item):
        if self._filling or item.column() != NAME or item.row() >= len(self._rows):
            return
        row = self._rows[item.row()]
        if row.saved_as or not row.draft.can_save:
            return
        row.name = item.text().strip()
        row.checked = item.checkState() == Qt.CheckState.Checked
        row.error = None
        self._refresh()

    # --------------------------------------------------------------------- saving

    def save_selected(self):
        """Save every ticked row under its planned name; returns the names saved."""
        self._taken = None              # the store may have changed since the list was made
        self._plan()
        saved, failed = [], 0
        # each save rewrites the store safely (user_colortables._write): a few seconds for a
        # whole exported collection
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for row in self._rows:
                if not row.planned:
                    continue
                try:
                    entry = self._save_one(row)
                except Exception as e:      # one table that cannot be saved must not stop the rest
                    reason = user_colortables.plain_save_error(e) if isinstance(e, OSError) else str(e)
                    row.error = f"Couldn't save: {_sentence(reason)}"
                    failed += 1
                    continue
                row.saved_as, row.checked, row.error = entry["name"], False, None
                saved.append(entry["name"])
        finally:
            QApplication.restoreOverrideCursor()
        self.saved_names += saved
        self._taken = None
        self._refresh()
        parts = []
        if saved:
            parts.append(f"Saved {_count(len(saved), 'table')}: {', '.join(saved)}.")
        if failed:
            parts.append(f"{_count(failed, 'table')} could not be saved; the status says why.")
        if not parts:
            parts.append("Nothing is ticked to save.")
        self.message.setText(" ".join(parts))
        return saved

    def _save_one(self, row):
        try:
            return colortable_import.save(row.draft, row.planned)
        except (user_colortables.NameTaken, user_colortables.NameReserved):
            # taken since the list was planned (saved from another window): the next free one
            self._taken = None
            others = {r.planned for r in self._rows if r is not row and r.planned}
            row.planned = colortable_import.free_name(row.planned, self._taken_names() | others)
            return colortable_import.save(row.draft, row.planned)


def _valid(name):
    try:
        user_colortables.validate_name(name)
    except ValueError:
        return False
    return True


def _count(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _sentence(text):
    text = text.strip().rstrip(".")
    return text[:1].upper() + text[1:] + "." if text else ""


def _swatch(draft):
    """The table as a strip, its high (warm) end on the left like the Add dialog's preview;
    None for a draft that does not build."""
    if not draft.stops:
        return None
    try:
        cmap = draft.cmap()
    except Exception:
        return None
    xs = np.linspace(1.0, 0.0, _SWATCH_W)
    row = (cmap(xs)[:, :3] * 255).astype(np.uint8)
    pixels = np.ascontiguousarray(np.broadcast_to(row, (_SWATCH_H, _SWATCH_W, 3)))
    image = QImage(pixels.data, _SWATCH_W, _SWATCH_H, 3 * _SWATCH_W, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(image.copy())


def open_import_dialog(parent=None):
    """Show the Import window and wait for it to close; returns the names of the tables
    saved from it (already live in the pickers)."""
    dialog = ImportDialog(parent)
    dialog.exec()
    return list(dialog.saved_names)
