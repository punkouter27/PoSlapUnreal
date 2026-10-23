"""Serve PoSlap's web version: python Web/serve.py, then open http://localhost:8080/Web/

Serves the repo root, so the page reads training/ (the arena XML, fingerprints, cards, ONNX brains) as the trainer
and Unreal do; .mjs and .wasm get their browser MIME types.
"""
import functools
import http.server
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript", ".wasm": "application/wasm", ".js": "text/javascript"}

    def end_headers(self):   # always the files on disk: new brains and code show on a plain reload
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


handler = functools.partial(Handler, directory=str(ROOT))
print("PoSlap: http://localhost:8080/Web/", flush=True)
http.server.ThreadingHTTPServer(("127.0.0.1", 8080), handler).serve_forever()
