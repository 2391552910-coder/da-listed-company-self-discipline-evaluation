"""Read-only baseline audit; no database, network, models or third-party packages.

Run from any working directory with Python 3.10+.
Reproductions isolate original method bodies with stub repositories/data.
This is a diagnostic, not a regression test that requires bugs to remain.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "cdd17fdd95bd01e658f6931007d62c5b352087a5"


def read(relative):
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def isolate_method(relative, class_name, method_name, environment):
    tree = ast.parse(read(relative))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method_name)
    method.decorator_list = []
    method.returns = None
    for arg in (*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs):
        arg.annotation = None
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    exec(compile(module, str(ROOT / relative), "exec"), environment)
    return environment[method_name]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    observations = []
    source_files = sorted(p for p in (ROOT / "backend").rglob("*.py") if "__pycache__" not in p.parts)
    errors = []
    for path in source_files:
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except SyntaxError as exc:
            errors.append({"path": path.relative_to(ROOT).as_posix(), "error": str(exc)})
    observations.append({"check": "backend_python_syntax", "files": len(source_files),
                         "status": "pass" if not errors else "fail", "errors": errors})

    render = isolate_method(
        "backend/app/services/report_service.py", "ReportService", "render_html",
        {"DIM_NAMES": {"D1": "D1", "D2": "D2", "D3": "D3", "D4": "D4"}})
    data = {
        "company": SimpleNamespace(stock_name="fixture", stock_code="000001", industry=None, market=None),
        "index_result": None,
        "evaluation": SimpleNamespace(d1_evaluation="", d2_evaluation="", d3_evaluation="",
                                      d4_evaluation="", overall_summary="fixture"),
        "evidence_by_dim": {}, "generated_at": datetime.now(timezone.utc),
    }
    try:
        render(SimpleNamespace(build_report_data=lambda *a: data), None, "000001", 2024)
        report_result = {"reproduced": False}
    except Exception as exc:
        report_result = {"reproduced": isinstance(exc, AttributeError),
                         "exception_type": type(exc).__name__, "message": str(exc)}
    observations.append({"check": "A08_missing_index_html", **report_result})

    saved = []
    save_evidence = isolate_method(
        "backend/app/services/evaluation_service.py", "EvaluationService", "_save_evidence",
        {"knowledge_repository": SimpleNamespace(get_by_title=lambda *a: None),
         "evaluation_evidence_repository": SimpleNamespace(create=lambda db, obj: saved.append(obj)),
         "EvaluationEvidence": lambda **kwargs: kwargs})
    save_evidence(None, None, 1, "D2",
                  [{"title": "uncited-candidate", "source": "fixture", "content": "fixture", "score": .5}], [])
    observations.append({"check": "A03_empty_citations_write_candidates",
                         "reproduced": len(saved) == 1, "candidate_count": 1,
                         "declared_citation_count": 0, "saved_evidence_count": len(saved)})

    docker = read("frontend/Dockerfile")
    observations.append({"check": "A07_frontend_nginx_config",
                         "custom_nginx_copy_present": any(line.lstrip().startswith("COPY ") and
                             ("nginx.conf" in line or "/etc/nginx/" in line)
                             for line in docker.splitlines())})
    dashboard = read("frontend/src/views/Dashboard.vue")
    observations.append({"check": "A09_fixed_dashboard_values",
                         "fixed_radar_present": "[82, 76, 88, 71]" in dashboard,
                         "fixed_pie_present": all(s in dashboard for s in ("value: 30", "value: 45",
                                                                                          "value: 18", "value: 7"))})
    index_tree = ast.parse(read("backend/app/services/index_service.py"))
    imported = {alias.asname or alias.name.split(".")[0]
                for node in ast.walk(index_tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names}
    observations.append({"check": "A16_indicator_missing_import",
                         "indicator_constructed": any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                                                      and n.func.id == "Indicator" for n in ast.walk(index_tree)),
                         "indicator_imported": "Indicator" in imported})
    observations.append({"check": "baseline_engineering_assets",
                         "tests_directory_present": any((ROOT / p).is_dir() for p in ("tests", "backend/tests")),
                         "migration_directory_present": any((ROOT / p).is_dir()
                                                            for p in ("backend/migrations", "backend/alembic")),
                         "ci_workflows_present": (ROOT / ".github/workflows").is_dir()})
    research_paths = [
        "backend/app/services/evaluation_service.py", "backend/app/services/report_service.py",
        "backend/app/services/index_service.py", "frontend/Dockerfile", "frontend/src/views/Dashboard.vue",
    ]
    result = {
        "schema_version": "stage1-baseline-audit-v1", "audited_baseline_commit": BASELINE,
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "AST syntax and isolated method probes, not end-to-end validation",
        "network_calls": 0, "model_calls": 0, "production_changes": False,
        "observations": observations,
        "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in research_paths},
        "not_verified": ["dependency installation", "FastAPI integration", "PostgreSQL", "Milvus",
                         "real PDF parsing", "GPU models", "generation API", "PDF rendering", "Docker deployment"],
    }
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        destination = args.output if args.output.is_absolute() else ROOT / args.output
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    print(text)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
