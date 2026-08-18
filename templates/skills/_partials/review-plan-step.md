7. **Review** — after writing plan files, run `wade review plan <plan_file>` for
   each plan file you created and check the exit code:
   - **Exit 0**: Review completed externally or skipped. If there is output, it
     is review feedback — read it and address any actionable findings before
     proceeding to validation.
   - **Exit 2**: Self-review mode. The output is a review prompt — you must act
     as the reviewer: read the instructions, analyze the plan, identify issues,
     and fix them before proceeding to validation.
   - **Exit 1**: If the output contains the timeout marker (see **Review budget
     & skip guidance** above), it is a budget overrun, not a bug — re-run once,
     or use the salvaged partial output. Any other exit 1: a real error — debug
     and retry.

   Fix **minor** findings (typos, small clarifications) and proceed to
   validation — no re-review needed. Fix **major** findings (structural
   issues, unclear requirements) and re-run once; always proceed to validation
   after that run, regardless of new findings. The pass count here is
   advisory, not code-tracked — see **Review budget & skip guidance** above.
