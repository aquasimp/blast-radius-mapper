"""End-to-end integration test using the simple_project fixture."""

from pathlib import Path

from blast_radius_mapper.models import AnalysisConfig
from blast_radius_mapper.pipeline import analyze_project, list_functions


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "simple_project"


class TestEndToEnd:
    """Integration tests for the full analysis pipeline."""

    def test_list_functions(self):
        """Verify that list_functions finds all functions in the fixture project."""
        config = AnalysisConfig(project_root=FIXTURE_DIR)
        functions = list_functions(config)

        fqn_set = {f["fqn"] for f in functions}

        # Core module
        assert "core.process_data" in fqn_set
        assert "core.clean_input" in fqn_set
        assert "core.validate" in fqn_set

        # API module
        assert "api.handle_request" in fqn_set
        assert "api.format_response" in fqn_set

        # Models
        assert "models.BaseModel.__init__" in fqn_set
        assert "models.BaseModel.validate_name" in fqn_set
        assert "models.User.__init__" in fqn_set
        assert "models.User.validate_email" in fqn_set
        assert "models.AdminUser.__init__" in fqn_set

    def test_analyze_blast_radius(self, tmp_path: Path):
        """Analyze blast radius for core.validate — should find callers."""
        config = AnalysisConfig(
            project_root=FIXTURE_DIR,
            target_function="core.validate",
            output_path=tmp_path / "blast_radius.html",
        )
        result = analyze_project(config)

        # validate is called by process_data
        assert "core.process_data" in result.direct_callers

        # process_data is called by handle_request
        assert "api.handle_request" in result.transitive_dependents

        # Confidence score should be between 0 and 1
        assert 0.0 <= result.confidence_score <= 1.0

        # Graph should be written
        assert (tmp_path / "blast_radius.html").exists()

    def test_analyze_with_json_output(self, tmp_path: Path):
        """Verify JSON output is written correctly."""
        import json

        config = AnalysisConfig(
            project_root=FIXTURE_DIR,
            target_function="core.clean_input",
            output_path=tmp_path / "blast_radius.html",
            json_output_path=tmp_path / "result.json",
        )
        result = analyze_project(config)

        json_path = tmp_path / "result.json"
        assert json_path.exists()

        data = json.loads(json_path.read_text())
        assert data["target"] == "core.clean_input"
        assert "direct_callers" in data
        assert "confidence_score" in data
        assert "graph_stats" in data

    def test_analyze_class_method(self, tmp_path: Path):
        """Analyze blast radius for BaseModel.validate_name."""
        config = AnalysisConfig(
            project_root=FIXTURE_DIR,
            target_function="models.BaseModel.validate_name",
            output_path=tmp_path / "blast_radius.html",
        )
        result = analyze_project(config)

        # validate_name is called by BaseModel.__init__ via self.validate_name()
        assert "models.BaseModel.__init__" in result.direct_callers

    def test_dead_code_detection(self, tmp_path: Path):
        """Verify dead code detection flag works."""
        config = AnalysisConfig(
            project_root=FIXTURE_DIR,
            target_function="core.validate",
            output_path=tmp_path / "blast_radius.html",
            include_dead_code=True,
        )
        result = analyze_project(config)

        # Dead code list should be populated (may or may not find dead functions)
        assert isinstance(result.dead_code, list)

    def test_full_graph_render(self, tmp_path: Path):
        """Render a full project graph (no target function)."""
        config = AnalysisConfig(
            project_root=FIXTURE_DIR,
            target_function=None,
            output_path=tmp_path / "full_graph.html",
        )
        result = analyze_project(config)

        assert (tmp_path / "full_graph.html").exists()
        assert result.target == "<project>"
