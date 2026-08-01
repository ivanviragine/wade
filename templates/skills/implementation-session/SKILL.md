---
name: implementation-session
description: >
  Rules for AI implementation sessions in a wade-managed worktree. Covers
  worktree safety, commit conventions, syncing with main, PR summaries, and
  session closing. Read this at the start of every work session.
---

# Implementation Session Rules

These rules govern AI implementation sessions in a wade-managed project.
Read and follow them before performing any other action.

## Execution mode

Run `wade` and `gh` commands with the required permissions/capabilities (not
in sandboxed mode). Do not "try sandbox first" — run them unsandboxed from the
start.

## Talking to the user

Inform the user before running `wade`/`gh` commands, reviews, or lifecycle
operations — say what you're doing and why; never run them silently. Announce
each step as you start it, and after each command report the outcome and the
next step you'll take.

{user_interaction_prompt}
- After presenting the workflow recap and state: "Want any further changes, or is the session complete?"
- If review findings need user input: "Should I address this review finding?"

## Never use `gh issue create`

**NEVER** use `gh issue create` or the GitHub API to create issues directly.
Always use `wade task create` for interactive issue creation.
Using `gh` directly bypasses label enforcement, snapshot/diff detection, and
dependency analysis hooks.

{review_enforcement_rule}

## Project Knowledge

Search for knowledge relevant to your task at session start (do not dump all
entries), and capture important learnings before writing `PR-SUMMARY.md` when
knowledge is enabled (`.wade.yml` → `knowledge.enabled`). Rating is required for
each entry you open and evaluate. See @.claude/skills/knowledge/SKILL.md for
search syntax, the rating decision tree, and entry style. Commit the updated
knowledge file with your other changes.

## First action: check your context

Run `wade implementation-session check` as your **first action**:

- `IN_WORKTREE` — you may proceed with work (code changes, commits, etc.)
- `IN_MAIN_CHECKOUT` — **editing any source file is forbidden, even before
  committing**. Tell the human to create a worktree first via `wade implement`.
- `NOT_IN_GIT_REPO` — you are not inside a git repository.

`wade implement` auto-syncs your branch with the base branch at startup. If that
catchup — or the closing sync — reports a conflict or error, see
@.claude/skills/implementation-session/reference/recovery.md.

## Worktree safety

All **code changes** (edits, new files, commits) **must** happen in a worktree.
The human creates worktrees via `wade implement` or `wade implement-batch`.
**Never** create worktrees yourself.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) format. Prefer
`git commit` (opens an editor) over `git commit -m` for multi-line messages.

## Closing the session

**NEVER** create Pull Requests manually (`gh pr create`) or push branches
directly.

To finalize your work, follow these steps in order:

{review_implementation_closing_step}

**Step 2 — Write PR summary:**

Write `PR-SUMMARY.md` in the worktree root with a real description of your
changes (format + the "never commit this file" fix:
@.claude/skills/implementation-session/reference/pr-summary-format.md). If the
file already exists, update it.

**Step 3 — Sync with main:**

```bash
wade implementation-session sync --json
```

Exit 0 means you're up to date — proceed. For any conflict or error, see
@.claude/skills/implementation-session/reference/recovery.md. Never re-implement
git operations yourself.

**Step 4 — Done:**

```bash
wade implementation-session done
```

`done` pushes the branch and updates the existing draft PR (appends a summary,
marks it ready). The worktree is **not** deleted — `implement` cleans it up after
merge. This is a **mandatory** step; if it fails, debug and fix it — do NOT bypass.

**Step 5 — Present results:** give a brief **workflow recap** (only the steps you
performed) and **current state** (PR number/URL, that the issue closes on merge,
the branch), then what's next (wade monitors the PR; later feedback →
`wade review pr-comments <issue>`; status → `wade status <issue>`). Then ask
(native question component): "Want any further changes, or is the session
complete?" — apply and repeat Steps 1–5 if so, else suggest the user exits.

### Tracking / epic issues

If your issue is a **child** in a parent "Tracking:" checklist, `done`
auto-detects the parent and adds `Part of #<parent>` alongside `Closes #<child>`
(pass `--no-close` to keep the issue open). After merge, tick your entry via
`gh issue edit <parent>`, and run `wade task close <parent>` once all children
are done. If you are working on the tracking issue **itself**, use
`Closes #<tracking>` and list child statuses in the PR body with GitHub tasklist
syntax.

## After creating a new plan

If you finalize a plan during a work session, create a GitHub Issue from it:
write the plan file to the worktree root, run `wade task create` (interactive),
then show `wade implement <number>` as a hint — do **not** run it yourself.

## Wade-managed skills

Directories under `.claude/skills/` (and the `.github/skills`, `.agents/skills`,
`.cursor/skills` aliases) are installed per-session and gitignored. Guard hooks
enforce this — do not modify, commit, or delete them.

## Skills reference

- **About to create GitHub Issues** → read @.claude/skills/task/SKILL.md first
