"""The live probe forwards its selected runtime/reviewer and preserves source."""

from pathlib import Path

import probe_native_plan_handoff as probe
import pytest
from crossby.models.ai import EffortLevel

from wade.config.loader import load_config
from wade.models.plan_bundle import PlanBundle, PlanMember


@pytest.mark.parametrize("independent", [False, True])
def test_probe_profiles_match_native_request_and_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, independent: bool
) -> None:
    arguments = ["probe", "--tool", "codex", "--model", "gpt-6.1-sol"]
    if independent:
        arguments += [
            "--effort",
            "high",
            "--review-ai",
            "codex",
            "--review-model",
            "gpt-6.1-sol",
            "--review-effort",
            "high",
            "--review-mode",
            "headless",
        ]
    monkeypatch.setattr(probe.sys, "argv", arguments)
    monkeypatch.setattr(probe.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(probe, "compose_session", lambda *args, **kwargs: None)
    monkeypatch.setenv("PATH", "original-path")

    def native_run(tool: str, plan_dir: str, **kwargs):
        assert tool == "codex"
        assert plan_dir == str(tmp_path / ".wade/plans")
        request = kwargs["request"]
        assert request.model == "gpt-6.1-sol"
        assert request.effort == (EffortLevel.HIGH if independent else None)
        fixture_review = load_config(tmp_path).ai.review_plan
        assert fixture_review == kwargs["config"].ai.review_plan
        assert fixture_review.enabled is True
        assert fixture_review.mode == ("headless" if independent else "prompt")
        assert fixture_review.tool == ("codex" if independent else None)
        assert fixture_review.model == ("gpt-6.1-sol" if independent else None)
        assert fixture_review.effort == ("high" if independent else None)
        context = kwargs["issue_context"]
        assert ("configured independent review" in context) is independent
        assert ("prompt self-review" in context) is not independent
        return PlanBundle(plans=(PlanMember(filename="PLAN.md", markdown="# fix: probe"),)), None

    monkeypatch.setattr(probe, "run_interactive_planning_session", native_run)
    assert probe.main() == 0
    assert (tmp_path / "greeting.py").read_text() == 'print("Hello")\n'


def test_probe_rejects_modified_fixture_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probe.sys, "argv", ["probe", "--tool", "codex"])
    monkeypatch.setattr(probe.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    monkeypatch.setattr(probe, "compose_session", lambda *args, **kwargs: None)
    monkeypatch.setenv("PATH", "original-path")

    def native_run(*args, **kwargs):
        (tmp_path / "greeting.py").write_text('print("Hi")\n')
        return PlanBundle(plans=(PlanMember(filename="PLAN.md", markdown="# fix: probe"),)), None

    monkeypatch.setattr(probe, "run_interactive_planning_session", native_run)
    with pytest.raises(ValueError, match="changed the fixture source"):
        probe.main()
