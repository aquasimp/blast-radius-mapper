"""
Tree-sitter fallback parser for files with syntax errors.

When ``ast.parse()`` fails (e.g. incomplete files, Python 2 syntax),
tree-sitter can still produce a partial parse tree.  This module extracts
function/class definitions from the tree-sitter CST.

This is a **secondary** parser — the primary is always ``ast``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from blast_radius_mapper.logging_config import get_logger
from blast_radius_mapper.models import ClassInfo, FQN, FunctionInfo

logger = get_logger("treesitter_parser")

# Lazy-loaded tree-sitter objects
_ts_parser = None
_ts_language = None


def _get_parser():
    """Lazily initialize the tree-sitter Python parser."""
    global _ts_parser, _ts_language

    if _ts_parser is not None:
        return _ts_parser

    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        _ts_language = Language(tspython.language())
        _ts_parser = Parser(_ts_language)
        logger.debug("Tree-sitter Python parser initialized")
        return _ts_parser
    except ImportError:
        logger.warning(
            "tree-sitter or tree-sitter-python not installed. "
            "Fallback parsing will be unavailable. "
            "Install with: pip install tree-sitter tree-sitter-python"
        )
        return None
    except Exception as e:
        logger.warning("Failed to initialize tree-sitter: %s", e)
        return None


def parse_with_treesitter(
    filepath: Path,
    module_path: str,
) -> tuple[list[FunctionInfo], list[ClassInfo]]:
    """
    Parse a Python file using tree-sitter and extract definitions.

    This is used as a fallback when ``ast.parse()`` fails.
    It extracts function/class names and line ranges, but does NOT
    extract call sites (that still requires ``ast``).

    Returns:
        Tuple of (function_info_list, class_info_list).
        May be empty if tree-sitter is unavailable.
    """
    parser = _get_parser()
    if parser is None:
        return [], []

    try:
        source = filepath.read_bytes()
        tree = parser.parse(source)
    except Exception as e:
        logger.warning("Tree-sitter parse failed for %s: %s", filepath, e)
        return [], []

    functions: list[FunctionInfo] = []
    classes: list[ClassInfo] = []

    _walk_ts_tree(
        tree.root_node,
        module_path=module_path,
        filepath=filepath,
        scope_stack=[],
        parent_is_class=False,
        functions=functions,
        classes=classes,
    )

    logger.debug(
        "Tree-sitter fallback for %s: %d functions, %d classes",
        module_path,
        len(functions),
        len(classes),
    )
    return functions, classes


def _walk_ts_tree(
    node,
    module_path: str,
    filepath: Path,
    scope_stack: list[str],
    parent_is_class: bool,
    functions: list[FunctionInfo],
    classes: list[ClassInfo],
) -> None:
    """Recursively walk a tree-sitter node tree, extracting definitions."""
    for child in node.children:
        if child.type == "class_definition":
            name = _ts_get_name(child)
            if name:
                qualname = ".".join(scope_stack + [name])
                fqn = FQN(module=module_path, qualname=qualname)

                cls_info = ClassInfo(
                    fqn=fqn,
                    filepath=filepath,
                    start_line=child.start_point[0] + 1,  # tree-sitter is 0-indexed
                    end_line=child.end_point[0] + 1,
                )
                classes.append(cls_info)

                scope_stack.append(name)
                _walk_ts_tree(
                    child, module_path, filepath, scope_stack,
                    parent_is_class=True,
                    functions=functions, classes=classes,
                )
                scope_stack.pop()

        elif child.type in ("function_definition", "async_function_definition"):
            name = _ts_get_name(child)
            if name:
                qualname = ".".join(scope_stack + [name])
                fqn = FQN(module=module_path, qualname=qualname)

                func_info = FunctionInfo(
                    fqn=fqn,
                    filepath=filepath,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    is_method=parent_is_class,
                    is_test=name.startswith("test_") or name.startswith("test"),
                )
                functions.append(func_info)

                scope_stack.append(name)
                _walk_ts_tree(
                    child, module_path, filepath, scope_stack,
                    parent_is_class=False,
                    functions=functions, classes=classes,
                )
                scope_stack.pop()

        else:
            # Recurse into other compound statements
            if child.child_count > 0:
                _walk_ts_tree(
                    child, module_path, filepath, scope_stack,
                    parent_is_class=parent_is_class,
                    functions=functions, classes=classes,
                )


def _ts_get_name(node) -> Optional[str]:
    """Extract the name identifier from a function/class definition node."""
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None
