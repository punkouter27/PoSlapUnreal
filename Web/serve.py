"""Serve PoSlap's web version: python Web/serve.py, then open http://localhost:8080/Web/

Serves the repo root, so the page reads training/ (the arena XML, fingerprints, cards, ONNX brains) as the trainer
and Unreal do; .mjs and .wasm get their browser MIME types.

Only Web/ and training/ are served: the rest of the checkout (.git with the full history, Saved/, Logs/, the venv)
is refused, so --host 0.0.0.0 does not publish it to the network.
"""
import argparse
import functools
import http.server
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ("Web", "training")   # the two directories the page actually reads


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript", ".wasm": "application/wasm", ".js": "text/javascript"}

    def send_error(self, code, message=None, explain=None):
        if code == 404:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"not found")
            self.close_connection = True
            return
        super().send_error(code, message, explain)

    def do_GET(self):
        if not self._guard():
            return          # refuse: do NOT fall through and serve the file anyway
        try:
            super().do_GET()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, OSError):
            pass

    def do_HEAD(self):
        if not self._guard():
            return
        try:
            super().do_HEAD()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, OSError):
            pass

    def _guard(self):
        """Refuse anything outside Web/ and training/ (the .git directory, Saved/, Logs/, the venv).

        The check is on the RESOLVED path, not the request path: SimpleHTTPRequestHandler
        drops ".." segments, so "/Web/../AGENTS.md" would otherwise walk straight out of
        the allowed directories.
        """
        try:
            full = Path(super().translate_path(self.path)).resolve()
        except Exception:
            full = None
        allowed = [(ROOT / d).resolve() for d in ALLOWED]
        if full is None or not any(full == root or root in full.parents for root in allowed):
            self.send_error(404)
            return False
        return True

    def end_headers(self):   # always the files on disk: new brains and code show on a plain reload
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


ap = argparse.ArgumentParser()
ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 serves phones on the network (the second screen, Web/live.py)")
ap.add_argument("--port", type=int, default=8080)
opts = ap.parse_args()
host, port = opts.host, opts.port
handler = functools.partial(Handler, directory=str(ROOT))
print(f"PoSlap: http://{'localhost' if host == '127.0.0.1' else host}:{port}/Web/", flush=True)
server = http.server.ThreadingHTTPServer((host, port), handler)
while True:
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        break
    except Exception:
        continue
