## What was done
Unified and completed AI tool permission pre-authorization across all adapters, replacing the fragmented hardcoded approach with a centralized configuration-driven system.

## Changes
- Added `PermissionsConfig` model to `src/wade/models/config.py` with `allowed_commands: list[str]` field (defaults to `["wade *"]`), and added `permissions` field to `ProjectConfig`
- Added `allowed_commands_args(commands)` adapter method to `AbstractAITool` base class, with `allowed_commands` parameter wired through `build_launch_command()` and `launch()`
- Implemented per-adapter translations:
  - **Claude**: `--allowedTools Bash(wade *) Bash(./scripts/check.sh *)`
  - **Copilot**: `--allow-tool shell(wade:*) --allow-tool shell(./scripts/check.sh:*)`
  - **Gemini**: `--allowedTools shell(wade:*) --allowedTools shell(./scripts/check.sh:*)`
  - **Cursor**: return `[]` (config-file permissions via `~/.cursor/cli-config.json`)
  - **Codex/OpenCode**: return `[]` (sandbox mode / no support)
- Extracted `canonical_to_claude()` and `canonical_to_cursor()` helpers for reusable pattern translation
- Expanded `configure_allowlist()` in both `claude_allowlist.py` and `cursor_allowlist.py` to accept `extra_patterns` parameter, translating and merging them idempotently
- Wired `config.permissions.allowed_commands` at all 5 launch sites: work_service (inline + detach), plan_service, deps_service (headless + interactive)
- Updated `bootstrap_worktree()` to propagate the full expanded allowlist from config to worktree `.claude/settings.json`
- Added `_detect_scripts()` and `_build_permissions_commands()` helpers to auto-detect `scripts/*.sh` files during `wade init`
- Added `permissions` section validation in `check_service.py` with key/type checking
- Wired Cursor allowlist configuration in `init_service.py`: `_prompt_cursor_settings()` and `update()` now pass extra patterns
- Updated VSCode and Antigravity adapter `launch()` signatures for compatibility

## Testing
- Added 55 new unit tests across 4 new test files:
  - `test_allowed_commands.py`: adapter method tests for Claude, Copilot, Cursor, Gemini, Codex, OpenCode + build_launch_command integration
  - `test_pattern_translation.py`: `canonical_to_claude()` + `canonical_to_cursor()` helpers + expanded `configure_allowlist()` with extra patterns for both Claude and Cursor
  - `test_bootstrap_allowlist.py`: worktree bootstrap propagation with expanded allowlist
  - `test_init_permissions.py`: `_detect_scripts()` and `_build_permissions_commands()` helpers
- Updated existing tests: `test_config_models.py` (PermissionsConfig + defaults), `test_check.py` (permissions validation)
- Full suite: 1018 tests pass, mypy --strict clean, ruff clean

## Notes for reviewers
- The `permissions` section in `.wade.yml` is fully optional — missing section uses `["wade *"]` default, maintaining backward compatibility
- The `canonical_to_claude()` helper uses `Bash(pattern)` format (wrapping the full pattern), matching the existing `WADE_ALLOW_PATTERN = "Bash(wade *)"` convention
- The `canonical_to_cursor()` helper uses `Shell(pattern)` format, matching Cursor's existing `Shell(wade *)` convention
- Copilot and Gemini use `shell(cmd:args)` format per their CLI documentation
- Cursor uses config-file permissions (`~/.cursor/cli-config.json`), not CLI flags — so `allowed_commands_args()` returns `[]` while allowlist is managed through `cursor_allowlist.py`
