"""The visual color table editor: the table as a bar of stops you drag, a list of the same
stops as numbers, the picture's own color scale, the chosen stop's color, and a real picture
redrawn through the table as you edit -- beside any other table, if you like, for
comparison.

It opens for a new table (mode "add"), one of yours (mode "edit": saving over its own
name replaces it without asking, and a new name renames it, favorites and all) and a copy
of any infrared or water-vapor table (mode "copy", from colortable_library.duplicate_draft,
which draws exactly what the original draws). A table is infrared or water vapor (in
degrees C), winds (in knots) or radar (in dBZ): the Kind box switches it, and the range,
the stops, the color scale and the sample picture follow in that kind's units. The Manage window's New and Edit, the
picker's "+ Add colortable..." and its right-click Edit and "Duplicate and edit..." all
open it. The older text-box dialog is still here as "Type or paste stops..." for pasting a
block someone shared, and "History..." shows the earlier versions of a table of yours
(colortable_history_dialog), one of which loads back in as one undo step, unsaved.

Every change to the table -- a stop added, moved, recolored, split into a hard step or
deleted, the range, the kind -- is one step of a QUndoStack, a whole drag being one step.
The table itself is a tcviz.colortable_model.ColortableModel; the widgets
(tcviz_gui.colortable_widgets) ask this dialog for each change and are told when to show
it. Save goes through colortable_library.save, then tells the rest of the app
(library_events), so the picker is up to date when the window closes.

Colors are picked in the color panel beside the picture (colortable_widgets.ColorPanel), not
in a window of their own: the picture follows the color while it is picked, and one drag
in the color square, one held Lighter or Hue button, or one visit to the hex box is one
undo step. The picture answers the mouse too: the value under the pointer shows in the
status line, a click chooses the stop that colors that spot, and Shift-click adds a stop
at its value.

The picture zooms -- the mouse wheel about the pointer, or the Zoom in, Zoom out and Fit
buttons -- and a drag moves a zoomed picture around (a click that does not move still
chooses a stop). Zoomed in on a big picture, which is shown thinned, the part in view is
drawn from every one of its pixels. The picture compared with zooms and pans with it.
Copy picture and Save picture hand over the whole saved picture in the edited table, every
pixel, colored as tcviz colors it, with the color scale in it if the box says so.

Swipe puts the two tables on one picture instead of two: this table left of a line, the
one compared with right of it (PicturePane.set_swipe), each side exactly its table's
colors. The line is dragged across the picture, or moved with the arrow keys once pressed;
the picture still zooms and pans under it, the pointer reads out either side, and a click
chooses a stop only on this table's side. Copy picture and Save picture then hand over the
swiped picture, split where the line is (colortable_preview.swiped_picture), unless "Both,
side by side" is ticked.

Share card… makes one picture to post of what is shown -- the title, each table's color
scale and the storm drawn with it, the storm's title and data line under it
(tcviz.share_card) -- in a window of its own (share_card_dialog).

One storm and Several storms, above the picture, switch what the picture's place shows
(set_view): the one picture, which zooms and compares, or the storm grid
(tcviz_gui.storm_grid), the table on several bundled samples at once -- those ticked under
its Storms… -- with its own Copy grid, Save grid… and Share card… where the picture's are.
The grid follows every edit: each flush tells it (`drawn`), and it draws its storms again a
moment later. A click brings a storm to the front, big, the others small beside it; the one
in front answers the mouse as the one picture does, with the same words (spot_for,
show_spot, pick_spot), and a double-click shows it as the one picture (open_storm). Compare with and Swipe wait, greyed, while the grid is shown. The view chosen
is remembered with the window's layout. The switch's row costs the window no height: the
pictures give it some of their least height (_give_panes_room), and the column of buttons
beside one picture lost the grid's own button.

The picture is redrawn through tcviz.colortable_preview: the table row of every pixel is
worked out once per picture and range, so an edit only looks the colors up again.

How to use the window waits behind the "?" at the top right (_help, in a pop-up that stays
until clicked away), so the picture has the room the help took. The color panel sits beside
the picture in a splitter (colortable_widgets.PanelSplitter): dragging its handle left folds
the panel away and the picture takes its width; the panel itself keeps its width. The
window opens as it was last left -- its size, maximized or not, the panel folded or not --
from layout.json beside the tables (tcviz.window_layout; the one setting kept, for the
editor only, by the author's choice), fitted to the screen; Reset layout in the "?" forgets
it.

While the table differs from what was saved, the editor keeps it in a draft file a moment
after each change (tcviz.colortable_drafts), removed again on Save or a confirmed Cancel: a
table left unsaved when the program closes unexpectedly is offered back at the next start
(tcviz_gui.pages.draft_recovery_dialog), and opens here marked changed (`draft`).

The Picture box lists the bundled samples of vendor/editor_samples first, in the
manifest's order, and the editor opens on the first one of the table's kind; then tcviz's
own samples (Genevieve, and the wind and radar ones), your recent pictures and "Choose a
saved picture…". Colortable Editor (tcviz.edition) lists only the bundled samples, offers
infrared and water-vapor tables only, and has no pickers to hide a table from.
"""
import contextlib
import functools
import itertools
import re
from collections import namedtuple
from pathlib import Path

from matplotlib.colors import Normalize
from PySide6.QtCore import QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage, QKeySequence, QPalette, QShortcut, QUndoCommand, QUndoStack
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSizePolicy, QSpacerItem, QToolButton,
    QVBoxLayout, QWidget,
)

from tcviz import (
    colortable_drafts, colortable_export, colortable_history, colortable_library, colortable_preview,
    colortable_registry, edition, user_colortables, window_layout,
)
from tcviz.colortable_model import ColortableModel, EditRefused, kind_of_entry
from tcviz_gui import colortable_widgets, library_events, modal
from tcviz_gui.colortable_widgets import (
    ZOOM_STEP, ColorPanel, HelpPopup, PanelSplitter, PicturePane, PictureView, ScalePanel, StopBar, StopListPanel,
    degrees_text, zoom_words,
)
from tcviz_gui.storm_grid import StormGrid

_TITLES = {"add": "Add colortable", "edit": "Edit colortable", "copy": "Duplicate colortable"}
_KINDS = (("ir", "Infrared"), ("wv", "Water vapor"), ("wind", "Winds (knots)"), ("radar", "Radar (dBZ)"))
_KIND_TIPS = {
    "tcviz": ("Infrared tables are offered for infrared bands, water-vapor tables for water-vapor bands, wind "
              "tables (in knots) for scatterometer and SAR wind pictures, and radar tables (in dBZ) for the GPM "
              "and EarthCARE radars."),
    "colortable-editor": ("An infrared table is for pictures of cloud-top temperature; a water-vapor table is for "
                          "water-vapor pictures, which are always drawn from 0 to -90 °C."),
}
_PICTURE_FILTER = "Saved pictures (*_data.npz);;All files (*)"
# A whole one of the table's units, for the help line and the snap box.
_WHOLE = {"C": "whole degrees", "kt": "whole knots", "dBZ": "whole dBZ"}
# The range boxes' limits by units: the IR range every curve fits in, wind speeds up to
# 300 kt, and reflectivity from the faintest a cloud radar sees to beyond any rain.
_RANGE_LIMITS = {"C": (-150.0, 100.0), "kt": (0.0, 300.0), "dBZ": (-70.0, 100.0)}
# The range labels: an infrared table is warm and cold, the others high and low.
_RANGE_LABELS = {"C": ("Warm (top):", "Cold (bottom):"), "kt": ("Top:", "Bottom:"), "dBZ": ("Top:", "Bottom:")}
# The sample picture a table of each kind is first shown on, and how the list names them.
_SAMPLE_NAMES = {"ir": "Sample infrared picture (GOES-19, Genevieve)",
                 "wv": "Sample water-vapor picture (GOES-19, Genevieve)",
                 "wind": "Sample wind picture (RCM-2 SAR, Melissa)",
                 "radar": "Sample radar picture (GPM DPR, Eta)"}


def _help(model):
    """How to use the window, in the table's units: what its "?" button shows, a short
    paragraph for each part of it."""
    unit = model.unit_label
    return "\n\n".join((
        f"Click the bar to add a stop; drag a stop to move it (hold Shift for {_WHOLE[model.units]}). "
        "Right-click a stop for a hard step or to delete it.",
        f"Change the chosen stop's color in the color panel. The arrow keys move the chosen stop 1 {unit} "
        f"(with Shift, 0.1 {unit}); Delete removes it.",
        "Click the picture to choose the stop that colors that spot; Shift-click adds a stop there. Scroll on "
        "the picture to zoom; drag it to move around.",
        "Drag the divider between the color panel and the picture to the left to fold the color panel away "
        "and give the picture more room. Drag it back, or double-click it, to bring the panel back.",
        "Several storms, above the picture, shows the table on several storms at once as you edit; Storms… "
        "chooses which. Click a storm to bring it to the front, then click a spot on it to choose the stop that "
        "colors it, or Shift-click to add a stop; double-click it to see it on its own. Back to grid shows them "
        "all the same size again.",
    ))
# what _edit returns for a change the table would not take
_REFUSED = object()
PictureSpot = namedtuple("PictureSpot", "value in_table")
PictureSpot.__doc__ = """One spot of the editor's picture: `value` the pixel's value in the table's
units, `in_table` the value in the table whose color it is drawn in."""
# The table compared with, as it is drawn on the picture shown.
_Compared = namedtuple("_Compared", "name cmap norm vmax vmin rows lut")
_WV_LOCK = ("Water-vapor pictures are always drawn from 0 to -90 °C, so a water-vapor table "
            "keeps that range.")
# The column of zoom and copy buttons beside a single picture: about as wide as its widest
# button, so the picture, which a default-sized window limits by its height, keeps its size.
_CONTROLS_WIDTH = 150
_PNG_FILTER = "PNG picture (*.png)"
# How long after the last change the draft is written: long enough that a drag or a burst of
# typing is one write, short enough that little is lost if the program closes unexpectedly.
DRAFT_DELAY_MS = 2000
_HISTORY_TIPS = {
    True: "See the earlier versions of this table, kept each time you saved over it, and load one back in.",
    False: "This table has no saved versions yet. Each time you save over one of your tables, the version it "
           "replaces is kept here.",
}
# The window's layout, remembered between runs (tcviz.window_layout): its record's key, the
# size it first opens at (no bigger than the screen), and what the "?" says about it.
_LAYOUT_KEY = "editor"
_DEFAULT_SIZE = (1600, 860)
_LAYOUT_NOTE = "This window opens the size you leave it, with the color panel shown or folded away as you left it."
_DIVIDER_TIP = ("Drag left to fold the color panel away and give the picture more room; drag it back, or "
                "double-click, to bring the panel back.")
# What the picture's place shows (set_view): the one picture, or the storm grid -- and what
# the switch's two buttons say, and Several storms when there are no storms of the table's
# kind to try it on (the bundled samples; none for a winds or radar table in tcviz).
_VIEWS = ("one", "several")
_VIEW_TIPS = {
    "one": "See one storm on its own: zoom in on it, and compare it with another table.",
    "several": "See your table on several storms at once, all redrawn as you edit. Storms… chooses which.",
    "none": "There are no other storm pictures to try this kind of table on.",
}
_COMPARE_TIP = "Show another table, built-in or yours, beside this one on the same picture."
_SWIPE_TIP = "Show both tables on one picture and drag the line to see where they differ."
_COMPARE_OFF = "Compare works on one storm at a time."
# The picture's least height (PicturePane's own least size), of which the pictures give the
# switch's row its height (_give_panes_room).
_PANE_LEAST = 220


class _Step(QUndoCommand):
    """One change to the table: the state before and after it. Changes made by one drag
    (or one held arrow key, or one spin box) share a `merge` key and fold into one step."""

    _MERGING = 1

    def __init__(self, dialog, text, before, after, merge):
        super().__init__(text)
        self._dialog, self.before, self.after, self.merge = dialog, before, after, merge
        self._done = True           # pushed after the change was already made

    def redo(self):
        if self._done:
            self._done = False
            return
        self._dialog._restore(self.after)

    def undo(self):
        self._dialog._restore(self.before)

    def id(self):
        return self._MERGING if self.merge is not None else -1

    def mergeWith(self, other):
        if other.merge != self.merge:
            return False
        self.after = other.after
        # a drag that ends where it began is no step at all
        self.setObsolete(self.after == self.before)
        return True


class _VersionStep(_Step):
    """An earlier version loaded back in: the table and its description, undone and redone
    together (the description box is not part of the table's own state)."""

    def __init__(self, dialog, text, before, after, descriptions):
        super().__init__(dialog, text, before, after, None)
        self.descriptions = descriptions        # (before, after)

    def redo(self):
        first = self._done
        super().redo()
        if not first:
            self._dialog._set_description(self.descriptions[1])

    def undo(self):
        super().undo()
        self._dialog._set_description(self.descriptions[0])


class ColortableEditorDialog(QDialog):
    saved = Signal(dict)            # the stored entry, just before the app is told
    drawn = Signal()                # the table, the picture or the range was just drawn again (flush)

    def __init__(self, parent=None, entry=None, mode="add", source=None, kind="ir", draft=None):
        """`entry` is the table to start from (a stored table, or a duplicate_draft);
        `mode` "add", "edit" or "copy" (see the module); `source` the name a copy was made
        from, for the message the rest of the app gets; `kind` what a new table is ("ir",
        "wv", "wind" or "radar") when there is no `entry`. `draft` is a
        colortable_drafts.Draft left unsaved when the program last closed, whose `entry`
        this is: the editor carries on with it, marked changed."""
        super().__init__(parent)
        if mode not in _TITLES:
            raise ValueError(f"mode must be one of {sorted(_TITLES)}")
        self._mode = mode
        self._source = source
        self._original = entry["name"] if mode == "edit" and entry else None
        if draft is not None and mode == "edit":
            # the saved table the draft changes, not whatever the name box said by then
            self._original = draft.original
        # the draft this editor keeps its unsaved table in (a recovered one keeps its own
        # file), and the saved table's fingerprint when it opened, to tell later whether
        # that table was saved again meanwhile
        self._draft_id = draft.id if draft is not None else colortable_drafts.new_id()
        if draft is not None:
            self._draft_base = draft.base
        else:
            self._draft_base = colortable_drafts.fingerprint(entry) if self._original is not None else None
        self._draft_written = None          # what the draft file holds now, None for no file
        self._draft_failed = False          # a write failed: said once
        self._marked_changed = False        # a recovered draft is unsaved from the start
        self._autosaving = False            # set at the end of __init__: building the window is no edit
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(DRAFT_DELAY_MS)
        self._draft_timer.timeout.connect(self._autosave)
        self.entry = None           # the stored entry, once saved
        self.history_note = ""      # once saved: what could not be kept in History, if anything
        unreadable = False
        if entry:
            try:
                self.model = ColortableModel.from_entry(entry)
            except (KeyError, ValueError, TypeError):
                # a damaged table: its name and notes, and a plain table of its kind to start
                # again from
                self.model = ColortableModel.new_table(name=str(entry.get("name") or ""),
                                                       kind=_kind_or_ir(entry))
                self.model.description = str(entry.get("description") or "")
                unreadable = True
        else:
            self.model = ColortableModel.new_table(name=_free_new_name(), kind=kind)
        self.setWindowTitle(_TITLES[mode] + (f" -- {self._original}" if self._original else ""))
        self.undo_stack = QUndoStack(self)
        self.selected = None
        self.snap = False
        self._said = ""                     # the status line's message, shown again when the pointer leaves the picture
        self._cmap = (None, None)           # (model.version, built table)
        self._preview = None                # the PreviewSource on show
        # which picture is shown, as the picture list keeps it: "sample:ir", "sample:wv",
        # "bundled:<id>", "recent:<path>" or "file:<path>" (plain text: Qt cannot look a tuple
        # up in a list)
        self._picture_choice = f"sample:{self.model.kind}"
        self._bundled = {s.id: s for s in colortable_preview.bundled_samples()}
        self._recent = {k: [] for k in colortable_preview.SAMPLE_KINDS}
        self._loading = None                # the path being loaded in the background
        self._jobs = itertools.count(1)
        self._job = None
        self._pending_choice = None
        self._compared = (None, None)       # (what it was drawn for, (rows, colors))
        self._shown_kind = self.model.kind
        self._view = "one"                  # what the picture's place shows (set_view)
        self._kept_view = None              # the view the window was left in (_restore_layout)
        # a burst of drag events is drawn once: each schedules this, which runs when the
        # window next has nothing else to do
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(0)
        self._refresh_timer.timeout.connect(self.flush)
        self._build_ui()
        self._fill_fields()
        # what Cancel compares with, to know whether to ask first
        self._start_fields = self._fields_now()
        self._fill_compare_combo()
        self._load_choice(self._default_choice(self.model.kind))
        self._searched = False
        self._changed()
        self.flush()
        if self._kept_view == "several" and self.grid_samples():
            # as the window was left: the storm grid, its storms read in the background
            self.set_view("several", remember=False)
        if unreadable:
            self._say("This table's colors could not be read, so the editor starts again from a plain "
                      "black-to-white table. Save replaces the damaged one.")
        if draft is not None:
            self.mark_changed()
            # already on disk as it is: written again only once it changes
            self._draft_written = self._draft_content()
            if not unreadable:
                self._say(f"These are your unsaved changes from {colortable_drafts.when_words(draft.when)}. Save "
                          "keeps them; Cancel lets them go.")
        self._autosaving = True

    # ================================================================== layout

    def _build_ui(self):
        layout = QVBoxLayout(self)

        if self._original is not None:
            note = QLabel(f"Editing your table '{self._original}'. Save replaces it; a new name renames it.")
            note.setWordWrap(True)
            layout.addWidget(note)

        top = QHBoxLayout()
        form = QFormLayout()
        name_row = QHBoxLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("lowercase, e.g. mycurve")
        name_row.addWidget(self.name_edit, 1)
        name_row.addSpacing(12)
        name_row.addWidget(QLabel("Kind:"))
        self.kind_combo = QComboBox()
        # the kinds this edition offers (and, should one of another kind be opened, its own)
        self._kind_keys = [k for k, _ in _KINDS if k in edition.kinds() or k == self.model.kind]
        for kind in self._kind_keys:
            self.kind_combo.addItem(dict(_KINDS)[kind])
        self.kind_combo.setToolTip(_KIND_TIPS[edition.current()])
        name_row.addWidget(self.kind_combo)
        name_row.addSpacing(12)
        self.hidden_check = QCheckBox("Hide from the pickers")
        self.hidden_check.setToolTip("Keep this table out of the palette lists without deleting it.")
        name_row.addWidget(self.hidden_check)
        # Colortable Editor has no palette lists to hide a table from
        self.hidden_check.setVisible(edition.has_pickers())
        # how to use the window, in a pop-up that stays until clicked away: the help was two
        # dense lines over the bar, which took the picture's room
        name_row.addSpacing(12)
        self.help_btn = QToolButton()
        self.help_btn.setText("?")
        self.help_btn.setToolTip("How to use this window")
        self.help_btn.setFixedWidth(max(26, self.help_btn.sizeHint().height()))
        self.help_btn.clicked.connect(self.show_help)
        name_row.addWidget(self.help_btn)
        self.help_popup = HelpPopup(self, _help(self.model), note=_LAYOUT_NOTE,
                                    action=("Reset layout", "Put this window back to the size it first opens at, "
                                            "with the color panel shown.", self.reset_layout))
        form.addRow("Name:", _wrap(name_row))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("short description (shown in tooltips and lists)")
        form.addRow("Description:", self.desc_edit)
        range_row = QHBoxLayout()
        self.vmax_spin = self._range_spin()
        self.vmin_spin = self._range_spin()
        self.vmax_label = QLabel(_RANGE_LABELS["C"][0])
        self.vmin_label = QLabel(_RANGE_LABELS["C"][1])
        range_row.addWidget(self.vmax_label)
        range_row.addWidget(self.vmax_spin)
        range_row.addSpacing(12)
        range_row.addWidget(self.vmin_label)
        range_row.addWidget(self.vmin_spin)
        range_row.addSpacing(12)
        self.range_note = QLabel("")
        self.range_note.setWordWrap(True)
        range_row.addWidget(self.range_note, 1)
        form.addRow("Range:", _wrap(range_row))
        top.addLayout(form, 1)
        layout.addLayout(top)

        body = QHBoxLayout()
        self.stop_bar = StopBar(self)
        body.addWidget(self.stop_bar)
        self.scale_panel = ScalePanel()
        body.addWidget(self.scale_panel)
        self.stop_list = StopListPanel(self)
        body.addWidget(self.stop_list)
        # between the list and the picture: the chosen row on one side, what its color does
        # on the other -- and beside the picture in a splitter, so it can be folded away to
        # give the picture its room
        self.color_panel = ColorPanel(self)

        self.preview_box = preview_box = QGroupBox("On a real picture")
        self._preview_layout = preview_layout = QVBoxLayout(preview_box)
        # One storm / Several storms: the one picture or the storm grid in its place, two
        # buttons side by side, the one shown held down
        view_row = QHBoxLayout()
        view_row.setSpacing(0)
        self._view_group = QButtonGroup(self)
        self._view_group.setExclusive(True)
        self._view_buttons = {}
        for view, words in (("one", "One storm"), ("several", "Several storms")):
            button = QPushButton(words)
            button.setCheckable(True)
            button.setToolTip(_VIEW_TIPS[view])
            # a click leaves the keyboard where it was, so the arrow keys still move the stop
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.setAutoDefault(False)
            button.clicked.connect(functools.partial(self._on_view_button, view))
            self._view_group.addButton(button)
            self._view_buttons[view] = button
            view_row.addWidget(button)
        self.one_storm_btn, self.several_storms_btn = self._view_buttons["one"], self._view_buttons["several"]
        view_row.addStretch()
        self.view_row = _wrap(view_row)
        preview_layout.addWidget(self.view_row)
        choose_row = QHBoxLayout()
        choose_row.addWidget(QLabel("Picture:"))
        self.picture_combo = QComboBox()
        self.picture_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.picture_combo.setMinimumContentsLength(24)
        self.picture_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        choose_row.addWidget(self.picture_combo, 1)
        choose_row.addSpacing(8)
        self.compare_label = QLabel("Compare with:")
        choose_row.addWidget(self.compare_label)
        self.compare_combo = QComboBox()
        self.compare_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.compare_combo.setMinimumContentsLength(12)
        self.compare_combo.setToolTip(_COMPARE_TIP)
        choose_row.addWidget(self.compare_combo)
        self.swipe_check = QCheckBox("Swipe")
        self.swipe_check.setToolTip(_SWIPE_TIP)
        # offered while there is a table to compare with (_redraw)
        self.swipe_check.setVisible(False)
        choose_row.addWidget(self.swipe_check)
        preview_layout.addLayout(choose_row)
        # the Swipe box takes its room from the picture list's least width, so comparing needs
        # no wider a window than before it came (the window is about as wide as a laptop's
        # screen): (that least width, the room the box takes)
        self._picture_room = (self.picture_combo.minimumSizeHint().width(),
                              self.swipe_check.sizeHint().width() + max(0, choose_row.spacing()))
        panes = QHBoxLayout()
        # only this table's picture answers the mouse: the one compared with is another table
        # (no tooltip: it would sit over the picture while the status line reads it out);
        # both are looked at through one view, so they zoom and pan together
        self.picture_view = PictureView(self)
        self.picture_pane = PicturePane(interactive=True, view=self.picture_view)
        self.compare_pane = PicturePane(view=self.picture_view)
        self.picture_caption = QLabel("This table")
        self.compare_caption = QLabel("")
        # swiping, one picture shows both: this table's name over its left and the other's
        # over its right
        self.swipe_caption = QLabel("")
        self.swipe_caption.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.swipe_caption.setVisible(False)
        for pane, caption in ((self.picture_pane, self.picture_caption), (self.compare_pane, self.compare_caption)):
            column = QVBoxLayout()
            caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            names = QHBoxLayout()
            names.addWidget(caption, 1)
            if pane is self.picture_pane:
                names.addWidget(self.swipe_caption, 1)
            column.addLayout(names)
            column.addWidget(pane, 1)
            holder = QWidget()
            holder.setLayout(column)
            panes.addWidget(holder, 1)
            pane.holder = holder
        self.picture_area = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.picture_area.addLayout(panes, 1)
        self.picture_area.addWidget(self._build_picture_controls())
        # One storm: the pictures, their buttons and the lines under them, in one place
        # that Several storms swaps for the storm grid
        self.one_page = QWidget()
        one_layout = QVBoxLayout(self.one_page)
        one_layout.setContentsMargins(0, 0, 0, 0)
        one_layout.addLayout(self.picture_area, 1)
        self.picture_note = QLabel("")
        self.picture_note.setWordWrap(True)
        one_layout.addWidget(self.picture_note)
        preview_layout.addWidget(self.one_page, 1)
        self.storm_grid = StormGrid(self, controls_width=_CONTROLS_WIDTH)
        preview_layout.addWidget(self.storm_grid, 1)
        self._show_view()
        # the color panel and the picture, a handle between them: the picture takes all the
        # room the window has to spare, and the color panel's too once it is folded away
        self.body_splitter = PanelSplitter()
        self.body_splitter.addWidget(self.color_panel)
        self.body_splitter.addWidget(preview_box)
        # the color panel keeps its width: the splitter only folds it away or brings it back
        self.body_splitter.keep_width(self.color_panel)
        self.body_splitter.setCollapsible(0, True)
        self.body_splitter.setStretchFactor(1, 1)
        self.body_splitter.handle(1).setToolTip(_DIVIDER_TIP)
        self.body_splitter.changed.connect(self._on_panels_changed)
        body.addWidget(self.body_splitter, 1)
        layout.addLayout(body, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        bottom = QHBoxLayout()
        self.undo_btn = QPushButton("Undo")
        self.undo_btn.setToolTip("Take back the last change to the table (Ctrl+Z).")
        self.undo_btn.clicked.connect(self.undo_stack.undo)
        self.redo_btn = QPushButton("Redo")
        self.redo_btn.setToolTip("Make the change you took back again (Ctrl+Shift+Z or Ctrl+Y).")
        self.redo_btn.clicked.connect(self.undo_stack.redo)
        bottom.addWidget(self.undo_btn)
        bottom.addWidget(self.redo_btn)
        bottom.addSpacing(12)
        self.snap_check = QCheckBox("Snap to whole degrees")
        self.snap_check.setToolTip("Dragged stops land on whole numbers (holding Shift while dragging does "
                                   "the same).")
        self.snap_check.toggled.connect(self._on_snap)
        bottom.addWidget(self.snap_check)
        bottom.addSpacing(12)
        self.text_btn = QPushButton("Type or paste stops…")
        self.text_btn.setToolTip("Write the stops as text, or paste a table someone shared with you.")
        self.text_btn.clicked.connect(self._on_type_or_paste)
        bottom.addWidget(self.text_btn)
        self.history_btn = QPushButton("History…")
        self.history_btn.clicked.connect(self._on_history)
        bottom.addWidget(self.history_btn)
        self._show_history_button()
        bottom.addStretch()
        self.save_btn = QPushButton("Save")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._on_save)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        bottom.addWidget(self.save_btn)
        bottom.addWidget(self.cancel_btn)
        layout.addLayout(bottom)

        self.undo_stack.canUndoChanged.connect(self.undo_btn.setEnabled)
        self.undo_stack.canRedoChanged.connect(self.redo_btn.setEnabled)
        self.undo_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        # Explicit keys, not QKeySequence.StandardKey: the standard Redo already holds
        # Ctrl+Shift+Z here, and the same key in two shortcuts makes both do nothing.
        # In the name or description box the box's own undo takes the key first.
        for keys, slot in (("Ctrl+Z", self.undo_stack.undo), ("Ctrl+Shift+Z", self.undo_stack.redo),
                           ("Ctrl+Y", self.undo_stack.redo)):
            QShortcut(QKeySequence(keys), self, activated=slot)

        self.kind_combo.currentIndexChanged.connect(self._on_kind_combo)
        self.vmax_spin.valueChanged.connect(self._on_range_spin)
        self.vmin_spin.valueChanged.connect(self._on_range_spin)
        self.picture_combo.activated.connect(self._on_picture_combo)
        self.compare_combo.currentIndexChanged.connect(lambda _i: self._schedule_refresh())
        self.name_edit.textChanged.connect(lambda text: self.picture_caption.setText(text.strip() or "This table"))
        # the boxes beside the table are no undo steps (_edit), so they start the draft's
        # timer themselves
        for signal in (self.name_edit.textChanged, self.desc_edit.textChanged, self.hidden_check.toggled):
            signal.connect(self._schedule_autosave)
        self.swipe_check.toggled.connect(lambda _on: self._schedule_refresh())
        self.picture_pane.hovered.connect(self._on_picture_hover)
        self.picture_pane.hovered_compared.connect(self._on_compared_hover)
        self.picture_pane.clicked.connect(self.pick_from_picture)
        # a zoom, a drag, a new picture or a new size: the zoom in words follows
        self.picture_pane.shown.connect(self._show_zoom)
        self._give_panes_room()
        self._restore_layout()

    def _build_picture_controls(self):
        """Zoom in, Zoom out, Fit and the zoom in words; Copy picture, Save picture and
        their tick boxes. One picture leaves room at its side in a default-sized window
        (its height limits it), two compared leave room below them (their width does), so
        the buttons stand in a column beside one picture and in two rows under two
        (_place_picture_controls)."""
        self.picture_controls = QWidget()
        controls = QVBoxLayout(self.picture_controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self.zoom_row = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.zoom_in_btn = self._picture_button(
            "Zoom in", "Look closer at the middle of the picture. The mouse wheel zooms in and out wherever the "
            "pointer is.", lambda: self.picture_pane.zoom_by(ZOOM_STEP))
        self.zoom_out_btn = self._picture_button(
            "Zoom out", "Step back out. The mouse wheel zooms in and out wherever the pointer is.",
            lambda: self.picture_pane.zoom_by(1 / ZOOM_STEP))
        self.fit_btn = self._picture_button("Fit", "Show the whole picture again.", self.picture_view.fit)
        for button in (self.zoom_in_btn, self.zoom_out_btn, self.fit_btn):
            self.zoom_row.addWidget(button)
        self.zoom_label = QLabel("")
        self.zoom_label.setWordWrap(True)
        self.zoom_label.setToolTip("Scroll the mouse wheel over the picture to zoom in and out where the pointer is. "
                                   "Zoomed in, drag the picture to move around it; a click that doesn't move still "
                                   "chooses a stop.")
        self.zoom_row.addWidget(self.zoom_label)
        self._zoom_row_end = QSpacerItem(0, 0)
        self.zoom_row.addItem(self._zoom_row_end)
        controls.addLayout(self.zoom_row)
        self._controls_gap = QSpacerItem(0, 0)
        controls.addItem(self._controls_gap)
        self.share_row = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self.copy_picture_btn = self._picture_button(
            "Copy picture", "Copy the whole picture in this table, every pixel, ready to paste into a message.",
            self.copy_picture)
        self.save_picture_btn = self._picture_button(
            "Save picture…", "Save the whole picture in this table, every pixel, as a PNG file.", self.save_picture)
        self.share_card_btn = self._picture_button(
            "Share card…", "Make one picture to post: the table's name, its color scale and the storm drawn with it.",
            self.open_share_card)
        self.scale_check = QCheckBox("With the color scale")
        self.scale_check.setChecked(True)
        self.scale_check.setToolTip("Put the color scale and the table's name in the copied or saved picture"
                                    + (", where tcviz pictures carry them." if not edition.is_editor() else "."))
        self.pair_check = QCheckBox("Both, side by side")
        self.pair_check.setToolTip("Copy or save this table's picture and the one it is compared with, side by side.")
        # offered while there is a table to compare with (_redraw)
        self.pair_check.setVisible(False)
        for widget in (self.copy_picture_btn, self.save_picture_btn, self.share_card_btn, self.scale_check,
                       self.pair_check):
            self.share_row.addWidget(widget)
        self._share_row_end = QSpacerItem(0, 0)
        self.share_row.addItem(self._share_row_end)
        controls.addLayout(self.share_row)
        self._controls_beside = None
        self._place_picture_controls(beside=True)
        return self.picture_controls

    def _picture_button(self, text, tip, slot):
        button = QPushButton(text)
        button.setToolTip(tip)
        # a click leaves the keyboard where it was, so the arrow keys still move the stop
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.clicked.connect(lambda _checked=False: slot())
        return button

    def _place_picture_controls(self, beside):
        """The zoom and copy buttons in a column beside the picture (`beside`: one picture,
        or two swiped into one), or in two rows under the two pictures compared."""
        if beside == self._controls_beside:
            return
        self._controls_beside = beside
        fixed, grow = QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        if beside:
            self.picture_area.setDirection(QBoxLayout.Direction.LeftToRight)
            for row in (self.zoom_row, self.share_row):
                row.setDirection(QBoxLayout.Direction.TopToBottom)
            self._zoom_row_end.changeSize(0, 0, fixed, fixed)
            self._share_row_end.changeSize(0, 0, fixed, fixed)
            # the zoom buttons at the top, the copy buttons at the bottom
            self._controls_gap.changeSize(0, 12, fixed, grow)
            self.picture_controls.setFixedWidth(_CONTROLS_WIDTH)
        else:
            self.picture_area.setDirection(QBoxLayout.Direction.TopToBottom)
            for row in (self.zoom_row, self.share_row):
                row.setDirection(QBoxLayout.Direction.LeftToRight)
            self._zoom_row_end.changeSize(0, 0, grow, fixed)
            self._share_row_end.changeSize(0, 0, grow, fixed)
            self._controls_gap.changeSize(0, 0, fixed, fixed)
            self.picture_controls.setMinimumWidth(0)
            self.picture_controls.setMaximumWidth(16777215)
        # Share card… stands with Copy and Save beside one picture; under two it ends the
        # zoom row, which has room for it: in the copy row it would make comparing need a
        # wider window
        for row in (self.zoom_row, self.share_row):
            row.removeWidget(self.share_card_btn)
        if beside:
            self.share_row.insertWidget(self.share_row.indexOf(self.save_picture_btn) + 1, self.share_card_btn)
        else:
            self.zoom_row.addWidget(self.share_card_btn)
        self.picture_controls.layout().invalidate()
        # the picture compared with was just shown or hidden: the pictures' area is measured
        # again, or the window could take a size for the layout it had (and grow by it)
        colortable_widgets.measure_layout_afresh(self.picture_area)

    def _give_panes_room(self):
        """The pictures' least height: PicturePane's own, less the One storm / Several
        storms row above them. The row takes its height from the pictures, so the window
        needs no more height than it did before the row came: under two pictures compared,
        and beside one at ordinary font sizes, the pictures set the window's least height;
        beside one with bigger fonts, the column of buttons does, which the row's height
        left too (the storm grid's own button, Other storms…, stood there)."""
        row = self.view_row.sizeHint().height() + max(0, self._preview_layout.spacing())
        least = max(_PANE_LEAST // 2, _PANE_LEAST - row)
        for pane in (self.picture_pane, self.compare_pane):
            pane.setMinimumHeight(least)

    @staticmethod
    def _range_spin():
        spin = QDoubleSpinBox()
        spin.setRange(*_RANGE_LIMITS["C"])
        spin.setDecimals(2)
        spin.setSuffix(" °C")
        spin.setKeyboardTracking(False)
        return spin

    # ================================================================== the window's layout, remembered

    def show_help(self):
        """The "?": how to use the window (_help), in a pop-up under the button that stays
        until clicked away, with Reset layout under it."""
        self.help_popup.show_under(self.help_btn)

    def colors_folded(self):
        """Whether the color panel is folded away, the picture taking its room."""
        return self.body_splitter.folded(0)

    def fold_colors(self, fold):
        """Fold the color panel away (`fold`) or bring it back."""
        self.body_splitter.set_folded(0, fold)

    def _on_panels_changed(self):
        # folded away with the keyboard in it (the hex box), the keys go back to the bar
        if self.colors_folded() and self.color_panel.isAncestorOf(QApplication.focusWidget()):
            self.stop_bar.setFocus()

    def _restore_layout(self):
        """Open as the window was last left (window_layout): its size, made to fit this
        screen, and maximized or not; the color panel folded away or not once it is shown
        (_restore_panels); one storm or several (set_view, once the picture is loaded). With
        nothing saved, as it first opens."""
        kept = window_layout.get(_LAYOUT_KEY)
        room = colortable_widgets.screen_room(self)
        self.resize(*(window_layout.fit_size(kept.get("size"), room) or _default_size(room)))
        # the layout as the window is shown (showEvent): closing keeps it only if it changed
        self._layout_base = None
        self._kept_panels = kept.get("splitter")
        self._kept_view = kept.get("view") if kept.get("view") in _VIEWS else None
        if kept.get("maximized") is True:
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    def _restore_panels(self):
        """The panels as they were left -- unless their saved sizes don't fit the window.
        Done as the window is first shown: only then is it laid out (one measured before
        its style has, would be measured wider than it is)."""
        saved, self._kept_panels = self._kept_panels, None
        splitter = self.body_splitter
        least = [self.color_panel.minimumWidth(), self.preview_box.minimumSizeHint().width()]
        sizes = window_layout.fit_sizes(saved, least, sum(splitter.sizes()), collapsible=(0,))
        if sizes is not None:
            splitter.setSizes(sizes)
        # saved on a big screen or never: either way it must fit this one
        self._fit_narrow_screen()

    def _fit_narrow_screen(self):
        """On a screen too narrow for the whole window -- a 1080p laptop at 150% scaling has
        1280 px, the window with its color panel at least 1296 -- open with the color panel
        folded away (its handle's arrow brings it back), at a size that fits."""
        room = colortable_widgets.screen_room(self)
        if not room or self.minimumSizeHint().width() <= room[0] - window_layout.SCREEN_MARGIN[0]:
            return
        self.fold_colors(True)
        # once shown: resized while it is being shown, the window's first layout pass sizes
        # it back up to the least it had with the panel
        QTimer.singleShot(0, lambda: self.resize(*_default_size(room)))

    def reset_layout(self):
        """Reset layout (in the "?"): forget the remembered layouts -- layout.json goes -- and
        put this window back as it first opens, the color panel shown and one storm in the
        picture's place."""
        window_layout.reset()
        self._kept_panels = None
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMaximized)
        self.resize(*_default_size(colortable_widgets.screen_room(self)))
        self.fold_colors(False)
        self.set_view("one", remember=False)
        if self._layout_base is not None:
            # back as it first opens is nothing to keep
            self._layout_base = self._layout_record()
        self._say("The window is back to the size it first opens at, with the color panel shown.")

    def _layout_record(self):
        """The window's layout, as window_layout keeps it: its size (when maximized, the size
        it goes back to), whether it is maximized, and the splitter's panel widths."""
        maximized = self.isMaximized()
        size = self.normalGeometry().size() if maximized else self.size()
        if size.isEmpty():
            size = self.size()
        return {"size": [size.width(), size.height()], "maximized": maximized, "splitter": self.body_splitter.sizes()}

    def _remember_layout(self):
        """Keep the layout for next time, if it changed while the window was open. A window
        never shown (the smoke run, a test) had no layout chosen, and keeps none."""
        if self._layout_base is None:
            return
        now = self._layout_record()
        if _layout_changed(self._layout_base, now):
            window_layout.update(_LAYOUT_KEY, now)
        self._layout_base = now

    def setVisible(self, visible):
        # first shown: measured as it is now, not as the splitter measured the picture's side
        # while the window was being built (with the picture compared with, hidden since)
        if visible and self._layout_base is None:
            # (the switch's row measured in the fonts the window is shown with)
            self._give_panes_room()
            colortable_widgets.measure_afresh(self)
        super().setVisible(visible)

    def done(self, result):
        # saved or not, the window's layout is kept as it closes -- and a layout that can't be
        # kept never keeps the window from closing
        with contextlib.suppress(Exception):
            self._remember_layout()
        super().done(result)

    # ================================================================== the fields

    def _fill_fields(self):
        m = self.model
        self.name_edit.setText(m.name)
        self.desc_edit.setText(m.description)
        self.desc_edit.setCursorPosition(0)
        self.hidden_check.setChecked(m.hidden)
        self._show_kind_and_range()

    def _show_kind_and_range(self):
        m = self.model
        for widget in (self.kind_combo, self.vmax_spin, self.vmin_spin):
            widget.blockSignals(True)
        try:
            self.kind_combo.setCurrentIndex(self._kind_keys.index(m.kind))
            # the units first: a spin box clamps a value to the limits it has when given it
            for spin in (self.vmax_spin, self.vmin_spin):
                spin.setRange(*_RANGE_LIMITS[m.units])
                spin.setSuffix(f" {m.unit_label}")
            self.vmax_spin.setValue(m.vmax)
            self.vmin_spin.setValue(m.vmin)
        finally:
            for widget in (self.kind_combo, self.vmax_spin, self.vmin_spin):
                widget.blockSignals(False)
        top, bottom = _RANGE_LABELS[m.units]
        self.vmax_label.setText(top)
        self.vmin_label.setText(bottom)
        self.help_popup.set_text(_help(m))
        self.snap_check.setText(f"Snap to {_WHOLE[m.units]}")
        locked = m.kind == "wv"
        self.vmax_spin.setEnabled(not locked)
        self.vmin_spin.setEnabled(not locked)
        note = ""
        if locked:
            note = _WV_LOCK
            if m.stored_range is not None:
                note += (f" Its own range was {degrees_text(m.stored_range[1])} to {degrees_text(m.stored_range[0])} °C, "
                         "which only an infrared picture would use; it is saved at 0 to -90 °C.")
        self.range_note.setText(note)

    def _fields_now(self):
        return (self.name_edit.text().strip(), self.desc_edit.text().strip(), self.hidden_check.isChecked())

    def _on_snap(self, on):
        self.snap = bool(on)

    def _on_kind_combo(self, index):
        self.set_kind(self._kind_keys[index])

    def _on_range_spin(self, _value):
        # the box shows two decimals: an end it did not change keeps its exact value
        # (bd's cold end is -94.99999999999997 C)
        vmax = self.model.vmax if self.vmax_spin.value() == round(self.model.vmax, 2) else self.vmax_spin.value()
        vmin = self.model.vmin if self.vmin_spin.value() == round(self.model.vmin, 2) else self.vmin_spin.value()
        if (vmin, vmax) != (self.model.vmin, self.model.vmax):
            # clicks on one box's arrows in a row are one undo step; the other box is another
            self.set_range(vmin, vmax, merge=("range", "warm" if vmax != self.model.vmax else "cold"))

    # ================================================================== changes (one undo step each)

    def _edit(self, text, change, merge=None):
        """Make one change to the table as an undo step; returns what `change` returned,
        or _REFUSED when the table could not take it (the status line says why)."""
        before = self.model.state()
        try:
            result = change()
        except EditRefused as e:
            self.model.restore(before)
            self._say(str(e))
            self._changed()
            return _REFUSED
        after = self.model.state()
        if after != before:
            self.undo_stack.push(_Step(self, text, before, after, merge))
        self._say("")
        self._changed()
        return result

    def _restore(self, state):
        """Undo and redo: put a state back."""
        self.model.restore(state)
        if self.selected is not None and self.selected not in self.model.ids():
            self.selected = None
        self._show_kind_and_range()
        self._changed()

    def add_stop(self, value, color=None):
        """A new stop at `value`, in the table's units (in `color`, else the color there
        now), chosen; its id, or None when the table could not take it."""
        sid = self._edit("Add a stop", lambda: self.model.add_stop(value, color))
        if sid is _REFUSED:
            return None
        self.select(sid)
        return sid

    def move_stop(self, sid, value, merge=None):
        """Move a stop to `value` (in the table's units); True when the table took it."""
        return self._edit("Move a stop", lambda: self.model.move_stop(sid, value), merge=merge) is not _REFUSED

    def set_stop_color(self, sid, color, merge=None):
        """Recolor stop `sid`; True when the table took it. Changes with one `merge` key --
        one drag in the color panel, one held nudge button -- are one undo step."""
        return self._edit("Change a color", lambda: self.model.set_color(sid, color), merge=merge) is not _REFUSED

    def delete_stop(self, sid):
        done = self._edit("Delete a stop", lambda: self.model.delete_stop(sid)) is not _REFUSED
        if done and self.selected == sid:
            self.selected = None
            self._changed()
        return done

    def make_hard_step(self, sid=None, value=None):
        """Split stop `sid` -- or make a new pair at `value` -- into a hard step, and
        choose its cold-side half, ready for a new color. Returns that half's id (None when
        the table could not take it)."""
        if sid is not None:
            new = self._edit("Make a hard step", lambda: self.model.make_hard_step(sid))
        else:
            new = self._edit("Make a hard step", lambda: self.model.hard_step_at(value)[1])
        if new is _REFUSED:
            return None
        self.select(new)
        self._say(f"Made a hard step at {degrees_text(self.model.get(new).value)} {self.model.unit_label}. Give "
                  "the chosen half a new color in the color panel to see the step.")
        return new

    def set_range(self, vmin, vmax, merge=None):
        done = self._edit("Change the range", lambda: self.model.set_range(vmin, vmax), merge=merge)
        self._show_kind_and_range()
        return done is not _REFUSED

    def set_kind(self, kind):
        return self._edit("Change the kind", lambda: self.model.set_kind(kind)) is not _REFUSED

    def select(self, sid):
        if sid != self.selected:
            self.selected = sid
            self.stop_bar.update()
            self.stop_list.show_selected()
        self.color_panel.sync()

    def choose_color(self, sid):
        """Go to stop `sid`'s color: choose the stop and put the keyboard in the color
        panel's hex box (a double-click on a stop, or "Change color") -- the panel brought
        back first, if it was folded away."""
        self.select(sid)
        self.fold_colors(False)
        self.color_panel.focus_color()

    def current_cmap(self):
        """The table as it will be drawn, or None when it cannot be."""
        version = self.model.version
        if self._cmap[0] != version:
            try:
                self._cmap = (version, self.model.build())
            except ValueError:
                self._cmap = (version, None)
        built = self._cmap[1]
        return built[0] if built else None

    # ================================================================== showing it

    def _changed(self):
        """The table changed: the bar and the list now, the picture and the scale as soon
        as the window is free (a burst of drag events is drawn once)."""
        if self.model.kind != self._shown_kind:
            self._kind_switched()
        self.stop_bar.update()
        self.stop_list.refresh()
        self.color_panel.sync()
        problem = self.model.problem() if self.current_cmap() is None else None
        if problem:
            self._say(f"This table can't be drawn yet: {problem}.")
        self.save_btn.setEnabled(problem is None)
        self._schedule_refresh()
        self._schedule_autosave()

    def _kind_switched(self):
        """The kind changed (an edit, or its undo): the range boxes, the sample, and the
        tables to compare with follow. A picture the new kind cannot be tried on -- an
        infrared one for a winds table, say -- gives way to the new kind's sample."""
        self._shown_kind = self.model.kind
        self._show_kind_and_range()
        on_sample = self._picture_choice.startswith(("sample:", "bundled:"))
        fits = self._preview is not None and self._preview.kind in colortable_preview.PICTURE_KINDS[self.model.kind]
        default = self._default_choice(self.model.kind)
        if (on_sample and self._picture_choice != default) or not (on_sample or fits):
            self._load_choice(default)
        else:
            self._fill_picture_combo()
        self._fill_compare_combo()
        if self._view == "several" and not self.grid_samples():
            # a winds or radar table has no storms for the grid: the one picture instead
            self.set_view("one", remember=False)

    def _schedule_refresh(self):
        self._refresh_timer.start()

    def flush(self):
        """Redraw the scale and the pictures now (the window does this by itself as soon as
        it is free after a change)."""
        self._refresh_timer.stop()
        self._redraw()
        self._show_zoom()
        # the storm grid follows, drawing itself again a moment later
        self.drawn.emit()

    def _redraw(self):
        cmap = self.current_cmap()
        # a card shows the table drawn: none of a table that can't be
        self.share_card_btn.setEnabled(cmap is not None)
        offered = bool(self.grid_samples())
        self.several_storms_btn.setEnabled(offered)
        self.several_storms_btn.setToolTip(_VIEW_TIPS["several" if offered else "none"])
        built = self._cmap[1]
        source = self._preview
        if cmap is None:
            self.scale_panel.set_scale(None, 0, 0)
            for pane in (self.picture_pane, self.compare_pane):
                pane.set_message("This table can't be drawn yet.")
            return
        _cmap, vmax_k, vmin_k = built
        vmax_d, vmin_d = source.drawing_range(vmax_k, vmin_k) if source else (vmax_k, vmin_k)
        # the units render labels the scale in: C (from Kelvin), kt (from m/s), dBZ
        self.scale_panel.set_scale(cmap, vmax_d, vmin_d, self.model.units)
        if self._view == "several":
            # the one picture is out of sight, and drawn again as it comes back (set_view):
            # an edit draws only the storm grid's storms
            return
        compare = self.compare_combo.currentData()
        swipe = self.swiping()
        self.compare_pane.holder.setVisible(bool(compare) and not swipe)
        self._place_picture_controls(beside=not compare or swipe)
        self._show_swipe_controls(compare, swipe)
        if not swipe:
            self.picture_pane.set_swipe(None)
        if source is None or self._loading:
            # (an edit while another picture is read leaves the words, not the picture before it)
            message = "Loading the picture…" if self._loading else "No picture."
            self.picture_pane.set_message(message)
            self.compare_pane.set_message(message)
            return
        self.picture_pane.set_message("")
        norm = Normalize(vmin=vmin_d, vmax=vmax_d)
        self.picture_pane.set_index_image(source.rows(norm, cmap.N), stride=source.stride,
                                          full_shape=source.full_shape,
                                          full_rows=functools.partial(source.window_rows, norm, cmap.N))
        self.picture_pane.set_lut(colortable_preview.lut_for(cmap))
        self.picture_caption.setText(self.name_edit.text().strip() or "This table")
        if compare:
            self._show_compare(compare, source)

    def swiping(self):
        """Whether the table compared with is swiped into this table's picture: Swipe
        ticked, with a table chosen in Compare with."""
        return bool(self.compare_combo.currentData()) and self.swipe_check.isChecked()

    def _show_swipe_controls(self, compare, swipe):
        """Swipe and Both, side by side while there is a table to compare with; swiping,
        the two names over the picture's two sides, and Copy and Save saying what they
        hand over."""
        self.swipe_check.setVisible(bool(compare))
        least, room = self._picture_room
        self.picture_combo.setMinimumWidth(least - room if compare else 0)
        self.pair_check.setVisible(bool(compare))
        self.picture_caption.setAlignment(Qt.AlignmentFlag.AlignLeft if swipe else Qt.AlignmentFlag.AlignHCenter)
        self.swipe_caption.setVisible(swipe)
        what = ("the whole picture as swiped, this table left of the line and the other right of it" if swipe
                else "the whole picture in this table")
        self.copy_picture_btn.setToolTip(f"Copy {what}, every pixel, ready to paste into a message.")
        self.save_picture_btn.setToolTip(f"Save {what}, every pixel, as a PNG file.")

    def _show_compare(self, name, source):
        # the other table does not change while this one is edited: worked out once per
        # table, picture and change to the library, not on every edit
        key = (name, id(source), colortable_registry.cache_key())
        if self._compared[0] != key:
            try:
                if source.water_vapor:
                    other, norm, vmax, vmin = colortable_registry.get(name, vmax=colortable_preview.WV_VMAX_K,
                                                                      vmin=colortable_preview.WV_VMIN_K)
                else:
                    other, norm, vmax, vmin = colortable_registry.get(name)
            except Exception:
                self._compared = (None, None)
                self.compare_pane.set_message(f"'{name}' can't be drawn.")
                self.picture_pane.set_swipe(None)
                self.swipe_caption.setText(f"'{name}' can't be drawn.")
                return
            norm = norm or Normalize(vmin=vmin, vmax=vmax)
            self._compared = (key, _Compared(name, other, norm, vmax, vmin, source.rows(norm, other.N),
                                             colortable_preview.lut_for(other)))
        compared = self._compared[1]
        full_rows = functools.partial(source.window_rows, compared.norm, compared.cmap.N)
        if self.swiping():
            # one picture: the other table's rows and colors go to this table's pane, which
            # draws them right of its line (the pane compared with is hidden, and left as it was)
            self.picture_pane.set_swipe(compared.rows, compared.lut, full_rows)
            self.swipe_caption.setText(name)
            return
        self.compare_pane.set_message("")
        self.compare_pane.set_index_image(compared.rows, stride=source.stride, full_shape=source.full_shape,
                                          full_rows=full_rows)
        self.compare_pane.set_lut(compared.lut)
        self.compare_caption.setText(name)

    def _show_zoom(self):
        """The zoom in words, and the zoom buttons that can do something now."""
        pane = self.picture_pane
        self.zoom_label.setText(zoom_words(pane))
        shown = pane.picture_rect() is not None and not pane.message
        zoomed = self.picture_view.zoom > 1.0
        self.zoom_in_btn.setEnabled(shown and self.picture_view.zoom < pane.max_zoom() - 1e-9)
        self.zoom_out_btn.setEnabled(shown and zoomed)
        self.fit_btn.setEnabled(shown and zoomed)

    def _say(self, text):
        self._said = text
        self.status.setText(text)

    def say(self, text):
        """Put a sentence in the status line (for the editor's widgets)."""
        self._say(text)

    # ================================================================== the picture under the mouse

    def picture_spot(self, row, col):
        """What this table's picture shows at (row, col), counted in the saved picture's
        own pixels, as the picture pane counts them at every zoom: a PictureSpot of the
        pixel's value in the table's units and the value in the table whose color it is
        drawn in. The two are the same but on a water-vapor picture with a table of another
        range, which is stretched to 0 to -90 °C there. None off the picture, on a pixel
        with no data, and while no picture is drawn."""
        source = self._preview
        pane = self.picture_pane
        if (source is None or pane.message or pane.full_shape != source.full_shape or pane.stride != source.stride
                or self.current_cmap() is None):
            return None
        return self.spot_for(source.saved_value(row, col), source)

    def spot_for(self, raw, source):
        """The PictureSpot of a pixel whose value is `raw` -- in the picture's own units, K,
        m/s or dBZ -- on picture `source` (a PreviewSource): its value in the table's units,
        and the value in the table whose color it is drawn in, at the range `source` is
        drawn at. None for a pixel with no data (`raw` None) and while the table can't be
        drawn. The one picture's (picture_spot) and the storm grid's storms' alike."""
        if raw is None or self.current_cmap() is None:
            return None
        _cmap, vmax_k, vmin_k = self._cmap[1]
        m = self.model
        value = colortable_preview.in_table_units(raw, m.units)
        vmax_d, vmin_d = source.drawing_range(vmax_k, vmin_k)
        if (vmax_d, vmin_d) == (vmax_k, vmin_k):
            return PictureSpot(value, value)
        return PictureSpot(value, m.vmin + (raw - vmin_d) / (vmax_d - vmin_d) * m.span)

    def _on_picture_hover(self, pixel):
        self.show_spot(None if pixel is None else self.picture_spot(*pixel))

    def show_spot(self, spot, hint=None):
        """The status line reading out `spot` (a PictureSpot), the pixel under the pointer,
        then what a click there does (`hint`, else choosing or adding a stop) -- or, with
        None (off the picture, or a pixel with no data), the last message again."""
        if spot is None:
            self.status.setText(self._said)
            return
        unit = self.model.unit_label
        text = f"Under the pointer: {_value_text(spot.value)} {unit}"
        if abs(spot.in_table - spot.value) >= 0.05:
            text += f", drawn in this table's color for {_value_text(spot.in_table)} {unit}"
        self.status.setText(f"{text}. {hint or 'Click to choose the stop that colors it; Shift-click to add a stop there.'}")

    def _on_compared_hover(self, pixel):
        """The pointer right of the swipe's line, over the table compared with: the value
        there, and which table that side is. A click there chooses nothing."""
        spot = None if pixel is None else self.picture_spot(*pixel)
        compared = self._compared[1]
        if spot is None or compared is None:
            self.status.setText(self._said)
            return
        self.status.setText(f"Under the pointer: {_value_text(spot.value)} {self.model.unit_label}, drawn in "
                            f"{compared.name}, the table compared with. Click left of the line to choose a stop "
                            "of this table.")

    def pick_from_picture(self, row, col, add=False):
        """A click on this table's picture at (row, col): choose the stop that colors that
        spot (ColortableModel.stop_for_value), its color then in the color panel -- or, with
        `add` (Shift-click), add a stop at its value in the color already there. Returns
        the stop's id, or None (no data there, or no picture)."""
        return self.pick_spot(self.picture_spot(row, col), add=add)

    def pick_spot(self, spot, add=False):
        """A click on a picture, the one or a storm of the grid, where it shows `spot` (a
        PictureSpot): as pick_from_picture. The stop's id, or None (no data there)."""
        if spot is None:
            return None
        m = self.model
        unit = m.unit_label
        past = "top" if spot.in_table > m.vmax else "bottom" if spot.in_table < m.vmin else None
        if add and past is None:
            value = float(round(spot.in_table)) if self.snap else round(spot.in_table, 1)
            there = next((v.id for v in m.ordered() if abs(v.value - value) < 1e-6), None)
            if there is None:
                sid = self.add_stop(value)
                if sid is not None:
                    self._say(f"Added a stop at {degrees_text(m.get(sid).value)} {unit} in the color already "
                              "there; change it in the color panel.")
                return sid
            self.select(there)
            self._say(f"There is already a stop at {degrees_text(value)} {unit}, so it is chosen instead.")
            return there
        sid = m.stop_for_value(spot.in_table)
        self.select(sid)
        stop = f"{degrees_text(m.get(sid).value)} {unit}"
        shown = f"{_value_text(spot.value)} {unit}"
        if past is None:
            self._say(f"{shown} is colored by the stop at {stop}; change it in the color panel.")
            return sid
        if m.units == "C":
            beyond = "warmer than the top" if past == "top" else "colder than the bottom"
        else:
            beyond = "above the top" if past == "top" else "below the bottom"
        text = f"{shown} is {beyond} of this table, so it is drawn in the color of the stop at the {past} ({stop})."
        if add:
            text += " A new stop has to go inside the table's range."
        self._say(text)
        return sid

    # ================================================================== copying and saving the picture

    def _no_picture(self, verb):
        """Why there is no picture to `verb` ("copy", "save") now, or ""."""
        if self._preview is None or self._loading:
            return f"There is no picture to {verb} yet."
        if self.current_cmap() is None:
            return f"This table can't be drawn yet, so there is no picture to {verb}."
        return ""

    def shared_picture(self):
        """(PIL image, the whole number each pixel was enlarged by) of the whole saved
        picture in this table, as Copy picture and Save picture hand it over: every pixel,
        colored as tcviz colors it (colortable_preview.full_picture), with the color scale
        and the table's name when "With the color scale" is ticked; beside the picture
        compared with when "Both, side by side" is, or else, swiping, swiped at the line
        as the picture shows it (colortable_preview.swiped_picture), each table's scale and
        name on its own side. (None, None) when there is no picture."""
        self.flush()
        if self._no_picture("copy"):
            return None, None
        source = self._preview
        cmap, vmax_k, vmin_k = self._cmap[1]
        vmax_d, vmin_d = source.drawing_range(vmax_k, vmin_k)
        scale = self.scale_check.isChecked()
        units = self.model.units
        name = self.name_edit.text().strip() or "This table"
        how, compared = self._handed_over()
        if how == "swipe":
            fraction = self.picture_pane.swipe_fraction()
            mine = colortable_preview.PictureTable(cmap, Normalize(vmin=vmin_d, vmax=vmax_d), vmax_d, vmin_d, name)
            theirs = colortable_preview.PictureTable(compared.cmap, compared.norm, compared.vmax, compared.vmin,
                                                     compared.name)
            return colortable_preview.swiped_picture(source, mine, theirs, 0.5 if fraction is None else fraction,
                                                     units, scale=scale)
        image, factor = colortable_preview.full_picture(
            source, cmap, Normalize(vmin=vmin_d, vmax=vmax_d), vmax_d, vmin_d, units, scale=scale,
            name=name if scale else None)
        if how == "pair":
            other, _factor = colortable_preview.full_picture(source, compared.cmap, compared.norm, compared.vmax,
                                                             compared.vmin, units, scale=scale,
                                                             name=compared.name if scale else None)
            image = colortable_preview.side_by_side(image, other)
        return image, factor

    def _handed_over(self):
        """What Copy picture and Save picture hand over: ("one", None) for this table's
        picture; ("pair", the table compared with) for both side by side, when that box is
        ticked; ("swipe", the table compared with) for the swiped picture, swiping."""
        name = self.compare_combo.currentData()
        if not name or self._compared[0] != (name, id(self._preview), colortable_registry.cache_key()):
            return "one", None
        if self.pair_check.isChecked():
            return "pair", self._compared[1]
        if self.swiping():
            return "swipe", self._compared[1]
        return "one", None

    def _handed_over_words(self, how, compared):
        """"the picture", "both pictures side by side", or which side of the swiped one is
        which, for the status line."""
        if how == "pair":
            return "both pictures side by side"
        if how == "swipe":
            mine = self.name_edit.text().strip() or "this table"
            return f"the swiped picture, {mine} left of the line and {compared.name} right of it"
        return "the picture"

    def _size_words(self, image, factor):
        """"1920 × 1920 pixels (its 384 × 384 pixels each drawn 5 × 5, ...)"."""
        width, height = image.size
        words = f"{width} × {height} pixels"
        if factor > 1:
            rows, cols = self._preview.full_shape
            why = ("so its scale and name can be read" if edition.is_editor() else "as tcviz draws a small picture")
            words += f" (its {cols} × {rows} pixels each drawn {factor} × {factor}, {why})"
        return words

    def copy_picture(self):
        """Copy picture: the whole picture (shared_picture) onto the clipboard. Whether the
        clipboard took it; the status line says either way."""
        problem = self._no_picture("copy")
        if problem:
            self._say(problem)
            return False
        with _busy():
            image, factor = self.shared_picture()
            what = self._handed_over_words(*self._handed_over())
            taken = image is not None and self._put_on_clipboard(_qimage(image))
        if image is None:
            self._say(self._no_picture("copy"))
        elif taken:
            # on Wayland, and on X without a clipboard keeper, what was copied goes when the
            # program that copied it closes
            self._say(f"Copied {what}, {self._size_words(image, factor)}. Paste it before you close "
                      f"{edition.app_name()}.")
        else:
            self._say("The picture couldn't be put on the clipboard. Use Save picture… to keep it as a file "
                      "instead.")
        return taken

    def save_picture(self):
        """Save picture…: the whole picture (shared_picture) as a PNG file where the person
        says. The path saved to, or None."""
        global _last_save_folder
        problem = self._no_picture("save")
        if problem:
            self._say(problem)
            return None
        path = self._ask_save_path(str(self._suggested_file()))
        if not path:
            return None
        path = Path(path)
        if path.suffix.lower() != ".png":
            path = path.with_name(path.name + ".png")
        with _busy():
            image, factor = self.shared_picture()
            what = self._handed_over_words(*self._handed_over())
            if image is None:
                self._say(self._no_picture("save"))
                return None
            try:
                image.save(path, "PNG")
            except PermissionError:
                # Windows, most often: a picture of that name is open in a viewer
                self._say("The picture couldn't be saved there: a file of that name is open in another program, "
                          "or the folder can't be written to. Nothing was saved.")
                return None
            except OSError as e:
                self._say(f"The picture couldn't be saved there ({e.strerror or e}). Nothing was saved.")
                return None
        _last_save_folder = path.parent
        self._say(f"Saved {what}, {self._size_words(image, factor)}, as {path}.")
        return path

    def _suggested_file(self):
        """The file Save picture offers: the table's name (and the other's, side by side or
        swiped), in the folder saved to last, else Pictures, else the home folder."""
        name = self.name_edit.text().strip() or "colortable"
        how, compared = self._handed_over()
        if how != "one":
            name += ("_beside_" if how == "pair" else "_swiped_with_") + compared.name
        name = re.sub(r"[^\w.-]+", "_", name).strip("._") or "colortable"
        folder = _last_save_folder
        if folder is None or not folder.is_dir():
            folder = _pictures_folder()
        return folder / f"{name}.png"

    def _put_on_clipboard(self, image):
        """Hand `image` (a QImage) to the clipboard; whether the clipboard holds it now."""
        clipboard = QGuiApplication.clipboard()
        try:
            clipboard.setImage(image)
            held = clipboard.mimeData()
        except RuntimeError:
            return False
        return held is not None and held.hasImage()

    # ================================================================== the share card

    def open_share_card(self):
        """Share card…: the window that makes one picture to post of what the editor shows
        (share_card_dialog). The window, or None when there is no card to make (the status
        line says why)."""
        from tcviz_gui.pages.share_card_dialog import ShareCardDialog
        if self._loading:
            self._say("The picture is still loading. Press Share card… again once it shows.")
            return None
        pictures = self.card_pictures()
        if not pictures:
            self._say("This table can't be drawn yet, so there is no card to make.")
            return None
        window = ShareCardDialog(self, pictures, self.card_title(), self.model.units)
        self._run(window)
        return window

    def _compared_shown(self):
        """The table compared with (_Compared) as the picture shows it now, or None."""
        name = self.compare_combo.currentData()
        if not name or self._compared[0] != (name, id(self._preview), colortable_registry.cache_key()):
            return None
        return self._compared[1]

    def card_pictures(self):
        """What Share card… puts on the card, as the editor shows it (tcviz.share_card's
        CardPicture each): this table's picture; beside the picture of the table compared
        with, comparing; or one picture in both tables, split where the line is, swiping.
        Each table at the range the picture is drawn at, with the lines shown under the
        picture. With no picture, the table alone; [] when the table can't be drawn."""
        from tcviz import share_card
        self.flush()
        if self.current_cmap() is None:
            return []
        source = self._preview
        cmap, vmax_k, vmin_k = self._cmap[1]
        vmax_d, vmin_d = source.drawing_range(vmax_k, vmin_k) if source is not None else (vmax_k, vmin_k)
        mine = colortable_preview.PictureTable(cmap, Normalize(vmin=vmin_d, vmax=vmax_d), vmax_d, vmin_d,
                                               self.name_edit.text().strip() or "This table")
        if source is None:
            return [share_card.CardPicture(None, (mine,))]
        caption = share_card.caption_lines(source)
        compared = self._compared_shown()
        if compared is None:
            return [share_card.CardPicture(source.full, (mine,), caption)]
        theirs = colortable_preview.PictureTable(compared.cmap, compared.norm, compared.vmax, compared.vmin,
                                                 compared.name)
        if self.swiping():
            fraction = self.picture_pane.swipe_fraction()
            return [share_card.CardPicture(source.full, (mine, theirs), caption,
                                           0.5 if fraction is None else fraction)]
        return [share_card.CardPicture(source.full, (mine,), caption),
                share_card.CardPicture(source.full, (theirs,), caption)]

    def card_title(self):
        """The title Share card… starts from: the table's name -- "mine vs bd05" while
        another table is compared with it."""
        name = self.name_edit.text().strip() or "This table"
        compared = self._compared_shown()
        return f"{name} vs {compared.name}" if compared is not None else name

    # ================================================================== the storm grid

    def grid_samples(self):
        """The bundled samples this table can be tried on (colortable_preview.PICTURE_KINDS:
        an infrared table on the water-vapor ones too), in the manifest's order -- what the
        storm grid offers. [] for a winds or radar table, and with no samples."""
        kinds = colortable_preview.PICTURE_KINDS.get(self.model.kind, ())
        return [s for s in self._bundled.values() if s.kind in kinds]

    def drawn_table(self):
        """(cmap, vmax K, vmin K) the table is drawn with now -- the same tuple until it next
        changes -- or None when it can't be drawn. What the storm grid draws its storms with,
        each at the range it is drawn at (PreviewSource.drawing_range)."""
        return self._cmap[1] if self.current_cmap() is not None else None

    def table_name(self):
        """The table's name as the pictures carry it: the Name box, or "This table"."""
        return self.name_edit.text().strip() or "This table"

    def view(self):
        """What the picture's place shows: "one" (One storm, the one picture, which zooms
        and compares) or "several" (Several storms, the storm grid)."""
        return self._view

    def set_view(self, view, remember=True):
        """Show one storm or several in the picture's place: "one" or "several" (_VIEWS).
        The storm grid shows only with storms to try the table on (grid_samples). The view
        the person chooses (`remember`) is kept with the window's layout, for next time.
        Whether it shows that now."""
        if view not in _VIEWS:
            raise ValueError(f"view must be one of {_VIEWS}")
        if view == "several" and not self.grid_samples():
            self._say(_VIEW_TIPS["none"])
            self._show_view()
            return False
        if remember and view != self._view:
            window_layout.update(_LAYOUT_KEY, {"view": view})
        if view != self._view:
            # a reading of a picture no longer shown goes: the last message again
            self.status.setText(self._said)
        self._view = view
        self._show_view()
        if view == "several":
            self.storm_grid.activate()
        else:
            # what changed while the grid was shown
            self.flush()
        return True

    def _on_view_button(self, view, _checked=False):
        self.set_view(view)

    def _show_view(self):
        """The switch, and the picture or the grid, as the view is; Compare with and Swipe
        greyed while the grid is shown (each keeps what it was set to, for the one picture)."""
        several = self._view == "several"
        for view, button in self._view_buttons.items():
            button.setChecked(view == self._view)
            # the view shown in the theme's selection colors: the style's own pressed-in
            # look is barely darker than a button that is not
            palette = QApplication.palette(button)
            if view == self._view:
                palette.setColor(QPalette.ColorRole.Button, palette.color(QPalette.ColorRole.Highlight))
                palette.setColor(QPalette.ColorRole.ButtonText, palette.color(QPalette.ColorRole.HighlightedText))
            button.setPalette(palette)
        self.one_page.setVisible(not several)
        self.storm_grid.setVisible(several)
        for widget in (self.compare_label, self.compare_combo, self.swipe_check):
            widget.setEnabled(not several)
        self.compare_label.setToolTip(_COMPARE_OFF if several else "")
        self.compare_combo.setToolTip(_COMPARE_OFF if several else _COMPARE_TIP)
        self.swipe_check.setToolTip(_COMPARE_OFF if several else _SWIPE_TIP)
        colortable_widgets.measure_layout_afresh(self._preview_layout)

    def open_storm(self, sample_id):
        """Show bundled sample `sample_id` on its own: One storm, with it in the Picture box
        (a double-click on a storm of the grid), read in the background as one chosen there
        is. Whether it is shown, or being read to be."""
        choice = f"bundled:{sample_id}"
        if sample_id not in self._bundled:
            return False
        if choice != self._picture_choice or self._preview is None:
            self._load_bundled(choice)
        self.set_view("one")
        self._say(f"Showing {self._bundled[sample_id].label} on its own. Several storms shows the grid again.")
        return True

    def show_sample(self, sample_id):
        """Show bundled sample `sample_id` in the Picture box, as choosing it there does.
        Whether it is shown now."""
        choice = f"bundled:{sample_id}"
        if sample_id not in self._bundled:
            return False
        if choice != self._picture_choice or self._preview is None:
            with _busy():
                self._load_choice(choice)
        return self._picture_choice == choice and self._preview is not None

    # ================================================================== the pictures

    def _fill_compare_combo(self):
        current = self.compare_combo.currentData()
        self.compare_combo.blockSignals(True)
        try:
            self.compare_combo.clear()
            self.compare_combo.addItem("Nothing", None)
            names = colortable_export.exportable_names(self.model.kind)
            mine = colortable_library.user_table_names()
            ordered = [n for n in names if n in mine] + [n for n in names if n not in mine]
            for name in ordered:
                if name == self._original:
                    continue          # the saved version of this very table: still offered under its name
                self.compare_combo.addItem(name, name)
            if self._original and self._original in names:
                self.compare_combo.insertItem(1, f"{self._original} (as saved)", self._original)
            at = self.compare_combo.findData(current)
            self.compare_combo.setCurrentIndex(max(0, at))
        finally:
            self.compare_combo.blockSignals(False)

    def _fill_picture_combo(self):
        """The pictures this kind of table is tried on: the bundled samples first, in the
        manifest's order, then tcviz's own samples (Colortable Editor: the bundled ones only,
        or its own two while there are none), then your recent pictures of this kind, then
        "Choose a file" (tcviz only)."""
        combo = self.picture_combo
        combo.blockSignals(True)
        try:
            combo.clear()
            kinds = colortable_preview.PICTURE_KINDS[self.model.kind]
            bundled = [s for s in self._bundled.values() if s.kind in kinds]
            for sample in bundled:
                combo.addItem(sample.label, f"bundled:{sample.id}")
                combo.setItemData(combo.count() - 1, sample.description or sample.label, Qt.ItemDataRole.ToolTipRole)
            if not (edition.is_editor() and bundled):
                if bundled:
                    combo.insertSeparator(combo.count())
                for kind in kinds:
                    combo.addItem(_SAMPLE_NAMES[kind], f"sample:{kind}")
            recent = self._recent.get(self.model.kind, [])
            if recent:
                combo.insertSeparator(combo.count())
                for picture in recent:
                    combo.addItem(f"Recent: {picture.label}", f"recent:{picture.path}")
            if combo.findData(self._picture_choice) < 0:
                # a picture chosen from a file, or a recent one of the other kind
                label = self._preview.label if self._preview else self._picture_choice.split(":", 1)[1]
                combo.insertSeparator(combo.count())
                combo.addItem(label, self._picture_choice)
            if edition.opens_picture_files():
                combo.insertSeparator(combo.count())
                combo.addItem("Choose a saved picture…", "choose:")
            combo.setCurrentIndex(max(0, combo.findData(self._picture_choice)))
        finally:
            combo.blockSignals(False)

    def _on_picture_combo(self, index):
        choice = self.picture_combo.itemData(index)
        if self._view == "several" and choice:
            # a picture chosen while the grid is shown is one to see on its own
            self.set_view("one")
        if not choice or choice == self._picture_choice:
            return
        what, value = choice.split(":", 1)
        if what == "bundled":
            self._load_bundled(choice)
        elif what == "sample":
            self._load_choice(choice)
        elif what in ("recent", "file"):
            self._load_file(value, choice)
        elif what == "choose":
            path = self._ask_picture_file()
            if path:
                self._load_file(path, f"file:{path}")
            else:
                self._fill_picture_combo()

    def _default_choice(self, kind):
        """The picture a table of `kind` is first shown on: the first bundled sample of that
        kind in the manifest (else of one it can be tried on), in both editions -- or, with
        none (a winds or radar table, or no manifest), tcviz's own sample of that kind."""
        kinds = (kind, *[k for k in colortable_preview.PICTURE_KINDS[kind] if k != kind])
        for wanted in kinds:
            for sample in self._bundled.values():
                if sample.kind == wanted:
                    return f"bundled:{sample.id}"
        return f"sample:{kind}"

    def _load_choice(self, choice):
        """Show a sample now: "sample:<kind>" (tcviz's own) or "bundled:<id>" -- read here, on
        the window's own thread (a picture chosen in the Picture box is read in the
        background instead: _load_bundled)."""
        self._job = next(self._jobs)        # a picture still being read in the background is not wanted now
        what, value = choice.split(":", 1)
        if what == "bundled":
            sample = self._bundled.get(value)
            try:
                if sample is None:
                    raise ValueError("it is no longer among the samples")
                self._preview = colortable_preview.load_bundled(sample)
            except (OSError, ValueError) as e:
                self._preview = None
                self._say(f"The sample picture could not be opened ({e}).")
            self._picture_choice = choice
            self._loading = None
            self._after_picture()
            return
        self._load_sample(value)

    def _load_sample(self, kind):
        self._job = next(self._jobs)
        try:
            self._preview = colortable_preview.load_sample(kind)
        except (OSError, ValueError) as e:
            self._preview = None
            self._say(f"The sample picture could not be opened ({e}).")
        self._picture_choice = f"sample:{kind}"
        self._loading = None
        self._after_picture()

    def _load_file(self, path, choice):
        """Open a saved picture in the background: a big one takes a moment to read."""
        from tcviz_gui.worker import run_in_background
        self._start_loading(path, choice)
        run_in_background(_load_picture_job, path, str(_output_dir()), self._job,
                          on_finished=self._on_picture_loaded, on_failed=self._on_picture_failed)

    def _load_bundled(self, choice):
        """A bundled sample chosen in the Picture box, read in the background: a VIIRS or SGLI
        one takes about a second to unpack, which held the whole window still."""
        from tcviz_gui.worker import run_in_background
        sample = self._bundled.get(choice.split(":", 1)[1])
        if sample is None:
            self._load_choice(choice)       # says it can't be opened
            return
        self._start_loading(choice, choice)
        run_in_background(_load_bundled_job, sample, self._job, on_finished=self._on_bundled_loaded,
                          on_failed=self._on_picture_failed)

    def _start_loading(self, what, choice):
        self._loading = what
        self._job = next(self._jobs)
        self.picture_pane.set_message("Loading the picture…")
        self.compare_pane.set_message("Loading the picture…")
        self._pending_choice = choice
        at = self.picture_combo.findData(choice)
        if at >= 0:
            # the Picture box names it at once (as it does a picture chosen in it)
            self.picture_combo.blockSignals(True)
            self.picture_combo.setCurrentIndex(at)
            self.picture_combo.blockSignals(False)

    def _on_bundled_loaded(self, result):
        job, source, why = result
        if job != self._job:
            return                  # a picture asked for before the latest one
        self._loading = None
        self._picture_choice = self._pending_choice
        self._preview = source
        if why is not None:
            self._say(f"The sample picture could not be opened ({why}).")
        self._after_picture()

    def _on_picture_loaded(self, result):
        job, source = result
        if job != self._job:
            return                  # a picture asked for before the latest one
        self._preview = source
        self._picture_choice = self._pending_choice
        self._loading = None
        self._say("")
        self._after_picture()

    def _on_picture_failed(self, message):
        if self._loading is None:
            return                  # a picture shown since: this one is not wanted any more
        self._loading = None
        self._say(f"That picture could not be opened: {message}")
        self._after_picture()

    def _after_picture(self):
        source = self._preview
        # another picture is seen whole first
        self.picture_view.fit()
        if source is None:
            self.picture_note.setText("")
        else:
            notes = [source.note or source.label + "."]
            if source.stride > 1:
                notes.append(f"Shown with 1 in every {source.stride} pixels across and down until you zoom in; "
                             "the colors are exact.")
            if source.water_vapor:
                notes.append("On a water-vapor picture every table is drawn from 0 to -90 °C.")
            self.picture_note.setText("\n".join(notes))           # one line each, under the title
        self._fill_picture_combo()
        self._schedule_refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if self._layout_base is None:
            self._restore_panels()
            self._layout_base = self._layout_record()
        # a change made before the window showed (a version loaded from History…) is kept too
        self._schedule_autosave()
        if not self._searched:
            self._searched = True
            # Colortable Editor opens no pictures of the person's own, so has none to find
            if edition.opens_picture_files():
                self._find_recent()

    def _find_recent(self):
        """Look for your recent pictures in the background; the picture list gains them
        when the search is done. Started when the window first shows, not when it is made:
        an output folder can hold thousands of pictures."""
        from tcviz_gui.worker import run_in_background
        run_in_background(colortable_preview.recent_pictures, str(_output_dir()),
                          on_finished=self._on_recent_found)

    def _on_recent_found(self, found):
        self._recent = found or {k: [] for k in colortable_preview.SAMPLE_KINDS}
        self._fill_picture_combo()

    # ================================================================== type or paste

    def _on_type_or_paste(self):
        from tcviz_gui.pages.add_colortable_dialog import AddColortableDialog
        dialog = AddColortableDialog(self, entry=self._entry_now(), mode="text")
        if not (self._run(dialog) and dialog.fields):
            return
        self.apply_fields(dialog.fields)

    def apply_fields(self, fields):
        """Take a whole table's fields -- what "Type or paste stops" hands back -- as one
        undo step (the name, description and hiding as they come). A winds or radar table
        carries its range as vmin/vmax in its units, an infrared one as vmin_c/vmax_c."""
        kind = _kind_or_ir(fields)
        bounds = ("vmin_c", "vmax_c") if kind == "ir" else ("vmin", "vmax") if kind in ("wind", "radar") else None

        def change():
            self.model.replace_stops(fields["stops"], reversed_=fields.get("reversed", fields.get("reversed_")),
                                     levels=fields.get("levels"))
            self.model.set_kind(kind)
            if bounds and all(b in fields for b in bounds):
                self.model.set_range(fields[bounds[0]], fields[bounds[1]])
            return True
        if self._edit("Type or paste stops", change) is _REFUSED:
            return False
        if fields.get("name"):
            self.name_edit.setText(fields["name"])
        if fields.get("description"):
            self.desc_edit.setText(fields["description"])
            self.desc_edit.setCursorPosition(0)
        if "hidden" in fields:
            self.hidden_check.setChecked(bool(fields["hidden"]))
        self.selected = None
        self._show_kind_and_range()
        self._changed()
        return True

    def _entry_now(self):
        entry = self.model.to_entry()
        entry.update(name=self.name_edit.text().strip(), description=self.desc_edit.text().strip(),
                     hidden=self.hidden_check.isChecked())
        return entry

    # ================================================================== earlier versions

    def _show_history_button(self):
        """History… works for a table of yours that has been saved over at least once; a new
        table or a copy has no versions yet."""
        has = self._original is not None and colortable_history.has_versions(self._original)
        self.history_btn.setEnabled(has)
        self.history_btn.setToolTip(_HISTORY_TIPS[has])

    def _on_history(self):
        from tcviz_gui.pages.colortable_history_dialog import ColortableHistoryDialog
        window = ColortableHistoryDialog(self, self._original, action="restore")
        if self._run(window) and window.chosen is not None:
            self.restore_version(window.chosen)

    def restore_version(self, version):
        """Load an earlier version (a colortable_history.Version) into the editor -- its
        stops, range, kind and description -- as one undo step. Nothing is saved until Save.
        Whether it was loaded; the status line says either way."""
        if colortable_history.same_table(version.entry, self._entry_now()):
            self._say(f"The table open now is already the same as version {version.label}.")
            return False
        try:
            loaded = ColortableModel.from_entry(version.entry).state()
        except (KeyError, ValueError, TypeError):
            self._say(f"Version {version.label} can't be read, so it was not loaded.")
            return False
        before = self.model.state()
        # the range the table last had in each of the other units stays, for a change of kind
        ranges = dict(before.ranges)
        ranges.update(loaded.ranges)
        after = loaded._replace(ranges=tuple(sorted(ranges.items())))
        descriptions = (self.desc_edit.text(), version.entry.get("description", ""))
        self.model.restore(after)
        self.undo_stack.push(_VersionStep(self, f"Restore version {version.label}", before, after, descriptions))
        self.selected = None
        self._set_description(descriptions[1])
        self._show_kind_and_range()
        self._changed()
        self._say(f"Version {version.label} loaded; press Save to keep it.")
        return True

    def _set_description(self, text):
        self.desc_edit.setText(text)
        self.desc_edit.setCursorPosition(0)

    # ================================================================== saving

    @property
    def original_name(self):
        return self._original

    def _on_save(self):
        name = self.name_edit.text().strip()
        self.model.name = name
        self.model.description = self.desc_edit.text().strip()
        self.model.hidden = self.hidden_check.isChecked()

        # what could not be kept in History, said by whoever opened the editor (it closes)
        problems = []

        def save(replace):
            return colortable_library.save(name, self.model.stored_stops(), original=self._original,
                                           replace=replace, history_problems=problems, **self.model.save_fields())
        try:
            try:
                entry = save(replace=False)
            except user_colortables.NameTaken:
                if not self._confirm_replace(name):
                    return
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
        self.history_note = " ".join(problems)
        self.undo_stack.setClean()
        self._start_fields = self._fields_now()
        self._marked_changed = False
        self._drop_draft()
        self.saved.emit(entry)
        self._announce(entry["name"])
        self.accept()

    def _announce(self, name):
        if self._original is not None and name != self._original:
            library_events.notify("renamed", self._original, name)
        elif self._mode == "copy" and self._source:
            library_events.notify("duplicated", self._source, name)
        else:
            library_events.notify("saved", name)

    def has_changes(self):
        return self._marked_changed or not self.undo_stack.isClean() or self._fields_now() != self._start_fields

    def mark_changed(self):
        """Count the table as unsaved from now on, whatever undo does: it came back from a
        draft, so even the state it opened in was never saved. Closing asks first."""
        self._marked_changed = True

    def reject(self):
        if self.has_changes() and not self._confirm_discard():
            return
        self._drop_draft()
        super().reject()

    # ================================================================== kept in case the program closes

    def _schedule_autosave(self, *_args):
        """A change: the draft is written (or removed) DRAFT_DELAY_MS after the last one.
        Only by an editor on screen: one never shown -- a smoke run's, a test's -- has no
        one's work in it to keep, and must not leave a draft behind."""
        if self._autosaving and self.isVisible():
            self._draft_timer.start()

    def _draft_content(self):
        return (self._entry_now(), self._mode, self._original, self._source)

    def _autosave(self):
        """Keep the unsaved table in its draft file -- or, with nothing unsaved (an undo back
        to what was saved), remove the file. A write that fails never stops the editing:
        the status line says so once."""
        if not (self._autosaving and self.isVisible()):
            return
        try:
            if not self.has_changes():
                if self._draft_written is not None:
                    colortable_drafts.remove(self._draft_id)
                    self._draft_written = None
                return
            content = self._draft_content()
            if content == self._draft_written:
                return
            entry, mode, original, source = content
            colortable_drafts.write(self._draft_id, entry, mode, original, source=source, base=self._draft_base)
            self._draft_written = content
        except Exception as e:  # noqa: BLE001 -- a safety copy that can't be kept must never stop the editing
            if not self._draft_failed:
                self._draft_failed = True
                self._say(f"Your unsaved changes can't be kept in case {edition.app_name()} closes unexpectedly: "
                          f"{colortable_history.plain_reason(e)}. You can keep editing, and Save still works.")

    def _drop_draft(self):
        """Saved, or let go: no more autosaving, and the draft file goes."""
        self._autosaving = False
        self._draft_timer.stop()
        with contextlib.suppress(Exception):
            colortable_drafts.remove(self._draft_id)
        self._draft_written = None

    # The pop-ups, each in one place so a test can answer it.

    def _confirm_replace(self, name):
        answer = QMessageBox.question(
            self, "Replace table?", f"Replace the existing table '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_discard(self):
        answer = QMessageBox.question(
            self, "Close without saving?", "Close the editor and lose the changes you made to this table?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _ask_picture_file(self):
        path, _filter = QFileDialog.getOpenFileName(self, "Choose a saved picture", str(_output_dir()),
                                                    _PICTURE_FILTER)
        return path or None

    def _ask_save_path(self, suggested):
        path, _filter = QFileDialog.getSaveFileName(self, "Save picture", suggested, _PNG_FILTER)
        return path or None

    def _run(self, dialog):
        return modal.run(dialog)


def _load_picture_job(path, root, job):
    """Runs on a background thread: one saved picture, ready to preview."""
    return job, colortable_preview.load_picture(path, root=root)


def _load_bundled_job(sample, job):
    """Runs on a background thread: a bundled sample, ready to preview -- (job, the picture,
    None), or (job, None, why it can't be opened)."""
    try:
        return job, colortable_preview.load_bundled(sample), None
    except (OSError, ValueError) as e:
        return job, None, str(e)


def _output_dir():
    """tcviz's pictures folder, where its saved pictures are looked for -- imported only when
    asked: Colortable Editor opens no pictures of the person's own, and ships without
    tcviz.paths, which also knows where tcviz keeps its sign-in file."""
    from tcviz import paths
    return paths.output_dir()


# Where Save picture saved last, offered again next time (in this run of the app).
_last_save_folder = None


@contextlib.contextmanager
def _busy():
    """The wait cursor while a big picture is drawn, copied or written."""
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


def _qimage(image):
    """A PIL RGB image as a QImage of its own (the bytes copied, so it outlives them)."""
    data = image.tobytes("raw", "RGB")
    return QImage(data, image.width, image.height, 3 * image.width, QImage.Format.Format_RGB888).copy()


def _value_text(value):
    """A value read off the picture, to a tenth: -63.25 -> "-63.2", 85.0 -> "85"."""
    return degrees_text(round(value, 1))


def _kind_or_ir(fields):
    """The kind a table's fields say (colortable_model.kind_of_entry), or infrared when they
    say nothing readable."""
    try:
        return kind_of_entry({"stops": [], **fields})
    except (TypeError, ValueError, KeyError):
        return "ir"


def _default_size(room):
    """The size the window first opens at: _DEFAULT_SIZE, or as much of the screen as there
    is (`room`, colortable_widgets.screen_room)."""
    return window_layout.fit_size(_DEFAULT_SIZE, room)


def _layout_changed(before, now):
    """Whether two _layout_record()s differ in what is restored: the size, maximized, and the
    width of every panel but the picture, which takes whatever room is left."""
    return (any(before[key] != now[key] for key in ("size", "maximized"))
            or before["splitter"][:-1] != now["splitter"][:-1])


def _wrap(layout):
    widget = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    widget.setLayout(layout)
    return widget


def _free_new_name():
    """my_table, then my_table2, ...: the first name no table has, yours or built in."""
    taken = ({e.get("name") for e in user_colortables.load() if isinstance(e, dict)}
             | set(colortable_registry.palettes()) | set(colortable_registry.registered_builders()))
    with contextlib.suppress(Exception):
        taken |= set(user_colortables._reserved_names())
    for n in itertools.count(1):
        name = "my_table" if n == 1 else f"my_table{n}"
        if name not in taken:
            return name


def _pictures_folder():
    """Where Save picture offers to save first: the system's Pictures folder (on Windows,
    the one Explorer calls Pictures, wherever it was moved), else the home folder."""
    for folder in QStandardPaths.standardLocations(QStandardPaths.StandardLocation.PicturesLocation):
        if folder and Path(folder).is_dir():
            return Path(folder)
    return Path.home()


def open_editor(parent=None, entry=None, mode="add", source=None):
    """Run the editor to its end; the stored entry, or None when nothing was saved."""
    dialog = ColortableEditorDialog(parent, entry=entry, mode=mode, source=source)
    return dialog.entry if modal.run(dialog) else None

