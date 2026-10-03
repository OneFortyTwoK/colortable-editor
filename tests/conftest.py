"""What every test shares: this folder's own copy of the program first on the import path,
no test reaching the internet, and the windows a test leaves behind freed on the main
thread (Qt crashes when a window is freed on any other)."""
import gc
import os
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_LOCAL = ("127.", "::1", "localhost", "0.0.0.0")


def _is_local(host):
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    return isinstance(host, str) and (host == "" or host.startswith(_LOCAL))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Opening a connection to anything but this computer fails the test at once."""
    real_connect, real_getaddrinfo = socket.socket.connect, socket.getaddrinfo

    def connect(self, address, *a, **k):
        if isinstance(address, tuple) and address and not _is_local(address[0]):
            raise AssertionError(f"test tried to connect to {address[0]!r}")
        return real_connect(self, address, *a, **k)

    def getaddrinfo(host, *a, **k):
        if host is not None and not _is_local(host):
            raise AssertionError(f"test tried to look up {host!r}")
        return real_getaddrinfo(host, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture(autouse=True)
def _qt_garbage_freed_here(request):
    yield
    if "qapp" in request.fixturenames:
        gc.collect()
