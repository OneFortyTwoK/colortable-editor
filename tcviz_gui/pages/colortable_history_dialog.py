"""The History window: the earlier versions of one of your color tables, newest first --
one is kept each time the table is saved over (tcviz.colortable_history) -- each with its
colors, range and number of stops, and a way to put one back.

It changes nothing itself. Opened from the editor, "Restore this version" closes it with
the version chosen (`chosen`), and the editor loads it as one undo step; nothing is saved
until Save there. Opened from the Manage window's right-click menu, "Open in the editor"
does the same with the table opened in the editor first.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget,
)

from tcviz import colortable_history, colortable_library, user_colortables
from tcviz.colortable_model import UNIT_LABELS
from tcviz_gui import swatches
from tcviz_gui.colortable_widgets import degrees_text

# the color bar of the chosen version: warm (or high) end on the left, as the Manage
# window's strips read
BAR_WIDTH, BAR_HEIGHT = 340, 30
_ACTIONS = {
    "restore": ("Restore this version", "Load this version into the editor. Nothing is saved until you press Save, "
                "and Undo takes it back out."),
    "open": ("Open in the editor", "Open the table in the editor with this version loaded. Nothing is saved until "
             "you press Save there."),
}


def when_in_words(when, now=None):
    """A version's time inside a sentence: "today at 14:32", "yesterday at 21:05", "on
    2026-09-30 at 10:11" (colortable_history.when_text, in lowercase where it belongs)."""
    day, _space, clock = colortable_history.when_text(when, now).rpartition(" ")
    if not day:
        return clock
    if day in ("Today", "Yesterday"):
        return f"{day.lower()} at {clock}"
    return f"on {day} at {clock}"


class ColortableHistoryDialog(QDialog):
    def __init__(self, parent=None, name="", action="restore", now=None):
        """The versions of your table `name`. `action` is "restore" (from the editor) or
        "open" (from the Manage window): what the button under the list says and does.
        `now` is for tests: the clock "Today" and "Yesterday" are read against."""
        super().__init__(parent)
        if action not in _ACTIONS:
            raise ValueError(f"action must be one of {sorted(_ACTIONS)}")
        self.name = name
        self.chosen = None              # the version to load, once the button is pressed
        self._now = now
        self.versions = colortable_history.versions(name)
        self.setWindowTitle(f"History -- {name}")
        self._build_ui(action)
        self._fill()
        if self.versions:
            self.resize(640, 360)
        else:
            # nothing to list: only the sentence saying so
            self.resize(560, self.sizeHint().height())

    def _build_ui(self, action):
        layout = QVBoxLayout(self)
        self.heading = QLabel("")
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)

        body = QHBoxLayout()
        self.version_list = QListWidget()
        self.version_list.setMinimumWidth(200)
        self.version_list.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.version_list.currentRowChanged.connect(self._show)
        self.version_list.itemDoubleClicked.connect(lambda _item: self._on_action())
        body.addWidget(self.version_list)

        self.details = QWidget()
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        details.addWidget(self.title)
        self.when_label = QLabel("")
        self.when_label.setWordWrap(True)
        details.addWidget(self.when_label)
        details.addSpacing(6)
        self.bar = QLabel()
        self.bar.setFixedSize(BAR_WIDTH, BAR_HEIGHT)
        self.bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        details.addWidget(self.bar)
        ends = QHBoxLayout()
        self.top_label = QLabel("")
        self.bottom_label = QLabel("")
        self.bottom_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        ends.addWidget(self.top_label)
        ends.addStretch()
        ends.addWidget(self.bottom_label)
        ends.setContentsMargins(0, 0, 0, 0)
        ends_holder = QWidget()          # the ends as wide as the bar, not the window
        ends_holder.setLayout(ends)
        ends_holder.setFixedWidth(BAR_WIDTH)
        details.addWidget(ends_holder)
        details.addSpacing(6)
        self.facts = QLabel("")
        self.facts.setWordWrap(True)
        details.addWidget(self.facts)
        self.description = QLabel("")
        self.description.setWordWrap(True)
        details.addWidget(self.description)
        details.addStretch()
        body.addWidget(self.details, 1)
        layout.addLayout(body, 1)

        row = QHBoxLayout()
        text, tip = _ACTIONS[action]
        self.action_btn = QPushButton(text)
        self.action_btn.setToolTip(tip)
        self.action_btn.setDefault(True)
        self.action_btn.clicked.connect(self._on_action)
        row.addWidget(self.action_btn)
        row.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)

    def _fill(self):
        for version in self.versions:
            self.version_list.addItem(f"{version.label} · {colortable_history.when_text(version.when, self._now)}")
        if self.versions:
            self.heading.setText(f"Each time you save over '{self.name}', the version it replaces is kept here, "
                                 f"newest first (the last {colortable_history.KEEP}). Choose one to see it.")
            self.version_list.setCurrentRow(0)
        else:
            self.heading.setText(f"'{self.name}' has no saved versions yet. Each time you save over it, the version "
                                 "it replaces is kept here.")
            self.version_list.hide()
            self.details.hide()
            self._show(-1)
            self.action_btn.hide()

    def current(self):
        """The version chosen in the list, or None."""
        row = self.version_list.currentRow()
        return self.versions[row] if 0 <= row < len(self.versions) else None

    def _show(self, row):
        version = self.versions[row] if 0 <= row < len(self.versions) else None
        for label in (self.title, self.when_label, self.top_label, self.bottom_label, self.facts, self.description):
            label.setText("")
        self.bar.clear()
        self.action_btn.setEnabled(False)
        if version is None:
            return
        entry = version.entry
        self.title.setText(f"Version {version.label}")
        self.when_label.setText(f"The table as it was until you saved over it {when_in_words(version.when, self._now)}.")
        try:
            cmap = user_colortables.build_cmap(entry)[0]
        except Exception:
            self.bar.setText("This version can't be drawn.")
            return
        self.bar.setPixmap(swatches.colormap_swatch(cmap, BAR_WIDTH, BAR_HEIGHT))
        unit = UNIT_LABELS.get(entry["units"], entry["units"])
        self.top_label.setText(f"{degrees_text(entry['vmax'])} {unit}")
        self.bottom_label.setText(f"{degrees_text(entry['vmin'])} {unit}")
        count = len(entry["stops"])
        facts = [colortable_library.KIND_LABELS[colortable_library.entry_kind(entry)],
                 f"{degrees_text(entry['vmin'])} to {degrees_text(entry['vmax'])} {unit}",
                 f"{count} stop{'s' if count != 1 else ''}"]
        if entry["levels"] != user_colortables.DEFAULT_LEVELS:
            facts.append(f"drawn with {entry['levels']} colors")
        self.facts.setText(" · ".join(facts))
        self.description.setText(f"Description: {entry['description']}" if entry["description"]
                                 else "No description.")
        self.action_btn.setEnabled(True)

    def _on_action(self):
        version = self.current()
        if version is None or not self.action_btn.isEnabled():
            return
        self.chosen = version
        self.accept()
