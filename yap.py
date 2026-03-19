#!/usr/bin/env python3
"""
yap - terminal LLM chat TUI
Keyboard-driven. Gets out of your way.
"""

import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path

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
    TextArea,
)
from textual_fspicker import FileOpen, Filters

API_URL = os.environ.get("YAP_API_URL", "http://lattice:8089/v1/chat/completions")
TIMEOUT = int(os.environ.get("YAP_TIMEOUT", 3600))
HISTORY_FILE = Path(os.environ.get("YAP_HISTORY_FILE", "chat_history.jsonl"))
LAST_RESPONSE_FILE = Path(os.environ.get("YAP_LAST_RESPONSE_FILE", "last_response.md"))
MAX_HISTORY = int(os.environ.get("YAP_MAX_HISTORY", 50))
MAX_PUSH_ITERATIONS = int(os.environ.get("YAP_MAX_PUSH_ITERATIONS", 10))
NUDGE_MESSAGE = "Continue working on the original request. Call yap__done when complete or if stuck."

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


def _build_payload(
    model: str,
    messages: list,
    system_prompt: str | None = None,
    tools: list | None = None,
) -> dict:
    """Build request payload."""
    payload_messages = []
    if system_prompt:
        payload_messages.append({"role": "system", "content": system_prompt})
    payload_messages.extend(messages)
    payload = {"model": model, "messages": payload_messages}
    if tools is not None:
        payload["tools"] = tools
    return payload


def _get_yap_done_tool() -> dict:
    """Return the yap__done tool definition for signaling completion."""
    return {
        "type": "function",
        "function": {
            "name": "yap__done",
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
    return any(tc.get("function", {}).get("name") == "yap__done" for tc in tool_calls)


def _parse_response(data: dict) -> dict:
    """Extract message object from API response."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Missing or empty 'choices' in response")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("Missing 'message' in choice")

    # Clean ANSI from content if present
    if "content" in message and message["content"]:
        message["content"] = _strip_ansi(str(message["content"]))

    # Clean reasoning_content if present
    if "reasoning_content" in message and message["reasoning_content"]:
        message["reasoning_content"] = _strip_ansi(str(message["reasoning_content"]))

    if "thought" in message and message["thought"]:
        message["thought"] = _strip_ansi(str(message["thought"]))

    return message


def _truncate_history(history: list, max_size: int) -> list:
    """Truncate history to max size."""
    if len(history) > max_size:
        return history[-max_size:]
    return history


def _format_chat_display(history: list) -> str:
    """Format history for display with tool call awareness."""
    if not history:
        return "Session started. No messages yet."

    formatted = []
    for msg in history:
        role = msg.get("role", "UNKNOWN").upper()
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")
        reasoning = msg.get("reasoning_content") or msg.get("thought")

        # Strip ANSI from content
        display_content = _strip_ansi(str(content))

        # Expert Transparency: Handle reasoning/thoughts (DeepSeek/O1 style)
        if reasoning:
            reasoning_text = _strip_ansi(str(reasoning))
            display_content = f"THOUGHTS:\n{reasoning_text}\n\n{display_content}"

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


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def _count_context(system_prompt: str, history: list) -> tuple[int, int]:
    """Count chars and estimated tokens in context."""
    chars = len(system_prompt)
    for msg in history:
        # Crash fix: content can be None (e.g. assistant msg with tool calls)
        content = msg.get("content") or ""
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


def _http_chat(url: str, payload: dict, timeout: int) -> dict:
    """Make HTTP POST to LLM endpoint with retries."""

    @retry(
        retry=retry_if_exception_type(
            (
                requests.exceptions.RequestException,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError,
            )
        ),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logging.getLogger("yap"), logging.WARNING),
        reraise=True,
    )
    def _do_post():
        r = requests.post(url, json=payload, timeout=timeout)
        # Retry on 5xx server errors
        if 500 <= r.status_code < 600:
            r.raise_for_status()
        r.raise_for_status()
        return r.json()

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
        ("q", "quit", "Quit"),
        ("escape", "cancel_push", "Cancel Push"),
    ]

    is_loading = reactive(False)
    push_mode = reactive(False)

    def __init__(self):
        super().__init__()
        self.history = []
        self._history_lock = threading.Lock()
        self._push_cancelled = threading.Event()
        self._load_history()

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
                with Horizontal(id="button-row"):
                    yield Button("Load Prompt", id="load-prompt", variant="primary")
                    yield Button("Load History", id="load-history", variant="primary")
                yield Static("Context: 0 chars | ~0 tokens", id="context-stats")
            with Vertical(id="main"):
                with Vertical(id="chat-container"):
                    yield Static("CONVERSATION", classes="header-text")
                    yield TextArea(read_only=True, id="chat-history")
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

        def make_request():
            start_time = time.time()
            iteration = 0
            try:
                while True:
                    # Build payload for current iteration
                    with self._history_lock:
                        current_history = self.history.copy()
                    payload = _build_payload(
                        model, current_history, system_prompt or None, tools
                    )

                    # Update status for push mode
                    if push_mode_local:
                        self.update_status(
                            f"[PUSH {iteration + 1}/{MAX_PUSH_ITERATIONS}] Requesting {model}...",
                            "normal",
                        )

                    # Make request
                    data = _http_chat(API_URL, payload, TIMEOUT)
                    message = _parse_response(data)

                    with self._history_lock:
                        self.history.append(message)
                        self.history = _truncate_history(self.history, MAX_HISTORY)

                    # Save raw response or just content to last_response.md
                    last_content = message.get("content") or ""
                    if not last_content and message.get("tool_calls"):
                        last_content = json.dumps(message.get("tool_calls"), indent=2)

                    _safe_write(LAST_RESPONSE_FILE, last_content)
                    self._save_history()

                    elapsed = time.time() - start_time
                    finish_reason = data.get("choices", [{}])[0].get(
                        "finish_reason", "unknown"
                    )

                    # Check if push mode logic should apply
                    if not push_mode_local:
                        # Single request mode
                        self.update_status(
                            f"Success ({elapsed:.1f}s) | Finish: {finish_reason}",
                            "success",
                        )
                        break

                    # Push mode: check for yap__done tool call
                    tool_calls = message.get("tool_calls") or []
                    has_done_call = _detect_yap_done(tool_calls)

                    if has_done_call:
                        # Done!
                        self.update_status(
                            f"[PUSH DONE] {iteration + 1} iteration(s) ({elapsed:.1f}s)",
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
                    with self._history_lock:
                        self.history.append({"role": "user", "content": NUDGE_MESSAGE})
                        self.history = _truncate_history(self.history, MAX_HISTORY)

                    self._save_history()
                    self.call_from_thread(self._refresh_chat_display)
                    iteration += 1

                    # Small delay between iterations
                    time.sleep(1)

            except Exception as e:
                self.update_status(f"Error: {str(e)[:100]}", "error")
            finally:
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

    def action_cancel_push(self) -> None:
        """Cancel the push loop if active."""
        if self.is_loading and self.push_mode:
            self._push_cancelled.set()
            self._update_status_text("Push cancelled", "normal")

    async def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

    def _refresh_chat_display(self) -> None:
        chat_display = self.query_one("#chat-history", TextArea)
        chat_display.text = _format_chat_display(self.history)
        chat_display.scroll_end(animate=False)

    def _refresh_context_stats(self) -> None:
        system_prompt = self.query_one("#system-prompt", TextArea).text
        chars, tokens = _count_context(system_prompt, self.history)
        stats = self.query_one("#context-stats", Static)
        stats.update(f"Context: {chars:,} chars | ~{tokens:,} tokens")


if __name__ == "__main__":
    Yap().run()
