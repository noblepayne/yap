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
| `YAP_API_KEY` | *(empty)* | Auth key. Sent as `Authorization: Bearer <key>`; if the value already contains a scheme (e.g. `Api-Key x`), it's sent as-is. Empty = no auth header. |
| `YAP_TIMEOUT` | `3600` | Request timeout in seconds (1 hr for heavy tool use) |
| `YAP_HISTORY_FILE` | `chat_history.jsonl` | Chat history path |
| `YAP_LAST_RESPONSE_FILE` | `last_response.md` | Last response output |
| `YAP_MAX_HISTORY` | `50` | Max messages to keep |
| `YAP_MAX_PUSH_ITERATIONS` | `10` | Max iterations in push mode |
| `YAP_THEME` | `nord` | Textual theme name (any builtin: tokyo-night, gruvbox, dracula, ...) |

---

## Code Style

- **Python**: 3.10+ type hints where it helps, skip where it doesn't
- **Formatting**: Black-compatible (we won't fight about it)
- **Line length**: 100 chars max
- **Textual**: Uses the Textual framework for TUI - read their docs if you touch UI code

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Enter` | Send message (works in input) |
| `Ctrl+S` | Send message |
| `Ctrl+P` | Toggle push mode |
| `Ctrl+L` | Clear history |
| `Ctrl+R` | Reset session (clear history + new session ID, keeps prompt) |
| `Ctrl+U` | Clear input |
| `y` | Copy last response to clipboard (OSC52) |
| `Y` | Copy full transcript to clipboard |
| `Q` | Quit |
| `Escape` | Cancel in-flight request |

---

## Testing

Run: `pytest tests/`

- Use `pytest`
- Put them in `tests/`

### Philosophy (how we decide what to test and how)

**Integration/e2e tests are best. Tests should match how the thing is actually used.**
If a feature is "make an HTTP call with streaming and render it", the test should
do exactly that against a real local server — not poke internals with mocks.
A test that exercises the real path catches protocol bugs, encoding issues,
and wrong assumptions; a mock only verifies we called our own code.

**Unit tests are for dev-ex, combinational correctness, and hard-to-reach cases.**
Pure functions (`_assemble_from_chunks`, `_build_payload`, `parse_obs`) are cheap
to test exhaustively — do that. Reach for unit tests when:
- the logic has many input combinations (combinatorial),
- failure modes are hard to trigger against a real server (malformed chunks,
  cancel mid-stream), or
- you want fast feedback while designing a function's shape.

They are *not* a substitute for integration coverage of the imperative shell.

**Generative/contract tests are super handy** — esp. with imperative-shell style
development. When an external system has a contract (OpenAI chunk format,
SSE framing), capture real examples from live endpoints as fixtures and replay
them in tests. Fixtures come from reality, not from imagination; hand-invented
payloads encode wrong assumptions (this has bitten us).

### Infrastructure

- `tests/conftest.py` — local threaded SSE chat-completions server fixture
  (replays fixture JSON files; supports fault injection)
- `tests/fixtures/*.jsonl` — captured chunk sequences from real endpoints
  (hermes, bifrost). Record new ones with `bin/probe --record`.
- `bin/probe` — probe a live endpoint before using it: list models, time a
  5-token stream, report latency-to-first-delta and whether it truly streams.
  `--verify <fixture>` replays a captured fixture against the live endpoint
  and reports shape drift (exit 0 match / 1 drifted).
  Check assumptions first; don't guess URLs/models/timeouts.

Don't mock HTTP. Spin up the local server instead.
- Test pure functions and config in `tests/test_pure.py`
- Don't mock HTTP - spin up a real local server if you need to test the stack
- Test the app manually for UI changes (Textual makes this easy with `app.run_test()`)

---

## Key Files to Know

- `yap.py:41-46` - Configuration (env vars)
- `yap.py:279-330` - The HTTP request logic (where TIMEOUT matters)
- `yap.py:373+` - Main App class

---

## Adding Features

1. Don't over-architect. It's one file.
2. If it grows past 500 lines, maybe split. Until then, don't.
3. Textual is solid but has its quirks. Test UI changes manually.

---

## Process Patterns

Patterns that earned their keep here. Reach for them when work is bigger
than a one-liner.

### Audit Council

For reviewing a chunk of finished work (tests, refactor, feature):

1. Run TWO independent reviewers with different lenses:
   - **small picture** — function correctness, edge cases, naming, dead code,
     assertion quality ("does this test actually test what its name says?")
   - **big picture** — architecture fit, production concerns, philosophy
     alignment, bar-raising gaps
2. You are the council that integrates: **validate every finding against the
   real code before acting.** Auditors get details wrong — file counts,
   timings, whether a thing exists at all. Accept what survives checking,
   reject what doesn't, and say why. Rejections are fine; proportionality
   beats completeness ("no drama" applies to review too).

### Plan → Review → Execute

For changes with real design surface (new abstractions, signatures, protocols):

1. Write the plan down first, including the proposed API/signature.
2. Get an independent review of the **plan**, not the code. Ask pointedly:
   what breaks, what's missing, is there a simpler shape?
3. Integrate corrections, then execute.

A plan review once rejected a proposed return-signature (it would have
silently killed streaming fidelity) AND surfaced a latent bug before any
code existed. Reviews can reject designs, not just bless them — let them.

### Fix Bug Classes, Not Instances

Same bug bites twice? Stop patching and restructure so the whole class is
impossible. (Bitten twice by `self`-vs-closure confusion inside a nested
HTTP handler → moved all decisions into a plain method where `self` means
exactly one thing, and unit-tested it without sockets.)

### Keep Invariants While Refactoring

A refactor must preserve properties tests depend on, not just the final
pass/fail outcome. A test can pass for the *wrong reason* after a bad
refactor (e.g. cancel-mid-stream passing because everything arrived at
once). Know which property each test pins — streaming fidelity, wire
content, exit codes — and keep it green *and* meaningful.

---

## What This Project Is Not

- A framework
- A template
- Complicated

It's a tool. Keep it that way.

---

## Philosophy Additions

**Simplicity over Cleverness** (Hickey)
- "Easy" ≠ "Simple". Don't sacrifice clarity for brevity.
- Names matter. Unnamed literals are code smell.
- Data is eternal. Functions transform data.

**Explicit over Implicit** (Normand)
- Hidden dependencies bite. Make them visible.
- Separate actions (side effects) from calculations (pure).
- If you have to read code to discover behavior, document it.

**Correctness = Conformance to Spec** (Wayne)
- Before fixing, define what "correct" means.
- Test the edge cases that violate the spec, not just happy paths.
- "Works" is not the same as "correct."

**Decision Framework**
- Don't fix what works. Fix what breaks or will break.
- Premature optimization/generalization is complexity debt.
- When in doubt, prefer the simpler implementation.
- Simplicity is not about having less code - it's about code that does what it says.
