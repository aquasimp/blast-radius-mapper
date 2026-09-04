"""
Global symbol table with multi-index lookups and C3 MRO computation.

The SymbolTable is the central registry of all functions, methods, and classes
discovered in the project.  It supports lookup by FQN string, by module, by
class, by short name, and by dotpath prefix.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

from blast_radius_mapper.logging_config import get_logger
from blast_radius_mapper.models import FQN, ClassInfo, FunctionInfo

logger = get_logger("symbol_table")


class SymbolTable:
    """
    Global registry of all functions/methods/classes in the analyzed project.

    Provides O(1) lookup by FQN string and O(1) lookup by module, class, or
    short name via secondary indices.
    """

    def __init__(self) -> None:
        # Primary index
        self._functions: dict[str, FunctionInfo] = {}
        self._classes: dict[str, ClassInfo] = {}

        # Secondary indices
        self._by_module: dict[str, list[FunctionInfo]] = defaultdict(list)
        self._by_class: dict[str, list[FunctionInfo]] = defaultdict(list)
        self._by_short_name: dict[str, list[FunctionInfo]] = defaultdict(list)

        self._classes_by_module: dict[str, list[ClassInfo]] = defaultdict(list)
        self._classes_by_short_name: dict[str, list[ClassInfo]] = defaultdict(list)

        # Import alias resolution: module_path → {local_name → resolved_fqn_prefix}
        self._import_aliases: dict[str, dict[str, str]] = {}

    # ── Registration ─────────────────────────────────────────────────────

    def register_function(self, func: FunctionInfo) -> None:
        """Register a function/method in the symbol table."""
        key = func.fqn.full
        if key in self._functions:
            # Last-definition-wins (handles conditional redefinitions)
            logger.debug("Overwriting function: %s", key)

        self._functions[key] = func
        self._by_module[func.fqn.module].append(func)
        self._by_short_name[func.fqn.short_name].append(func)

        if func.class_fqn:
            self._by_class[func.class_fqn.full].append(func)

    def register_class(self, cls: ClassInfo) -> None:
        """Register a class in the symbol table."""
        key = cls.fqn.full
        self._classes[key] = cls
        self._classes_by_module[cls.fqn.module].append(cls)
        self._classes_by_short_name[cls.fqn.short_name].append(cls)

    def register_imports(self, module_path: str, aliases: dict[str, str]) -> None:
        """Store resolved import aliases for a module."""
        self._import_aliases[module_path] = aliases

    # ── Function lookups ─────────────────────────────────────────────────

    def has_function(self, fqn_str: str) -> bool:
        return fqn_str in self._functions

    def get_function(self, fqn_str: str) -> FunctionInfo | None:
        return self._functions.get(fqn_str)

    def functions_in_module(self, module_path: str) -> list[FunctionInfo]:
        return self._by_module.get(module_path, [])

    def methods_of_class(self, class_fqn_str: str) -> list[FunctionInfo]:
        return self._by_class.get(class_fqn_str, [])

    def functions_by_short_name(self, name: str) -> list[FunctionInfo]:
        return self._by_short_name.get(name, [])

    def all_functions(self) -> Iterator[tuple[str, FunctionInfo]]:
        yield from self._functions.items()

    @property
    def function_count(self) -> int:
        return len(self._functions)

    # ── Class lookups ────────────────────────────────────────────────────

    def has_class(self, fqn_str: str) -> bool:
        return fqn_str in self._classes

    def get_class(self, fqn_str: str) -> ClassInfo | None:
        return self._classes.get(fqn_str)

    def classes_in_module(self, module_path: str) -> list[ClassInfo]:
        return self._classes_by_module.get(module_path, [])

    def classes_by_short_name(self, name: str) -> list[ClassInfo]:
        return self._classes_by_short_name.get(name, [])

    def all_classes(self) -> Iterator[tuple[str, ClassInfo]]:
        yield from self._classes.items()

    @property
    def class_count(self) -> int:
        return len(self._classes)

    # ── Dotpath resolution ───────────────────────────────────────────────

    def find_by_dotpath(self, dotpath: str) -> str | None:
        """
        Try to resolve a dot-separated path to a known FQN string.

        Checks functions first, then classes (for constructor calls).
        """
        if dotpath in self._functions:
            return dotpath
        if dotpath in self._classes:
            return dotpath
        return None

    def find_class_by_name(
        self, name: str, module_path: str, import_aliases: dict[str, str]
    ) -> ClassInfo | None:
        """
        Resolve a class name using import aliases and module context.

        1. Check if ``name`` is an import alias → resolve to full path
        2. Check if ``name`` is defined in the same module
        3. Check by short name (ambiguous — pick first match + warn)
        """
        # Check import aliases
        if name in import_aliases:
            resolved = import_aliases[name]
            cls = self._classes.get(resolved)
            if cls:
                return cls

        # Check same-module definition
        local_fqn = f"{module_path}.{name}"
        cls = self._classes.get(local_fqn)
        if cls:
            return cls

        # Fallback: short name (may be ambiguous)
        candidates = self._classes_by_short_name.get(name, [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            logger.warning(
                "Ambiguous class name '%s' — %d candidates, picking first",
                name,
                len(candidates),
            )
            return candidates[0]

        return None

    # ── Import alias access ──────────────────────────────────────────────

    def get_imports(self, module_path: str) -> dict[str, str]:
        return self._import_aliases.get(module_path, {})

    # ── C3 MRO computation ───────────────────────────────────────────────

    def compute_all_mros(self) -> None:
        """
        Compute C3 linearization for all registered classes.

        Must be called after all classes and imports are registered,
        and after ``resolved_bases`` has been populated on each ClassInfo.
        """
        computed = 0
        failed = 0
        for fqn_str, cls in self._classes.items():
            try:
                mro = self._c3_linearize(cls, visited=set())
                cls.mro = mro
                computed += 1
            except ValueError as e:
                logger.warning("C3 MRO failed for %s: %s", fqn_str, e)
                # Fallback: use declared order of resolved bases
                cls.mro = [cls.fqn] + cls.resolved_bases
                failed += 1

        logger.info("Computed MRO for %d classes (%d failures)", computed, failed)

    def _c3_linearize(self, cls: ClassInfo, visited: set[str]) -> list[FQN]:
        """
        Compute C3 linearization (the same algorithm Python uses for MRO).

        Algorithm:
        L[C] = C + merge(L[B1], L[B2], ..., [B1, B2, ...])

        Where merge takes the first head that doesn't appear in the tail
        of any other list.
        """
        fqn_str = cls.fqn.full

        if fqn_str in visited:
            raise ValueError(f"Cyclic inheritance detected: {fqn_str}")
        visited.add(fqn_str)

        if not cls.resolved_bases:
            return [cls.fqn]

        # Recursively compute linearizations of bases
        base_linearizations: list[list[FQN]] = []
        for base_fqn in cls.resolved_bases:
            base_cls = self._classes.get(base_fqn.full)
            if base_cls:
                lin = self._c3_linearize(base_cls, visited.copy())
                base_linearizations.append(lin)
            else:
                # External base class — just include it as-is
                base_linearizations.append([base_fqn])

        # Add the direct bases list
        base_linearizations.append(list(cls.resolved_bases))

        # Merge
        result = [cls.fqn]
        result.extend(self._c3_merge(base_linearizations))

        return result

    @staticmethod
    def _c3_merge(linearizations: list[list[FQN]]) -> list[FQN]:
        """
        The merge step of C3 linearization.

        Pick the first head that does not appear in the tail of any
        other linearization.  Remove it from all lists.  Repeat.
        """
        result: list[FQN] = []
        # Work with copies
        lists = [list(lin) for lin in linearizations]

        while True:
            # Remove empty lists
            lists = [lst for lst in lists if lst]
            if not lists:
                break

            # Find a good head
            head: FQN | None = None
            for lst in lists:
                candidate = lst[0]
                # Check candidate is not in the tail of any other list
                in_tail = any(candidate in other[1:] for other in lists)
                if not in_tail:
                    head = candidate
                    break

            if head is None:
                raise ValueError("Cannot compute C3 linearization — inconsistent hierarchy")

            result.append(head)
            # Remove head from all lists
            for lst in lists:
                if lst and lst[0] == head:
                    lst.pop(0)

        return result

    # ── Method resolution via MRO ────────────────────────────────────────

    def resolve_method_via_mro(self, class_fqn_str: str, method_name: str) -> str | None:
        """
        Resolve ``self.method_name()`` by walking the class's MRO.

        Returns the FQN string of the first matching method definition,
        or None if not found.
        """
        cls = self._classes.get(class_fqn_str)
        if not cls:
            return None

        mro = cls.mro if cls.mro else [cls.fqn] + cls.resolved_bases

        for base_fqn in mro:
            candidate = f"{base_fqn.full}.{method_name}"
            if candidate in self._functions:
                return candidate

        return None

    # ── Diagnostics ──────────────────────────────────────────────────────

    def summary(self) -> str:
        """Human-readable summary for CLI output."""
        return (
            f"SymbolTable: {self.function_count} functions, "
            f"{self.class_count} classes, "
            f"{len(self._import_aliases)} modules with imports"
        )
