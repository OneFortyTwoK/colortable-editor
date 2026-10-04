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
# Every sample's source, as samples.json credits each one: NOAA's open buckets for GOES and
# most VIIRS, NCEI's GridSat-GOES for Katrina 2005, NASA LAADS for the MODIS passes and Haiyan
# 2013's VIIRS, and JAXA G-Portal for Yutu 2018's SGLI -- whose terms (G-Portal terms of use,
# Article 7(2)) ask for that last sentence word for word.
SAMPLES_CREDIT = ("Sample pictures: NOAA GOES, and NOAA-20, NOAA-21 and Suomi NPP VIIRS, from NOAA Open Data "
                  "Dissemination; GOES-12 (Katrina 2005) from NOAA NCEI's GridSat-GOES; NASA Terra and Aqua "
                  "MODIS and Suomi NPP VIIRS (Haiyan 2013) from NASA LAADS DAAC; GCOM-C SGLI (Yutu 2018) from "
                  "JAXA G-Portal. Original data for this value added data product was provided by Japan "
                  "Aerospace Exploration Agency")
