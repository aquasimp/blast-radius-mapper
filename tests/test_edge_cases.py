"""Tests for advanced Python static analysis edge cases: deep MRO, star imports, closures."""

from __future__ import annotations

import ast
from pathlib import Path

from blast_radius_mapper.extractor import extract_definitions
from blast_radius_mapper.models import FQN, ClassInfo, FunctionInfo
from blast_radius_mapper.resolver import resolve_imports
from blast_radius_mapper.symbol_table import SymbolTable
from blast_radius_mapper.utils import safe_parse


class TestAdvancedEdgeCases:
    """Test challenging Python syntax patterns and inheritance topologies."""

    def test_diamond_c3_linearization_order(self):
        """Classic diamond: D(B, C), B(A), C(A). Resolution order must be D -> B -> C -> A."""
        table = SymbolTable()

        # Classes
        cls_a = ClassInfo(
            fqn=FQN("pkg", "A"),
            filepath=Path("pkg.py"),
            start_line=1,
            end_line=3,
        )
        cls_b = ClassInfo(
            fqn=FQN("pkg", "B"),
            filepath=Path("pkg.py"),
            start_line=4,
            end_line=6,
            resolved_bases=[FQN("pkg", "A")],
        )
        cls_c = ClassInfo(
            fqn=FQN("pkg", "C"),
            filepath=Path("pkg.py"),
            start_line=7,
            end_line=9,
            resolved_bases=[FQN("pkg", "A")],
        )
        cls_d = ClassInfo(
            fqn=FQN("pkg", "D"),
            filepath=Path("pkg.py"),
            start_line=10,
            end_line=12,
            resolved_bases=[FQN("pkg", "B"), FQN("pkg", "C")],
        )

        table.register_class(cls_a)
        table.register_class(cls_b)
        table.register_class(cls_c)
        table.register_class(cls_d)

        # Methods
        m_a = FunctionInfo(
            fqn=FQN("pkg", "A.ping"),
            filepath=Path("pkg.py"),
            start_line=2,
            end_line=3,
            is_method=True,
            class_fqn=FQN("pkg", "A"),
        )
        m_c = FunctionInfo(
            fqn=FQN("pkg", "C.ping"),
            filepath=Path("pkg.py"),
            start_line=8,
            end_line=9,
            is_method=True,
            class_fqn=FQN("pkg", "C"),
        )

        table.register_function(m_a)
        table.register_function(m_c)

        table.compute_all_mros()

        # Resolving 'ping' on D: B does not define ping, C defines ping -> pkg.C.ping
        resolved = table.resolve_method_via_mro("pkg.D", "ping")
        assert resolved == "pkg.C.ping"

    def test_star_import_and_all_parsing(self, tmp_path: Path):
        """Verify star import parses __all__ exported symbols."""
        dep_file = tmp_path / "other_pkg.py"
        dep_code = '__all__ = ["exported_fn"]\ndef exported_fn(): pass\n'
        dep_file.write_text(dep_code, encoding="utf-8")

        source = "from other_pkg import *\n"
        tree = ast.parse(source)
        aliases = resolve_imports(tree, "my_mod", tmp_path)
        assert "exported_fn" in aliases
        assert aliases["exported_fn"] == "other_pkg.exported_fn"

    def test_nested_closures_and_inner_functions(self, tmp_path: Path):
        """Verify inner helper functions nested inside an outer function."""
        source = """
def outer(x: int):
    def inner_helper(y: int):
        return x + y
    return inner_helper(10)
"""
        tree = ast.parse(source)
        funcs, classes = extract_definitions(tmp_path / "mod.py", "pkg.mod", tree, [], [])

        fqn_names = [f.fqn.qualname for f in funcs]
        assert "outer" in fqn_names
        assert "outer.inner_helper" in fqn_names

    def test_safe_parse_invalid_syntax_returns_none(self, tmp_path: Path):
        """Malformed file should return None rather than raising an uncaught exception."""
        bad_file = tmp_path / "broken.py"
        bad_file.write_text("def broken_syntax(::: this is invalid", encoding="utf-8")

        result = safe_parse(bad_file)
        assert result is None
