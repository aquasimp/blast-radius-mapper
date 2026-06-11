"""Core module for the simple test fixture project."""


def process_data(raw_input: str) -> dict:
    """Process raw input into a structured dict."""
    cleaned = clean_input(raw_input)
    validated = validate(cleaned)
    return {"data": validated, "status": "ok"}


def clean_input(text: str) -> str:
    """Strip and normalize whitespace."""
    return " ".join(text.strip().split())


def validate(text: str) -> str:
    """Validate that text is non-empty."""
    if not text:
        raise ValueError("Empty input")
    return text
