"""Colortable Editor: tcviz's color table editor as a program of its own, for infrared and
water-vapor color tables -- make one, edit it on a real storm picture, and share it.

It is the same editor tcviz has (tcviz_gui.pages.colortable_editor_dialog and the Manage,
Import and Add windows), run as the "colortable-editor" edition (tcviz.edition): six
built-in tables of its own (builtin_tables.json, written from tcviz's by
tools/make_editor_builtins.py), the bundled sample pictures, and the person's tables kept in
a folder of its own. Start it with `python -m colortable_editor`.

Importing this package changes nothing; only colortable_editor.__main__ switches the edition.
"""
__version__ = "1.0.0"

APP_NAME = "Colortable Editor"
AUTHOR = "OneFortyTwoK"
LICENSE = "MIT License"
# Katrina 2005's two samples are not from Open Data Dissemination but from NCEI's GridSat-GOES
# (vendor/editor_samples/samples.json), so the credit says so.
SAMPLES_CREDIT = ("Sample pictures: NOAA GOES and VIIRS, from NOAA Open Data Dissemination; "
                  "Katrina 2005 from NOAA NCEI's GridSat-GOES")
