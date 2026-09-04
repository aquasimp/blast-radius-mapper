"""
Core data structures for the Blast Radius Mapper.

Every function/method in the analyzed project is identified by a Fully Qualified
Name (FQN). All other structures reference functions via FQN strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ── FQN ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FQN:
    """
    Fully Qualified Name for a Python callable.

    Format: ``<module_dotpath>.<qualname>``

    Examples::

        FQN("myproject.utils.helpers", "retry")              # free function
        FQN("myproject.models.user", "User.__init__")         # method
        FQN("myproject.models.user", "User.validate_email")   # method
    """

    module: str
    """Dot-separated module path relative to project root."""

    qualname: str
    """Qualified name within the module (e.g. ``"User.validate_email"``)."""

    @property
    def full(self) -> str:
        """Return the globally unique identifier string."""
        return f"{self.module}.{self.qualname}"

    @property
    def short_name(self) -> str:
        """Return just the last component (bare function/method name)."""
        return self.qualname.rsplit(".", maxsplit=1)[-1]

    @property
    def class_qualname(self) -> str | None:
        """Return the enclosing class qualname, or None if top-level function."""
        parts = self.qualname.rsplit(".", maxsplit=1)
        return parts[0] if len(parts) > 1 else None

    def __str__(self) -> str:
        return self.full

    def __repr__(self) -> str:
        return f"FQN({self.full!r})"


# ── Call types ───────────────────────────────────────────────────────────────


class CallType(str, Enum):
    """Classification of a call site for confidence scoring."""

    DIRECT = "direct"
    """``foo()`` where ``foo`` is a known function."""

    SELF_METHOD = "self_method"
    """``self.method()``."""

    CLS_METHOD = "cls_method"
    """``cls.method()`` inside a classmethod."""

    SUPER_CALL = "super_call"
    """``super().method()``."""

    MODULE_FUNCTION = "module_function"
    """``module.func()`` via import."""

    CONSTRUCTOR = "constructor"
    """``ClassName()`` → resolves to ``__init__``."""

    ALIASED = "aliased"
    """Aliased import or reassigned name."""

    UNRESOLVED = "unresolved"
    """Could not resolve the callee statically."""


#: Edge confidence scores by call type.  Higher = more certain the edge is real.
EDGE_CONFIDENCE: dict[CallType, float] = {
    CallType.DIRECT: 1.0,
    CallType.SELF_METHOD: 0.95,
    CallType.CLS_METHOD: 0.95,
    CallType.SUPER_CALL: 0.90,
    CallType.MODULE_FUNCTION: 0.95,
    CallType.CONSTRUCTOR: 0.95,
    CallType.ALIASED: 0.85,
    CallType.UNRESOLVED: 0.0,
}


# ── FunctionInfo ─────────────────────────────────────────────────────────────


@dataclass
class FunctionInfo:
    """Metadata about a single function or method definition."""

    fqn: FQN
    filepath: Path
    """Absolute path to the source file."""

    start_line: int
    """1-indexed first line of the ``def`` statement."""

    end_line: int
    """1-indexed last line of the function body (inclusive)."""

    decorators: list[str] = field(default_factory=list)
    """Decorator names as strings (e.g. ``["staticmethod", "lru_cache"]``)."""

    is_method: bool = False
    is_static: bool = False
    is_classmethod: bool = False
    is_property: bool = False
    is_test: bool = False
    """True if function name starts with ``test_`` or is in a test file."""

    class_fqn: FQN | None = None
    """FQN of the enclosing class, if this is a method."""

    parameters: list[str] = field(default_factory=list)
    """Parameter names from the signature."""

    docstring: str | None = None

    @property
    def is_dunder(self) -> bool:
        return self.fqn.short_name.startswith("__") and self.fqn.short_name.endswith("__")

    @property
    def display_name(self) -> str:
        """Human-friendly label for CLI output."""
        return self.fqn.full


# ── ClassInfo ────────────────────────────────────────────────────────────────


@dataclass
class ClassInfo:
    """Metadata about a class definition, including base class references."""

    fqn: FQN
    filepath: Path
    start_line: int
    end_line: int

    base_names: list[str] = field(default_factory=list)
    """Raw base class names as written in source (before resolution)."""

    resolved_bases: list[FQN] = field(default_factory=list)
    """FQNs of resolved base classes (filled after import resolution)."""

    mro: list[FQN] = field(default_factory=list)
    """Computed C3 linearization (filled after all classes are resolved)."""

    decorators: list[str] = field(default_factory=list)
    methods: list[FQN] = field(default_factory=list)
    """FQNs of all methods defined directly in this class."""


# ── CallEdge ─────────────────────────────────────────────────────────────────


@dataclass
class CallEdge:
    """A directed edge: caller → callee."""

    caller: FQN
    callee: FQN

    call_type: CallType
    call_site_line: int
    """Line number of the call expression in the caller's file."""

    confidence: float = 1.0
    """0.0–1.0 — how certain we are this edge is correct."""

    def __post_init__(self) -> None:
        if self.confidence == 1.0:
            self.confidence = EDGE_CONFIDENCE.get(self.call_type, 0.0)


# ── ImpactResult ─────────────────────────────────────────────────────────────


@dataclass
class ImpactResult:
    """Result of blast radius analysis for a single target function."""

    target: str
    """FQN string of the function being analyzed."""

    direct_callers: list[str] = field(default_factory=list)
    """FQN strings of functions that directly call the target."""

    transitive_dependents: list[str] = field(default_factory=list)
    """FQN strings of all transitively affected functions, ordered by distance."""

    depth_map: dict[str, int] = field(default_factory=dict)
    """FQN → shortest distance from target in the reverse call graph."""

    coverage_map: dict[str, float] = field(default_factory=dict)
    """FQN → line coverage ratio [0.0, 1.0]."""

    test_functions: list[str] = field(default_factory=list)
    """FQN strings of test functions that exercise the target (call-graph path)."""

    confidence_score: float = 0.0
    """Overall safety score [0.0, 1.0]."""

    unresolved_calls: list[str] = field(default_factory=list)
    """Call expressions the tool could not resolve."""

    warnings: list[str] = field(default_factory=list)
    """Human-readable caveats about the analysis."""

    dead_code: list[str] = field(default_factory=list)
    """FQN strings of functions with zero callers (secondary signal)."""

    @property
    def risk_label(self) -> str:
        """Human-readable risk label (ASCII-safe for Windows consoles)."""
        if self.confidence_score >= 0.80:
            return "[SAFE]"
        if self.confidence_score >= 0.50:
            return "[MODERATE]"
        if self.confidence_score >= 0.20:
            return "[RISKY]"
        return "[DANGEROUS]"

    @property
    def total_affected(self) -> int:
        return len(self.transitive_dependents)


# ── AnalysisConfig ───────────────────────────────────────────────────────────


@dataclass
class AnalysisConfig:
    """Configuration for a single analysis run."""

    project_root: Path
    target_function: str | None = None
    coverage_path: Path | None = None
    output_path: Path = Path("blast_radius.html")
    json_output_path: Path | None = None
    max_depth: int = 50
    include_dead_code: bool = False
    exclude_patterns: list[str] = field(
        default_factory=lambda: [
            "__pycache__",
            ".git",
            ".venv",
            "venv",
            "node_modules",
            ".tox",
            ".mypy_cache",
            ".pytest_cache",
            "dist",
            "build",
            "*.egg-info",
        ]
    )
    test_file_patterns: list[str] = field(
        default_factory=lambda: [
            "test_*.py",
            "*_test.py",
            "tests.py",
        ]
    )
    test_dir_patterns: list[str] = field(
        default_factory=lambda: [
            "tests",
            "test",
        ]
    )
