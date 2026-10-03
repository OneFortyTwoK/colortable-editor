"""Dialog for creating a new colortable from the GUI, no code required.

The user pastes a color-stop list (the same ``(pos, "#hex")`` style used to author the
built-in curves -- trailing ``#comment`` labels are fine), names it, sets the warm/cold
bounds, and sees a live gradient preview. On Save it's persisted and registered live
(tcviz.colortable_library.save), so it appears in the palette picker immediately and on
every future launch. See OptionsPage for the wiring.

The same dialog edits one of your tables (mode "edit": pre-filled, and saving over its
own name replaces it without asking, while a new name renames it) and makes a copy of
any table (mode "copy": pre-filled under a free name, saved as a new table). Those now
open the visual editor (colortable_editor_dialog.py); this dialog is its "Type or paste
stops..." (mode "text"), where nothing is saved: the fields go back to the editor.

A winds table's range is in knots and a radar table's in dBZ (the Category box switches
the range boxes), and a pasted block that says `# units: kt` or `# units: dBZ` fills in as
one. Colortable Editor (tcviz.edition) offers infrared and water-vapor tables only: its
Category box has those two, and a winds or radar block is not taken in.
"""
import numpy as np
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QComboBox,
    QDoubleSpinBox, QCheckBox, QPlainTextEdit, QDialogButtonBox, QMessageBox,
    QPushButton, QApplication,
)

from tcviz import colortable_library, edition, user_colortables

_PREVIEW_W, _PREVIEW_H = 320, 26

_PLACEHOLDER = (
    "One stop per line: a position and a #color. Position can be a fraction\n"
    "like 60/130 or a 0-1 number. Trailing #labels are ignored. Example:\n\n"
    '(0/130, "#000000"),   # coldest end\n'
    '(60/130, "#0c4c8c"),\n'
    '(93/130, "#fcff51"),\n'
    '(130/130, "#ffc2ff")  # warmest end'
)


def _position_text(position, span):
    """A stop's position as the box shows it: a/span in whole degrees where that is the
    exact position (user_colortables._fraction), else in tenths to thousandths of a degree
    (10.15/135, as the exporter writes a built-in's stops), else its exact decimal. Always
    the exact number: it is read back when the table is saved."""
    text = user_colortables._fraction(position, span)
    if "/" in text or not span:
        return text
    whole = round(span)
    if abs(span - whole) < 1e-9 and whole > 0:
        for digits in (1, 2, 3):
            number = f"{position * whole:.{digits}f}"
            if float(number) / whole == position:
                return f"{number.rstrip('0').rstrip('.')}/{whole}"
    return text


_TITLES = {"add": "Add colortable", "edit": "Edit colortable", "copy": "Duplicate colortable",
           "text": "Type or paste stops"}
# What each category's range is in, and how the range boxes read for it.
_CATEGORY_UNITS = {"Temperature (IR)": "C", "Water Vapor": "C",
                   user_colortables.UNIT_CATEGORIES["kt"]: "kt", user_colortables.UNIT_CATEGORIES["dBZ"]: "dBZ"}
_UNIT_SUFFIX = {"C": " C", "kt": " kt", "dBZ": " dBZ"}
_UNIT_LIMITS = {"C": (-150.0, 100.0), "kt": (0.0, 300.0), "dBZ": (-70.0, 100.0)}
_UNIT_LABELS = {"C": ("Warm (top):", "Cold (bottom):"), "kt": ("Top:", "Bottom:"), "dBZ": ("Top:", "Bottom:")}


class AddColortableDialog(QDialog):
    def __init__(self, parent=None, entry=None, mode="add"):
        """`entry` pre-fills the fields (a stored table, or a duplicate_draft). `mode`:
        "add" (a new table), "edit" (the table `entry` names: saving over that name
        replaces it without asking, a new name renames it), "copy" (a new table from
        `entry`), "text" (nothing is saved: "Use these stops" leaves the fields in
        self.fields for the visual editor)."""
        super().__init__(parent)
        if mode not in _TITLES:
            raise ValueError(f"mode must be one of {sorted(_TITLES)}")
        self.setWindowTitle(_TITLES[mode])
        self.entry = None  # set to the stored entry dict on a successful save
        self.fields = None  # mode "text": the entry fields handed back, nothing saved
        self._mode = mode
        self._original = entry["name"] if mode == "edit" and entry else None
        # How many colors the table is drawn with. Not a field of its own: it comes with
        # the table being edited or copied, or a pasted block (`], N=57)`), so a 57-color
        # MetPy copy is saved with its 57 rather than stretched to 256.
        self._levels = user_colortables.DEFAULT_LEVELS
        # The exact bound each range box was filled with. The boxes show two decimals, and
        # a few built-ins end a hair off a whole degree (bd's cold end is
        # -94.99999999999997 C, 178.15 K exactly): a copy keeps the exact number as long as
        # the box still shows it, so it draws exactly what the built-in draws.
        self._exact_bounds = {}
        self._units = "C"           # what the range boxes are in (the category's units)
        self._build_ui()
        if entry:
            self._fill_from(user_colortables.normalize(entry))

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Sharing lives at the top because both directions start here: pick one of yours
        # to copy out, or paste someone else's block into the stops box below and every
        # field fills itself in.
        if self._original is not None:
            note = QLabel(f"Editing your table '{self._original}'. Save replaces it; "
                          "a new name renames it.")
            note.setWordWrap(True)
            layout.addWidget(note)
        elif self._mode == "text":
            note = QLabel("Type or paste the color stops below. \"Use these stops\" puts them in the "
                          "editor; nothing is saved until you save the table there.")
            note.setWordWrap(True)
            layout.addWidget(note)
        share_row = QHBoxLayout()
        self.load_combo = QComboBox()
        self.load_combo.addItem("Start from scratch")
        # only real tables: one the app could not load is named in the startup message
        self._saved = [e for e in user_colortables.load()
                       if isinstance(e, dict) and isinstance(e.get("name"), str)]
        for saved in self._saved:
            self.load_combo.addItem(saved["name"])
        self.load_combo.currentIndexChanged.connect(self._on_load_saved)
        load_label = QLabel("Load one of yours:")
        share_row.addWidget(load_label)
        share_row.addWidget(self.load_combo)
        # Editing or copying one table, loading another over it would quietly turn the
        # edit into a different table under a different name.
        load_label.setVisible(self._mode in ("add", "text"))
        self.load_combo.setVisible(self._mode in ("add", "text"))
        self.copy_btn = QPushButton("Copy for sharing")
        self.copy_btn.setToolTip(
            ("Copy this colortable as the Python that defines it, ready to send to a friend. "
             if edition.is_editor() else
             "Copy this colortable as the Python that would define it in colormaps.py. ")
            + "Paste one of those into the stops box and everything -- name, range, "
            "category, reverse -- fills itself in.")
        self.copy_btn.clicked.connect(self._on_copy)
        share_row.addWidget(self.copy_btn)
        share_row.addStretch()
        layout.addLayout(share_row)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("lowercase, e.g. mycurve")
        form.addRow("Name:", self.name_edit)

        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("short description (shown in tooltips/listings)")
        form.addRow("Description:", self.desc_edit)

        self.category_combo = QComboBox()
        # label -> stored category value. Hiding used to be a third category here
        # ("Hidden (CLI only)", stored as None); it is its own checkbox now, so a hidden
        # table keeps its kind. A stored None still loads, as a hidden IR table.
        categories = [("ir", "Temperature (IR)", "Temperature (IR)"),
                      ("wv", "Water Vapor", "Water Vapor"),
                      ("wind", "Winds (knots)", user_colortables.UNIT_CATEGORIES["kt"]),
                      ("radar", "Radar (dBZ)", user_colortables.UNIT_CATEGORIES["dBZ"])]
        # the kinds this edition offers
        self._categories = [(label, value) for kind, label, value in categories if kind in edition.kinds()]
        for label, _ in self._categories:
            self.category_combo.addItem(label)
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        form.addRow("Category:", self.category_combo)

        self.hidden_check = QCheckBox("Hide from the pickers")
        self.hidden_check.setToolTip(
            "Keep this table out of the palette lists without deleting it. Load it here "
            "and untick this to show it again.")
        form.addRow("", self.hidden_check)
        # Colortable Editor has no palette lists to hide a table from
        self.hidden_check.setVisible(edition.has_pickers())

        bounds = QHBoxLayout()
        self.vmax_spin = QDoubleSpinBox()
        self.vmax_spin.setRange(-150, 100)
        self.vmax_spin.setValue(50.0)
        self.vmax_spin.setSuffix(" C")
        self.vmin_spin = QDoubleSpinBox()
        self.vmin_spin.setRange(-150, 100)
        self.vmin_spin.setValue(-100.0)
        self.vmin_spin.setSuffix(" C")
        self.vmax_label = QLabel(_UNIT_LABELS["C"][0])
        self.vmin_label = QLabel(_UNIT_LABELS["C"][1])
        bounds.addWidget(self.vmax_label)
        bounds.addWidget(self.vmax_spin)
        bounds.addSpacing(12)
        bounds.addWidget(self.vmin_label)
        bounds.addWidget(self.vmin_spin)
        bounds.addStretch()
        form.addRow("Range:", self._wrap(bounds))

        self.reversed_check = QCheckBox("Reverse (cold cloud tops map to the last stop -- the usual convention)")
        self.reversed_check.setChecked(True)
        form.addRow("", self.reversed_check)
        layout.addLayout(form)

        layout.addWidget(QLabel("Color stops:"))
        self.stops_edit = QPlainTextEdit()
        self.stops_edit.setPlaceholderText(_PLACEHOLDER)
        self.stops_edit.setMinimumHeight(180)
        layout.addWidget(self.stops_edit)

        layout.addWidget(QLabel("Preview (warm left -> cold right):"))
        self.preview = QLabel()
        self.preview.setFixedSize(_PREVIEW_W, _PREVIEW_H)
        self.preview.setFrameShape(QLabel.Shape.Box)
        layout.addWidget(self.preview)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self._save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if self._mode == "text":
            self._save_btn.setText("Use these stops")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # live preview + save-enable as the stops / reversed change
        self.stops_edit.textChanged.connect(self._absorb_shared_block)
        self.stops_edit.textChanged.connect(self._update_preview)
        self.reversed_check.toggled.connect(self._update_preview)
        self._update_preview()

    def _wrap(self, inner_layout):
        from PySide6.QtWidgets import QWidget
        w = QWidget()
        w.setLayout(inner_layout)
        return w

    def _current_stops(self):
        return user_colortables.parse_stops(self.stops_edit.toPlainText())

    def _range_fields(self):
        """The range boxes as entry fields: vmin_c/vmax_c in degrees C, or for a winds or
        radar table its units with vmin/vmax in them."""
        lo, hi = self._bound(self.vmin_spin), self._bound(self.vmax_spin)
        if self._units == "C":
            return {"vmin_c": lo, "vmax_c": hi}
        return {"units": self._units, "vmin": lo, "vmax": hi}

    def _on_category_changed(self, _index):
        """A category in other units (degrees C, knots, dBZ): the range boxes switch to them,
        at that kind's usual range."""
        units = _CATEGORY_UNITS[self._current_category() or "Temperature (IR)"]
        if units == self._units:
            return
        self._units = units
        for spin in (self.vmax_spin, self.vmin_spin):
            spin.setRange(*_UNIT_LIMITS[units])
            spin.setSuffix(_UNIT_SUFFIX[units])
        lo, hi = user_colortables.DEFAULT_RANGES[units]
        self._set_bound(self.vmin_spin, lo)
        self._set_bound(self.vmax_spin, hi)
        top, bottom = _UNIT_LABELS[units]
        self.vmax_label.setText(top)
        self.vmin_label.setText(bottom)
        self._update_preview()

    def _update_preview(self):
        if self._foreign_units(self.stops_edit.toPlainText()):
            self.preview.clear()
            self.status.setText(f"That is not an infrared or water-vapor table, so {edition.app_name()} can't "
                                "use it.")
            self._save_btn.setEnabled(False)
            return
        try:
            stops = self._current_stops()
            entry = {"stops": stops, **self._range_fields(),
                     "reversed": self.reversed_check.isChecked(), "levels": self._levels}
            cmap = user_colortables.build_cmap(entry)[0]
        except Exception as e:
            self.preview.clear()
            self.status.setText(f"Stops: {e}")
            self._save_btn.setEnabled(False)
            return
        xs = np.linspace(1.0, 0.0, _PREVIEW_W)  # warm (vmax) on the left, matching the render colorbar
        row = (cmap(xs)[:, :3] * 255).astype(np.uint8)
        arr = np.ascontiguousarray(np.broadcast_to(row, (_PREVIEW_H, _PREVIEW_W, 3)))
        qimg = QImage(arr.data, _PREVIEW_W, _PREVIEW_H, 3 * _PREVIEW_W, QImage.Format.Format_RGB888)
        self.preview.setPixmap(QPixmap.fromImage(qimg.copy()))
        if self._levels == user_colortables.DEFAULT_LEVELS:
            self.status.setText("Looks good.")
        else:
            self.status.setText(f"Looks good. Drawn with {self._levels} colors, like the table it came from.")
        self._save_btn.setEnabled(True)

    def _set_bound(self, spin, value):
        spin.setValue(value)
        self._exact_bounds[spin] = value

    def _bound(self, spin):
        exact = self._exact_bounds.get(spin)
        if exact is not None and spin.value() == round(exact, spin.decimals()):
            return exact
        return spin.value()

    def _current_category(self):
        return self._categories[self.category_combo.currentIndex()][1]

    def _current_entry(self):
        """What the fields currently describe, without saving it.

        A hidden IR table is given as category None, the spelling the shared block has
        always carried ("# category: none") and reads back as hidden -- so Copy keeps it
        hidden. A hidden water-vapor table shares as a plain water-vapor one."""
        category = self._current_category()
        if self.hidden_check.isChecked() and category == "Temperature (IR)":
            category = None
        entry = {"name": self.name_edit.text().strip() or "untitled",
                 "stops": user_colortables.parse_stops(self.stops_edit.toPlainText()),
                 **self._range_fields(),
                 "description": self.desc_edit.text().strip(),
                 "category": category,
                 "reversed": self.reversed_check.isChecked(),
                 "levels": self._levels}
        if self._units != "C":
            entry["hidden"] = self.hidden_check.isChecked()
        return entry

    def _on_copy(self):
        try:
            text = user_colortables.to_shareable(self._current_entry())
        except Exception as e:
            self.status.setText(f"Nothing to copy yet: {e}")
            return
        QApplication.clipboard().setText(text)
        self.status.setText(f"Copied {len(text.splitlines())} lines to the clipboard -- "
                            "paste that to a friend.")

    def _on_load_saved(self, index):
        if index <= 0:
            return
        # normalized, so an old entry's category None shows as IR with Hide ticked
        self._fill_from(user_colortables.normalize(self._saved[index - 1]))

    def _absorb_shared_block(self):
        """Fill the rest of the dialog in when a shared block is pasted into the stops box.

        Only fires on text that carries the header, so hand-typed stops are never
        second-guessed, and the stops box is rewritten to just the stops so the block's
        comment lines do not sit there looking like part of the curve.
        """
        text = self.stops_edit.toPlainText()
        if not user_colortables.looks_shareable(text):
            return
        try:
            fields = user_colortables.parse_shareable(text)
        except Exception:
            return  # a half-pasted block: leave it alone, the preview will say why
        if self._foreign_units(text):
            return  # a kind this edition does not offer: the preview says so
        # a whole block says how many colors it has: `], N=57)`, or nothing for 256 -- so a
        # block pasted over a 57-color copy does not keep the 57
        fields.setdefault("levels", user_colortables.DEFAULT_LEVELS)
        self._fill_from(fields)

    def _foreign_units(self, text):
        """Whether `text` says (`# units: kt`) it is a table in units the Category box does not
        offer -- a winds or radar table in Colortable Editor."""
        units = user_colortables.units_of(text)
        if units is None or units == "C":
            return False
        return not any(_CATEGORY_UNITS.get(value) == units for _label, value in self._categories)

    def _fill_from(self, fields):
        blocked = self.stops_edit.blockSignals(True)
        try:
            if fields.get("name"):
                self.name_edit.setText(fields["name"])
            if "levels" in fields:
                self._levels = int(fields["levels"])
            if fields.get("description"):
                self.desc_edit.setText(fields["description"])
                self.desc_edit.setCursorPosition(0)     # a long one shows its start
            # The category first: it sets what the range boxes are in. A winds or radar
            # table's units say its category; a range given in degrees C (vmin_c/vmax_c)
            # with nothing else said is an infrared table's.
            units = fields.get("units", "C")
            category = None
            if units in user_colortables.UNIT_CATEGORIES:
                category = user_colortables.UNIT_CATEGORIES[units]
            elif "category" in fields:
                # None is the legacy "hidden" category: an IR table with Hide ticked
                category = fields["category"] or "Temperature (IR)"
            elif ("vmin_c" in fields or "vmax_c" in fields) and self._units != "C":
                category = "Temperature (IR)"
            if category is not None:
                for i, (_label, value) in enumerate(self._categories):
                    if value == category:
                        self.category_combo.setCurrentIndex(i)
                        break
            if "category" in fields and units == "C":
                self.hidden_check.setChecked(bool(fields.get("hidden", fields["category"] is None)))
            elif "hidden" in fields:
                self.hidden_check.setChecked(bool(fields["hidden"]))
            low, high = ("vmin_c", "vmax_c") if self._units == "C" else ("vmin", "vmax")
            if low in fields:
                self._set_bound(self.vmin_spin, float(fields[low]))
            if high in fields:
                self._set_bound(self.vmax_spin, float(fields[high]))
            reverse = fields.get("reversed_", fields.get("reversed"))
            if reverse is not None:
                self.reversed_check.setChecked(bool(reverse))
            if fields.get("stops"):
                # Written back in the same (a/b, "#hex") style the placeholder shows and
                # a shared definition uses, so what is in the box is what you would paste
                # somewhere else.
                span = self._bound(self.vmax_spin) - self._bound(self.vmin_spin)
                rows = [f'({_position_text(p, span)}, "{c}")' for p, c in fields["stops"]]
                self.stops_edit.setPlainText(
                    "\n".join(r + "," for r in rows[:-1]) + ("\n" if len(rows) > 1 else "")
                    + rows[-1])
        finally:
            self.stops_edit.blockSignals(blocked)
        self._update_preview()

    def _confirm_replace(self, name):
        """Ask before a save replaces a table of the same name (it used to, silently)."""
        answer = QMessageBox.question(
            self, "Replace table?", f"Replace the existing table '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    @property
    def original_name(self):
        """The name the edited table had before this save (None unless editing)."""
        return self._original

    def _on_save(self):
        if self._mode == "text":
            self._use_fields()
            return
        name = self.name_edit.text().strip()

        def save(replace):
            # stored and made live in one step, no restart needed; editing, the table's
            # own name is not "taken"
            return colortable_library.save(
                name, self.stops_edit.toPlainText(), **self._range_fields(),
                description=self.desc_edit.text().strip(), category=self._current_category(),
                reversed_=self.reversed_check.isChecked(), levels=self._levels,
                hidden=self.hidden_check.isChecked(), original=self._original, replace=replace)
        try:
            try:
                entry = save(replace=False)
            except user_colortables.NameTaken:
                if not self._confirm_replace(name):
                    return          # back to the dialog, everything as it was typed
                entry = save(replace=True)
        except user_colortables.NameReserved as e:
            QMessageBox.warning(self, "Pick another name", str(e))
            return
        except OSError as e:
            QMessageBox.warning(self, "Couldn't save colortable", user_colortables.plain_save_error(e))
            return
        except Exception as e:
            QMessageBox.warning(self, "Couldn't save colortable", str(e))
            return
        self.entry = entry
        self.accept()

    def _use_fields(self):
        """Mode "text": hand what the fields describe back, checked to draw, unsaved."""
        try:
            fields = self._current_entry()
            fields["name"] = self.name_edit.text().strip()
            if fields["category"] is None:
                fields["category"], fields["hidden"] = "Temperature (IR)", True
            else:
                fields["hidden"] = self.hidden_check.isChecked()
            user_colortables.build_cmap(fields)
        except Exception as e:
            self.status.setText(f"Stops: {e}")
            return
        self.fields = fields
        self.accept()
