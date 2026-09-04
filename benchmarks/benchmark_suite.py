"""
Performance & Scalability Benchmark Suite for Blast Radius Mapper.

Generates reproducible synthetic Python codebases of varying scales and
measures end-to-end static analysis performance:
1. AST parsing & symbol table registration
2. C3 MRO class hierarchy resolution
3. Call graph construction
4. Transitive blast radius analysis (reverse BFS)
5. Confidence scoring calculation
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
    src_dir = root / "synthetic_pkg"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")

    first_target_fqn = ""

    for m_idx in range(num_modules):
        mod_name = f"mod_{m_idx}"
        lines = [
            f"# Synthetic module {m_idx}",
            "from __future__ import annotations",
            "",
        ]

        if m_idx > 0:
            lines.append(f"import synthetic_pkg.mod_{m_idx - 1} as prev_mod")
            lines.append("")

        for c_idx in range(classes_per_module):
            class_name = f"Service_{c_idx}"
            if c_idx > 0:
                lines.append(f"class {class_name}(Service_{c_idx - 1}):")
            else:
                lines.append(f"class {class_name}:")

            lines.append("    def execute(self) -> str:")
            lines.append("        return 'result'")
            lines.append("")
            lines.append("    def run(self) -> str:")
            lines.append("        return self.execute()")
            lines.append("")

        for f_idx in range(funcs_per_module):
            func_name = f"task_{f_idx}"
            if not first_target_fqn:
                first_target_fqn = f"synthetic_pkg.{mod_name}.{func_name}"

            callee = f"task_{f_idx + 1}" if f_idx + 1 < funcs_per_module else None
            cross_call = "prev_mod.task_0()" if (m_idx > 0 and f_idx == 0) else None

            lines.append(f"def {func_name}():")
            lines.append("    x = 1 + 1")
            if callee:
                lines.append(f"    {callee}()")
            if cross_call:
                lines.append(f"    {cross_call}")
            lines.append("    return x")
            lines.append("")

        test_file = src_dir / f"test_{mod_name}.py"
        test_lines = [
            f"from synthetic_pkg.{mod_name} import task_0",
            "",
            f"def test_{mod_name}_entry():",
            "    task_0()",
        ]
        test_file.write_text("\n".join(test_lines), encoding="utf-8")

        mod_file = src_dir / f"{mod_name}.py"
        mod_file.write_text("\n".join(lines), encoding="utf-8")

    return root, first_target_fqn


def run_benchmarks() -> list[dict[str, object]]:
    scales = [
        ("Small", 5, 10, 2),  # ~50 funcs, 10 classes
        ("Medium", 15, 20, 3),  # ~300 funcs, 45 classes
        ("Large", 30, 35, 4),  # ~1,050 funcs, 120 classes
        ("Very Large", 50, 60, 5),  # ~3,000 funcs, 250 classes
    ]

    results = []

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
            scale_result = {
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
                f"Dependents: {len(impact.transitive_dependents):3} | "
                f"Score: {impact.confidence_score:.3f} {impact.risk_label}"
            )

    print("=" * 80 + "\n")
    return results


if __name__ == "__main__":
    run_benchmarks()
