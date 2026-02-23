---
name: workflow
description: >
  Mandatory session-start rules for AI agents working in a ghaiw-managed
  project. Covers worktree safety, commit conventions, planning lifecycle,
  and sync workflow. Read this BEFORE doing anything else every session.
---

# Git Workflow — Session Rules

These rules govern every AI agent session in this project. Read and follow
them before performing any other action.

## When to activate

**Every session, before any other action.** Unlike other skills that activate
on-demand for specific tasks, this skill is always-on. Re-read it at the start
of every session.

## Rule 0: Execution mode gate

Before running any `ghaiwpy` command, ensure it runs with the required
permissions/capabilities (not in sandboxed mode). In this repo, apply the
same rule to direct `gh` commands used by the workflow.

Do not "try sandbox first" for `ghaiwpy`/`gh`; run them unsandboxed from the
start.

If a `ghaiwpy` command fails due to auth, permission, lock-file, or similar
environment restrictions, retry the same command with the required permissions
before diagnosing workflow behavior.

## Rule 1: Check your context first

Run `ghaiwpy check` as your **first action**:

- `IN_WORKTREE` → you may proceed with any work (code changes, commits, etc.)
- `IN_MAIN_CHECKOUT` → planning and issue operations are allowed; **editing any
  source file is forbidden, even before committing**. Tell the human to create
  a worktree first via `ghaiwpy work start`.
- `NOT_IN_GIT_REPO` → you are not inside a git repository.

## Rule 2: Worktree safety

All **code changes** (edits, new files, commits) **must** happen in a worktree.
The human creates worktrees via `ghaiwpy work start` (single issue) or
`ghaiwpy work batch` (multiple issues in parallel). **Never** create worktrees
yourself.

**Planning does NOT need a worktree** — creating issues, reading issues, writing
plans, and running `ghaiwpy task create` are all safe from the main checkout.
However, if you write a plan file during planning, write it to `/tmp` (e.g.,
`/tmp/plan.md`) — **never** write it into the repo directory from a main
checkout. Files written into the repo outside a worktree will pollute the
working tree and may block future worktree operations.

## Rule 3: Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) format.
Prefer `git commit` (which opens an editor) over `git commit -m` for
multi-line messages.

## Rule 4: After creating a new plan — create the issue

Whenever you finalize a plan, feature spec, or design document — whether
written to a file or discussed in conversation — you **must** proceed to
create a GitHub Issue from it. Follow the task skill (@.claude/skills/task/SKILL.md)
through **all** steps:

1. Write the plan file
2. Create the issue via `ghaiwpy task create --plan-file`
3. List the created issues and show `ghaiwpy work start <number>` as a hint
   (this is mandatory — see the task skill Step 7). Do **not** run the command
   yourself or offer it as a selectable option; the human starts work sessions.

Do **not** trigger this when implementing or editing an existing plan — only
when you authored a new one.

> **Exception — inside a `ghaiwpy task plan` session:** If you were launched by
> `ghaiwpy task plan`, do **not** apply Rule 4. Your job is to write plan
> file(s) to the temp dir shown in your clipboard/prompt and exit. ghaiw
> creates the GitHub Issues automatically after you exit. Rule 4 only applies
> when planning outside of a `ghaiwpy task plan` session.

## Rule 5: Closing the session — use `ghaiwpy work done`

**NEVER** create Pull Requests manually (e.g., via `gh pr create`) or push branches
directly unless explicitly instructed to bypass the workflow.

To finalize your work:

1. **Write a PR summary** — Create `/tmp/PR-SUMMARY-{issue-number}.md` (e.g. `/tmp/PR-SUMMARY-42.md`)
   documenting what was done (follow @.claude/skills/pr-summary/SKILL.md). **This is required** —
   `ghaiwpy work done` reads this file to populate the PR body. Without it the PR description will be empty.
   Write outside the repo (to `/tmp`).
2. Run `ghaiwpy work sync` to sync with main.
3. Run `ghaiwpy work done`. This command handles:
   - Pushing the branch
   - Creating the PR (or merging, based on config)
   - **Triggering auto-versioning** (if enabled)
4. After `ghaiwpy work done` completes, you can exit your session normally. The
   worktree is **not** deleted by `work done` (PR strategy) — it is cleaned up
   automatically by `work start` after the human merges the PR.

This is a **mandatory** final step. Follow the sync skill (@.claude/skills/sync/SKILL.md)
for step 2.

> **Note:** `PLAN.md` and `PR-SUMMARY-*.md` are gitignored artifacts — **never
> commit them**. They exist only to guide your session and generate the PR body.

### Working on a child issue (sub-issue of a tracking/epic)

If your issue appears in a parent "Tracking:" issue checklist:

- `ghaiwpy work done` **automatically detects** the parent tracking issue and
  adds `Part of #<parent>` to the PR body alongside `Closes #<child>` — no
  manual action needed. The linked issue is closed by default on merge; pass
  `--no-close` to leave it open.
- After your PR is merged, update the parent tracking issue's checklist:
  change `- [ ] #<your-issue>` to `- [x] #<your-issue>` using
  `gh issue edit <parent-number> --body "<updated-body>"`.
- If all children are complete, close the parent: `ghaiwpy task close <parent-number>`.

## Rule 6: Never create GitHub Issues directly

**NEVER** use `gh issue create` or the GitHub API to create issues directly.

Always use the ghaiw task workflow instead:

- **From a plan file**: `ghaiwpy task create --plan-file <path>`
- **Interactively**: `ghaiwpy task create`
- **Full planning workflow**: read @.claude/skills/task/SKILL.md before creating any issues

Using `gh issue create` bypasses label enforcement, snapshot/diff detection,
dependency analysis hooks, and the structured plan-file format. Even if you
think it's simpler, it will break the workflow.

## Rule 7: Do not run `ghaiwpy` or `gh` commands in sandboxed mode

If your environment supports sandboxed command execution, run `ghaiwpy`/`gh`
commands with the required permissions/capabilities instead of sandbox mode.

Do not attempt sandbox execution first. If a `ghaiwpy`/`gh` command fails with
permission/lock-file/auth errors, retry the same command with the required
permissions before diagnosing workflow behavior.

## Skills reference

Stop and read the relevant skill **before** taking any of these actions:

- **About to create GitHub Issues** → read @.claude/skills/task/SKILL.md first
- **About to analyze issue dependencies** → read @.claude/skills/deps/SKILL.md first
- **About to merge/finalize work** → read @.claude/skills/sync/SKILL.md first
- **About to write a PR description** → read @.claude/skills/pr-summary/SKILL.md first
