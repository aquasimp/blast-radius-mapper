"""Tests for the extractor module."""

import ast
from pathlib import Path

from blast_radius_mapper.extractor import extract_definitions


class TestExtractDefinitions:
    """Tests for AST-based function/class extraction."""

    def test_extracts_top_level_function(self, tmp_path: Path):
        source = '''
def hello():
    """Say hello."""
    pass
'''
        tree = ast.parse(source)
        funcs, classes = extract_definitions(
            filepath=tmp_path / "mod.py",
            module_path="mod",
            tree=tree,
            test_dir_patterns=["tests"],
            test_file_patterns=["test_*.py"],
        )
        assert len(funcs) == 1
        assert funcs[0].fqn.full == "mod.hello"
        assert funcs[0].docstring == "Say hello."
        assert not funcs[0].is_method

    def test_extracts_class_and_methods(self, tmp_path: Path):
        source = """
class MyClass:
    def __init__(self):
        pass

    def method(self):
        pass

    @staticmethod
    def static_method():
        pass

    @classmethod
    def class_method(cls):
        pass
"""
        tree = ast.parse(source)
        funcs, classes = extract_definitions(
            filepath=tmp_path / "mod.py",
            module_path="mod",
            tree=tree,
            test_dir_patterns=["tests"],
            test_file_patterns=["test_*.py"],
        )

        assert len(classes) == 1
        assert classes[0].fqn.full == "mod.MyClass"

        assert len(funcs) == 4
        fqn_set = {f.fqn.full for f in funcs}
        assert "mod.MyClass.__init__" in fqn_set
        assert "mod.MyClass.method" in fqn_set
        assert "mod.MyClass.static_method" in fqn_set
        assert "mod.MyClass.class_method" in fqn_set

        # Check decorator flags
        for f in funcs:
            if f.fqn.short_name == "static_method":
                assert f.is_static
            if f.fqn.short_name == "class_method":
                assert f.is_classmethod

    def test_extracts_nested_class(self, tmp_path: Path):
        source = """
class Outer:
    class Inner:
        def inner_method(self):
            pass
"""
        tree = ast.parse(source)
        funcs, classes = extract_definitions(
            filepath=tmp_path / "mod.py",
            module_path="mod",
            tree=tree,
            test_dir_patterns=["tests"],
            test_file_patterns=["test_*.py"],
        )

        assert len(classes) == 2
        class_fqns = {c.fqn.full for c in classes}
        assert "mod.Outer" in class_fqns
        assert "mod.Outer.Inner" in class_fqns

        assert len(funcs) == 1
        assert funcs[0].fqn.full == "mod.Outer.Inner.inner_method"
        assert funcs[0].is_method

    def test_detects_test_function(self, tmp_path: Path):
        source = """
def test_something():
    pass

def helper():
    pass
"""
        tree = ast.parse(source)
        funcs, _ = extract_definitions(
            filepath=tmp_path / "test_mod.py",
            module_path="test_mod",
            tree=tree,
            test_dir_patterns=["tests"],
            test_file_patterns=["test_*.py"],
        )

        test_funcs = [f for f in funcs if f.is_test]
        assert len(test_funcs) == 2  # Both are in a test file

    def test_extracts_base_classes(self, tmp_path: Path):
        source = """
class Parent:
    pass

class Child(Parent):
    pass

class MultiChild(Parent, object):
    pass
"""
        tree = ast.parse(source)
        _, classes = extract_definitions(
            filepath=tmp_path / "mod.py",
            module_path="mod",
            tree=tree,
            test_dir_patterns=["tests"],
            test_file_patterns=["test_*.py"],
        )

        assert len(classes) == 3
        child = next(c for c in classes if c.fqn.short_name == "Child")
        assert child.base_names == ["Parent"]

        multi = next(c for c in classes if c.fqn.short_name == "MultiChild")
        assert multi.base_names == ["Parent", "object"]

    def test_extracts_async_function(self, tmp_path: Path):
        source = """
async def async_handler():
    pass
"""
        tree = ast.parse(source)
        funcs, _ = extract_definitions(
            filepath=tmp_path / "mod.py",
            module_path="mod",
            tree=tree,
            test_dir_patterns=["tests"],
            test_file_patterns=["test_*.py"],
        )

        assert len(funcs) == 1
        assert funcs[0].fqn.full == "mod.async_handler"

    def test_extracts_decorators(self, tmp_path: Path):
        source = """
import functools

@functools.lru_cache
def cached():
    pass

@property
def prop(self):
    pass
"""
        tree = ast.parse(source)
        funcs, _ = extract_definitions(
            filepath=tmp_path / "mod.py",
            module_path="mod",
            tree=tree,
            test_dir_patterns=["tests"],
            test_file_patterns=["test_*.py"],
        )

        cached = next(f for f in funcs if f.fqn.short_name == "cached")
        assert "functools.lru_cache" in cached.decorators

        prop = next(f for f in funcs if f.fqn.short_name == "prop")
        assert prop.is_property
