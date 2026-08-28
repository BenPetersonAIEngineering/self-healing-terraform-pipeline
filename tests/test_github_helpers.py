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


class _EchoPostHandler(BaseHTTPRequestHandler):
    """Echoes back the method, path, and JSON body it received, so the
    test can assert post_json actually sent a real POST with a JSON body
    and the Authorization header, not just that it didn't crash."""

    received = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        _EchoPostHandler.received = {
            "method": self.command,
            "path": self.path,
            "body": body.decode(),
            "auth": self.headers.get("Authorization"),
            "content_type": self.headers.get("Content-Type"),
        }
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_post_json_sends_a_real_post_with_json_body_and_auth(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-test")

    server = HTTPServer(("127.0.0.1", 0), _EchoPostHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = _github.post_json(f"http://127.0.0.1:{server.server_port}/comments", {"body": "hello"})
    finally:
        server.shutdown()
        thread.join()

    assert result == {"body": "hello"}
    assert _EchoPostHandler.received["method"] == "POST"
    assert _EchoPostHandler.received["auth"] == "Bearer fake-token-for-test"
    assert _EchoPostHandler.received["content_type"] == "application/json"
