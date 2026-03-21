"""Tests for observability module (TypedDicts and header parsing)."""

import sys
from pathlib import Path


root = Path(__file__).parent.parent
sys.path.insert(0, str(root))

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("yap", root / "yap.py")
yap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yap)

parse_obs = yap.parse_obs
format_obs_status = yap.format_obs_status


class TestObsStateFromHeaders:
    def test_empty_headers_returns_empty_obs(self):
        obs = parse_obs({"content-type": "application/json"})
        assert obs["injector_present"] is False
        assert obs["turns"] == 0
        assert obs["tools_called"] == []
        assert obs["session_id"] == ""
        assert obs["ms"] == 0

    def test_full_headers_parses_correctly(self):
        headers = {
            "X-Injector-Version": "1",
            "X-Injector-Session": "abc123",
            "X-Injector-Turns": "3",
            "X-Injector-Tools": "mcp__stripe__foo,mcp__postgres__bar",
            "X-Injector-Ms": "842",
        }
        obs = parse_obs(headers)
        assert obs["injector_present"] is True
        assert obs["session_id"] == "abc123"
        assert obs["turns"] == 3
        assert obs["tools_called"] == ["mcp__stripe__foo", "mcp__postgres__bar"]
        assert obs["ms"] == 842

    def test_case_insensitive_header_keys(self):
        headers = {
            "x-injector-version": "1",
            "x-injector-session": "xyz",
            "x-injector-turns": "1",
        }
        obs = parse_obs(headers)
        assert obs["injector_present"] is True
        assert obs["session_id"] == "xyz"

    def test_malformed_turns_defaults_to_zero(self):
        headers = {"x-injector-version": "1", "x-injector-turns": "not-a-number"}
        obs = parse_obs(headers)
        assert obs["turns"] == 0

    def test_malformed_ms_defaults_to_zero(self):
        headers = {"x-injector-version": "1", "x-injector-ms": ""}
        obs = parse_obs(headers)
        assert obs["ms"] == 0

    def test_empty_tools_string_gives_empty_list(self):
        headers = {"x-injector-version": "1", "x-injector-tools": ""}
        obs = parse_obs(headers)
        assert obs["tools_called"] == []

    def test_tools_whitespace_stripped(self):
        headers = {
            "x-injector-version": "1",
            "x-injector-tools": " mcp__a__b , mcp__c__d ",
        }
        obs = parse_obs(headers)
        assert obs["tools_called"] == ["mcp__a__b", "mcp__c__d"]

    def test_none_headers_safe(self):
        obs = parse_obs({})
        assert obs["injector_present"] is False

    def test_no_version_header_means_not_present(self):
        headers = {
            "x-injector-session": "abc",
            "x-injector-turns": "1",
        }
        obs = parse_obs(headers)
        assert obs["injector_present"] is False


class TestObsStateStatusLine:
    def test_no_injector_returns_empty(self):
        obs = parse_obs({})
        assert format_obs_status(obs) == ""

    def test_with_tools_and_ms(self):
        obs = {
            "injector_present": True,
            "turns": 2,
            "tools_called": ["mcp__stripe__retrieve_customer"],
            "ms": 500,
        }
        line = format_obs_status(obs)
        assert "Turns: 2" in line
        assert "mcp__stripe__retrieve_customer" in line
        assert "500ms" in line

    def test_many_tools_truncated_in_display(self):
        obs = {
            "injector_present": True,
            "turns": 5,
            "tools_called": [f"mcp__s__tool{i}" for i in range(10)],
            "ms": 1000,
        }
        line = format_obs_status(obs)
        assert "+7 more" in line

    def test_no_tools_no_ms_minimal_display(self):
        obs = {
            "injector_present": True,
            "turns": 0,
            "tools_called": [],
            "ms": 0,
        }
        line = format_obs_status(obs)
        assert "Turns: 0" in line
        assert "Tools" not in line

    def test_single_tool_no_truncation(self):
        obs = {
            "injector_present": True,
            "turns": 1,
            "tools_called": ["mcp__git__status"],
            "ms": 50,
        }
        line = format_obs_status(obs)
        assert "mcp__git__status" in line
        assert "+" not in line

    def test_empty_tools_omitted_from_display(self):
        obs = {
            "injector_present": True,
            "turns": 1,
            "tools_called": [],
            "ms": 100,
        }
        line = format_obs_status(obs)
        assert "Turns: 1" in line
        assert "Tools" not in line
        assert "100ms" in line
