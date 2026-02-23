"""Tests for dependency graph models."""

from __future__ import annotations

import pytest

from ghaiw.models.deps import DependencyEdge, DependencyGraph


class TestTopoSort:
    def test_linear_chain(self) -> None:
        graph = DependencyGraph(
            edges=[
                DependencyEdge(from_task="1", to_task="2"),
                DependencyEdge(from_task="2", to_task="3"),
            ]
        )
        order = graph.topo_sort(["1", "2", "3"])
        assert order == ["1", "2", "3"]

    def test_independent_tasks(self) -> None:
        graph = DependencyGraph(edges=[])
        order = graph.topo_sort(["3", "1", "2"])
        # Independent tasks are sorted by ID for determinism
        assert order == ["1", "2", "3"]

    def test_diamond_dependency(self) -> None:
        graph = DependencyGraph(
            edges=[
                DependencyEdge(from_task="1", to_task="2"),
                DependencyEdge(from_task="1", to_task="3"),
                DependencyEdge(from_task="2", to_task="4"),
                DependencyEdge(from_task="3", to_task="4"),
            ]
        )
        order = graph.topo_sort(["1", "2", "3", "4"])
        assert order[0] == "1"
        assert order[-1] == "4"
        assert order.index("1") < order.index("2")
        assert order.index("1") < order.index("3")

    def test_cycle_raises(self) -> None:
        graph = DependencyGraph(
            edges=[
                DependencyEdge(from_task="1", to_task="2"),
                DependencyEdge(from_task="2", to_task="1"),
            ]
        )
        with pytest.raises(ValueError, match="cycle"):
            graph.topo_sort(["1", "2"])


class TestMermaid:
    def test_generate_mermaid(self) -> None:
        graph = DependencyGraph(
            edges=[
                DependencyEdge(from_task="1", to_task="2", reason="schema first"),
            ]
        )
        mermaid = graph.generate_mermaid({"1": "Add schema", "2": "Add API"})
        assert "graph TD" in mermaid
        assert "1" in mermaid
        assert "2" in mermaid
        assert "schema first" in mermaid

    def test_empty_graph(self) -> None:
        graph = DependencyGraph(edges=[])
        mermaid = graph.generate_mermaid()
        assert mermaid == "graph TD"


class TestPartition:
    def test_all_independent(self) -> None:
        graph = DependencyGraph(edges=[])
        independent, chains = graph.partition(["1", "2", "3"])
        assert independent == ["1", "2", "3"]
        assert chains == []

    def test_mixed(self) -> None:
        graph = DependencyGraph(edges=[DependencyEdge(from_task="2", to_task="3")])
        independent, chains = graph.partition(["1", "2", "3"])
        assert "1" in independent
        assert len(chains) == 1
        assert "2" in chains[0]
        assert "3" in chains[0]
