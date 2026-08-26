# SPEC: Config panel cleanup + dynamic model picker

Status: DRAFT for design review | Pattern: Plan → Review → Execute
User decisions locked via interview (dynamic picker / cut switches / keep file buttons / all dead models removed).

## 1. Problem

Model RadioSet hardcodes six stale entries (OpenRouter Free, DeepSeek v3.2,
Hunter Alpha, Healer Alpha, `brian`, Custom) while the real endpoints are
hermes (`hermes-agent`, 1 model) and bifrost (591 models). Both speak
`GET /v1/models`. Web Search + Reasoning Effort controls target capabilities
the current proxies don't exercise.

## 2. Changes

### C1 — Dynamic model picker (replaces RadioSet + MODEL_MAP)

- Derive base URL: `API_URL.rsplit("/chat/completions", 1)[0]`; fetch
  `GET {base}/models` with `build_auth_headers(API_KEY)`, timeout 10s,
  daemon thread (mount never blocks); results marshalled via
  `call_from_thread(self._set_models, ids)` (pattern already used for status).
- New state: `self.available_models: list[str]`.
- Widgets: `Input#model-input` + `OptionList#model-suggestions`
  (display:none until there are matches).
- Filtering: pure `_filter_models(models, query) -> list[str]` —
  case-insensitive substring, preserves endpoint order, capped at 8.
- Interaction: typing filters live; selecting an option fills the input and
  hides suggestions; empty query hides suggestions; fetch failure degrades
  silently to free-text (endpoint may not serve /models).
- Prefill precedence: `YAP_MODEL` env > sole fetched model (hermes case:
  auto-fills `hermes-agent`) > empty.
- `_get_selected_model()` + `MODEL_MAP` deleted; `action_send` reads
  `Input#model-input.value.strip()` (empty → existing error status path).

### C2 — Cut web search + reasoning effort

- Remove Switch/RadioSet/handlers/reactives (`web_search`, `reasoning_effort`).
- Remove `include_search` / `reasoning_effort` params from `_build_payload`
  and their call sites; delete the 3 corresponding unit tests in
  test_pure.py (recoverable in history; feature was for a proxy contract
  no longer exercised).

### C3 — Keep

Load Prompt / Load History buttons, System Prompt textarea, Show Reasoning
switch, context stats — untouched.

### C4 — Test infrastructure

- `ChatServer` gains `models=None` attr + `do_GET` serving
  `{"data": [{"id": m} for m in models]}` at `/v1/models` (404 otherwise),
  so TUI tests exercise the real fetch path over HTTP (no mocks).
- New tests: `_filter_models` units (empty query → [], case-insensitivity,
  cap-8, order preservation); fetch-populates-suggestions; sole-model
  prefill; YAP_MODEL precedence; option-select fills input; e2e asserts
  wire payload `model` == input value.
- Update any tests referencing removed reactive names (grep shows none
  outside the deleted payload tests).

## 3. Risks

- R1: fetch failure UX — silent degrade accepted (debug tool; probe exists
  for diagnosing endpoints).
- R2: OptionList focus/tab-stop noise inside sidebar — leave focusable
  (arrow keys useful); revisit if it annoys.
- R3: worker→UI race vs `run_test` pump — reuse call_from_thread pattern;
  tests poll bounded like e2e does today.
- R4: `YAP_MODEL` empty-string env should mean unset (strip + falsy check).

## 4. Out of scope

No persistence of last-used model (session-only + env); no multi-endpoint
profiles; no change to push-mode/tool plumbing.
