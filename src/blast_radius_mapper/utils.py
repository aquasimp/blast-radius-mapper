"""
Shared utilities for the Blast Radius Mapper.
"""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path


def is_test_file(
    filepath: Path,
    test_dir_patterns: list[str],
    test_file_patterns: list[str],
) -> bool:
    """
    Determine if a file is a test file based on its path and name.

    Checks:
    1. File name matches test_file_patterns (e.g. ``test_*.py``)
    2. An *immediate* parent directory matches test_dir_patterns (e.g. ``tests/``)

    Note: Only checks the file's own parent directory name, not the entire
    ancestor chain.  This avoids false positives when the project root
    itself lives under a ``tests/`` directory.
    """
    name = filepath.name
    for pattern in test_file_patterns:
        if fnmatch.fnmatch(name, pattern):
            return True

    # Only check the immediate parent directory name
    parent_name = filepath.parent.name.lower()
    return parent_name in [p.lower() for p in test_dir_patterns]


def is_test_function(name: str) -> bool:
    """Return True if the function name follows test naming conventions."""
    return name.startswith("test_") or name.startswith("test")


def decorator_name(node: ast.expr) -> str:
    """
    Extract a human-readable name from a decorator AST node.

    Handles:
    - ``@staticmethod``                → ``"staticmethod"``
    - ``@functools.lru_cache``         → ``"functools.lru_cache"``
    - ``@app.route("/path")``          → ``"app.route"``
    - ``@decorator_factory(arg)``      → ``"decorator_factory"``
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = _attribute_chain(node)
        return ".".join(parts)
    if isinstance(node, ast.Call):
        return decorator_name(node.func)
    # Fallback: dump the AST for unknown patterns
    return ast.dump(node)


def _attribute_chain(node: ast.Attribute) -> list[str]:
    """Recursively collect attribute chain: ``a.b.c`` → ``["a", "b", "c"]``."""
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    parts.reverse()
    return parts


def collect_attribute_chain(node: ast.expr) -> list[str] | None:
    """
    Given an AST expression, collect the dotted attribute chain.

    ``a.b.c`` → ``["a", "b", "c"]``
    ``self.method`` → ``["self", "method"]``

    Returns None if the expression isn't a simple attribute chain.
    """
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        parts.reverse()
        return parts
    return None


def safe_parse(filepath: Path) -> ast.Module | None:
    """
    Parse a Python file, returning None on failure instead of raising.

    Tries UTF-8 first, falls back to latin-1 (which never raises).
    """
    for encoding in ("utf-8", "latin-1"):
        try:
            source = filepath.read_text(encoding=encoding)
            return ast.parse(source, filename=str(filepath))
        except SyntaxError:
            return None
        except (UnicodeDecodeError, ValueError):
            continue
    return None
