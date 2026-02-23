"""Dependency domain models — DependencyEdge, DependencyGraph."""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel


class DependencyEdge(BaseModel):
    """A → B means A must be done before B."""

    from_task: str
    to_task: str
    reason: str = ""


class DependencyGraph(BaseModel):
    """Complete dependency analysis result."""

    edges: list[DependencyEdge] = []
    topological_order: list[str] = []
    independent_groups: list[list[str]] = []
    mermaid_diagram: str = ""
    tracking_task_id: str | None = None

    def topo_sort(self, task_ids: list[str] | None = None) -> list[str]:
        """Compute topological order of tasks.

        Uses Kahn's algorithm for deterministic ordering.
        Returns task IDs in dependency-respecting execution order.

        Raises ValueError if the graph contains a cycle.
        """
        # Build adjacency list and in-degree count
        adj: dict[str, list[str]] = defaultdict(list)
        in_degree: dict[str, int] = defaultdict(int)

        all_nodes = set(task_ids or [])
        for edge in self.edges:
            all_nodes.add(edge.from_task)
            all_nodes.add(edge.to_task)
            adj[edge.from_task].append(edge.to_task)
            in_degree[edge.to_task] += 1

        # Initialize in-degree for nodes with no incoming edges
        for node in all_nodes:
            if node not in in_degree:
                in_degree[node] = 0

        # Kahn's algorithm
        queue = sorted(n for n in all_nodes if in_degree[n] == 0)
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbor in sorted(adj.get(node, [])):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
            queue.sort()  # Deterministic ordering

        if len(result) != len(all_nodes):
            raise ValueError("Dependency graph contains a cycle")

        return result

    def generate_mermaid(self, task_titles: dict[str, str] | None = None) -> str:
        """Generate a Mermaid flowchart from the dependency graph.

        Args:
            task_titles: Optional mapping of task_id → title for node labels.
        """
        lines = ["graph TD"]
        titles = task_titles or {}

        # Add nodes
        all_nodes: set[str] = set()
        for edge in self.edges:
            all_nodes.add(edge.from_task)
            all_nodes.add(edge.to_task)

        for node in sorted(all_nodes):
            label = titles.get(node, f"#{node}")
            lines.append(f'    {node}["#{node} {label}"]')

        # Add edges
        for edge in self.edges:
            if edge.reason:
                lines.append(f"    {edge.from_task} -->|{edge.reason}| {edge.to_task}")
            else:
                lines.append(f"    {edge.from_task} --> {edge.to_task}")

        return "\n".join(lines)

    def partition(self, task_ids: list[str]) -> tuple[list[str], list[list[str]]]:
        """Partition tasks into independent tasks and dependency chains.

        Returns:
            Tuple of (independent_ids, chains) where chains are ordered lists
            of task IDs that must execute sequentially.
        """
        # Build dependency info scoped to requested task_ids
        task_set = set(task_ids)
        has_dep: set[str] = set()
        is_dep_of: set[str] = set()

        for edge in self.edges:
            if edge.from_task in task_set and edge.to_task in task_set:
                has_dep.add(edge.to_task)
                is_dep_of.add(edge.from_task)

        dependent = has_dep | is_dep_of
        independent = [t for t in task_ids if t not in dependent]

        # Build chains from dependent tasks using topo sort
        chains: list[list[str]] = []
        if dependent:
            ordered = self.topo_sort(list(dependent))
            # Group into connected chains
            chain: list[str] = []
            for task_id in ordered:
                chain.append(task_id)
            if chain:
                chains.append(chain)

        return independent, chains
