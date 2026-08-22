Analyze dependencies between the tasks in the context below.

WADE ran the detached-session readiness check before launching this analysis. If
it reported a missing capability, stop and follow its narrow remediation; do not
disable the sandbox globally or request write access to the main checkout.

Output requirements (strict):
- Output ONLY dependency edges in this format: <number> -> <number> # reason
- Each edge must include a short reason comment.
- Use only issue numbers present in the context.
- The dependency graph must be acyclic.
- Keep edges minimal: do not add transitive edges unless independently required.
- If there are no dependencies, output exactly: # No dependencies found
- Do not output markdown fences, headings, bullets, or any extra prose.

Edge semantics:
- "A -> B" means issue A must be done before issue B.

Context:
{context}
