# yap

![yap in action](yap.png)

A lightweight TUI for testing LLM endpoints and proxies.

## What this is

A simple keyboard-driven interface for sending messages to LLMs and seeing responses. Built for testing proxies (like [mcp-injector](https://github.com/noblepayne/mcp-injector)) and model servers without the overhead of a full AI coding agent.

Not competing with [opencode](https://github.com/anomalyco/opencode) - this is simpler, easier to modify, and focused purely on the request/response cycle. Has optional push mode for multi-step tasks. Just you, the model, and the response.

## Use cases

- Testing LLM endpoints
- Debugging proxy configurations
- Quick experiments with different models/prompts
- Loading system prompts from files

## Setup

### Nix (Recommended)

```bash
nix run github:noblepayne/yap
```

Or for development:

```bash
nix develop
./yap.py
```

### Manual (uv)

```bash
uv sync
./yap.py
```

### Manual (pip)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

### Nix

```bash
nix run .
```

### Manual

```bash
./yap.py
```

Or just `python3 yap.py`.

## Configure

Environment variables. That's it.

| Variable | Default | What it does |
|----------|---------|--------------|
| `YAP_API_URL` | `http://lattice:8089/v1/chat/completions` | Endpoint |
| `YAP_TIMEOUT` | `3600` | Request timeout (seconds) |
| `YAP_HISTORY_FILE` | `chat_history.jsonl` | Where messages go |
| `YAP_LAST_RESPONSE_FILE` | `last_response.md` | Last response output |
| `YAP_MAX_HISTORY` | `50` | Messages to keep |
| `YAP_MAX_PUSH_ITERATIONS` | `10` | Max iterations in push mode |

### Updating Dependencies

```bash
# In nix develop shell:
./bin/update
```

This updates `uv.lock`, `requirements.txt`, and the Nix FOD hash.

## Keys

- `Ctrl+S` - Send message
- `Ctrl+L` - Clear history
- `Ctrl+U` - Clear input
- `Escape` - Cancel push loop (when active)
- `Q` - Quit
- `Tab` - Switch between config and chat

## Features

- **Push Mode** - Iterative LLM calls until explicit completion via `yap__done` tool
- **Robust Retries** - Automatic exponential backoff for transient network errors (via `tenacity`)
- **Tool Call Awareness** - Structured display of `tool_calls` and `tool` role messages
- **Long-Running Workflows** - 1-hour default timeout for complex MCP tool loops
- **Load Prompt** - Load a system prompt from a .md or .txt file
- **Load History** - Import a previous conversation from a .jsonl file
- **Context Stats** - See character count and estimated tokens

---

Built because curling curl is annoying and GUI chat apps are worse.
