# Colortable Editor

Make and change infrared and water-vapor color tables on real storm pictures, and share them.

Colortable Editor is a free program for Windows and Linux. You build an infrared or
water-vapor color table stop by stop, and see it on a real storm as you go: the cold cloud
tops around an eye, the rain bands, the dry slots in water vapor. When you like it, send it
to a friend as a file or as a few lines of text to paste.

![The main window: your tables first, then the six built-in ones](docs/main-window.png)

![The editor: a copy of a built-in table, drawn on Hurricane Melissa](docs/editor.png)

![A 375-meter VIIRS picture, zoomed in on the eye](docs/viirs.png)

## Download

Get the newest version from the [Releases page](https://github.com/OneFortyTwoK/colortable-editor/releases). It runs on 64-bit
Windows 10 and 11, and on 64-bit Linux from about 2022 on (Ubuntu 22.04, Debian 12, Fedora 36,
Linux Mint 21 or newer).

### Windows

1. Download `ColortableEditor.exe`.
2. Double-click it.

The program isn't signed, so Windows may say **"Windows protected your PC"**. Click
**More info**, then **Run anyway**. Windows asks only the first time.

It takes a few seconds to start while the program unpacks itself, and the very first start
can take a little longer while Windows checks it. It is one file: there is nothing to
install, and you can keep it in any folder.

### Linux

1. Download `ColortableEditor-x86_64.AppImage`.
2. Mark it executable: right-click it, choose **Properties**, then **Permissions**, and
   tick **Allow executing file as program**. (Or, in a terminal:
   `chmod +x ColortableEditor-x86_64.AppImage`.)
3. Double-click it.

If it won't start, open a terminal in its folder and run
`./ColortableEditor-x86_64.AppImage --appimage-extract-and-run`.

## How to use

**The list.** The main window lists your own color tables first, then the built-in ones. A built-in table can't be changed, but you can copy it and change the copy.

**Making a table.** Press New… for a table of your own, or choose a built-in table and press Duplicate (or Edit…, which opens a copy of it in the editor). Infrared tables are for pictures of cloud-top temperature; water-vapor tables are for water-vapor pictures.

**In the editor.** The bar on the left is the table, warm at the top and cold at the bottom. Click the bar to add a stop, and drag a stop to move it. Choose a stop's color in the color panel. Right-click a stop to make a hard step or to delete it. Undo and Redo take back or redo any change.

**The picture.** The picture is a real storm drawn with your table as you work. Choose another one in the Picture box. Point at the picture to read its temperature; click it to choose the stop that colors that spot. Compare with shows another table beside yours. Copy picture and Save picture… keep the whole picture in your table.

**Saving and sharing.** Save keeps the table. Export… writes the chosen table to a file, and Copy for sharing puts it on the clipboard, ready to paste into a message. Import… brings in tables a friend sent you, as files or as pasted text.

**Taking something back.** Deleted a table by mistake? Choose it under Recently deleted and press Restore.

**Help → How to use** in the program says the same.

## Where your tables are kept

| System | Folder |
|---|---|
| Windows | `%APPDATA%\Colortable Editor` (for example `C:\Users\you\AppData\Roaming\Colortable Editor`) |
| Linux | `~/.config/Colortable Editor` |

Your tables are in `colortables.json` in that folder, your starred tables in
`favorite_palettes.json` and the recently deleted ones in `colortables.deleted.json`.
**Help → How to use** shows the exact folder. Nothing is saved anywhere else.

To keep your tables somewhere else (on a USB stick, say), set the environment variable
`COLORTABLE_EDITOR_CONFIG_DIR` to that folder before you start the program.

## Sharing tables

- **Copy for sharing** puts the chosen table on the clipboard as a few lines of text.
  Paste it into a message, a forum post or a chat.
- **Export…** saves the chosen table to a file you can send, and **Export many…** saves
  several tables in one file.
- **Import…** brings tables in: files a friend sent, or text they pasted. It also reads GMT
  `.cpt` files, MetPy `.tbl` files and plain lists of colors.

## The sample storms

The program comes with 16 real satellite pictures of storms to try your tables on, each the 13-degree box around the storm. Choose one in the editor's **Picture** box.

**Infrared:**

- **Melissa, 2025** (Atlantic; GOES-19 ABI, 2.3 km pixels): Melissa at her 165 kt peak, five hours before landfall in Jamaica: a clear, round eye inside a ring of -80 °C cloud tops.
- **Polo, 2026** (East Pacific; NOAA-20 VIIRS, 375 m pixels): Polo at its 155 kt peak, seen almost straight down by NOAA-20: a warm eye near +23 °C in a solid ring of -70 to -80 °C cloud, at 375 m.
- **Milton, 2024** (Atlantic; GOES-16 ABI, 2.6 km pixels): Milton at its 155 kt peak in the Gulf: a pinhole eye only about 12 km across, a few pixels wide, inside -80 °C tops.
- **Nolo, 2026** (Central Pacific; NOAA-20 VIIRS, 375 m pixels): Nolo at its 135 kt peak southwest of Hawaii, at night: a wide, round eye about 50 km across, with eyewall tops as cold as -89 °C, at 375 m.
- **Genevieve, 2026** (East Pacific; GOES-18 ABI, 2.6 km pixels): Genevieve near its 140 kt peak: a clear eye about 35 km across inside a thick ring of -70 to -76 °C cloud tops.
- **Lala, 2026** (Central Pacific; GOES-18 ABI, 3.0 km pixels): Lala at its 115 kt peak west of Hawaii: a small eye about 33 km across in a compact ring of cloud tops no colder than -74 °C.
- **Rachel, 2026** (East Pacific; GOES-18 ABI, 2.9 km pixels): Tropical Storm Rachel at 60 kt, six hours before it became a hurricane: a big central dense overcast with tops below -90 °C, the center near its northern edge.
- **Katrina, 2005** (Atlantic; GOES-12 Imager, 4.5 km pixels): Katrina at its 150 kt peak in the Gulf, the day before landfall in Louisiana: a clear eye in a wide ring of cold cloud, from GOES-12 on NCEI's 4 km GridSat-GOES grid.
- **Elida, 2026** (East Pacific; GOES-18 ABI, 2.4 km pixels): Tropical Storm Elida at 55 kt, ragged and sheared: its center sits under ragged cloud between bursts of thunderstorms, the biggest, with tops near -88 °C, about 300 km to the south-southwest.
- **Five (later Edouard), 2026** (Atlantic; GOES-19 ABI, 2.9 km pixels): Tropical Depression Five off Louisiana at 30 kt, six hours before it became Tropical Storm Edouard: a loose curl of showers and a few small thunderstorms near the center, with no organized core.
- **Fausto, 2026** (Central Pacific; GOES-18 ABI, 2.7 km pixels): What was left of Fausto north of Hawaii, hours after it became a 30 kt remnant low: a bare swirl of warm low cloud with no thunderstorms at its center.

**Water vapor:**

- **Melissa, 2025** (Atlantic; GOES-19 ABI, 2.3 km pixels): Melissa at her 165 kt peak in upper-level water vapor: the eye in a broad moist shield, with outflow cirrus fanning out to its north and west.
- **Milton, 2024** (Atlantic; GOES-16 ABI, 2.6 km pixels): Milton at 135 kt, about 12 hours before landfall in Florida: dry air to its west and south wrapping in toward the core.
- **Genevieve, 2026** (East Pacific; GOES-18 ABI, 2.7 km pixels): Genevieve at 115 kt and strengthening fast, in mid-level water vapor: a small eye, moist spiral bands and drier air to the northwest.
- **Lowell, 2026** (Central Pacific; GOES-18 ABI, 2.3 km pixels): Lowell at 130 kt in lower-level water vapor: a compact moist core with an eye, surrounded by very dry air.
- **Katrina, 2005** (Atlantic; GOES-12 Imager, 4.5 km pixels): Katrina at her 150 kt peak in water vapor, from GOES-12 on NCEI's 4 km GridSat-GOES grid: the eye in a broad moist shield, dry air to the west.

Sample pictures: NOAA GOES and VIIRS, from NOAA Open Data Dissemination; Katrina 2005 from NOAA NCEI's GridSat-GOES.

## Build it yourself

You need [Python](https://www.python.org/downloads/) 3.12 or newer, and this folder
(the green **Code** button, then **Download ZIP**, or `git clone https://github.com/OneFortyTwoK/colortable-editor.git`).
In a terminal, in the folder:

```
python -m venv .venv
.venv\Scripts\activate            (on Windows)
source .venv/bin/activate          (on Linux)
pip install -r requirements.txt
python -m colortable_editor
```

To run the tests: `pip install pytest`, then `python -m pytest`.

To build the program the way the Releases page has it:

```
pip install pyinstaller==6.22.3
pyinstaller colortable_editor.spec --noconfirm
```

On Windows that makes `dist\ColortableEditor.exe`. On Linux it makes the folder
`dist/ColortableEditor/`, and `bash packaging/build_appimage.sh` then wraps it into
`ColortableEditor-x86_64.AppImage`. GitHub builds both the same way when a version is tagged
(`.github/workflows/release.yml`).

## License

Colortable Editor is free software under the MIT License (see [LICENSE](LICENSE)), made by
OneFortyTwoK. It is built with Qt (through PySide6), numpy, matplotlib, Pillow and others, and
saved pictures are lettered in JetBrains Mono: their licenses are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
