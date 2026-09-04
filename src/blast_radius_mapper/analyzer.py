"""
Impact analysis via reverse BFS on the call graph.

Given a target function, traces all transitive dependents (functions that
would be affected if the target changes) and optionally detects dead code.
"""

from __future__ import annotations

from collections import deque

import networkx as nx

from blast_radius_mapper.logging_config import get_logger
from blast_radius_mapper.models import ImpactResult

logger = get_logger("analyzer")

_IMPLICIT_DUNDERS: set[str] = {
    "__init__",
    "__new__",
    "__del__",
    "__repr__",
    "__str__",
    "__hash__",
    "__eq__",
    "__ne__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__bool__",
    "__len__",
    "__getitem__",
    "__setitem__",
    "__delitem__",
    "__iter__",
    "__next__",
    "__contains__",
    "__call__",
    "__enter__",
    "__exit__",
    "__aenter__",
    "__aexit__",
    "__get__",
    "__set__",
    "__delete__",
    "__init_subclass__",
    "__class_getitem__",
    "__post_init__",  # dataclasses
}


def trace_blast_radius(
    graph: nx.DiGraph,
    target_fqn: str,
    max_depth: int = 50,
) -> ImpactResult:
    """
    Find all functions transitively affected by a change to ``target_fqn``.

    Uses BFS on the **reverse** graph: edges are caller→callee in the
    original graph, but we want callee→caller (who calls me?).  BFS gives
    us shortest-path depth, which is exactly what we need for distance-weighted
    scoring.

    Args:
        graph: The project call graph (caller→callee directed edges).
        target_fqn: FQN string of the function being changed.
        max_depth: Maximum BFS depth to explore.

    Returns:
        ImpactResult with direct callers, transitive dependents, and depth map.

    Raises:
        ValueError: If ``target_fqn`` is not a node in the graph.
    """
    if target_fqn not in graph:
        known_count = graph.number_of_nodes()
        raise ValueError(
            f"Function '{target_fqn}' not found in call graph "
            f"({known_count} functions indexed). "
            f"Check the fully qualified name."
        )

    reverse_graph = graph.reverse(copy=False)

    # BFS from target
    visited: dict[str, int] = {target_fqn: 0}
    queue: deque[tuple[str, int]] = deque([(target_fqn, 0)])

    direct_callers: list[str] = []
    transitive_dependents: list[str] = []

    while queue:
        current, depth = queue.popleft()

        if depth >= max_depth:
            continue

        for neighbor in reverse_graph.neighbors(current):
            if neighbor not in visited:
                next_depth = depth + 1
                visited[neighbor] = next_depth
                queue.append((neighbor, next_depth))

                if next_depth == 1:
                    direct_callers.append(neighbor)
                transitive_dependents.append(neighbor)

    # Collect unresolved calls originating from the target
    unresolved: list[str] = []
    for _, callee, data in graph.edges(target_fqn, data=True):
        if data.get("call_type") == "unresolved":
            unresolved.append(callee)

    logger.info(
        "Blast radius for '%s': %d direct callers, %d transitive dependents "
        "(max depth %d reached: %s)",
        target_fqn,
        len(direct_callers),
        len(transitive_dependents),
        max_depth,
        any(d == max_depth for d in visited.values()),
    )

    return ImpactResult(
        target=target_fqn,
        direct_callers=direct_callers,
        transitive_dependents=transitive_dependents,
        depth_map=visited,
        unresolved_calls=unresolved,
    )


def detect_dead_code(graph: nx.DiGraph) -> list[str]:
    """
    Find functions with zero incoming edges (never called).

    These are potential dead code, entry points, or signal handlers.
    The distinction is left to the developer — the tool just flags them.

    Excludes:
    - Test functions (they are entry points by nature)
    - ``__init__`` methods (called implicitly by constructors)
    - ``__main__`` guards
    - Dunder methods that may be called by the runtime

    Returns:
        List of FQN strings of potentially dead functions.
    """
    dead: list[str] = []

    for node in graph.nodes:
        # Skip test functions — they're meant to have no callers
        if graph.nodes[node].get("is_test", False):
            continue

        # Skip implicit dunders
        short_name = node.rsplit(".", maxsplit=1)[-1]
        if short_name in _IMPLICIT_DUNDERS:
            continue

        # Check for zero incoming edges
        if graph.in_degree(node) == 0:
            dead.append(node)

    logger.info("Detected %d potentially dead functions", len(dead))
    return dead


def find_covering_tests(
    target_fqn: str,
    graph: nx.DiGraph,
) -> list[str]:
    """
    Find test functions that transitively call the target function.

    A test "covers" a function (in the call-graph sense) if there exists
    a directed path from the test to the target in the call graph.

    This is structural coverage, not line-level coverage — it answers
    "which tests would break if this function's behavior changes?"
    """
    test_nodes = [n for n in graph.nodes if graph.nodes[n].get("is_test", False)]

    covering: list[str] = []
    for test_node in test_nodes:
        try:
            if nx.has_path(graph, test_node, target_fqn):
                covering.append(test_node)
        except nx.NodeNotFound:
            continue

    logger.debug(
        "%d/%d test functions cover '%s'",
        len(covering),
        len(test_nodes),
        target_fqn,
    )
    return covering


# Backwards-compatible alias
find_dead_code = detect_dead_code
