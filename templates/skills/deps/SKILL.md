---
name: deps
description: >
  Analyze a set of GitHub issues and identify dependency relationships between
  them. Reads issue titles and bodies, determines execution order, and outputs
  a structured dependency file that wade uses to generate Mermaid diagrams
  and update issue bodies. Use when multiple related issues need ordering.
---

# Dependency Analysis

Analyze a set of tasks (GitHub Issues, or any provider whose task IDs are
numeric) and determine the dependency relationships between them. Output a
structured file that `wade` will use to generate dependency graphs and update
task bodies.

> **Scope:** dependency analysis is **numeric-ID only** — the output contract
> below (`X -> Y`) and wade's parser accept only digits. Providers with opaque
> task IDs (e.g. ClickUp, IDs like `abc123`) can't use this skill; link their
> tasks with an epic instead (see the [task skill](../task/SKILL.md)).

## When to activate

- After `wade plan` creates multiple issues
- When `wade task deps` is run on existing issues
- When the user asks to analyze dependencies between issues

As your **first action** in the detached analysis worktree, run
`wade deps-session check`. It verifies the local worktree and (when knowledge
is enabled) `.wade/` vote staging in the **actual AI runtime**. Dependency
analysis itself does not need GitHub or writable out-of-worktree Git metadata:
the trusted parent `wade task deps` process reads and updates task-provider data
after this session exits. If the check reports `KNOWLEDGE_STAGING_BLOCKED`, stop
and use the printed narrow remediation; never disable the sandbox globally or
request main-checkout write access.

> **Note:** `wade task deps` first attempts headless analysis (AI tools that
> support `--print`/`--prompt`). If headless fails, it falls back to interactive
> mode: passes the analysis prompt directly to the AI tool as an initial message,
> then reads the output from a file after exit.

## Input

You will receive a context file containing one or more issues in this format:

```
=== Issue #41: Add user preferences schema ===
<issue body text>

=== Issue #42: Add preferences API endpoints ===
<issue body text>

=== Issue #43: Add preferences UI panel ===
<issue body text>
```

## Step 1: Analyze relationships

For each pair of issues, determine if there is a dependency. A dependency
exists when:

- One issue creates something (schema, module, API) that another issue uses
- One issue sets up infrastructure that another issue builds upon
- The implementation order matters for correctness (not just convenience)

**Do NOT create dependencies for:**
- Soft preferences ("it would be nice to do A first")
- Shared concerns without actual data/code flow
- Testing dependencies (tests can usually be written independently)

## Step 2: Write the deps file

Write the dependency file to the path specified in your instructions.
Use this exact format:

```
# Dependencies (A -> B means "A must be done before B")
41 -> 42   # schema must exist before API can use it
41 -> 43   # schema must exist before UI can display it
42 -> 43   # API endpoints needed for UI to call
```

Rules:
- One edge per line: `<number> -> <number>`
- Comments after `#` explaining the reasoning (required for each edge)
- Only use issue numbers from the input context
- The graph must be a DAG (no cycles)
- If no dependencies exist, write only: `# No dependencies found`

> **Note:** In the Mermaid diagram that wade generates from this file, arrows are
> rendered in the conventional DAG direction: **dependent → dependency** (e.g., `42 --> 41`
> means issue 42 depends on 41). This is the reverse of the `A -> B` notation above, which
> is only the internal file format used to communicate with wade.

## Step 3: Confirm completion

After writing the file, end with the shared session-summary block — a compact
status, not a prose recap. Keep the blank `>` separators so the lines don't
collapse into one paragraph:

> ✅ Dependency file written to `<path>`
>
> ✅ Nothing needs your attention.
>
> **Next:** exit this session so wade applies the dependencies to the issues.

## Rules

- **Only create edges for genuine technical dependencies** — not stylistic preferences.
- **Every edge needs a comment** explaining why the dependency exists.
- **No cycles** — if A depends on B, B cannot depend on A (directly or transitively).
- **Minimize edges** — if A→B and B→C, don't add A→C unless there's an independent reason.
- **Use only issue numbers from the input** — never reference issues not in the context file.
