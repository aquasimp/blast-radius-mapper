"""
Interactive graph rendering with pyvis.

Generates a self-contained HTML file with a force-directed graph visualization
of the blast radius.  Nodes are color-coded by role (target, caller, test)
and coverage level.  Edges are styled by confidence.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
from pyvis.network import Network

from blast_radius_mapper.logging_config import get_logger
from blast_radius_mapper.models import ImpactResult

logger = get_logger("renderer")

# ── Color palette ────────────────────────────────────────────────────────────

COLOR_TARGET = "#e94560"        # Bright red — the function being changed
COLOR_DIRECT_CALLER = "#ff8c42" # Orange — direct callers
COLOR_TEST = "#4da6ff"          # Blue — test functions
COLOR_EDGE_HIGH = "#888888"     # Solid gray — high confidence edge
COLOR_EDGE_LOW = "#444444"      # Dim — low confidence edge
COLOR_BG = "#1a1a2e"            # Dark background


def render_blast_radius_graph(
    graph: nx.DiGraph,
    impact: ImpactResult,
    coverage_map: dict[str, float],
    output_path: str | Path = "blast_radius.html",
    max_nodes: int = 500,
) -> Path:
    """
    Render an interactive HTML graph of the blast radius.

    Node encoding:
    - **Target**: red star, largest
    - **Direct callers**: orange dots
    - **Tests**: blue triangles
    - **Transitive dependents**: color gradient by coverage (red→green)

    Edge encoding:
    - High confidence (≥ 0.8): solid gray
    - Low confidence (< 0.8): dashed, dimmer

    Args:
        graph: Full project call graph.
        impact: Blast radius analysis result.
        coverage_map: FQN → coverage ratio.
        output_path: Where to save the HTML file.
        max_nodes: Maximum nodes to render (prevents browser overload).

    Returns:
        Path to the generated HTML file.
    """
    output_path = Path(output_path)

    # Build the subgraph of affected nodes + tests
    affected_nodes = (
        {impact.target}
        | set(impact.direct_callers)
        | set(impact.transitive_dependents)
        | set(impact.test_functions)
    )

    # Limit node count for rendering performance
    if len(affected_nodes) > max_nodes:
        logger.warning(
            "Blast radius has %d nodes, limiting to %d for rendering. "
            "Use --max-nodes to increase.",
            len(affected_nodes),
            max_nodes,
        )
        # Keep target + direct callers + tests, then fill with closest dependents
        priority = (
            {impact.target}
            | set(impact.direct_callers)
            | set(impact.test_functions)
        )
        remaining = sorted(
            set(impact.transitive_dependents) - priority,
            key=lambda n: impact.depth_map.get(n, 999),
        )
        affected_nodes = priority | set(remaining[: max_nodes - len(priority)])

    # Filter subgraph to only include nodes that exist in the graph
    existing_nodes = affected_nodes & set(graph.nodes)
    subgraph = graph.subgraph(existing_nodes)

    # Create pyvis network
    net = Network(
        height="900px",
        width="100%",
        bgcolor=COLOR_BG,
        font_color="#eeeeee",
        directed=True,
        notebook=False,
        select_menu=True,
        filter_menu=True,
    )

    # Physics for good layout
    net.barnes_hut(
        gravity=-8000,
        central_gravity=0.3,
        spring_length=200,
        spring_strength=0.05,
        damping=0.09,
    )

    # Track sets for fast lookup
    direct_caller_set = set(impact.direct_callers)
    test_set = set(impact.test_functions)

    # Add nodes
    for node in subgraph.nodes:
        color, size, shape = _node_style(
            node, impact.target, direct_caller_set, test_set, coverage_map
        )
        depth = impact.depth_map.get(node, 0)
        cov = coverage_map.get(node, 0.0)

        tooltip = _build_tooltip(node, depth, cov, test_set)
        label = node.rsplit(".", maxsplit=1)[-1]  # Short name

        net.add_node(
            node,
            label=label,
            title=tooltip,
            color=color,
            size=size,
            shape=shape,
        )

    # Add edges
    for u, v, data in subgraph.edges(data=True):
        conf = data.get("confidence", 1.0)
        edge_color = COLOR_EDGE_HIGH if conf >= 0.8 else COLOR_EDGE_LOW
        dashes = conf < 0.8
        call_type = data.get("call_type", "unknown")

        net.add_edge(
            u, v,
            color=edge_color,
            dashes=dashes,
            title=f"Type: {call_type} | Confidence: {conf:.0%}",
            width=1.5 if conf >= 0.8 else 0.8,
        )

    # Add legend as a note in the HTML
    _inject_legend(net, impact)

    _save_graph_utf8(net, output_path)
    logger.info("Interactive graph saved to %s (%d nodes)", output_path, len(existing_nodes))

    return output_path


def render_full_graph(
    graph: nx.DiGraph,
    coverage_map: dict[str, float],
    output_path: str | Path = "full_graph.html",
    max_nodes: int = 1000,
) -> Path:
    """
    Render the full project call graph (not scoped to a single function).

    Useful for exploring the project's overall structure.
    """
    output_path = Path(output_path)

    if graph.number_of_nodes() > max_nodes:
        logger.warning(
            "Full graph has %d nodes, limiting to %d. "
            "Use --max-nodes to increase.",
            graph.number_of_nodes(),
            max_nodes,
        )
        # Keep highest-degree nodes
        degrees = sorted(graph.degree, key=lambda x: x[1], reverse=True)
        keep = {n for n, _ in degrees[:max_nodes]}
        graph = graph.subgraph(keep)

    net = Network(
        height="900px",
        width="100%",
        bgcolor=COLOR_BG,
        font_color="#eeeeee",
        directed=True,
        notebook=False,
        select_menu=True,
        filter_menu=True,
    )

    net.barnes_hut(
        gravity=-5000,
        central_gravity=0.3,
        spring_length=250,
        spring_strength=0.04,
        damping=0.09,
    )

    for node in graph.nodes:
        is_test = graph.nodes[node].get("is_test", False)
        cov = coverage_map.get(node, 0.0)

        if is_test:
            color = COLOR_TEST
            shape = "triangle"
        else:
            color = _coverage_to_color(cov)
            shape = "dot"

        label = node.rsplit(".", maxsplit=1)[-1]
        size = min(10 + graph.degree(node) * 2, 50)

        net.add_node(
            node,
            label=label,
            title=f"<b>{node}</b><br>Coverage: {cov:.0%}<br>Degree: {graph.degree(node)}",
            color=color,
            size=size,
            shape=shape,
        )

    for u, v, data in graph.edges(data=True):
        conf = data.get("confidence", 1.0)
        net.add_edge(
            u, v,
            color=COLOR_EDGE_HIGH if conf >= 0.8 else COLOR_EDGE_LOW,
            dashes=conf < 0.8,
            width=1.0,
        )

    _save_graph_utf8(net, output_path)
    logger.info("Full graph saved to %s (%d nodes)", output_path, graph.number_of_nodes())
    return output_path


# ── Internal helpers ─────────────────────────────────────────────────────────


def _node_style(
    node: str,
    target: str,
    direct_callers: set[str],
    tests: set[str],
    coverage_map: dict[str, float],
) -> tuple[str, int, str]:
    """Return (color, size, shape) for a graph node."""
    if node == target:
        return COLOR_TARGET, 40, "star"
    if node in tests:
        return COLOR_TEST, 20, "triangle"
    if node in direct_callers:
        return COLOR_DIRECT_CALLER, 30, "dot"

    # Coverage gradient for transitive dependents
    cov = coverage_map.get(node, 0.0)
    return _coverage_to_color(cov), 20, "dot"


def _coverage_to_color(coverage: float) -> str:
    """Map coverage [0, 1] to a color from red → yellow → green."""
    hue = int(coverage * 120)  # 0=red, 60=yellow, 120=green
    return f"hsl({hue}, 80%, 50%)"


def _build_tooltip(
    node: str,
    depth: int,
    coverage: float,
    test_set: set[str],
) -> str:
    """Build an HTML tooltip for a graph node."""
    node_type = "Test" if node in test_set else "Source"
    return (
        f"<b>{node}</b><br>"
        f"Depth: {depth}<br>"
        f"Coverage: {coverage:.0%}<br>"
        f"Type: {node_type}"
    )


def _inject_legend(net: Network, impact: ImpactResult) -> None:
    """Add a summary heading to the pyvis graph."""
    # Use ASCII-safe risk labels to avoid encoding issues on Windows
    risk = _ascii_risk_label(impact.confidence_score)
    heading = (
        f"Blast Radius: {impact.target} | "
        f"Direct: {len(impact.direct_callers)} | "
        f"Transitive: {len(impact.transitive_dependents)} | "
        f"Score: {impact.confidence_score:.0%} {risk}"
    )
    net.heading = heading


def _ascii_risk_label(score: float) -> str:
    """Return an ASCII-safe risk label (no emoji)."""
    if score >= 0.80:
        return "[SAFE]"
    if score >= 0.50:
        return "[MODERATE]"
    if score >= 0.20:
        return "[RISKY]"
    return "[DANGEROUS]"


def _save_graph_utf8(net: Network, output_path: Path) -> None:
    """
    Save pyvis graph as UTF-8 HTML.

    pyvis's built-in save_graph uses the system default encoding, which
    on Windows (cp1252) can't handle Unicode characters like emoji.
    We generate the HTML and write it ourselves with explicit UTF-8.
    """
    html_content = net.generate_html()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")
