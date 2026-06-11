"""Tests for the simple project — verifies call-graph test coverage detection."""


from simple_project.core import process_data, validate
from simple_project.api import handle_request


def test_process_data():
    result = process_data("  hello  world  ")
    assert result["status"] == "ok"
    assert result["data"] == "hello world"


def test_validate_empty():
    try:
        validate("")
    except ValueError:
        pass


def test_handle_request():
    result = handle_request("test input")
    assert "response" in result
