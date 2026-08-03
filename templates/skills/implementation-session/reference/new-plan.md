# Creating an issue from a new plan

Loaded on demand when you finalize a plan or feature spec during an
implementation session.

1. Write the plan file to the **worktree root** — never into the repo's main
   checkout.
2. Create the issue with `wade task create` (interactive). Never use
   `gh issue create`.
3. List the created issues and show `wade implement <number>` as a hint. Do
   **not** run it yourself — the human starts work sessions.
