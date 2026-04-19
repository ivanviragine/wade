"""Git operations and utilities."""

from __future__ import annotations

from wade.git.branch import (
    branch_exists,
    commits_ahead,
    create_branch,
    delete_branch,
    make_branch_name,
)
from wade.git.pr import (
    GhCliError,
    create_pr,
    get_pr_for_branch,
    merge_pr,
)
from wade.git.repo import (
    GitError,
    detect_main_branch,
    get_current_branch,
    get_dirty_file_paths,
    get_remote_url,
    get_repo_root,
    is_clean,
    is_file_tracked,
    is_git_repo,
    is_worktree,
    list_tracked_files,
    skip_worktree_file,
    unskip_worktree_file,
)
from wade.git.sync import (
    abort_merge,
    fetch_origin,
    get_conflicted_files,
    merge_branch,
)
from wade.git.worktree import (
    create_worktree,
    list_worktrees,
    prune_worktrees,
    remove_worktree,
)

__all__ = [
    "GhCliError",
    "GitError",
    "abort_merge",
    "branch_exists",
    "commits_ahead",
    "create_branch",
    "create_pr",
    "create_worktree",
    "delete_branch",
    "detect_main_branch",
    "fetch_origin",
    "get_conflicted_files",
    "get_current_branch",
    "get_dirty_file_paths",
    "get_pr_for_branch",
    "get_remote_url",
    "get_repo_root",
    "is_clean",
    "is_file_tracked",
    "is_git_repo",
    "is_worktree",
    "list_tracked_files",
    "list_worktrees",
    "make_branch_name",
    "merge_branch",
    "merge_pr",
    "prune_worktrees",
    "remove_worktree",
    "skip_worktree_file",
    "unskip_worktree_file",
]
