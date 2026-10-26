"""Explicit deployment preflight. Configuration is never a network/model test."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import urlopen


def http_checks(base_url):
    try:
        value = urlsplit(base_url)
    except ValueError:
        return [{"name": "http", "status": "failed", "error_code": "INVALID_HEALTH_URL"}]
    if value.scheme not in {"http", "https"} or not value.hostname or value.username or value.password or value.query or value.fragment:
        return [{"name": "http", "status": "failed", "error_code": "INVALID_HEALTH_URL"}]
    checks = []
    for endpoint, field, expected in (("/health/live", "status", "ok"), ("/health/ready", "status", "ok"),
                                       ("/health/workers", "healthy", True)):
        try:
            with urlopen(base_url.rstrip("/") + endpoint, timeout=5) as response:
                body = json.loads(response.read(65536))
            passed = body.get(field) == expected
            checks.append({"name": endpoint, "status": "passed" if passed else "failed"})
        except Exception:
            checks.append({"name": endpoint, "status": "failed", "error_code": "HTTP_CHECK_FAILED"})
    return checks


def run_preflight(settings, retrieval_profile="bm25_diagnostic", verify_cuda=False, http_base=None, http_only=False):
    checks = []
    if not http_only:
        dependencies = {name: importlib.util.find_spec(name) is not None for name in ("fastapi", "sqlalchemy", "fitz", "tokenizers")}
        checks.append({"name": "web_dependencies", "status": "passed" if all(dependencies.values()) else "failed",
                       "modules": dependencies})
        try:
            from app.documents.service import Documents
            docs = Documents(settings)
            ready = docs.db.ready()
            checks.append({"name": "database_schema", "status": "passed" if ready else "failed", "profile": docs.db.dialect})
        except Exception:
            checks.append({"name": "database_schema", "status": "failed", "error_code": "DATABASE_UNAVAILABLE"})
        try:
            settings.data_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=settings.data_root) as handle:
                handle.write(b"dalun-storage-probe")
                handle.flush()
            checks.append({"name": "storage_writable", "status": "passed"})
        except OSError:
            checks.append({"name": "storage_writable", "status": "failed", "error_code": "STORAGE_NOT_WRITABLE"})
        if retrieval_profile == "qwen_cuda":
            interpreter = Path(settings.model_python)
            if not interpreter.is_absolute() or not interpreter.is_file():
                checks.append({"name": "cuda", "status": "failed", "cuda_verified": False, "error_code": "MODEL_PYTHON_UNAVAILABLE"})
            elif not verify_cuda:
                checks.append({"name": "cuda", "status": "not_checked", "cuda_verified": False,
                               "required_action": "rerun with --verify-cuda in the selected model environment"})
            else:
                try:
                    probe = subprocess.run([str(interpreter), "-c", "import json,torch; print(json.dumps({'cuda':torch.cuda.is_available(),'devices':torch.cuda.device_count()}))"],
                                           capture_output=True, text=True, timeout=30, check=False)
                    result = json.loads(probe.stdout)
                    passed = probe.returncode == 0 and result.get("cuda") is True and result.get("devices", 0) > 0
                    checks.append({"name": "cuda", "status": "passed" if passed else "failed", "cuda_verified": passed})
                except Exception:
                    checks.append({"name": "cuda", "status": "failed", "cuda_verified": False, "error_code": "CUDA_PROBE_FAILED"})
        else:
            checks.append({"name": "retrieval_profile", "status": "passed", "profile": "bm25_diagnostic", "cuda_verified": False})
        try:
            from app.evaluation.provider import Provider
            identity = Provider(settings).identity()
            checks.append({"name": "generation_configuration", "status": "configured", "model": identity["model"],
                           "availability_check": "configuration_only", "network_verified": False})
        except Exception:
            checks.append({"name": "generation_configuration", "status": "not_configured", "network_verified": False})
    if http_base:
        checks += http_checks(http_base)
    healthy = bool(checks) and all(c["status"] in {"passed", "configured", "not_configured"} for c in checks)
    return {"healthy": healthy, "checks": checks, "generation_network_verified": False,
            "note": "Generation configuration does not prove remote service availability; this command makes no generation request."}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval-profile", choices=["bm25_diagnostic", "qwen_cuda"], default="bm25_diagnostic")
    parser.add_argument("--verify-cuda", action="store_true")
    parser.add_argument("--http-base")
    parser.add_argument("--http-only", action="store_true")
    args = parser.parse_args(argv)
    if args.http_only and not args.http_base:
        parser.error("--http-only requires --http-base")
    from app.documents.settings import Settings
    result = run_preflight(Settings.from_env(), args.retrieval_profile, args.verify_cuda, args.http_base, args.http_only)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
