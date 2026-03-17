#!/usr/bin/env python3
"""
Duck.ai “chat‑cURL” helper – editable TUI with easy model selection.

Features
* Free‑form model entry **or** quick pick from a curated list.
* Long timeout (3 min) for tool‑driven actions.
* Shows the raw assistant output – works for plain replies or tool‑call payloads.
* Simple to edit the system/user messages without touching a curl command.

Dependencies
-------------
pip install prompt-toolkit requests
"""

import json
import sys

import requests
from prompt_toolkit import Application
from prompt_toolkit.layout import HSplit, Layout
from prompt_toolkit.widgets import (
    Button,
    Dialog,
    Label,
    TextArea,
    RadioList,
)
from prompt_toolkit.key_binding import KeyBindings

# -------------------------------------------------
# Configuration – change only if you need different defaults
# -------------------------------------------------
API_URL = "http://lattice:8089/v1/chat/completions"
DEFAULT_SYSTEM = ""  # optional system message
DEFAULT_USER = ""
# DEFAULT_USER = (
#    "you have mcp__chrome tools. review any schemas necessary. "
#    "use socratic questioning and scientific reasoning. "
#    "build a plan of action, review any tool schemas, then act. "
#    "be fully transparent in any errors or issue. debug mode. "
#    "full compliance only. INSTRUCTIONS: use chrome tools to open a new tab, "
#    "then in that tab, navigate the jupiterbroadcasting.com and then, using clicks, "
#    "not navigation, access the most recent episode and summarise it in a simple markdown format. "
#    "output only that markdown summary."
# )

# Curated model choices (including the new ones you requested)
MODEL_CHOICES = [
    ("openrouter/openrouter/free", "OpenRouter – free"),
    ("openrouter/openrouter/hunter-alpha", "OpenRouter – hunter‑alpha"),
    ("openrouter/openrouter/healer-alpha", "OpenRouter – healer‑alpha"),
    ("openrouter/deepseek/deepseek-v3.2", "OpenRouter – DeepSeek v3.2"),
    ("brian", "Brian (custom)"),
    ("custom", "Custom (type below)"),
]

REQUEST_TIMEOUT = 180  # seconds

# -------------------------------------------------
# UI components
# -------------------------------------------------
model_radio = RadioList(values=MODEL_CHOICES, default="openrouter/openrouter/free")
model_free = TextArea(text="", height=1, prompt="Custom model: ")


def get_selected_model():
    """Return the chosen model string – either a preset or the free‑form entry."""
    choice = model_radio.current_value
    if choice == "custom":
        return model_free.text.strip()
    return choice


system_input = TextArea(text=DEFAULT_SYSTEM, height=3, prompt="System (optional): ")
user_input = TextArea(text=DEFAULT_USER, height=8, prompt="User message: ")
output_area = TextArea(style="class:output", read_only=True, scrollbar=True)


def send_request():
    """Collect fields, POST to the API, and display the raw response."""
    model = get_selected_model()
    if not model:
        output_area.text = "❌ No model selected – please choose or type one."
        return

    payload = {
        "model": model,
        "messages": [],
    }

    if system_input.text.strip():
        payload["messages"].append(
            {"role": "system", "content": system_input.text.strip()}
        )
    payload["messages"].append({"role": "user", "content": user_input.text.strip()})

    try:
        resp = requests.post(
            API_URL,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        assistant_msg = data["choices"][0]["message"]["content"]
        output_area.text = assistant_msg
    except requests.exceptions.Timeout:
        output_area.text = f"❌ Request timed out after {REQUEST_TIMEOUT}s."
    except Exception as e:
        output_area.text = f"❌ Error: {e}"


send_button = Button(text="Send", handler=send_request)
quit_button = Button(text="Quit", handler=lambda: app.exit())

model_section = HSplit(
    [
        Label(text="Select model (or choose “Custom” and type below):"),
        model_radio,
        model_free,
    ]
)

dialog = Dialog(
    title="🦆 Duck.ai chat‑cURL helper",
    body=HSplit(
        [
            model_section,
            system_input,
            user_input,
            Label(
                text="--- Response (raw assistant output) ---", style="class:separator"
            ),
            output_area,
        ]
    ),
    buttons=[send_button, quit_button],
    width=80,
)

# -------------------------------------------------
# Application setup
# -------------------------------------------------
kb = KeyBindings()


@kb.add("c-c")
def _(event):
    """Ctrl‑C quits."""
    event.app.exit()


app = Application(layout=Layout(dialog), key_bindings=kb, full_screen=True)

if __name__ == "__main__":
    try:
        app.run()
    except KeyboardInterrupt:
        sys.exit(0)
