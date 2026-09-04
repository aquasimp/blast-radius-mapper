"""
AST-based extraction of function, method, and class definitions.

Walks the AST of each file with a scope stack to compute correct qualnames
for nested classes and functions.  Produces FunctionInfo and ClassInfo records.
"""

from __future__ import annotations

import ast
from pathlib import Path

from blast_radius_mapper.logging_config import get_logger
from blast_radius_mapper.models import FQN, ClassInfo, FunctionInfo
from blast_radius_mapper.utils import decorator_name, is_test_file, is_test_function

logger = get_logger("extractor")


# ── Public API ───────────────────────────────────────────────────────────────


def extract_definitions(
    filepath: Path,
    module_path: str,
    tree: ast.Module,
    test_dir_patterns: list[str],
    test_file_patterns: list[str],
) -> tuple[list[FunctionInfo], list[ClassInfo]]:
    """
    Extract all function and class definitions from a parsed AST.

    Args:
        filepath: Absolute path to the source file.
        module_path: Dot-separated module path (e.g. ``"myproject.utils"``).
        tree: Parsed AST module.
        test_dir_patterns: Directory names that indicate test code.
        test_file_patterns: File glob patterns that indicate test files.

    Returns:
        Tuple of (function_info_list, class_info_list).
    """
    file_is_test = is_test_file(filepath, test_dir_patterns, test_file_patterns)

    functions: list[FunctionInfo] = []
    classes: list[ClassInfo] = []

    _walk_scope(
        node=tree,
        module_path=module_path,
        filepath=filepath,
        scope_stack=[],
        parent_is_class=False,
        file_is_test=file_is_test,
        functions=functions,
        classes=classes,
    )

    logger.debug(
        "%s: extracted %d functions, %d classes",
        module_path,
        len(functions),
        len(classes),
    )
    return functions, classes


# ── Internal walker ──────────────────────────────────────────────────────────


def _walk_scope(
    node: ast.AST,
    module_path: str,
    filepath: Path,
    scope_stack: list[str],
    parent_is_class: bool,
    file_is_test: bool,
    functions: list[FunctionInfo],
    classes: list[ClassInfo],
) -> None:
    """
    Recursively walk AST nodes, maintaining a scope stack for qualname computation.

    The scope stack tracks nesting: ``["MyClass", "InnerClass"]`` → qualname
    prefix is ``"MyClass.InnerClass"``.
    """
    for child in ast.iter_child_nodes(node):
        # ── Class definition ────────────────────────────────────────────
        if isinstance(child, ast.ClassDef):
            class_qualname = ".".join(scope_stack + [child.name])
            class_fqn = FQN(module=module_path, qualname=class_qualname)

            class_info = ClassInfo(
                fqn=class_fqn,
                filepath=filepath,
                start_line=child.lineno,
                end_line=child.end_lineno or child.lineno,
                base_names=_extract_base_names(child),
                decorators=[decorator_name(d) for d in child.decorator_list],
            )
            classes.append(class_info)

            # Recurse into class body
            scope_stack.append(child.name)
            _walk_scope(
                node=child,
                module_path=module_path,
                filepath=filepath,
                scope_stack=scope_stack,
                parent_is_class=True,
                file_is_test=file_is_test,
                functions=functions,
                classes=classes,
            )
            scope_stack.pop()

        # ── Function / method definition ────────────────────────────────
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = ".".join(scope_stack + [child.name])
            fqn = FQN(module=module_path, qualname=qualname)

            dec_names = [decorator_name(d) for d in child.decorator_list]

            # Determine if this is a method
            is_method = parent_is_class

            # Build class FQN reference if this is a method
            parent_class_fqn: FQN | None = None
            if is_method and scope_stack:
                class_qualname = ".".join(scope_stack)
                parent_class_fqn = FQN(module=module_path, qualname=class_qualname)

            func_info = FunctionInfo(
                fqn=fqn,
                filepath=filepath,
                start_line=child.lineno,
                end_line=child.end_lineno or child.lineno,
                decorators=dec_names,
                is_method=is_method,
                is_static="staticmethod" in dec_names,
                is_classmethod="classmethod" in dec_names,
                is_property="property" in dec_names,
                is_test=file_is_test or is_test_function(child.name),
                class_fqn=parent_class_fqn,
                parameters=[arg.arg for arg in child.args.args],
                docstring=ast.get_docstring(child),
            )
            functions.append(func_info)

            # If this is a method, register it on the class
            if parent_class_fqn:
                _register_method_on_class(classes, parent_class_fqn, fqn)

            # Recurse into nested functions (they are real, but rare)
            scope_stack.append(child.name)
            _walk_scope(
                node=child,
                module_path=module_path,
                filepath=filepath,
                scope_stack=scope_stack,
                parent_is_class=False,  # Nested functions are not methods
                file_is_test=file_is_test,
                functions=functions,
                classes=classes,
            )
            scope_stack.pop()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _extract_base_names(class_node: ast.ClassDef) -> list[str]:
    """
    Extract base class names as strings from a ClassDef node.

    Handles:
    - ``class Foo(Bar):``              → ``["Bar"]``
    - ``class Foo(module.Bar):``       → ``["module.Bar"]``
    - ``class Foo(Bar, Baz):``         → ``["Bar", "Baz"]``
    - ``class Foo(metaclass=Meta):``   → ``[]``  (metaclass is not a base)
    """
    bases: list[str] = []
    for base in class_node.bases:
        if isinstance(base, ast.Name):
            bases.append(base.id)
        elif isinstance(base, ast.Attribute):
            # Collect dotted name
            parts: list[str] = []
            current: ast.expr = base
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            parts.reverse()
            bases.append(".".join(parts))
        elif isinstance(base, ast.Subscript):
            # Generic base: class Foo(Generic[T]) — extract the base name only
            if isinstance(base.value, ast.Name):
                bases.append(base.value.id)
            elif isinstance(base.value, ast.Attribute):
                # e.g. typing.Generic[T]
                parts_sub: list[str] = []
                current_sub: ast.expr = base.value
                while isinstance(current_sub, ast.Attribute):
                    parts_sub.append(current_sub.attr)
                    current_sub = current_sub.value
                if isinstance(current_sub, ast.Name):
                    parts_sub.append(current_sub.id)
                parts_sub.reverse()
                bases.append(".".join(parts_sub))
        # Silently skip bases we can't parse (e.g. function call as base)
    return bases


def _register_method_on_class(classes: list[ClassInfo], class_fqn: FQN, method_fqn: FQN) -> None:
    """Add a method FQN to its parent ClassInfo's methods list."""
    for cls in reversed(classes):  # Most recent class is most likely the match
        if cls.fqn == class_fqn:
            cls.methods.append(method_fqn)
            return
