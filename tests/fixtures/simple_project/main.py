"""Main entry point — calls handle_request and creates a User."""

from .api import handle_request
from .models import User


def main():
    """Application entry point."""
    result = handle_request("  hello world  ")
    print(result)

    user = User("Alice", "alice@example.com")
    user.validate_email()
    print(user.serialize())


if __name__ == "__main__":
    main()
