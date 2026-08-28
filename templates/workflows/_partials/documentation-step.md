Re-read the complete branch diff against its base and update {doc_targets}, plus
anything else the change makes inaccurate. A new flag, renamed option, or
changed default requires documentation. State the outcome in one line: list the
files updated, or say why no documentation change was needed. After the final
documentation commit, record the decision with
`wade {documentation_command} docs --updated` or
`wade {documentation_command} docs --not-needed "<reason>"`. The receipt is
commit-specific: if any later workflow step creates a commit, reconsider the
complete diff and record the decision again for the new HEAD.
