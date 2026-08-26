# yap Tier B UI Spec — per-message rendering + collapsible config

Status: DRAFT for design review (Plan → Review → Execute pattern)
Scope ref: AGENTS.md Process Patterns; Tier A landed in b183593

## 1. Goals

- G1: Assistant messages render as real **Markdown** (code blocks with syntax
  highlighting matter for an LLM debugging tool).
- G2: Reasoning renders as a **Collapsible** per message (default collapsed),
  not dim-italic inline text.
- G3: Transcript becomes **incremental**: appending a completed message is
  O(new message), not a full transcript rebuild.
- G4: Config sidebar collapses via keybind (`ctrl+g`), giving the transcript
  full width when desired.
- G5: Remove hand-rolled section labels ("CONFIG"/"CONVERSATION"/"INPUT")
  in favor of `border_title` on the containers they label.

## 2. Non-goals

- N1: No in-flow live streaming yet — the existing external `#stream-pane`
  Markdown widget stays exactly as-is (Tier A behavior preserved). Moving
  streaming into the transcript flow is a follow-up after this lands.
- N2: No settings persistence, no theme picker UI (YAP_THEME env stands).
- N3: No mouse-first interactions; keyboard-driven remains.
- N4: Copy behavior unchanged: `y`/`Y` read from history via pure formatters,
  which are independent of the display widgets. Terminal-level selection
  degrades further (widgets aren't selectable text) — accepted decision from
  the OSC52 analysis; revisit only if it hurts in practice.

## 3. Current state (what changes)

Today `_refresh_chat_display()` does:
    log.clear(); _write_transcript(log)   # writes Text segments into ONE RichLog

Every refresh trigger (send complete, push iteration, load history, clear,
reset session) rebuilds the entire transcript string/widget content.
Reasoning = dim italic Text; tool calls = ⚙ line; user/tool = plain Text.
Config sidebar: always-visible `#config` (width 30). Labels: three
`.header-text` Statics.

## 4. Proposed architecture

### Widget tree (inside #chat-container)

    Vertical#chat-container
      ├─ VerticalScroll#transcript        (NEW — replaces RichLog)
      │    ├─ (per message, appended:)
      │    │    Static.user-line          [USER] text            (plain)
      │    │    Collapsible.reasoning     "▸ reasoning"          (collapsed)
      │    │      └─ Static(Text(thoughts, dim italic))
      │    │    Markdown.assistant        rendered assistant text
      │    │    Static.tool-call          ⚙ name(args)  [CALL:id] (Text obj)
      │    │    Static.rule               ──── separator (dim)
      │    └─ Static#transcript-empty     "Session started..." placeholder
      ├─ Markdown#stream-pane             (unchanged from Tier A)
      └─ Static#metadata-debug            (unchanged)

Rules:
- One message → zero-or-more child widgets appended in order.
- Model-derived text goes into `Static(Text(...))` renderables or Markdown's
  `source` — never markup strings (Tier A rule preserved: brackets safe).
- Tool call lines keep `_format_tool_call` + `[CALL:id]` suffix (proxy log
  matching requirement).

### Rendering & invalidation (G3)

`Yap` gains `_rendered_count: int` (messages already in DOM).

    def _refresh_chat_display():
        if len(history) >= _rendered_count and _tail_matches():
            _append_messages(history[_rendered_count:])     # incremental path
        else:
            _rebuild_transcript()                            # clear + all
        _rendered_count = len(history)

`_tail_matches()` guards against truncation weirdness: cheap check that
history[:_rendered_count] identity holds (compare stored id list or just
len + first-id). Full rebuild also on: load_history, clear, reset session,
show_reasoning toggle NO LONGER rebuilds (see below).

Perf target: push-mode iteration append ≈ O(iteration message); 50-message
rebuild only on explicit load/clear events.

### Reasoning toggle without rebuild (G2)

All reasoning Collapsibles get class `reasoning-block`. The existing
show-reasoning Switch handler stops calling `_refresh_chat_display()` and
instead toggles `hidden` class on `.reasoning-block` children (pure CSS,
O(n) class flip, no widget construction).

### Config sidebar (G4)

- Binding: `("ctrl+g", "toggle_config", "Toggle Config")`.
- Implementation: `#config.add_class("collapsed")`; CSS:
  `#config.collapsed { display: none; }`. Layout: #main is width 1fr and
  expands automatically inside the Horizontal parent.
- State is ephemeral (not persisted) — debug tool, default open.

### Chrome labels (G5)

Delete the three `Static(...classes="header-text")` rows. Set
`border_title="conversation"` on #chat-container, `border_title="input"` on
#input-container, and for config use `border_title="config"` on #config.
Keeps orientation, kills two full label rows (~4 lines of vertical space).
`.header-text` CSS removed.

## 5. Test impact matrix

| Test | Impact |
|---|---|
| test_tui::test_transcript_renders_into_richlog | REWRITE: assert #transcript has children; assistant msg produced a Markdown child |
| test_tui::test_stream_pane_hidden_by_default | unchanged (pane untouched) |
| test_tui::test_stream_update_shows_and_resets_pane | unchanged |
| test_tui::test_copy_last_response / copy_transcript | unchanged (history-based pure functions) |
| test_tui::test_full_send_stream_render | RichLog strip assertion → query Markdown child source contains "E2E reply" |
| test_keyboard_shortcuts | unaffected (presence-only assertions); optionally add ctrl+g presence test |
| test_loading | patches `_refresh_chat_display` — method survives |
| NEW test_tui tests | incremental append: send twice → no full rebuild (assert _rendered_count tracking + child count == expected); reasoning toggle hides blocks without rebuild |

## 6. Risks

- R1: Markdown widget construction cost per assistant message — bounded by
  MAX_HISTORY=50; rebuilds rare under invalidation rules.
- R2: Scroll-to-bottom must be explicit now (RichLog auto-scrolled):
  `#transcript.scroll_end(animate=False)` after appends and stream updates.
- R3: Incremental-append desync if any code mutates self.history in place
  without refresh (audit: grep shows mutation sites all followed by
  _refresh_chat_display or covered by lock + final refresh).
- R4: Collapsible inside VerticalScroll focus/tab order noise — set
  `can_focus=False` on reasoning collapsibles? (open question for reviewer)

## 7. Open questions for review

- Q1: Is the `_tail_matches` guard worth it vs always-rebuild-on-anomaly?
  (Simplicity ethos vs perf goal G3.)
- Q2: Reasoning Collapsible default: collapsed (spec says yes) — does that
  fight the project's "Expert Transparency" comments in yap.py?
- Q3: Should tool-call lines ALSO be Markdown for long pretty JSON args, or
  stay single-line Static? (Spec: stay Static, args are compact canonical.)
- Q4: Anything in this spec over-engineered for a one-file debug tool?


## 8. Review amendments (GO-WITH-CHANGES, ses_fc4867cc)

All adopted before implementation:

1. VERSION: textual is 8.2.8 (not 8.1.1). Markdown is a plain Widget with
   height:auto default — CSS pins '#transcript Markdown { height: auto; }'
   explicitly so future ScrollView reverts cannot silently break layout.
2. INVALIDATION: naive length/tail check UNSOUND at MAX_HISTORY boundary
   (equal counts, shifted head, empty slice => silently stale). Replaced by
   two-field identity rule: _rendered_count + _rendered_tail (strong ref to
   last rendered dict; id() banned — address reuse after GC). Mismatch =>
   full rebuild. Both fields updated after either branch.
3. _refresh_chat_display retains stream-pane reset (update("") +
   remove_class streaming) in BOTH branches — existing test depends on it.
4. Reasoning Collapsibles mount WITH the suppressed class applied when
   show_reasoning is False at build time (else next append leaks reasoning).
   Class named suppressed, not hidden (avoid framework-idiom collisions).
5. R4 RESOLVED OPPOSITE TO SPEC LEAN: Collapsible titles stay FOCUSABLE —
   they carry enter-to-toggle; disabling focus violates keyboard-driven N3.
   Tab stops accepted.
6. Placeholder lifecycle specified: hide on first append; restore whenever
   rebuild runs against empty history.
7. Tests added: MAX_HISTORY truncation-boundary rebuild (THE G3 correctness
   test), placeholder-on-clear, bounded-poll Markdown-child assertion in
   e2e, ctrl+g binding presence. RichLog imports removed.
8. Scrolling wrapped in call_after_refresh(scroll_end) to avoid racing
   layout. Stream pane loses scroll_end (no longer a ScrollView in 8.x).
9. Q2 resolution: collapsed-by-default does NOT fight Expert Transparency;
   title carries char count (runaway-CoT visibility). Toggle-OFF now hides
   blocks entirely while clipboard formatter still emits suppression
   marker — screen vs copy divergence documented, acceptable.
10. G3 payoff restated honestly: avoiding per-push-iteration remount of
    thousands of Markdown child widgets, not generic rebuild speed.
