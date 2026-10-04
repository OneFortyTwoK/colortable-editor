"""Running a window to its end (QDialog.exec) -- and letting it go after.

When exec() returns, PySide6 hands the window to Python. A lambda connected to one of its
own widgets that uses `self` (the editor has dozens) then holds it in a loop Python cannot
see, so a closed window stayed in memory, pictures and all, for as long as the program ran:
13 MB for each editor opened on the first sample, far more on a VIIRS one (measured
2026-10-03). run() deletes it once the event loop has control back.

That is always after the code that opened it has read what it needs: Qt deletes an object
asked for with deleteLater() only when control returns to the event loop it was asked from,
never in a window opened after it from the same code -- so a handler can run a window, open
a file dialog, then read the first window's choices (Manage's Export many does).
"""


def run(dialog):
    """dialog.exec(); the window is deleted once the event loop has control back."""
    try:
        return dialog.exec()
    finally:
        dialog.deleteLater()
