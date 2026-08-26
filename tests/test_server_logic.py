"""Unit tests for ChatServer.resolve() — the response decision function.

These pin the script-position and fault-injection semantics WITHOUT
sockets. resolve() exists precisely so this logic is testable directly;
the Handler shim is transport-only.
"""

from helpers import ChatServer, make_chunks, make_toolcall_chunks


def test_script_advances_one_entry_per_call():
    """Regression: script position must advance across calls (this exact
    behavior was broken twice via self-vs-server confusion)."""
    server = ChatServer(script=[[{"v": "a"}], [{"v": "b"}], [{"v": "c"}]])
    assert server.resolve({"stream": True}) == ("stream", [{"v": "a"}])
    assert server.resolve({"stream": True}) == ("stream", [{"v": "b"}])
    assert server.resolve({"stream": True}) == ("stream", [{"v": "c"}])
    # Exhausted script repeats last entry
    assert server.resolve({"stream": True}) == ("stream", [{"v": "c"}])


def test_script_position_lock_allows_reset():
    server = ChatServer(script=[[{"v": "a"}], [{"v": "b"}]])
    server.resolve({"stream": True})
    server._script_pos = 0  # tests may rewind
    assert server.resolve({"stream": True}) == ("stream", [{"v": "a"}])


def test_json_body_overrides_everything():
    server = ChatServer(chunks=make_chunks("ignored"), json_body={"foo": 1})
    kind, value = server.resolve({"stream": True})
    assert (kind, value) == ("json", {"foo": 1})


def test_nonstreaming_assembles_chunks_to_json():
    server = ChatServer(chunks=make_chunks("hello"))
    kind, value = server.resolve({"stream": False})
    assert kind == "json"
    assert value["choices"][0]["message"]["content"]


def test_nonstreaming_status_injection_hoisted():
    """Fault injection must apply on the NON-streaming path too — clients
    without on_delta send non-streaming requests (push-mode loop does)."""
    server = ChatServer(script=[[{"status": 500}]])
    assert server.resolve({}) == ("error", 500)
    server = ChatServer(script=[[{"status": 502}]])
    assert server.resolve({}) == ("error", 502)


def test_streaming_first_event_status_injection():
    server = ChatServer(script=[[{"status": 500}, {"choices": []}]])
    assert server.resolve({"stream": True}) == ("error", 500)


def test_streaming_mid_stream_status_raises():
    """A status event after other events cannot be honored once headers
    are framed — raise instead of emitting garbage."""
    server = ChatServer(
        chunks=[
            {"choices": [{"delta": {"content": "x"}}]},
            {"status": 500},
        ]
    )
    try:
        server.resolve({"stream": True})
        assert False, "should have raised"
    except ValueError:
        pass


def test_delay_only_events_still_stream():
    """delay_ms entries are skipped when finding the first effective event
    but preserved in the event list for the shim's incremental framing."""
    events = [{"delay_ms": 500}, {"choices": [{"delta": {"content": "x"}}]}]
    server = ChatServer(chunks=events)
    kind, value = server.resolve({"stream": True})
    assert kind == "stream"
    assert value is events  # shim needs the delay entries intact


def test_toolcall_chunks_resolve_and_assemble():
    """End-to-end through assembly: whole-call tool_calls delta produces a
    complete tool_calls message (the push-mode contract)."""
    server = ChatServer(
        chunks=make_toolcall_chunks(
            [{"id": "call_1", "name": "yap__done", "arguments": '{"summary": "done"}'}]
        )
    )
    kind, value = server.resolve({})  # non-streaming decision path
    assert kind == "json"
    msg = value["choices"][0]["message"]
    assert msg["tool_calls"][0]["function"]["name"] == "yap__done"
    assert value["choices"][0]["finish_reason"] == "tool_calls"
