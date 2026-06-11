"""
CLI entry point for the Blast Radius Mapper.

Commands:
    analyze   Analyze blast radius for a specific function
    list      List all functions in the project
    graph     Generate full project call graph
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from blast_radius_mapper.logging_config import setup_logging
from blast_radius_mapper.models import AnalysisConfig


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "command") or args.command is None:
        parser.print_help()
        return 1

    setup_logging(verbose=getattr(args, "verbose", False))

    try:
        if args.command == "analyze":
            return _cmd_analyze(args)
        elif args.command == "list":
            return _cmd_list(args)
        elif args.command == "graph":
            return _cmd_graph(args)
        else:
            parser.print_help()
            return 1
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        if getattr(args, "verbose", False):
            import traceback
            traceback.print_exc()
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blast-radius",
        description=(
            "Blast Radius Mapper — Map the impact of Python code changes. "
            "Function-level call graph, transitive dependents, test coverage, "
            "confidence scoring, and interactive visualization."
        ),
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # ── analyze ──────────────────────────────────────────────────────
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Analyze blast radius for a specific function",
    )
    analyze_parser.add_argument(
        "project_root",
        type=Path,
        help="Path to the Python project root directory",
    )
    analyze_parser.add_argument(
        "--function", "-f",
        required=True,
        dest="target_function",
        help=(
            "Fully qualified name of the function to analyze "
            "(e.g. myproject.utils.helpers.retry)"
        ),
    )
    analyze_parser.add_argument(
        "--coverage", "-c",
        type=Path,
        dest="coverage_path",
        help="Path to coverage.py JSON output (run: coverage json -o coverage.json)",
    )
    analyze_parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("blast_radius.html"),
        dest="output_path",
        help="Output path for the interactive HTML graph (default: blast_radius.html)",
    )
    analyze_parser.add_argument(
        "--json",
        type=Path,
        dest="json_output",
        help="Output path for machine-readable JSON report",
    )
    analyze_parser.add_argument(
        "--max-depth",
        type=int,
        default=50,
        help="Maximum BFS depth for transitive impact (default: 50)",
    )
    analyze_parser.add_argument(
        "--dead-code",
        action="store_true",
        help="Include dead code detection (secondary signal)",
    )
    analyze_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose/debug logging",
    )

    # ── list ─────────────────────────────────────────────────────────
    list_parser = subparsers.add_parser(
        "list",
        help="List all functions in the project",
    )
    list_parser.add_argument(
        "project_root",
        type=Path,
        help="Path to the Python project root directory",
    )
    list_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    list_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
    )

    # ── graph ────────────────────────────────────────────────────────
    graph_parser = subparsers.add_parser(
        "graph",
        help="Generate full project call graph visualization",
    )
    graph_parser.add_argument(
        "project_root",
        type=Path,
        help="Path to the Python project root directory",
    )
    graph_parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("full_graph.html"),
        dest="output_path",
        help="Output path for the HTML graph (default: full_graph.html)",
    )
    graph_parser.add_argument(
        "--coverage", "-c",
        type=Path,
        dest="coverage_path",
        help="Path to coverage.py JSON output",
    )
    graph_parser.add_argument(
        "--max-nodes",
        type=int,
        default=1000,
        help="Maximum nodes to render (default: 1000)",
    )
    graph_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
    )

    return parser


# ── Command implementations ─────────────────────────────────────────────────


def _cmd_analyze(args: argparse.Namespace) -> int:
    """Execute the ``analyze`` command."""
    from blast_radius_mapper.pipeline import analyze_project
    from blast_radius_mapper.scorer import score_breakdown, score_label

    config = AnalysisConfig(
        project_root=args.project_root,
        target_function=args.target_function,
        coverage_path=args.coverage_path,
        output_path=args.output_path,
        json_output_path=args.json_output,
        max_depth=args.max_depth,
        include_dead_code=args.dead_code,
    )

    result = analyze_project(config)

    # Print summary to stdout
    print()
    print("=" * 70)
    print(f"  BLAST RADIUS ANALYSIS")
    print("=" * 70)
    print(f"  Target:      {result.target}")
    print(f"  Confidence:  {result.confidence_score:.0%} - {result.risk_label}")
    print(f"  Direct:      {len(result.direct_callers)} callers")
    print(f"  Transitive:  {len(result.transitive_dependents)} dependents")
    print(f"  Tests:       {len(result.test_functions)} covering tests")
    print(f"  Graph:       {config.output_path}")

    if result.direct_callers:
        print()
        print("  Direct Callers:")
        for caller in result.direct_callers[:20]:
            cov = result.coverage_map.get(caller, 0.0)
            cov_bar = _coverage_bar(cov)
            print(f"    {cov_bar} {caller}")
        if len(result.direct_callers) > 20:
            print(f"    ... and {len(result.direct_callers) - 20} more")

    if result.unresolved_calls:
        print()
        print(f"  [!] {len(result.unresolved_calls)} unresolved calls")

    if result.warnings:
        print()
        for warning in result.warnings:
            print(f"  [!] {warning}")

    if result.dead_code:
        print()
        print(f"  [DEAD] {len(result.dead_code)} potentially dead functions (--dead-code)")

    print("=" * 70)
    print()

    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    """Execute the ``list`` command."""
    from blast_radius_mapper.pipeline import list_functions
    import json as json_mod

    config = AnalysisConfig(project_root=args.project_root)
    functions = list_functions(config)

    if args.format == "json":
        print(json_mod.dumps(functions, indent=2))
    else:
        # Table format
        print()
        print(f"{'FQN':<60} {'Type':<10} {'Line':<6} {'Decorators'}")
        print("-" * 100)
        for func in functions:
            fqn_display = func["fqn"]
            if len(fqn_display) > 58:
                fqn_display = "..." + fqn_display[-57:]
            print(f"{fqn_display:<60} {func['type']:<10} {func['line']:<6} {func['decorators']}")
        print()
        print(f"Total: {len(functions)} functions")
        print()

    return 0


def _cmd_graph(args: argparse.Namespace) -> int:
    """Execute the ``graph`` command."""
    from blast_radius_mapper.pipeline import analyze_project

    config = AnalysisConfig(
        project_root=args.project_root,
        target_function=None,  # No target = full graph
        coverage_path=args.coverage_path,
        output_path=args.output_path,
    )

    analyze_project(config)

    print()
    print(f"Full project graph saved to: {config.output_path}")
    print()
    return 0


# ── Formatting helpers ───────────────────────────────────────────────────────


def _coverage_bar(coverage: float, width: int = 10) -> str:
    """Render a small ASCII coverage bar."""
    filled = int(coverage * width)
    empty = width - filled

    if coverage >= 0.8:
        indicator = "[+++]"
    elif coverage >= 0.5:
        indicator = "[++ ]"
    elif coverage > 0.0:
        indicator = "[+  ]"
    else:
        indicator = "[   ]"

    bar = "#" * filled + "-" * empty
    return f"{indicator} [{bar}] {coverage:>5.0%}"


if __name__ == "__main__":
    sys.exit(main())
