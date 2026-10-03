"""tcviz's own saved pictures: save_raw writes the values a picture was drawn from (and what
it shows) beside the PNG, load_raw reads them back -- the viewer's box analysis, the color
table editor's previews and its sample pictures all read these files.

Moved out of tcviz.composites.rawio, which re-exports both, so the color table editor can
read a saved picture without loading the composites package (satpy, pyresample, dask...):
this module needs only numpy.
"""
from pathlib import Path

import numpy as np


def save_raw(path, arr, storm_lat, storm_lon, box_deg, satellite, instrument, band, obs_time_utc,
             units="K", palette="", sat_key="", sensor="", extra=None):
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
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path, **(extra or {}), bt_kelvin=arr.astype(np.float32),
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
        sat_key=sat_key, sensor=sensor,
    )
    return path


def load_raw(path):
    """Inverse of save_raw(); returns a dict of the stored fields."""
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] if z[k].shape != () else z[k].item() for k in z.files}
