"""Shared helpers for integration tests.

ChatServer (local SSE endpoint) and fixture utilities used by both
conftest.py (pytest fixture) and test_http.py (direct imports).
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from yap_module import yap

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class ChatServer:
    """Configurable fake chat-completions endpoint."""

    def __init__(self, chunks=None, require_auth=None, json_body=None):
        self.chunks = chunks or []
        self.require_auth = require_auth
        self.json_body = json_body  # dict: served verbatim as JSON regardless of stream flag
        self.requests = []
        self._server = None
        self._thread = None

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                content_len = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(content_len))
                auth = self.headers.get("Authorization")
                if server.require_auth and auth != server.require_auth:
                    self.send_response(401)
                    self.end_headers()
                    return

                server.requests.append(body)

                streaming = body.get("stream") is True
                if server.json_body is not None:
                    # Serve captured JSON verbatim — simulates an endpoint that
                    # ignores stream:true and always replies with JSON.
                    data = json.dumps(server.json_body).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if not streaming:
                    assembled, usage = yap._assemble_from_chunks(server.chunks)
                    payload = {**assembled}
                    if usage:
                        payload["usage"] = usage
                    data = json.dumps(payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()

                for event in server.chunks:
                    delay_ms = event.get("delay_ms")
                    if delay_ms:
                        time.sleep(delay_ms / 1000)
                        continue
                    if "status" in event:
                        err = json.dumps(
                            {"error": {"message": "injected failure", "type": "server_error"}}
                        ).encode()
                        self.send_response(event["status"])
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(err)))
                        self.end_headers()
                        self.wfile.write(err)
                        return
                    if "raw" in event:
                        self.wfile.write(event["raw"].encode())
                        self.wfile.flush()
                        continue
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1/chat/completions"

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)  # wait for in-flight handler


def load_fixture(name):
    """Load a chunk sequence from tests/fixtures/<name>.jsonl.

    Lines may have SSE framing (e.g. 'data: {...}') — the prefix is
    stripped before JSON parsing. Lines ending in '[DONE]' are skipped.
    """
    path = FIXTURES_DIR / f"{name}.jsonl"
    events = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip SSE 'data: ' prefix
        if line.startswith("data: "):
            line = line[len("data: "):]
        if line == "[DONE]":
            continue
        events.append(json.loads(line))
    return events


def make_chunks(text, usage=None, finish_reason="stop"):
    """Build a simple text-streaming chunk sequence (one delta per word).

    Useful for tests that need control over timing without a fixture file.
    """
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
    ]
    words = text.split(" ")
    for i, w in enumerate(words):
        suffix = "" if i == len(words) - 1 else " "
        chunks.append(
            {
                "choices": [
                    {"index": 0, "delta": {"content": w + suffix}, "finish_reason": None}
                ]
            }
        )
    final = {"choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]}
    if usage:
        final["usage"] = usage
    chunks.append(final)
    chunks.append({"done": True})
    return chunks
