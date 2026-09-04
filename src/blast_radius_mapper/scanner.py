"""
File discovery and module path computation.

Scans a project directory for Python files, respects exclusion patterns,
and converts filesystem paths to Python module dotpaths.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from blast_radius_mapper.logging_config import get_logger
from blast_radius_mapper.models import AnalysisConfig

logger = get_logger("scanner")


def discover_python_files(config: AnalysisConfig) -> list[Path]:
    """
    Recursively find all ``.py`` files under the project root.

    Excludes directories matching ``config.exclude_patterns`` (e.g.
    ``__pycache__``, ``.venv``, ``node_modules``).

    Returns:
        Sorted list of absolute paths to Python files.
    """
    root = config.project_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Project root not found: {root}")

    py_files: list[Path] = []

    for path in root.rglob("*.py"):
        if _is_excluded(path, root, config.exclude_patterns):
            continue
        py_files.append(path.resolve())

    py_files.sort()
    logger.info("Discovered %d Python files in %s", len(py_files), root)
    return py_files


def _is_excluded(path: Path, root: Path, exclude_patterns: list[str]) -> bool:
    """Check if any component of the relative path matches an exclusion pattern."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True

    for part in rel.parts:
        for pattern in exclude_patterns:
            if fnmatch.fnmatch(part, pattern):
                return True
    return False


def filepath_to_module(filepath: Path, project_root: Path) -> str:
    """
    Convert a filesystem path to a Python module dotpath.

    Rules:
    1. Path is made relative to ``project_root``
    2. ``.py`` extension is stripped
    3. If filename is ``__init__.py``, use parent directory path
    4. Path separators become dots

    Examples::

        filepath_to_module(Path("myproject/utils/helpers.py"), Path("."))
        # → "myproject.utils.helpers"

        filepath_to_module(Path("myproject/utils/__init__.py"), Path("."))
        # → "myproject.utils"

    Raises:
        ValueError: If the file is not under ``project_root`` or not a ``.py`` file.
    """
    filepath = filepath.resolve()
    project_root = project_root.resolve()

    try:
        rel = filepath.relative_to(project_root)
    except ValueError:
        raise ValueError(f"File {filepath} is not under project root {project_root}") from None

    parts = list(rel.parts)

    if not parts or not parts[-1].endswith(".py"):
        raise ValueError(f"Not a Python file: {filepath}")

    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]  # Strip .py

    if not parts:
        # __init__.py at project root — edge case
        return "__init__"

    return ".".join(parts)


def find_project_packages(project_root: Path, exclude_patterns: list[str]) -> list[str]:
    """
    Identify top-level Python packages under the project root.

    A directory is a package if it contains ``__init__.py``.
    Namespace packages (no ``__init__.py``) are also detected by the
    presence of ``.py`` files.

    Returns:
        List of top-level package names (e.g. ``["myproject", "tests"]``).
    """
    root = project_root.resolve()
    packages: list[str] = []

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if _is_excluded(child, root, exclude_patterns):
            continue

        # Regular package
        if (child / "__init__.py").exists():
            packages.append(child.name)
            continue

        # Namespace package — has .py files but no __init__.py
        has_py = any(child.rglob("*.py"))
        if has_py:
            packages.append(child.name)
            logger.debug("Detected namespace package: %s", child.name)

    logger.info("Found %d top-level packages: %s", len(packages), packages)
    return packages
