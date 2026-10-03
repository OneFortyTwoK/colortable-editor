"""The Share card window: one picture to post, made from what the color table editor shows
-- the title, the table's color scale and the storm drawn with it, with the storm's title and
data line under it (tcviz.share_card) -- seen in a preview, with a title to change, then
copied or saved.

The editor opens it (ColortableEditorDialog.open_share_card) with the pictures it shows
(card_pictures: this table's picture, or both tables compared, side by side or swiped) and
the title to start from. Copy card and Save card… hand the card over the way the editor's
Copy picture and Save picture hand over a picture: through the editor's own clipboard and
save window (_put_on_clipboard, _ask_save_path), the wait cursor while it is drawn and
written, and a plain sentence in the status line saying what happened.
"""
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout,
)

from tcviz import edition, share_card

# the card as the window shows it, scaled down to fit; the card itself is about 1,500 to
# 1,800 pixels across
PREVIEW_SIZE = (460, 520)
# how long after the last key in the title box the card is drawn again
_REDRAW_MS = 150


def footer_text():
    """"Made with Colortable Editor" (or tcviz): the line at the bottom of every card."""
    return f"Made with {edition.app_name()}"


class ShareCardDialog(QDialog):
    def __init__(self, editor, pictures, title="", units="C"):
        """`editor` the ColortableEditorDialog it is opened from; `pictures` the card's
        pictures (share_card.CardPicture each); `title` the title to start from; `units` the
        tables' ("C", "kt", "dBZ")."""
        super().__init__(editor)
        self.editor = editor
        self.pictures = list(pictures)
        self.units = units
        self.card = None                # the card as last drawn (a PIL image)
        self._drawn_for = None          # (title, with captions) it was drawn for
        self.setWindowTitle("Share card")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_REDRAW_MS)
        self._timer.timeout.connect(self.redraw)
        self._build_ui(title)
        self.redraw()

    def _build_ui(self, title):
        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        self.preview = QLabel()
        self.preview.setFixedSize(*PREVIEW_SIZE)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("background-color: rgb(%d, %d, %d);" % share_card.PLATE)
        body.addWidget(self.preview)

        side = QVBoxLayout()
        intro = QLabel("One picture to post: the title, the table's color scale and the storm drawn with it.")
        intro.setWordWrap(True)
        side.addWidget(intro)
        side.addSpacing(8)
        side.addWidget(QLabel("Title:"))
        self.title_edit = QLineEdit(title)
        self.title_edit.setToolTip("The words across the top of the card. It starts as the table's name; "
                                   "change it to anything you like, or leave it empty for no title.")
        self.title_edit.textChanged.connect(lambda _text: self._timer.start())
        side.addWidget(self.title_edit)
        self.caption_check = QCheckBox("Include the storm's title and data line")
        self.caption_check.setChecked(True)
        has_lines = any(p.caption for p in self.pictures)
        self.caption_check.setEnabled(has_lines)
        self.caption_check.setToolTip(
            "Put the lines shown under the picture in the editor under the storm on the card: what the storm "
            "was, and where the picture came from." if has_lines else "This picture has no title or data line.")
        self.caption_check.toggled.connect(lambda _on: self.redraw())
        side.addWidget(self.caption_check)
        side.addSpacing(8)
        self.size_label = QLabel("")
        self.size_label.setWordWrap(True)
        side.addWidget(self.size_label)
        side.addSpacing(8)
        buttons = QHBoxLayout()
        self.copy_btn = QPushButton("Copy card")
        self.copy_btn.setToolTip("Copy the card, ready to paste into a message.")
        # Enter in the title box copies the card; it never opens the save window
        self.copy_btn.setDefault(True)
        self.copy_btn.clicked.connect(lambda _checked=False: self.copy_card())
        self.save_btn = QPushButton("Save card…")
        self.save_btn.setToolTip("Save the card as a PNG file.")
        self.save_btn.clicked.connect(lambda _checked=False: self.save_card())
        buttons.addWidget(self.copy_btn)
        buttons.addWidget(self.save_btn)
        buttons.addStretch()
        side.addLayout(buttons)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
        self.status.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        side.addWidget(self.status, 1)
        body.addLayout(side, 1)
        layout.addLayout(body, 1)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.resize(PREVIEW_SIZE[0] + 380, PREVIEW_SIZE[1] + 70)

    # ------------------------------------------------------------------ the card

    def _wanted(self):
        return self.title_edit.text().strip(), self.caption_check.isChecked() and self.caption_check.isEnabled()

    def redraw(self):
        """Draw the card for the title and the box as they are now, and show it."""
        self._timer.stop()
        title, captions = self._wanted()
        self.card = share_card.share_card(self.pictures, title, footer_text(), units=self.units, captions=captions)
        self._drawn_for = (title, captions)
        from tcviz_gui.pages.colortable_editor_dialog import _qimage
        shown = QPixmap.fromImage(_qimage(self.card)).scaled(
            *PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(shown)
        self.size_label.setText(f"The card is {self._size_words()}.")

    def current_card(self):
        """The card for the title and the box as they are now (drawn again first, when the
        title was changed since)."""
        if self.card is None or self._drawn_for != self._wanted():
            self.redraw()
        return self.card

    def _size_words(self):
        width, height = self.card.size
        return f"{width} × {height} pixels"

    def _say(self, text):
        self.status.setText(text)

    def copy_card(self):
        """Copy card: the card onto the clipboard. Whether the clipboard took it; the status
        line says either way."""
        from tcviz_gui.pages.colortable_editor_dialog import _busy, _qimage
        with _busy():
            card = self.current_card()
            taken = self.editor._put_on_clipboard(_qimage(card))
        if taken:
            # on Wayland, and on X without a clipboard keeper, what was copied goes when the
            # program that copied it closes
            self._say(f"Copied the card, {self._size_words()}. Paste it before you close {edition.app_name()}.")
        else:
            self._say("The card couldn't be put on the clipboard. Use Save card… to keep it as a file instead.")
        return taken

    def save_card(self):
        """Save card…: the card as a PNG file where the person says. The path saved to, or
        None."""
        from tcviz_gui.pages import colortable_editor_dialog as editor_module
        path = self.editor._ask_save_path(str(self.suggested_file()))
        if not path:
            return None
        path = Path(path)
        if path.suffix.lower() != ".png":
            path = path.with_name(path.name + ".png")
        with editor_module._busy():
            card = self.current_card()
            try:
                card.save(path, "PNG")
            except PermissionError:
                # Windows, most often: a picture of that name is open in a viewer
                self._say("The card couldn't be saved there: a file of that name is open in another program, or "
                          "the folder can't be written to. Nothing was saved.")
                return None
            except OSError as e:
                self._say(f"The card couldn't be saved there ({e.strerror or e}). Nothing was saved.")
                return None
        # Save picture… and Save card… offer the folder either saved to last
        editor_module._last_save_folder = path.parent
        self._say(f"Saved the card, {self._size_words()}, as {path}.")
        return path

    def suggested_file(self):
        """The file Save card… offers: named for the card's title, or the table when it has
        none ("mine_vs_bd05_card.png"), in the folder the editor's Save picture… offers."""
        folder = Path(self.editor._suggested_file()).parent
        words = self.title_edit.text().strip() or self.editor.name_edit.text().strip()
        stem = re.sub(r"[^\w.-]+", "_", words).strip("._") or "colortable"
        return folder / f"{stem}_card.png"
