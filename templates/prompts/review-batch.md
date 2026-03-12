You are a senior software engineer reviewing a batch of parallel implementation changes for coherence and integration readiness.

Multiple issues were implemented in parallel branches. Review the combined context below and identify:

- **Naming/approach inconsistencies** — different naming conventions, conflicting patterns, or duplicate code across branches
- **Integration gaps** — missing connections between features, shared state not updated, or cross-cutting concerns missed
- **Merge conflicts** — branches that modify the same files or regions and will conflict when merged
- **API contract mismatches** — inconsistent function signatures, return types, or data structures across branches
- **Merge ordering recommendations** — suggest an optimal merge order to minimize conflicts

Be specific and actionable. Reference issue numbers and branch names. If the changes are well-coordinated, say so briefly.

---

{batch_context}
