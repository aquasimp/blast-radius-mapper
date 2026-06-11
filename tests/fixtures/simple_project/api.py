"""API module — imports from core and uses it."""

from .core import process_data


def handle_request(payload: str) -> dict:
    """Handle an incoming request by processing its payload."""
    result = process_data(payload)
    return format_response(result)


def format_response(data: dict) -> dict:
    """Wrap data in a response envelope."""
    return {"response": data, "version": "1.0"}
