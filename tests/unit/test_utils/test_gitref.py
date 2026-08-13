"""Tests for the lean git-ref validator (``wade.utils.gitref``)."""

from __future__ import annotations

import pytest

from wade.utils.gitref import is_valid_git_ref


class TestIsValidGitRef:
    @pytest.mark.parametrize(
        "ref",
        [
            "main",
            "develop",
            "release/1.2",
            "feature/add-auth",
            "hotfix/v2.0.1",
            "user.name/branch",
        ],
    )
    def test_valid_refs(self, ref: str) -> None:
        assert is_valid_git_ref(ref) is True

    @pytest.mark.parametrize(
        "ref",
        [
            "",
            "use develop",  # whitespace
            "feat\tx",  # tab
            "feat~1",  # tilde
            "feat^",  # caret
            "feat:x",  # colon
            "feat?",  # question mark
            "feat*",  # glob
            "feat[x]",  # bracket
            "feat\\x",  # backslash
            "feat..x",  # double dot
            "feat//x",  # double slash
            "feat@{x",  # reflog syntax
            "/leading-slash",
            "trailing-slash/",
            "-leading-dash",
            ".leading-dot",
            "trailing-dot.",
            "ends-in.lock",
            "comp.lock/tail",  # a path component ending in .lock
            "release/.candidate",  # a non-leading component beginning with a dot
            ".hidden/tail",  # leading component beginning with a dot
            "HEAD",  # git reserves HEAD — check-ref-format --branch rejects it
        ],
    )
    def test_invalid_refs(self, ref: str) -> None:
        assert is_valid_git_ref(ref) is False

    @pytest.mark.parametrize(
        "ref",
        [
            "head",  # case-sensitive — only the exact all-caps HEAD is reserved
            "Head",
            "feat/HEAD",  # HEAD is reserved only as the whole ref, not a component
            "release/HEAD",
        ],
    )
    def test_head_reserved_only_as_whole_ref(self, ref: str) -> None:
        assert is_valid_git_ref(ref) is True
