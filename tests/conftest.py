"""Shared test fixtures for blast_radius_mapper tests."""

from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to the test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def simple_project_dir(fixtures_dir: Path) -> Path:
    """Path to the simple_project test fixture."""
    return fixtures_dir / "simple_project"
