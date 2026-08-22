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

Keep the sandbox enabled with only the capabilities this session needs. The
readiness check identifies missing worktree Git or configured GitHub access.
Never grant write access to the main checkout just to make it pass.

## Talking to the user

{user_interaction_prompt}
- After `done` succeeds (dialog): `Exit now — wade takes over (recommended)` / `Keep editing — I have more changes`.
- If a review finding needs your call (dialog): `Fix it now (recommended)` / `Skip — open a follow-up issue`.

## Never use `gh issue create`

**NEVER** use `gh issue create` or the GitHub API to create issues directly.
Always use `wade task create` for interactive issue creation.
Using `gh` directly bypasses conventional-title enforcement, the configured
issue label, and the task-provider abstraction.

{review_enforcement_rule}

{review_budget_notes}

{knowledge_step}

## First action: check your context

Run `wade implementation-session check` as your **first action**:

- `IN_WORKTREE` — you may proceed with work (code changes, commits, etc.)
- `IN_MAIN_CHECKOUT` — **editing any source file is forbidden, even before
  committing**. Tell the human to create a worktree first via `wade implement`.
- `NOT_IN_GIT_REPO` — you are not inside a git repository.
- `WORKTREE_GIT_BLOCKED` — in a worktree, but its git metadata (the `blocked=…`
  path) is **not writable**, so git writes and `wade sync`/`done` will fail. Do
  not edit files; relaunch with only that metadata path writable.
- `GITHUB_AUTH_BLOCKED` / `GITHUB_API_BLOCKED` — GitHub credentials or the
  read-only API route required by this configured provider are unavailable. Do
  not edit files; relaunch with only the required `gh` access.

Read its machine-readable `reason=…` and exact remediation. If it cannot be
granted narrowly, stop before edits and ask for a trusted relaunch.

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
The **issue title** must be conventional too — the PR title derives from it, so
`done` blocks a non-conventional title (fix it as `done` instructs, then re-run).

## Closing the session

**NEVER** create Pull Requests manually (`gh pr create`) or push branches
directly.

To finalize your work, follow these steps in order:

{review_implementation_closing_step}

**Step 2 — Documentation pass [MANDATORY]:**

{doc_update_step}

@.claude/skills/implementation-session/reference/doc-update.md

**Step 3 — Write PR summary:**

Write `PR-SUMMARY.md` in the worktree root with a real description of your
changes (format + the "never commit this file" fix:
@.claude/skills/implementation-session/reference/pr-summary-format.md). If the
file already exists, update it.

**Step 4 — Sync with main:**

```bash
wade implementation-session sync --json
```

Exit 0 means you're up to date — proceed. For any conflict or error, see
@.claude/skills/implementation-session/reference/recovery.md. Never re-implement
git operations yourself.

**Step 5 — Done:**

```bash
wade implementation-session done
```

`done` is the **authoritative completion gate**: it requires PR-SUMMARY and a
review for this commit; it auto-syncs a branch behind `main`, refusing only on
conflict (bypass: `--skip-review`, or a `done.*` toggle in `.wade.yml`). A pre-push
hook blocks pushes with no `.wade/done@<sha>` marker (`--no-verify` bypasses it).
The worktree is **not** deleted (cleaned up after merge). **Mandatory**; if it
fails, fix the cause, do NOT bypass.

**Step 6 — Present results:** end with the emoji step-status summary (steps:
`Review · Docs · PR-SUMMARY · Sync · Done`) and its handles — PR number/URL,
`closes #N on merge` (or `stays open` with `--no-close`), branch. `done` has
succeeded, so present the exit decision as a **native dialog**: `Exit now — wade
takes over (recommended)` (later feedback → `wade review pr-comments <issue>`;
status → `wade status <issue>`) and `Keep editing — I have more changes` → apply
and repeat Steps 1–6. See @.claude/skills/task/reference/session-summary-format.md.

## Wade-managed skills

Directories under `.claude/skills/` (and the `.github/skills`, `.agents/skills`,
`.cursor/skills` aliases) are installed per-session and gitignored. Guard hooks
enforce this — do not modify, commit, or delete them.

## Skills reference

- **About to create GitHub Issues** → read @.claude/skills/task/SKILL.md first
- **Child of a "Tracking:" issue, or the epic itself** →
  @.claude/skills/implementation-session/reference/tracking-issues.md
- **Finalized a new plan mid-session** →
  @.claude/skills/implementation-session/reference/new-plan.md
