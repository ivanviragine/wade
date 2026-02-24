"""Dependency domain models — DependencyEdge, DependencyGraph."""

from __future__ import annotations

from collections import defaultdict, deque

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
        task_set = set(task_ids)
        scoped_edges = [
            edge for edge in self.edges if edge.from_task in task_set and edge.to_task in task_set
        ]

        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in scoped_edges:
            adjacency[edge.from_task].add(edge.to_task)
            adjacency[edge.to_task].add(edge.from_task)

        visited: set[str] = set()
        independent: list[str] = []
        chains: list[list[str]] = []

        for task_id in task_ids:
            if task_id in visited:
                continue

            if task_id not in adjacency:
                visited.add(task_id)
                independent.append(task_id)
                continue

            queue: deque[str] = deque([task_id])
            component_nodes: list[str] = []
            visited.add(task_id)

            while queue:
                node = queue.popleft()
                component_nodes.append(node)
                for neighbor in sorted(adjacency[node]):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

            component_set = set(component_nodes)
            component_edges = [
                edge
                for edge in scoped_edges
                if edge.from_task in component_set and edge.to_task in component_set
            ]

            if component_edges:
                chains.append(DependencyGraph(edges=component_edges).topo_sort(component_nodes))
            else:
                independent.extend(component_nodes)

        return independent, chains
