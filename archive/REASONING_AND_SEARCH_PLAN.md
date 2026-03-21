# Implementation Plan: Reasoning & Web Search Support

## Goal
Enhance the `yap` TUI to support OpenAI reasoning models (`o1`, `o3-mini`) and web search capabilities, with high-quality visual feedback for thinking blocks.

## Feature Spec

### 1. UI Controls (Config Sidebar)
- **Web Search**: A `Switch` widget to enable/disable `include_search`.
- **Reasoning Effort**: A `RadioSet` with `low`, `medium`, `high` options. Default to `low`.
- **Show Reasoning**: A `Checkbox` to toggle visibility of thinking blocks in the conversation.

### 2. Request Logic (`yap.py`)
- **State Management**: Add reactive variables for `web_search_enabled`, `reasoning_effort`, and `show_thinking`.
- **Payload Construction**: Update `_build_payload` to include these new fields in the JSON request sent to `mcp-injector`.
- **History Preparation**: Ensure `_prepare_history_for_request` remains compatible with the new roles if `mcp-injector` passes them back.

### 3. Visual Display
- **Thinking Blocks**: Update `_format_chat_display` to:
    - Extract thinking content (already partially implemented).
    - Wrap thoughts in a distinct style (e.g., `[dim][italic]`).
    - Respect the `show_thinking` toggle to hide reasoning for a cleaner UI.

## TDD Plan

### 1. Pure Logic Tests (`tests/test_pure.py`)
- **Payload**: Test that `_build_payload` correctly maps the UI state to the outbound JSON.
- **Extraction**: Verify `_extract_thoughts` handles various reasoning field names (`reasoning_content`, `thought`).

### 2. UI Tests
- Use `app.run_test()` to verify:
    - Widgets are present and accessible via keyboard shortcuts if applicable.
    - Toggling "Show Reasoning" immediately updates the chat display without a fresh request.

## Dependencies
This relies on `mcp-injector` handling the role/token mapping (`system` -> `developer`, `max_tokens` -> `max_completion_tokens`).
