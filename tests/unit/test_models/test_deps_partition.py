from __future__ import annotations

from wade.models.deps import DependencyEdge, DependencyGraph


def test_disconnected_subgraphs_form_separate_chains() -> None:
    graph = DependencyGraph(
        edges=[
            DependencyEdge(from_task="A", to_task="B"),
            DependencyEdge(from_task="C", to_task="D"),
        ]
    )

    independent, chains = graph.partition(["A", "B", "C", "D"])

    assert independent == []
    assert chains == [["A", "B"], ["C", "D"]]


def test_single_connected_component() -> None:
    graph = DependencyGraph(
        edges=[
            DependencyEdge(from_task="A", to_task="B"),
            DependencyEdge(from_task="B", to_task="C"),
        ]
    )

    independent, chains = graph.partition(["A", "B", "C"])

    assert independent == []
    assert chains == [["A", "B", "C"]]


def test_mixed_independent_and_dependent() -> None:
    graph = DependencyGraph(edges=[DependencyEdge(from_task="A", to_task="B")])

    independent, chains = graph.partition(["A", "B", "C"])

    assert independent == ["C"]
    assert chains == [["A", "B"]]


def test_no_edges() -> None:
    graph = DependencyGraph(edges=[])

    independent, chains = graph.partition(["A", "B", "C"])

    assert independent == ["A", "B", "C"]
    assert chains == []


def test_single_node_with_edge_target_outside_set() -> None:
    graph = DependencyGraph(edges=[DependencyEdge(from_task="A", to_task="X")])

    independent, chains = graph.partition(["A"])

    assert independent == ["A"]
    assert chains == []
