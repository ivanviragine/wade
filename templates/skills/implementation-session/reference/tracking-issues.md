# Tracking / epic issues

Loaded on demand when your issue is part of a parent "Tracking:" checklist.

## Working on a child issue

`done` auto-detects the parent and adds `Part of #<parent>` alongside
`Closes #<child>` — no manual action needed. Pass `--no-close` to leave the
child issue open.

After your PR is merged, tick your entry in the parent's checklist
(`- [ ] #<child>` → `- [x] #<child>`) with:

```bash
gh issue edit <parent-number> --body "<updated-body>"
```

Once every child is complete, close the parent:

```bash
wade task close <parent-number>
```

## Working on the tracking issue itself

Use `Closes #<tracking>` in the PR body, reference all child issues with their
status, and list them using GitHub's tasklist syntax so GitHub renders progress
automatically.
