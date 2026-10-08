"""tcviz's own saved pictures: save_raw writes the values a picture was drawn from (and what
it shows) beside the PNG, load_raw reads them back -- the viewer's box analysis, the color
table editor's previews and its sample pictures all read these files. save_rgb does the
same for a color picture (true color, geocolor...), which has colors rather than values.

Moved out of tcviz.composites.rawio, which re-exports both, so the color table editor can
read a saved picture without loading the composites package (satpy, pyresample, dask...):
this module needs only numpy.

The satellite's own pixels (since 2026-10-04)
---------------------------------------------
A picture drawn while the engine keeps pixels (composites.pixels.capture_pixels: every pass
the app or the command line draws, not a video's frames) is saved with the satellite's own
pixels for its whole box, so the app's viewer (tcviz_gui/pixel_viewer.py) redraws each
pixel's exact shape at any zoom rather than the picture's cells. They are in the same data.npz, under the keys below;
load_pixels reads them into a PixelSet. A file saved before them, or a picture not drawn as
pixels or footprints (a grid of nearest pixels, a gridded archive, MRF-averaged
microwave), has none of the keys, and load_pixels answers None.

    pixels_version       int, 1
    pixels_product       "values": one value per pixel, the quantity and units of bt_kelvin
                         (K, reflectance, ...; "units" says), to be colored by a table;
                         "rgb": the color each pixel shows in the picture (save_rgb)
    pixels_pieces        int N, the pieces below, numbered 0 .. N-1
    pixels_box_crs       str, WKT of the picture's own map (an equidistant cylindrical
                         centered on the storm, meters): the map the drawing gave each
                         pixel its shape on
    pixels_box_extent    float64 (4,), (x0, y0, x1, y1) of the picture in that map
    pixels_box_shape     int64 (2,), (rows, columns) of bt_kelvin / rgb

and for each piece i, the window of one satellite grid the picture was drawn from:

    pixels{i}_kind           "swath" (positions given) or "grid" (a projected grid:
                             geostationary, positions from its definition)
    pixels{i}_band           str, the band(s) drawn ("I05", "ch4", "C13"; "Oa08/..." for rgb)
    pixels{i}_values         "values": float32 (rows, cols), NaN where the sample has no
                             value; "rgb": uint8 (rows, cols, 3)
    pixels{i}_usable         bool (rows, cols): the samples that own their shape. A sample
                             not usable paints nothing (no position, no value -- VIIRS's
                             bow-tie deletion -- or, for rgb, no cell of the picture left
                             to it); its neighbors' shapes cover its ground as drawn.
    pixels{i}_rows_per_scan  int: detector rows one scan sweeps (VIIRS 16 / 32, MODIS 10 /
                             20 / 40, METimage 24 ...; 1 for none). Row 0 is a scan's
                             first row. Where scans overlap (the bow-tie) the later scan is
                             drawn over the earlier.
  swath only:
    pixels{i}_lon, _lat      float32 (rows, cols), degrees: the positions the pixels were
                             drawn at (SLSTR's and FIDUCEO GAC's zigzag smoothed, as
                             drawn); NaN for none. Longitudes as the file gives them --
                             project into pixels_box_crs, which has no seam near the box.
  grid only:
    pixels{i}_grid_crs       str, WKT of the satellite's grid
    pixels{i}_grid_extent    float64 (4,), the whole grid's extent in it
    pixels{i}_grid_shape     int64 (2,), the whole grid's (rows, columns)
    pixels{i}_grid_window    int64 (4,), (row0, row1, col0, col1): the window the piece is

A pixel's shape is its midpoint quadrilateral on pixels_box_crs's map -- corners halfway to
its neighbors along the scan and along the track (within its own scan where rows_per_scan >
1) -- as swath._quad_index draws it and tcviz/pixel_mesh.py works it out again for the
viewer. Pieces are in the order they
were drawn; where two cover the same ground (a mosaic of granules, frames joined along the
track), the first one drawn shows. A color picture keeps only its finest grid
(composites.pixels.rgb_keys says why).

A close-up (tcviz.engine.closeup) says so: "closeup" True. Its keys are the same.
"""
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


def save_raw(path, arr, storm_lat, storm_lon, box_deg, satellite, instrument, band, obs_time_utc,
             units="K", palette="", sat_key="", sensor="", extra=None, area=None):
    """Save the calibrated physical-value array + geo/metadata for the viewer's
    box-select/highest-value analysis -- the rendered PNGs are colorized and can't
    be un-mapped back to physical values, so this is what view_pass.py actually
    loads. Despite the array's own on-disk key (bt_kelvin, kept for backward
    compatibility with already-saved passes), it holds whatever physical quantity
    this particular sensor/product actually produces, not always a brightness
    temperature -- units says what (default "K", matching every IR/microwave
    brightness-temp caller that doesn't pass one explicitly).

    sat_key/sensor are this project's own internal identifiers for the pass (e.g.
    "goes19"/"goes"), stored so a rendered frame can be refreshed to the latest scan
    later; see render_latest_geo. Left empty for sensors that have no live feed.

    palette, if given, is the ONE dedicated color scale this product always renders
    with (e.g. the SAR wind table for every wind-speed sensor) -- tcviz.viewer/view_pass.py
    use it to default the viewer to the same look the rendered PNG already has,
    instead of always guessing "grayscale". Left as "" (the default) for sensors
    that render the SAME raw array under multiple user-chosen palettes (every IR/
    microwave band, MIMIC-TPW2) -- there's no single "right" answer to default to
    there, so the viewer keeps falling back to grayscale for those, same as before.

    extra, if given, is {name: array} saved alongside under those names -- e.g. a
    product's own quality mask ("rain_flagged" for QuikSCAT). Readers that do not know a
    name ignore it.

    The picture says how it was drawn: "resolution" is its "resolution:" log line (the
    data's spacing at the storm, the picture's cells) and "drawing" how the samples went
    onto the cells ("each pixel drawn as its own shape", "nearest pixel per grid square"...),
    both "" when unknown. A Terra picture saved at 1225 cells could not be told apart from
    one drawn at 4000 that fell back to grid squares, because nothing said which.

    area, for a picture on the satellite's own grid (Meteosat's first generation), is that
    grid -- "area_crs" (WKT), "area_extent" and "area_shape" -- since the storm box's
    lat/lon do not place its cells.

    The satellite's own pixels the picture was drawn from go with it when they were kept
    (the module's docstring has the keys), and "closeup" says a storm close-up.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    resolution, drawing = _drawing(arr, box_deg, storm_lat, storm_lon, area)
    if area is not None:
        extra = {**(extra or {}), "area_crs": area.crs.to_wkt(), "area_extent": np.asarray(area.area_extent),
                 "area_shape": np.asarray((area.height, area.width))}
    extra = {**(extra or {}), **_kept_pixels("values_keys", arr), **_closeup()}
    np.savez_compressed(
        path, **extra, bt_kelvin=arr.astype(np.float32),
        storm_lat=storm_lat, storm_lon=storm_lon, box_deg=box_deg,
        satellite=satellite, instrument=instrument, band=band,
        obs_time_utc=obs_time_utc.isoformat(),
        units=units, palette=palette,
        # The internal keys this pass came from, so a saved render can be re-fetched
        # without the caller having to remember what produced it -- see
        # run_tc_pass.render_latest_geo, which the app's "Latest frame" button uses.
        # "satellite"/"instrument" above are display labels ("GOES-19", "ABI") and are
        # deliberately not usable for that. Older .npz files simply lack these two, and
        # every reader treats a missing value as "cannot refresh" rather than failing.
        sat_key=sat_key, sensor=sensor, resolution=resolution, drawing=drawing,
    )
    return path


def save_rgb(path, rgb, storm_lat, storm_lon, box_deg, satellite, instrument, band, obs_time_utc,
             drawn_from=None, sat_key="", sensor=""):
    """save_raw for a color picture: the colors it shows (rows, columns, 3 bytes, before any
    caption or coastline), under "rgb" where a one-band picture has bt_kelvin, with the same
    metadata, and its finest grid's pixels each with its own color (the module's docstring).
    drawn_from: the loader's array the picture was made from, whose drawing it was
    (render_rgb's)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rgb = np.asarray(rgb, dtype=np.uint8)
    resolution, drawing = _drawing(rgb if drawn_from is None else drawn_from, box_deg, storm_lat, storm_lon)
    extra = {**_kept_pixels("rgb_keys", rgb, drawn_from), **_closeup()}
    np.savez_compressed(
        path, **extra, rgb=rgb, storm_lat=storm_lat, storm_lon=storm_lon, box_deg=box_deg,
        satellite=satellite, instrument=instrument, band=band, obs_time_utc=obs_time_utc.isoformat(),
        units="rgb", palette="", sat_key=sat_key, sensor=sensor, resolution=resolution, drawing=drawing,
    )
    return path


def _kept_pixels(which, *args):
    """The satellite's own pixels behind a picture about to be saved (composites.pixels'
    values_keys or rgb_keys), when its drawing kept them -- read only when that module is
    already loaded, like _drawing. {} otherwise, and when they cannot be had: the values
    are what the file is for."""
    pixels = sys.modules.get("tcviz.composites.pixels")
    if pixels is None:
        return {}
    try:
        return getattr(pixels, which)(*args)
    except Exception as e:  # noqa: BLE001 -- the values are what the file is for
        print(f"    the satellite's own pixels not saved ({type(e).__name__}: {e})")
        return {}


def _closeup():
    """{"closeup": True} while the storm's close-up is drawn (tcviz.engine.closeup), else {}."""
    modes = sys.modules.get("tcviz.render_modes")
    return {"closeup": True} if modes is not None and modes.closeup() else {}


def _drawing(arr, box_deg, storm_lat, storm_lon, area=None):
    """(the "resolution:" line without its label, how the samples were drawn) for a picture
    about to be saved, from the note its loader made (tcviz.composites.geometry) -- read
    only when that module is already loaded, so the color table editor, which saves
    pictures of its own, never pulls in satpy for it. ("", "") when nothing is known."""
    geometry = sys.modules.get("tcviz.composites.geometry")
    if geometry is None:
        return "", ""
    try:
        shape = np.shape(arr)
        if area is not None:
            line = geometry.native_resolution_line(area, arr, storm_lat, storm_lon)
            note = geometry.resolution_note((area.height, area.width), arr)
        else:
            line = geometry.resolution_line(shape, box_deg, arr=arr, lat=storm_lat)
            note = geometry.resolution_note(shape, arr)
        return (line or "").removeprefix("resolution: "), geometry.drawing_mode(note) or ""
    except Exception:  # noqa: BLE001 -- the values are what the file is for
        return "", ""


def load_raw(path):
    """Inverse of save_raw(); returns a dict of the stored fields."""
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] if z[k].shape != () else z[k].item() for k in z.files}


@dataclass
class GridWindow:
    """A window of a projected satellite grid (a geostationary disk): the grid by its CRS
    (WKT), extent and (rows, columns), and the window (row0, row1, col0, col1) a piece is."""
    crs_wkt: str
    extent: tuple
    shape: tuple
    window: tuple

    def lonlats(self):
        """(lons, lats) of the window's samples, float64 degrees, NaN off the disk -- the
        positions the drawing used. Needs pyresample (not this module's numpy only)."""
        from pyproj import CRS
        from pyresample.geometry import AreaDefinition

        rows, cols = self.shape
        grid = AreaDefinition("grid", "", "grid", CRS.from_wkt(self.crs_wkt), cols, rows, self.extent)
        r0, r1, c0, c1 = self.window
        lons, lats = (np.asarray(v, dtype=np.float64)
                      for v in grid.get_lonlats(data_slice=(slice(r0, r1), slice(c0, c1))))
        off = ~(np.isfinite(lons) & np.isfinite(lats)) | (np.abs(lons) > 360) | (np.abs(lats) > 90)
        return np.where(off, np.nan, lons), np.where(off, np.nan, lats)


@dataclass
class PixelPiece:
    """One window of one satellite grid a picture was drawn from (the module's docstring)."""
    kind: str                       # "swath" or "grid"
    band: str
    values: np.ndarray              # float32 (rows, cols), or uint8 (rows, cols, 3) for "rgb"
    usable: np.ndarray              # bool (rows, cols)
    rows_per_scan: int
    lon: np.ndarray | None = None   # float32 (rows, cols), a swath's
    lat: np.ndarray | None = None
    grid: GridWindow | None = None  # a projected grid's

    def lonlats(self):
        """(lons, lats) as float64, a grid's worked out from its definition."""
        if self.grid is not None:
            return self.grid.lonlats()
        return np.asarray(self.lon, dtype=np.float64), np.asarray(self.lat, dtype=np.float64)


@dataclass
class PixelSet:
    """The satellite's own pixels behind one saved picture: what load_pixels gives back."""
    product: str                    # "values" (colored by a table) or "rgb" (each its color)
    units: str                      # the picture's units ("K", "%", "rgb" ...)
    box_crs_wkt: str                # the picture's map, its extent and (rows, columns)
    box_extent: tuple
    box_shape: tuple
    pieces: list = field(default_factory=list)
    closeup: bool = False


def load_pixels(source):
    """The satellite's own pixels saved with a picture (a data.npz path, or load_raw's dict
    of one) as a PixelSet, or None for a file without them -- every file saved before
    2026-10-04, and pictures not drawn as pixels or footprints."""
    data = load_raw(source) if isinstance(source, (str, Path)) else source
    if "pixels_version" not in data:
        return None
    pieces = []
    for i in range(int(data["pixels_pieces"])):
        pre = f"pixels{i}_"
        kind = str(data[pre + "kind"])
        piece = PixelPiece(kind=kind, band=str(data[pre + "band"]), values=np.asarray(data[pre + "values"]),
                           usable=np.asarray(data[pre + "usable"], dtype=bool),
                           rows_per_scan=int(data[pre + "rows_per_scan"]))
        if kind == "swath":
            piece.lon, piece.lat = np.asarray(data[pre + "lon"]), np.asarray(data[pre + "lat"])
        else:
            piece.grid = GridWindow(str(data[pre + "grid_crs"]),
                                    tuple(float(v) for v in np.ravel(data[pre + "grid_extent"])),
                                    tuple(int(v) for v in np.ravel(data[pre + "grid_shape"])),
                                    tuple(int(v) for v in np.ravel(data[pre + "grid_window"])))
        pieces.append(piece)
    return PixelSet(product=str(data["pixels_product"]), units=str(data.get("units", "K")),
                    box_crs_wkt=str(data["pixels_box_crs"]),
                    box_extent=tuple(float(v) for v in np.ravel(data["pixels_box_extent"])),
                    box_shape=tuple(int(v) for v in np.ravel(data["pixels_box_shape"])),
                    pieces=pieces, closeup=bool(data.get("closeup", False)))
