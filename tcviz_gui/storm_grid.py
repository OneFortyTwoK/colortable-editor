"""The storm grid: the table being edited, on several storms at once -- what the color table
editor shows in place of its one picture in its Several storms view
(ColortableEditorDialog.set_view).

Storms… beside the grid opens a checklist of the bundled samples
(colortable_preview.bundled_samples) the table can be tried on: those of the table's own
kind first, then those of the other kind it can be tried on, unticked at first -- an
infrared table on the water-vapor storms, which are drawn at 0 to -90 °C whatever the
table's range, as the editor's Picture box draws them (colortable_preview.PICTURE_KINDS,
PreviewSource.drawing_range). With nothing chosen before, the first six of the table's own
kind are ticked; what is ticked is remembered for each kind of table in layout.json beside
the tables (tcviz.window_layout, under "storm_grid": {"ir": [ids], "wv": [ids]}).

Each storm ticked is a small picture drawn with the table being edited, its name under it,
as many across as leaves each the most room (best_columns). The storms share whatever room
the window gives the grid and always fit, however many are ticked, so the grid never needs
a bigger window than the one picture does (AREA_LEAST). They follow every edit: the editor
says each time it has drawn (its `drawn` signal), and the grid draws itself again at most
every REDRAW_MS, so dragging a stop stays smooth.

Each storm answers the mouse as the editor's one picture does -- it is a PicturePane of its
own, never zoomed: the value under the pointer in the editor's status line, a click to
choose the stop that colors that spot, Shift-click to add a stop there (through the
editor's show_spot and pick_spot, so the words are the same). The value is the storm's own
pixel, read from its thinned values, at the range its colors are drawn at: 0 to -90 °C on a
water-vapor storm. A double-click shows the storm on its own, as the editor's one picture.

The pictures are drawn as the editor draws its own (colortable_preview): the table row of
each pixel is worked out once per range (PreviewSource.rows), and an edit is one lookup of
the table's colors when a storm is next painted -- so each pixel is exactly the color tcviz
gives it. Each storm is read on a background thread (tcviz_gui.worker), one at a time,
"Loading Polo 2026…" in the line under the grid -- a 20 MB VIIRS sample takes about a
second to unpack -- and only its thinned values are kept (load_cell: at most CELL_PIXELS,
about 500 x 500 or 1 MB, where the VIIRS picture itself is 60 MB). What was read stays for
the editor's life, so ticking a storm again, or coming back to Several storms, draws it at
once.

Copy grid, Save grid… and Share card…, beside the grid where the one picture has Copy
picture, Save picture… and its own Share card…, make one picture of the grid with
tcviz.share_card: the storms ticked, drawn in the table with its color scale beside them,
each with its title and data line under it, about 1,900 pixels across. Those are drawn from
the saved pictures themselves, every pixel exact: read again one at a time on the
background thread and thinned at once to the size the card shows them at (card_values:
share_card.picture_room and share_card.fit), so no more than one whole picture is ever
held. Copy grid and Save grid… hand the grid over as the editor's Copy picture and Save
picture do, saying how it went in the editor's status line; Share card… opens the editor's
Share card window with it, its title to change.

A storm whose file is missing or can't be read is left out, and the line under the grid
says so in plain words.
"""
import collections
import functools
import itertools
import math
from pathlib import Path

from matplotlib.colors import Normalize
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QSpacerItem, QVBoxLayout, QWidget,
)

from tcviz import colortable_preview, edition, share_card, window_layout
from tcviz_gui import colortable_widgets
from tcviz_gui.colortable_widgets import PicturePane
from tcviz_gui.worker import run_in_background

LAYOUT_RECORD = "storm_grid"
# The most pixels a storm of the grid keeps: plenty for a picture a few hundred pixels
# across on screen (a 3,859-pixel VIIRS picture keeps 1 pixel in 8 across and down).
CELL_PIXELS = 250_000
# How many storms are ticked when nothing was chosen before: the first of the table's kind.
DEFAULT_TICKED = 6
# The least time between two drawings of the grid while the table changes: a drag in the
# editor is drawn there at every step, and here about 25 times a second.
REDRAW_MS = 40
# The least room the storms take, (across, down): less than the one picture and its controls
# need, so switching to Several storms never makes the editor's least window size bigger.
AREA_LEAST = (180, 160)
_GAP = 10                           # between two storms, across and down
_HIGHLIGHT = QColor(42, 130, 218)   # the theme's selection blue: the storm in the Picture box
_EDGE = QColor(85, 85, 85)          # a pop-up's edge and fill, as the "?" pop-up has them
_RAISED = QColor(45, 45, 45)
_HEADINGS = {"ir": "Infrared storms", "wv": "Water-vapor storms (always drawn from 0 to -90 °C)"}
_NONE_OFFERED = "There are no storm pictures to try this kind of table on."
_NONE_TICKED = "No storms are ticked. Press Storms… and tick the storms to see your table on."
_STORMS_TIPS = {
    True: "Choose the storms to show: tick them in the list that opens. What you tick is kept for next time.",
    False: _NONE_OFFERED,
}

Cell = collections.namedtuple("Cell", "sample source saved_shape stride")
Cell.__doc__ = """One storm of the grid as read: its bundled sample, a PreviewSource of its thinned
values alone (`source.full` is `source.values`: the saved picture is not kept), the saved
picture's (rows, columns), and the step it was thinned by."""

_Handover = collections.namedtuple("_Handover", "action path ids tables units name room")


# ------------------------------------------------------------------------ the storms offered

def offered(kind, samples):
    """[(kind, [samples])] a table of `kind` is tried on, as the list shows them: those of its
    own kind first, then those of each other kind it can be tried on
    (colortable_preview.PICTURE_KINDS), each in the manifest's order."""
    kinds = (kind, *[k for k in colortable_preview.PICTURE_KINDS.get(kind, ()) if k != kind])
    groups = [(k, [s for s in samples if s.kind == k]) for k in kinds]
    return [(k, group) for k, group in groups if group]


def default_ticked(kind, samples):
    """The storms ticked when nothing was chosen before: the first DEFAULT_TICKED of the
    table's own kind."""
    return [s.id for s in samples if s.kind == kind][:DEFAULT_TICKED]


def remembered_ticks(kind, samples):
    """The ids ticked for a table of `kind`, in the list's order: as they were last left
    (layout.json), those still offered -- none, if none were ticked -- else the default."""
    ids = [s.id for _kind, group in offered(kind, samples) for s in group]
    kept = window_layout.get(LAYOUT_RECORD).get(kind)
    if isinstance(kept, list) and all(isinstance(i, str) for i in kept):
        ticked = [i for i in ids if i in kept]
        if ticked or not kept:
            return ticked
    return default_ticked(kind, samples)


def remember_ticks(kind, ids):
    """Keep the storms ticked for a table of `kind` for next time (layout.json)."""
    return window_layout.update(LAYOUT_RECORD, {kind: list(ids)})


def drawing_range(sample, vmax_k, vmin_k):
    """(vmax, vmin) in K a table of that range is drawn at on `sample`: 0 to -90 °C on a
    water-vapor picture (PreviewSource.drawing_range; a bundled water-vapor sample's band
    always says so, load_bundled)."""
    if sample.kind == "wv":
        return colortable_preview.WV_VMAX_K, colortable_preview.WV_VMIN_K
    return vmax_k, vmin_k


def caption(sample):
    """The lines under a storm on the card: its title and its data line, as the editor
    shows them under the picture (share_card.caption_lines of load_bundled's source)."""
    note = "\n".join(p.strip() for p in (sample.description, sample.credit) if p.strip())
    return tuple(line.strip() for line in note.splitlines() if line.strip()) or (sample.label,)


def name_lines(sample):
    """The two lines under a storm in the grid: "Polo 2026" and "NOAA-20 IR" -- its label
    (colortable_preview's "Polo 2026 · NOAA-20 IR") at its dot, so it fits a narrow cell."""
    first, _dot, rest = sample.label.partition(" · ")
    return (first, rest) if rest else (first,)


def storm_words(sample):
    """"Polo 2026": the storm and its year, for the line under the grid."""
    return " ".join(str(v) for v in (sample.storm, sample.year) if v) or sample.label


def best_columns(count, width, height, caption_height, gap=_GAP):
    """How many storms across, of `count` in a room `width` x `height` with `caption_height`
    of name under each: as many as leaves each picture the most room (a storm is about as
    wide as it is tall) -- of two that leave the same, the fewer across."""
    count = max(1, count)
    best, most = 1, -math.inf
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        side = min((width - gap * (cols - 1)) / cols, (height - gap * (rows - 1)) / rows - caption_height)
        if side > most + 1e-9:
            best, most = cols, side
    return best


# ------------------------------------------------------------------------ reading (background thread)

def load_cell(sample, max_pixels=CELL_PIXELS):
    """One storm of the grid (Cell): the sample read whole, thinned to at most `max_pixels`
    (as PreviewSource thins), and only the thinned values kept -- the saved picture is let
    go before this returns."""
    whole = colortable_preview.load_bundled(sample, max_pixels=max_pixels)
    shown = colortable_preview.PreviewSource(whole.values, band=whole.band, label=whole.label, max_pixels=max_pixels,
                                             units=whole.units, note=whole.note)
    return Cell(sample, shown, whole.full_shape, whole.stride)


def card_values(sample, room):
    """The values a storm is drawn from on a card whose pictures get `room` pixels
    (share_card.picture_room): the saved picture read whole, then every step-th pixel across
    and down that share_card.fit takes of it -- or, no bigger than the room, the whole
    picture. On the card it comes out exactly as the whole picture would."""
    whole = colortable_preview.load_bundled(sample, max_pixels=CELL_PIXELS)
    step, _factor = share_card.fit(whole.full_shape, room)
    if step == 1:
        return whole.full
    return whole.full[::step, ::step].copy()


def reason_words(error):
    """Why a sample couldn't be read, in a few plain words."""
    if isinstance(error, FileNotFoundError):
        return "its file is missing"
    if isinstance(error, PermissionError):
        return "its file can't be read"
    if isinstance(error, MemoryError):
        return "there isn't enough memory free to read it"
    return "its file can't be read and may be damaged"


def _job(number, task, sample, room):
    """Runs on a background thread: one sample read, for the grid ("cell": load_cell) or for
    the grid as one picture ("card": card_values). (number, task, its id, room, what was read
    or None, why it couldn't be or None)."""
    try:
        value = load_cell(sample) if task == "cell" else card_values(sample, room)
    except Exception as e:  # noqa: BLE001 -- a sample that can't be read is left out, whatever it raised
        return number, task, sample.id, room, None, reason_words(e)
    return number, task, sample.id, room, value, None


# ------------------------------------------------------------------------ one storm on screen

class _CellPane(PicturePane):
    """A storm's picture in the grid: the editor's picture pane, answering the mouse as the
    one picture does, but always the whole picture -- the grid's storms always fit, so the
    wheel zooms nothing -- with a double-click of its own (`double_clicked`), and a frame in
    the theme's blue while it is the storm in the editor's Picture box (`current`)."""

    double_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, interactive=True)
        self.current = False
        # the grid gives each storm its room (_GridArea): it asks for none of its own
        self.setMinimumSize(0, 0)

    def set_current(self, current):
        if current != self.current:
            self.current = current
            self.update()

    def wheelEvent(self, event):
        event.ignore()

    def mouseDoubleClickEvent(self, event):
        # the press before it was a click, and chose a stop; the second press is no click
        if event.button() == Qt.MouseButton.LeftButton and not self.message and self.picture_rect() is not None:
            event.accept()
            self.double_clicked.emit()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        target = None if self.message else self.picture_rect()
        if self.current and target is not None:
            painter = QPainter(self)
            painter.setPen(QPen(_HIGHLIGHT, 3))
            painter.drawRect(target.adjusted(1.5, 1.5, -1.5, -1.5))
            painter.end()


class _StormCell(QWidget):
    """One storm in the grid: its picture (_CellPane) as big as fits, its name right under
    it on two lines (name_lines), each cut short with "…" when the cell is too narrow for it
    -- the storm's title in the name's tooltip -- the two together in the middle of the
    cell. A double-click on either is `opened`."""

    opened = Signal()

    def __init__(self, sample, parent=None):
        super().__init__(parent)
        self.sample = sample
        self.pane = _CellPane(self)
        self.pane.double_clicked.connect(self.opened)
        self.caption = QLabel(self)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.lines = name_lines(sample)
        self.caption.setToolTip(f"{sample.description or sample.label}\n"
                                "Double-click the picture to see this storm on its own.")
        self.place()

    def caption_height(self):
        return caption_height(self)

    def place(self):
        """The picture and its name laid out again: for the cell's size, and for the
        picture's shape once it has one (before that, its few words fill the room)."""
        w, h = self.width(), self.height()
        below = min(h, self.caption_height())
        room = h - below
        shape = None if self.pane.message else self.pane.full_shape
        if shape and w > 0 and room > 0:
            # the picture's own shape, so its name sits right under it, not under a bar
            scale = min(w / shape[1], room / shape[0])
            pw, ph = max(1, round(shape[1] * scale)), max(1, round(shape[0] * scale))
        else:
            pw, ph = w, room
        top = (h - ph - below) // 2
        self.pane.setGeometry((w - pw) // 2, top, pw, ph)
        self.caption.setGeometry(0, top + ph, w, below)
        metrics = self.caption.fontMetrics()
        self.caption.setText("\n".join(metrics.elidedText(line, Qt.TextElideMode.ElideRight, max(0, w))
                                       for line in self.lines))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.place()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            self.opened.emit()
            return
        super().mouseDoubleClickEvent(event)


def caption_height(widget):
    """The room the two lines of a storm's name take under it, in `widget`'s letters."""
    return 2 * widget.fontMetrics().lineSpacing() + 4


class _GridArea(QWidget):
    """The storms ticked, all the same size, as many across as best_columns says, in the
    list's order; a few words in the middle while there are none. It asks the window for
    no more than AREA_LEAST: the storms share what room it gets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cells = []
        self.columns = 1
        self.message = QLabel("", self)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.message.hide()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def minimumSizeHint(self):
        return QSize(*AREA_LEAST)

    def sizeHint(self):
        return QSize(640, 440)

    def set_cells(self, cells, message=""):
        """Show `cells` (_StormCell each), in that order -- or, with none, `message`."""
        for cell in self.cells:
            if cell not in cells:
                cell.hide()
        self.cells = list(cells)
        self.message.setText(message)
        self.message.setVisible(not self.cells)
        self.place()
        for cell in self.cells:
            cell.show()

    def place(self):
        """Lay the storms out again in the room there is now."""
        w, h = self.width(), self.height()
        self.message.setGeometry(12, 12, max(0, w - 24), max(0, h - 24))
        count = len(self.cells)
        if not count:
            return
        below = caption_height(self)
        cols = self.columns = best_columns(count, w, h, below)
        rows = math.ceil(count / cols)
        across = max(0.0, (w - _GAP * (cols - 1)) / cols)
        # each row as tall as a picture as wide as a column (or as fits) and its name, the
        # rows together in the middle: room to spare goes above and below the whole grid,
        # not between its rows
        down = max(0.0, min((h - _GAP * (rows - 1)) / rows, across + below))
        top0 = max(0.0, (h - rows * down - _GAP * (rows - 1)) / 2)
        for i, cell in enumerate(self.cells):
            row, col = divmod(i, cols)
            left, top = round(col * (across + _GAP)), round(top0 + row * (down + _GAP))
            right, bottom = round(col * (across + _GAP) + across), round(top0 + row * (down + _GAP) + down)
            cell.setGeometry(left, top, right - left, bottom - top)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.place()


class _StormsPopup(QFrame):
    """Storms…: every storm the grid can show, a box to tick for each under a heading for
    its kind, in a pop-up that stays until clicked away (as the "?" pop-up does). A tick
    shows or hides that storm in the grid at once. As tall as the list, or, longer than
    most of the screen, a list to scroll."""

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("storms_popup")
        self.setStyleSheet(f"QFrame#storms_popup {{ background-color: {_RAISED.name()}; "
                           f"border: 1px solid {_EDGE.name()}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        self.intro = QLabel("Tick the storms to see your table on:")
        layout.addWidget(self.intro)
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.list_widget)
        layout.addWidget(self.scroll)

    def fit_list(self):
        """The list as tall as it is, but no taller than most of the screen (it scrolls then)."""
        self.list_layout.activate()
        hint = self.list_widget.sizeHint()
        room = colortable_widgets.screen_room(self)
        most = int(room[1] * 0.7) if room else 600
        bar = self.scroll.verticalScrollBar().sizeHint().width() if hint.height() > most else 0
        self.scroll.setFixedSize(max(hint.width(), self.intro.sizeHint().width()) + bar, min(hint.height(), most))


# ------------------------------------------------------------------------ the grid

class StormGrid(QWidget):
    def __init__(self, editor, controls_width=150, parent=None):
        """`editor` the ColortableEditorDialog it is part of, whose table it draws;
        `controls_width` how wide its column of buttons is (as wide as the one picture's)."""
        super().__init__(parent)
        self.editor = editor
        self._kind = None
        self._known = {}                # id -> every sample offered so far
        self._groups = []               # offered(...) for the table's kind
        self._ticked = []               # ids ticked, in the list's order
        self._cells = {}                # id -> Cell read, kept while the editor lasts
        self._failed = {}               # id -> why it couldn't be read
        self._card_values = {}          # (id, room) -> the values it is drawn from on a card
        self._queue = []                # (task, id, room) waiting to be read, one at a time
        self._running = None            # (job number, task, id, room) being read now
        self._jobs = itertools.count(1)
        self._handover = None           # what Copy grid, Save grid… or Share card… waits for
        self._widgets = {}              # id -> _StormCell, while ticked
        self.checks = {}                # id -> its box in Storms…
        self._lut = (None, None)        # (the editor's table tuple, its colors)
        self._note = ""                 # the last thing said under the grid, under what is being read
        self._left_now = []             # the storms the grid being handed over lost
        self.card = None                # the grid as last copied or saved (a PIL image)
        self.last_handover = None       # what it was made of (_Handover: its storms, tables, room)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REDRAW_MS)
        self._timer.timeout.connect(self.redraw)
        self._build_ui(controls_width)
        editor.drawn.connect(self._schedule)

    def _build_ui(self, controls_width):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        body = QHBoxLayout()
        self.area = _GridArea()
        body.addWidget(self.area, 1)
        self.controls = QWidget()
        controls = QVBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self.storms_btn = self._button("Storms…", _STORMS_TIPS[True], self.open_storms)
        controls.addWidget(self.storms_btn)
        # Storms… at the top, the copy buttons at the bottom, as the one picture's zoom and copy buttons stand
        controls.addItem(QSpacerItem(0, 12, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding))
        self.copy_btn = self._button(
            "Copy grid", "Copy the storms shown, drawn in this table with its color scale, as one picture ready to "
            "paste into a message.", self.copy_grid)
        self.save_btn = self._button(
            "Save grid…", "Save the storms shown, drawn in this table with its color scale, as one PNG picture.",
            self.save_grid)
        self.share_btn = self._button(
            "Share card…", "Make a card of the grid to post: a title, the color scale and the storms shown, each "
            "with its title and data line.", self.open_share_card)
        for button in (self.copy_btn, self.save_btn, self.share_btn):
            controls.addWidget(button)
        self.controls.setFixedWidth(controls_width)
        body.addWidget(self.controls)
        layout.addLayout(body, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.hide()              # shown while it has something to say (_show_status)
        layout.addWidget(self.status)
        self.storms_popup = _StormsPopup(self)

    def _button(self, text, tip, slot):
        button = QPushButton(text)
        button.setToolTip(tip)
        # a click leaves the keyboard where it was, so the arrow keys still move the stop
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _checked=False: slot())
        return button

    def on_show(self):
        """Whether the grid is what the editor shows (its Several storms view)."""
        return self.editor.view() == "several"

    def activate(self):
        """The editor's Several storms: the storms the table's kind is tried on, read if
        they aren't yet, and drawn with the table as it is now."""
        if self._kind != self.editor.model.kind:
            self._follow_kind()
        else:
            for sid in self._ticked:
                self._want("cell", sid)
            self._pump()
        self.redraw()

    # ------------------------------------------------------------------ Storms…

    def _follow_kind(self):
        """The storms the table's kind is tried on, ticked as last left for that kind."""
        if self._kind is not None:
            self._note = ""             # what was said was about the other kind's storms
        self._kind = self.editor.model.kind
        samples = self.editor.grid_samples()
        self._known.update((s.id, s) for s in samples)
        self._groups = offered(self._kind, samples)
        self._ticked = remembered_ticks(self._kind, samples)
        self._fill_list()
        self._arrange()
        for sid in self._ticked:
            self._want("cell", sid)
        self._pump()

    def _fill_list(self):
        layout = self.storms_popup.list_layout
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                # off the list at once: deleteLater alone leaves it showing until the window
                # next has nothing to do (and a box may be in the middle of saying it toggled)
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.checks = {}
        for kind, group in self._groups:
            if len(self._groups) > 1:
                heading = QLabel(_HEADINGS[kind])
                font = heading.font()
                font.setBold(True)
                heading.setFont(font)
                layout.addWidget(heading)
            for sample in group:
                box = QCheckBox(sample.label)
                box.setChecked(sample.id in self._ticked)
                box.toggled.connect(self._on_tick)
                layout.addWidget(box)
                self.checks[sample.id] = box
            layout.addSpacing(8)
        if not self._groups:
            layout.addWidget(QLabel(_NONE_OFFERED))
        layout.addStretch()
        self.storms_btn.setEnabled(bool(self._groups))
        self.storms_btn.setToolTip(_STORMS_TIPS[bool(self._groups)])
        self._show_failed()

    def _show_failed(self):
        """The storms that couldn't be read say so in the list."""
        for sid, box in self.checks.items():
            sample = self._known[sid]
            if sid in self._failed:
                box.setText(f"{sample.label} (can't be opened)")
                box.setToolTip(f"This storm couldn't be opened: {self._failed[sid]}.")
            else:
                box.setText(sample.label)
                box.setToolTip(sample.description or sample.label)
        if self.storms_popup.isVisible():
            self.storms_popup.fit_list()
            self.storms_popup.adjustSize()

    def open_storms(self):
        """Storms…: the checklist, in a pop-up under the button."""
        if self._kind != self.editor.model.kind:
            self._follow_kind()
        self.storms_popup.fit_list()
        colortable_widgets.show_popup_under(self.storms_popup, self.storms_btn)
        return self.storms_popup

    def _on_tick(self, _on):
        self._ticked = [sid for sid, box in self.checks.items() if box.isChecked()]
        remember_ticks(self._kind, self._ticked)
        # a storm unticked keeps its values (ticked again, it is drawn at once), not its rows
        # nor its picture on screen
        for sid in [sid for sid in self._widgets if sid not in self._ticked]:
            widget = self._widgets.pop(sid)
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        for sid, cell in self._cells.items():
            if sid not in self._ticked:
                cell.source.forget_rows()
        self._arrange()
        for sid in self._ticked:
            self._want("cell", sid)
        self._pump()
        self.redraw()

    def ticked(self):
        """The ids of the storms ticked, in the list's order."""
        return list(self._ticked)

    def set_ticked(self, ids):
        """Tick exactly the storms `ids` (as clicking their boxes does)."""
        if self._kind != self.editor.model.kind:
            self._follow_kind()
        for sid, box in self.checks.items():
            box.blockSignals(True)
            box.setChecked(sid in ids)
            box.blockSignals(False)
        self._on_tick(True)

    # ------------------------------------------------------------------ the grid

    def shown_ids(self):
        """The storms in the grid: those ticked, but any that couldn't be read."""
        return [sid for sid in self._ticked if sid not in self._failed]

    def columns(self):
        """How many storms across, as they are laid out now."""
        return self.area.columns

    def _arrange(self):
        """The grid laid out again for the storms ticked."""
        cells = []
        for sid in self.shown_ids():
            widget = self._widgets.get(sid)
            if widget is None:
                widget = self._widgets[sid] = _StormCell(self._known[sid], self.area)
                widget.pane.hovered.connect(functools.partial(self._on_hover, sid))
                widget.pane.clicked.connect(functools.partial(self._on_click, sid))
                widget.opened.connect(functools.partial(self.open_storm, sid))
            cells.append(widget)
        self.area.set_cells(cells, "" if cells else _NONE_TICKED if self._groups else _NONE_OFFERED)
        self._show_buttons()

    def cell(self, sid):
        """The storm `sid` on screen (_StormCell: its `pane` and `caption`), or None."""
        return self._widgets.get(sid)

    def _schedule(self):
        """The editor drew: the grid follows within REDRAW_MS -- once for a burst of edits,
        and every REDRAW_MS while a drag goes on. Not while the editor shows one storm."""
        if self.on_show() and not self._timer.isActive():
            self._timer.start()

    def redraw(self):
        """Draw every storm in the grid with the table as it is now: the rows of each storm
        for its range (worked out once per range) and the table's colors, looked up when
        it is next painted."""
        self._timer.stop()
        if self.editor.model.kind != self._kind:
            self._follow_kind()
        table = self.editor.drawn_table()
        if table is not None and self._lut[0] is not table:
            self._lut = (table, colortable_preview.lut_for(table[0]))
        current = self.editor._picture_choice
        running = self._running[2] if self._running and self._running[1] == "cell" else None
        for sid in self.shown_ids():
            widget = self._widgets[sid]
            pane = widget.pane
            was = (pane.message, pane.full_shape)
            pane.set_current(current == f"bundled:{sid}")
            cell = self._cells.get(sid)
            if cell is None:
                pane.set_message("Loading…" if running == sid else "Waiting to load…")
            elif table is None:
                pane.set_message("This table can't be drawn yet.")
            else:
                cmap, vmax_k, vmin_k = table
                vmax_d, vmin_d = cell.source.drawing_range(vmax_k, vmin_k)
                if pane.message:
                    pane.set_message("")
                pane.set_index_image(cell.source.rows(Normalize(vmin=vmin_d, vmax=vmax_d), cmap.N),
                                     stride=cell.stride, full_shape=cell.saved_shape)
                pane.set_lut(self._lut[1])
            if (pane.message, pane.full_shape) != was:
                widget.place()          # words in the picture's place, or a picture of another shape
        self._show_buttons()

    # ------------------------------------------------------------------ the mouse on a storm

    def spot(self, sid, row, col):
        """What storm `sid` shows at its saved picture's pixel (row, col), as the editor's
        picture_spot says it of the one picture (a PictureSpot, or None): the pixel the
        thinned one shown was kept from (PreviewSource.saved_pixel), whose value the
        thinned values hold, at the range the storm is drawn at -- 0 to -90 °C on a
        water-vapor storm, whatever the table's."""
        cell = self._cells.get(sid)
        if cell is None:
            return None
        raw = cell.source.saved_value(row // cell.stride, col // cell.stride)
        return self.editor.spot_for(raw, cell.source)

    def _on_hover(self, sid, pixel):
        self.editor.show_spot(None if pixel is None else self.spot(sid, *pixel))

    def _on_click(self, sid, row, col, add):
        self.editor.pick_spot(self.spot(sid, row, col), add=add)

    def open_storm(self, sid):
        """Show storm `sid` on its own: the editor's One storm, with it in the Picture box (a
        double-click on it). Whether it is shown."""
        shown = self.editor.open_storm(sid)
        sample = self._known.get(sid)
        if not shown and sample is not None:
            self._say(f"{sample.label} couldn't be opened on its own.")
        return shown

    # ------------------------------------------------------------------ reading, one at a time

    def _want(self, task, sid, room=None):
        key = (task, sid, room)
        if key in self._queue or (self._running is not None and self._running[1:] == key):
            return
        self._queue.append(key)

    def _needed(self, task, sid, room):
        if sid in self._failed or sid not in self._known:
            return False
        if task == "cell":
            return sid in self._ticked and sid not in self._cells
        handover = self._handover
        return (handover is not None and room == handover.room and sid in handover.ids
                and (sid, room) not in self._card_values)

    def _pump(self):
        """Start reading the next storm wanted, when nothing is being read."""
        if self._running is None:
            while self._queue:
                task, sid, room = self._queue.pop(0)
                if not self._needed(task, sid, room):
                    continue
                number = next(self._jobs)
                self._running = (number, task, sid, room)
                run_in_background(_job, number, task, self._known[sid], room, on_finished=self._on_read,
                                  on_failed=self._on_read_failed)
                break
        self._show_status()

    def busy(self):
        """Whether a storm is being read, or waits to be."""
        return self._running is not None or any(self._needed(*key) for key in self._queue)

    def _on_read(self, result):
        number, task, sid, room, value, why = result
        if self._running is None or self._running[0] != number:
            return
        self._running = None
        if why is not None:
            self._left_out(sid, why)
        elif task == "cell":
            self._cells[sid] = value
        else:
            self._card_values[(sid, room)] = value
        self._pump()
        self.redraw()
        self._check_handover()

    def _on_read_failed(self, message):
        if self._running is None:
            return
        sid = self._running[2]
        self._running = None
        self._left_out(sid, "something went wrong reading it")
        self._pump()
        self.redraw()
        self._check_handover()

    def _left_out(self, sid, why):
        """A storm that couldn't be read: out of the grid, and said so."""
        self._failed[sid] = why
        sample = self._known[sid]
        self._note = f"{sample.label} couldn't be opened ({why}), so it is left out of the grid."
        self._show_status()
        self._show_failed()
        widget = self._widgets.pop(sid, None)
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        self._arrange()
        if self._handover is not None:
            # said again once the grid is handed over, which says something else
            self._left_now.append(sample.label)
            self._plan()

    def _left_words(self):
        """" Left out, as it couldn't be opened: …" for what the grid being handed over lost, or ""."""
        if not self._left_now:
            return ""
        what = "it" if len(self._left_now) == 1 else "they"
        return f" Left out, as {what} couldn't be opened: {', '.join(self._left_now)}."

    # ------------------------------------------------------------------ what is said

    def _say(self, text):
        """A sentence about what a button did, in the editor's status line (as the one
        picture's Copy picture says how it went)."""
        self.editor.say(text)

    def _show_status(self):
        """The line under the grid: what is being read, and what was left out."""
        lines = []
        if self._running is not None:
            _number, task, sid, _room = self._running
            sample = self._known[sid]
            if task == "cell":
                lines.append(f"Loading {storm_words(sample)}…")
            else:
                handover = self._handover
                at = handover.ids.index(sid) + 1 if handover is not None and sid in handover.ids else 1
                count = len(handover.ids) if handover is not None else 1
                lines.append(f"Drawing the grid: {storm_words(sample)} ({at} of {count})…")
        if self._note:
            lines.append(self._note)
        self.status.setText("\n".join(lines))
        self.status.setVisible(bool(lines))

    def _show_buttons(self):
        ready = self.editor.drawn_table() is not None and bool(self.shown_ids()) and self._handover is None
        for button in (self.copy_btn, self.save_btn, self.share_btn):
            button.setEnabled(ready)

    # ------------------------------------------------------------------ the grid as one picture

    def copy_grid(self):
        """Copy grid: the grid as one picture (share_card) onto the clipboard, once its
        storms are read. Whether it was started (the status line says how it went)."""
        return self._hand_over("copy")

    def save_grid(self):
        """Save grid…: the grid as one PNG picture where the person says. Whether it was
        started."""
        path = self.editor._ask_save_path(str(self.suggested_file()))
        if not path:
            return False
        path = Path(path)
        if path.suffix.lower() != ".png":
            path = path.with_name(path.name + ".png")
        return self._hand_over("save", path)

    def open_share_card(self):
        """Share card…: the editor's Share card window with the grid on the card. Whether it
        was started."""
        return self._hand_over("share")

    def suggested_file(self):
        """The file Save grid… offers: the table's name and "storms", in the folder the
        editor's Save picture… offers."""
        folder = Path(self.editor._suggested_file()).parent
        stem = "".join(c if c.isalnum() or c in "-." else "_" for c in self.editor.table_name()).strip("._")
        return folder / f"{stem or 'colortable'}_storms.png"

    def _hand_over(self, action, path=None):
        verb = {"copy": "copy", "save": "save", "share": "make a card of"}[action]
        if self._handover is not None:
            self._say("The grid is still being drawn. Try again once it is done.")
            return False
        table = self.editor.drawn_table()
        if table is None:
            self._say(f"This table can't be drawn yet, so there is no grid to {verb}.")
            return False
        ids = self.shown_ids()
        if not ids:
            self._say(f"Tick a storm under Storms… first: there is no grid to {verb}.")
            return False
        cmap, vmax_k, vmin_k = table
        # the table as it is now: further edits while the storms are read don't change it
        tables = self.card_tables(cmap.copy(), vmax_k, vmin_k, self.editor.table_name(), ids)
        self._handover = _Handover(action, path, ids, tables, self.editor.model.units, self.editor.table_name(), None)
        self._left_now = []
        self._plan()
        return True

    def card_tables(self, cmap, vmax_k, vmin_k, name, ids):
        """{id: (PictureTable,)} each storm is drawn in on the card: the table at the range
        that storm is drawn at -- one PictureTable for each range, so storms drawn alike
        share a scale on the card. The water-vapor storms of an infrared table carry the
        range in their name."""
        made, out = {}, {}
        for sid in ids:
            vmax_d, vmin_d = drawing_range(self._known[sid], vmax_k, vmin_k)
            if (vmax_d, vmin_d) not in made:
                label = name if (vmax_d, vmin_d) == (vmax_k, vmin_k) else f"{name} (0 to -90 °C)"
                made[(vmax_d, vmin_d)] = (colortable_preview.PictureTable(
                    cmap, Normalize(vmin=vmin_d, vmax=vmax_d), vmax_d, vmin_d, label),)
            out[sid] = made[(vmax_d, vmin_d)]
        return out

    def _plan(self):
        """What the grid as one picture still needs read, for the storms that can be: the
        room each gets depends on how many there are, so a storm left out means the others
        are read again for theirs."""
        handover = self._handover
        ids = [sid for sid in handover.ids if sid not in self._failed]
        if not ids:
            self._handover = None
            self._say("None of the storms ticked could be opened, so there is no grid.")
            self._show_buttons()
            return
        room = share_card.picture_room([share_card.CardPicture(None, handover.tables[sid]) for sid in ids],
                                       handover.units)
        self._handover = handover._replace(ids=ids, room=room)
        # what was read for another room (other storms ticked then) is let go: kept, it would
        # pile up a set of storms for every grid handed over
        self._card_values = {key: values for key, values in self._card_values.items() if key[1] == room}
        for sid in ids:
            if (sid, room) not in self._card_values:
                self._want("card", sid, room)
        self._show_buttons()
        self._pump()
        self._check_handover()

    def card_pictures(self, handover):
        return [share_card.CardPicture(self._card_values[(sid, handover.room)], handover.tables[sid],
                                       caption(self._known[sid])) for sid in handover.ids]

    def _check_handover(self):
        """Every storm of the grid read: hand it over."""
        # the editor's module, which imports this one: its save folder and its helpers
        from tcviz_gui.pages import colortable_editor_dialog as editor_module
        handover = self._handover
        if handover is None or handover.room is None:
            return
        if any((sid, handover.room) not in self._card_values for sid in handover.ids):
            return
        self._handover = None
        self.last_handover = handover
        self._show_buttons()
        self._show_status()
        pictures = self.card_pictures(handover)
        if handover.action == "share":
            from tcviz_gui.pages.share_card_dialog import ShareCardDialog
            window = ShareCardDialog(self.editor, pictures, handover.name, handover.units)
            if self._left_now:
                self._say(self._left_words().strip())
            self.editor._run(window)
            return
        with editor_module._busy():
            self.card = card = share_card.share_card(pictures, "", "", units=handover.units)
            words = f"{card.width} × {card.height} pixels"
            if handover.action == "copy":
                taken = self.editor._put_on_clipboard(editor_module._qimage(card))
                if taken:
                    self._say(f"Copied the grid, {words}. Paste it before you close {edition.app_name()}."
                              + self._left_words())
                else:
                    self._say("The grid couldn't be put on the clipboard. Use Save grid… to keep it as a file "
                              "instead.")
                return
            try:
                card.save(handover.path, "PNG")
            except PermissionError:
                self._say("The grid couldn't be saved there: a file of that name is open in another program, or "
                          "the folder can't be written to. Nothing was saved.")
                return
            except OSError as e:
                self._say(f"The grid couldn't be saved there ({e.strerror or e}). Nothing was saved.")
                return
        editor_module._last_save_folder = handover.path.parent
        self._say(f"Saved the grid, {words}, as {handover.path}." + self._left_words())
