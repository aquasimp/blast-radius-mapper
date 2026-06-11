"""Tests for the scanner module."""

from pathlib import Path

import pytest

from blast_radius_mapper.models import AnalysisConfig
from blast_radius_mapper.scanner import discover_python_files, filepath_to_module


class TestFilepathToModule:
    """Tests for filepath → module dotpath conversion."""

    def test_simple_file(self, tmp_path: Path):
        (tmp_path / "myproject").mkdir()
        f = tmp_path / "myproject" / "utils.py"
        f.write_text("pass")
        assert filepath_to_module(f, tmp_path) == "myproject.utils"

    def test_nested_file(self, tmp_path: Path):
        (tmp_path / "myproject" / "sub" / "deep").mkdir(parents=True)
        f = tmp_path / "myproject" / "sub" / "deep" / "mod.py"
        f.write_text("pass")
        assert filepath_to_module(f, tmp_path) == "myproject.sub.deep.mod"

    def test_init_file(self, tmp_path: Path):
        (tmp_path / "myproject").mkdir()
        f = tmp_path / "myproject" / "__init__.py"
        f.write_text("pass")
        assert filepath_to_module(f, tmp_path) == "myproject"

    def test_not_python_file(self, tmp_path: Path):
        f = tmp_path / "readme.md"
        f.write_text("hello")
        with pytest.raises(ValueError, match="Not a Python file"):
            filepath_to_module(f, tmp_path)


class TestDiscoverPythonFiles:
    """Tests for file discovery."""

    def test_finds_py_files(self, tmp_path: Path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("")
        (tmp_path / "pkg" / "mod.py").write_text("pass")
        (tmp_path / "pkg" / "readme.md").write_text("not python")

        config = AnalysisConfig(project_root=tmp_path)
        files = discover_python_files(config)

        py_names = [f.name for f in files]
        assert "__init__.py" in py_names
        assert "mod.py" in py_names
        assert "readme.md" not in py_names

    def test_excludes_pycache(self, tmp_path: Path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "mod.cpython-310.pyc").write_text("")
        (tmp_path / "real.py").write_text("pass")

        config = AnalysisConfig(project_root=tmp_path)
        files = discover_python_files(config)

        assert len(files) == 1
        assert files[0].name == "real.py"

    def test_excludes_venv(self, tmp_path: Path):
        (tmp_path / ".venv" / "lib").mkdir(parents=True)
        (tmp_path / ".venv" / "lib" / "site.py").write_text("pass")
        (tmp_path / "app.py").write_text("pass")

        config = AnalysisConfig(project_root=tmp_path)
        files = discover_python_files(config)

        assert len(files) == 1
        assert files[0].name == "app.py"
