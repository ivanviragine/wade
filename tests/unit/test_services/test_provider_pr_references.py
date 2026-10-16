"""Provider-aware PR references, closure intent, and merge-time closure (#490)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wade.models.config import ProjectConfig, ProviderConfig, ProviderID
from wade.models.session import MergeStatus
from wade.models.task import Task, TaskReference
from wade.services.implementation_service.draft_pr import _build_draft_pr_body
from wade.services.implementation_service.lifecycle import (
    _apply_pr_refs,
    _build_pr_body,
    _merge_pr,
    task_close_intent,
)
from wade.utils.markdown import MarkdownStructureError

URL = "https://app.clickup.com/t/abc1234"
OPAQUE = TaskReference(provider="clickup", task_id="abc1234", url=URL)
NUMERIC = TaskReference(provider="clickup", task_id="42", url="https://app.clickup.com/t/42")
PLAN = "<!-- wade:plan:start -->\nCloses #42 inside the embedded plan.\n<!-- wade:plan:end -->"


def _apply(body: str, ref: TaskReference, close: bool, parent: str | None = None) -> str:
    parent_ref = TaskReference(provider=ref.provider, task_id=parent) if parent else None
    return _apply_pr_refs(
        body, ref.task_id, close, None, reference=ref, parent_reference=parent_ref
    )


class TestExternalReferenceLines:
    def test_draft_body_uses_a_qualified_non_closing_reference(self) -> None:
        body = _build_draft_pr_body("Plan text", OPAQUE)
        assert body.startswith(f"Implements [clickup:abc1234]({URL})\n")
        assert "#" not in body.splitlines()[0]
        assert _build_draft_pr_body("Plan", "42").startswith("Implements #42\n")

    def test_close_and_no_close_are_idempotent_and_legible(self) -> None:
        draft = _build_draft_pr_body("Plan text", OPAQUE)
        closing = _apply(draft, OPAQUE, True, parent="parent789")
        assert closing.startswith(
            f"Part of clickup:parent789\nCompletes [clickup:abc1234]({URL})\n"
        )
        assert _apply(closing, OPAQUE, True, parent="parent789") == closing
        kept_open = _apply(closing, OPAQUE, False, parent="parent789")
        assert kept_open.startswith(
            f"Part of clickup:parent789\nImplements [clickup:abc1234]({URL})\n"
        )
        assert _apply(kept_open, OPAQUE, False, parent="parent789") == kept_open
        assert task_close_intent(closing, OPAQUE) is True
        assert task_close_intent(kept_open, OPAQUE) is False

    @pytest.mark.parametrize("legacy", ["Closes #42", "Implements #42"])
    def test_legacy_numeric_external_lines_are_replaced(self, legacy: str) -> None:
        body = f"Part of #7\n{legacy}\n\n## Notes\nSee also #42 in prose.\n\n{PLAN}"
        updated = _apply(body, NUMERIC, False, parent="7")
        lines = updated.splitlines()
        assert lines[:2] == ["Part of clickup:7", f"Implements {NUMERIC.link}"]
        assert "See also #42 in prose." in updated  # unrelated prose preserved
        assert PLAN in updated  # protected block untouched
        outside = updated.replace(PLAN, "")
        assert not [line for line in outside.splitlines() if line.startswith("Closes")]

    def test_ambiguous_legacy_closing_line_refuses(self) -> None:
        with pytest.raises(MarkdownStructureError, match="would close GitHub issue #42"):
            _apply("Closes #42 and the follow-up\n", NUMERIC, True)

    def test_lines_inside_fences_and_blocks_are_never_rewritten(self) -> None:
        body = f"```\nCloses #42\nImplements #42\n```\n\n{PLAN}"
        updated = _apply(body, NUMERIC, True)
        assert updated.startswith(f"Completes {NUMERIC.link}\n\n```\nCloses #42\n")
        assert PLAN in updated

    def test_fallback_body_records_intent_for_every_provider(self) -> None:
        task = Task(id="abc1234", title="fix: x", url=URL)
        closing = _build_pr_body(task, reference=OPAQUE, parent_reference=None)
        assert closing == f"Completes [clickup:abc1234]({URL})"
        kept_open = _build_pr_body(task, close_issue=False, reference=OPAQUE)
        assert kept_open == f"Implements [clickup:abc1234]({URL})"
        github = _build_pr_body(Task(id="42", title="fix: x"), close_issue=False)
        assert github == "Implements #42"


class TestGitHubReferenceLines:
    def test_github_keeps_closing_keywords(self) -> None:
        updated = _apply_pr_refs(f"Implements #42\n\n{PLAN}", "42", True, "7")
        assert updated.startswith("Part of #7\nCloses #42\n\n")
        assert PLAN in updated
        assert task_close_intent(updated, TaskReference(task_id="42")) is True

    def test_no_close_downgrades_only_this_task(self) -> None:
        body = "Closes #42\nCloses #420\n"
        updated = _apply_pr_refs(body, "42", False, None)
        assert updated == "Implements #42\nCloses #420\n"
        assert task_close_intent(updated, TaskReference(task_id="42")) is False

    def test_absent_reference_is_unknown_intent(self) -> None:
        assert task_close_intent("No references here.", TaskReference(task_id="42")) is None
        assert task_close_intent("Closes #420", TaskReference(task_id="42")) is None


class TestMergeTimeClosure:
    @pytest.fixture
    def merge(self, tmp_path: Path) -> Iterator[MagicMock]:
        lifecycle = "wade.services.implementation_service.lifecycle"
        config = ProjectConfig(provider=ProviderConfig(name=ProviderID.CLICKUP))
        patches = [
            patch(f"{lifecycle}.git_repo.main_checkout_root", return_value=tmp_path),
            patch(f"{lifecycle}.git_repo.is_head_attached", return_value=True),
            patch(f"{lifecycle}._pull_main_after_merge"),
            patch("wade.config.loader.load_config", return_value=config),
        ]
        merge_pr = patch(f"{lifecycle}.git_pr.merge_pr")
        for item in patches:
            item.start()
        mocked = merge_pr.start()
        yield mocked
        patch.stopall()

    def _run(self, tmp_path: Path, body: str | None) -> MagicMock:
        provider = MagicMock()
        with patch(
            "wade.services.implementation_service.lifecycle.git_pr.get_pr_body",
            return_value=body,
        ):
            status = _merge_pr(tmp_path, "feat/idv1_61626331323334-x", 5, "abc1234", None, provider)
        assert status == MergeStatus.MERGED
        return provider

    def test_close_intent_closes_the_exact_provider_id(
        self, merge: MagicMock, tmp_path: Path
    ) -> None:
        provider = self._run(tmp_path, f"Completes [clickup:abc1234]({URL})\n")
        provider.close_task.assert_called_once_with("abc1234")
        merge.assert_called_once()

    def test_no_close_intent_leaves_the_task_open(self, merge: MagicMock, tmp_path: Path) -> None:
        provider = self._run(tmp_path, f"Implements [clickup:abc1234]({URL})\n")
        provider.close_task.assert_not_called()

    def test_unreadable_body_never_closes(self, merge: MagicMock, tmp_path: Path) -> None:
        provider = self._run(tmp_path, None)
        provider.close_task.assert_not_called()

    def test_legacy_body_without_a_reference_keeps_closing(
        self, merge: MagicMock, tmp_path: Path
    ) -> None:
        provider = self._run(tmp_path, "Manually written PR body.")
        provider.close_task.assert_called_once_with("abc1234")

    def test_failed_merge_never_closes(self, merge: MagicMock, tmp_path: Path) -> None:
        merge.side_effect = RuntimeError("merge blocked")
        provider = MagicMock()
        with patch(
            "wade.services.implementation_service.lifecycle.git_pr.get_pr_body",
            return_value=f"Completes [clickup:abc1234]({URL})",
        ):
            status = _merge_pr(tmp_path, "feat/x", 5, "abc1234", None, provider)
        assert status == MergeStatus.MERGE_FAILED
        provider.close_task.assert_not_called()
