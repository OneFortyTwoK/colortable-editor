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
  one is clicked. A PictureView holds the zoom, so two panes can share one.

Values are in the table's own units (the model's unit_label: °C, kt or dBZ).

None of them changes the table itself. Each asks the editor (the dialog), which makes the
change as one step its Undo can take back and then tells every piece to show it again.
"""
import colorsys
import math

import numpy as np
from matplotlib.colors import to_hex, to_rgb
from PySide6.QtCore import QAbstractTableModel, QEvent, QModelIndex, QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QColorDialog, QDoubleSpinBox, QGridLayout, QGroupBox, QHeaderView,
    QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy, QStyledItemDelegate, QTableView, QToolTip, QVBoxLayout,
    QWidget,
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
    (PreviewSource.saved_pixel), and zoomed in to every pixel, the pixel itself."""

    hovered = Signal(object)                # (row, col) of the saved picture, or None
    clicked = Signal(int, int, bool)        # row, col of the saved picture, Shift held
    shown = Signal()                        # the zoom, the place, the picture or the pane's size changed

    def __init__(self, parent=None, interactive=False, view=None):
        super().__init__(parent)
        self._index = None
        self._lut = None
        self._buffer = None
        self._image = None
        self._dirty = False
        self._stride = 1
        self._full_shape = None
        self._full_rows = None
        # (bounds, row image) of the saved picture's own pixels around the part in view,
        # when zoomed in to every pixel, and its colors as last looked up
        self._window = None
        self._window_buffer = None
        self._window_image = None
        self._window_dirty = False
        self._message = ""
        self._press = None              # (button, where, modifiers), from a press to its release
        self._dragged_to = None         # where a drag has got to, once the press became one
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
        if index is not self._index or stride != self._stride or full_shape != self._full_shape:
            self._index, self._stride, self._full_shape = index, stride, full_shape
            self._dirty = True
            # the window belongs to the rows it was worked out with; the same rows (one
            # range and table size, for the same picture) mean the same window
            self._window = None
            self.update()
            self.shown.emit()
        self._full_rows = full_rows

    def set_lut(self, lut):
        if lut is self._lut:
            return
        self._lut = lut
        self._dirty = True
        self._window_dirty = True
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
        if self._dirty:
            self._dirty = False
            if self._index is None or self._lut is None:
                self._image = self._buffer = None
            else:
                from tcviz.colortable_preview import apply
                self._buffer = apply(self._index, self._lut)
                h, w = self._index.shape
                self._image = QImage(self._buffer.data, w, h, 4 * w, QImage.Format.Format_RGBA8888)
        return self._image

    def rgba(self):
        """The RGBA bytes of the picture as handed over (H, W, 4), or None."""
        self.image()
        return self._buffer

    def detail_rgba(self):
        """((top, bottom, left, right), RGBA bytes) of the saved picture's own pixels the
        pane draws, zoomed in to every pixel -- a window a little wider than the view --
        else None."""
        if not self.full_detail() or self._detail_window() is None:
            return None
        return self._window[0], self._window_buffer

    @property
    def picture_shape(self):
        """(rows, columns) of the picture as handed over, or None."""
        return None if self._index is None else self._index.shape

    @property
    def full_shape(self):
        """(rows, columns) of the saved picture, or None."""
        return None if self._index is None else self._full_shape

    @property
    def stride(self):
        return self._stride

    # ------------------------------------------------------------- where it is drawn

    def fit_scale(self):
        """Screen pixels across one pixel of the saved picture at Fit (None without a picture)."""
        if self._index is None:
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
        if self._index is None:
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
        if self._index is None or self._stride == 1 or self._full_rows is None or self.view.zoom <= 1.0:
            return False
        window = self.visible_window()
        if window is None:
            return False
        top, bottom, left, right = window
        return (bottom - top) * (right - left) <= self._index.size

    def _detail_window(self):
        """((top, bottom, left, right), QImage) of the saved picture's own pixels around
        the part in view, or None. Worked out a little wider than the view, so a short drag
        needs no new one, and again only when the view leaves it or it holds far more than
        the view (after a zoom in): an edit looks up only these colors."""
        visible = self.visible_window()
        if visible is None or self._lut is None:
            return None
        top, bottom, left, right = visible
        area = (bottom - top) * (right - left)
        held = self._window[0] if self._window is not None else None
        if held is None or not (held[0] <= top and bottom <= held[1] and held[2] <= left and right <= held[3]
                                and (held[1] - held[0]) * (held[3] - held[2]) <= 4 * area):
            # a margin of up to a quarter of the view on each side, the whole window no
            # more pixels than the thinned picture
            margin = min(0.25, max(0.0, (math.sqrt(self._index.size / area) - 1) / 2))
            mh, mw = int((bottom - top) * margin), int((right - left) * margin)
            h, w = self._full_shape
            bounds = (max(0, top - mh), min(h, bottom + mh), max(0, left - mw), min(w, right + mw))
            try:
                rows = self._full_rows(*bounds)
            except (ValueError, MemoryError):
                return None
            self._window = (bounds, rows)
            self._window_dirty = True
        if self._window_dirty or self._window_image is None:
            from tcviz.colortable_preview import apply
            rows = self._window[1]
            self._window_buffer = apply(rows, self._lut)
            h, w = rows.shape
            self._window_image = QImage(self._window_buffer.data, w, h, 4 * w, QImage.Format.Format_RGBA8888)
            self._window_dirty = False
        return self._window[0], self._window_image

    def pixel_at(self, point):
        """(row, col) of the saved picture's pixel under `point` (in the pane), or None:
        off the picture, in the bars beside it, or while words are shown instead. On a
        thinned picture it is the pixel the one shown was kept from, until the pane is
        zoomed in to every pixel."""
        target = None if self._message else self.picture_rect()
        if target is None or target.isEmpty():
            return None
        detail = self.full_detail()
        h, w = self._full_shape if detail else self._index.shape
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
        if self._index is None or self.view.zoom <= 1.0:
            return
        size = self.pixel_size()
        cx, cy = self._center()
        self.view.set(self.view.zoom, self._clamped(cx - delta.x() / size, cy - delta.y() / size, size))

    def _show_cursor(self):
        if self._dragged_to is not None:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self.interactive:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self.view.zoom > 1.0:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    # ------------------------------------------------------------- the mouse

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if self._message or self.picture_rect() is None or not steps:
            event.ignore()
            return
        self.zoom_by(WHEEL_STEP ** steps, event.position())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() in _DRAG_BUTTONS and not self._message and self._index is not None:
            # a press is a click or the start of a drag: which one, the pointer says
            self._press = (event.button(), event.position(), event.modifiers())
            self._dragged_to = None
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        point = event.position()
        if self._press is not None:
            start = self._press[1]
            if self._dragged_to is None and (point - start).manhattanLength() >= DRAG_START:
                self._dragged_to = start
                self._show_cursor()
            if self._dragged_to is not None:
                self.pan_by(point - self._dragged_to)
                self._dragged_to = point
        if self.interactive:
            self.hovered.emit(self.pixel_at(point))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._press is None or event.button() != self._press[0]:
            super().mouseReleaseEvent(event)
            return
        button, start, modifiers = self._press
        dragged = self._dragged_to is not None
        self._press = self._dragged_to = None
        self._show_cursor()
        event.accept()
        if not dragged and button == Qt.MouseButton.LeftButton and self.interactive:
            pixel = self.pixel_at(start)
            if pixel is not None:
                self.clicked.emit(pixel[0], pixel[1], bool(modifiers & Qt.KeyboardModifier.ShiftModifier))

    def leaveEvent(self, event):
        if self.interactive:
            self.hovered.emit(None)
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.shown.emit()

    # ------------------------------------------------------------- painting

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(18, 18, 18))
        if self._message or self._index is None or self._lut is None:
            painter.setPen(_TEXT)
            painter.drawText(QRectF(self.rect()).adjusted(12, 12, -12, -12),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._message)
            painter.end()
            return
        target = self.picture_rect()
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
        h, w = self._index.shape
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
