# Documentation update pass — detail

Loaded on demand from the closing documentation pass. The pass exists because
doc drift is introduced by the same change that should have fixed it, and
nothing else in the workflow catches it.

## What to re-read

Run `git diff <base-branch>...HEAD` and read it as a reviewer would. For each
change, ask what it makes untrue elsewhere in the repo.

## What counts as needing an update

Update anything that describes behaviour you changed:

- A new or renamed command, flag, or option
- A changed default, exit code, or error message
- New or changed configuration keys
- Architecture, layering, or conventions
- Setup/install steps or supported versions

"The change is small" is not a reason to skip. A renamed flag with stale docs is
worse than an undocumented one, because the reader trusts the wrong answer.

The detected file list is a floor, not a ceiling — update any other file the
change makes wrong, including in-repo templates and generated-input sources.

## Stating the outcome

State it in **one terse line**, not a paragraph or a step recap. The step is not
complete until you say, in your own message, either:

- the documentation files you updated (e.g. `Docs: README, AGENTS.md`), or
- "no doc changes needed" **plus** the reason — not user-facing, no change to
  architecture, commands, or conventions.

Proceeding silently is not a valid result: it is indistinguishable from having
forgotten the step.
