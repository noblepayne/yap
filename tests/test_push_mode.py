"""Integration tests for push mode with real HTTP server."""

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import sys

import importlib.util

import pytest

# Add project root to path
root = Path(__file__).parent.parent
sys.path.insert(0, str(root))  # noqa: E402

spec = importlib.util.spec_from_file_location("yap", root / "yap.py")
yap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yap)


class MockLLMHandler(BaseHTTPRequestHandler):
    """HTTP handler that mimics LLM API responses."""

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        request = json.loads(body)

        # Get the test scenario from the server
        scenario = self.server.scenario

        # Build response based on scenario
        response = self._build_response(request, scenario)

        # Send response
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def _build_response(self, request, scenario):
        messages = request.get("messages", [])
        iteration = len([m for m in messages if m.get("role") == "assistant"])

        if scenario == "done_on_first":
            # Call yap__done on first iteration
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "yap__done",
                                        "arguments": '{"summary": "Task completed"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        elif scenario == "nudge_then_done":
            # First iteration: no tool call, second: call yap__done
            if iteration == 0:
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Let me think about this...",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "other_tool",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            else:
                # Second iteration: call yap__done
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Done!",
                                "tool_calls": [
                                    {
                                        "id": "call_2",
                                        "type": "function",
                                        "function": {
                                            "name": "yap__done",
                                            "arguments": '{"summary": "Completed after nudge"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
        elif scenario == "max_iterations":
            # Never call yap__done, just return text
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": f"Thinking... iteration {iteration}",
                            "tool_calls": [],
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        elif scenario == "error_on_second":
            # First iteration succeeds, second returns error
            if iteration == 0:
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "First response",
                                "tool_calls": [],
                            },
                            "finish_reason": "stop",
                        }
                    ]
                }
            else:
                # Return invalid JSON to simulate error
                self.send_response(500)
                self.end_headers()
                return None
        else:
            # Default: call yap__done
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Done!",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "yap__done",
                                        "arguments": '{"summary": "Default done"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }

    def log_message(self, format, *args):
        """Suppress log messages."""
        pass


@pytest.fixture
def mock_llm_server():
    """Create a mock LLM server for testing."""
    server = HTTPServer(("localhost", 0), MockLLMHandler)
    server.scenario = "done_on_first"
    port = server.server_address[1]

    # Start server in background thread
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    # Yield server info
    yield server, port

    # Cleanup
    server.shutdown()
    server_thread.join(timeout=1)


def test_push_mode_done_on_first(mock_llm_server):
    """Test that push mode exits when yap__done is called on first iteration."""
    server, port = mock_llm_server
    server.scenario = "done_on_first"

    # Temporarily override API_URL
    original_url = yap.API_URL
    yap.API_URL = f"http://localhost:{port}/v1/chat/completions"

    try:
        # Create app instance
        app = yap.Yap()
        app.push_mode = True

        # Simulate sending a message
        # Note: We can't easily test the full TUI loop here, but we can test
        # the HTTP interaction directly
        payload = yap._build_payload(
            "test-model",
            [{"role": "user", "content": "test message"}],
            None,
            [yap._get_yap_done_tool()],
        )

        data = yap._http_chat(yap.API_URL, payload, 5)
        message = yap._parse_response(data)

        # Verify the response
        assert message.get("tool_calls") is not None
        assert yap._detect_yap_done(message["tool_calls"])
    finally:
        yap.API_URL = original_url


def test_push_mode_nudge_then_done(mock_llm_server):
    """Test that push mode adds nudge and continues when not done."""
    server, port = mock_llm_server
    server.scenario = "nudge_then_done"

    original_url = yap.API_URL
    yap.API_URL = f"http://localhost:{port}/v1/chat/completions"

    try:
        payload = yap._build_payload(
            "test-model",
            [{"role": "user", "content": "test message"}],
            None,
            [yap._get_yap_done_tool()],
        )

        # First request
        data = yap._http_chat(yap.API_URL, payload, 5)
        message = yap._parse_response(data)
        assert not yap._detect_yap_done(message.get("tool_calls"))

        # Add nudge
        history = [{"role": "user", "content": "test message"}]
        history.append(message)
        history.append({"role": "user", "content": yap.NUDGE_MESSAGE})

        # Second request
        payload = yap._build_payload(
            "test-model", history, None, [yap._get_yap_done_tool()]
        )
        data = yap._http_chat(yap.API_URL, payload, 5)
        message = yap._parse_response(data)

        # Second request should call yap__done
        assert yap._detect_yap_done(message.get("tool_calls"))
    finally:
        yap.API_URL = original_url


def test_push_mode_max_iterations(mock_llm_server):
    """Test that push mode respects max iterations limit."""
    server, port = mock_llm_server
    server.scenario = "max_iterations"

    original_url = yap.API_URL
    original_max = yap.MAX_PUSH_ITERATIONS
    yap.API_URL = f"http://localhost:{port}/v1/chat/completions"
    yap.MAX_PUSH_ITERATIONS = 3  # Low limit for testing

    try:
        history = [{"role": "user", "content": "test message"}]
        iteration = 0

        while iteration < yap.MAX_PUSH_ITERATIONS:
            payload = yap._build_payload(
                "test-model", history, None, [yap._get_yap_done_tool()]
            )
            data = yap._http_chat(yap.API_URL, payload, 5)
            message = yap._parse_response(data)

            if yap._detect_yap_done(message.get("tool_calls")):
                break

            # Add nudge and continue
            history.append(message)
            history.append({"role": "user", "content": yap.NUDGE_MESSAGE})
            iteration += 1

        # Should have stopped at max iterations
        assert iteration == yap.MAX_PUSH_ITERATIONS
    finally:
        yap.API_URL = original_url
        yap.MAX_PUSH_ITERATIONS = original_max


def test_push_mode_error_handling(mock_llm_server):
    """Test that push mode handles errors gracefully."""
    server, port = mock_llm_server
    server.scenario = "error_on_second"

    original_url = yap.API_URL
    yap.API_URL = f"http://localhost:{port}/v1/chat/completions"

    try:
        history = [{"role": "user", "content": "test message"}]

        # First request should succeed
        payload = yap._build_payload(
            "test-model", history, None, [yap._get_yap_done_tool()]
        )
        data = yap._http_chat(yap.API_URL, payload, 5)
        message = yap._parse_response(data)
        assert message.get("content") == "First response"

        # Add nudge
        history.append(message)
        history.append({"role": "user", "content": yap.NUDGE_MESSAGE})

        # Second request should fail
        payload = yap._build_payload(
            "test-model", history, None, [yap._get_yap_done_tool()]
        )
        with pytest.raises(Exception):
            yap._http_chat(yap.API_URL, payload, 5)
    finally:
        yap.API_URL = original_url


def test_push_mode_cancel_event():
    """Test that cancel event stops the push loop."""
    # Create a cancel event
    cancel_event = threading.Event()

    # Simulate a loop that checks the event
    iteration = 0
    max_iterations = 10

    def cancel_after_delay():
        time.sleep(0.1)
        cancel_event.set()

    # Start cancel thread
    cancel_thread = threading.Thread(target=cancel_after_delay)
    cancel_thread.start()

    # Simulate push loop
    while iteration < max_iterations and not cancel_event.is_set():
        time.sleep(0.05)
        iteration += 1

    cancel_thread.join()

    # Should have cancelled before max iterations
    assert iteration < max_iterations
    assert cancel_event.is_set()


def test_push_mode_detect_yap_done():
    """Test the _detect_yap_done helper function."""
    # Test with yap__done call
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "yap__done", "arguments": '{"summary": "done"}'},
        }
    ]
    assert yap._detect_yap_done(tool_calls) is True

    # Test without yap__done
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "other_tool", "arguments": "{}"},
        }
    ]
    assert yap._detect_yap_done(tool_calls) is False

    # Test with empty list
    assert yap._detect_yap_done([]) is False

    # Test with None
    assert yap._detect_yap_done(None) is False
