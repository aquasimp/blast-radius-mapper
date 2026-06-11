"""
Integration with coverage.py JSON output.

Maps line-level coverage data to function-level coverage ratios by
intersecting executed/missing lines with each function's line range.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from blast_radius_mapper.logging_config import get_logger
from blast_radius_mapper.models import FunctionInfo
from blast_radius_mapper.symbol_table import SymbolTable

logger = get_logger("coverage_integrator")


def load_coverage_data(coverage_path: Path) -> dict[str, Any]:
    """
    Load and validate a coverage.py JSON file.

    Expected format (coverage.py >= 7.0)::

        {
          "meta": {"version": "7.x", ...},
          "files": {
            "myproject/utils.py": {
              "executed_lines": [1, 2, 3],
              "missing_lines": [4, 5],
              "summary": {"percent_covered": 60.0}
            }
          }
        }

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file is not valid coverage JSON.
    """
    if not coverage_path.exists():
        raise FileNotFoundError(f"Coverage file not found: {coverage_path}")

    try:
        data = json.loads(coverage_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid coverage JSON: {e}") from e

    if "files" not in data:
        raise ValueError(
            "Coverage JSON missing 'files' key. "
            "Run: coverage json -o coverage.json"
        )

    # Check staleness
    meta = data.get("meta", {})
    timestamp = meta.get("timestamp")
    if timestamp:
        logger.info("Coverage data timestamp: %s", timestamp)

    logger.info("Loaded coverage data for %d files", len(data["files"]))
    return data


def compute_function_coverage(
    func_info: FunctionInfo,
    coverage_data: dict[str, Any],
    project_root: Path,
) -> float:
    """
    Compute the line coverage ratio for a single function.

    Returns:
        Float in [0.0, 1.0] — fraction of executable lines covered.
    """
    # Normalize filepath to match coverage.json keys (forward slashes, relative)
    try:
        rel_path = func_info.filepath.resolve().relative_to(project_root.resolve())
    except ValueError:
        return 0.0

    # coverage.py uses forward slashes on all platforms
    rel_key = str(rel_path).replace("\\", "/")

    file_cov = coverage_data.get("files", {}).get(rel_key)

    if not file_cov:
        # Try with different prefix variations
        # Some coverage configs use the package name, others use relative paths
        for key in coverage_data.get("files", {}):
            normalized_key = key.replace("\\", "/")
            if normalized_key.endswith(rel_key) or rel_key.endswith(normalized_key):
                file_cov = coverage_data["files"][key]
                break

    if not file_cov:
        return 0.0

    executed = set(file_cov.get("executed_lines", []))
    missing = set(file_cov.get("missing_lines", []))

    # All executable lines within the function's line range
    func_range = set(range(func_info.start_line, func_info.end_line + 1))
    executable_in_range = (executed | missing) & func_range
    covered_in_range = executed & func_range

    if not executable_in_range:
        return 0.0

    return len(covered_in_range) / len(executable_in_range)


def compute_all_function_coverage(
    symbol_table: SymbolTable,
    coverage_data: dict[str, Any],
    project_root: Path,
) -> dict[str, float]:
    """
    Compute coverage ratios for all functions in the symbol table.

    Returns:
        Dict mapping FQN string → coverage ratio [0.0, 1.0].
    """
    coverage_map: dict[str, float] = {}

    covered_count = 0
    total_count = 0

    for fqn_str, func_info in symbol_table.all_functions():
        ratio = compute_function_coverage(func_info, coverage_data, project_root)
        coverage_map[fqn_str] = ratio
        total_count += 1
        if ratio > 0.0:
            covered_count += 1

    logger.info(
        "Function coverage: %d/%d functions have >0%% coverage",
        covered_count,
        total_count,
    )

    return coverage_map
