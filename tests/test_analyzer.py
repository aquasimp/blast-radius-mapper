"""Unit tests for blast radius analyzer -- cycles, dead code, and test coverage."""

from __future__ import annotations

import networkx as nx

from blast_radius_mapper.analyzer import (
    detect_dead_code,
    find_covering_tests,
    find_dead_code,
    trace_blast_radius,
)


class TestCyclicCallGraphs:
    """Verify that cycles and recursion in call graphs are handled safely."""

    def test_self_recursive_function(self):
        """f calls f -- BFS must not loop infinitely."""
        graph = nx.DiGraph()
        graph.add_node("pkg.mod.recurse", is_test=False)
        graph.add_edge(
            "pkg.mod.recurse",
            "pkg.mod.recurse",
            call_type="direct",
            confidence=1.0,
        )

        result = trace_blast_radius(graph, "pkg.mod.recurse", max_depth=10)

        assert result.target == "pkg.mod.recurse"
        assert result.transitive_dependents == []  # target itself not in dependents list
        assert result.depth_map == {"pkg.mod.recurse": 0}

    def test_mutually_recursive_functions(self):
        """a -> b -> a cycle with external caller c -> a."""
        graph = nx.DiGraph()
        graph.add_node("pkg.mod.a", is_test=False)
        graph.add_node("pkg.mod.b", is_test=False)
        graph.add_node("pkg.mod.c", is_test=False)

        # c calls a, a calls b, b calls a
        graph.add_edge("pkg.mod.c", "pkg.mod.a", call_type="direct", confidence=1.0)
        graph.add_edge("pkg.mod.a", "pkg.mod.b", call_type="direct", confidence=1.0)
        graph.add_edge("pkg.mod.b", "pkg.mod.a", call_type="direct", confidence=1.0)

        # Changing 'a' affects 'b' (who calls 'a') and 'c' (who calls 'a')
        result = trace_blast_radius(graph, "pkg.mod.a", max_depth=10)

        assert set(result.direct_callers) == {"pkg.mod.b", "pkg.mod.c"}
        assert set(result.transitive_dependents) == {"pkg.mod.b", "pkg.mod.c"}
        assert result.depth_map["pkg.mod.b"] == 1
        assert result.depth_map["pkg.mod.c"] == 1

    def test_max_depth_bounding(self):
        """Linear chain a -> b -> c -> d -- max_depth=1 should only return direct caller."""
        graph = nx.DiGraph()
        for n in ["d", "c", "b", "a"]:
            graph.add_node(f"pkg.mod.{n}", is_test=False)

        graph.add_edge("pkg.mod.c", "pkg.mod.d", call_type="direct", confidence=1.0)
        graph.add_edge("pkg.mod.b", "pkg.mod.c", call_type="direct", confidence=1.0)
        graph.add_edge("pkg.mod.a", "pkg.mod.b", call_type="direct", confidence=1.0)

        # Tracing 'd' with max_depth=1 should only include 'c'
        result = trace_blast_radius(graph, "pkg.mod.d", max_depth=1)
        assert result.transitive_dependents == ["pkg.mod.c"]
        assert "pkg.mod.b" not in result.transitive_dependents


class TestDeadCodeDetection:
    """Verify dead code analysis filters dunders and test functions."""

    def test_dead_code_detection_filters_implicit_dunders(self):
        graph = nx.DiGraph()
        # Normal uncalled function -> dead
        graph.add_node("pkg.mod.orphan_func", is_test=False)
        # Implicit dunders -> should NOT be flagged as dead
        graph.add_node("pkg.mod.User.__init__", is_test=False)
        graph.add_node("pkg.mod.User.__repr__", is_test=False)
        graph.add_node("pkg.mod.Context.__enter__", is_test=False)
        # Test function -> should NOT be flagged as dead
        graph.add_node("tests.test_mod.test_case", is_test=True)

        dead = find_dead_code(graph)
        assert "pkg.mod.orphan_func" in dead
        assert "pkg.mod.User.__init__" not in dead
        assert "pkg.mod.User.__repr__" not in dead
        assert "pkg.mod.Context.__enter__" not in dead
        assert "tests.test_mod.test_case" not in dead

        dead_detected = detect_dead_code(graph)
        assert dead_detected == dead


class TestCoveringTests:
    """Verify structural test coverage path discovery."""

    def test_find_covering_tests_multi_hop(self):
        graph = nx.DiGraph()
        graph.add_node("tests.test_api.test_flow", is_test=True)
        graph.add_node("pkg.api.handle_req", is_test=False)
        graph.add_node("pkg.core.validate", is_test=False)

        # test -> handle_req -> validate
        graph.add_edge(
            "tests.test_api.test_flow",
            "pkg.api.handle_req",
            call_type="direct",
            confidence=1.0,
        )
        graph.add_edge(
            "pkg.api.handle_req",
            "pkg.core.validate",
            call_type="direct",
            confidence=1.0,
        )

        covering = find_covering_tests("pkg.core.validate", graph)
        assert covering == ["tests.test_api.test_flow"]
