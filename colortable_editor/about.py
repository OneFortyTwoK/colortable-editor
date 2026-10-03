"""What the About and How to use windows say, as plain text (Qt-free, so a test or the
public repo's README can read the same words).

The color tables are credited nowhere, by the author's choice (2026-10-03); the sample pictures'
source is."""
from colortable_editor import APP_NAME, AUTHOR, LICENSE, SAMPLES_CREDIT, __version__

BLURB = "Make and change infrared and water-vapor color tables on real storm pictures, and share them."


def about_lines():
    """The About window's text, line by line."""
    return [f"{APP_NAME} {__version__}", f"by {AUTHOR}", LICENSE, "", BLURB, "", SAMPLES_CREDIT]


def about_text():
    return "\n".join(about_lines())


HOW_TO_USE = [
    ("The list",
     "The main window lists your own color tables first, then the built-in ones. A built-in table can't be "
     "changed, but you can copy it and change the copy."),
    ("Making a table",
     "Press New… for a table of your own, or choose a built-in table and press Duplicate (or Edit…, which "
     "opens a copy of it in the editor). Infrared tables are for pictures of cloud-top temperature; "
     "water-vapor tables are for water-vapor pictures."),
    ("In the editor",
     "The bar on the left is the table, warm at the top and cold at the bottom. Click the bar to add a "
     "stop, and drag a stop to move it. Choose a stop's color in the color panel. Right-click a stop to "
     "make a hard step or to delete it. Undo and Redo take back or redo any change."),
    ("The picture",
     "The picture is a real storm drawn with your table as you work. Choose another one in the Picture box. "
     "Point at the picture to read its temperature; click it to choose the stop that colors that spot. "
     "Compare with shows another table beside yours. Copy picture and Save picture… keep the whole picture "
     "in your table."),
    ("Saving and sharing",
     "Save keeps the table. Export… writes the chosen table to a file, and Copy for sharing puts it on the "
     "clipboard, ready to paste into a message. Import… brings in tables a friend sent you, as files or as "
     "pasted text."),
    ("Taking something back",
     "Deleted a table by mistake? Choose it under Recently deleted and press Restore."),
]


def how_to_use_text():
    """The How to use window's text, a heading and a paragraph at a time."""
    return "\n\n".join(f"{heading}\n{text}" for heading, text in HOW_TO_USE)
