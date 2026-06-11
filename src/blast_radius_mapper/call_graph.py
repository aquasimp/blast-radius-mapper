"""
Call graph construction from AST analysis.

Walks function bodies to find all ``ast.Call`` nodes, resolves each callee
to a fully qualified name using the symbol table and import aliases, and
builds a NetworkX DiGraph of caller → callee edges.
"""

from __future__ import annotations

import ast
from typing import Optional

import networkx as nx

from blast_radius_mapper.logging_config import get_logger
from blast_radius_mapper.models import CallEdge, CallType, FQN, FunctionInfo
from blast_radius_mapper.symbol_table import SymbolTable
from blast_radius_mapper.utils import collect_attribute_chain

logger = get_logger("call_graph")


# ── Public API ───────────────────────────────────────────────────────────────


def build_call_graph(
    symbol_table: SymbolTable,
    all_asts: dict[str, ast.Module],
) -> tuple[nx.DiGraph, list[CallEdge]]:
    """
    Build the full project call graph.

    Args:
        symbol_table: Populated symbol table with all functions, classes,
            and import aliases registered.
        all_asts: Mapping of module_path → parsed AST tree.

    Returns:
        Tuple of (networkx DiGraph, list of all edges including unresolved).
    """
    all_edges: list[CallEdge] = []
    unresolved_count = 0

    for module_path, tree in all_asts.items():
        import_aliases = symbol_table.get_imports(module_path)
        func_nodes = _iter_function_nodes(tree, module_path)

        for qualname, func_node in func_nodes:
            caller_fqn = FQN(module=module_path, qualname=qualname)

            if not symbol_table.has_function(caller_fqn.full):
                continue  # Skip nodes we don't have info for

            caller_info = symbol_table.get_function(caller_fqn.full)

            edges = _extract_calls_from_function(
                func_node=func_node,
                caller_fqn=caller_fqn,
                caller_info=caller_info,
                module_path=module_path,
                import_aliases=import_aliases,
                symbol_table=symbol_table,
            )
            all_edges.extend(edges)
            unresolved_count += sum(1 for e in edges if e.call_type == CallType.UNRESOLVED)

    logger.info(
        "Extracted %d call edges (%d unresolved)",
        len(all_edges),
        unresolved_count,
    )

    # Build NetworkX graph
    graph = _build_nx_graph(symbol_table, all_edges)

    logger.info(
        "Call graph: %d nodes, %d edges",
        graph.number_of_nodes(),
        graph.number_of_edges(),
    )

    return graph, all_edges


# ── Function node iteration ─────────────────────────────────────────────────


def _iter_function_nodes(
    tree: ast.Module, module_path: str
) -> list[tuple[str, ast.FunctionDef]]:
    """
    Iterate all function/method definitions in an AST, yielding
    (qualname, node) pairs.

    Uses a scope stack to compute correct qualnames for nested definitions.
    """
    results: list[tuple[str, ast.FunctionDef]] = []
    _walk_for_functions(tree, [], results)
    return results


def _walk_for_functions(
    node: ast.AST,
    scope_stack: list[str],
    results: list[tuple[str, ast.FunctionDef]],
) -> None:
    """Recursive walker that collects function nodes with their qualnames."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            scope_stack.append(child.name)
            _walk_for_functions(child, scope_stack, results)
            scope_stack.pop()

        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = ".".join(scope_stack + [child.name])
            results.append((qualname, child))

            scope_stack.append(child.name)
            _walk_for_functions(child, scope_stack, results)
            scope_stack.pop()


# ── Call extraction from a single function ───────────────────────────────────


def _extract_calls_from_function(
    func_node: ast.FunctionDef,
    caller_fqn: FQN,
    caller_info: Optional[FunctionInfo],
    module_path: str,
    import_aliases: dict[str, str],
    symbol_table: SymbolTable,
) -> list[CallEdge]:
    """
    Extract all function/method calls from a single function body.

    Walks the AST of the function and resolves each ``ast.Call`` node.
    """
    edges: list[CallEdge] = []

    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue

        callee_fqn, call_type = _resolve_call(
            call_node=node,
            caller_info=caller_info,
            module_path=module_path,
            import_aliases=import_aliases,
            symbol_table=symbol_table,
        )

        if callee_fqn and call_type != CallType.UNRESOLVED:
            edges.append(CallEdge(
                caller=caller_fqn,
                callee=callee_fqn,
                call_type=call_type,
                call_site_line=node.lineno,
            ))
        else:
            # Record unresolved for diagnostics
            unresolved_label = _call_label(node)
            edges.append(CallEdge(
                caller=caller_fqn,
                callee=FQN("", f"UNRESOLVED:{unresolved_label}"),
                call_type=CallType.UNRESOLVED,
                call_site_line=node.lineno,
                confidence=0.0,
            ))

    return edges


# ── Call resolution ──────────────────────────────────────────────────────────


def _resolve_call(
    call_node: ast.Call,
    caller_info: Optional[FunctionInfo],
    module_path: str,
    import_aliases: dict[str, str],
    symbol_table: SymbolTable,
) -> tuple[Optional[FQN], CallType]:
    """
    Resolve an ``ast.Call`` node to a callee FQN.

    Dispatches based on the call pattern:
    - ``ast.Name``: simple call like ``foo()``
    - ``ast.Attribute``: dotted call like ``obj.method()``
    - Other: unresolvable (e.g. lambda calls, subscript calls)
    """
    func = call_node.func

    if isinstance(func, ast.Name):
        return _resolve_simple_call(
            func.id, module_path, import_aliases, symbol_table
        )

    if isinstance(func, ast.Attribute):
        return _resolve_attribute_call(
            func, caller_info, module_path, import_aliases, symbol_table
        )

    return None, CallType.UNRESOLVED


def _resolve_simple_call(
    name: str,
    module_path: str,
    import_aliases: dict[str, str],
    symbol_table: SymbolTable,
) -> tuple[Optional[FQN], CallType]:
    """
    Resolve a simple call: ``foo()``.

    Strategy:
    1. Check if ``name`` is in import aliases → resolve to imported function
    2. Check if ``name`` is defined in the same module → local function
    3. Check if ``name`` is a class name → constructor call (→ ``__init__``)
    4. Check by short name (ambiguous fallback)
    """
    # 1. Import alias
    if name in import_aliases:
        resolved = import_aliases[name]

        # Is it a function?
        if symbol_table.has_function(resolved):
            func = symbol_table.get_function(resolved)
            if func:
                return func.fqn, CallType.DIRECT

        # Is it a class? → constructor
        if symbol_table.has_class(resolved):
            cls = symbol_table.get_class(resolved)
            if cls:
                init_fqn = f"{cls.fqn.full}.__init__"
                if symbol_table.has_function(init_fqn):
                    init_func = symbol_table.get_function(init_fqn)
                    if init_func:
                        return init_func.fqn, CallType.CONSTRUCTOR
                # Class exists but no __init__ — still a constructor
                return cls.fqn, CallType.CONSTRUCTOR

    # 2. Same-module function
    local_fqn = f"{module_path}.{name}"
    if symbol_table.has_function(local_fqn):
        func = symbol_table.get_function(local_fqn)
        if func:
            return func.fqn, CallType.DIRECT

    # 3. Same-module class → constructor
    if symbol_table.has_class(local_fqn):
        cls = symbol_table.get_class(local_fqn)
        if cls:
            init_fqn = f"{local_fqn}.__init__"
            if symbol_table.has_function(init_fqn):
                init_func = symbol_table.get_function(init_fqn)
                if init_func:
                    return init_func.fqn, CallType.CONSTRUCTOR
            return cls.fqn, CallType.CONSTRUCTOR

    # 4. Short name fallback (may be ambiguous)
    candidates = symbol_table.functions_by_short_name(name)
    if len(candidates) == 1:
        return candidates[0].fqn, CallType.ALIASED

    return None, CallType.UNRESOLVED


def _resolve_attribute_call(
    func: ast.Attribute,
    caller_info: Optional[FunctionInfo],
    module_path: str,
    import_aliases: dict[str, str],
    symbol_table: SymbolTable,
) -> tuple[Optional[FQN], CallType]:
    """
    Resolve a dotted call: ``obj.method()``, ``module.func()``, ``self.method()``.

    Handles:
    - ``self.method()``     → MRO-based resolution
    - ``cls.method()``      → MRO-based resolution
    - ``super().method()``  → Parent class resolution via MRO
    - ``module.func()``     → Import alias resolution
    - ``Class.method()``    → Direct class method lookup
    """
    chain = collect_attribute_chain(func)
    if not chain:
        return None, CallType.UNRESOLVED

    root = chain[0]
    method_name = chain[-1]

    # ── self.method() ────────────────────────────────────────────────
    if root == "self" and len(chain) == 2 and caller_info and caller_info.class_fqn:
        resolved = symbol_table.resolve_method_via_mro(
            caller_info.class_fqn.full, method_name
        )
        if resolved:
            func_info = symbol_table.get_function(resolved)
            if func_info:
                return func_info.fqn, CallType.SELF_METHOD
        return None, CallType.UNRESOLVED

    # ── cls.method() ─────────────────────────────────────────────────
    if root == "cls" and len(chain) == 2 and caller_info and caller_info.class_fqn:
        resolved = symbol_table.resolve_method_via_mro(
            caller_info.class_fqn.full, method_name
        )
        if resolved:
            func_info = symbol_table.get_function(resolved)
            if func_info:
                return func_info.fqn, CallType.CLS_METHOD
        return None, CallType.UNRESOLVED

    # ── super().method() ─────────────────────────────────────────────
    if root == "super" and len(chain) == 2:
        return _resolve_super_call(
            method_name, caller_info, symbol_table
        )

    # ── Imported module.function() or module.Class.method() ──────────
    if root in import_aliases:
        resolved_root = import_aliases[root]
        remaining = chain[1:]

        # Try direct function resolution
        full_path = ".".join([resolved_root] + remaining)
        if symbol_table.has_function(full_path):
            func_info = symbol_table.get_function(full_path)
            if func_info:
                return func_info.fqn, CallType.MODULE_FUNCTION

        # Try as Class.method → resolve Class, then method
        if len(remaining) >= 1:
            class_path = ".".join([resolved_root] + remaining[:-1])
            if symbol_table.has_class(class_path):
                method_fqn = f"{class_path}.{remaining[-1]}"
                if symbol_table.has_function(method_fqn):
                    func_info = symbol_table.get_function(method_fqn)
                    if func_info:
                        return func_info.fqn, CallType.MODULE_FUNCTION

        # Try as constructor: module.Class()
        if len(remaining) == 1:
            class_path = f"{resolved_root}.{remaining[0]}"
            if symbol_table.has_class(class_path):
                # This is actually module.Class() — but method_name would be
                # the class name, and the call is Class() which is a constructor
                # Wait — this case is module.something() where something is a class
                init_fqn = f"{class_path}.__init__"
                if symbol_table.has_function(init_fqn):
                    func_info = symbol_table.get_function(init_fqn)
                    if func_info:
                        return func_info.fqn, CallType.CONSTRUCTOR

    # ── Same-module Class.method() ───────────────────────────────────
    if len(chain) == 2:
        class_fqn = f"{module_path}.{root}"
        if symbol_table.has_class(class_fqn):
            method_fqn = f"{class_fqn}.{method_name}"
            if symbol_table.has_function(method_fqn):
                func_info = symbol_table.get_function(method_fqn)
                if func_info:
                    return func_info.fqn, CallType.MODULE_FUNCTION

    return None, CallType.UNRESOLVED


def _resolve_super_call(
    method_name: str,
    caller_info: Optional[FunctionInfo],
    symbol_table: SymbolTable,
) -> tuple[Optional[FQN], CallType]:
    """
    Resolve ``super().method()`` to the parent class method via MRO.

    Uses the caller's class MRO and skips the first entry (the class itself)
    to find the inherited implementation.
    """
    if not caller_info or not caller_info.class_fqn:
        return None, CallType.UNRESOLVED

    cls = symbol_table.get_class(caller_info.class_fqn.full)
    if not cls or not cls.mro:
        return None, CallType.UNRESOLVED

    # Skip the class itself in MRO — super() starts from the next class
    for i, base_fqn in enumerate(cls.mro):
        if i == 0:
            continue  # Skip self
        candidate = f"{base_fqn.full}.{method_name}"
        if symbol_table.has_function(candidate):
            func_info = symbol_table.get_function(candidate)
            if func_info:
                return func_info.fqn, CallType.SUPER_CALL

    return None, CallType.UNRESOLVED


# ── NetworkX graph construction ──────────────────────────────────────────────


def _build_nx_graph(
    symbol_table: SymbolTable,
    edges: list[CallEdge],
) -> nx.DiGraph:
    """
    Build a NetworkX DiGraph from the symbol table and extracted edges.

    Nodes: FQN strings with metadata attributes.
    Edges: caller → callee with call_type, confidence, and line number.
    """
    graph = nx.DiGraph()

    # Add all functions as nodes
    for fqn_str, func_info in symbol_table.all_functions():
        graph.add_node(
            fqn_str,
            is_test=func_info.is_test,
            file=str(func_info.filepath),
            start_line=func_info.start_line,
            end_line=func_info.end_line,
            is_method=func_info.is_method,
            module=func_info.fqn.module,
        )

    # Add resolved edges
    for edge in edges:
        if edge.call_type == CallType.UNRESOLVED:
            continue

        caller_str = edge.caller.full
        callee_str = edge.callee.full

        if graph.has_node(caller_str) and graph.has_node(callee_str):
            # If edge already exists, keep the higher-confidence one
            if graph.has_edge(caller_str, callee_str):
                existing = graph.edges[caller_str, callee_str]
                if edge.confidence > existing.get("confidence", 0):
                    graph.edges[caller_str, callee_str].update(
                        call_type=edge.call_type.value,
                        confidence=edge.confidence,
                        line=edge.call_site_line,
                    )
            else:
                graph.add_edge(
                    caller_str,
                    callee_str,
                    call_type=edge.call_type.value,
                    confidence=edge.confidence,
                    line=edge.call_site_line,
                )

    return graph


# ── Helpers ──────────────────────────────────────────────────────────────────


def _call_label(node: ast.Call) -> str:
    """Generate a human-readable label for an unresolved call."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        chain = collect_attribute_chain(func)
        if chain:
            return ".".join(chain)
        return f"<attr>.{func.attr}"
    return "<complex_call>"
