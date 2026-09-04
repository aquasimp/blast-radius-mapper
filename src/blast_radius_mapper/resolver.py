"""
Import resolution for all Python import patterns.

Resolves import statements to fully qualified names by combining AST analysis
with the project's file structure.  Handles all 7 import patterns plus star
imports with __all__ expansion.
"""

from __future__ import annotations

import ast
from pathlib import Path

from blast_radius_mapper.logging_config import get_logger
from blast_radius_mapper.models import FQN
from blast_radius_mapper.symbol_table import SymbolTable

logger = get_logger("resolver")


# ── Public API ───────────────────────────────────────────────────────────────


def resolve_imports(
    tree: ast.Module,
    module_path: str,
    project_root: Path,
) -> dict[str, str]:
    """
    Build a mapping of ``local_name → resolved_dotpath`` for all imports
    in a module.

    Handles:
    1. ``import foo``                → ``{"foo": "foo"}``
    2. ``import foo.bar``            → ``{"foo": "foo"}``
       (``foo.bar`` is accessible as attribute, but ``foo`` is the binding)
    3. ``from foo import bar``       → ``{"bar": "foo.bar"}``
    4. ``from foo import bar as b``  → ``{"b": "foo.bar"}``
    5. ``from . import sibling``     → ``{"sibling": "<resolved>.sibling"}``
    6. ``from ..parent import x``    → ``{"x": "<resolved>.x"}``
    7. ``from foo import *``         → expanded via ``__all__`` or dir

    Returns:
        Dict mapping local names to resolved dotpaths.
    """
    alias_map: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname if alias.asname else alias.name.split(".")[0]
                alias_map[local_name] = alias.name.split(".")[0]
                # Also store the full dotted name if imported as-is
                if not alias.asname:
                    alias_map[alias.name] = alias.name

        elif isinstance(node, ast.ImportFrom):
            base_module = _resolve_import_base(module_path, node, project_root)

            if base_module is None:
                logger.debug(
                    "Could not resolve import base for 'from %s import ...' in %s",
                    node.module or ".",
                    module_path,
                )
                continue

            if node.names and node.names[0].name == "*":
                # Star import — expand
                star_names = _resolve_star_import(base_module, project_root)
                for name in star_names:
                    alias_map[name] = f"{base_module}.{name}"
                logger.debug(
                    "Star import from %s expanded to %d names", base_module, len(star_names)
                )
            else:
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    alias_map[local_name] = f"{base_module}.{alias.name}"

    logger.debug("Module %s: resolved %d import aliases", module_path, len(alias_map))
    return alias_map


def resolve_class_bases(
    symbol_table: SymbolTable,
    project_root: Path,
) -> None:
    """
    Resolve raw base class names to FQNs on all registered ClassInfo objects.

    Must be called after all modules have been parsed and all imports registered.
    """
    resolved_count = 0
    unresolved_count = 0

    for _, cls in symbol_table.all_classes():
        import_aliases = symbol_table.get_imports(cls.fqn.module)
        resolved_bases: list[FQN] = []

        for base_name in cls.base_names:
            resolved = _resolve_base_class(base_name, cls.fqn.module, import_aliases, symbol_table)
            if resolved:
                resolved_bases.append(resolved)
                resolved_count += 1
            else:
                unresolved_count += 1
                logger.debug(
                    "Unresolved base class '%s' for %s",
                    base_name,
                    cls.fqn.full,
                )

        cls.resolved_bases = resolved_bases

    logger.info(
        "Resolved %d base class references (%d unresolved)",
        resolved_count,
        unresolved_count,
    )


# ── Internal: Import base resolution ────────────────────────────────────────


def _resolve_import_base(
    current_module: str,
    node: ast.ImportFrom,
    project_root: Path,
) -> str | None:
    """
    Resolve the base module of a ``from ... import`` statement.

    For absolute imports, returns ``node.module`` directly.
    For relative imports (``level > 0``), computes the target module
    relative to the current module's position.
    """
    if node.level == 0:
        # Absolute import
        return node.module or ""

    # Relative import
    return _resolve_relative_import(
        current_module=current_module,
        level=node.level,
        target=node.module,
        project_root=project_root,
    )


def _resolve_relative_import(
    current_module: str,
    level: int,
    target: str | None,
    project_root: Path,
) -> str | None:
    """
    Resolve a relative import.

    ``level=1, target="sibling"`` in module ``"pkg.sub.mod"``
    → ``"pkg.sub.sibling"``

    ``level=2, target="other"`` in module ``"pkg.sub.mod"``
    → ``"pkg.other"``

    ``level=1, target=None`` in module ``"pkg.sub.mod"``
    → ``"pkg.sub"`` (importing from the package itself)
    """
    parts = current_module.split(".")

    # Go up `level` levels
    # For level=1 in "pkg.sub.mod": strip 1 component → "pkg.sub"
    # For level=2 in "pkg.sub.mod": strip 2 components → "pkg"
    if level > len(parts):
        logger.warning(
            "Relative import level %d exceeds module depth of '%s'",
            level,
            current_module,
        )
        return None

    base_parts = parts[:-level]

    if target:
        base_parts.append(target)

    return ".".join(base_parts) if base_parts else None


# ── Internal: Star import expansion ──────────────────────────────────────────


def _resolve_star_import(module_dotpath: str, project_root: Path) -> list[str]:
    """
    Expand ``from module import *`` by reading the target module's ``__all__``.

    Strategy:
    1. Find the file for the target module
    2. Parse it
    3. Look for ``__all__ = [...]`` assignment
    4. If found, return those names
    5. If not, return all top-level names not starting with ``_``
    """
    filepath = _module_to_filepath(module_dotpath, project_root)
    if filepath is None or not filepath.exists():
        logger.debug("Star import: cannot find file for %s", module_dotpath)
        return []

    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        logger.warning("Star import: cannot parse %s", filepath)
        return []

    # Look for __all__
    all_names = _extract_dunder_all(tree)
    if all_names is not None:
        return all_names

    # Fallback: all top-level definitions not starting with _
    names: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.append(target.id)
    return names


def _extract_dunder_all(tree: ast.Module) -> list[str] | None:
    """
    Extract the value of ``__all__`` if it's a simple list/tuple of strings.

    Returns None if ``__all__`` is not found or is not a simple literal.
    """
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "__all__"
                and isinstance(node.value, (ast.List, ast.Tuple))
            ):
                names: list[str] = []
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        names.append(elt.value)
                return names
    return None


# ── Internal: Base class resolution ──────────────────────────────────────────


def _resolve_base_class(
    base_name: str,
    module_path: str,
    import_aliases: dict[str, str],
    symbol_table: SymbolTable,
) -> FQN | None:
    """
    Resolve a base class name to an FQN.

    Tries:
    1. Import alias (``from foo import Bar`` → ``Bar`` → ``foo.Bar``)
    2. Same-module definition (``Bar`` → ``<module>.Bar``)
    3. Dotted name via import (``mod.Bar`` → resolved ``mod`` + ``.Bar``)
    """
    # Skip known builtins that won't be in our symbol table
    if base_name in (
        "object",
        "type",
        "Exception",
        "BaseException",
        "ValueError",
        "TypeError",
        "RuntimeError",
        "KeyError",
        "AttributeError",
        "NotImplementedError",
        "StopIteration",
        "ABC",
        "Protocol",
    ):
        return None

    # Check if base_name is a dotted path like "module.Class"
    parts = base_name.split(".")
    root = parts[0]

    if len(parts) > 1:
        # Dotted base: resolve root via aliases, then append rest
        if root in import_aliases:
            resolved = import_aliases[root]
            full = ".".join([resolved] + parts[1:])
            if symbol_table.has_class(full):
                cls = symbol_table.get_class(full)
                return cls.fqn if cls else None
    else:
        # Simple name
        if base_name in import_aliases:
            resolved = import_aliases[base_name]
            if symbol_table.has_class(resolved):
                cls = symbol_table.get_class(resolved)
                return cls.fqn if cls else None

        # Same-module
        local_fqn = f"{module_path}.{base_name}"
        if symbol_table.has_class(local_fqn):
            cls = symbol_table.get_class(local_fqn)
            return cls.fqn if cls else None

    return None


# ── Internal: Module → filepath ──────────────────────────────────────────────


def _module_to_filepath(module_dotpath: str, project_root: Path) -> Path | None:
    """
    Convert a module dotpath to a filesystem path.

    Tries in order:
    1. ``<root>/<path>.py``
    2. ``<root>/<path>/__init__.py``
    """
    parts = module_dotpath.split(".")
    rel_path = Path(*parts)

    # Try as .py file
    candidate = project_root / rel_path.with_suffix(".py")
    if candidate.is_file():
        return candidate

    # Try as package
    candidate = project_root / rel_path / "__init__.py"
    if candidate.is_file():
        return candidate

    return None
