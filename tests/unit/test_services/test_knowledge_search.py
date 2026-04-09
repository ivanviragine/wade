"""Unit tests for knowledge_search boolean query parser and evaluator."""

from __future__ import annotations

import pytest

from wade.services.knowledge_search import (
    AndNode,
    NotNode,
    OrNode,
    PhraseNode,
    TermNode,
    evaluate_query,
    parse_query,
)


class TestParseQuery:
    def test_single_term(self) -> None:
        node = parse_query("worktree")
        assert isinstance(node, TermNode)
        assert node.term == "worktree"

    def test_quoted_phrase(self) -> None:
        node = parse_query('"exact phrase"')
        assert isinstance(node, PhraseNode)
        assert node.phrase == "exact phrase"

    def test_and_explicit(self) -> None:
        node = parse_query("worktree AND safety")
        assert isinstance(node, AndNode)
        assert isinstance(node.left, TermNode)
        assert node.left.term == "worktree"
        assert isinstance(node.right, TermNode)
        assert node.right.term == "safety"

    def test_and_implicit(self) -> None:
        node = parse_query("worktree safety")
        assert isinstance(node, AndNode)
        assert isinstance(node.left, TermNode)
        assert node.left.term == "worktree"
        assert isinstance(node.right, TermNode)
        assert node.right.term == "safety"

    def test_or(self) -> None:
        node = parse_query("worktree OR branch")
        assert isinstance(node, OrNode)
        assert isinstance(node.left, TermNode)
        assert isinstance(node.right, TermNode)

    def test_not(self) -> None:
        node = parse_query("NOT deprecated")
        assert isinstance(node, NotNode)
        assert isinstance(node.child, TermNode)
        assert node.child.term == "deprecated"

    def test_parentheses(self) -> None:
        node = parse_query("(worktree OR branch) AND safety")
        assert isinstance(node, AndNode)
        assert isinstance(node.left, OrNode)
        assert isinstance(node.right, TermNode)

    def test_complex_expression(self) -> None:
        node = parse_query('(git OR worktree) AND "error handling" AND NOT deprecated')
        assert isinstance(node, AndNode)

    def test_empty_query(self) -> None:
        node = parse_query("")
        assert isinstance(node, TermNode)
        assert node.term == ""

    def test_unterminated_quote(self) -> None:
        node = parse_query('"unclosed phrase')
        assert isinstance(node, PhraseNode)
        assert node.phrase == "unclosed phrase"

    def test_case_insensitive_operators(self) -> None:
        node = parse_query("a and b or c")
        assert isinstance(node, OrNode)

    def test_malformed_missing_rparen(self) -> None:
        with pytest.raises(ValueError, match="Expected"):
            parse_query("(a AND b")

    def test_unexpected_rparen(self) -> None:
        with pytest.raises(ValueError, match="Unexpected"):
            parse_query(")")

    def test_trailing_operator(self) -> None:
        with pytest.raises(ValueError, match="Unexpected"):
            parse_query("a AND")


class TestEvaluateQuery:
    def test_term_match(self) -> None:
        node = parse_query("worktree")
        assert evaluate_query(node, "Use git worktree for isolation")

    def test_term_no_match(self) -> None:
        node = parse_query("docker")
        assert not evaluate_query(node, "Use git worktree for isolation")

    def test_case_insensitive(self) -> None:
        node = parse_query("Worktree")
        assert evaluate_query(node, "use git worktree for isolation")

    def test_phrase_match(self) -> None:
        node = parse_query('"error handling"')
        assert evaluate_query(node, "Always add error handling in services")

    def test_phrase_no_match(self) -> None:
        node = parse_query('"error handling"')
        assert not evaluate_query(node, "Handle the error carefully")

    def test_and_both_match(self) -> None:
        node = parse_query("git AND worktree")
        assert evaluate_query(node, "Use git worktree for safe development")

    def test_and_one_missing(self) -> None:
        node = parse_query("git AND docker")
        assert not evaluate_query(node, "Use git worktree for safe development")

    def test_or_one_matches(self) -> None:
        node = parse_query("docker OR worktree")
        assert evaluate_query(node, "Use git worktree for safe development")

    def test_or_neither_matches(self) -> None:
        node = parse_query("docker OR kubernetes")
        assert not evaluate_query(node, "Use git worktree for safe development")

    def test_not_excludes(self) -> None:
        node = parse_query("NOT deprecated")
        assert evaluate_query(node, "This is the current approach")
        assert not evaluate_query(node, "This is deprecated")

    def test_complex_query(self) -> None:
        node = parse_query("(git OR testing) AND NOT deprecated")
        text = "Use git worktree for safe development"
        assert evaluate_query(node, text)

    def test_complex_query_excluded(self) -> None:
        node = parse_query("(git OR testing) AND NOT deprecated")
        text = "This git approach is deprecated"
        assert not evaluate_query(node, text)

    def test_empty_query_matches_everything(self) -> None:
        node = parse_query("")
        assert evaluate_query(node, "anything")

    def test_implicit_and(self) -> None:
        node = parse_query("worktree safety")
        assert evaluate_query(node, "worktree safety is important")
        assert not evaluate_query(node, "only worktree here")
