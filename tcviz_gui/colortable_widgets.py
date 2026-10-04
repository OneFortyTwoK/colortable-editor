"""The pieces of the color table editor (pages/colortable_editor_dialog.py):

* StopBar -- the table standing up, warm (or high) at the top like the picture's color
  scale, with a handle for every stop: click the bar to add a stop, drag a handle to move
  it, double-click to go to its color, right-click for a hard step, deleting, or a color.
* StopListPanel -- the same stops as rows of numbers: the value, color, hex.
* ColorPanel -- the chosen stop's color, picked in place (a color square, a brightness
  strip, a hex box and lighter / darker / hue buttons); the picture follows as it is picked.
* ScalePanel -- the picture's own color scale for the table (picture_overlays.colorbar_preview,
  the scale render draws), so what is shown beside the bar is exactly what the picture will carry.
* PicturePane -- a real picture through the table, redrawn on every edit, that zooms
  (the mouse wheel, or the editor's buttons) and pans (a drag) -- down to every pixel of
  the saved picture; the editor's own one says which pixel is under the pointer and which
  one is clicked. A PictureView holds the zoom, so two panes can share one. Swiping, it
  shows a second table right of a line you drag across it, each side exactly its table.
* PanelSplitter -- panels side by side with a handle to drag between them: the color panel
  folds away to give the picture its room, and comes back.
* HelpPopup -- the editor's "?": a few paragraphs of help that stay up until clicked away.

Values are in the table's own units (the model's unit_label: °C, kt or dBZ).

None of them changes the table itself. Each asks the editor (the dialog), which makes the
change as one step its Undo can take back and then tells every piece to show it again.
"""
import colorsys
import math

import numpy as np
from matplotlib.colors import to_hex, to_rgb
from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QColorDialog, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy, QSplitter, QSplitterHandle,
    QStyledItemDelegate, QTableView, QToolTip, QVBoxLayout, QWidget,
)

from tcviz import picture_overlays

_HIGHLIGHT = QColor(42, 130, 218)       # the theme's selection blue (tcviz_gui.theme)
_FRAME = QColor(20, 20, 20)
_TEXT = QColor(220, 220, 220)
_DIM = QColor(140, 140, 140)


def degrees_text(value):
    """12.3 -> "12.3", -94.99999999999997 -> "-95", 48.99995 -> "49": a temperature as a
    person would write it, to the hundredth."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def step_side(model, step):
    """The words for one half of a hard step: the warm or cold side of a temperature, the
    high or low side of a wind speed or a reflectivity."""
    if model.units == "C":
        return step
    return {"warm": "high", "cold": "low"}[step]


def _tick_step(span, height, min_gap=26):
    """Degrees between the bar's labels: 10, or 20, 30... when 10 would crowd them."""
    step = 10
    while span / step * min_gap > height and step < 1000:
        step += 10
    return step


# ---------------------------------------------------------------------------- StopBar

class StopBar(QWidget):
    """The table standing up, warm at the top, with a handle for each stop."""

    _TOP, _BOTTOM = 12, 12       # room above and below the bar
    _LABELS = 44                 # width of the temperature labels on the left
    _BAR = 34                    # width of the bar itself
    _GAP = 6                     # between the bar and the handles
    _HANDLE_W, _HANDLE_H = 22, 12

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self._editor = editor
        self._drag = None            # (stop id, drag number) while a handle is dragged
        self._drags = 0
        self._gradient = None        # (key, QImage) of the bar's colors
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(150, 260)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setToolTip("")

    def sizeHint(self):
        return QSize(self._LABELS + self._BAR + self._GAP + 3 * (self._HANDLE_W + 2) + 8, 520)

    # ------------------------------------------------------------- geometry

    @property
    def model(self):
        return self._editor.model

    def _top(self):
        return float(self._TOP)

    def _bottom(self):
        return float(self.height() - self._BOTTOM)

    def bar_rect(self):
        return QRectF(self._LABELS, self._top(), self._BAR, self._bottom() - self._top())

    def y_of(self, value):
        m = self.model
        return self._top() + (m.vmax - value) / m.span * (self._bottom() - self._top())

    def value_at(self, y):
        m = self.model
        frac = (y - self._top()) / max(1.0, self._bottom() - self._top())
        return m.vmax - frac * m.span

    def handle_rects(self):
        """[(stop id, QRectF)] for every handle. The stops of a hard step sit side by side,
        warm-side half first, since they share a height."""
        out = []
        x0 = self._LABELS + self._BAR + self._GAP
        slot, last_value = 0, None
        for view in self.model.ordered():
            slot = slot + 1 if view.value == last_value else 0
            last_value = view.value
            y = self.y_of(view.value)
            out.append((view.id, QRectF(x0 + slot * (self._HANDLE_W + 2), y - self._HANDLE_H / 2,
                                        self._HANDLE_W, self._HANDLE_H)))
        return out

    def handle_at(self, point):
        """The stop whose handle is under `point` (the selected one first), else None."""
        hits = [sid for sid, rect in self.handle_rects() if rect.adjusted(-2, -2, 2, 2).contains(point)]
        if not hits:
            return None
        return self._editor.selected if self._editor.selected in hits else hits[-1]

    def _in_reach(self, point):
        """Whether a click here means a temperature on the bar (the bar or the handles' column)."""
        return self._LABELS - 4 <= point.x() and self._top() - 4 <= point.y() <= self._bottom() + 4

    # ------------------------------------------------------------- painting

    def _gradient_image(self, height):
        cmap = self._editor.current_cmap()
        if cmap is None or height < 2:
            return None
        key = (self.model.version, id(cmap), height)
        if self._gradient is None or self._gradient[0] != key:
            # sampled the way the picture's own scale samples it: top row warm (vmax)
            rgb = (cmap(np.linspace(1.0, 0.0, height))[:, :3] * 255).round().astype(np.uint8)
            rows = np.ascontiguousarray(rgb.reshape(height, 1, 3))
            image = QImage(rows.data, 1, height, 3, QImage.Format.Format_RGB888).copy()
            self._gradient = (key, image)
        return self._gradient[1]

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bar = self.bar_rect()
        image = self._gradient_image(int(bar.height()))
        if image is not None:
            painter.drawImage(bar, image)
        else:
            painter.fillRect(bar, QColor(60, 60, 60))
        painter.setPen(QPen(_FRAME, 1))
        painter.drawRect(bar)
        self._paint_labels(painter, bar)
        self._paint_handles(painter, bar)
        if self.hasFocus():
            painter.setPen(QPen(_HIGHLIGHT, 1, Qt.PenStyle.DotLine))
            painter.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()

    def _paint_labels(self, painter, bar):
        m = self.model
        step = _tick_step(m.span, bar.height())
        painter.setPen(_TEXT)
        font = QFont(self.font())
        painter.setFont(font)
        metrics = painter.fontMetrics()
        value = math.floor(m.vmax / step) * step
        while value >= m.vmin - 1e-9:
            y = self.y_of(value)
            painter.drawLine(QPointF(bar.left() - 4, y), QPointF(bar.left(), y))
            text = degrees_text(value)
            painter.drawText(QRectF(0, y - metrics.height() / 2, bar.left() - 6, metrics.height()),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text)
            value -= step

    def _paint_handles(self, painter, bar):
        selected = self._editor.selected
        views = {v.id: v for v in self.model.ordered()}
        for sid, rect in self.handle_rects():
            view = views[sid]
            y = rect.center().y()
            # where the stop sits: a notch at the bar's edge, or a line right across it for
            # the chosen one -- a line for every stop would hide a dense table's colors
            # (a real one has 540 stops, one per pixel row of the bar)
            left = bar.left() if sid == selected else bar.right() - 6
            painter.setPen(QPen(QColor(0, 0, 0, 170), 3))
            painter.drawLine(QPointF(left, y), QPointF(bar.right(), y))
            painter.setPen(QPen(QColor(255, 255, 255, 220), 1))
            painter.drawLine(QPointF(left, y), QPointF(bar.right(), y))
            # the handle: the stop's color, pointing at the bar
            path = QPainterPath()
            path.moveTo(rect.left() - 5, y)
            path.lineTo(rect.left(), rect.top())
            path.lineTo(rect.right(), rect.top())
            path.lineTo(rect.right(), rect.bottom())
            path.lineTo(rect.left(), rect.bottom())
            path.closeSubpath()
            painter.fillPath(path, QColor(view.color))
            if sid == selected:
                painter.setPen(QPen(_HIGHLIGHT, 2.5))
            elif view.end:
                painter.setPen(QPen(_DIM, 1, Qt.PenStyle.DashLine))
            else:
                painter.setPen(QPen(QColor(230, 230, 230), 1))
            painter.drawPath(path)

    # ------------------------------------------------------------- the mouse

    def _snapped(self, value, event):
        whole = self._editor.snap or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        return float(round(value)) if whole else round(value, 1)

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        point = event.position()
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        sid = self.handle_at(point)
        if sid is None and self._in_reach(point):
            sid = self._editor.add_stop(self._snapped(self.value_at(point.y()), event))
        if sid is None:
            return
        self._editor.select(sid)
        self._drags += 1
        self._drag = (sid, self._drags)

    def mouseMoveEvent(self, event):
        point = event.position()
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            sid, number = self._drag
            try:
                end = self.model.is_end(sid)
            except KeyError:            # gone mid-drag (undone with the keyboard)
                self._drag = None
                return
            if not end:
                self._editor.move_stop(sid, self._snapped(self.value_at(point.y()), event), merge=("drag", number))
            return
        sid = self.handle_at(point)
        if sid is not None:
            view = self.model.get(sid)
            QToolTip.showText(event.globalPosition().toPoint(), self._describe(view), self)
        elif self._in_reach(point):
            value = self.value_at(point.y())
            if self.model.vmin <= value <= self.model.vmax:
                QToolTip.showText(event.globalPosition().toPoint(),
                                  f"{degrees_text(value)} {self.model.unit_label} -- click to add a stop", self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = None

    def mouseDoubleClickEvent(self, event):
        sid = self.handle_at(event.position())
        if sid is not None:
            self._editor.select(sid)
            self._editor.choose_color(sid)

    def _describe(self, view):
        text = f"{degrees_text(view.value)} {self.model.unit_label}   {view.color}"
        if view.step:
            text += f"\nHard step: this color is on the {step_side(self.model, view.step)} side."
        if view.end:
            text += "\nThis stop stays at the end of the range."
        return text

    # ------------------------------------------------------------- right-click

    def menu_for(self, point):
        """(QMenu, {QAction: what it does}) for a right-click at `point`; each value a
        no-argument callable."""
        menu = QMenu(self)
        actions = {}
        sid = self.handle_at(point)
        if sid is not None:
            self._editor.select(sid)
            end = self.model.is_end(sid)
            step = menu.addAction("Make a hard step here")
            step.setEnabled(not end)
            actions[step] = lambda s=sid: self._editor.make_hard_step(sid=s)
            actions[menu.addAction("Change color")] = lambda s=sid: self._editor.choose_color(s)
            delete = menu.addAction("Delete stop")
            actions[delete] = lambda s=sid: self._editor.delete_stop(s)
        elif self._in_reach(point):
            value = round(self.value_at(point.y()), 1)
            unit = self.model.unit_label
            actions[menu.addAction(f"Add a stop at {degrees_text(value)} {unit}")] = lambda v=value: self._select_new(
                self._editor.add_stop(v))
            actions[menu.addAction(f"Make a hard step at {degrees_text(value)} {unit}")] = (
                lambda v=value: self._editor.make_hard_step(value=v))
        return menu, actions

    def _select_new(self, sid):
        if sid is not None:
            self._editor.select(sid)

    def contextMenuEvent(self, event):
        menu, actions = self.menu_for(QPointF(event.pos()))
        if not actions:
            return
        chosen = menu.exec(event.globalPos())
        if chosen in actions and chosen.isEnabled():
            actions[chosen]()

    # ------------------------------------------------------------- the keyboard

    def keyPressEvent(self, event):
        sid = self._editor.selected
        key = event.key()
        if sid is not None and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 0.1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
            value = self.model.get(sid).value + (step if key == Qt.Key.Key_Up else -step)
            self._editor.move_stop(sid, value, merge=("key", sid))
            return
        if sid is not None and key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._editor.delete_stop(sid)
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------- StopListPanel

VALUE, COLOR, HEX = range(3)


class StopTableModel(QAbstractTableModel):
    """The stops as rows, warm (or high) to low, for StopListPanel."""

    _HEADERS = (None, "Color", "Hex")          # the first is the table's units

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self._editor = editor
        self._rows = []
        self._unit = editor.model.unit_label

    def refresh(self):
        unit = self._editor.model.unit_label
        if unit != self._unit:
            self._unit = unit
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, VALUE, VALUE)
        rows = self._editor.model.ordered()
        if len(rows) != len(self._rows):
            self.beginResetModel()
            self._rows = rows
            self.endResetModel()
        else:
            self._rows = rows
            if rows:
                self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, HEX))

    def row_of(self, sid):
        for i, view in enumerate(self._rows):
            if view.id == sid:
                return i
        return None

    def stop_at(self, row):
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self._rows)

    def columnCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else 3

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._HEADERS[section] or self._editor.model.unit_label
        return None

    def flags(self, index):
        view = self.stop_at(index.row())
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if view is not None and (index.column() == HEX or (index.column() == VALUE and not view.end)):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        view = self.stop_at(index.row())
        if view is None:
            return None
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if column == VALUE:
                return f"{view.value:.2f}"
            if column == HEX:
                return view.color
            return None
        if role == Qt.ItemDataRole.EditRole:
            return view.value if column == VALUE else view.color if column == HEX else None
        if role == Qt.ItemDataRole.BackgroundRole and column == COLOR:
            return QColor(view.color)
        if role == Qt.ItemDataRole.ToolTipRole:
            if column == COLOR:
                return "Click to change this stop's color in the color panel."
            if column == VALUE and view.end:
                return "This stop stays at the end of the range. Change the range to move it."
            if view.step:
                return f"Hard step: this color is on the {step_side(self._editor.model, view.step)} side."
        if role == Qt.ItemDataRole.TextAlignmentRole and column == VALUE:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and column == VALUE and view.end:
            return _DIM
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        view = self.stop_at(index.row())
        if view is None or role != Qt.ItemDataRole.EditRole:
            return False
        if index.column() == VALUE:
            return bool(self._editor.move_stop(view.id, float(value)))
        if index.column() == HEX:
            text = str(value).strip()
            if text and not text.startswith("#"):
                text = "#" + text
            return bool(self._editor.set_stop_color(view.id, text))
        return False


class _ValueDelegate(QStyledItemDelegate):
    """A spin box in the table's units for the value column."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self._editor = editor

    def createEditor(self, parent, option, index):
        box = QDoubleSpinBox(parent)
        model = self._editor.model
        box.setDecimals(2)
        box.setRange(model.vmin, model.vmax)
        box.setSuffix(f" {model.unit_label}")
        box.setSingleStep(1.0)
        box.setKeyboardTracking(False)
        return box

    def setEditorData(self, editor, index):
        editor.setValue(float(index.data(Qt.ItemDataRole.EditRole)))

    def setModelData(self, editor, model, index):
        editor.interpretText()
        old = float(index.data(Qt.ItemDataRole.EditRole))
        # the box shows two decimals; a stop it did not change keeps its exact place
        if editor.value() != round(old, 2):
            model.setData(index, editor.value())


class _SwatchDelegate(QStyledItemDelegate):
    """The color column: the stop's color, inset so a chosen row's highlight shows around
    it instead of covering it."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        color = index.data(Qt.ItemDataRole.BackgroundRole)
        if color is not None:
            painter.fillRect(QRectF(option.rect).adjusted(3, 3, -3, -3), color)


class StopListPanel(QTableView):
    """The stops as rows of numbers, kept in step with the bar: picking a row picks the
    stop, and a stop picked on the bar is shown here."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self._editor = editor
        self.table = StopTableModel(editor, self)
        self.setModel(self.table)
        self.setItemDelegateForColumn(VALUE, _ValueDelegate(editor, self))
        self.setItemDelegateForColumn(COLOR, _SwatchDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                             | QAbstractItemView.EditTrigger.EditKeyPressed
                             | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(22)
        header = self.horizontalHeader()
        header.setSectionResizeMode(VALUE, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COLOR, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(HEX, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(VALUE, 78)
        self.setColumnWidth(COLOR, 52)
        self.setMinimumWidth(230)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._syncing = False
        self.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self.clicked.connect(self._on_clicked)

    def sizeHint(self):
        return QSize(250, 400)

    def refresh(self):
        self._syncing = True
        try:
            self.table.refresh()
            self.show_selected()
        finally:
            self._syncing = False

    def show_selected(self):
        row = self.table.row_of(self._editor.selected)
        selection = self.selectionModel()
        if row is None:
            selection.clearSelection()
            return
        index = self.table.index(row, VALUE)
        if self.currentIndex().row() != row or not selection.isRowSelected(row, QModelIndex()):
            was = self._syncing
            self._syncing = True
            try:
                selection.setCurrentIndex(index, selection.SelectionFlag.ClearAndSelect
                                          | selection.SelectionFlag.Rows)
            finally:
                self._syncing = was
        self.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)

    def _on_row_changed(self, current, _previous):
        if self._syncing or not current.isValid():
            return
        view = self.table.stop_at(current.row())
        if view is not None:
            self._editor.select(view.id)

    def _on_clicked(self, index):
        if index.column() == COLOR:
            view = self.table.stop_at(index.row())
            if view is not None:
                self._editor.choose_color(view.id)

    def keyPressEvent(self, event):
        # Delete removes the stop; inside an open editor the key is the editor's
        if event.key() in (Qt.Key.Key_Delete,) and self.state() != QAbstractItemView.State.EditingState:
            if self._editor.selected is not None:
                self._editor.delete_stop(self._editor.selected)
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------- ColorPanel

# How far one press of a nudge button moves a color: 5% of the way between black and white,
# or 8 degrees around the color wheel.
LIGHTNESS_STEP = 0.05
HUE_STEP = 8.0
NO_STOP_TEXT = ("Choose a stop to change its color: click it on the bar or in the list, or click the "
                "picture where its color is.")
# The color square, a little smaller than Qt draws it, so the pictures keep their room
# (Qt redraws the square at whatever size it is given).
_SQUARE = QSize(200, 180)


class _EmbeddedColorDialog(QColorDialog):
    """Qt's color dialog as a part of the editor's window: no OK or Cancel, and nothing
    closes it. Escape is left to the editor (its Cancel, which asks first); Enter in the hex
    box takes the color typed there and goes no further -- on to the editor it would press
    Save."""

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            event.ignore()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)

    def done(self, _result):
        pass


def _trim(dialog, hex_edit):
    """Keep the color square, the brightness strip, the swatch and the hex box of Qt's color
    dialog. Its basic and custom color wells, the screen color picker and the six number
    boxes are hidden: a narrower panel leaves the picture its room. Returns (square, strip),
    either None should a Qt release name them differently."""
    square = strip = None
    for child in dialog.findChildren(QWidget):
        kind = child.metaObject().className()
        if kind.endswith("QColorPicker"):
            square = child
        elif kind.endswith("QColorLuminancePicker"):
            strip = child
        elif kind.endswith("QColorWell") or isinstance(child, QAbstractSpinBox):
            child.hide()
        elif isinstance(child, QPushButton) and child.parent() is dialog:
            child.hide()                # Pick Screen Color, Add to Custom Colors
        elif isinstance(child, QLabel):
            buddy = child.buddy()
            if child.parent() is dialog or isinstance(buddy, QAbstractSpinBox):
                child.hide()
            elif hex_edit is not None and buddy is hex_edit:
                child.setText("Hex:")   # Qt says "HTML"
    return square, strip


class ColorPanel(QGroupBox):
    """The chosen stop's color, changed in place, beside the picture: Qt's color square, its
    brightness strip, a swatch and the hex box, and four buttons that move the color a
    little lighter, darker or around the color wheel. Every change goes to the editor at
    once, so the bar, the list, the scale and the picture follow the color while it is
    picked.

    One drag in the square or the strip, one held (or clicked) button, or one visit to the
    hex box is one undo step: each starts a new `session`, and the editor folds changes
    with the same merge_key() into one step, as it does a drag on the bar."""

    def __init__(self, editor, parent=None):
        super().__init__("Color of the chosen stop", parent)
        self._editor = editor
        self._sid = None                # the stop shown
        self._loading = False           # a color being shown, not picked
        self._session = 0
        self._held = None               # the nudge button held down, from its first press to its release
        # ("#rrggbb", (hue, lightness, saturation)) the nudges count from, so a color taken
        # to white and back keeps its hue, and Lighter then Darker is exactly where it began
        self._anchor = None
        layout = QVBoxLayout(self)
        self.caption = QLabel("")
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        self.dialog = _EmbeddedColorDialog(self)
        self.dialog.setWindowFlags(Qt.WindowType.Widget)
        self.dialog.setOptions(QColorDialog.ColorDialogOption.NoButtons
                               | QColorDialog.ColorDialogOption.DontUseNativeDialog)
        self.hex_edit = self.dialog.findChild(QLineEdit, "qt_colorname_lineedit")
        self.square, self.strip = _trim(self.dialog, self.hex_edit)
        if self.dialog.layout() is not None:
            self.dialog.layout().setContentsMargins(0, 0, 0, 0)
        if self.square is not None:
            self.square.setFixedSize(_SQUARE)
            self.square.setToolTip("Click or drag to pick the color: across for the hue, up and down for how "
                                   "strong it is.")
        if self.strip is not None:
            self.strip.setToolTip("Click or drag for how bright the color is.")
        if self.hex_edit is not None:
            self.hex_edit.setToolTip("The color as six hex digits, like #ff8800. Type or paste one here.")
        layout.addWidget(self.dialog)
        grid = QGridLayout()
        self.lighter_btn = self._nudge_button(
            "Lighter", "Make the color 5% lighter, keeping its hue. Hold the button down to keep going.",
            "lightness", LIGHTNESS_STEP)
        self.darker_btn = self._nudge_button(
            "Darker", "Make the color 5% darker, keeping its hue. Hold the button down to keep going.",
            "lightness", -LIGHTNESS_STEP)
        self.hue_left_btn = self._nudge_button(
            "Hue left", "Turn the color 8° around the color wheel, the way moving left in the color square "
            "does: red toward yellow, yellow toward green, green toward blue.", "hue", HUE_STEP)
        self.hue_right_btn = self._nudge_button(
            "Hue right", "Turn the color 8° the other way, as moving right in the color square does: blue "
            "toward green, green toward yellow, yellow toward red.", "hue", -HUE_STEP)
        self.nudge_buttons = (self.lighter_btn, self.darker_btn, self.hue_left_btn, self.hue_right_btn)
        for i, button in enumerate(self.nudge_buttons):
            grid.addWidget(button, i // 2, i % 2)
        layout.addLayout(grid)
        self.hint = QLabel("The picture changes as you pick. One drag, or one button held down, is one step "
                           "for Undo.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {_DIM.name()};")
        layout.addWidget(self.hint)
        layout.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.dialog.currentColorChanged.connect(self._on_dialog_color)
        for widget in (self.square, self.strip, self.hex_edit):
            if widget is not None:
                widget.installEventFilter(self)
        if self.hex_edit is not None:
            self.hex_edit.editingFinished.connect(self._new_session)
        # Qt's color picker fixes its own size once it is laid out -- left to happen when it
        # is first shown, the panel asks till then for more width than it takes (20 px here,
        # 120 with bigger fonts), and a window opened near its least width grows by that much
        if self.dialog.layout() is not None:
            self.dialog.layout().activate()
        self.sync()

    def _nudge_button(self, text, tip, what, amount):
        button = QPushButton(text)
        button.setToolTip(tip)
        # held down it keeps going, a moment after the first step
        button.setAutoRepeat(True)
        button.setAutoRepeatDelay(400)
        button.setAutoRepeatInterval(80)
        # a click leaves the keyboard where it was, so the arrow keys still move the stop
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.pressed.connect(lambda b=button: self._on_pressed(b))
        button.released.connect(lambda b=button: self._on_released(b))
        button.clicked.connect(lambda _checked=False, w=what, a=amount: self.nudge(w, a))
        return button

    # ------------------------------------------------------------- showing the stop

    @property
    def stop(self):
        """The id of the stop shown, or None."""
        return self._sid

    def color(self):
        """"#rrggbb" the panel shows."""
        return self.dialog.currentColor().name()

    def merge_key(self):
        return ("color", self._sid, self._session)

    def sync(self):
        """Show the editor's chosen stop and its color as the table has it now -- after a
        new choice, an edit anywhere, an undo -- without that counting as a change."""
        model = self._editor.model
        sid = self._editor.selected
        try:
            view = model.get(sid) if sid is not None else None
        except KeyError:
            view = None
        for widget in (self.dialog, *self.nudge_buttons):
            widget.setEnabled(view is not None)
        if view is None:
            self._sid = None
            self.caption.setText(NO_STOP_TEXT)
            return
        if view.id != self._sid:
            self._sid = view.id
            self._new_session()
        text = f"The stop at {degrees_text(view.value)} {model.unit_label}"
        if view.step:
            text += f", the {step_side(model, view.step)} side of a hard step"
        self.caption.setText(text + ".")
        if self.color() != view.color:
            self._show(view.color)

    def _show(self, color):
        self._loading = True
        try:
            self.dialog.setCurrentColor(QColor(color))
        finally:
            self._loading = False

    def focus_color(self):
        """The keyboard to the hex box, ready for a color typed or pasted (a double-click
        on a stop)."""
        if self.hex_edit is not None and self.dialog.isEnabled():
            self.hex_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            self.hex_edit.selectAll()

    # ------------------------------------------------------------- picking

    def _new_session(self):
        self._session += 1

    def eventFilter(self, watched, event):
        # a press in the square or the strip starts a drag, and going into the hex box
        # starts some typing: each a new undo step
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress or (watched is self.hex_edit and kind == QEvent.Type.FocusIn):
            self._new_session()
        return False

    def _on_dialog_color(self, color):
        if self._loading or self._sid is None:
            return
        self._anchor = None
        self._editor.set_stop_color(self._sid, color.name(), merge=self.merge_key())

    def _on_pressed(self, button):
        # a held button says "pressed" again at every repeat: only its first press is new
        if self._held is not button:
            self._held = button
            self._new_session()

    def _on_released(self, button):
        # ... and "released" before each repeat too, while it is still down
        if not button.isDown():
            self._held = None

    def nudge(self, what, amount):
        """Move the chosen stop's color a little: what="lightness" by `amount` (a fraction
        of the way from black to white), what="hue" by `amount` degrees around the color
        wheel. Lightness stops at white and black; the hue goes round."""
        if self._sid is None:
            return
        current = self._editor.model.get(self._sid).color
        if self._anchor is not None and self._anchor[0] == current:
            hue, light, sat = self._anchor[1]
        else:
            hue, light, sat = colorsys.rgb_to_hls(*to_rgb(current))
        if what == "lightness":
            if (amount > 0 and light >= 1.0) or (amount < 0 and light <= 0.0):
                self._editor.say("The color is already white, so it can't get any lighter." if amount > 0 else
                                 "The color is already black, so it can't get any darker.")
                return
            light = min(1.0, max(0.0, light + amount))
        else:
            if sat == 0.0 or light in (0.0, 1.0):
                self._editor.say("Gray, black and white have no hue to turn. Give the stop some color in "
                                 "the color square first.")
                return
            hue = (hue + amount / 360.0) % 1.0
        new = to_hex(colorsys.hls_to_rgb(hue, light, sat))
        self._anchor = (new, (hue, light, sat))
        if new != current:
            self._editor.set_stop_color(self._sid, new, merge=self.merge_key())


# ---------------------------------------------------------------------------- ScalePanel

# The scale is drawn as a picture this tall carries it, then shrunk to the panel: the
# proportions of the real thing, legible at the panel's size.
SCALE_PICTURE_PX = 1600


class ScalePanel(QWidget):
    """The picture's own color scale for the table (picture_overlays.colorbar_preview)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = None          # the PIL image shown, as render drew it
        self._qimage = None
        self.setMinimumSize(70, 200)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setToolTip("The color scale exactly as your pictures will carry it.")

    def sizeHint(self):
        return QSize(96, 520)

    def set_scale(self, cmap, vmax_k, vmin_k, units="C"):
        if cmap is None:
            self.image, self._qimage = None, None
        else:
            self.image = picture_overlays.colorbar_preview(cmap, None, vmax_k, vmin_k, units, SCALE_PICTURE_PX)
            data = self.image.convert("RGB").tobytes("raw", "RGB")
            self._qimage = QImage(data, self.image.width, self.image.height, 3 * self.image.width,
                                  QImage.Format.Format_RGB888).copy()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        if self._qimage is None:
            painter.end()
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        w, h = self._qimage.width(), self._qimage.height()
        scale = min(self.width() / w, self.height() / h)
        target = QRectF((self.width() - w * scale) / 2, 0, w * scale, h * scale)
        painter.drawImage(target, self._qimage)
        painter.end()


# ---------------------------------------------------------------------------- PicturePane

# One press of Zoom in or Zoom out, and one notch of the mouse wheel.
ZOOM_STEP = 2.0
WHEEL_STEP = 1.25
# The closest look: one pixel of the saved picture this many screen pixels across.
MAX_PIXEL_SIZE = 48.0
# From this many screen pixels across, one pixel of the picture reads as a square of its own.
SQUARE_PIXELS = 3.0
# How far the pointer moves with a button held before a click on the picture becomes a drag.
DRAG_START = 5
# The buttons that drag the picture around; the left one also clicks.
_DRAG_BUTTONS = (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton)


class PictureView(QObject):
    """How closely a picture is looked at: `zoom` times the size that shows all of it (1 is
    Fit), about `center` -- the point of the saved picture at the middle of the pane, (x, y)
    in the saved picture's own pixels, None for the picture's middle. The editor's picture
    and the one compared with share one, so they zoom and pan together."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom = 1.0
        self.center = None

    def set(self, zoom, center=None):
        zoom = float(zoom)
        # a step out by a wheel notch is a fraction no float holds exactly: back to Fit
        # means Fit, not 1.0000000000000002
        if zoom < 1.0 + 1e-9:
            zoom, center = 1.0, None
        if center is not None:
            center = (float(center[0]), float(center[1]))
        if (zoom, center) != (self.zoom, self.center):
            self.zoom, self.center = zoom, center
            self.changed.emit()

    def fit(self):
        """The whole picture again."""
        self.set(1.0)


def zoom_text(zoom):
    """1.5625 -> "1.6×", 2.0 -> "2×", 24.41 -> "24×"."""
    if zoom >= 10:
        return f"{round(zoom)}×"
    return f"{zoom:.1f}".rstrip("0").rstrip(".") + "×"


def zoom_words(pane):
    """The zoom of `pane` in words for the editor: how close, and what is shown."""
    size = pane.pixel_size()
    if size is None or pane.view.zoom <= 1.0:
        return "Zoom: whole picture"
    level = f"Zoom: {zoom_text(pane.view.zoom)}"
    if pane.stride > 1 and not pane.full_detail():
        return f"{level} (zoom in more to see every pixel)"
    if size >= SQUARE_PIXELS:
        return f"{level} (each square is one real pixel)"
    return f"{level} (every pixel shown)"


def _looked_up(rows, lut):
    """(RGBA bytes, a QImage over them) of the row image `rows` through `lut`."""
    from tcviz.colortable_preview import apply
    buffer = apply(rows, lut)
    h, w = rows.shape
    return buffer, QImage(buffer.data, w, h, 4 * w, QImage.Format.Format_RGBA8888)


class _TableColors:
    """One table's colors over a pane's picture: the table row of every pixel as handed over
    (`index`) and the table's colors (`lut`), looked up only when the pane is next painted;
    and, zoomed in to every pixel, the rows of a window of the saved picture's own pixels
    (`full_rows`, as PicturePane.set_index_image takes it) and their colors. A pane's own
    table is one of these; in a swipe, the table compared with is another, over the same
    pixels, with its window at the same place."""

    def __init__(self):
        self.index = None
        self.lut = None
        self.full_rows = None
        self.buffer = None
        self._image = None
        self._dirty = False
        # (bounds, row image) of the saved picture's own pixels around the part in view,
        # when zoomed in to every pixel, and its colors as last looked up
        self.window = None
        self.window_buffer = None
        self._window_image = None
        self._window_dirty = False

    def set_index(self, index):
        """New rows. The window belongs to the rows it was worked out with, so it goes too."""
        self.index = index
        self._dirty = True
        self.window = None

    def set_lut(self, lut):
        """New colors; whether they are new."""
        if lut is self.lut:
            return False
        self.lut = lut
        self._dirty = True
        self._window_dirty = True
        return True

    def image(self):
        """The picture through the table as a QImage (None without rows or colors)."""
        if self._dirty:
            self._dirty = False
            if self.index is None or self.lut is None:
                self._image = self.buffer = None
            else:
                self.buffer, self._image = _looked_up(self.index, self.lut)
        return self._image

    def window_image(self, bounds):
        """The QImage of the saved picture's own pixels in `bounds` (top, bottom, left,
        right): their rows worked out again only when the window moves, their colors only
        after an edit. None when the rows can't be worked out."""
        if self.window is None or self.window[0] != bounds:
            try:
                rows = self.full_rows(*bounds)
            except (ValueError, MemoryError):
                return None
            self.window = (bounds, rows)
            self._window_dirty = True
        if self._window_dirty or self._window_image is None:
            self.window_buffer, self._window_image = _looked_up(self.window[1], self.lut)
            self._window_dirty = False
        return self._window_image


# How near the swipe's divider, in screen pixels either side, a press takes hold of the
# line rather than of the picture.
DIVIDER_REACH = 6
# How far an arrow key moves the divider: a hundredth of the width of the picture in view;
# with Shift, a tenth of that.
DIVIDER_STEP = 0.01
# The divider's grab handle, in screen pixels, and how far above the foot of the line it stands.
_GRIP_W, _GRIP_H, _GRIP_GAP = 14.0, 40.0, 12.0


class PicturePane(QWidget):
    """A picture through a color table. It is handed the picture as the table row of every
    pixel (set_index_image) and the table as its colors (set_lut); the colors are looked up
    only when it is next painted, so a burst of edits costs one lookup.

    It shows the picture at any zoom (`view`, a PictureView two panes can share): the
    mouse wheel zooms about the pointer, zoom_by about any point, and a drag moves a
    zoomed picture around. The picture handed over may be thinned (`stride`, as
    colortable_preview.PreviewSource thins one); zoomed in until the part in view holds no
    more pixels than the thinned picture does, the pane draws every pixel of that part,
    read through `full_rows` (PreviewSource.window_rows) -- so an edit there never looks up
    more pixels than one at Fit. Enlarged pixels are drawn as squares, never smoothed.

    An `interactive` pane -- the editor's own picture, never the one compared with -- says
    which pixel is under the pointer (`hovered`, with None off the picture) and which one
    was clicked (`clicked`, with whether Shift was held): a press and a release that do not
    move more than DRAG_START pixels apart; further than that is a drag. Pixels are counted
    in the saved picture's own: a thinned pixel as the one it was kept from
    (PreviewSource.saved_pixel), and zoomed in to every pixel, the pixel itself.

    Swiping (set_swipe), the pane shows a second table over the same picture: its own
    table left of a vertical line, the other right of it, each drawn from its own rows and
    colors and clipped at the line, thinned or up close alike -- so each side's colors are
    exactly its table's. The line (`divider`, a fraction across the part of the picture in
    view) stays where it is on screen while the picture zooms and pans under it; a press
    within DIVIDER_REACH of it drags the line instead of the picture, and the arrow keys
    move it once the pane has the keyboard (a press on the line, or Tab). Right of the line
    the pointer is reported as `hovered_compared` and a click does nothing: that side is
    another table."""

    hovered = Signal(object)                # (row, col) of the saved picture, or None
    hovered_compared = Signal(object)       # the same, right of a swipe's line
    clicked = Signal(int, int, bool)        # row, col of the saved picture, Shift held
    shown = Signal()                        # the zoom, the place, the picture or the pane's size changed

    def __init__(self, parent=None, interactive=False, view=None):
        super().__init__(parent)
        self._main = _TableColors()
        self._other = None              # the table compared with, swiping
        self._stride = 1
        self._full_shape = None
        self._message = ""
        self._press = None              # (button, where, modifiers), from a press to its release
        self._dragged_to = None         # where a drag has got to, once the press became one
        self.divider = 0.5
        self._grab = None               # how far right of the line it was taken hold of, while dragged
        self._over_line = False         # the pointer is near enough the line to take hold of it
        self._focus_before = None       # what had the keyboard before a press on the line took it
        self.interactive = bool(interactive)
        self.view = None
        self.set_view(view if view is not None else PictureView(self))
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        if self.interactive:
            self.setMouseTracking(True)
        self._show_cursor()

    def sizeHint(self):
        return QSize(420, 420)

    def set_view(self, view):
        """Look at the picture through `view` (shared with another pane, say)."""
        if self.view is not None:
            self.view.changed.disconnect(self._on_view)
        self.view = view
        view.changed.connect(self._on_view)
        self._on_view()

    def _on_view(self):
        self._show_cursor()
        self.update()
        self.shown.emit()

    def set_index_image(self, index, stride=1, full_shape=None, full_rows=None):
        """The picture as the table row of every pixel; `stride` the thinning it was made
        with, `full_shape` the saved picture's (rows, columns), and `full_rows(top, bottom,
        left, right)` the rows of any window of the saved picture's own pixels (None: the
        thinned pixels are enlarged instead)."""
        stride = max(1, int(stride))
        if full_shape is None:
            full_shape = (index.shape[0] * stride, index.shape[1] * stride)
        full_shape = (int(full_shape[0]), int(full_shape[1]))
        if index is not self._main.index or stride != self._stride or full_shape != self._full_shape:
            self._stride, self._full_shape = stride, full_shape
            # the same rows (one range and table size, for the same picture) mean the same window
            self._main.set_index(index)
            self.update()
            self.shown.emit()
        self._main.full_rows = full_rows

    def set_lut(self, lut):
        if self._main.set_lut(lut):
            self.update()

    def set_message(self, text):
        """Show words instead of the picture ("" to show the picture again)."""
        self._message = text
        self.update()

    @property
    def message(self):
        return self._message

    def image(self):
        """The picture as last handed over, as a QImage (None without one)."""
        return self._main.image()

    def rgba(self):
        """The RGBA bytes of the picture as handed over (H, W, 4), or None."""
        self._main.image()
        return self._main.buffer

    def detail_rgba(self):
        """((top, bottom, left, right), RGBA bytes) of the saved picture's own pixels the
        pane draws, zoomed in to every pixel -- a window a little wider than the view --
        else None."""
        if not self.full_detail() or self._detail_window() is None:
            return None
        return self._main.window[0], self._main.window_buffer

    @property
    def picture_shape(self):
        """(rows, columns) of the picture as handed over, or None."""
        return None if self._main.index is None else self._main.index.shape

    @property
    def full_shape(self):
        """(rows, columns) of the saved picture, or None."""
        return None if self._main.index is None else self._full_shape

    @property
    def stride(self):
        return self._stride

    # ------------------------------------------------------------- swiping

    def set_swipe(self, index, lut=None, full_rows=None):
        """Swipe: show another table right of the divider -- `index` its row of every pixel
        of the same picture (the same thinning), `lut` its colors and `full_rows` its rows
        up close, as set_index_image and set_lut take this pane's own. None: this table
        alone again. The line starts in the middle of each new swipe."""
        if index is None:
            if self._other is None:
                return
            self._other = None
            self._grab = None
            self._over_line = False
            if self.hasFocus():
                self._give_focus_back()
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setMouseTracking(self.interactive)
            self._show_cursor()
            self.update()
            return
        if self._other is None:
            self._other = _TableColors()
            self.divider = 0.5
            # the keyboard comes by Tab, or by a press on the line; a click on the picture
            # leaves it where it was, so the arrow keys still move the chosen stop
            self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            self.setMouseTracking(True)
        if index is not self._other.index:
            self._other.set_index(index)
            self.update()
        if self._other.set_lut(lut):
            self.update()
        self._other.full_rows = full_rows

    @property
    def swiping(self):
        return self._other is not None

    def swipe_rgba(self):
        """The RGBA bytes of the picture in the table compared with, as handed over (H, W,
        4), or None when not swiping."""
        if self._other is None:
            return None
        self._other.image()
        return self._other.buffer

    def swipe_detail_rgba(self):
        """detail_rgba for the table compared with: the same window, its colors."""
        if self._other is None or self.detail_rgba() is None:
            return None
        bounds = self._main.window[0]
        if self._other.window_image(bounds) is None:
            return None
        return bounds, self._other.window_buffer

    def _span(self):
        """(left, right): where the picture is drawn across the pane, or None."""
        target = None if self._message else self.picture_rect()
        if target is None or target.isEmpty():
            return None
        left, right = max(0.0, target.left()), min(float(self.width()), target.right())
        return (left, right) if right > left else None

    def divider_x(self):
        """Where the divider is, across the pane (None when not swiping, or no picture)."""
        span = self._span() if self._other is not None else None
        if span is None:
            return None
        return span[0] + self.divider * (span[1] - span[0])

    def set_divider(self, fraction):
        """Put the divider `fraction` of the way across the picture in view, kept on it."""
        fraction = min(1.0, max(0.0, float(fraction)))
        if fraction != self.divider:
            self.divider = fraction
            self.update()

    def swipe_column(self):
        """The saved picture's column the divider is at: its columns before this one are
        drawn in this pane's table, the rest in the other (None when not swiping)."""
        line = self.divider_x()
        if line is None:
            return None
        width = self._full_shape[1]
        return min(width, max(0, round((line - self.picture_rect().left()) / self.pixel_size())))

    def swipe_fraction(self):
        """swipe_column as a fraction of the saved picture's width, as
        colortable_preview.swiped_picture takes it (None when not swiping)."""
        column = self.swipe_column()
        return None if column is None else column / self._full_shape[1]

    def divider_handle_rect(self):
        """The divider's grab handle, near its foot: a storm picture has the eye in the
        middle, which is what the line is moved across to see (None when not swiping)."""
        line = self.divider_x()
        if line is None:
            return None
        target = self.picture_rect()
        top, bottom = max(0.0, target.top()), min(float(self.height()), target.bottom())
        y = max(top, bottom - _GRIP_GAP - _GRIP_H)
        return QRectF(line - _GRIP_W / 2, y, _GRIP_W, min(_GRIP_H, bottom - y))

    def near_divider(self, point):
        """Whether a press at `point` takes hold of the divider rather than the picture."""
        line = self.divider_x()
        if line is None:
            return False
        if self.divider_handle_rect().adjusted(-2, -2, 2, 2).contains(point):
            return True
        target = self.picture_rect()
        return (abs(point.x() - line) <= DIVIDER_REACH
                and max(0.0, target.top()) <= point.y() <= min(float(self.height()), target.bottom()))

    def _right_of_divider(self, point):
        line = self.divider_x()
        return line is not None and point.x() >= line

    def _take_focus(self):
        before = QApplication.focusWidget()
        if before is not self:
            self._focus_before = before
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def _give_focus_back(self):
        """The keyboard back where it was before a press on the line took it: a click that
        chooses a stop leaves the arrow keys to the stop, as it does without a swipe."""
        before, self._focus_before = self._focus_before, None
        if before is None or not self.hasFocus():
            return
        try:
            if before.isVisible() and before.focusPolicy() != Qt.FocusPolicy.NoFocus:
                before.setFocus(Qt.FocusReason.OtherFocusReason)
        except RuntimeError:
            pass                    # it has gone since

    # ------------------------------------------------------------- where it is drawn

    def fit_scale(self):
        """Screen pixels across one pixel of the saved picture at Fit (None without a picture)."""
        if self._main.index is None:
            return None
        h, w = self._full_shape
        return min(self.width() / w, self.height() / h)

    def pixel_size(self):
        """Screen pixels across one pixel of the saved picture as drawn now (None without a picture)."""
        fit = self.fit_scale()
        return None if fit is None else fit * self.view.zoom

    def max_zoom(self):
        """The closest zoom: one pixel of the saved picture MAX_PIXEL_SIZE screen pixels across."""
        fit = self.fit_scale()
        return max(1.0, MAX_PIXEL_SIZE / fit) if fit else 1.0

    def _clamped(self, cx, cy, size):
        """The center (x, y) moved as little as it takes for the picture to fill the pane
        where it is bigger than the pane, and to sit in the middle where it is not."""
        h, w = self._full_shape
        if size <= 0:
            return w / 2, h / 2
        half_w, half_h = self.width() / (2 * size), self.height() / (2 * size)
        cx = w / 2 if 2 * half_w >= w else min(max(cx, half_w), w - half_w)
        cy = h / 2 if 2 * half_h >= h else min(max(cy, half_h), h - half_h)
        return cx, cy

    def _center(self):
        h, w = self._full_shape
        cx, cy = self.view.center if self.view.center is not None else (w / 2, h / 2)
        return self._clamped(cx, cy, self.pixel_size())

    def picture_rect(self):
        """Where the whole picture is drawn: at Fit as big as fits, in the middle, with bars
        of background either side or above and below; zoomed in, bigger than the pane and
        mostly past its edges (None without a picture)."""
        if self._main.index is None:
            return None
        h, w = self._full_shape
        size = self.pixel_size()
        cx, cy = self._center()
        return QRectF(self.width() / 2 - cx * size, self.height() / 2 - cy * size, w * size, h * size)

    def visible_window(self):
        """(top, bottom, left, right): the saved picture's pixels in view, whole or in
        part (None without a picture)."""
        target = self.picture_rect()
        if target is None or target.isEmpty():
            return None
        h, w = self._full_shape
        size = self.pixel_size()
        left = max(0, math.floor(-target.left() / size))
        right = min(w, math.ceil((self.width() - target.left()) / size))
        top = max(0, math.floor(-target.top() / size))
        bottom = min(h, math.ceil((self.height() - target.top()) / size))
        if right <= left or bottom <= top:
            return None
        return top, bottom, left, right

    def full_detail(self):
        """Whether every pixel of the saved picture in view is drawn: zoomed in on a
        thinned picture until the part in view holds no more pixels than the thinned
        picture does."""
        index = self._main.index
        if index is None or self._stride == 1 or self._main.full_rows is None or self.view.zoom <= 1.0:
            return False
        if self._other is not None and self._other.full_rows is None:
            return False
        window = self.visible_window()
        if window is None:
            return False
        top, bottom, left, right = window
        return (bottom - top) * (right - left) <= index.size

    def _detail_bounds(self):
        """(top, bottom, left, right) of the window of the saved picture's own pixels to
        draw up close, or None. A little wider than the view, so a short drag needs no new
        one, and moved only when the view leaves it or it holds far more than the view
        (after a zoom in): an edit looks up only these colors."""
        visible = self.visible_window()
        if visible is None or self._main.lut is None:
            return None
        top, bottom, left, right = visible
        area = (bottom - top) * (right - left)
        held = self._main.window[0] if self._main.window is not None else None
        if held is not None and (held[0] <= top and bottom <= held[1] and held[2] <= left and right <= held[3]
                                 and (held[1] - held[0]) * (held[3] - held[2]) <= 4 * area):
            return held
        # a margin of up to a quarter of the view on each side, the whole window no more
        # pixels than the thinned picture
        margin = min(0.25, max(0.0, (math.sqrt(self._main.index.size / area) - 1) / 2))
        mh, mw = int((bottom - top) * margin), int((right - left) * margin)
        h, w = self._full_shape
        return max(0, top - mh), min(h, bottom + mh), max(0, left - mw), min(w, right + mw)

    def _detail_window(self):
        """((top, bottom, left, right), QImage) of the saved picture's own pixels around
        the part in view (_detail_bounds) in this pane's table, or None."""
        bounds = self._detail_bounds()
        image = None if bounds is None else self._main.window_image(bounds)
        return None if image is None else (bounds, image)

    def pixel_at(self, point):
        """(row, col) of the saved picture's pixel under `point` (in the pane), or None:
        off the picture, in the bars beside it, or while words are shown instead. On a
        thinned picture it is the pixel the one shown was kept from, until the pane is
        zoomed in to every pixel."""
        target = None if self._message else self.picture_rect()
        if target is None or target.isEmpty():
            return None
        detail = self.full_detail()
        h, w = self._full_shape if detail else self._main.index.shape
        col = math.floor((point.x() - target.left()) / target.width() * w)
        row = math.floor((point.y() - target.top()) / target.height() * h)
        if not (0 <= row < h and 0 <= col < w):
            return None
        return (row, col) if detail else (row * self._stride, col * self._stride)

    # ------------------------------------------------------------- zooming and panning

    def zoom_by(self, factor, anchor=None):
        """Zoom by `factor` (2 twice as close, 0.5 back out), keeping the point of the
        picture under `anchor` -- a point in the pane, its middle when None -- where it is.
        Never further out than Fit, nor closer than MAX_PIXEL_SIZE."""
        target = self.picture_rect()
        if target is None or target.isEmpty():
            return
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        size = self.pixel_size()
        x = (anchor.x() - target.left()) / size
        y = (anchor.y() - target.top()) / size
        zoom = min(max(1.0, self.view.zoom * factor), self.max_zoom())
        new = self.fit_scale() * zoom
        center = (x - (anchor.x() - self.width() / 2) / new, y - (anchor.y() - self.height() / 2) / new)
        self.view.set(zoom, self._clamped(*center, new))

    def pan_by(self, delta):
        """Move a zoomed picture by `delta` screen pixels (a QPointF), as a drag does."""
        if self._main.index is None or self.view.zoom <= 1.0:
            return
        size = self.pixel_size()
        cx, cy = self._center()
        self.view.set(self.view.zoom, self._clamped(cx - delta.x() / size, cy - delta.y() / size, size))

    def _show_cursor(self):
        if self._grab is not None or (self._over_line and self._dragged_to is None):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._dragged_to is not None:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self.interactive:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self.view.zoom > 1.0:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def _hover_line(self, over):
        if over != self._over_line:
            self._over_line = over
            self._show_cursor()
            self.update()

    # ------------------------------------------------------------- the mouse and the keyboard

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if self._message or self.picture_rect() is None or not steps:
            event.ignore()
            return
        self.zoom_by(WHEEL_STEP ** steps, event.position())
        event.accept()

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton and self._press is None
                and self.near_divider(event.position())):
            # the line before the picture: a press on it drags it, never pans or chooses
            self._grab = event.position().x() - self.divider_x()
            self._take_focus()
            self._show_cursor()
            self.update()
            event.accept()
            return
        if event.button() in _DRAG_BUTTONS and not self._message and self._main.index is not None:
            # a press is a click or the start of a drag: which one, the pointer says
            self._press = (event.button(), event.position(), event.modifiers())
            self._dragged_to = None
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        point = event.position()
        if self._grab is not None:
            span = self._span()
            if span is not None:
                self.set_divider((point.x() - self._grab - span[0]) / (span[1] - span[0]))
            event.accept()
            return
        if self._press is not None:
            start = self._press[1]
            if self._dragged_to is None and (point - start).manhattanLength() >= DRAG_START:
                self._dragged_to = start
                self._show_cursor()
            if self._dragged_to is not None:
                self.pan_by(point - self._dragged_to)
                self._dragged_to = point
        if self._other is not None:
            self._hover_line(self._press is None and self.near_divider(point))
        if self.interactive:
            pixel = self.pixel_at(point)
            if self._right_of_divider(point):
                self.hovered_compared.emit(pixel)
            else:
                self.hovered.emit(pixel)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._grab is not None and event.button() == Qt.MouseButton.LeftButton:
            self._grab = None
            self._over_line = self.near_divider(event.position())
            self._show_cursor()
            self.update()
            event.accept()
            return
        if self._press is None or event.button() != self._press[0]:
            super().mouseReleaseEvent(event)
            return
        button, start, modifiers = self._press
        dragged = self._dragged_to is not None
        self._press = self._dragged_to = None
        self._show_cursor()
        event.accept()
        # right of a swipe's line is the table compared with: a click there chooses nothing
        if not dragged and button == Qt.MouseButton.LeftButton and self.interactive \
                and not self._right_of_divider(start):
            pixel = self.pixel_at(start)
            if pixel is not None:
                self._give_focus_back()
                self.clicked.emit(pixel[0], pixel[1], bool(modifiers & Qt.KeyboardModifier.ShiftModifier))

    def leaveEvent(self, event):
        self._hover_line(False)
        if self.interactive:
            self.hovered.emit(None)
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if self._other is not None and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = DIVIDER_STEP / 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else DIVIDER_STEP
            self.set_divider(self.divider + (step if key == Qt.Key.Key_Right else -step))
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()               # the line shows it has the keyboard

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.shown.emit()

    # ------------------------------------------------------------- painting

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(18, 18, 18))
        if self._message or self._main.index is None or self._main.lut is None:
            painter.setPen(_TEXT)
            painter.drawText(QRectF(self.rect()).adjusted(12, 12, -12, -12),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._message)
            painter.end()
            return
        target = self.picture_rect()
        if self._swipe_ready():
            self._paint_swipe(painter, target)
            painter.end()
            return
        size = self.pixel_size()
        detail = self._detail_window() if self.full_detail() else None
        if detail is not None:
            (top, bottom, left, right), image = detail
            where = QRectF(target.left() + left * size, target.top() + top * size,
                           (right - left) * size, (bottom - top) * size)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, size < 1.0)
            painter.drawImage(where, image)
            painter.end()
            return
        image = self.image()
        h, w = self._main.index.shape
        across, down = target.width() / w, target.height() / h      # screen pixels per pixel shown
        # only the part in view: a picture enlarged 48 times is mostly off the pane
        c0 = max(0, math.floor(-target.left() / across))
        c1 = min(w, math.ceil((self.width() - target.left()) / across))
        r0 = max(0, math.floor(-target.top() / down))
        r1 = min(h, math.ceil((self.height() - target.top()) / down))
        if c1 > c0 and r1 > r0:
            where = QRectF(target.left() + c0 * across, target.top() + r0 * down, (c1 - c0) * across, (r1 - r0) * down)
            # a picture drawn smaller than it is is smoothed; an enlarged one is drawn pixel
            # for pixel, as the app enlarges a picture, so pixels look like pixels
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, across < 1.0)
            painter.drawImage(where, image, QRectF(c0, r0, c1 - c0, r1 - r0))
        painter.end()

    def _swipe_ready(self):
        """Swiping, with the other table's rows and colors for this very picture."""
        other = self._other
        return (other is not None and other.index is not None and other.lut is not None
                and other.index.shape == self._main.index.shape)

    def _paint_swipe(self, painter, target):
        """Both tables, each clipped to its side of the divider, then the divider."""
        line = self.divider_x()
        tables = (self._main, self._other)
        pictures = None
        if self.full_detail():
            # up close, both from the same window of the saved picture's own pixels; should
            # either table's not be had, both thinned, so the two sides always match
            bounds = self._detail_bounds()
            images = [] if bounds is None else [table.window_image(bounds) for table in tables]
            if images and None not in images:
                top, bottom, left, right = bounds
                size = self.pixel_size()
                where = QRectF(target.left() + left * size, target.top() + top * size,
                               (right - left) * size, (bottom - top) * size)
                pictures = [(image, where) for image in images]
        if pictures is None:
            pictures = [(table.image(), target) for table in tables]
        sides = ((0.0, line), (line, float(self.width())))
        for (image, where), (x0, x1) in zip(pictures, sides):
            if x1 <= x0:
                continue
            painter.save()
            painter.setClipRect(QRectF(x0, 0.0, x1 - x0, float(self.height())))
            self._draw_part(painter, image, where, x0, x1)
            painter.restore()
        self._paint_divider(painter, line, target)

    def _draw_part(self, painter, image, where, x0, x1):
        """Draw `image`, laid over `where`, but only its pixels that show between x0 and x1
        across the pane: a side of a swipe looks up and scales no more than it shows."""
        w, h = image.width(), image.height()
        across, down = where.width() / w, where.height() / h
        c0 = max(0, math.floor((max(0.0, x0) - where.left()) / across))
        c1 = min(w, math.ceil((min(float(self.width()), x1) - where.left()) / across))
        r0 = max(0, math.floor(-where.top() / down))
        r1 = min(h, math.ceil((self.height() - where.top()) / down))
        if c1 > c0 and r1 > r0:
            part = QRectF(where.left() + c0 * across, where.top() + r0 * down, (c1 - c0) * across, (r1 - r0) * down)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, across < 1.0)
            painter.drawImage(part, image, QRectF(c0, r0, c1 - c0, r1 - r0))

    def _paint_divider(self, painter, line, target):
        """The line from the top of the picture in view to its bottom, dark-edged so it
        shows on any color, and its handle; in the theme's blue while it is held, under the
        pointer or has the keyboard."""
        top = max(0.0, target.top())
        bottom = min(float(self.height()), target.bottom())
        lit = _HIGHLIGHT if (self._grab is not None or self._over_line or self.hasFocus()) else _TEXT
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(_FRAME, 4.0))
        painter.drawLine(QPointF(line, top), QPointF(line, bottom))
        painter.setPen(QPen(lit, 2.0))
        painter.drawLine(QPointF(line, top), QPointF(line, bottom))
        grip = self.divider_handle_rect()
        painter.setPen(QPen(lit, 1.5))
        painter.setBrush(_FRAME)
        painter.drawRoundedRect(grip, 4.0, 4.0)
        # an arrow each way: it moves sideways
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_TEXT)
        cx, cy = grip.center().x(), grip.center().y()
        for sign in (-1, 1):
            painter.drawPolygon(QPolygonF([QPointF(cx + sign * 1.5, cy - 5), QPointF(cx + sign * 1.5, cy + 5),
                                           QPointF(cx + sign * 5.5, cy)]))


# ---------------------------------------------------------------------------- PanelSplitter

# Across the handle between two panels: as wide as the gap the panels had before there was
# one, so the window needs no more room than it did.
HANDLE_WIDTH = 6
_EDGE = QColor(85, 85, 85)              # the edge of a pop-up, as the theme's tooltips have
_RAISED = QColor(45, 45, 45)            # the theme's window color


class PanelSplitter(QSplitter):
    """Panels side by side with a handle between each two to drag -- the editor's color
    panel and its picture. A panel allowed to fold (setCollapsible) folds away when its
    handle is dragged over it, giving its room to the panel beside it, and comes back when
    the handle is dragged out again or double-clicked (set_folded does the same). The
    handle shows a grip, lit while the pointer is on it, and an arrow while the panel
    before it is folded away, so it can be found again. `changed` follows every move of a
    handle, a drag's or set_folded's.

    A panel added with keep_width stays exactly as wide as its contents ask (its size
    hint), so its controls never stretch or squeeze: its handle only folds it away or
    brings it back."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._kept = []                 # the panels that keep their own width
        self.setHandleWidth(HANDLE_WIDTH)
        self.setChildrenCollapsible(False)
        self.splitterMoved.connect(lambda _pos, _index: self._moved())

    def createHandle(self):
        return _GripHandle(self.orientation(), self)

    def keep_width(self, panel):
        """Hold `panel` (one of the splitter's) at the width its contents ask for, however
        the handles are dragged -- or folded away, if it may be. Measured once the splitter
        is shown, not now: a window still being built is not yet as it will be shown (a
        part hidden after this would be counted in, and stay counted)."""
        self._kept.append(panel)

    def event(self, event):
        # shown, or a panel's contents ask for another width -- most often once it is first
        # shown, when its style has measured it: the splitter lays them out again now
        relaid = event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Show)
        if relaid:
            self._fit_kept()
        done = super().event(event)
        if relaid:
            # QSplitter tells the window it sits in nothing of its panels' new least sizes
            # (only of a new greatest): the window would go on with the old ones
            self.updateGeometry()
        return done

    def _fit_kept(self):
        sizes = self.sizes()
        respace = False
        for panel in self._kept:
            width = max(panel.sizeHint().width(), panel.minimumSizeHint().width())
            if width <= 0:
                continue
            if (panel.minimumWidth(), panel.maximumWidth()) != (width, width):
                panel.setFixedWidth(width)
            i = self.indexOf(panel)
            # QSplitter goes on giving a panel the room it first measured for it (one
            # measured before it was shown, say), whatever width it takes since: laid out
            # (any size not 0) and not folded away, it is given exactly its width again
            if any(sizes) and sizes[i] not in (0, width):
                other = i + 1 if i + 1 < len(sizes) else i - 1
                sizes[other] = max(0, sizes[other] + sizes[i] - width)
                sizes[i] = width
                respace = True
        if respace:
            self.setSizes(sizes)

    def minimumSizeHint(self):
        # A panel folded away takes no room, so the window around may be narrower by its
        # width. QSplitter counts every panel's least width, folded or not: the editor could
        # never be narrower than 1296 px, wider than a 1080p laptop at 150% scaling has (1280)
        # -- folding the color panel freed nothing (2026-10-03).
        hint = super().minimumSizeHint()
        sizes = self.sizes()
        if any(sizes):
            for i in range(self.count()):
                if self.isCollapsible(i) and sizes[i] == 0:
                    panel = self.widget(i)
                    freed = max(panel.minimumWidth(), panel.minimumSizeHint().width())
                    hint.setWidth(max(0, hint.width() - freed))
        return hint

    def folded(self, index):
        """Whether panel `index` is folded away (never, before the splitter is laid out)."""
        sizes = self.sizes()
        return self.isCollapsible(index) and sizes[index] == 0 and any(sizes)

    def set_folded(self, index, fold):
        """Fold panel `index` away, its room going to the panel after it (or before, for
        the last), or bring it back at its own width, from the panel that gives it room."""
        sizes = self.sizes()
        if not any(sizes):
            # not laid out yet: the splitter shares out the room it gets by these
            sizes = [max(1, self.widget(i).minimumWidth(), self.widget(i).sizeHint().width())
                     for i in range(self.count())]
        if fold == (sizes[index] == 0) or (fold and not self.isCollapsible(index)):
            return
        other = index + 1 if index + 1 < len(sizes) else index - 1
        panel = self.widget(index)
        width = sizes[index] if fold else max(panel.minimumWidth(), panel.sizeHint().width())
        if fold:
            sizes[other] += width
            sizes[index] = 0
        else:
            sizes[other] = max(1, sizes[other] - width)
            sizes[index] = width
        self.setSizes(sizes)
        self._moved()

    def _moved(self):
        for i in range(1, self.count()):
            self.handle(i).update()
        # folded or brought back: the window around is told its new least width
        self.updateGeometry()
        self.changed.emit()


class _GripHandle(QSplitterHandle):
    """A PanelSplitter's handle: a column of dots down its middle, or an arrow pointing out
    while the panel before it is folded away; in the theme's blue under the pointer and
    while dragged. A double-click folds that panel away or brings it back."""

    def __init__(self, orientation, splitter):
        super().__init__(orientation, splitter)
        self._hot = False
        self._held = False

    def _before(self):
        """The index of the panel before this handle."""
        return self.splitter().indexOf(self) - 1

    def enterEvent(self, event):
        self._hot = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hot = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._held = event.button() == Qt.MouseButton.LeftButton
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._held = False
        self.update()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        splitter, before = self.splitter(), self._before()
        if event.button() == Qt.MouseButton.LeftButton and before >= 0 and splitter.isCollapsible(before):
            splitter.set_folded(before, not splitter.folded(before))
        event.accept()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        lit = self._hot or self._held
        cx, cy = self.width() / 2.0, self.height() / 2.0
        before = self._before()
        if before >= 0 and self.splitter().folded(before):
            # a panel folded away is easy to miss: a raised strip, and an arrow pointing the
            # way to drag it back out
            painter.setBrush(_EDGE)
            painter.drawRoundedRect(QRectF(0.0, cy - 36.0, float(self.width()), 72.0), 2.5, 2.5)
            painter.setBrush(_HIGHLIGHT if lit else _TEXT)
            painter.drawPolygon(QPolygonF([QPointF(cx - 1.5, cy - 8.0), QPointF(cx - 1.5, cy + 8.0),
                                           QPointF(cx + 2.5, cy)]))
            return
        painter.setBrush(_HIGHLIGHT if lit else _DIM)
        for k in range(-2, 3):
            painter.drawEllipse(QPointF(cx, cy + 7.0 * k), 1.4, 1.4)


# ---------------------------------------------------------------------------- HelpPopup

class HelpPopup(QFrame):
    """A few paragraphs of help that stay up until clicked away (a pop-up window): what the
    editor's "?" button shows. Under the help, optionally, a line of `note` and a button
    (`action`: (text, tip, slot), the pop-up closing once it is pressed)."""

    WIDTH = 440

    def __init__(self, parent, text="", note="", action=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("help_popup")
        self.setStyleSheet(f"QFrame#help_popup {{ background-color: {_RAISED.name()}; "
                           f"border: 1px solid {_EDGE.name()}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setFixedWidth(self.WIDTH)
        layout.addWidget(self.label)
        self.note = self.button = None
        if note or action:
            layout.addSpacing(6)
            row = QHBoxLayout()
            self.note = QLabel(note)
            self.note.setWordWrap(True)
            self.note.setStyleSheet(f"color: {_DIM.name()};")
            row.addWidget(self.note, 1)
            if action:
                words, tip, slot = action
                self.button = QPushButton(words)
                self.button.setToolTip(tip)
                self.button.clicked.connect(lambda _checked=False: (self.hide(), slot()))
                row.addWidget(self.button, 0, Qt.AlignmentFlag.AlignBottom)
            layout.addLayout(row)

    def text(self):
        return self.label.text()

    def set_text(self, text):
        self.label.setText(text)
        self.adjustSize()

    def show_under(self, widget):
        """Open just below `widget`, right edges together, kept on the screen."""
        show_popup_under(self, widget)


def show_popup_under(popup, widget):
    """Open `popup` (a pop-up window) just below `widget`, right edges together, kept on
    the screen -- above the widget when there is no room below it."""
    popup.adjustSize()
    corner = widget.mapToGlobal(widget.rect().bottomRight())
    x, y = corner.x() - popup.width() + 1, corner.y() + 3
    screen = widget.screen()
    if screen is not None:
        room = screen.availableGeometry()
        x = max(room.left(), min(x, room.right() - popup.width() + 1))
        if y + popup.height() > room.bottom() + 1:
            # no room below: above it instead
            y = max(room.top(), widget.mapToGlobal(widget.rect().topLeft()).y() - popup.height() - 3)
    popup.move(x, y)
    popup.show()


def measure_afresh(window):
    """Forget every size the layouts in `window` have worked out, so it is measured as it is
    now. For a window about to be first shown: Qt measures a widget's own layout again only
    when that widget is shown, after the window has taken its least size -- so a part a
    splitter measured while the window was being built, and that was hidden since, would
    still be counted in that size."""
    for widget in (window, *window.findChildren(QWidget)):
        # what a layout holds of each widget's sizes, and each layout's own sums
        widget.updateGeometry()
        if widget.layout() is not None:
            _invalidate(widget.layout())


def measure_layout_afresh(layout):
    """Forget the sizes `layout` and every layout inside it have worked out. Qt measures a
    layout inside another again only when its own widget is shown, not when a widget in it
    is shown or hidden: one that was, would be counted (or not) as it was before."""
    _invalidate(layout)


def _invalidate(layout):
    layout.invalidate()
    for i in range(layout.count()):
        inner = layout.itemAt(i).layout()
        if inner is not None:
            _invalidate(inner)


def screen_room(widget):
    """(width, height) of the screen space `widget`'s window may use -- the screen's, less
    its taskbars -- or None when there is no screen to ask."""
    screen = widget.screen()
    if screen is None:
        return None
    room = screen.availableGeometry()
    return room.width(), room.height()
