# Changelog

All notable changes to yap will be documented here.

## [Unreleased]

### Added
- **Symmetric History Synchronization (v2)** - High-fidelity history ingestion via a signed footer protocol.
- **HMAC-SHA256 Verification** - Secure footer validation to prevent history spoofing.
- **Native Reasoning Support** - Supports Anthropic-style thinking blocks with sequential rendering and visual styling.
- **Outbound History Stripping** - Automatically omits thinking blocks when switching to incompatible providers.
- **Stable Session Identity** - Session IDs derived from history file hashes to enable kernel-side re-hydration.
- **Expert Debug View (Ctrl+M)** - Real-time stats on footer size, spliced turns, and session metadata.
- **Push mode disclosure** - System prompt explains push mode mechanism and current status (ON/OFF).
- **Final round after yap__done** - One clean response round (no tools) for final summary.
- **Ctrl+Enter to send** - Works even when focused in input field.
- **Ctrl+P to toggle push mode** - Keyboard shortcut for enabling/disabling push mode.

### Fixed
- **Type handling in yap__done summary extraction** - Now handles missing/null arguments gracefully
- **Final round disclosure leak** - Clean round no longer includes push mode disclosure text

### Changed
- **Refactored push mode toggle** - Button handler now uses shared `action_toggle_push()` method
- **Extracted yap__done tool name** - Now a constant (`YAP_DONE_TOOL_NAME`) for clarity

### Documentation
- Added philosophy section to AGENTS.md (Hickey, Normand, Wayne)
- Added keyboard shortcuts section to AGENTS.md
- Updated README with new keyboard shortcuts
