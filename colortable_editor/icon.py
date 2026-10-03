"""The window icon, drawn rather than shipped as a file: a dark rounded square holding a
color table's bar, warm at the top, with two stop markers beside it. Drawn afresh at each
size, so the small ones stay crisp."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap

# crys10v4's stops, warm (top) to cold: a table every friend has
_STOPS = ("#000000", "#414754", "#ffffff", "#33ffff", "#010071", "#00fe24", "#ffff33", "#ff6333",
          "#b30000", "#cc0c89", "#5b3797", "#aedbfa", "#ffffff")
_SIZES = (16, 24, 32, 48, 64, 128, 256)


def pixmap(size):
    """The icon at `size` x `size` pixels."""
    image = QPixmap(size, size)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(45, 45, 45))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.18, size * 0.18)
    bar = QRectF(size * 0.30, size * 0.12, size * 0.30, size * 0.76)
    gradient = QLinearGradient(bar.topLeft(), bar.bottomLeft())
    for i, color in enumerate(_STOPS):
        gradient.setColorAt(i / (len(_STOPS) - 1), QColor(color))
    p.setBrush(gradient)
    p.drawRoundedRect(bar, size * 0.04, size * 0.04)
    p.setBrush(QColor(220, 220, 220))
    for fraction in (0.30, 0.62):
        y = bar.top() + bar.height() * fraction
        tip = bar.right() + size * 0.04
        marker = QPainterPath(QPointF(tip, y))
        marker.lineTo(tip + size * 0.16, y - size * 0.09)
        marker.lineTo(tip + size * 0.16, y + size * 0.09)
        marker.closeSubpath()
        p.drawPath(marker)
    p.end()
    return image


def icon():
    """A QIcon holding the icon at every size a desktop asks for."""
    out = QIcon()
    for size in _SIZES:
        out.addPixmap(pixmap(size))
    return out
