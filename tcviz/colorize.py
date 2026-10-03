"""A picture's values through its color table into RGBA bytes: what render.render draws every
single-band picture with.

matplotlib's `cmap(norm(np.ma.masked_invalid(values)), bytes=True)` is the reference, and it
stays the answer: tcviz_native.colorize_linear / colorize_bounded (native/src/colorize.rs)
give the same bytes in one pass, without the five picture-sized arrays matplotlib makes on
the way (~550 MB at 4000 x 4000 pixels, measured -- the peak of the whole render). The module
answers only the cases it reproduces exactly:

* a plain numpy array of float32 or float64 (a masked array keeps its own mask: matplotlib);
* a colormap that maps values the way matplotlib's Colormap does (every one tcviz makes);
* Normalize with vmin and vmax set, clip off and a finite vmax - vmin, or BoundaryNorm with
  clip off, increasing finite float64 boundaries and no more colors than regions.

Anything else, and every picture when the module is missing or off, goes to matplotlib
itself (tcviz.native decides, and falls back if the module fails).
"""
import numpy as np
from matplotlib.colors import BoundaryNorm, Colormap, Normalize

from . import native


def rgba_numpy(values, cmap, norm):
    """matplotlib's RGBA bytes for `values`: NaN and infinities in the colormap's bad color."""
    return cmap(norm(np.ma.masked_invalid(values)), bytes=True)


def rgba(values, cmap, norm, threads=None):
    """rgba_numpy(values, cmap, norm), by tcviz_native when it reproduces that exactly."""
    plan = _plan(values, cmap, norm) if native.available() else None
    if plan is None:
        return rgba_numpy(values, cmap, norm)
    name, args = plan
    return native.run(name, _Reference(cmap, norm), *args, threads=threads)


class _Reference:
    """matplotlib's answer for the module's arguments, which carry the values (the same
    ones, contiguous) and the rule read off the colormap and norm."""

    def __init__(self, cmap, norm):
        self.cmap, self.norm = cmap, norm

    def __call__(self, values, *rule):
        return rgba_numpy(values, self.cmap, self.norm)


def table(cmap):
    """The colormap's (N + 3) x 4 bytes as Colormap.__call__(bytes=True) takes rows from them:
    its N colors, then under, over and bad -- asked of the colormap itself."""
    n = cmap.N
    return np.ascontiguousarray(np.concatenate([
        cmap(np.arange(n), bytes=True),
        cmap(np.array([-1]), bytes=True),                       # under
        cmap(np.array([n]), bytes=True),                        # over
        cmap(np.ma.masked_array([0], mask=[True]), bytes=True),  # bad
    ]))


def _plan(values, cmap, norm):
    """(kernel name, arguments) for the module, or None for matplotlib."""
    if (type(values) is not np.ndarray or values.dtype not in (np.float32, np.float64)
            or not values.dtype.isnative or not isinstance(cmap, Colormap)
            or type(cmap).__call__ is not Colormap.__call__
            or type(cmap)._get_rgba_and_mask is not Colormap._get_rgba_and_mask
            or not 1 <= cmap.N <= 1 << 24):
        return None
    values = np.ascontiguousarray(values)
    if type(norm) is Normalize:
        if norm.clip or norm.vmin is None or norm.vmax is None:
            return None
        # as Normalize.__call__ makes them: np.float64 scalars
        (vmin,), _ = norm.process_value(norm.vmin)
        (vmax,), _ = norm.process_value(norm.vmax)
        if type(vmin) is not np.float64 or type(vmax) is not np.float64:
            return None
        with np.errstate(over="ignore", invalid="ignore"):
            if not (vmin <= vmax and np.isfinite(vmax - vmin)):
                return None
        return "colorize_linear", (values, table(cmap), float(vmin), float(vmax))
    if type(norm) is BoundaryNorm:
        bounds = np.asarray(norm.boundaries)
        if (norm.clip or norm.Ncmap > norm._n_regions or bounds.dtype != np.float64 or bounds.ndim != 1
                or not 2 <= bounds.size <= 32_000 or not np.isfinite(bounds).all()
                or not (np.diff(bounds) > 0).all() or norm._offset not in (0, 1)
                or not 0 <= norm.Ncmap <= np.iinfo(np.int16).max):
            return None
        # BoundaryNorm compares the values with its vmin and vmax as their own dtype holds them
        with np.errstate(over="ignore"):
            vmin, vmax = values.dtype.type(norm.vmin), values.dtype.type(norm.vmax)
        if not (np.isfinite(vmin) and np.isfinite(vmax) and vmin < vmax):
            return None
        return "colorize_bounded", (values, table(cmap), np.ascontiguousarray(bounds), int(norm._offset),
                                    int(norm.Ncmap), float(vmin), float(vmax))
    return None
