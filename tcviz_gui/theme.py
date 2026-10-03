"""Dark theme for the whole app -- Fusion style + a dark QPalette.

Fusion specifically (not whatever native style Linux/Windows would otherwise pick)
because it's the one Qt style that reliably respects a custom QPalette across every
widget on both platforms -- native styles often ignore palette colors for some
widgets (e.g. Windows' native style keeps text fields white regardless of Base/Text
palette roles), which would leave a half-dark, half-native-light app. Fusion is
built into Qt itself, no extra dependency.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory


def apply_dark_theme(app: QApplication):
    app.setStyle(QStyleFactory.create("Fusion"))

    palette = QPalette()
    window = QColor(45, 45, 45)
    base = QColor(30, 30, 30)
    alt_base = QColor(45, 45, 45)
    text = QColor(220, 220, 220)
    disabled_text = QColor(127, 127, 127)
    highlight = QColor(42, 130, 218)

    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, alt_base)
    palette.setColor(QPalette.ColorRole.ToolTipBase, window)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    # Placeholder text (e.g. OptionsPage's "blank = <default handle>" hint in the
    # Handle/watermark field) -- without this it falls back to a dark default that's
    # near-unreadable on the dark Base, so match it to the same light text color the rest
    # of the UI uses.
    palette.setColor(QPalette.ColorRole.PlaceholderText, text)
    palette.setColor(QPalette.ColorRole.Button, window)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 90, 90))
    palette.setColor(QPalette.ColorRole.Link, highlight)
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)

    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled_text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled_text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled_text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor(80, 80, 80))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.HighlightedText, disabled_text)

    app.setPalette(palette)
    # QToolTip doesn't reliably pick up the palette on its own on every platform --
    # a small stylesheet nudge just for tooltips (OptionsPage's own palette
    # checkboxes rely on tooltips for descriptions -- see colormaps.PALETTE_
    # DESCRIPTIONS -- these would otherwise render as unreadable dark-on-dark).
    app.setStyleSheet("QToolTip { color: #dcdcdc; background-color: #2d2d2d; border: 1px solid #555555; }")
