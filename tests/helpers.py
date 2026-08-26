"""Shared helpers for integration tests.

ChatServer: a local threaded chat-completions endpoint. Real HTTP over
localhost, no mocks. Supports four behaviors:

- chunks:     static event list replayed for every request (SSE if the
              client asks for streaming, assembled JSON otherwise)
- script:     list of chunk-lists, consumed one per request (last repeats);
              lets tests script multi-turn conversations
- json_body:  dict served verbatim as JSON regardless of the stream flag
              (simulates endpoints that ignore stream:true)
- faults:     {"status": N} entries inject HTTP errors; {"delay_ms": N}
              stalls delivery; {"raw": "..."} emits verbatim bytes

Response selection lives in ChatServer.resolve() (a decision function,
unit-testable without sockets); the Handler shim only does transport:
parse, auth, record, frame.
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

    def __init__(self, chunks=None, require_auth=None, json_body=None, script=None):
        self.chunks = chunks or []
        self.require_auth = require_auth
        self.json_body = json_body  # dict: served verbatim regardless of stream flag
        self.script = list(script) if script else None  # chunk-list per request
        self._script_pos = 0
        self._script_lock = threading.Lock()
        self.requests = []  # recorded request bodies for assertions
        self._server = None
        self._thread = None

    def resolve(self, body):
        """Decide the response for a parsed request body.

        Returns one of:
          ("error", http_status)   -- injected fault
          ("json", dict)           -- complete JSON body
          ("stream", [events])     -- SSE events for the shim to frame

        Dynamic lookup throughout: tests mutate attributes after start().
        """
        if self.script is not None:
            with self._script_lock:
                idx = min(self._script_pos, len(self.script) - 1)
                events = self.script[idx]
                self._script_pos += 1
        else:
            events = self.chunks

        # json_body mode wins over everything: endpoint ignores stream flag.
        if self.json_body is not None:
            return ("json", self.json_body)

        streaming = body.get("stream") is True

        if not streaming:
            # Fault injection applies in BOTH modes: non-streaming clients
            # (no on_delta) must see injected errors too.
            for event in events:
                if "status" in event:
                    return ("error", event["status"])
            assembled, usage = yap._assemble_from_chunks(events)
            payload = {**assembled}
            if usage:
                payload["usage"] = usage
            return ("json", payload)

        # Streaming: a status event is only meaningful as the first
        # effective event (headers not yet sent). Later is a script bug —
        # fail loudly instead of emitting garbage frames.
        first = next((e for e in events if "delay_ms" not in e), None)
        if first is not None and "status" in first:
            return ("error", first["status"])
        for i, event in enumerate(events):
            if "delay_ms" not in event and "status" in event:
                raise ValueError(
                    f"status injection must be the first effective event "
                    f"(found at position {i} after response framing)"
                )
        return ("stream", events)

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            """Transport shim: parse, auth, record, frame. All decisions
            live in server.resolve(). NOTE: `self` here is the Handler;
            server state is always accessed through the `server` closure."""

            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    body = json.loads(
                        self.rfile.read(int(self.headers.get("Content-Length", 0)))
                    )
                except Exception:
                    self.send_response(400)
                    self.end_headers()
                    return

                if server.require_auth and (
                    self.headers.get("Authorization") != server.require_auth
                ):
                    self.send_response(401)
                    self.end_headers()
                    return

                server.requests.append(body)

                try:
                    kind, value = server.resolve(body)
                except Exception as e:
                    # Explicit 500 beats connection-reset + client retry burn
                    err = json.dumps({"error": {"message": f"server error: {e}"}}).encode()
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err)))
                    self.end_headers()
                    self.wfile.write(err)
                    return

                if kind == "error":
                    err = json.dumps(
                        {
                            "error": {
                                "message": "injected failure",
                                "type": "server_error",
                            }
                        }
                    ).encode()
                    self.send_response(value)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err)))
                    self.end_headers()
                    self.wfile.write(err)
                    return

                if kind == "json":
                    data = json.dumps(value).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

                # kind == "stream": frame incrementally — delays, raw
                # passthrough, flush per event. Fidelity matters here
                # (mid-stream cancel tests depend on it).
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event in value:
                    delay_ms = event.get("delay_ms")
                    if delay_ms:
                        time.sleep(delay_ms / 1000)
                        continue
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


def make_toolcall_chunks(calls, content=None, finish_reason="tool_calls"):
    """Build a chunk sequence delivering whole tool calls (groq-style).

    calls: list of dicts with keys name, arguments, optional id. Emitted as
    one tool_calls delta — yap's assembly handles both whole and fragmented.
    """
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]}
    ]
    if content:
        chunks.append({"choices": [{"index": 0, "delta": {"content": content}}]})
    tcs = []
    for i, c in enumerate(calls):
        tc = {"index": i}
        if c.get("id"):
            tc["id"] = c["id"]
        fn = {"name": c["name"]}
        if "arguments" in c:
            fn["arguments"] = c["arguments"]
        tc["function"] = fn
        tcs.append(tc)
    chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": tcs}}]})
    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]})
    chunks.append({"done": True})
    return chunks
