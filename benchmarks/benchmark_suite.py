"""Performance & Scalability Benchmark Suite for Blast Radius Mapper.

Generates reproducible synthetic Python codebases of varying scales and
measures end-to-end static analysis performance:
1. AST parsing & symbol table registration across modules
2. C3 MRO class hierarchy resolution (diamond multiple inheritance)
3. Cross-module import resolution and call graph construction
4. Transitive blast radius analysis (multi-hop reverse BFS)
5. Confidence scoring and risk level calculation
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from blast_radius_mapper.models import AnalysisConfig
from blast_radius_mapper.pipeline import analyze_project


def generate_synthetic_project(
    root: Path,
    num_modules: int,
    funcs_per_module: int,
    classes_per_module: int,
) -> tuple[Path, str]:
    """Generate a deterministic synthetic Python project designed to genuinely stress:
    - Multi-file AST parsing and symbol extraction
    - Diamond multiple inheritance with C3 MRO linearization
    - Cross-module import resolution
    - Deep transitive reverse-BFS call graph traversal
    - Structural test coverage mapping and confidence scoring

    Topology:
    - ``core.py`` defines a foundational diamond inheritance hierarchy
      (``BaseEngine`` -> ``ComputeEngine``, ``StorageEngine`` -> ``HybridEngine``)
      and a foundational utility ``core_compute(x)``.
    - Modules ``mod_0`` through ``mod_{N-1}`` each define:
      - Class hierarchies extending ``HybridEngine`` with multiple inheritance.
      - Function pipelines ``task_0`` -> ``task_1`` -> ... -> ``task_{K-1}`` -> ``core_compute()``.
      - Cross-module dependency: ``mod_{m}.task_0`` calls ``mod_{m-1}.task_0``.
    - Test files ``test_mod_{m}.py`` exercise each module's entry point ``task_0``.
    - Target: ``synthetic_pkg.core.core_compute`` sits at the base of every pipeline,
      creating a realistic, multi-branched transitive blast radius.
    """
    src_dir = root / "synthetic_pkg"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")

    # 1. Foundational core module with C3 diamond inheritance and target utility
    core_lines = [
        "from __future__ import annotations",
        "",
        "class BaseEngine:",
        "    def compute(self, val: int) -> int:",
        "        return val + 1",
        "",
        "class ComputeEngine(BaseEngine):",
        "    def compute(self, val: int) -> int:",
        "        return super().compute(val) * 2",
        "",
        "class StorageEngine(BaseEngine):",
        "    def compute(self, val: int) -> int:",
        "        return super().compute(val) + 10",
        "",
        "class HybridEngine(ComputeEngine, StorageEngine):",
        "    def compute(self, val: int) -> int:",
        "        return super().compute(val) + 100",
        "",
        "def core_compute(x: int) -> int:",
        "    engine = HybridEngine()",
        "    return engine.compute(x)",
        "",
    ]
    (src_dir / "core.py").write_text("\n".join(core_lines), encoding="utf-8")

    # 2. Feature modules
    for m_idx in range(num_modules):
        mod_name = f"mod_{m_idx}"
        lines = [
            f"# Synthetic module {m_idx}",
            "from __future__ import annotations",
            "from synthetic_pkg.core import HybridEngine, core_compute",
            "",
        ]

        if m_idx > 0:
            lines.append(f"from synthetic_pkg.mod_{m_idx - 1} import task_0 as prev_task")
            lines.append("")

        # Module class hierarchy exercising multi-inheritance C3 MRO
        for c_idx in range(classes_per_module):
            cls_name = f"Service_{c_idx}"
            if c_idx == 0:
                lines.append(f"class {cls_name}(HybridEngine):")
            else:
                lines.append(f"class {cls_name}(Service_{c_idx - 1}, HybridEngine):")
            lines.append("    def process(self, v: int) -> int:")
            lines.append("        return self.compute(v)")
            lines.append("")

        # Function pipeline: task_0 -> task_1 -> ... -> task_{K-1} -> core_compute
        for f_idx in range(funcs_per_module):
            func_name = f"task_{f_idx}"
            lines.append(f"def {func_name}(val: int = 1) -> int:")
            lines.append("    x = val + 1")
            if f_idx + 1 < funcs_per_module:
                lines.append(f"    x = task_{f_idx + 1}(x)")
            else:
                lines.append("    x = core_compute(x)")
                if classes_per_module > 0:
                    last_cls = f"Service_{classes_per_module - 1}"
                    lines.append(f"    s = {last_cls}()")
                    lines.append("    x += s.process(x)")
            if m_idx > 0 and f_idx == 0:
                lines.append("    x += prev_task(x)")
            lines.append("    return x")
            lines.append("")

        (src_dir / f"{mod_name}.py").write_text("\n".join(lines), encoding="utf-8")

        # Test covering this module's pipeline
        test_lines = [
            "from __future__ import annotations",
            f"from synthetic_pkg.{mod_name} import task_0",
            "",
            f"def test_{mod_name}_pipeline():",
            "    assert task_0(1) > 0",
            "",
        ]
        (src_dir / f"test_{mod_name}.py").write_text("\n".join(test_lines), encoding="utf-8")

    target_fqn = "synthetic_pkg.core.core_compute"
    return root, target_fqn


def run_benchmarks() -> list[dict[str, object]]:
    scales = [
        ("Small", 5, 10, 2),  # ~50 funcs, 10 classes
        ("Medium", 15, 20, 3),  # ~300 funcs, 45 classes
        ("Large", 30, 35, 4),  # ~1,050 funcs, 120 classes
        ("Very Large", 50, 60, 5),  # ~3,000 funcs, 250 classes
    ]

    results: list[dict[str, object]] = []

    print("\n" + "=" * 80)
    print("BLAST RADIUS MAPPER -- PERFORMANCE & SCALABILITY BENCHMARKS")
    print("=" * 80)

    for name, num_modules, funcs_per_mod, classes_per_mod in scales:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            project_root, target_fqn = generate_synthetic_project(
                tmp_path, num_modules, funcs_per_mod, classes_per_mod
            )

            config = AnalysisConfig(
                project_root=project_root,
                target_function=target_fqn,
            )

            t_start = time.perf_counter()
            impact = analyze_project(config)
            total_time_ms = (time.perf_counter() - t_start) * 1000

            approx_funcs = num_modules * funcs_per_mod
            scale_result: dict[str, object] = {
                "scale": name,
                "modules": num_modules,
                "approx_funcs": approx_funcs,
                "e2e_time_ms": round(total_time_ms, 2),
                "direct_callers": len(impact.direct_callers),
                "transitive_dependents": len(impact.transitive_dependents),
                "confidence_score": round(impact.confidence_score, 3),
                "risk_label": impact.risk_label,
            }
            results.append(scale_result)

            print(
                f"[{name:10}] Modules: {num_modules:3} | Functions: ~{approx_funcs:4} | "
                f"E2E Pipeline: {total_time_ms:7.2f} ms | "
                f"Direct: {len(impact.direct_callers):2} | "
                f"Dependents: {len(impact.transitive_dependents):4} | "
                f"Score: {impact.confidence_score:.3f} {impact.risk_label}"
            )

    print("=" * 80 + "\n")
    return results


if __name__ == "__main__":
    run_benchmarks()
