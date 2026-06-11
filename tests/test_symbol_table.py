"""Tests for the symbol table and C3 MRO computation."""

from blast_radius_mapper.models import ClassInfo, FQN, FunctionInfo
from blast_radius_mapper.symbol_table import SymbolTable
from pathlib import Path


class TestSymbolTable:
    """Tests for symbol table registration and lookup."""

    def _make_func(self, module: str, qualname: str, **kwargs) -> FunctionInfo:
        fqn = FQN(module=module, qualname=qualname)
        return FunctionInfo(
            fqn=fqn,
            filepath=Path(f"{module.replace('.', '/')}.py"),
            start_line=1,
            end_line=10,
            **kwargs,
        )

    def _make_class(self, module: str, qualname: str, bases=None) -> ClassInfo:
        fqn = FQN(module=module, qualname=qualname)
        return ClassInfo(
            fqn=fqn,
            filepath=Path(f"{module.replace('.', '/')}.py"),
            start_line=1,
            end_line=50,
            base_names=bases or [],
        )

    def test_register_and_lookup(self):
        st = SymbolTable()
        func = self._make_func("pkg.mod", "foo")
        st.register_function(func)

        assert st.has_function("pkg.mod.foo")
        assert st.get_function("pkg.mod.foo") is func

    def test_lookup_by_module(self):
        st = SymbolTable()
        st.register_function(self._make_func("pkg.mod", "foo"))
        st.register_function(self._make_func("pkg.mod", "bar"))
        st.register_function(self._make_func("pkg.other", "baz"))

        mod_funcs = st.functions_in_module("pkg.mod")
        assert len(mod_funcs) == 2

    def test_lookup_by_short_name(self):
        st = SymbolTable()
        st.register_function(self._make_func("pkg.a", "foo"))
        st.register_function(self._make_func("pkg.b", "foo"))

        candidates = st.functions_by_short_name("foo")
        assert len(candidates) == 2

    def test_class_registration(self):
        st = SymbolTable()
        cls = self._make_class("pkg.mod", "MyClass")
        st.register_class(cls)

        assert st.has_class("pkg.mod.MyClass")
        assert st.get_class("pkg.mod.MyClass") is cls


class TestC3MRO:
    """Tests for C3 linearization (MRO computation)."""

    def _make_class(self, module: str, qualname: str, bases=None, resolved_bases=None) -> ClassInfo:
        fqn = FQN(module=module, qualname=qualname)
        return ClassInfo(
            fqn=fqn,
            filepath=Path("test.py"),
            start_line=1,
            end_line=10,
            base_names=bases or [],
            resolved_bases=resolved_bases or [],
        )

    def _make_func(self, module: str, qualname: str) -> FunctionInfo:
        fqn = FQN(module=module, qualname=qualname)
        return FunctionInfo(
            fqn=fqn,
            filepath=Path("test.py"),
            start_line=1,
            end_line=5,
            is_method=True,
            class_fqn=FQN(module=module, qualname=qualname.rsplit(".", 1)[0]),
        )

    def test_simple_hierarchy(self):
        st = SymbolTable()

        base = self._make_class("m", "Base")
        child = self._make_class("m", "Child", resolved_bases=[base.fqn])

        st.register_class(base)
        st.register_class(child)
        st.compute_all_mros()

        assert child.mro == [child.fqn, base.fqn]

    def test_diamond_inheritance(self):
        st = SymbolTable()

        a = self._make_class("m", "A")
        b = self._make_class("m", "B", resolved_bases=[a.fqn])
        c = self._make_class("m", "C", resolved_bases=[a.fqn])
        d = self._make_class("m", "D", resolved_bases=[b.fqn, c.fqn])

        for cls in [a, b, c, d]:
            st.register_class(cls)

        st.compute_all_mros()

        # Python's C3 MRO for D(B, C) where B(A), C(A):
        # D → B → C → A
        mro_names = [f.qualname for f in d.mro]
        assert mro_names == ["D", "B", "C", "A"]

    def test_method_resolution_via_mro(self):
        st = SymbolTable()

        base = self._make_class("m", "Base")
        child = self._make_class("m", "Child", resolved_bases=[base.fqn])

        st.register_class(base)
        st.register_class(child)

        # Method on base only
        base_method = self._make_func("m", "Base.shared")
        st.register_function(base_method)

        # Method on child
        child_own = self._make_func("m", "Child.own")
        st.register_function(child_own)

        st.compute_all_mros()

        # Child.shared should resolve to Base.shared via MRO
        resolved = st.resolve_method_via_mro("m.Child", "shared")
        assert resolved == "m.Base.shared"

        # Child.own should resolve to Child.own
        resolved = st.resolve_method_via_mro("m.Child", "own")
        assert resolved == "m.Child.own"

        # Missing method returns None
        resolved = st.resolve_method_via_mro("m.Child", "nonexistent")
        assert resolved is None
