"""
Confidence scoring for code change safety.

Combines five weighted factors into a single [0.0, 1.0] score that
quantifies how safe it is to modify a given function.
"""

from __future__ import annotations

import math

import networkx as nx

from blast_radius_mapper.logging_config import get_logger
from blast_radius_mapper.models import ImpactResult

logger = get_logger("scorer")

# ── Weight constants (tunable) ───────────────────────────────────────────────

W_TARGET_COVERAGE = 0.30
"""Weight for the target function's own line coverage."""

W_DEPENDENT_COVERAGE = 0.25
"""Weight for average coverage across transitive dependents."""

W_FAN_OUT = 0.15
"""Weight for fan-out penalty (more direct callers = riskier)."""

W_DEPTH = 0.10
"""Weight for depth penalty (deeper dependency chains = riskier)."""

W_TEST_REACHABILITY = 0.20
"""Weight for fraction of dependents reachable from at least one test."""


def compute_confidence(
    impact: ImpactResult,
    coverage_map: dict[str, float],
    graph: nx.DiGraph,
) -> float:
    """
    Compute a confidence score for safely changing the target function.

    Formula::

        confidence = W1 × target_coverage
                   + W2 × avg_dependent_coverage
                   + W3 × (1 / log2(fan_out + 2))
                   + W4 × (1 / log2(max_depth + 2))
                   + W5 × test_reachability_ratio

    Args:
        impact: The ImpactResult from blast radius analysis.
        coverage_map: FQN → line coverage ratio [0.0, 1.0].
        graph: The full project call graph.

    Returns:
        Float in [0.0, 1.0] where 1.0 = very safe, 0.0 = very risky.
    """
    # Factor 1: Target function's own coverage
    target_cov = coverage_map.get(impact.target, 0.0)

    # Factor 2: Average dependent coverage
    if impact.transitive_dependents:
        dep_coverages = [coverage_map.get(d, 0.0) for d in impact.transitive_dependents]
        avg_dep_cov = sum(dep_coverages) / len(dep_coverages)
    else:
        # No dependents = isolated function = safe
        avg_dep_cov = 1.0

    # Factor 3: Fan-out penalty (inverse logarithmic)
    fan_out = len(impact.direct_callers)
    fan_out_score = 1.0 / math.log2(fan_out + 2)

    # Factor 4: Depth penalty
    max_depth = max(impact.depth_map.values()) if impact.depth_map else 0
    depth_score = 1.0 / math.log2(max_depth + 2)

    # Factor 5: Test reachability ratio
    test_reach_ratio = _compute_test_reachability(impact, graph)

    # Weighted sum
    score = (
        W_TARGET_COVERAGE * target_cov
        + W_DEPENDENT_COVERAGE * avg_dep_cov
        + W_FAN_OUT * fan_out_score
        + W_DEPTH * depth_score
        + W_TEST_REACHABILITY * test_reach_ratio
    )

    score = round(min(max(score, 0.0), 1.0), 3)

    logger.info(
        "Confidence for '%s': %.3f "
        "(target_cov=%.2f, dep_cov=%.2f, fan_out=%d→%.2f, "
        "max_depth=%d→%.2f, test_reach=%.2f)",
        impact.target,
        score,
        target_cov,
        avg_dep_cov,
        fan_out,
        fan_out_score,
        max_depth,
        depth_score,
        test_reach_ratio,
    )

    return score


def _compute_test_reachability(
    impact: ImpactResult,
    graph: nx.DiGraph,
) -> float:
    """
    Compute the fraction of transitive dependents reachable from at least
    one test function.

    If there are no dependents, returns 1.0 (isolated function).
    If there are no tests, returns 0.0.
    """
    if not impact.transitive_dependents:
        return 1.0

    if not impact.test_functions:
        return 0.0

    test_set = set(impact.test_functions)
    reachable_count = 0

    for dep in impact.transitive_dependents:
        # Check if any test can reach this dependent
        for test in test_set:
            try:
                if nx.has_path(graph, test, dep):
                    reachable_count += 1
                    break
            except nx.NodeNotFound:
                continue

    return reachable_count / len(impact.transitive_dependents)


def score_label(score: float) -> str:
    """Return a human-readable risk label for a confidence score."""
    if score >= 0.80:
        return "🟢 Safe"
    if score >= 0.50:
        return "🟡 Moderate"
    if score >= 0.20:
        return "🟠 Risky"
    return "🔴 Dangerous"


def score_breakdown(
    impact: ImpactResult,
    coverage_map: dict[str, float],
    graph: nx.DiGraph,
) -> dict[str, float]:
    """
    Return a detailed breakdown of the confidence score components.

    Useful for CLI ``--verbose`` output to explain *why* the score is
    what it is.
    """
    target_cov = coverage_map.get(impact.target, 0.0)

    if impact.transitive_dependents:
        dep_coverages = [coverage_map.get(d, 0.0) for d in impact.transitive_dependents]
        avg_dep_cov = sum(dep_coverages) / len(dep_coverages)
    else:
        avg_dep_cov = 1.0

    fan_out = len(impact.direct_callers)
    fan_out_score = 1.0 / math.log2(fan_out + 2)

    max_depth = max(impact.depth_map.values()) if impact.depth_map else 0
    depth_score = 1.0 / math.log2(max_depth + 2)

    test_reach = _compute_test_reachability(impact, graph)

    return {
        "target_coverage": round(target_cov, 3),
        "avg_dependent_coverage": round(avg_dep_cov, 3),
        "fan_out": fan_out,
        "fan_out_score": round(fan_out_score, 3),
        "max_depth": max_depth,
        "depth_score": round(depth_score, 3),
        "test_reachability": round(test_reach, 3),
        "weighted_target_cov": round(W_TARGET_COVERAGE * target_cov, 3),
        "weighted_dep_cov": round(W_DEPENDENT_COVERAGE * avg_dep_cov, 3),
        "weighted_fan_out": round(W_FAN_OUT * fan_out_score, 3),
        "weighted_depth": round(W_DEPTH * depth_score, 3),
        "weighted_test_reach": round(W_TEST_REACHABILITY * test_reach, 3),
    }
