"""Generic background-job runner: any tcviz call that hits the network, downloads
files, or resamples/renders (i.e. everything in run_tc_pass.py's process_*_pass
functions, plus resolve_storm() and every _find_*_passes search) must not run on the
GUI's own thread or the whole window freezes. One Worker class covers every job kind
(pass search, resolve_storm, fetch+render) -- they're just different callables.
"""
import gc
import sys
import threading

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal

from .stdout_bridge import QtStdoutBridge

# What a stopped job reports through `failed` (Worker.cancel).
STOPPED = "stopped"


class JobCanceled(BaseException):
    """Raised inside a job asked to stop (Worker.cancel), at its next line of tcviz's own
    output: every download reports its progress and every pass step says what it did, so
    that comes within seconds. A BaseException, so the `except Exception` blocks that keep
    one failed pass from ending a run do not swallow it.

    Only tcviz's own output (stdout) stops a job. NASA's earthaccess draws its progress on
    stderr while it writes straight into the finished file's name, and a stop there would
    leave half a file that the next run takes for a whole one; tcviz's own downloads write
    to a temporary name and rename it only when complete."""


class _OutputRouter:
    """sys.stdout (or sys.stderr) for the whole app, installed once: each write goes to the
    log of the job that owns the writing thread -- its own thread, or one it started
    (QtStdoutBridge._owns_current_thread) -- and anything else to the real stream.

    Each job used to swap sys.stdout for its own log (contextlib.redirect_stdout) and put
    back what it had found. Jobs overlap -- the search the app starts for every active
    storm at launch runs beside the user's own search or render -- and they do not end in
    order: the first to finish put the real stream back while the other still ran, whose
    remaining lines then went to the terminal instead of its log, and the last to finish
    left sys.stdout on a finished job's log (both measured, 2026-09-28)."""

    def __init__(self, name, real):
        self.name, self.real = name, real

    def write(self, s):
        with _jobs_lock:
            jobs = [(bridge, worker) for stream, bridge, worker in _jobs if stream == self.name]
        for bridge, worker in reversed(jobs):
            if bridge._owns_current_thread():
                if worker.canceled and self.name == "stdout":
                    raise JobCanceled()
                return bridge.write(s)
        if self.real is None:        # a windowed Windows program (Colortable Editor) has no console
            return len(s)
        return self.real.write(s)

    def flush(self):
        if self.real is not None:
            self.real.flush()

    def __getattr__(self, name):
        return getattr(self.real, name)


_jobs = []                       # (stream name, bridge, worker) of every job running
_jobs_lock = threading.Lock()


def stop_point():
    """Raise JobCanceled if the job this thread belongs to has been asked to stop; nothing
    on a thread no job owns.

    For code that waits without printing: a job otherwise notices its Stop only at its next
    line of output, and a pass search waiting on a server that never answers prints
    nothing at all (run_tc_pass.run_pass_searches calls this while it waits)."""
    with _jobs_lock:
        jobs = [(bridge, worker) for stream, bridge, worker in _jobs if stream == "stdout"]
    for bridge, worker in reversed(jobs):
        if bridge._owns_current_thread():
            if worker.canceled:
                raise JobCanceled()
            return


def _router(name):
    """The router installed on sys.<name>, installing one over whatever is there if not
    (a test harness may have swapped the stream since)."""
    current = getattr(sys, name)
    if not isinstance(current, _OutputRouter):
        current = _OutputRouter(name, current)
        setattr(sys, name, current)
    return current


class Worker(QObject):
    """Runs fn(*args, **kwargs) with its output (and that of every thread it starts)
    routed into QtStdoutBridges, so tcviz's existing print()/print_progress-based
    progress reaches the GUI as `progress_line` signals with no changes to any
    tcviz/sources/*.py module. Must be moved to a QThread via run_in_background()
    below, not run directly on the GUI thread.
    """

    progress_line = Signal(str, bool)  # (line, is_overwrite) -- see QtStdoutBridge.line_written
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.canceled = False

    def cancel(self):
        """Ask the job to stop: it ends at its next line of output (JobCanceled) and
        reports STOPPED through `failed`. Safe from any thread."""
        self.canceled = True

    def run(self):
        bridges = []
        for name in ("stdout", "stderr"):
            bridge = QtStdoutBridge(_router(name).real)
            bridge.line_written.connect(self.progress_line, Qt.ConnectionType.DirectConnection)
            bridges.append((name, bridge))
        with _jobs_lock:
            _jobs.extend((name, bridge, self) for name, bridge in bridges)
        try:
            result = self._fn(*self._args, **self._kwargs)
        except JobCanceled:
            self._close(bridges)
            self.failed.emit(STOPPED)
            return
        except SystemExit as e:
            self._close(bridges)
            self.failed.emit(str(e.code) if e.code else STOPPED)
            return
        except Exception as e:
            self._close(bridges)
            self.failed.emit(str(e))
            return
        except BaseException as e:  # noqa: BLE001 -- see below
            # Anything else that is not an Exception -- a Rust extension's panic arrives as
            # one, for instance. Unreported, the page that started the job waited for an
            # answer that never came and stayed greyed out for good.
            self._close(bridges)
            self.failed.emit(str(e) or type(e).__name__)
            return
        self._close(bridges)
        self.finished.emit(result)

    def _close(self, bridges):
        with _jobs_lock:
            _jobs[:] = [entry for entry in _jobs if entry[2] is not self]
        for _name, bridge in bridges:
            bridge.close_and_flush_remainder()


_active_jobs = set()


def run_in_background(fn, *args, on_progress=None, on_finished=None, on_failed=None, **kwargs):
    """Starts fn(*args, **kwargs) on a new QThread. Returns (thread, worker); the
    worker's cancel() stops the job (see Worker.cancel) -- object lifetime itself is
    already handled internally, no GC-safety burden on the caller.

    IMPORTANT: on_progress/on_finished/on_failed must be bound methods of a QObject
    that lives on the GUI thread (i.e. a normal page/widget method) -- confirmed
    live this session that passing a plain function/lambda instead breaks Qt's
    AutoConnection thread detection (it can only tell the receiver's thread apart
    from the emitting worker thread via the receiving QObject's own thread
    affinity), silently making the callback run synchronously ON the worker thread
    instead of being queued to the GUI thread. Two real, concrete failures result:
    unsafe cross-thread widget access, and -- if the callback itself calls print()
    -- infinite recursion (the callback's own print() would be routed into this
    job's own log, re-entering the same write->emit->callback chain). Every page in
    this project is a QWidget subclass, so its own methods are always safe to pass
    directly; just never wrap one in a bare lambda/function that isn't itself a
    QObject method.
    """
    thread = QThread()
    worker = Worker(fn, *args, **kwargs)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    if on_progress is not None:
        worker.progress_line.connect(on_progress)
    if on_finished is not None:
        worker.finished.connect(on_finished)
    if on_failed is not None:
        worker.failed.connect(on_failed)
    # Directly, from the job's own thread (QThread.quit is safe from any): queued to the
    # GUI thread, the job's thread outlived it whenever that loop was not running -- as it
    # is not once the window has closed, where stop_all_jobs then waited it out in full.
    worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
    worker.failed.connect(thread.quit, Qt.ConnectionType.DirectConnection)

    pair = (thread, worker)
    _active_jobs.add(pair)

    def _cleanup():
        worker.deleteLater()
        thread.deleteLater()
        _active_jobs.discard(pair)

    thread.finished.connect(_cleanup)
    thread.start()
    return thread, worker


def stop_all_jobs(wait_s=3.0):
    """Ask every running job to stop and wait up to wait_s for them to end. True when
    none is left running.

    For quitting: a QThread still running when Python tears the app down aborts the
    whole process ("QThread: Destroyed while thread is still running", a core dump --
    measured 2026-09-28 closing the window during the launch-time search). A job waiting
    on a download or a remote server may not reach its next line of output in time;
    tcviz_gui.__main__ then ends the process without that teardown."""
    import time

    running = [(thread, worker) for thread, worker in list(_active_jobs) if thread.isRunning()]
    for _thread, worker in running:
        worker.cancel()
    deadline = time.monotonic() + wait_s
    for thread, _worker in running:
        thread.wait(max(0, int((deadline - time.monotonic()) * 1000)))
    return not any(thread.isRunning() for thread, _worker in running)


class GuiThreadGarbageCollector(QObject):
    """Python's cycle collector, run on the GUI thread only.

    Left automatic, the collector runs on whichever thread allocates past its threshold --
    as often as not a background job -- and frees there whatever has become garbage, Qt
    windows included. A widget destroyed off the GUI thread takes the process down: a
    segfault on CI, 2026-10-03, when storm_index.load's json.dump on a job thread
    collected a window an earlier test had left behind. So the automatic collector is
    switched off, and a timer does the same work on the GUI thread, generation by
    generation at the same thresholds (the pattern pyqtgraph's GarbageCollector uses).
    Most garbage never needs it: reference counting frees it at once, on any thread."""

    def __init__(self, parent=None, interval_ms=1000):
        super().__init__(parent)
        self._threshold = gc.get_threshold()
        gc.disable()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check)
        self._timer.start(interval_ms)

    def check(self):
        young, middle, old = gc.get_count()
        if young > self._threshold[0]:
            gc.collect(0)
            if middle > self._threshold[1]:
                gc.collect(1)
                if old > self._threshold[2]:
                    gc.collect(2)

    def stop(self):
        """Back to the automatic collector."""
        self._timer.stop()
        gc.enable()
