"""The storm grid: the table being edited, tried on several storms at once -- the window the
color table editor's "Other storms…" button opens (ColortableEditorDialog.open_storm_grid).

On the left, the bundled samples (colortable_preview.bundled_samples) the table can be tried
on, each with a box to tick: those of the table's own kind first, then those of the other
kind it can be tried on, unticked at first -- an infrared table on the water-vapor storms,
which are drawn at 0 to -90 °C whatever the table's range, as the editor's Picture box
draws them (colortable_preview.PICTURE_KINDS, PreviewSource.drawing_range). With nothing
chosen before, the first six of the table's own kind are ticked; what is ticked is
remembered for each kind of table, with the window's size and place, in layout.json beside
the tables (tcviz.window_layout, under "storm_grid": {"ir": [ids], "wv": [ids], "size": [w,
h], "position": [x, y]}).

On the right, the storms ticked, each a small picture drawn with the table being edited and
its label under it. They follow every edit: the editor says each time it has drawn (its
`drawn` signal), and the grid draws itself again at most every REDRAW_MS, so dragging a
stop stays smooth in the editor's own picture. A click on one shows it in the editor.

The pictures are drawn as the editor draws its own (colortable_preview): the table row of
each pixel is worked out once per range (PreviewSource.rows), and an edit is one lookup of
the table's colors -- so each pixel is exactly the color tcviz gives it. Each storm is read
on a background thread (tcviz_gui.worker), one at a time, "Loading Polo 2026…" in the line
under the grid -- a 20 MB VIIRS sample takes about a second to unpack -- and only its
thinned values are kept (load_cell: at most CELL_PIXELS, about 500 x 500 or 1 MB, where
the VIIRS picture itself is 60 MB). What was read stays while the window does -- closing
hides it until the editor closes -- so ticking a storm again draws it at once.

Copy grid, Save grid… and Share card… make one picture of the grid with tcviz.share_card:
the storms ticked, drawn in the table with its color scale beside them, each with its
title and data line under it, about 1,900 pixels across. Those are drawn from the saved
pictures themselves, every pixel exact: read again one at a time on the background thread
and thinned at once to the size the card shows them at (card_values: share_card.picture_room
and share_card.fit), so no more than one whole picture is ever held. Copy grid and Save
grid… hand the grid over as the editor's Copy picture and Save picture do; Share card…
opens the editor's Share card window with it, its title to change.

A storm whose file is missing or can't be read is left out, and the line under the grid
says so in plain words.
"""
import collections
import contextlib
import functools
import itertools
from pathlib import Path

from matplotlib.colors import Normalize
from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from tcviz import colortable_preview, edition, share_card, window_layout
from tcviz_gui import colortable_widgets
from tcviz_gui.pages import colortable_editor_dialog as editor_module
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
_DEFAULT_SIZE = (1180, 780)
_CELL_LEAST = 150                 # a storm's picture, at the least, across and down
_PLATE = QColor(*share_card.PLATE)
_HIGHLIGHT = QColor(42, 130, 218)  # the theme's selection blue: the storm the editor shows
_TEXT = QColor(220, 220, 220)
_HEADINGS = {"ir": "Infrared storms", "wv": "Water-vapor storms (always drawn from 0 to -90 °C)"}
_NONE_OFFERED = "There are no storm pictures to try this kind of table on."
_NONE_TICKED = "Tick a storm on the left to see the table on it."

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


def storm_words(sample):
    """"Polo 2026": the storm and its year, for the line under the grid."""
    return " ".join(str(v) for v in (sample.storm, sample.year) if v) or sample.label


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

class _CellPicture(QWidget):
    """A storm's picture, as big as fits, in the middle: a picture drawn smaller than it is
    smoothed, an enlarged one pixel for pixel, as the editor's picture is. A click on it is
    `clicked`; before it is drawn, a few words instead."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rgba = None                # the RGBA bytes on show (the QImage is drawn from them)
        self.image = None
        self.message = ""
        self.current = False            # the storm the editor shows
        self.drawn_for = None           # the editor's table tuple it was drawn with
        self._pressed = False
        self.setMinimumSize(_CELL_LEAST, _CELL_LEAST)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self):
        return QSize(300, 300)

    def set_rgba(self, rgba, drawn_for):
        h, w = rgba.shape[:2]
        self.rgba = rgba
        self.image = QImage(rgba.data, w, h, 4 * w, QImage.Format.Format_RGBA8888)
        self.message = ""
        self.drawn_for = drawn_for
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update()

    def set_message(self, text):
        self.rgba = self.image = self.drawn_for = None
        self.message = text
        self.unsetCursor()
        self.update()

    def picture_rect(self):
        """Where the picture is drawn: as big as fits, in the middle (None without one)."""
        if self.image is None:
            return None
        w, h = self.image.width(), self.image.height()
        scale = min(self.width() / w, self.height() / h)
        return QRectF((self.width() - w * scale) / 2, (self.height() - h * scale) / 2, w * scale, h * scale)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), _PLATE)
        target = self.picture_rect()
        if target is None:
            painter.setPen(_TEXT)
            painter.drawText(QRectF(self.rect()).adjusted(8, 8, -8, -8),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            painter.end()
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, target.width() < self.image.width())
        painter.drawImage(target, self.image)
        if self.current:
            painter.setPen(QPen(_HIGHLIGHT, 3))
            painter.drawRect(target.adjusted(1.5, 1.5, -1.5, -1.5))
        painter.end()

    def mousePressEvent(self, event):
        self._pressed = event.button() == Qt.MouseButton.LeftButton and self.image is not None
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._pressed and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        self._pressed = False
        event.accept()


class _StormCell(QWidget):
    """One storm in the grid: its picture, its label under it."""

    def __init__(self, sample, parent=None):
        super().__init__(parent)
        self.sample = sample
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.picture = _CellPicture(self)
        layout.addWidget(self.picture, 1)
        self.caption = QLabel(sample.label)
        self.caption.setWordWrap(True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.caption)
        title = sample.description or sample.label
        self.setToolTip(f"{title}\nClick to open this storm in the editor.")


# ------------------------------------------------------------------------ the window

class StormGridDialog(QDialog):
    def __init__(self, editor):
        """`editor` the ColortableEditorDialog it is opened from, whose table it draws."""
        super().__init__(editor)
        self.editor = editor
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self._kind = None
        self._known = {}                # id -> every sample offered so far
        self._groups = []               # offered(...) for the table's kind
        self._ticked = []               # ids ticked, in the list's order
        self._cells = {}                # id -> Cell read, kept while the window lasts
        self._failed = {}               # id -> why it couldn't be read
        self._card_values = {}          # (id, room) -> the values it is drawn from on a card
        self._queue = []                # (task, id, room) waiting to be read, one at a time
        self._running = None            # (job number, task, id, room) being read now
        self._jobs = itertools.count(1)
        self._handover = None           # what Copy grid, Save grid… or Share card… waits for
        self._widgets = {}              # id -> _StormCell
        self.checks = {}                # id -> its box in the list
        self._lut = (None, None)        # (the editor's table tuple, its colors)
        self._note = ""                 # the last thing said, under what is being read
        self._left_now = []             # the storms the grid being handed over lost
        self._layout_shown = False
        self.card = None                # the grid as last copied or saved (a PIL image)
        self.last_handover = None       # what it was made of (_Handover: its storms, tables, room)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REDRAW_MS)
        self._timer.timeout.connect(self.redraw)
        self._build_ui()
        self._follow_kind()
        editor.drawn.connect(self._schedule)
        self._restore_layout()
        self.redraw()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        intro = QLabel("Your table on several storms at once, as you edit it. Tick the storms to show; click one to "
                       "open it in the editor.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        body = QHBoxLayout()
        self.list_widget = QWidget()
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(4, 4, 4, 4)
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_scroll.setWidget(self.list_widget)
        self.list_scroll.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        body.addWidget(self.list_scroll)

        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(12)
        self.empty_label = QLabel("")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        grid_scroll.setWidget(self.grid_widget)
        body.addWidget(grid_scroll, 1)
        layout.addLayout(body, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.copy_btn = QPushButton("Copy grid")
        self.copy_btn.setToolTip("Copy the storms ticked, drawn in this table with its color scale, as one picture "
                                 "ready to paste into a message.")
        self.copy_btn.clicked.connect(lambda _checked=False: self.copy_grid())
        self.save_btn = QPushButton("Save grid…")
        self.save_btn.setToolTip("Save the storms ticked, drawn in this table with its color scale, as one PNG "
                                 "picture.")
        self.save_btn.clicked.connect(lambda _checked=False: self.save_grid())
        self.share_btn = QPushButton("Share card…")
        self.share_btn.setToolTip("Make a card of the grid to post: a title, the color scale and the storms ticked, "
                                  "each with its title and data line.")
        self.share_btn.clicked.connect(lambda _checked=False: self.open_share_card())
        close = QPushButton("Close")
        close.setToolTip("Close this window. The editor stays open, and the storms stay ticked.")
        # no button is the window's own: Enter in the list hands nothing over
        for button in (self.copy_btn, self.save_btn, self.share_btn, close):
            button.setAutoDefault(False)
        for button in (self.copy_btn, self.save_btn, self.share_btn):
            buttons.addWidget(button)
        buttons.addStretch()
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    # ------------------------------------------------------------------ the list

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
        while self.list_layout.count():
            widget = self.list_layout.takeAt(0).widget()
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
                heading.setWordWrap(True)
                font = heading.font()
                font.setBold(True)
                heading.setFont(font)
                self.list_layout.addWidget(heading)
            for sample in group:
                box = QCheckBox(sample.label)
                box.setToolTip(sample.description or sample.label)
                box.setChecked(sample.id in self._ticked)
                box.toggled.connect(self._on_tick)
                self.list_layout.addWidget(box)
                self.checks[sample.id] = box
            self.list_layout.addSpacing(8)
        if not self._groups:
            none = QLabel(_NONE_OFFERED)
            none.setWordWrap(True)
            self.list_layout.addWidget(none)
        self.list_layout.addStretch()
        self._show_failed()

    def _show_failed(self):
        """The storms that couldn't be read say so in the list, which is kept as wide as its
        widest storm's name, so none is cut short."""
        for sid, box in self.checks.items():
            sample = self._known[sid]
            if sid in self._failed:
                box.setText(f"{sample.label} (can't be opened)")
                box.setToolTip(f"This storm couldn't be opened: {self._failed[sid]}.")
            else:
                box.setText(sample.label)
                box.setToolTip(sample.description or sample.label)
        widest = max((box.sizeHint().width() for box in self.checks.values()), default=200)
        bar = self.list_scroll.verticalScrollBar().sizeHint().width()
        self.list_scroll.setFixedWidth(widest + bar + 2 * self.list_scroll.frameWidth() + 12)

    def _on_tick(self, _on):
        self._ticked = [sid for sid, box in self.checks.items() if box.isChecked()]
        remember_ticks(self._kind, self._ticked)
        # a storm unticked keeps its values (ticked again, it is drawn at once), not its rows
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
        """How many storms across: as the card lays them out (all of up to three in a row,
        then about as many across as down)."""
        return share_card._columns(max(1, len(self.shown_ids())))

    def _arrange(self):
        """The grid laid out again for the storms ticked."""
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
        for i in range(self.grid.columnCount()):
            self.grid.setColumnStretch(i, 0)
        for i in range(self.grid.rowCount()):
            self.grid.setRowStretch(i, 0)
        ids = self.shown_ids()
        if not ids:
            self.empty_label.setText(_NONE_TICKED if self._groups else _NONE_OFFERED)
            self.grid.addWidget(self.empty_label, 0, 0)
            self.empty_label.show()
            self._show_buttons()
            return
        cols = self.columns()
        for i, sid in enumerate(ids):
            widget = self._widgets.get(sid)
            if widget is None:
                widget = self._widgets[sid] = _StormCell(self._known[sid], self.grid_widget)
                widget.picture.clicked.connect(functools.partial(self.open_in_editor, sid))
            self.grid.addWidget(widget, i // cols, i % cols)
            widget.show()
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)
        for r in range((len(ids) + cols - 1) // cols):
            self.grid.setRowStretch(r, 1)
        self._show_buttons()

    def cell(self, sid):
        """The storm `sid` on screen (_StormCell), or None."""
        return self._widgets.get(sid)

    def _schedule(self):
        """The editor drew: the grid follows within REDRAW_MS -- once for a burst of edits,
        and every REDRAW_MS while a drag goes on."""
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def redraw(self):
        """Draw every storm in the grid with the table as it is now (a storm already drawn
        with it is left as it is)."""
        self._timer.stop()
        if self.editor.model.kind != self._kind:
            self._follow_kind()
        self.setWindowTitle(f"Other storms -- {self.editor.table_name()}")
        table = self.editor.drawn_table()
        current = self.editor._picture_choice
        for sid in self.shown_ids():
            picture = self._widgets[sid].picture
            picture.current = current == f"bundled:{sid}"
            cell = self._cells.get(sid)
            if cell is None:
                picture.set_message("Loading…" if self._running and self._running[2] == sid else "Waiting to load…")
            elif table is None:
                picture.set_message("This table can't be drawn yet.")
            elif picture.drawn_for is not table:
                picture.set_rgba(self.cell_rgba(cell, table), table)
            else:
                picture.update()
        self._show_buttons()

    def cell_rgba(self, cell, table):
        """RGBA bytes of a storm (Cell) through the table (the editor's drawn_table): its rows
        for the range it is drawn at, worked out once per range, then the table's colors
        looked up -- what render.render would color its thinned pixels."""
        cmap, vmax_k, vmin_k = table
        if self._lut[0] is not table:
            self._lut = (table, colortable_preview.lut_for(cmap))
        vmax_d, vmin_d = cell.source.drawing_range(vmax_k, vmin_k)
        rows = cell.source.rows(Normalize(vmin=vmin_d, vmax=vmax_d), cmap.N)
        return colortable_preview.apply(rows, self._lut[1])

    def open_in_editor(self, sid):
        """Show storm `sid` in the editor's Picture box. Whether it is shown."""
        shown = self.editor.show_sample(sid)
        sample = self._known.get(sid)
        if shown:
            self._say(f"{storm_words(sample)} is open in the editor.")
        elif sample is not None:
            self._say(f"{sample.label} couldn't be opened in the editor.")
        self.redraw()
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
        self._say(f"{sample.label} couldn't be opened ({why}), so it is left out of the grid.")
        self._show_failed()
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

    # ------------------------------------------------------------------ the line under the grid

    def _say(self, text):
        self._note = text
        self._show_status()

    def _show_status(self):
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

    def _show_buttons(self):
        ready = self.editor.drawn_table() is not None and bool(self.shown_ids()) and self._handover is None
        for button in (self.copy_btn, self.save_btn, self.share_btn):
            button.setEnabled(ready)

    # ------------------------------------------------------------------ the grid as one picture

    def copy_grid(self):
        """Copy grid: the grid as one picture (share_card) onto the clipboard, once its
        storms are read. Whether it was started (the line under the grid says how it went)."""
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
            self._say(f"Tick a storm first: there is no grid to {verb}.")
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

    # ------------------------------------------------------------------ the window's place, remembered

    def _restore_layout(self):
        """Open the size and in the place it was left (window_layout), fitted to the screen;
        the first time, at _DEFAULT_SIZE or as much of the screen as there is."""
        kept = window_layout.get(LAYOUT_RECORD)
        room = colortable_widgets.screen_room(self)
        self.resize(*(window_layout.fit_size(kept.get("size"), room) or window_layout.fit_size(_DEFAULT_SIZE, room)))
        position = kept.get("position")
        if (isinstance(position, list) and len(position) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) for v in position)
                and QGuiApplication.screenAt(QPoint(position[0] + 40, position[1] + 20)) is not None):
            self.move(*position)

    def _remember_layout(self):
        """Keep the window's size and place for next time (once it has been on screen)."""
        if self._layout_shown:
            window_layout.update(LAYOUT_RECORD, {"size": [self.width(), self.height()],
                                              "position": [self.x(), self.y()]})

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_shown = True
        # edits made while it was hidden
        self.redraw()

    def done(self, result):
        # closed, it is only hidden: the storms read stay for next time, while the editor is open
        with contextlib.suppress(Exception):
            self._remember_layout()
        self._timer.stop()
        super().done(result)
