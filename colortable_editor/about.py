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
     "make a hard step or to delete it. Undo and Redo take back or redo any change. The ? button at the top "
     "right of the editor shows these tips while you work."),
    ("The picture",
     "The picture is a real storm drawn with your table as you work. Choose another one in the Picture box. "
     "Point at the picture to read its temperature; click it to choose the stop that colors that spot. "
     "Compare with shows another table beside yours. Tick Swipe to put both on one picture instead, yours "
     "left of a line and the other right of it, and drag the line across to see where they differ. Copy "
     "picture and Save picture… keep the whole picture in your table (or, with Swipe ticked, split where "
     "the line is)."),
    ("Several storms at once",
     "Press Several storms, above the picture, to see your table on several storms at once, all changing as "
     "you edit; One storm goes back to the one picture. Storms… chooses the storms: at first, the first six of "
     "your table's kind are ticked, and what you tick is kept for next time, as is the view you leave the "
     "editor in. The storms of the other kind (water vapor, for an infrared table) are listed after them. The "
     "storms work like the one picture: point at one to read its temperature, click it to choose the stop that "
     "colors that spot, and Shift-click to add a stop there. Double-click a storm to see it on its own. Copy "
     "grid and Save grid… keep the storms shown as one picture with the table's color scale, and Share card… "
     "makes a card of them to post."),
    ("More room for the picture",
     "Drag the divider between the color panel and the picture to the left to fold the color panel away; "
     "drag it back, or double-click it, to bring the panel back. The windows open the size you leave them. "
     "View → Reset window layout (or Reset layout under the editor's ? button) puts them back as they first "
     "opened."),
    ("Saving and sharing",
     "Save keeps the table. Export… writes the chosen table to a file, and Copy for sharing puts it on the "
     "clipboard, ready to paste into a message. Import… brings in tables a friend sent you, as files or as "
     "pasted text. In the editor, Share card… makes one picture to post: the table's name, its color scale and "
     "the storm drawn with it, with the storm's title and data line under it. Change the title if you like, "
     "then copy the card or save it as a PNG file. While you compare two tables, the card shows both, side by "
     "side or swiped as they are on screen."),
    ("Earlier versions",
     "Each time you save over one of your tables, the version it replaces is kept, the last 20 of them. In "
     "the editor, press History… (or right-click a table in the list and choose History…) to see them, "
     "newest first, with their colors and range. Choose one and press Restore this version (Open in the "
     "editor, from the list) to put it back in the editor; press Save to keep it, or Undo to take it back "
     "out."),
    ("Taking something back",
     "Deleted a table by mistake? Choose it under Recently deleted and press Restore. Its earlier versions "
     "come back with it."),
    ("If the program closes unexpectedly",
     "While a table in the editor has changes you haven't saved, a copy of them is kept a moment after each "
     "change. If the program closes before you save (it crashes, or the computer loses power), it offers "
     "those tables back the next time it starts: Open puts one back in the editor with your changes, ready "
     "to save; Discard throws the changes away; Decide later asks again next time."),
]


def kept_files_text(mark=""):
    """The files in the tables folder, in one sentence for How to use and the README (which
    marks each file name with `mark`, a backtick)."""
    def name(file):
        return f"{mark}{file}{mark}"
    return (f"Your tables are in {name('colortables.json')} in that folder, your starred tables in "
            f"{name('favorite_palettes.json')}, the recently deleted ones in {name('colortables.deleted.json')}, "
            f"the earlier versions of your tables (History) in {name('colortables.history.json')}, and how you "
            "left the windows (their size, whether the color panel is folded away, one storm or several, and the "
            f"storms ticked under Storms…) in {name('layout.json')}. "
            f"Changes not yet saved wait in the {name('drafts')} folder there while a table is open in the "
            "editor, and until you save or discard them if the program closed unexpectedly.")


def how_to_use_text():
    """The How to use window's text, a heading and a paragraph at a time."""
    return "\n\n".join(f"{heading}\n{text}" for heading, text in HOW_TO_USE)
