# Features shipped since last PyPI release (v0.0.2)

> Current version: **v0.15.15** | Last PyPI release: **v0.0.2**
> Period: 2026-03-04 to 2026-04-08

---

## Multi-Provider Task Management

- **ClickUp integration** — pluggable task provider backend alongside GitHub Issues ([v0.3.0] #134)
- **Conventional commit prefix enforcement** — plan issue titles must use conventional commit format ([v0.6.0])
- **Plan status badges** — show `[PLANNED]` / `[NO PLAN]` badges in issue listing ([v0.5.0] #142)
- **Tracking issue detection** — automatically detect tracking issues in `wade implement` and `wade <N>` ([v0.5.4] #183)

## AI Delegation & Multi-Tool Support

- **Unified AI delegation infrastructure** — generic delegation framework for plan review, code review, and dependency analysis ([v0.2.0] #122, #123, #124, #125)
- **AI tool/model confirmation prompt** — interactive confirmation before launching AI sessions ([v0.0.6] #61)
- **Deferred AI selection** — postpone AI tool choice until after plan check ([v0.2.0] #127)
- **Change AI tool mid-workflow** — allow changing AI tool and model in auto-deps confirmation ([v0.2.0] #132)
- **Timeout support** — configurable timeout field for AI commands and review delegation ([v0.11.0])
- **Effort configuration overhaul** — `--effort` flag + `WADE_EFFORT` env var for Claude ([v0.5.8] #190)
- **Model thinking/effort mode support** — control model reasoning depth ([v0.1.2] #81)
- **Cursor CLI support** — full integration with Cursor's AI CLI ([v0.1.1] #72)
- **Codex headless execution** — non-interactive execution via `exec` subcommand ([v0.5.1] #154)
- **Codex and Gemini adapter updates** — new CLI capabilities and model discovery ([v0.5.1] #170, #172)
- **Gemini CLI integration fixes** — hooks format, deprecated allowed-tools, positional args ([v0.15.10] #249)
- **Native confirmation/question components** — AI agents use built-in UI components ([v0.12.0] #225)

## Session Workflow

- **Setup-worktree hook** — prompt for and wire a `setup-worktree.sh` script during `wade init` ([v0.1.0], [v0.0.5] #52, #53)
- **Worktree bootstrap self-awareness** — `bootstrap_worktree` detects wade development context ([v0.0.5] #57)
- **Auto-copy wade files to worktrees** — internal wade files copied regardless of user config ([v0.11.0])
- **Catchup step at implementation startup** — sync worktree with base branch before implementing ([v0.15.0] #232)
- **Workflow recap and state summary** — all session skills show recap of current workflow state ([v0.13.0] #223)
- **Resume session option** — "Resume session" added to "Continue working" menu ([v0.1.4] #112)
- **Offer to start working after planning** — seamless plan-to-implement transition ([v0.1.4] #107)
- **Smart-start menu context-awareness** — menu adapts based on PR draft state and worktree ([v0.1.3] #102)
- **Open PR in browser** — added to smart-start menu ([v0.6.1] #194)
- **Terminal tab titles** — set terminal tab title during plan and work sessions ([v0.1.1] #70)
- **Ghostty terminal launcher** — support for Ghostty terminal + batch terminal launcher ([v0.5.4] #179)
- **Selective skill installation** — per-command skill installation during init ([v0.1.4] #114)
- **CLI completion install prompt** — prompt to install shell completions during init ([v0.1.4] #116)
- **CLI commands reorganization** — commands organized around workflow phases and audience ([v0.1.4] #110)
- **Non-interactive task creation** — `wade new-task` works without interactive prompts ([v0.1.1] #71)

## Plan Sessions

- **`wade plan-done` command** — deterministic plan validation ([v0.1.1] #73)
- **Plan file-write guard hooks** — prevent accidental writes during plan sessions ([v0.5.8] #177, [v0.15.6] #241)
- **Plan session write guard resilience** — guard fails open on hook execution errors ([v0.15.12] #253)

## Code Review & PR Workflow

- **`wade address-reviews` command** — dedicated command to address PR review comments ([v0.1.1] #79)
- **Review reminder in done commands** — prompt to review before closing sessions ([v0.5.0] #145)
- **PR comment polling** — poll for PR comments in smart_start review flow ([v0.8.0] #202)
- **Commit-aware review polling** — review polling tracks which comments are new per commit ([v0.7.0] #196)
- **Active review comment polling** — "Wait for reviews" actively polls for comments ([v0.6.1] #192)
- **Outdated thread detection** — PR comment polling catches outdated threads and PR-level reviews ([v0.15.8] #245)
- **Bot review detection** — detect completed bot reviews (CodeRabbit) in PR comment polling ([v0.15.7] #243)
- **CodeRabbit review bot status** — detect CodeRabbit status and clarify review prompt wording ([v0.4.0])
- **Comprehensive PR review status reporting** — detailed review status display ([v0.5.1] #162)
- **Stronger review enforcement** — implementation-session `done` enforces review completion ([v0.15.7] #239)
- **Post-batch coherence review** — review session after batch operations ([v0.5.0] #144)
- **Branch diff fallback** — fall back to branch diff when no working-tree changes found ([v0.5.1] #175)
- **Self-review improvements** — actionable output for AI agents, renamed from "Self-review" to "Review" ([v0.5.1] #160, #164)

## Batch Mode & Chaining

- **Batch mode with stacked branches** — chain-aware review for batch operations ([v0.11.3] #218)
- **Chain auto-continuation** — automatic continuation with merge-gated dependencies ([v0.5.6] #181)
- **YOLO mode** — skip AI tool permission prompts during work sessions ([v0.4.0] #135)
- **YOLO flag propagation** — propagate yolo_explicit flag through review session handoff ([v0.11.3] #220)

## Project Knowledge System

- **Project knowledge file** — cross-session AI learning via persistent knowledge entries ([v0.9.0] #207)
- **`knowledge get` command** — read project knowledge file ([v0.10.0] #211)
- **Knowledge thumbs-up/down** — rate knowledge entries for relevance ([v0.11.0] #212)
- **Descriptive knowledge IDs** — relaxed regex to allow descriptive entry IDs ([v0.15.9] #247)

## Worktree Safety & Artifacts

- **Worktree file-write guard** — hook prevents writes outside allowed paths during implementation ([v0.14.0] #230)
- **Session-specific file detection** — detect committed session-specific files on `done` and `sync` ([v0.15.15] #257)
- **Wade artifacts moved to worktree-only** — AGENTS.md pointer and AI tool settings no longer committed to main ([v0.15.1] #234)
- **Managed artifacts cleanup** — eliminate committed .gitignore block, all wade artifacts worktree-only ([v0.15.11] #251)
- **Gitignore audit** — comprehensive audit and fix of gitignore patterns ([v0.15.5] #237)
- **Prevent skill commits** — prevent wade-managed skills from being committed in inited projects ([v0.11.1] #214)
- **AI tool session preservation** — preserve AI tool session data on worktree deletion ([v0.1.1] #75)
- **AI tool permission pre-authorization** — unified and complete permission setup ([v0.1.1] #76)
- **Wade allowlist propagation** — always propagate wade CLI allowlist to worktrees ([v0.5.3])

## Infrastructure & Reliability

- **Auto version bump CI workflow** — automatic version bump on PR merge ([v0.1.2])
- **PR title linter** — CI enforces conventional commit format on PR titles ([v0.5.9])
- **Retry logic for `gh` CLI** — retry on transient network failures ([v0.13.1])
- **Model registry updates** — updated tier mappings for new OpenAI/Google models ([v0.5.3] #168)

---

*Generated from CHANGELOG.md — includes all features, significant enhancements, and workflow improvements shipped between v0.0.3 and v0.15.15.*
