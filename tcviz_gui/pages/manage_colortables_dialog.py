"""The Manage colortables window: every table of yours in one list, and what can be done
with each -- make, edit, copy, rename, hide, delete (and bring back), and write out as
Python to share.

Everything here goes through tcviz.colortable_library, which keeps the tables file and
the live registry in step; after each change the rest of the app is told through
tcviz_gui.library_events, so the palette picker behind this window is already up to date
when it closes. New and Edit open the visual editor (colortable_editor_dialog.py), which
tells the app itself when it saves.

Every message is for someone who has never seen the code: what happened, and what to do
about it.

Colortable Editor (tcviz.edition) shows this window as its main window's content
(colortable_editor.main_window). There it lists its built-in tables too, under yours: they
can be copied, opened in the editor as a copy, and exported, but not changed. It has no
palette lists, so no Hide.
"""
import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from tcviz import colortable_export, colortable_library, edition, user_colortables
from tcviz_gui import library_events, swatches

_NAME, _KIND, _RANGE, _STATUS = range(4)
_BADGE_STYLES = {
    "hidden": "color: #dcdcdc; background-color: #555555;",
    "problem": "color: #ffffff; background-color: #8a3a3a;",
    "builtin": "color: #dcdcdc; background-color: #2f4f6f;",
}
_FILE_FILTER = "Text files (*.txt);;Python files (*.py);;All files (*)"


def _degrees(value):
    """-94.99999999999997 -> "-95", 56.7 -> "56.7": the range as a person would write it."""
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def range_text(entry):
    """A table's range as the list shows it, in its own units: "-100 to 50 °C", "0 to 100
    kt", "-10 to 70 dBZ" ("" for a table whose range cannot be read)."""
    entry = user_colortables.normalize(entry)
    unit = {"C": "°C", "kt": "kt", "dBZ": "dBZ"}.get(entry["units"], entry["units"])
    try:
        return f"{_degrees(float(entry['vmin']))} to {_degrees(float(entry['vmax']))} {unit}"
    except (KeyError, TypeError, ValueError):
        return ""


def _when_text(when):
    """"today 3:07 PM", "Oct 2, 3:07 PM". Built by hand: strftime's %-I and %-d (no leading
    zero) are glibc's own, and Windows refuses them."""
    if when is None:
        return ""
    clock = f"{when.hour % 12 or 12}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"
    if when.date() == datetime.date.today():
        return f"today {clock}"
    return f"{when.strftime('%b')} {when.day}, {clock}"


def _badge(text, style):
    """A small rounded label in a cell, kept to its own width."""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(4, 0, 4, 0)
    label = QLabel(text)
    label.setStyleSheet(f"QLabel {{ {_BADGE_STYLES[style]} border-radius: 7px; padding: 0px 8px; }}")
    row.addWidget(label)
    row.addStretch()
    return holder


class ManageColortablesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage colortables")
        self.resize(820, 480)
        self._tables = {}
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------ layout

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.heading = QLabel("")
        layout.addWidget(self.heading)

        body = QHBoxLayout()
        self.table_list = QTreeWidget()
        self.table_list.setColumnCount(4)
        self.table_list.setHeaderLabels(["Name", "Kind", "Range", ""])
        self.table_list.setRootIsDecorated(False)
        self.table_list.setUniformRowHeights(True)
        self.table_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_list.setIconSize(QSize(swatches.WIDTH, swatches.HEIGHT))
        # a little air around each cell: the columns are sized to their contents
        self.table_list.setStyleSheet("QTreeWidget::item { padding: 3px 8px 3px 2px; }")
        header = self.table_list.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(_NAME, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_KIND, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_RANGE, QHeaderView.ResizeMode.ResizeToContents)
        self.table_list.itemSelectionChanged.connect(self._update_buttons)
        self.table_list.itemDoubleClicked.connect(lambda *_: self._on_edit())
        body.addWidget(self.table_list, 1)

        buttons = QVBoxLayout()
        self.new_btn = self._button(buttons, "New…", self._on_new, "Make a new table of your own.")
        self.import_btn = self._button(buttons, "Import…", self._on_import,
                                       "Bring in tables from files (.pal, .tbl, .cpt, color lists, "
                                       "shared Python) or from text you paste.")
        self.edit_btn = self._button(buttons, "Edit…", self._on_edit,
                                     "Change the colors, range, name or notes of the selected table.")
        self.duplicate_btn = self._button(buttons, "Duplicate", self._on_duplicate,
                                          "Make a copy of the selected table under a new name.")
        self.rename_btn = self._button(buttons, "Rename…", self._on_rename,
                                       "Give the selected table a new name. Favorites follow it.")
        self.hide_btn = self._button(buttons, "Hide", self._on_hide,
                                     "Keep the selected table out of the palette lists without deleting it.")
        # Colortable Editor has no palette lists to hide a table from
        self.hide_btn.setVisible(edition.has_pickers())
        self.delete_btn = self._button(buttons, "Delete", self._on_delete,
                                       "Delete the selected table. You can bring it back from Recently "
                                       "deleted below.")
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        buttons.addWidget(line)
        self.export_btn = self._button(buttons, "Export…", self._on_export,
                                       "Save the selected table to a file, as the Python that defines it.")
        self.copy_btn = self._button(buttons, "Copy for sharing", self._on_copy,
                                     "Copy the selected table to the clipboard, as the Python that "
                                     "defines it -- ready to paste to a friend.")
        self.export_many_btn = self._button(buttons, "Export many…", self._on_export_many,
                                            "Save many tables -- yours, every built-in infrared table, "
                                            "every built-in water-vapor table -- into one file.")
        self.notes_check = QCheckBox("Include my notes")
        self.notes_check.setToolTip("Write each table's description (and its kind, for a water-vapor "
                                    "table) above its colors when exporting or copying.")
        buttons.addWidget(self.notes_check)
        buttons.addStretch()
        body.addLayout(buttons)
        layout.addLayout(body, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        deleted_row = QHBoxLayout()
        deleted_row.addWidget(QLabel("Recently deleted:"))
        self.deleted_combo = QComboBox()
        self.deleted_combo.setMinimumWidth(280)
        deleted_row.addWidget(self.deleted_combo)
        self.restore_btn = QPushButton("Restore")
        self.restore_btn.setToolTip("Bring the chosen table back, where it was in the list.")
        self.restore_btn.clicked.connect(self._on_restore)
        deleted_row.addWidget(self.restore_btn)
        deleted_row.addStretch()
        layout.addLayout(deleted_row)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.close_box = close          # a main window showing this one hides it

        QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.table_list, activated=self._on_delete)

    def _button(self, layout, text, slot, tip):
        button = QPushButton(text)
        button.setToolTip(tip)
        button.clicked.connect(slot)
        layout.addWidget(button)
        return button

    # ------------------------------------------------------------------ contents

    def refresh(self, select=None):
        """Fill the list from the store again, selecting `select` (else what was selected)."""
        select = select or self.selected_name()
        self.table_list.clear()
        self._tables = {}
        mine = colortable_library.tables()
        builtin = [t for t in colortable_library.builtin_tables() if t.name not in {m.name for m in mine}]
        for table in mine + builtin:
            self._tables[table.name] = table
            item = QTreeWidgetItem(["", "", "", ""])
            item.setText(_NAME, table.name)
            item.setData(_NAME, Qt.ItemDataRole.UserRole, table.name)
            pixmap = None if table.problem else swatches.palette_swatch(table.name)
            if pixmap is not None:
                icon = QIcon()
                # the same strip selected or not: the style would otherwise tint it
                icon.addPixmap(pixmap, QIcon.Mode.Normal)
                icon.addPixmap(pixmap, QIcon.Mode.Selected)
                item.setIcon(_NAME, icon)
            item.setText(_KIND, colortable_library.KIND_LABELS[table.kind])
            item.setText(_RANGE, range_text(table.entry))
            item.setToolTip(_NAME, self._tooltip(table))
            if table.kind == "wv":
                item.setToolTip(_RANGE, "Water-vapor bands are always drawn from 0 to -90 °C; "
                                        "this range is used on any other band.")
            elif table.kind == "wind":
                item.setToolTip(_RANGE, "Offered for wind speed pictures: the scatterometers, SAR winds, "
                                        "CYGNSS, SMAP and WindSat.")
            elif table.kind == "radar":
                item.setToolTip(_RANGE, "Offered for radar reflectivity pictures: GPM's radar and "
                                        "EarthCARE's cloud radar.")
            self.table_list.addTopLevelItem(item)
            if table.builtin:
                badge = _badge("Built in", "builtin")
                badge.setToolTip("Comes with the program. Duplicate it, or press Edit… to start a copy of it "
                                 "in the editor; it can't be changed itself.")
                self.table_list.setItemWidget(item, _STATUS, badge)
            elif table.problem:
                badge = _badge("Can't be drawn", "problem")
                badge.setToolTip(f"This table can't be drawn: {table.problem}. Edit it to fix it, or "
                                 "delete it.")
                self.table_list.setItemWidget(item, _STATUS, badge)
            elif table.hidden:
                badge = _badge("Hidden", "hidden")
                badge.setToolTip("Kept out of the palette lists. Select it and press Show to list it again.")
                self.table_list.setItemWidget(item, _STATUS, badge)
            if table.name == select:
                item.setSelected(True)
                self.table_list.setCurrentItem(item)
        if not self.table_list.selectedItems() and self.table_list.topLevelItemCount():
            # something is always selected, so the buttons beside the list are ready
            self.table_list.topLevelItem(0).setSelected(True)
            self.table_list.setCurrentItem(self.table_list.topLevelItem(0))
        count = len(mine)
        if builtin:
            self.heading.setText("You have no color tables of your own yet. Press New… to make one, or choose a "
                                 "built-in one and press Duplicate." if not count else
                                 f"Your color tables ({count}), then the built-in ones. Double-click one to edit it.")
        else:
            self.heading.setText("You have no color tables of your own yet. Press New… to make one."
                                 if not count else
                                 f"Your color tables ({count}). Double-click one to edit it.")
        self._fill_deleted()
        self._update_buttons()

    def _tooltip(self, table):
        if table.builtin:
            # Colortable Editor's built-ins carry no description
            return "Comes with the program. Duplicate it, or press Edit… to start a copy of it in the editor."
        lines = [table.entry.get("description") or "No description."]
        if table.shadows_builtin:
            lines.append(f"{edition.app_name()} also has a built-in table called '{table.name}'. Yours is used "
                         "in its place; if you rename or delete yours, the built-in one is used again.")
        if table.entry.get("levels", 256) != 256:
            lines.append(f"Drawn with {table.entry['levels']} colors.")
        return "\n\n".join(lines)

    def _fill_deleted(self):
        self.deleted_combo.clear()
        self._deleted = colortable_library.deleted_tables()
        for record in self._deleted:
            when = _when_text(record.when)
            self.deleted_combo.addItem(f"{record.name}  (deleted {when})" if when else record.name, record.id)
        if not self._deleted:
            self.deleted_combo.addItem("Nothing deleted lately")
        self.deleted_combo.setEnabled(bool(self._deleted))
        self.restore_btn.setEnabled(bool(self._deleted))

    def selected_name(self):
        items = self.table_list.selectedItems()
        return items[0].data(_NAME, Qt.ItemDataRole.UserRole) if items else None

    def select(self, name):
        for i in range(self.table_list.topLevelItemCount()):
            item = self.table_list.topLevelItem(i)
            if item.data(_NAME, Qt.ItemDataRole.UserRole) == name:
                self.table_list.setCurrentItem(item)
                item.setSelected(True)
                return

    def _selected_table(self):
        return self._tables.get(self.selected_name())

    def _update_buttons(self):
        table = self._selected_table()
        for button in (self.edit_btn, self.duplicate_btn, self.rename_btn, self.hide_btn, self.delete_btn):
            button.setEnabled(table is not None)
        builtin = table is not None and table.builtin
        for button in (self.rename_btn, self.hide_btn, self.delete_btn):
            button.setEnabled(table is not None and not builtin)
        self.edit_btn.setToolTip("Open a copy of the selected built-in table in the editor; save it under a name of "
                                 "your own." if builtin else
                                 "Change the colors, range, name or notes of the selected table.")
        drawable = table is not None and table.problem is None
        self.duplicate_btn.setEnabled(drawable)
        self.export_btn.setEnabled(drawable)
        self.copy_btn.setEnabled(drawable)
        hidden = table is not None and table.hidden
        self.hide_btn.setText("Show" if hidden else "Hide")
        self.hide_btn.setToolTip("List the selected table in the palette lists again." if hidden else
                                 "Keep the selected table out of the palette lists without deleting it.")

    def _say(self, text):
        self.status.setText(text)

    # ------------------------------------------------------------------ actions

    def _on_new(self):
        from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
        dialog = ColortableEditorDialog(self)
        if not (self._run(dialog) and dialog.entry):
            return
        # the editor has told the rest of the app already
        name = dialog.entry["name"]
        self.refresh(select=name)
        self._say(f"Saved '{name}'.")

    def _on_import(self):
        names = self._import()
        if not names:
            return
        self.refresh(select=names[-1])
        self._say(f"Imported {len(names)} table{'s' if len(names) != 1 else ''}: {', '.join(names)}.")
        for name in names:
            library_events.notify("saved", name)

    def _import(self):
        """The Import window, run to its end; the names it saved."""
        from tcviz_gui.pages.import_colortables_dialog import open_import_dialog
        return open_import_dialog(self)

    def _on_edit(self):
        table = self._selected_table()
        if table is None:
            return
        from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
        if table.builtin:
            self._edit_copy(table)
            return
        dialog = ColortableEditorDialog(self, entry=table.entry, mode="edit")
        if not (self._run(dialog) and dialog.entry):
            return
        # the editor has told the rest of the app already, a rename as a rename
        name = dialog.entry["name"]
        self.refresh(select=name)
        if name != table.name:
            self._say(f"Saved your changes and renamed '{table.name}' to '{name}'."
                      + self._builtin_back(table))
        else:
            self._say(f"Saved your changes to '{name}'.")

    def _edit_copy(self, table):
        """Edit… on a built-in table: the editor on a copy of it, saved as a new table."""
        from tcviz_gui.pages.colortable_editor_dialog import ColortableEditorDialog
        try:
            draft = colortable_library.duplicate_draft(table.name)
        except (ValueError, LookupError, OSError) as e:
            self._warn("Couldn't make a copy", self._plain(e))
            return
        dialog = ColortableEditorDialog(self, entry=draft, mode="copy", source=table.name)
        if not (self._run(dialog) and dialog.entry):
            return
        name = dialog.entry["name"]
        self.refresh(select=name)
        self._say(f"Saved '{name}', your copy of '{table.name}'.")

    def _on_duplicate(self):
        table = self._selected_table()
        if table is None:
            return
        try:
            copy = colortable_library.duplicate(table.name)
        except (ValueError, LookupError, OSError) as e:
            self._warn("Couldn't make a copy", self._plain(e))
            return
        self.refresh(select=copy["name"])
        self._say(f"Made a copy of '{table.name}' called '{copy['name']}'. Rename or edit it as you like.")
        library_events.notify("duplicated", table.name, copy["name"])

    def _on_rename(self):
        table = self._selected_table()
        if table is None:
            return
        new = self._ask_new_name(table.name)
        if new is None:
            return
        new = new.strip()
        if not new or new == table.name:
            return
        try:
            colortable_library.rename(table.name, new)
        except (ValueError, LookupError, OSError) as e:
            self._warn("Couldn't rename", self._plain(e))
            return
        self.refresh(select=new)
        self._say(f"Renamed '{table.name}' to '{new}'." + self._builtin_back(table))
        library_events.notify("renamed", table.name, new)

    def _on_hide(self):
        table = self._selected_table()
        if table is None:
            return
        hide = not table.hidden
        try:
            colortable_library.set_hidden(table.name, hide)
        except (ValueError, LookupError, OSError) as e:
            self._warn("Couldn't change it", self._plain(e))
            return
        self.refresh(select=table.name)
        if hide:
            self._say(f"'{table.name}' is hidden from the palette lists. It is still here, and Show lists "
                      "it again.")
        else:
            self._say(f"'{table.name}' is listed in the palettes again.")
        library_events.notify("hidden" if hide else "shown", table.name)

    def _on_delete(self):
        table = self._selected_table()
        if table is None:
            return
        row = self.table_list.indexOfTopLevelItem(self.table_list.selectedItems()[0])
        try:
            colortable_library.delete(table.name)
        except (ValueError, LookupError, OSError) as e:
            self._warn("Couldn't delete", self._plain(e))
            return
        self.refresh()
        # the next table down takes the selection, so Delete can be pressed again
        count = self.table_list.topLevelItemCount()
        if count:
            self.select(self.table_list.topLevelItem(min(row, count - 1)).data(_NAME, Qt.ItemDataRole.UserRole))
        self._say(f"Deleted '{table.name}'. To undo, choose it under Recently deleted and press Restore."
                  + self._builtin_back(table))
        library_events.notify("deleted", table.name)

    def _on_restore(self):
        index = self.deleted_combo.currentIndex()
        if not self._deleted or index < 0:
            return
        record = self._deleted[index]
        try:
            entry = colortable_library.restore(record.id)
        except (ValueError, LookupError, OSError) as e:
            self._warn("Couldn't bring it back", self._plain(e))
            return
        self.refresh(select=entry["name"])
        if entry["name"] == record.name:
            self._say(f"Brought back '{record.name}'.")
        else:
            self._say(f"Brought back '{record.name}' as '{entry['name']}', because you have made another "
                      f"table called '{record.name}' since.")
        library_events.notify("restored", record.name, entry["name"])

    def _on_export(self):
        table = self._selected_table()
        if table is None:
            return
        try:
            text = colortable_export.export_block(table.name, include_notes=self.notes_check.isChecked())
        except Exception:
            self._warn("Couldn't export", f"{edition.app_name()} could not write '{table.name}' out as Python "
                                          "that draws exactly the same colors, so nothing was saved.")
            return
        path = self._ask_save_path("Export a color table", f"{table.name}.txt")
        if not path:
            return
        if self._write(path, text):
            self._say(f"Saved '{table.name}' to {path}.")

    def _on_copy(self):
        table = self._selected_table()
        if table is None:
            return
        try:
            text = colortable_export.export_block(table.name, include_notes=self.notes_check.isChecked())
        except Exception:
            self._warn("Couldn't copy", f"{edition.app_name()} could not write '{table.name}' out as Python "
                                        "that draws exactly the same colors, so nothing was copied.")
            return
        QApplication.clipboard().setText(text)
        self._say(f"Copied '{table.name}' to the clipboard ({len(text.splitlines())} lines) -- paste it to "
                  "a friend, or into Add colortable.")

    def _on_export_many(self):
        groups = colortable_library.export_groups()
        chooser = ExportManyDialog(groups, self.notes_check.isChecked(), self)
        if not self._run(chooser):
            return
        names = chooser.names()
        path = self._ask_save_path("Export many color tables",
                                   "color-tables.txt" if edition.is_editor() else "tcviz-color-tables.txt")
        if not path:
            return
        failures = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            text = colortable_export.export_all(include_notes=chooser.notes_check.isChecked(),
                                                failures=failures, only=names)
        finally:
            QApplication.restoreOverrideCursor()
        if not self._write(path, text):
            return
        written = len(names) - len(failures)
        message = f"Saved {written} table{'s' if written != 1 else ''} to {path}."
        if failures:
            left = ", ".join(sorted(name for name, _why in failures))
            message += (f" {len(failures)} could not be written out exactly and were left out: {left}.")
        self._say(message)

    # ------------------------------------------------------------------ helpers

    def _builtin_back(self, table):
        if not table.shadows_builtin:
            return ""
        return f" {edition.app_name()}'s own built-in table called '{table.name}' is used under that name again."

    @staticmethod
    def _plain(error):
        if isinstance(error, OSError):
            return user_colortables.plain_save_error(error)
        return str(error)

    def _write(self, path, text):
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as e:
            self._warn("Couldn't save the file", user_colortables.plain_save_error(e, what=str(path)))
            return False
        return True

    # The pop-ups, each in one place so a test can answer it.

    def _run(self, dialog):
        return dialog.exec()

    def _warn(self, title, text):
        QMessageBox.warning(self, title, text)

    def _ask_new_name(self, current):
        text, ok = QInputDialog.getText(self, "Rename table", f"New name for '{current}':",
                                        QLineEdit.EchoMode.Normal, current)
        return text if ok else None

    def _ask_save_path(self, title, suggested):
        path, _filter = QFileDialog.getSaveFileName(self, title, str(Path.home() / suggested), _FILE_FILTER)
        return path or None


class ExportManyDialog(QDialog):
    """The "Export many…" checklist: which groups of tables go into the one file."""

    def __init__(self, groups, include_notes=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export many color tables")
        self._groups = groups
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Write these tables into one text file, as Python:"))
        self.checks = {}
        labels = {"mine": "My tables", "ir": "All built-in infrared tables",
                  "wv": "All built-in water-vapor tables"}
        for key in ("mine", "ir", "wv"):
            box = QCheckBox(f"{labels[key]} ({len(groups[key])})")
            box.setChecked(key == "mine" and bool(groups[key]))
            box.setEnabled(bool(groups[key]))
            box.toggled.connect(self._update)
            self.checks[key] = box
            layout.addWidget(box)
        self.notes_check = QCheckBox("Include my notes (each table's description)")
        self.notes_check.setChecked(include_notes)
        layout.addWidget(self.notes_check)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_btn.setText("Save…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update()

    def names(self):
        return [name for key in ("mine", "ir", "wv") if self.checks[key].isChecked()
                for name in self._groups[key]]

    def _update(self):
        self.save_btn.setEnabled(any(box.isChecked() for box in self.checks.values()))

