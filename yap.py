#!/usr/bin/env python3
"""
yap - terminal LLM chat TUI
Keyboard-driven. Gets out of your way.
"""

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import List, TypedDict

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    RadioButton,
    RadioSet,
    Static,
    Switch,
    TextArea,
)
from textual_fspicker import FileOpen, Filters


class HTTPResult(TypedDict):
    data: dict
    headers: dict[str, str]


class ObsState(TypedDict):
    session_id: str
    turns: int
    tools_called: List[str]
    ms: int
    injector_present: bool


API_URL = os.environ.get("YAP_API_URL", "http://lattice:8089/v1/chat/completions")
TIMEOUT = int(os.environ.get("YAP_TIMEOUT", 3600))
HISTORY_FILE = Path(os.environ.get("YAP_HISTORY_FILE", "chat_history.jsonl"))
LAST_RESPONSE_FILE = Path(os.environ.get("YAP_LAST_RESPONSE_FILE", "last_response.md"))
MAX_HISTORY = int(os.environ.get("YAP_MAX_HISTORY", 50))
MAX_PUSH_ITERATIONS = int(os.environ.get("YAP_MAX_PUSH_ITERATIONS", 10))

YAP_PROVIDER = os.environ.get("YAP_PROVIDER", "anthropic")

NUDGE_MESSAGE = (
    "CONTINUE. This is iteration {iteration}. You are in a multi-step loop.\n\n"
    "CRITICAL:\n"
    "1. If all requested work is verified complete: YOU MUST CALL yap__done(summary='...') NOW.\n"
    "2. If work remains: state exactly what is missing and take the NEXT step immediately.\n\n"
    "Do not restart the task or re-run successful tools. "
    "Reason from fresh observations, not memory."
)

PUSH_MODE_ITERATION_WARNING = (
    "\n\nWARNING: You are on iteration {iteration}/{max}. "
    "If the task is complete, call yap__done NOW. Continuing without calling yap__done "
    "may result in the loop being cancelled."
)

YAP_DONE_TOOL_NAME = "yap__done"

PUSH_MODE_DISCLOSURE = """## Push Mode
After your response, if yap__done() was not called, your response will be 
re-submitted to you with a nudge to continue. Call yap__done(summary) to 
signal completion. After yap__done, you receive one final clean response 
round (no tools).
Status: {status}"""

PUSH_MODE_SUMMARY_REQUEST = (
    "Task marked complete via yap__done. Provide a complete summary in plain "
    "markdown of what was accomplished, any issues encountered, and the final "
    "state. Be thorough but concise."
)

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s"
)

MODEL_MAP = {
    "model-free": "openrouter/openrouter/free",
    "model-deepseek": "openrouter/deepseek/deepseek-v3.2",
    "model-hunter": "openrouter/openrouter/hunter-alpha",
    "model-healer": "openrouter/openrouter/healer-alpha",
    "model-brian": "brian",
    "model-custom": "custom",
}


# === PURE FUNCTIONS ===


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences from a string."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def empty_obs() -> ObsState:
    """Factory for an inactive ObsState with safe defaults."""
    return ObsState(
        session_id="",
        turns=0,
        tools_called=[],
        ms=0,
        injector_present=False,
    )


def parse_obs(headers: dict[str, str]) -> ObsState:
    """Parse X-Injector-* headers into ObsState.

    Case-insensitive header matching. Safe to call with any headers dict.
    Returns empty ObsState if no injector headers present.
    """
    normalised = {k.lower(): v for k, v in headers.items()}

    present = "x-injector-version" in normalised
    if not present:
        return empty_obs()

    session = normalised.get("x-injector-session", "")

    turns_raw = normalised.get("x-injector-turns", "0")
    try:
        turns = int(turns_raw)
    except (ValueError, TypeError):
        turns = 0

    tools_raw = normalised.get("x-injector-tools", "")
    tools = [t.strip() for t in tools_raw.split(",") if t.strip()]

    ms_raw = normalised.get("x-injector-ms", "0")
    try:
        ms = int(ms_raw)
    except (ValueError, TypeError):
        ms = 0

    return ObsState(
        session_id=session,
        turns=turns,
        tools_called=tools,
        ms=ms,
        injector_present=True,
    )


def format_obs_status(obs: ObsState, max_tools: int = 3) -> str:
    """Format a one-line status string for the UI status bar.

    Args:
        obs: The Observability state to format.
        max_tools: Maximum number of tools to display before truncation.

    Returns empty string if injector not present.
    """
    if not obs["injector_present"]:
        return ""
    parts = [f"Turns: {obs['turns']}"]
    if obs["tools_called"]:
        tools_display = ", ".join(obs["tools_called"][:max_tools])
        if len(obs["tools_called"]) > max_tools:
            tools_display += f" +{len(obs['tools_called']) - max_tools} more"
        parts.append(f"Tools: {tools_display}")
    if obs["ms"]:
        parts.append(f"{obs['ms']}ms")
    return " | ".join(parts)


def _safe_write(path: Path, content: str) -> None:
    """Atomic write to a file to prevent corruption."""
    dir_path = path.parent
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=dir_path, delete=False, encoding="utf-8"
        ) as tf:
            tf.write(content)
            tempname = tf.name
        os.replace(tempname, path)
    except Exception as e:
        logging.error(f"Failed to write to {path}: {e}")


def derive_session_id(history_path: str | Path) -> str:
    """Derive a stable session ID from the history file path.
    Uses SHA-256 of absolute path, truncated to 16 chars."""
    canonical = os.path.abspath(str(history_path))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _extract_thoughts(content_blocks: list[dict] | str) -> tuple[list[str], str]:
    """
    Separate thinking blocks from text blocks in a content block array.

    Returns:
        thoughts: list of reasoning strings (in order)
        text:     concatenated text content for display
    """
    if isinstance(content_blocks, str):
        # Legacy plain-string content — no thoughts, return as-is
        return [], content_blocks

    thoughts = [
        b["thinking"]
        for b in content_blocks
        if b.get("type") == "thinking" and "thinking" in b
    ]
    text = "\n\n".join(
        b["text"] for b in content_blocks if b.get("type") == "text" and "text" in b
    )
    return thoughts, text


def _unify_message(message: dict) -> dict:
    """
    Unify different reasoning fields into standard Anthropic-style content blocks.
    Ensures message['content'] is always a list of blocks.
    """
    content = message.get("content") or ""
    if isinstance(content, str):
        blocks = [{"type": "text", "text": _strip_ansi(content)}]
    else:
        blocks = []
        for block in content:
            if block.get("type") == "text" and "text" in block:
                blocks.append({"type": "text", "text": _strip_ansi(block["text"])})
            elif block.get("type") == "thinking" and "thinking" in block:
                blocks.append(
                    {"type": "thinking", "thinking": _strip_ansi(block["thinking"])}
                )
            else:
                blocks.append(block)

    # Collect and unify legacy thoughts
    existing_thinking = {b["thinking"] for b in blocks if b.get("type") == "thinking"}
    for field in ["reasoning_content", "thought"]:
        if field in message and message[field]:
            thought_val = _strip_ansi(str(message[field]))
            if thought_val not in existing_thinking:
                blocks.insert(0, {"type": "thinking", "thinking": thought_val})
                existing_thinking.add(thought_val)

    message["content"] = blocks
    return message


def _prepare_history_for_request(
    history: list[dict], outbound_provider: str
) -> list[dict]:
    """
    Project history into a form safe to send to outbound_provider.

    For each message:
    - If the message contains thinking blocks AND was generated by a different
      provider than outbound_provider, replace those blocks with a text placeholder.
    - Strip _meta from all messages before sending.

    Does not modify history in place — returns a new list.
    """
    result = []

    for message in history:
        source_provider = (message.get("_meta") or {}).get("provider", "unknown")
        content = message.get("content", [])

        if isinstance(content, str):
            # Plain string content — strip _meta and pass through
            result.append({k: v for k, v in message.items() if k != "_meta"})
            continue

        # Check for thinking blocks
        has_thinking = any(b.get("type") == "thinking" for b in content)

        if has_thinking and source_provider != outbound_provider:
            # Replace thinking blocks with a neutral placeholder
            new_content = []
            for block in content:
                if block.get("type") == "thinking":
                    new_content.append(
                        {
                            "type": "text",
                            "text": "[reasoning from prior turn omitted: provider mismatch]",
                        }
                    )
                else:
                    new_content.append(block)
            result.append(
                {
                    **{k: v for k, v in message.items() if k != "_meta"},
                    "content": new_content,
                }
            )
        else:
            result.append({k: v for k, v in message.items() if k != "_meta"})

    return result


def _build_payload(
    model: str,
    messages: list,
    system_prompt: str | None = None,
    tools: list | None = None,
    push_mode: bool | None = None,
    extra_body: dict | None = None,
    reasoning_effort: str | None = None,
    include_search: bool | None = None,
) -> dict:
    """Build request payload."""
    parts = []
    if push_mode is not None:
        status = "ON" if push_mode else "OFF"
        parts.append(PUSH_MODE_DISCLOSURE.format(status=status))
    if system_prompt:
        parts.append(system_prompt)
    if parts:
        system = "\n\n".join(parts)
        payload_messages = [{"role": "system", "content": system}]
    else:
        payload_messages = []
    payload_messages.extend(messages)
    payload = {"model": model, "messages": payload_messages}
    if tools is not None:
        payload["tools"] = tools
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    if include_search:
        payload["plugins"] = [{"id": "web"}]
    if extra_body:
        payload["extra_body"] = extra_body
    return payload


def _get_yap_done_tool() -> dict:
    """Return the yap__done tool definition for signaling completion."""
    return {
        "type": "function",
        "function": {
            "name": YAP_DONE_TOOL_NAME,
            "description": "Call this when you have completed the task and no further tool calls or reasoning are required.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what was accomplished.",
                    }
                },
                "required": ["summary"],
            },
        },
    }


def _detect_yap_done(tool_calls: list[dict] | None) -> bool:
    """Detect if yap__done tool was called."""
    if not tool_calls:
        return False
    return any(
        tc.get("function", {}).get("name") == YAP_DONE_TOOL_NAME for tc in tool_calls
    )


def _parse_response(data: dict) -> dict:
    """Extract message object from API response and unify reasoning."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Missing or empty 'choices' in response")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("Missing 'message' in choice")

    # REVISE: Unify all reasoning into content blocks (SPEC_YAP P3)
    return _unify_message(message)


def _truncate_history(history: list, max_size: int) -> list:
    """Truncate history to max size."""
    if len(history) > max_size:
        return history[-max_size:]
    return history


def _format_chat_display(history: list, show_reasoning: bool = True) -> str:
    """Format history for display with tool call awareness."""
    if not history:
        return "Session started. No messages yet."

    formatted = []
    for msg in history:
        role = msg.get("role", "UNKNOWN").upper()
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")

        # New reasoning extraction (T2)
        thoughts, display_text = _extract_thoughts(content)

        # Expert Transparency: Handle reasoning/thoughts (DeepSeek/O1 style fallback)
        reasoning_api = msg.get("reasoning_content") or msg.get("thought")
        if reasoning_api:
            thoughts.append(_strip_ansi(str(reasoning_api)))

        # Visual styling for thoughts (SPEC §4.2)
        if thoughts and show_reasoning:
            reasoning_blocks = []
            for thought in thoughts:
                reasoning_blocks.append(f"▶ Reasoning\n{thought}")
            display_content = "\n\n".join(reasoning_blocks) + f"\n\n{display_text}"
        elif thoughts and not show_reasoning:
            # Optionally, we could still include a placeholder to indicate thoughts were suppressed
            display_content = f"...thinking suppressed...\n\n{display_text}"
        else:
            display_content = display_text

        # Handle tool calls in assistant messages
        if tool_calls:
            try:
                # Expert transparency: include IDs for easier proxy/injector log matching
                calls_formatted = []
                for call in tool_calls:
                    call_id = call.get("id", "no-id")
                    func = call.get("function", {})
                    name = func.get("name", "unknown")
                    args = func.get("arguments", "{}")
                    calls_formatted.append(f"[CALL:{call_id}] {name}({args})")

                calls_str = "\n".join(calls_formatted)
                display_content += f"\n\n[TOOL CALLS]\n{calls_str}"
            except Exception:
                display_content += f"\n\n[TOOL CALLS] {tool_calls}"

        # Handle tool role messages
        if role == "TOOL":
            tool_name = msg.get("name", "unknown")
            tool_id = msg.get("tool_call_id", "no-id")
            formatted.append(
                f"[TOOL: {tool_name} | ID: {tool_id}]\n{display_content}\n" + ("-" * 40)
            )
        else:
            formatted.append(f"[{role}]\n{display_content}\n" + ("-" * 40))

    return "\n\n".join(formatted)


# TODO: Future enhancement - Rich Reasoning Display
# -------------------------------------------------
# Current implementation shows reasoning as plain "THOUGHTS:" prefix.
# For better visibility and separation, consider:
#
# 1. Parsing Strategies:
#    - Check for dedicated fields: message.get("reasoning") or message.get("reasoning_content")
#    - Parse XML-style tags from content: <thought>...</thought> or <reasoning>...</reasoning>
#    - Handle both simultaneously (tags take precedence if both exist)
#
# 2. Visual Presentation Options:
#    - Use Textual markup for styling: [dim]...[/dim] or [italic]...[/italic]
#    - Create collapsible sections with headers (requires custom widget)
#    - Use ASCII art boxes or borders to separate reasoning from main content
#    - Examples:
#        ╭─ Reasoning ────────────────────────
#        │ I need to check the file contents...
#        ╰───────────────────────────────────
#        [DIM]Reasoning: I will first list the directory[/DIM]
#
# 3. Layout Considerations:
#    - Default to showing reasoning? Or make it user-toggleable?
#    - Should reasoning appear above or below the main assistant message?
#    - How to handle very long reasoning (scrolling, truncation)?
#
# 4. Implementation Approach:
#    - Modify _parse_response to extract reasoning into a standardized field
#    - Update _format_chat_display to apply styling and layout
#    - Consider adding a toggle in settings or via keyboard shortcut
#
# 5. Epistemological Note:
#    This display should reinforce the push mode nudge: the model must
#    verify state through action, not just reason internally. Seeing
#    the reasoning helps users audit whether the model is grounding
#    its actions in observed facts.
#
# For now, the basic "THOUGHTS:" prefix maintains backward compatibility
# while providing minimal transparency into the model's process.


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def _count_context(system_prompt: str, history: list) -> tuple[int, int]:
    """Count chars and estimated tokens in context."""
    chars = len(system_prompt)
    for msg in history:
        # Crash fix: content can be None (e.g. assistant msg with tool calls)
        content = msg.get("content") or ""
        if isinstance(content, list):
            chars += len(json.dumps(content))
        else:
            chars += len(str(content))

        # Expert Transparency: include tool_calls in context pressure
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            try:
                # We serialize to get a realistic character count of what the LLM "sees"
                chars += len(json.dumps(tool_calls))
            except Exception:
                pass

        # Expert Transparency: include reasoning/thought if present (e.g. DeepSeek/O1)
        reasoning = msg.get("reasoning_content") or msg.get("thought")
        if reasoning:
            chars += len(str(reasoning))

        # Metadata overhead (approx 4 tokens / 16 chars per message)
        chars += 16

    tokens = chars // 4
    return chars, tokens


# === SIDE EFFECTS ===


def _http_chat(
    url: str,
    payload: dict,
    timeout: int,
    session: requests.Session | None = None,
    cancel_event: threading.Event | None = None,
) -> HTTPResult:
    """Make HTTP POST to LLM endpoint with retries."""
    s = session or requests.Session()

    def _should_retry(retry_state):
        if cancel_event and cancel_event.is_set():
            return False
        return retry_if_exception_type(
            (
                requests.exceptions.RequestException,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError,
            )
        )(retry_state)

    def _cancel_sleep(seconds: float) -> None:
        """Sleep that can be interrupted by cancel_event."""
        if cancel_event:
            cancel_event.wait(timeout=seconds)
        else:
            time.sleep(seconds)

    @retry(
        retry=_should_retry,
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logging.getLogger("yap"), logging.WARNING),
        reraise=True,
        sleep=_cancel_sleep,
    )
    def _do_post():
        r = s.post(url, json=payload, timeout=timeout)
        # Retry on 5xx server errors
        if 500 <= r.status_code < 600:
            r.raise_for_status()
        r.raise_for_status()
        return HTTPResult(data=r.json(), headers=dict(r.headers))

    return _do_post()


def _load_history(path: Path) -> list:
    """Load chat history from JSONL file."""
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception as e:
        logging.warning(f"Failed to load history: {e}")
        return []


def _save_history(path: Path, history: list) -> None:
    """Save chat history to JSONL file."""
    content = "".join(json.dumps(msg) + "\n" for msg in history)
    _safe_write(path, content)


def _load_prompt_file(path: Path) -> str:
    """Load prompt from file."""
    return path.read_text(encoding="utf-8")


# === TEXTUAL WIDGETS ===


class ChatInput(TextArea):
    """Custom input widget that handles send keys locally."""

    def action_send(self) -> None:
        if self.text.strip():
            self.app.action_send()

    def action_clear(self) -> None:
        self.text = ""
        if hasattr(self.app, "update_status"):
            self.app.update_status("Input Cleared")


# === APP (IMPERATIVE SHELL) ===


class Yap(App):
    CSS = """
    Screen {
        layout: horizontal;
    }

    #config {
        width: 30;
        border: solid $primary;
    }

    #main {
        width: 1fr;
    }

    #chat-container {
        height: 1fr;
        border: solid $accent;
    }

    #input-container {
        height: 12;
        border: solid $success;
    }

    #status {
        height: 1;
        background: $surface;
        padding: 0 1;
    }

    #status.error {
        color: red;
    }

    #status.success {
        color: green;
    }

    #status.normal {
        color: white;
    }

    #status.loading {
        color: yellow;
    }

    .debug-info {
        display: none;
        background: $surface;
        color: $accent;
        padding: 0 1;
        height: auto;
        border-top: solid $primary;
    }

    .debug-info.visible {
        display: block;
    }

    RadioButton {
        margin: 0 1;
    }

    #system-prompt {
        height: 1fr;
        min-height: 10;
    }

    .header-text {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #button-row {
        height: auto;
        layout: horizontal;
    }
    """

    BINDINGS = [
        ("ctrl+s", "send", "Send"),
        ("ctrl+l", "clear_history", "Clear History"),
        ("ctrl+u", "clear_input", "Clear Input"),
        ("ctrl+p", "toggle_push", "Toggle Push"),
        ("ctrl+m", "toggle_debug", "Toggle Metadata"),
        ("ctrl+enter", "send", "Send"),
        ("q", "quit", "Quit"),
        ("escape", "cancel_push", "Cancel Push"),
    ]

    is_loading = reactive(False)
    push_mode = reactive(False)
    debug_mode = reactive(False)
    web_search = reactive(False)
    reasoning_effort = reactive("low")  # default to low
    show_reasoning = reactive(True)  # default to show reasoning

    def __init__(self):
        super().__init__()
        self.history = []
        self._history_lock = threading.Lock()
        self._push_cancelled = threading.Event()
        self._http_session: requests.Session | None = None
        self._http_session_lock = threading.Lock()
        self.session_id = derive_session_id(HISTORY_FILE)
        self.last_obs: ObsState = empty_obs()
        self._load_history()
        logging.info(
            f"YAP_PROVIDER={YAP_PROVIDER} session_id={self.session_id} "
            f"history_file={HISTORY_FILE}"
        )

    def update_session_id(self, session_id: str) -> None:
        """Update the session ID from a background thread."""
        self.session_id = session_id

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="config"):
                yield Static("CONFIG", classes="header-text")
                yield Static("Model Selection:")
                with RadioSet(id="model-select"):
                    yield RadioButton("OpenRouter Free", value=True, id="model-free")
                    yield RadioButton("DeepSeek v3.2", id="model-deepseek")
                    yield RadioButton("Hunter Alpha", id="model-hunter")
                    yield RadioButton("Healer Alpha", id="model-healer")
                    yield RadioButton("Brian (Custom)", id="model-brian")
                    yield RadioButton("Custom Model", id="model-custom")
                yield Static("Custom Model Name:")
                yield Input(
                    placeholder="org/model...", id="custom-model", disabled=True
                )
                yield Static("System Prompt:")
                yield TextArea(id="system-prompt")
                yield Static("Web Search:")
                yield Switch(id="web-search", value=False)
                yield Static("Reasoning Effort:")
                with RadioSet(id="reasoning-effort"):
                    yield RadioButton("Low", value=True, id="reasoning-low")
                    yield RadioButton("Medium", id="reasoning-medium")
                    yield RadioButton("High", id="reasoning-high")
                yield Static("Show Reasoning:")
                yield Switch(id="show-reasoning", value=True)
                yield Button("Load Prompt", id="load-prompt", variant="primary")
                yield Button("Load History", id="load-history", variant="primary")
                yield Static("Context: 0 chars | ~0 tokens", id="context-stats")
            with Vertical(id="main"):
                with Vertical(id="chat-container"):
                    yield Static("CONVERSATION", classes="header-text")
                    yield TextArea(read_only=True, id="chat-history")
                    yield Static(id="metadata-debug", classes="debug-info")
                yield Static("Push Mode:")
                yield Button("Push Mode: Off", id="push-mode-toggle", variant="default")
                with Vertical(id="input-container"):
                    yield Static("INPUT", classes="header-text")
                    yield ChatInput(id="user-input")
                yield Static(id="status", classes="normal")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_chat_display()
        self._refresh_context_stats()
        self._update_status_text("Ready")

    def _load_history(self) -> None:
        self.history = _load_history(HISTORY_FILE)
        self.history = _truncate_history(self.history, MAX_HISTORY)

    def _save_history(self) -> None:
        with self._history_lock:
            try:
                _save_history(HISTORY_FILE, self.history)
            except Exception as e:
                logging.error(f"Failed to prepare history for save: {e}")

    @on(RadioSet.Changed)
    def _on_model_changed(self, event: RadioSet.Changed) -> None:
        custom_input = self.query_one("#custom-model", Input)
        selected = self._get_selected_model()
        custom_input.disabled = selected != "custom"

    @on(Button.Pressed, "#load-prompt")
    def _on_load_prompt(self, event: Button.Pressed) -> None:
        self._action_load_prompt()

    @on(Button.Pressed, "#load-history")
    def _on_load_history(self, event: Button.Pressed) -> None:
        self._action_load_history()

    @on(Button.Pressed, "#push-mode-toggle")
    def _on_push_mode_toggle(self, event: Button.Pressed) -> None:
        self.action_toggle_push()

    @on(Switch.Changed, "#web-search")
    def _on_web_search_changed(self, event: Switch.Changed) -> None:
        self.web_search = event.value

    @on(RadioSet.Changed, "#reasoning-effort")
    def _on_reasoning_effort_changed(self, event: RadioSet.Changed) -> None:
        if event.pressed:
            if event.pressed.id == "reasoning-low":
                self.reasoning_effort = "low"
            elif event.pressed.id == "reasoning-medium":
                self.reasoning_effort = "medium"
            elif event.pressed.id == "reasoning-high":
                self.reasoning_effort = "high"

    @on(Switch.Changed, "#show-reasoning")
    def _on_show_reasoning_changed(self, event: Switch.Changed) -> None:
        self.show_reasoning = event.value
        self._refresh_chat_display()

    def watch_is_loading(self, loading: bool) -> None:
        status = self.query_one("#status", Static)
        if loading:
            status.add_class("loading")
        else:
            status.remove_class("loading")

    def _get_selected_model(self) -> str | None:
        try:
            radio_set = self.query_one("#model-select", RadioSet)
            pressed = radio_set.pressed_button
            if pressed and pressed.id in MODEL_MAP:
                return MODEL_MAP[pressed.id]
        except Exception:
            pass
        return None

    def update_status(self, text: str, status_type: str = "normal") -> None:
        self.call_from_thread(self._update_status_text, text, status_type)

    def _update_status_text(self, text: str, status_type: str = "normal") -> None:
        status = self.query_one("#status", Static)
        status.update(f" {text} ")
        status.remove_class("error", "success", "normal", "loading")
        status.add_class(status_type)
        if self.is_loading:
            status.add_class("loading")

    def _action_load_prompt(self) -> None:
        def on_selected(path: Path | None) -> None:
            if path:
                try:
                    content = _load_prompt_file(path)
                    self.query_one("#system-prompt", TextArea).text = content
                    self._refresh_context_stats()
                    self._update_status_text(f"Loaded: {path.name}", "success")
                except Exception as e:
                    self._update_status_text(f"Error loading file: {e}", "error")

        picker = FileOpen(
            filters=Filters(
                ("Markdown", lambda p: p.suffix.lower() == ".md"),
                ("Text", lambda p: p.suffix.lower() == ".txt"),
            )
        )
        self.push_screen(picker, callback=on_selected)

    def _action_load_history(self) -> None:
        def on_selected(path: Path | None) -> None:
            if path:
                try:
                    self.session_id = derive_session_id(path)
                    loaded = _load_history(path)
                    self.history = _truncate_history(loaded, MAX_HISTORY)
                    self._save_history()
                    self._refresh_chat_display()
                    self._refresh_context_stats()
                    self._update_status_text(
                        f"Loaded {len(loaded)} messages", "success"
                    )
                except Exception as e:
                    self._update_status_text(f"Error loading history: {e}", "error")

        picker = FileOpen(
            filters=Filters(("JSONL", lambda p: p.suffix.lower() == ".jsonl")),
            must_exist=True,
        )
        self.push_screen(picker, callback=on_selected)

    def action_send(self) -> None:
        if self.is_loading:
            return

        selected = self._get_selected_model()
        if not selected:
            self._update_status_text("Error: Select a valid model", "error")
            return

        if selected == "custom":
            model = self.query_one("#custom-model", Input).value.strip()
            if not model:
                self._update_status_text("Error: Enter custom model name", "error")
                return
        else:
            model = selected

        user_input = self.query_one("#user-input", ChatInput)
        user_text = user_input.text.strip()
        if not user_text:
            self._update_status_text("Error: Enter a message", "error")
            return

        system_prompt = self.query_one("#system-prompt", TextArea).text.strip()

        with self._history_lock:
            self.history.append({"role": "user", "content": user_text})
            self.history = _truncate_history(self.history, MAX_HISTORY)

        self._save_history()
        self._refresh_chat_display()
        user_input.text = ""

        self.is_loading = True
        self._push_cancelled.clear()
        self._update_status_text(f"Requesting {model}...", "normal")

        # Snapshot push_mode at loop entry to prevent toggle-during-execution
        push_mode_local = self.push_mode
        tools = [_get_yap_done_tool()] if push_mode_local else None

        # Create an HTTP session we can close to cancel in-flight requests
        session = requests.Session()
        with self._http_session_lock:
            self._http_session = session

        def make_request():
            start_time = time.time()
            iteration = 0
            try:
                while True:
                    # Check for cancellation before starting next request
                    if self._push_cancelled.is_set():
                        self.update_status(
                            f"[CANCELLED] {iteration} iteration(s) ({time.time() - start_time:.1f}s)",
                            "normal",
                        )
                        break

                    # Build payload for current iteration
                    with self._history_lock:
                        # SPEC_YAP T6: If injector is present, it handles history projection
                        # Otherwise, fallback to YAP_PROVIDER-based preparation
                        if self.last_obs.get("injector_present"):
                            current_history = list(
                                self.history
                            )  # Send raw history with _meta
                        else:
                            current_history = _prepare_history_for_request(
                                self.history, YAP_PROVIDER
                            )
                    payload = _build_payload(
                        model,
                        current_history,
                        system_prompt or None,
                        tools,
                        push_mode_local,
                        extra_body={
                            "session-id": self.session_id
                        },  # Hyphen for injector compatibility
                        reasoning_effort=self.reasoning_effort,
                        include_search=self.web_search,
                    )

                    # Update status for push mode
                    if push_mode_local:
                        self.update_status(
                            f"[PUSH {iteration + 1}/{MAX_PUSH_ITERATIONS}] Requesting {model}...",
                            "normal",
                        )

                    # Make request
                    result = _http_chat(
                        API_URL, payload, TIMEOUT, session, self._push_cancelled
                    )
                    data = result["data"]
                    obs = parse_obs(result["headers"])
                    message = _parse_response(data)
                    # Tag with current provider for stripping logic
                    message.setdefault("_meta", {})["provider"] = YAP_PROVIDER

                    with self._history_lock:
                        self.history.append(message)
                        self.history = _truncate_history(self.history, MAX_HISTORY)

                    # Extract display text for last_response.md
                    _, clean_text = _extract_thoughts(message.get("content", []))
                    _safe_write(LAST_RESPONSE_FILE, clean_text)
                    self._save_history()

                    # Update observability state
                    self.last_obs = obs
                    if obs["session_id"] and obs["session_id"] != self.session_id:
                        self.call_from_thread(self.update_session_id, obs["session_id"])

                    elapsed = time.time() - start_time
                    finish_reason = data.get("choices", [{}])[0].get(
                        "finish_reason", "unknown"
                    )

                    # Check if push mode logic should apply
                    if not push_mode_local:
                        # Single request mode
                        obs_str = format_obs_status(obs)
                        status_msg = (
                            f"Success ({elapsed:.1f}s) | Finish: {finish_reason}"
                        )
                        if obs_str:
                            status_msg = f"Success ({elapsed:.1f}s) | {obs_str} | Finish: {finish_reason}"
                        self.update_status(status_msg, "success")
                        break

                    # Push mode: check for yap__done tool call
                    tool_calls = message.get("tool_calls") or []
                    has_done_call = _detect_yap_done(tool_calls)

                    if has_done_call:
                        self.update_status(
                            f"[PUSH DONE] {iteration + 1} iteration(s) - final round ({elapsed:.1f}s)",
                            "normal",
                        )

                        with self._history_lock:
                            self.history.append(
                                {"role": "user", "content": PUSH_MODE_SUMMARY_REQUEST}
                            )
                            self.history = _truncate_history(self.history, MAX_HISTORY)
                        self._save_history()
                        self.call_from_thread(self._refresh_chat_display)

                        with self._history_lock:
                            current_history = _prepare_history_for_request(
                                self.history, YAP_PROVIDER
                            )
                        final_payload = _build_payload(
                            model,
                            current_history,
                            system_prompt or None,
                            None,
                            None,
                            extra_body={"session-id": self.session_id},
                        )
                        self.update_status(
                            "[PUSH DONE] Final summary round...",
                            "normal",
                        )
                        final_result = _http_chat(
                            API_URL,
                            final_payload,
                            TIMEOUT,
                            session,
                            self._push_cancelled,
                        )
                        final_data = final_result["data"]
                        final_obs = parse_obs(final_result["headers"])
                        final_message = _parse_response(final_data)
                        final_message.setdefault("_meta", {})["provider"] = YAP_PROVIDER

                        with self._history_lock:
                            self.history.append(final_message)
                            self.history = _truncate_history(self.history, MAX_HISTORY)

                        # Update observability state for final round
                        self.last_obs = final_obs

                        _, final_clean_text = _extract_thoughts(
                            final_message.get("content", [])
                        )
                        _safe_write(LAST_RESPONSE_FILE, final_clean_text)
                        self._save_history()

                        final_elapsed = time.time() - start_time
                        self.update_status(
                            f"[PUSH DONE] {iteration + 1} iteration(s) ({final_elapsed:.1f}s)",
                            "success",
                        )
                        break

                    # Check for cancellation or max iterations
                    if self._push_cancelled.is_set():
                        self.update_status(
                            f"[PUSH CANCELLED] {iteration + 1} iteration(s) ({elapsed:.1f}s)",
                            "normal",
                        )
                        break

                    if iteration >= MAX_PUSH_ITERATIONS - 1:
                        self.update_status(
                            f"[PUSH MAX] {MAX_PUSH_ITERATIONS} iterations ({elapsed:.1f}s)",
                            "error",
                        )
                        break

                    # Nudge: add continuation message AFTER successful response
                    nudge = NUDGE_MESSAGE.format(iteration=iteration + 1)
                    if iteration >= 7:
                        nudge += PUSH_MODE_ITERATION_WARNING.format(
                            iteration=iteration + 1,
                            max=MAX_PUSH_ITERATIONS,
                        )
                    with self._history_lock:
                        self.history.append({"role": "user", "content": nudge})
                        self.history = _truncate_history(self.history, MAX_HISTORY)

                    self._save_history()
                    self.call_from_thread(self._refresh_chat_display)
                    iteration += 1

                    # Small delay between iterations
                    time.sleep(1)

            except Exception as e:
                self.update_status(f"Error: {str(e)[:100]}", "error")
            finally:
                with self._http_session_lock:
                    self._http_session = None
                session.close()
                self.call_from_thread(setattr, self, "is_loading", False)
                self.call_from_thread(self._refresh_chat_display)
                self.call_from_thread(self._refresh_context_stats)

        self.run_worker(make_request, thread=True, exclusive=True)

    def action_clear_history(self) -> None:
        with self._history_lock:
            self.history = []
        try:
            HISTORY_FILE.unlink(missing_ok=True)
        except Exception as e:
            logging.error(f"Failed to delete history file: {e}")
        self._refresh_chat_display()
        self._update_status_text("History Cleared")

    def action_clear_input(self) -> None:
        self.query_one("#user-input", ChatInput).text = ""
        self._update_status_text("Input Cleared")

    def action_toggle_push(self) -> None:
        """Toggle push mode on/off and update UI."""
        self.push_mode = not self.push_mode
        button = self.query_one("#push-mode-toggle", Button)
        if self.push_mode:
            button.label = "Push Mode: On"
            button.variant = "success"
            self._update_status_text("Push Mode enabled", "success")
        else:
            button.label = "Push Mode: Off"
            button.variant = "default"
            self._update_status_text("Push Mode disabled", "normal")

    def action_cancel_push(self) -> None:
        """Cancel any in-flight request (push or single)."""
        if self.is_loading:
            self._push_cancelled.set()
            with self._http_session_lock:
                if self._http_session:
                    self._http_session.close()
            self._update_status_text("Cancelled", "normal")

    def action_toggle_debug(self) -> None:
        """Toggle metadata debug view."""
        self.debug_mode = not self.debug_mode

    def watch_debug_mode(self, debug_mode: bool) -> None:
        """Update metadata debug view visibility."""
        debug_panel = self.query_one("#metadata-debug", Static)
        debug_panel.set_class(debug_mode, "visible")
        self._refresh_metadata_display()

    def _refresh_metadata_display(self) -> None:
        """Update the content of the metadata debug panel."""
        if not self.debug_mode:
            return

        debug_panel = self.query_one("#metadata-debug", Static)
        obs = self.last_obs
        if obs["injector_present"]:
            tools_str = (
                ", ".join(obs["tools_called"]) if obs["tools_called"] else "none"
            )
            debug_text = (
                f"Session: {obs['session_id']} | "
                f"Provider: {YAP_PROVIDER} | "
                f"Turns: {obs['turns']} | "
                f"Tools: {tools_str} | "
                f"Time: {obs['ms']}ms"
            )
        else:
            debug_text = f"No injector detected | Provider: {YAP_PROVIDER} | Session: {self.session_id}"
        debug_panel.update(debug_text)

    async def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

    def _refresh_chat_display(self) -> None:
        chat_display = self.query_one("#chat-history", TextArea)
        chat_display.text = _format_chat_display(self.history, self.show_reasoning)
        chat_display.scroll_end(animate=False)
        self._refresh_metadata_display()

    def _refresh_context_stats(self) -> None:
        system_prompt = self.query_one("#system-prompt", TextArea).text
        chars, tokens = _count_context(system_prompt, self.history)
        stats = self.query_one("#context-stats", Static)
        stats.update(f"Context: {chars:,} chars | ~{tokens:,} tokens")


if __name__ == "__main__":
    Yap().run()
