7. **Review** — after writing plan files, run `wade review plan <plan_file>` for
   each plan file you created and check the exit code:
   - **Exit 0**: Review completed externally or skipped. If there is output, it
     is review feedback — read it and address any actionable findings before
     proceeding to validation.
   - **Exit 2**: Self-review mode. The output is a review prompt — you must act
     as the reviewer: read the instructions, analyze the plan, identify issues,
     and fix them before proceeding to validation.
   - **Exit 1**: Error — debug and retry.

   **Run at most 2 times total**:
   - If findings are **minor** (typos, small clarifications): Fix and proceed to
     validation; no re-review needed.
   - If findings are **major** (structural issues, unclear requirements): Fix and
     re-run once. Always proceed to validation after the 2nd run, regardless of
     new findings.
   - Never re-run more than twice.
