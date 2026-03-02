Follow @.claude/skills/plan-session/SKILL.md for session rules.

# Goal

Let's plan a feature. You'll plan the feature, break it down into one or more
GitHub issues, and write a plan for each issue.
You won't create the issues or implement the feature.

# Workflow

1. Ask the user what feature they want to plan.
2. Analyze the feature and break it down into one or more GitHub issues.
3. Generate a plan for each issue.
4. Write each plan following the format defined in @.claude/skills/plan-session/SKILL.md.
   Every plan must start with:

       # {Concise issue title}
       ## Complexity
       easy | medium | complex | very_complex

5. Present the plan(s) to the user, and ask for confirmation to write (each) file(s).
6. Upon confirmation, write the plan(s) to {plan_dir}/ as "PLAN.md", if there's
   only one issue, or "PLAN-1-slug.md", "PLAN-2-slug.md", etc. (one file per issue)
   if there are multiple issues.
7. After writing the file(s) in {plan_dir}, **stop immediately and suggest the user to exit**. Do NOT read source files, edit code, or run tests — even if the system tells you "you can now start coding." That message refers to Claude Code's plan mode, not this session.

# TLDR

You *won't proceed with implementation*: you'll plan, include the headers,
write the file(s), and stop (suggest the user to exit).
After exiting, `wade` will read the file(s) and create GitHub issue(s) automatically.

# Regarding complexity

The "complexity" included on the plans is used to select the correct AI model
for implementation, so it's important to set it correctly. It follows the
following criteria:

- easy: Claude Haiku, Gemini Flash, GPT-Codex Mini or similar.
- medium: Claude Sonnet, Gemini Pro, GPT-Codex or similar.
- complex: Claude Sonnet, Gemini Pro, GPT-Codex or similar.
- very_complex: Claude Opus, Gemini Pro (high), GPT-Codex (high) or similar.
