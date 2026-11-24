"""Shared pytest fixtures: fake kiwix-tools (used by every test) and a local HTTP file
server (for download tests).

**fake_kiwix_tools** (autouse) points ``software.OPT_DIR`` and ``software.BIN_DIR`` at
temporary folders, and puts small Python stand-ins for ``kiwix-manage`` and
``kiwix-serve`` where the configured version (3.8.2) is expected. So no test reads or
changes the real ``~/.local``. The stand-ins:

- ``kiwix-manage LIB add ZIM...``: writes a library XML with one ``<book path="...">``
  per ZIM (absolute paths), or prints an error and exits 1 if a ZIM doesn't exist.
- ``kiwix-serve ...``: writes its arguments, one per line, to ``serve-args.txt`` next to
  itself, then waits. It exits 0 on SIGTERM, or with status 3 when a file named ``stop``
  appears next to it (so tests can end ``run_serve`` without sending signals).

It also replaces ``software._fetch`` with one that raises ``SoftwareError``, so
``update``'s kiwix-tools backup step never contacts download.kiwix.org. Tests of that
step replace it again.

**desktop** (autouse) points ``alert.DESKTOP`` at a temporary folder and returns it, so
no test writes a failure alert to the real ``~/Desktop``.

``file_server`` runs a ``ThreadingHTTPServer`` on ``127.0.0.1`` (a random free port) for
one test. Tests register content with ``server.add(path, data, mode)``, where ``mode``
picks the behaviour:

- ``"range"``: honours ``Range: bytes=N-`` with ``206`` and a correct ``Content-Range``.
- ``"drop-once"``: like ``"range"``, but the first request sends ``Content-Length`` for
  the full reply and then closes the connection halfway through the body.
- ``"no-range"``: ignores ``Range`` and always sends ``200`` with the whole body.
- ``"wrong-total"``: replies ``206`` with a ``Content-Range`` that names a different total.

Any other path returns 404. ``server.requests`` records ``(path, Range header)`` for every
request, so tests can check what was asked for. Used by ``test_download.py`` and
``test_update.py``; nothing leaves the machine.
"""

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from wikibackup import alert, software

FAKE_MANAGE = """\
import sys, os
lib, action, *zims = sys.argv[1:]
assert action == "add"
for z in zims:
    if not os.path.isfile(z):
        print(f"Cannot add ZIM {z} to the library.", file=sys.stderr)
        sys.exit(1)
books = "".join(f'  <book id="x{i}" path="{os.path.abspath(z)}" />\\n' for i, z in enumerate(zims))
with open(lib, "w") as f:
    f.write(f'<library version="20110515">\\n{books}</library>\\n')
"""

FAKE_SERVE = """\
import os, signal, sys, time
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "serve-args.txt"), "w") as f:
    f.write("\\n".join(sys.argv[1:]))
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
while not os.path.exists(os.path.join(here, "stop")):
    time.sleep(0.05)
sys.exit(3)
"""


def write_tool(path: Path, body: str) -> None:
    """Write an executable Python script that runs with this test's interpreter."""
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(0o755)


@pytest.fixture(autouse=True)
def fake_kiwix_tools(tmp_path_factory, monkeypatch) -> Path:
    """Install fake kiwix-tools 3.8.2 in a temporary OPT_DIR; return its folder."""
    root = tmp_path_factory.mktemp("local")
    monkeypatch.setattr(software, "OPT_DIR", root / "opt")
    monkeypatch.setattr(software, "BIN_DIR", root / "bin")
    tools = software.install_dir("3.8.2")
    tools.mkdir(parents=True)
    write_tool(tools / "kiwix-manage", FAKE_MANAGE)
    write_tool(tools / "kiwix-serve", FAKE_SERVE)

    def no_network(url):
        raise software.SoftwareError(f"network disabled in tests: {url}")

    monkeypatch.setattr(software, "_fetch", no_network)
    return tools


@pytest.fixture(autouse=True)
def desktop(tmp_path_factory, monkeypatch) -> Path:
    """A temporary stand-in for ``~/Desktop`` (``alert.DESKTOP``); return it."""
    folder = tmp_path_factory.mktemp("Desktop")
    monkeypatch.setattr(alert, "DESKTOP", folder)
    return folder


class FileServer:
    """The running server: its base URL, registered files, and a request log."""

    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}
        self.requests: list[tuple[str, str | None]] = []
        self.dropped: set[str] = set()
        self.url = ""

    def add(self, path: str, data: bytes, mode: str = "range") -> str:
        """Serve ``data`` at ``path`` with behaviour ``mode``; return its full URL."""
        self.files[path] = (data, mode)
        return self.url + path


def _handler(server: FileServer):
    """Build a request handler class bound to ``server``."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test output quiet
            pass

        def do_GET(self):
            range_header = self.headers.get("Range")
            server.requests.append((self.path, range_header))
            if self.path not in server.files:
                self.send_error(404)
                return
            data, mode = server.files[self.path]
            start = 0
            if range_header and mode != "no-range":
                start = int(range_header.removeprefix("bytes=").rstrip("-"))
            if start >= len(data) and mode != "no-range" and range_header:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.end_headers()
                return
            body = data[start:]
            if range_header and mode != "no-range":
                self.send_response(206)
                total = len(data) + (1 if mode == "wrong-total" else 0)
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{total}")
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if mode == "drop-once" and self.path not in server.dropped:
                server.dropped.add(self.path)
                self.wfile.write(body[: len(body) // 2])
                self.wfile.flush()
                self.close_connection = True
                return
            self.wfile.write(body)

    return Handler


@pytest.fixture
def file_server():
    """A local HTTP server for one test (see the module docstring)."""
    server = FileServer()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(server))
    server.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    # poll_interval: how often serve_forever checks for shutdown (default 0.5 s per test).
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield server
    httpd.shutdown()
    httpd.server_close()
