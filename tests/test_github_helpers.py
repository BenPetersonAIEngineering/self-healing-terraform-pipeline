import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from healer.live import _github


class _RedirectingHandler(BaseHTTPRequestHandler):
    """A tiny local HTTP server: /start 302s to /blob, and /blob fails the
    test if it ever sees an Authorization header — reproducing the real
    401 seen from GitHub's job-logs endpoint redirecting to blob storage."""

    seen_auth_on_blob = None

    def do_GET(self):
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/blob")
            self.end_headers()
        elif self.path == "/blob":
            _RedirectingHandler.seen_auth_on_blob = self.headers.get("Authorization")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"log content")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def test_get_text_strips_authorization_header_on_redirect(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-test")
    _RedirectingHandler.seen_auth_on_blob = "not-yet-set"

    server = HTTPServer(("127.0.0.1", 0), _RedirectingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _github.get_text(f"http://127.0.0.1:{server.server_port}/start")
    finally:
        server.shutdown()
        thread.join()

    assert result == "log content"
    assert _RedirectingHandler.seen_auth_on_blob is None, "Authorization header must not survive the redirect"
