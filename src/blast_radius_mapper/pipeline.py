"""
Main analysis pipeline — orchestrates the full blast radius analysis.

This module ties together all components: scanning, parsing, resolution,
call graph construction, impact analysis, coverage integration, scoring,
and rendering.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Optional

import networkx as nx

from blast_radius_mapper.analyzer import (
    detect_dead_code,
    find_covering_tests,
    trace_blast_radius,
)
from blast_radius_mapper.call_graph import build_call_graph
from blast_radius_mapper.coverage_integrator import (
    compute_all_function_coverage,
    load_coverage_data,
)
from blast_radius_mapper.extractor import extract_definitions
from blast_radius_mapper.logging_config import get_logger
from blast_radius_mapper.models import AnalysisConfig, ImpactResult
from blast_radius_mapper.renderer import render_blast_radius_graph, render_full_graph
from blast_radius_mapper.resolver import resolve_class_bases, resolve_imports
from blast_radius_mapper.scanner import discover_python_files, filepath_to_module
from blast_radius_mapper.scorer import compute_confidence, score_breakdown
from blast_radius_mapper.symbol_table import SymbolTable
from blast_radius_mapper.utils import safe_parse

logger = get_logger("pipeline")


def analyze_project(config: AnalysisConfig) -> ImpactResult:
    """
    Run the full blast radius analysis pipeline.

    Phases:
    1. Discover Python files
    2. Parse all files (AST)
    3. Extract function/class definitions
    4. Build symbol table
    5. Resolve imports
    6. Resolve class bases + compute C3 MRO
    7. Build call graph
    8. Trace blast radius (if target specified)
    9. Integrate coverage data (if available)
    10. Compute confidence score
    11. Detect dead code (if requested)
    12. Render interactive graph
    13. Write JSON output (if requested)
    """
    root = config.project_root.resolve()

    # ── Phase 1: Discovery ───────────────────────────────────────────
    logger.info("Phase 1: Discovering Python files...")
    py_files = discover_python_files(config)

    # ── Phase 2: Parse all files ─────────────────────────────────────
    logger.info("Phase 2: Parsing %d files...", len(py_files))
    all_asts: dict[str, ast.Module] = {}
    parse_failures: list[str] = []

    for filepath in py_files:
        module_path = filepath_to_module(filepath, root)
        tree = safe_parse(filepath)
        if tree:
            all_asts[module_path] = tree
        else:
            parse_failures.append(str(filepath))
            logger.warning("Failed to parse: %s", filepath)

    if parse_failures:
        logger.warning("%d files failed to parse", len(parse_failures))

    # ── Phase 3: Extract definitions ─────────────────────────────────
    logger.info("Phase 3: Extracting definitions...")
    symbol_table = SymbolTable()

    for module_path, tree in all_asts.items():
        filepath = _module_to_file(module_path, root)
        if not filepath:
            continue

        functions, classes = extract_definitions(
            filepath=filepath,
            module_path=module_path,
            tree=tree,
            test_dir_patterns=config.test_dir_patterns,
            test_file_patterns=config.test_file_patterns,
        )

        for func in functions:
            symbol_table.register_function(func)
        for cls in classes:
            symbol_table.register_class(cls)

    logger.info("Phase 3 complete: %s", symbol_table.summary())

    # ── Phase 4: Resolve imports ─────────────────────────────────────
    logger.info("Phase 4: Resolving imports...")
    for module_path, tree in all_asts.items():
        aliases = resolve_imports(tree, module_path, root)
        symbol_table.register_imports(module_path, aliases)

    # ── Phase 5: Resolve class bases + compute MRO ───────────────────
    logger.info("Phase 5: Resolving class hierarchy (C3 MRO)...")
    resolve_class_bases(symbol_table, root)
    symbol_table.compute_all_mros()

    # ── Phase 6: Build call graph ────────────────────────────────────
    logger.info("Phase 6: Building call graph...")
    graph, all_edges = build_call_graph(symbol_table, all_asts)

    # ── Phase 7: Trace blast radius ──────────────────────────────────
    if config.target_function:
        logger.info("Phase 7: Tracing blast radius for '%s'...", config.target_function)
        impact = trace_blast_radius(graph, config.target_function, config.max_depth)
    else:
        # No target — create a summary impact for the whole project
        impact = ImpactResult(target="<project>")

    # ── Phase 8: Coverage integration ────────────────────────────────
    coverage_map: dict[str, float] = {}
    if config.coverage_path:
        logger.info("Phase 8: Integrating coverage data...")
        try:
            cov_data = load_coverage_data(config.coverage_path)
            coverage_map = compute_all_function_coverage(
                symbol_table, cov_data, root
            )
            impact.coverage_map = coverage_map
        except (FileNotFoundError, ValueError) as e:
            logger.warning("Coverage integration failed: %s", e)
            impact.warnings.append(f"Coverage data unavailable: {e}")

    # ── Phase 9: Find covering tests ─────────────────────────────────
    if config.target_function:
        logger.info("Phase 9: Finding covering tests...")
        impact.test_functions = find_covering_tests(config.target_function, graph)

    # ── Phase 10: Confidence scoring ─────────────────────────────────
    if config.target_function:
        logger.info("Phase 10: Computing confidence score...")
        impact.confidence_score = compute_confidence(impact, coverage_map, graph)

    # ── Phase 11: Dead code detection ────────────────────────────────
    if config.include_dead_code:
        logger.info("Phase 11: Detecting dead code...")
        impact.dead_code = detect_dead_code(graph)

    # ── Phase 12: Render graph ───────────────────────────────────────
    if config.target_function:
        logger.info("Phase 12: Rendering interactive graph...")
        render_blast_radius_graph(
            graph, impact, coverage_map, config.output_path
        )
    else:
        logger.info("Phase 12: Rendering full project graph...")
        render_full_graph(graph, coverage_map, config.output_path)

    # ── Phase 13: JSON output ────────────────────────────────────────
    if config.json_output_path:
        logger.info("Phase 13: Writing JSON output...")
        _write_json_output(config, impact, coverage_map, graph)

    logger.info("Analysis complete.")
    return impact


def list_functions(config: AnalysisConfig) -> list[dict[str, Any]]:
    """
    List all functions in the project (no blast radius analysis).

    Returns a list of dicts for tabular display.
    """
    root = config.project_root.resolve()
    py_files = discover_python_files(config)
    symbol_table = SymbolTable()

    for filepath in py_files:
        module_path = filepath_to_module(filepath, root)
        tree = safe_parse(filepath)
        if not tree:
            continue

        functions, classes = extract_definitions(
            filepath=filepath,
            module_path=module_path,
            tree=tree,
            test_dir_patterns=config.test_dir_patterns,
            test_file_patterns=config.test_file_patterns,
        )

        for func in functions:
            symbol_table.register_function(func)
        for cls in classes:
            symbol_table.register_class(cls)

    results = []
    for fqn_str, func_info in symbol_table.all_functions():
        results.append({
            "fqn": fqn_str,
            "file": str(func_info.filepath),
            "line": func_info.start_line,
            "type": "test" if func_info.is_test else ("method" if func_info.is_method else "function"),
            "decorators": ", ".join(func_info.decorators) or "—",
        })

    return sorted(results, key=lambda r: r["fqn"])


# ── Internal helpers ─────────────────────────────────────────────────────────


def _module_to_file(module_path: str, project_root: Path) -> Optional[Path]:
    """Reverse-map a module dotpath to a file path."""
    parts = module_path.split(".")
    rel_path = Path(*parts)

    # Try .py
    candidate = project_root / rel_path.with_suffix(".py")
    if candidate.is_file():
        return candidate.resolve()

    # Try __init__.py
    candidate = project_root / rel_path / "__init__.py"
    if candidate.is_file():
        return candidate.resolve()

    return None


def _write_json_output(
    config: AnalysisConfig,
    impact: ImpactResult,
    coverage_map: dict[str, float],
    graph: nx.DiGraph,
) -> None:
    """Write machine-readable JSON output."""
    breakdown = {}
    if config.target_function:
        breakdown = score_breakdown(impact, coverage_map, graph)

    output = {
        "version": "0.1.0",
        "target": impact.target,
        "confidence_score": impact.confidence_score,
        "risk_label": impact.risk_label,
        "direct_callers": impact.direct_callers,
        "direct_caller_count": len(impact.direct_callers),
        "transitive_dependents": impact.transitive_dependents,
        "transitive_dependent_count": len(impact.transitive_dependents),
        "test_functions": impact.test_functions,
        "test_function_count": len(impact.test_functions),
        "depth_map": impact.depth_map,
        "coverage_map": {k: round(v, 3) for k, v in coverage_map.items() if v > 0},
        "score_breakdown": breakdown,
        "unresolved_calls": impact.unresolved_calls,
        "warnings": impact.warnings,
        "dead_code": impact.dead_code,
        "graph_stats": {
            "total_nodes": graph.number_of_nodes(),
            "total_edges": graph.number_of_edges(),
        },
    }

    json_path = config.json_output_path
    if json_path:
        json_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        logger.info("JSON output written to %s", json_path)
