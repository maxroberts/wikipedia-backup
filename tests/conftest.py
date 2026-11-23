"""Shared pytest fixtures: a local HTTP file server for download tests.

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

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


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
