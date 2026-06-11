"""Models with class hierarchy for testing self.method() and MRO resolution."""


class BaseModel:
    """Base model with shared behavior."""

    def __init__(self, name: str):
        self.name = name
        self.validate_name()

    def validate_name(self) -> None:
        """Validate the model name."""
        if not self.name:
            raise ValueError("Name cannot be empty")

    def serialize(self) -> dict:
        """Serialize to dict."""
        return {"name": self.name}


class User(BaseModel):
    """User model — inherits from BaseModel."""

    def __init__(self, name: str, email: str):
        self.email = email
        super().__init__(name)

    def validate_email(self) -> None:
        """Validate email format."""
        if "@" not in self.email:
            raise ValueError("Invalid email")

    def serialize(self) -> dict:
        """Override serialize to include email."""
        base = super().serialize()
        base["email"] = self.email
        return base


class AdminUser(User):
    """Admin user — tests diamond-like inheritance."""

    def __init__(self, name: str, email: str, role: str = "admin"):
        self.role = role
        super().__init__(name, email)

    def get_permissions(self) -> list:
        """Get admin permissions."""
        return ["read", "write", "admin"]
