7. **Review** — after writing plan files, run `wade review plan <plan_file>` for
   each plan file you created and check the exit code:
   - **Exit 0**: Review completed externally or skipped. If there is output, it
     is review feedback — read it and address any actionable findings before
     proceeding to validation.
   - **Exit 2**: Self-review mode. The output is a review prompt — you must act
     as the reviewer: read the instructions, analyze the plan, identify issues,
     and fix them before proceeding to validation.
   - **Exit 1**: Error — debug and retry.
