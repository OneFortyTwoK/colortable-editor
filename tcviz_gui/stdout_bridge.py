"""Bridges tcviz's existing print()-based progress/status output into a Qt signal,
without touching any tcviz/sources/*.py module.

Every process_*_pass function (and the shared helpers they call, like
tcviz.sources.print_progress and parallel_map_with_progress) reports progress by
writing plain text to sys.stdout -- some of it \\r-overwritten single-line progress
(print_progress's own convention), some of it third-party tqdm/pqdm output
(earthaccess.download(show_progress=True), used by MODIS/CYGNSS/GMI). Rather than
rewrite any of that to call back into the GUI directly, the worker thread redirects
sys.stdout to an instance of this class for the duration of one job -- see worker.py.
"""
import threading

from PySide6.QtCore import QObject, Signal

# Which threads a bridge should capture: its owner AND that owner's descendants.
#
# Owner-only was too narrow. The slowest downloads in this project report progress from a
# thread the job SPAWNS rather than from the job thread itself -- sftp_download_with_retry
# runs each G-Portal transfer on its own watchdog thread, and parallel_map_with_progress
# uses a thread pool. Their print_progress() output therefore failed the owner check and
# was passed through to the terminal, so the GUI sat on one static line for the entire
# transfer. Confirmed live on a 95MB AMSR3 granule: seven minutes, no feedback,
# indistinguishable from a hang -- the exact complaint print_progress exists to prevent.
#
# Ancestry is recorded on the child at start() time, which runs on the PARENT thread
# before the child can write anything, so there is no window where a child's first lines
# are misrouted. Each child stores its full ancestor set rather than a parent link, which
# keeps the write() check O(1) and needs no ident->Thread lookup table (a QThread appears
# to `threading` as a _DummyThread it never registered, so walking parent idents would
# not be reliable anyway).
#
# The lineage is of _thread_mark()s, not thread idents. An ident is handed straight to the
# next thread once its own ends, and the app's jobs each run on a new QThread that gets
# the ident of the one before it (measured: four jobs in a row, one ident). A thread that
# outlived its job -- a pass search's source left running past the search's deadline -- was
# then claimed by whichever job came next: its lines went into that job's log, and that
# job's Stop stopped it.
_ANCESTORS = "_tcviz_bridge_ancestors"
_patched = False
_lineage = threading.local()


def _thread_mark():
    """An object standing for this thread, made the first time it is asked for: unlike an
    ident, never the same for two threads (see above)."""
    mark = getattr(_lineage, "mark", None)
    if mark is None:
        mark = _lineage.mark = object()
    return mark


def _track_thread_ancestry():
    """Idempotently teach threading.Thread to record its creator's lineage."""
    global _patched
    if _patched:
        return
    _patched = True
    original_start = threading.Thread.start

    def start(self):
        parent = threading.current_thread()
        setattr(self, _ANCESTORS, getattr(parent, _ANCESTORS, frozenset()) | {_thread_mark()})
        return original_start(self)

    threading.Thread.start = start


class QtStdoutBridge(QObject):
    """A duck-typed file-like object (drop-in for sys.stdout via
    contextlib.redirect_stdout -- NOT a real io.TextIOBase subclass, since that base
    class's metaclass conflicts with QObject's; write()/flush() are all
    contextlib.redirect_stdout actually requires) that splits incoming writes on
    '\\n' and '\\r' (print_progress's own overwrite-in-place convention, and tqdm's
    own \\r-redraw) and emits one Qt signal per completed/updated line, instead of
    buffering forever. Qt's own signal/slot connection queues this safely across
    threads for a normal (non-Qt.DirectConnection) connection, so the GUI thread
    receives each line in order without any extra locking needed here.

    IMPORTANT thread-safety note (confirmed live this session, found via a genuinely
    confusing bug: an end-to-end test's own print() calls on the main GUI thread
    were silently vanishing whenever a background job -- even just StormPage's own
    automatic active-storms fetch -- was in flight): contextlib.redirect_stdout/
    redirect_stderr reassign sys.stdout/sys.stderr, which are process-global
    attributes, NOT thread-local. Naively redirecting them for "the worker thread"
    actually redirects EVERY thread's print() for the duration, including the main
    GUI thread's own. Fixed by recording which thread this bridge actually belongs
    to (whichever thread first constructs it, i.e. the worker thread that calls
    Worker.run()) and the real original stream to fall back to: writes from any
    OTHER thread (the main GUI thread, or any other) pass straight through to that
    original stream unchanged, instead of being captured into this bridge's buffer
    and misattributed to (or silently lost from) this job's own progress log.
    """

    # bool arg: True if this line was terminated by a bare '\r' (print_progress/
    # tqdm's own "redraw this same line in place" convention) rather than a real
    # '\n' -- confirmed live this session: without this distinction, every consumer
    # (see process_page.py's Activity Log) had no way to tell "genuinely the next
    # line" from "still the same progress update, just redrawn again", so a single
    # download's dozens of \r-throttled percentage updates each landed as their own
    # permanent new line instead of overwriting the previous one in place the way a
    # real terminal renders \r -- exactly what a real terminal does, and exactly
    # what every consumer of this signal should do too.
    line_written = Signal(str, bool)

    def __init__(self, passthrough_stream):
        super().__init__()
        _track_thread_ancestry()
        self._buffer = ""
        # Capturing descendants means a thread POOL can now write here concurrently, so
        # the buffer is no longer touched by one thread alone. Lines are split under the
        # lock and emitted outside it (a queued signal must never run a GUI slot with a
        # lock held).
        self._lock = threading.Lock()
        self._owner = _thread_mark()
        self._passthrough_stream = passthrough_stream
        # A redraw shown before its line ended (see write): its text, so the same text is
        # not sent again when the next redraw or the final newline ends it.
        self._redrawing = False
        self._shown = None

    def claim_current_thread(self):
        """Make the thread calling this the bridge's own: a job's bridges are made on the
        GUI thread (worker.Worker.prepare) and claimed by the job's thread when it starts.
        A plain attribute -- no Qt call, so nothing here waits on Qt's locks."""
        self._owner = _thread_mark()

    def _owns_current_thread(self):
        """This job's own thread, or a worker it spawned (see _track_thread_ancestry).

        Descendants rather than "any non-GUI thread": two jobs can be in flight at once
        (a page's automatic storm fetch during a render), and each bridge must claim only
        its own subtree or their progress lines cross-attribute into the wrong log.
        """
        if _thread_mark() is self._owner:
            return True
        return self._owner in getattr(threading.current_thread(), _ANCESTORS, ())

    def write(self, s):
        if not s:
            return 0
        if not self._owns_current_thread():
            return self._passthrough_stream.write(s)
        ready = []
        with self._lock:
            self._buffer += s
            self._split_buffer(ready)
            # A redraw is shown as soon as it is written. print_progress writes "\r" + its
            # text with nothing after it, so the text used to wait for the NEXT write to end
            # it: every update reached the log one update (~2 s) late, and a download that
            # printed only 0% and then 100% showed nothing until it was done (reported
            # 2026-10-05). Shown now as an overwrite, it is replaced in place by whatever
            # ends it.
            tail = self._buffer.rstrip("\r")
            if self._redrawing and tail and tail != self._shown:
                ready.append((tail, True))
                self._shown = tail
        for line, is_overwrite in ready:
            self.line_written.emit(line, is_overwrite)
        return len(s)

    def _split_buffer(self, ready):
        """Peel every complete line off the buffer into `ready`. Caller holds the lock."""
        # Flush on '\n' (line finished) or a bare '\r' (an overwrite-in-place progress
        # update -- print_progress/tqdm both end each redraw with one). A '\r\n' PAIR,
        # though, is a single CRLF newline, NOT an overwrite: some tqdm/pqdm final-line
        # renders (and any CRLF source) end that way, and treating the '\r' as an
        # overwrite there let the very next log line clobber a genuinely-completed line.
        while True:
            n = self._buffer.find("\n")
            r = self._buffer.find("\r")
            if n == -1 and r == -1:
                break
            if r != -1 and (n == -1 or r < n):
                # A '\r' is the earliest terminator. If it's the last char in the
                # buffer we can't yet tell a bare '\r' from the '\r' of a '\r\n' split
                # across two writes -- leave it buffered until the next write decides.
                if r == len(self._buffer) - 1:
                    break
                is_overwrite = self._buffer[r + 1] != "\n"
                cut, advance = r, (1 if is_overwrite else 2)
            else:
                is_overwrite = False
                cut, advance = n, 1
            line, self._buffer = self._buffer[:cut], self._buffer[cut + advance:]
            # already on screen as the redraw it was (see write): sent again only when a
            # newline makes it final
            if line and not (is_overwrite and line == self._shown):
                ready.append((line, is_overwrite))
            self._shown, self._redrawing = None, is_overwrite

    def flush(self):
        self._passthrough_stream.flush()

    def close_and_flush_remainder(self):
        """Call once after a job finishes, in case the very last line had no
        trailing newline/carriage-return (print_progress's own final call already
        forces one, but be defensive for anything else that doesn't)."""
        # rstrip a trailing '\r' the write() loop deferred (its "can't tell bare '\r'
        # from a split '\r\n' yet" case) so a final redraw marker doesn't surface as
        # its own stray line.
        with self._lock:
            remainder = self._buffer.rstrip("\r")
            self._buffer, self._shown, self._redrawing = "", None, False
        if remainder:
            self.line_written.emit(remainder, False)
