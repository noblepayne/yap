# AGENTS.md - yap

## Philosophy

Simple. Works. No drama.

A lightweight TUI for testing LLM endpoints and proxies. Built for debugging proxies like [mcp-injector](https://github.com/noblepayne/mcp-injector). Not competing with opencode - this is simpler, easier to modify, focused purely on request/response. Has optional push mode for multi-step tasks.

---

## Project Structure

```
yap.py            # The whole app. Edit it, run it.
pyproject.toml    # uv project config (source of truth)
uv.lock           # uv lockfile
requirements.txt  # pip-compatible requirements (exported from uv)
flake.nix         # Nix build & dev shell
bin/update        # Update deps script
tests/            # Tests (test_pure.py, test_loading.py)
.venv/            # uv virtualenv (gitignored)
*.jsonl *.md      # Generated: chat history, last response
```

---

## Dependency Management

**Dev**: Uses `uv` with `pyproject.toml` as source of truth.

**Build**: Uses FOD pattern with `pip` - pre-downloads wheels, then builds offline.

### Updating Dependencies

```bash
# In nix develop shell:
./bin/update

# Or directly:
nix develop -c ./bin/update
```

This script:
1. Runs `uv lock --upgrade`
2. Exports `requirements.txt` (removes stray `.` reference)
3. Builds FOD to get new hash
4. Updates `flake.nix`

---

## Running

### Nix (Recommended)

```bash
nix run .
```

### Development

```bash
# Enter dev shell
nix develop

# Or manually with uv:
uv sync
./yap.py
```

---

## Configuration

All via environment variables. No config files.

| Variable | Default | Purpose |
|----------|---------|---------|
| `YAP_API_URL` | `http://lattice:8089/v1/chat/completions` | LLM endpoint |
| `YAP_TIMEOUT` | `3600` | Request timeout in seconds (1 hr for heavy tool use) |
| `YAP_HISTORY_FILE` | `chat_history.jsonl` | Chat history path |
| `YAP_LAST_RESPONSE_FILE` | `last_response.md` | Last response output |
| `YAP_MAX_HISTORY` | `50` | Max messages to keep |
| `YAP_MAX_PUSH_ITERATIONS` | `10` | Max iterations in push mode |

---

## Code Style

- **Python**: 3.10+ type hints where it helps, skip where it doesn't
- **Formatting**: Black-compatible (we won't fight about it)
- **Line length**: 100 chars max
- **Textual**: Uses the Textual framework for TUI - read their docs if you touch UI code

---

## Testing

Run: `pytest tests/`

- Use `pytest`
- Put them in `tests/`
- Test pure functions and config in `tests/test_pure.py`
- Don't mock HTTP - spin up a real local server if you need to test the stack
- Test the app manually for UI changes (Textual makes this easy with `app.run_test()`)

---

## Key Files to Know

- `yap.py:30-36` - Configuration (env vars)
- `yap.py:250-320` - The HTTP request logic (where TIMEOUT matters)
- `yap.py:86+` - Main App class

---

## Adding Features

1. Don't over-architect. It's one file.
2. If it grows past 500 lines, maybe split. Until then, don't.
3. Textual is solid but has its quirks. Test UI changes manually.

---

## What This Project Is Not

- A framework
- A template
- Complicated

It's a tool. Keep it that way.
