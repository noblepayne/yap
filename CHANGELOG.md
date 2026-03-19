# Changelog

All notable changes to yap will be documented here.

## [Unreleased]

### Added
- **Push mode disclosure** - System prompt now explains push mode mechanism and current status (ON/OFF)
- **Final round after yap__done** - One clean response round (no tools) after push mode completion for summary
- **Ctrl+Enter to send** - Works even when focused in input field
- **Ctrl+P to toggle push mode** - Keyboard shortcut for enabling/disabling push mode

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
