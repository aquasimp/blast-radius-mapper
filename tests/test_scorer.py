"""Unit tests for confidence scoring mathematical invariants and boundary conditions."""

from __future__ import annotations

import networkx as nx

from blast_radius_mapper.models import ImpactResult
from blast_radius_mapper.scorer import compute_confidence, score_breakdown, score_label


class TestConfidenceScoring:
    """Validate mathematical properties of the refactoring confidence scoring algorithm."""

    def test_score_bounded_between_zero_and_one(self):
        """Score must always be in [0.0, 1.0] across arbitrary topologies."""
        graph = nx.DiGraph()
        graph.add_node("target", is_test=False)
        for i in range(10):
            node = f"caller_{i}"
            graph.add_node(node, is_test=(i % 2 == 0))
            graph.add_edge(node, "target")

        impact = ImpactResult(
            target="target",
            direct_callers=[f"caller_{i}" for i in range(10)],
            transitive_dependents=[f"caller_{i}" for i in range(10)],
            depth_map={f"caller_{i}": 1 for i in range(10)},
        )

        score_zero_cov = compute_confidence(impact, {}, graph)
        assert 0.0 <= score_zero_cov <= 1.0

        full_cov = {"target": 1.0, **{f"caller_{i}": 1.0 for i in range(10)}}
        score_full_cov = compute_confidence(impact, full_cov, graph)
        assert 0.0 <= score_full_cov <= 1.0
        assert score_full_cov > score_zero_cov

    def test_fanout_penalty_monotonicity(self):
        """As number of direct callers grows, fan-out score penalty increases (score decreases)."""
        graph = nx.DiGraph()
        graph.add_node("target", is_test=False)

        # 1 caller vs 20 callers
        graph.add_node("caller_1", is_test=False)
        graph.add_edge("caller_1", "target")

        impact_small = ImpactResult(
            target="target",
            direct_callers=["caller_1"],
            transitive_dependents=["caller_1"],
            depth_map={"caller_1": 1},
        )
        score_small = compute_confidence(impact_small, {"target": 0.8}, graph)

        # Add 19 more callers
        for i in range(2, 21):
            n = f"caller_{i}"
            graph.add_node(n, is_test=False)
            graph.add_edge(n, "target")

        impact_large = ImpactResult(
            target="target",
            direct_callers=[f"caller_{i}" for i in range(1, 21)],
            transitive_dependents=[f"caller_{i}" for i in range(1, 21)],
            depth_map={f"caller_{i}": 1 for i in range(1, 21)},
        )
        score_large = compute_confidence(impact_large, {"target": 0.8}, graph)

        assert score_small > score_large, "Wide fan-out should decrease safety confidence"

    def test_risk_labels_and_breakdown(self):
        assert "Safe" in score_label(0.95)
        assert "Moderate" in score_label(0.65)
        assert "Risky" in score_label(0.35)
        assert "Dangerous" in score_label(0.10)

        graph = nx.DiGraph()
        graph.add_node("t", is_test=False)
        impact = ImpactResult(target="t")
        breakdown = score_breakdown(impact, {}, graph)

        assert "target_coverage" in breakdown
        assert "fan_out_score" in breakdown
        assert "weighted_target_cov" in breakdown
