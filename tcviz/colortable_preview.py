"""A real picture drawn through a color table while the table is edited, near-instantly.

render.render colors a picture with `cmap(norm(np.ma.masked_invalid(values)), bytes=True)`
(tcviz.colorize, with the colormap's bad color set to black). That splits into two steps:
which of the table's N rows each pixel falls in -- fixed by the picture, the range and N --
and what color each row is, which is all an edit changes. So the expensive step runs once
per (picture, range, N), and every edit after it is a lookup of N + 3 colors:

* The row of every pixel is found by drawing the picture once through a "probe" colormap
  whose row i is the color whose bytes spell i (red the low byte, green the next, blue the
  top), with the rows past the end -- under, over and bad -- spelling N, N + 1 and N + 2.
  colorize.rgba does the drawing, so the rows are matplotlib's own to the last pixel:
  NaN, infinities, values exactly on vmin and vmax, everything.
* An edit builds the table's N + 3 colors (colorize.table: the colors, then under, over
  and bad, as matplotlib takes rows from them) and looks every pixel up in it.

The result is byte for byte what render.render puts in the picture before it is enlarged
and lettered (tests/test_colortable_editor.py checks every built-in IR and water-vapor
table, with NaN pixels, with tcviz_native and without). A picture over MAX_PIXELS is
thinned by a whole-number step first: fewer pixels, each one still exact.

The saved picture itself is kept too (PreviewSource.full; the thinned values are a view of
it when nothing is thinned). Zoomed in on a thinned picture, the editor shows every pixel
of the part in view: window_rows works out the rows of that window from the full values
the same way, so its colors are exact too. Keeping the array costs its size -- 64 MB for a
4000 x 4000 float32 picture beside the 4 MB thinned one; nothing extra for the small
samples (Genevieve's 384 x 384, the GOES ones), which are not thinned, while a 375 m VIIRS
sample (3,859 x 3,859, about 14.9 M pixels) is shown 1 pixel in 4 across and down until
zoomed in -- and buys a zoom that never reads the file again: a saved picture can be moved
or deleted while the editor is open (reading it took 0.1 s for a 4898 x 4898 MODIS picture,
2026-10-02, so the wait was not the reason). full_picture draws the whole saved picture for
the editor's Copy picture and Save picture.

The editor's picture pane takes the row image and the colors separately (set_index_image /
set_lut), so the lookup could move to the graphics card later without changing this. The
pixel under the pointer is read back from the saved picture's own values
(PreviewSource.saved_value), in the table's units (in_table_units), for the editor's
readout and its click on a color.

The samples the editor opens with are real scans, cut around the eye from pictures tcviz
saved and kept in its own raw format (saved_pictures.save_raw, float32, the values as
the picture was drawn from them):

* vendor/samples/ir.npz -- GOES-19 ABI band 13 (10.3 um, "IR"), Genevieve (07E, eastern
  Pacific), 2026-07-27 02:30:20 UTC, eye 292.9 K, coldest cloud 192.3 K, 384 x 384; from
  out/07E_GENEVIEVE/geo/raw/20260727T023020_goes_goes19_IR_data.npz.
* vendor/samples/wv.npz -- GOES-19 ABI band 9 (6.9 um, "WV-Mid"), the same storm,
  2026-07-27 00:50:20 UTC, 384 x 384; from
  out/07E_GENEVIEVE/geo/raw/20260727T005020_goes_goes19_WV-Mid_data.npz.
* vendor/samples/wind.npz -- NOAA/STAR's SAR wind speed from RADARSAT Constellation
  Mission 2, Melissa (AL13), 2025-10-28 10:55:22 UTC, the day she reached Jamaica: rows
  523-810 and columns 139-426 of the 500 m picture (288 x 288, 144 km, the eye at its
  center; 0 to 75 m/s in the eyewall, and the 22 cells NOAA's file holds at its 100 m/s
  ceiling); from tcviz's own saved wind picture of that RCM-2 pass. The
  values are m/s, as every wind field tcviz draws: the "kt" the file says is the unit its
  scale is labeled in (render._colorbar_ticks), the way every saved wind picture says it.
* vendor/samples/radar.npz -- GPM's DPR, Ku-band reflectivity near the surface, Eta (AL29),
  2020-11-02 17:04:13 UTC, the whole saved picture (223 x 223, 13 to 49 dBZ, no echo NaN);
  from out/AL292020_ETA/20201102T170413_gpm_dpr_gpmcore/raw/z_surface_data.npz.

A table is tried on pictures of its own kind: infrared and water-vapor tables on the
infrared and water-vapor samples (and Kelvin pictures), a winds table on wind pictures, a
radar table on reflectivity (picture_kind).

The editor's bundled samples -- the pictures Colortable Editor ships with, which tcviz's own
editor opens on too -- are named in vendor/editor_samples/samples.json (tcviz.edition), one
entry per picture: its id, file, kind (ir/wv), storm, year, basin, satellite, instrument,
band, band_words, obs_time_utc, pixel_km, description, coldest_c, warmest_c, credit and
source (bundled_samples, load_bundled). Until that file is there, the editor falls back to
the samples above.

Qt-free; reads saved pictures through tcviz.saved_pictures and letters them through
tcviz.picture_overlays, so it never loads the composites package or render.
"""
import collections
import datetime
import json
import math
import sys
import zipfile
from pathlib import Path

import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap, Normalize

from . import colorize, user_colortables

MAX_PIXELS = 1_000_000
# Water-vapor bands are drawn at 0 to -90 C whatever the table says
# (engine.common.drawing._wv_palette_range; the engine is not imported for two numbers).
WV_VMAX_K, WV_VMIN_K = 0 + 273.15, -90 + 273.15
SAMPLE_KINDS = ("ir", "wv", "wind", "radar")
# The storm each sample shows, for its label.
SAMPLE_STORMS = {"ir": "Genevieve", "wv": "Genevieve", "wind": "Melissa", "radar": "Eta"}
# The kinds of picture a table of each kind is tried on: infrared and water vapor are both
# brightness temperature, so either table can be seen on either picture.
PICTURE_KINDS = {"ir": ("ir", "wv"), "wv": ("ir", "wv"), "wind": ("wind",), "radar": ("radar",)}
# How many row images one picture keeps: the table's own range, a comparison table's, and
# one more, so flipping between them does not draw the picture again.
_CACHED = 3
# How many full-resolution windows (window_rows) it keeps: this table's and a comparison
# table's, where the view is and where it just was.
_CACHED_WINDOWS = 4
_RECENT = 30
# How many of the newest saved pictures the search opens at most. Water-vapor pictures are
# rare (6 of 1,766 in one user's folder, 2026-10-02), so without a limit the search for 30 of
# them would open every file in the folder.
_LOOK_AT = 600

_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def samples_dir():
    """vendor/samples, beside the package in a checkout or inside a frozen app."""
    return Path(getattr(sys, "_MEIPASS", _SOURCE_ROOT)) / "vendor" / "samples"


def sample_path(kind):
    if kind not in SAMPLE_KINDS:
        raise ValueError(f"kind must be one of {SAMPLE_KINDS}")
    return samples_dir() / f"{kind}.npz"


# ------------------------------------------------------------------------ the probe

def _probe(n):
    """A colormap of n rows whose row i is drawn as the bytes of i, with under, over and
    bad drawn as n, n + 1 and n + 2. matplotlib turns a color into a byte by truncating
    255 x the color, so each byte k is stored as (k + 0.5) / 255 (255 as 1.0), which no
    rounding can push to a neighbor."""
    rows = np.arange(n + 3, dtype=np.int64)
    channels = np.stack([rows & 255, (rows >> 8) & 255, (rows >> 16) & 255], axis=1)
    floats = np.where(channels == 255, 1.0, (channels + 0.5) / 255.0)
    colors = np.column_stack([floats, np.ones(n + 3)])
    probe = ListedColormap(colors[:n])
    return probe.with_extremes(under=tuple(colors[n]), over=tuple(colors[n + 1]), bad=tuple(colors[n + 2]))


def _norm_key(norm, n):
    """What a row image depends on besides the picture, or None when the norm is not one
    whose behavior is known from its settings (that one is never cached)."""
    if type(norm) is Normalize:
        return ("linear", n, float(norm.vmin), float(norm.vmax), bool(norm.clip))
    if type(norm) is BoundaryNorm:
        return ("bounded", n, np.asarray(norm.boundaries, dtype=np.float64).tobytes(), norm.Ncmap,
                bool(norm.clip), norm.extend)
    return None


def _spelled_rows(values, norm, n):
    """The row (0 .. n + 2) of every pixel of `values`, drawn through the probe."""
    spelled = colorize.rgba(values, _probe(n), norm)
    index = (spelled[..., 0].astype(np.uint32) | (spelled[..., 1].astype(np.uint32) << 8)
             | (spelled[..., 2].astype(np.uint32) << 16))
    # np.take turns any other index type into the machine's own first; done here once,
    # not on every edit
    return index.astype(np.intp)


def lut_for(cmap):
    """The N + 3 colors an edit changes, as one uint32 per row: the table's colors, then
    under, over and bad -- bad black, as render.render sets it."""
    table = colorize.table(cmap.with_extremes(bad="black"))
    return np.ascontiguousarray(table).view(np.uint32).reshape(-1)


def apply(index, lut):
    """RGBA bytes (H, W, 4) of the row image `index` through `lut` (lut_for)."""
    out = np.take(lut, index)
    return out.view(np.uint8).reshape(*index.shape, 4)


def in_table_units(value, units):
    """A picture's value as the engine draws it (K, m/s, dBZ) in a table's units ("C",
    "kt", "dBZ"): user_colortables.drawing_range the other way round."""
    if units == "C":
        return value - 273.15
    if units == "kt":
        return value * user_colortables.KT_PER_MS
    if units == "dBZ":
        return value
    raise ValueError(f"no table is measured in {units}")


class PreviewSource:
    """One picture to preview tables on: its values (thinned to MAX_PIXELS) and the saved
    picture's own (`full`), what band it is, and the row images worked out for it so far."""

    def __init__(self, values, band="", label="", max_pixels=MAX_PIXELS, units="K", note=None):
        values = np.asarray(values)
        if values.ndim != 2 or not values.size:
            raise ValueError("a picture has to be a 2-D array of values")
        if values.dtype not in (np.float32, np.float64):
            values = values.astype(np.float64)
        step = max(1, math.ceil(math.sqrt(values.size / max_pixels)))
        self.stride = step
        self.full = np.ascontiguousarray(values)
        self.values = self.full if step == 1 else np.ascontiguousarray(self.full[::step, ::step])
        self.band = str(band or "")
        self.units = str(units or "K")
        self.label = label
        # what the editor says under the picture; its label, when there is nothing more
        self.note = note
        self._rows = collections.OrderedDict()
        self._windows = collections.OrderedDict()

    @property
    def shape(self):
        """(rows, columns) of the thinned picture, the one `values` holds."""
        return self.values.shape

    @property
    def full_shape(self):
        """(rows, columns) of the saved picture."""
        return self.full.shape

    @property
    def kind(self):
        """"ir", "wv", "wind" or "radar": what the picture is (picture_kind), or None."""
        return picture_kind(self.band, self.units)

    @property
    def water_vapor(self):
        """Whether the engine draws this picture at the water-vapor range."""
        return self.band.startswith("WV")

    def drawing_range(self, vmax_k, vmin_k):
        """(vmax, vmin) in K a table with this range is drawn at on this picture."""
        return (WV_VMAX_K, WV_VMIN_K) if self.water_vapor else (vmax_k, vmin_k)

    def value_at(self, row, col):
        """The value of one pixel of the picture as shown -- `row` and `col` count the
        thinned pixels, the ones in `values` -- in the picture's own units (K, m/s, dBZ).
        None off the picture, and for a pixel with no data (NaN, or an infinity, which the
        picture draws as no data too)."""
        height, width = self.values.shape
        if not (0 <= row < height and 0 <= col < width):
            return None
        value = float(self.values[row, col])
        return value if math.isfinite(value) else None

    def saved_pixel(self, row, col):
        """(row, col) in the saved picture of a pixel as shown: thinning keeps every
        `stride`-th pixel, starting with the first."""
        return row * self.stride, col * self.stride

    def saved_value(self, row, col):
        """The value of the saved picture's pixel (row, col) -- any pixel, not only the
        ones thinning keeps -- in the picture's own units; None off the picture and for a
        pixel with no data, as value_at."""
        height, width = self.full.shape
        if not (0 <= row < height and 0 <= col < width):
            return None
        value = float(self.full[row, col])
        return value if math.isfinite(value) else None

    def rows(self, norm, n):
        """The row (0 .. n + 2) of every pixel for a table of n colors drawn with `norm`."""
        key = _norm_key(norm, n)
        if key is not None and key in self._rows:
            self._rows.move_to_end(key)
            return self._rows[key]
        index = _spelled_rows(self.values, norm, n)
        if key is not None:
            self._rows[key] = index
            while len(self._rows) > _CACHED:
                self._rows.popitem(last=False)
        return index

    def window_rows(self, norm, n, top, bottom, left, right):
        """The rows, as `rows`, of the saved picture's own pixels [top:bottom, left:right]
        -- every one of them, whatever the thinning -- for the editor's zoom. Worked out
        through the same probe, so each is the row render.render would color it with.
        The last few windows are kept, so an edit, which only changes the colors, does
        not work one out again."""
        top, bottom, left, right = (int(v) for v in (top, bottom, left, right))
        height, width = self.full.shape
        if not (0 <= top < bottom <= height and 0 <= left < right <= width):
            raise ValueError(f"the window {top}:{bottom}, {left}:{right} is not inside a {height} x {width} picture")
        if self.stride == 1:
            # nothing is thinned: the window is part of the picture's own rows
            return self.rows(norm, n)[top:bottom, left:right]
        key = _norm_key(norm, n)
        if key is not None:
            key = (key, top, bottom, left, right)
            if key in self._windows:
                self._windows.move_to_end(key)
                return self._windows[key]
        index = _spelled_rows(self.full[top:bottom, left:right], norm, n)
        if key is not None:
            self._windows[key] = index
            while len(self._windows) > _CACHED_WINDOWS:
                self._windows.popitem(last=False)
        return index

    def apply(self, lut, norm=None, n=None):
        """The picture's RGBA bytes through `lut` (lut_for). With `norm` and `n` the rows
        are those; without, the rows asked for last."""
        if norm is not None:
            index = self.rows(norm, n)
        elif self._rows:
            index = next(reversed(self._rows.values()))
        else:
            raise ValueError("no rows worked out for this picture yet")
        return apply(index, lut)


# ------------------------------------------------------------------------ sharing

def full_picture(source, cmap, norm, vmax, vmin, units, scale=True, name=None):
    """The whole saved picture through a table, drawn the way tcviz draws a picture, for
    the editor's Copy picture and Save picture: (PIL RGB image, the whole number each of
    the saved picture's pixels was enlarged by).

    * Every pixel of the saved picture (source.full), never the thinned ones, colored by
      colorize.rgba with the table's bad color black -- render.render's own call, so each
      pixel is the color a picture drawn with this table gives it.
    * A picture smaller than render's is enlarged as render enlarges it, by a whole number
      with nearest-neighbor (render._upscale_factor), so each pixel is a square of one
      color: the scale and the name are sized from the picture's height, and on a
      384-pixel sample they would be too small to read.
    * With `scale`, the color scale inside it (render.overlay_colorbar); with `name`, the
      name in a corner (render.overlay_caption) -- where tcviz's pictures carry them.

    vmax and vmin are the range the picture is drawn at (PreviewSource.drawing_range), in
    the engine's units (K, m/s, dBZ); `units` is the table's ("C", "kt", "dBZ"), which the
    scale is labeled in."""
    from PIL import Image

    from . import picture_overlays
    masked = cmap.with_extremes(bad="black")
    img = Image.fromarray(colorize.rgba(source.full, masked, norm)).convert("RGB")
    factor = int(picture_overlays._upscale_factor(min(img.size), Image.NEAREST))
    if factor > 1:
        img = img.resize((img.width * factor, img.height * factor), Image.NEAREST)
    if scale:
        img = picture_overlays.overlay_colorbar(img, masked, vmin, vmax, units=units)
    if name:
        img = picture_overlays.overlay_caption(img, name, corner="tl")
    return img, factor


def side_by_side(first, second, plate=(18, 18, 18)):
    """Two pictures of the same size side by side, a narrow dark gap between them."""
    from PIL import Image
    gap = max(4, first.height // 100)
    out = Image.new("RGB", (first.width + gap + second.width, max(first.height, second.height)), plate)
    out.paste(first, (0, 0))
    out.paste(second, (first.width + gap, 0))
    return out


# ------------------------------------------------------------------------ pictures

def describe(raw, path=None, root=None):
    """A line naming a saved picture for a person: what took it, when, and the storm."""
    parts = [" ".join(str(raw.get(k) or "").strip() for k in ("satellite", "instrument", "band")).strip()]
    when = raw.get("obs_time_utc")
    try:
        when = datetime.datetime.fromisoformat(str(when))
        parts.append(when.strftime("%Y-%m-%d %H:%M UTC"))
    except (TypeError, ValueError):
        pass
    if path is not None and root is not None:
        try:
            parts.append(Path(path).resolve().relative_to(Path(root).resolve()).parts[0])
        except (ValueError, IndexError):
            pass
    return ", ".join(p for p in parts if p) or Path(path or "").name


def load_picture(path, root=None, max_pixels=MAX_PIXELS):
    """A PreviewSource from one of tcviz's saved pictures (raw/*_data.npz)."""
    from .saved_pictures import load_raw
    raw = load_raw(path)
    if "bt_kelvin" not in raw:
        raise ValueError("this file holds no picture values")
    band = str(raw.get("band") or _band_from_name(path))
    return PreviewSource(raw["bt_kelvin"], band=band, label=describe(raw, path, root), max_pixels=max_pixels,
                         units=raw.get("units") or "K")


def load_sample(kind):
    """The sample infrared ("ir"), water-vapor ("wv"), wind ("wind") or radar ("radar")
    picture."""
    source = load_picture(sample_path(kind))
    source.label = f"Sample: {source.label}, {SAMPLE_STORMS[kind]}"
    return source


# ------------------------------------------------------------------------ bundled samples

BundledSample = collections.namedtuple(
    "BundledSample", "id path kind label description credit source storm year basin satellite instrument band "
                     "band_words obs_time_utc pixel_km coldest_c warmest_c")
BundledSample.__doc__ = """One picture of vendor/editor_samples/samples.json: its manifest fields,
`path` the npz file, and `label` the name the editor's Picture box gives it ("Melissa 2025 ·
GOES-19 IR")."""

# How a label says what kind of picture it is.
_KIND_WORDS = {"ir": "IR", "wv": "water vapor"}
_MANIFEST_FIELDS = ("storm", "year", "basin", "satellite", "instrument", "band", "band_words", "obs_time_utc",
                    "pixel_km", "coldest_c", "warmest_c")


def bundled_samples(manifest=None):
    """The samples the manifest names (`manifest`, else tcviz.edition.samples_manifest_path),
    in its order: infrared and water-vapor pictures whose file is there. [] with no manifest,
    or one that does not read -- the editor then shows the samples it always had.

    Two that would be named alike ("Melissa 2025 · GOES-19 IR" twice) say their times too."""
    from . import edition
    path = Path(manifest) if manifest is not None else edition.samples_manifest_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("samples", [])
    if not isinstance(data, list):
        return []
    out = []
    for raw in data:
        if not isinstance(raw, dict) or raw.get("kind") not in _KIND_WORDS:
            continue
        sample_id, file = raw.get("id"), raw.get("file")
        if not (isinstance(sample_id, str) and sample_id and isinstance(file, str) and file):
            continue
        file_path = path.parent / file
        if not file_path.is_file():
            continue
        fields = {k: raw.get(k) for k in _MANIFEST_FIELDS}
        out.append(BundledSample(sample_id, file_path, raw["kind"], _sample_label(raw), str(raw.get("description") or ""),
                                 str(raw.get("credit") or ""), str(raw.get("source") or ""), **fields))
    named = collections.Counter(s.label for s in out)
    return [s._replace(label=f"{s.label}, {_when_words(s.obs_time_utc)}")
            if named[s.label] > 1 and _when_words(s.obs_time_utc) else s for s in out]


def _sample_label(raw):
    """"Melissa 2025 · GOES-19 IR": the storm and its year, then what took the picture."""
    storm = " ".join(str(raw.get(k) or "").strip() for k in ("storm", "year")).strip()
    taken = " ".join(p for p in (str(raw.get("satellite") or "").strip(), _KIND_WORDS[raw["kind"]]) if p)
    return " · ".join(p for p in (storm, taken) if p)


def _when_words(when):
    try:
        return datetime.datetime.fromisoformat(str(when).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return ""


def load_bundled(sample, max_pixels=MAX_PIXELS):
    """A PreviewSource of one bundled sample (bundled_samples), named by its label, with its
    description and credit as the note under the picture.

    The file is tcviz's own saved-picture format (saved_pictures.save_raw: the values under
    bt_kelvin) or any npz holding one 2-D array of values. Values in Kelvin, as every
    temperature picture is drawn; a picture that says it is in degrees C -- or, saying
    nothing, has no value above 150, colder than any cloud top in Kelvin -- is moved up by
    273.15."""
    from .saved_pictures import load_raw
    raw = load_raw(sample.path)
    values = raw.get("bt_kelvin")
    if values is None:
        arrays = [v for v in raw.values() if isinstance(v, np.ndarray) and v.ndim == 2 and v.size]
        if len(arrays) != 1:
            raise ValueError(f"{Path(sample.path).name} holds no picture values")
        values = arrays[0]
    values = np.asarray(values, dtype=np.float32 if np.asarray(values).dtype == np.float32 else np.float64)
    units = str(raw.get("units") or "K")
    if units in ("C", "°C", "degC") or (units in ("K", "", "?") and "units" not in raw
                                          and np.isfinite(values).any() and np.nanmax(values) < 150.0):
        values = values + 273.15
    units = "K"
    band = str(raw.get("band") or "")
    if picture_kind(band, units) != sample.kind:
        band = "WV" if sample.kind == "wv" else "IR"
    # the title ("Hurricane Melissa (2025) at 165 knots on 2025-10-28T12:10Z") and the data
    # source each on a line of its own, as the author asked (2026-10-03); neither ends in a
    # full stop, so they are not run together into one sentence
    note = "\n".join(p.strip() for p in (sample.description, sample.credit) if p.strip()) or None
    return PreviewSource(values, band=band, label=sample.label, max_pixels=max_pixels, units=units, note=note)


def _band_from_name(path):
    """The band key in a saved picture's file name: <...>_<band>_data.npz or <band>_data.npz."""
    stem = Path(path).name.removesuffix("_data.npz")
    return stem.rsplit("_", 1)[-1]


# The units a saved wind speed or radar picture says it is in. A wind picture's array is
# m/s whatever it says: "kt" is the unit its scale is labeled in (composites.save_raw).
_WIND_UNITS = ("kt", "m/s")
_RADAR_UNITS = ("dBZ",)


def picture_kind(band, units, path=None):
    """"ir" or "wv" for a saved picture an infrared or water-vapor table can be tried on,
    "wind" for a wind speed, "radar" for reflectivity, else None (visible, microwave, rain
    rate...). Temperature pictures are in Kelvin; older files say no unit at all."""
    if str(units) in _WIND_UNITS:
        return "wind"
    if str(units) in _RADAR_UNITS:
        return "radar"
    if str(units or "K") not in ("K", "", "?"):
        return None
    keys = {str(band or "")}
    if path is not None:
        keys.add(_band_from_name(path))
    if any(k.startswith("WV") for k in keys):
        return "wv"
    if any(k == "IR" or k.startswith("IR") or k.endswith("-IR") for k in keys):
        return "ir"
    return None


RecentPicture = collections.namedtuple("RecentPicture", "path label kind")


def recent_pictures(root, kinds=SAMPLE_KINDS, limit=_RECENT, look_at=_LOOK_AT):
    """{kind: the newest `limit` saved pictures under `root` of that kind (picture_kind:
    infrared, water vapor, wind, radar), newest first}, from the newest `look_at`
    saved pictures of any kind. Reads only each file's few words of description, never its
    picture; meant for a background thread all the same, since an output folder can hold
    thousands of files."""
    out = {kind: [] for kind in kinds}
    root = Path(root)
    if not root.is_dir():
        return out
    found = []
    # A pass with one picture saves it as a bare data.npz -- most wind passes do
    # (composites.save_raw's callers in engine.processors.winds_sar) -- so both names.
    for path in root.rglob("*data.npz"):
        if path.name != "data.npz" and not path.name.endswith("_data.npz"):
            continue
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            continue
    found.sort(key=lambda t: t[0], reverse=True)
    for _mtime, path in found[:look_at]:
        if all(len(pictures) >= limit for pictures in out.values()):
            break
        try:
            with np.load(path, allow_pickle=False) as z:
                if "bt_kelvin" not in z.files:
                    continue
                fields = {k: z[k].item() for k in ("satellite", "instrument", "band", "obs_time_utc", "units")
                          if k in z.files and z[k].shape == ()}
        except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
            continue
        kind = picture_kind(fields.get("band"), fields.get("units"), path)
        if kind in out and len(out[kind]) < limit:
            out[kind].append(RecentPicture(path, describe(fields, path, root), kind))
    return out
