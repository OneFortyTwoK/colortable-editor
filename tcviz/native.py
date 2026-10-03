"""The one door to tcviz_native, the Rust layer (native/, PACKAGING.md "The native module").

Every native kernel has a numpy implementation in the Python tree, and that one is the
reference: the Rust kernel must reproduce it bit for bit (the identical-picture rule). This
module decides which of the two runs, and makes sure a picture is drawn either way:

* tcviz_native is used when it imports, its ABI_VERSION is the one this file expects, and
  TCVIZ_NO_NATIVE is unset. Otherwise -- not built, the wrong build, or turned off -- every
  call runs the numpy reference, which is exactly how tcviz worked before the module
  existed and how it runs from a source checkout by default.
* A kernel that raises (bad input, out of memory) is answered by numpy for that call. A
  kernel that panics is answered by numpy for that call and for the rest of the process. A
  Rust panic reaches Python as pyo3_runtime.PanicException, which derives from
  BaseException precisely so that `except Exception` does NOT catch it; run() catches it by
  name, and lets KeyboardInterrupt and SystemExit through as always.
* TCVIZ_NATIVE_VERIFY=1 (shadow mode) runs both, logs any difference on the "tcviz.native"
  logger, and returns numpy's result, so turning it on never changes a picture.

The switches are read when they matter: TCVIZ_NO_NATIVE once, when the module is first
needed (reset() forgets that decision), TCVIZ_NATIVE_VERIFY on every call.
"""
import logging
import os

import numpy as np

log = logging.getLogger(__name__)

# What tcviz_native's functions are called, take and return; native/src/lib.rs has the same
# number. A module with another one is not used at all.
ABI_VERSION = 14

# Elements per chunk in fixed_order_sum; native/src/lib.rs's SUM_CHUNK must match.
SUM_CHUNK = 4096

_NOT_LOADED = object()
_module = _NOT_LOADED
_why = "not looked for yet"
_panicked = set()


def _flag(name):
    """An environment switch: set to anything but '', 0, false, no or off."""
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


def is_panic(exc):
    """A Rust panic, as PyO3 raises it: pyo3_runtime.PanicException. Each PyO3 module makes
    its own copy of the type, so it is recognized by name -- and only outside the Exception
    family, which is where PyO3 puts it."""
    return type(exc).__name__ == "PanicException" and not isinstance(exc, Exception)


def reset():
    """Forget the loaded module, its verdict and which kernels panicked (tests, or after
    changing TCVIZ_NO_NATIVE)."""
    global _module, _why
    _module, _why = _NOT_LOADED, "not looked for yet"
    _panicked.clear()


def _load():
    global _module, _why
    if _module is not _NOT_LOADED:
        return _module
    _module = None
    if _flag("TCVIZ_NO_NATIVE"):
        _why = "turned off by TCVIZ_NO_NATIVE"
        return None
    try:
        import tcviz_native as mod
        found = mod.abi_version()
    except ImportError as exc:
        _why = f"not built ({exc})"
        return None
    except BaseException as exc:
        if not isinstance(exc, Exception) and not is_panic(exc):
            raise
        _why = f"failed to load ({type(exc).__name__}: {exc})"
        log.warning("tcviz_native %s; drawing with numpy", _why)
        return None
    if found != ABI_VERSION:
        _why = f"ABI {found}, but this tcviz expects {ABI_VERSION} -- rebuild native/"
        log.warning("tcviz_native speaks %s; drawing with numpy", _why)
        return None
    _module, _why = mod, f"tcviz_native {getattr(mod, '__version__', '?')} (ABI {found})"
    return mod


def module():
    """tcviz_native, or None when numpy does the work (see status() for why)."""
    return _load()


def available():
    return _load() is not None


def status():
    """One line for logs and the smoke test: what runs the kernels, and why."""
    mod = _load()
    return _why if mod is not None else f"numpy ({_why})"


def default_threads():
    """Threads a kernel uses when the caller does not say: at most three, and one core left
    for the app (or, on a small server, for the bot answering its requests)."""
    count = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    return max(1, min(3, count - 1))


def identical(a, b):
    """Bit for bit the same: same type of value, shape and dtype, and the same bytes -- so
    NaN payloads and the sign of zero count, as they do for the pictures."""
    if isinstance(a, (tuple, list)) or isinstance(b, (tuple, list)):
        return (type(a) is type(b) and len(a) == len(b)
                and all(identical(x, y) for x, y in zip(a, b)))
    a, b = np.asarray(a), np.asarray(b)
    if a.dtype != b.dtype or a.shape != b.shape:
        return False
    if a.dtype.kind not in "biufc":
        return bool(np.array_equal(a, b))
    return bool(np.array_equal(np.ascontiguousarray(a).reshape(-1).view(np.uint8),
                               np.ascontiguousarray(b).reshape(-1).view(np.uint8)))


def _describe(native, reference):
    a, b = np.asarray(native), np.asarray(reference)
    if a.shape != b.shape or a.dtype != b.dtype:
        return f"native {a.dtype}{a.shape}, numpy {b.dtype}{b.shape}"
    if a.ndim == 0:
        return f"native {a.item()!r}, numpy {b.item()!r}"
    flat_a, flat_b = np.ascontiguousarray(a).reshape(-1), np.ascontiguousarray(b).reshape(-1)
    if flat_a.size == 0 or a.dtype.kind not in "biufc":
        return f"native {a.dtype}{a.shape} and numpy {b.dtype}{b.shape} differ"
    bytes_a = np.ascontiguousarray(flat_a).view(np.uint8).reshape(flat_a.size, -1)
    bytes_b = np.ascontiguousarray(flat_b).view(np.uint8).reshape(flat_b.size, -1)
    differ = np.flatnonzero((bytes_a != bytes_b).any(axis=1))
    if differ.size == 0:
        return "every byte matches, but the comparison rejected them"
    first = differ[0]
    return (f"{differ.size} of {flat_a.size} elements differ; first at flat index {first}: "
            f"native {flat_a[first]!r}, numpy {flat_b[first]!r}")


def run(name, reference, *args, threads=None, compare=identical, finish=None, **kwargs):
    """reference(*args, **kwargs), computed by tcviz_native.<name> when that is available.

    The native kernel gets the same arguments plus threads= (default_threads() when not
    given). numpy answers instead whenever the module is unavailable, the kernel raises, or
    the kernel panicked (then for the rest of the process). With TCVIZ_NATIVE_VERIFY set
    both run, a difference by `compare` is logged, and numpy's result is returned.

    finish: for a kernel that leaves part of its answer to Python, what makes the kernel's
    result the answer -- tcviz_native.nearest_samples hands back the pixels whose nearest
    sample only scipy can choose, and finish asks scipy. It is applied to the module's
    result alone, before shadow mode compares it, and is not the kernel: what it raises
    is raised.
    """
    mod = _load()
    kernel = None if mod is None or name in _panicked else getattr(mod, name, None)
    if kernel is None:
        return reference(*args, **kwargs)
    if threads is None:
        threads = default_threads()
    try:
        result = kernel(*args, threads=threads, **kwargs)
    except Exception as exc:
        log.warning("tcviz_native.%s failed (%s: %s); numpy instead", name, type(exc).__name__, exc)
        return reference(*args, **kwargs)
    except BaseException as exc:
        if not is_panic(exc):
            raise
        _panicked.add(name)
        log.error("tcviz_native.%s panicked (%s); numpy from now on in this process", name, exc)
        return reference(*args, **kwargs)
    if finish is not None:
        result = finish(result)
    if _flag("TCVIZ_NATIVE_VERIFY"):
        expected = reference(*args, **kwargs)
        if not compare(result, expected):
            log.warning("tcviz_native.%s differs from numpy: %s", name, _describe(result, expected))
        return expected
    return result


# ---------------------------------------------------------------- the example kernel

def fixed_order_sum_numpy(values):
    """The sum of a float64 array in a fixed order: each SUM_CHUNK-element chunk left to
    right, then the chunk sums left to right. Not np.sum, whose pairwise order changes with
    numpy's build -- np.cumsum adds strictly in sequence, so this is the order the Rust
    kernel follows, to the bit."""
    a = np.ascontiguousarray(values, dtype=np.float64).reshape(-1)
    if a.size == 0:
        return 0.0
    whole = a.size // SUM_CHUNK * SUM_CHUNK
    with np.errstate(invalid="ignore", over="ignore"):      # inf - inf, overflow: as Rust, silently
        partials = [np.cumsum(a[:whole].reshape(-1, SUM_CHUNK), axis=1)[:, -1]] if whole else []
        if whole < a.size:
            partials.append(np.cumsum(a[whole:])[-1:])
        return float(np.cumsum(np.concatenate(partials))[-1])


def fixed_order_sum(values, threads=None):
    """fixed_order_sum_numpy(values), by tcviz_native when available (the spike's example
    of a dispatched kernel; nothing draws with it)."""
    a = np.ascontiguousarray(values, dtype=np.float64).reshape(-1)
    return run("fixed_order_sum", fixed_order_sum_numpy, a, threads=threads)
