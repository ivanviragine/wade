"""Git operations and utilities."""

from __future__ import annotations

from ghaiw.git.branch import (
    branch_exists,
    commits_ahead,
    create_branch,
    delete_branch,
    make_branch_name,
)
from ghaiw.git.pr import (
    GhCliError,
    create_pr,
    get_pr_for_branch,
    merge_pr,
)
from ghaiw.git.repo import (
    GitError,
    detect_main_branch,
    get_current_branch,
    get_remote_url,
    get_repo_root,
    is_clean,
    is_git_repo,
    is_worktree,
)
from ghaiw.git.sync import (
    abort_merge,
    fetch_origin,
    get_conflicted_files,
    merge_branch,
)
from ghaiw.git.worktree import (
    create_worktree,
    list_worktrees,
    prune_worktrees,
    remove_worktree,
)

__all__ = [
    # repo
    "GitError",
    "detect_main_branch",
    "get_current_branch",
    "get_remote_url",
    "get_repo_root",
    "is_clean",
    "is_git_repo",
    "is_worktree",
    # branch
    "branch_exists",
    "commits_ahead",
    "create_branch",
    "delete_branch",
    "make_branch_name",
    # worktree
    "create_worktree",
    "list_worktrees",
    "prune_worktrees",
    "remove_worktree",
    # sync
    "abort_merge",
    "fetch_origin",
    "get_conflicted_files",
    "merge_branch",
    # pr
    "GhCliError",
    "create_pr",
    "get_pr_for_branch",
    "merge_pr",
]
