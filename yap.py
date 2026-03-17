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
from pathlib import Path

import requests
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

API_URL = os.environ.get("CHAT_CURL_API_URL", "http://lattice:8089/v1/chat/completions")
TIMEOUT = int(os.environ.get("CHAT_CURL_TIMEOUT", 600))
HISTORY_FILE = Path(os.environ.get("CHAT_CURL_HISTORY_FILE", "chat_history.jsonl"))
LAST_RESPONSE_FILE = Path(
    os.environ.get("CHAT_CURL_LAST_RESPONSE_FILE", "last_response.md")
)
MAX_HISTORY = int(os.environ.get("CHAT_CURL_MAX_HISTORY", 50))

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
    model: str, messages: list, system_prompt: str | None = None
) -> dict:
    """Build request payload."""
    payload_messages = []
    if system_prompt:
        payload_messages.append({"role": "system", "content": system_prompt})
    payload_messages.extend(messages)
    return {"model": model, "messages": payload_messages}


def _parse_response(data: dict) -> str:
    """Extract content from API response."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Missing or empty 'choices' in response")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("Missing 'message' in choice")

    raw_content = message.get("content")
    if raw_content is None:
        raise ValueError("Missing 'content' in message")

    return _strip_ansi(str(raw_content))


def _truncate_history(history: list, max_size: int) -> list:
    """Truncate history to max size."""
    if len(history) > max_size:
        return history[-max_size:]
    return history


def _format_chat_display(history: list) -> str:
    """Format history for display."""
    if not history:
        return "Session started. No messages yet."

    formatted = []
    for msg in history:
        role = "USER" if msg.get("role") == "user" else "ASSISTANT"
        content = _strip_ansi(str(msg.get("content", "")))
        formatted.append(f"[{role}]\n{content}\n" + ("-" * 40))
    return "\n\n".join(formatted)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4


def _count_context(system_prompt: str, history: list) -> tuple[int, int]:
    """Count chars and estimated tokens in context."""
    chars = len(system_prompt)
    for msg in history:
        chars += len(msg.get("content", ""))
    tokens = chars // 4
    return chars, tokens


# === SIDE EFFECTS ===


def _http_chat(url: str, payload: dict, timeout: int) -> dict:
    """Make HTTP POST to LLM endpoint."""
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


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
    ]

    is_loading = reactive(False)

    def __init__(self):
        super().__init__()
        self.history = []
        self._history_lock = threading.Lock()
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
        async def on_selected(path: Path) -> None:
            try:
                content = _load_prompt_file(path)
                self.query_one("#system-prompt", TextArea).text = content
                self._refresh_context_stats()
                self._update_status_text(f"Loaded: {path.name}", "success")
            except Exception as e:
                self._update_status_text(f"Error loading file: {e}", "error")
            self.pop_screen()

        picker = FileOpen(filters=Filters([("Markdown", [".md"]), ("Text", [".txt"])]))
        picker.on_file_selected = on_selected
        self.push_screen(picker)

    def _action_load_history(self) -> None:
        async def on_selected(path: Path) -> None:
            try:
                loaded = _load_history(path)
                self.history = _truncate_history(loaded, MAX_HISTORY)
                self._save_history()
                self._refresh_chat_display()
                self._refresh_context_stats()
                self._update_status_text(f"Loaded {len(loaded)} messages", "success")
            except Exception as e:
                self._update_status_text(f"Error loading history: {e}", "error")
            self.pop_screen()

        picker = FileOpen(filters=Filters([("JSONL", [".jsonl"])]), must_exist=True)
        picker.on_file_selected = on_selected
        self.push_screen(picker)

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
        self._update_status_text(f"Requesting {model}...", "normal")

        payload = _build_payload(model, self.history, system_prompt or None)

        def make_request():
            try:
                data = _http_chat(API_URL, payload, TIMEOUT)
                content = _parse_response(data)

                with self._history_lock:
                    self.history.append({"role": "assistant", "content": content})
                    self.history = _truncate_history(self.history, MAX_HISTORY)

                _safe_write(LAST_RESPONSE_FILE, content)
                self._save_history()
                self.update_status("Response saved to last_response.md", "success")
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

    def action_quit(self) -> None:
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
