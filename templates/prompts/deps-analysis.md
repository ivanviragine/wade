Analyze dependencies between the GitHub issues in the context below.

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
