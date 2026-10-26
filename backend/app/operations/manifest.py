"""Reproducible public code inventory. This is not a private data backup."""
import hashlib
import os
from pathlib import Path
import re

from app.documents.common import DomainError, dump, sha
from app.operations.backup import _linked, _relative, _safe_root

EXCLUDED_DIRECTORIES = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".aws", ".codex", ".agents", "runtime", "uploads",
    "models", "weights", "raw", "rawsource", "raw_source", "research_data", "source_inputs", "results", "test-results", "dist", "build"}
EXCLUDED_EXTENSIONS = {".pyc", ".pyo", ".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db", ".dump",
                       ".pt", ".pth", ".safetensors", ".onnx", ".bin", ".pdf", ".docx", ".xlsx", ".csv", ".tsv", ".jsonl", ".ndjson", ".parquet", ".arrow", ".feather", ".zip", ".tar", ".gz"}
TEMPLATES = {".env.example", ".env.template", ".env.sample"}
MAX_PUBLIC_FILE_BYTES = 8 * 1024 * 1024
SOURCE_EXTENSIONS = {".py", ".pyi", ".js", ".ts", ".tsx", ".jsx", ".vue", ".css", ".scss", ".sass", ".less",
                     ".html", ".md", ".rst", ".toml", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".ps1", ".sh",
                     ".bat", ".cmd", ".service", ".timer", ".sql"}
SOURCE_NAMES = {"dockerfile", ".dockerignore", ".gitignore", ".gitattributes", ".editorconfig", "license", "license.txt",
                "notice", "notice.txt", "makefile", "cmakelists.txt", "package.json", "package-lock.json", "yarn.lock"}
PUBLIC_VALIDATION_JSON = {"baseline-audit.json", "real-validation.json", "test-summary.json", "validation-summary.json",
                          "delivery-manifest.json", "source-provenance.json"}


def _template(name):
    low = name.casefold()
    return low in TEMPLATES or low.endswith((".env.example", ".env.template", ".env.sample")) or (
        low.startswith(".env.") and low.endswith((".example", ".template", ".sample")))


def _protected(relative):
    parts = Path(relative).parts
    if len(parts) >= 2 and tuple(part.casefold() for part in parts[:2]) == ("backend", "backend"):
        return True
    for part in parts:
        low = part.casefold()
        if low in EXCLUDED_DIRECTORIES or low.startswith(".t"):
            return True
    name = parts[-1].casefold()
    return ((name.startswith(".env") and not _template(name)) or
            name.startswith(("credentials", "secrets", "id_rsa", "id_ed25519")) or
            Path(name).suffix in EXCLUDED_EXTENSIONS)


def _source_file(relative):
    """Use a positive source/configuration allowlist, not a data denylist alone."""
    path = Path(relative)
    name = path.name.casefold()
    if _template(name) or name in SOURCE_NAMES or path.suffix.casefold() in SOURCE_EXTENSIONS:
        return True
    if name.startswith("requirements") and name.endswith(".txt"):
        return True
    if name.endswith(".json"):
        return (name.startswith(("tsconfig", "jsconfig")) or
                (relative.startswith("docs/") and name in PUBLIC_VALIDATION_JSON) or
                relative == "backend/app/research_adapter/provenance.json")
    if path.suffix.casefold() in {".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2"}:
        return relative.startswith(("frontend/src/assets/", "frontend/public/"))
    return False


def _scan(data, relative):
    # Known credential forms only. A source manifest is not a claim that an
    # arbitrary user-chosen password can be identified from text automatically.
    patterns = (rb"\bsk-[A-Za-z0-9_-]{20,}\b", rb"\bhf_[A-Za-z0-9]{20,}\b", rb"\bAKIA[A-Z0-9]{16}\b")
    private_header = b"-----BEGIN " + b"PRIVATE KEY-----"
    if private_header in data or any(re.search(pattern, data) for pattern in patterns):
        raise DomainError("DELIVERY_SECRET_DETECTED", "交付文件包含疑似密钥，拒绝生成公开清单", 409)
    if _template(Path(relative).name):
        text = data.decode("utf-8", errors="replace")
        for value in re.findall(r"(?m)^[ \t]*LLM_API_KEY[ \t]*=[ \t]*(.*?)[ \t]*$", text):
            value = value.strip().strip("\"'")
            if value and value.casefold() not in {"replace-me", "your-api-key", "changeme", "example"} and not value.startswith("${"):
                raise DomainError("DELIVERY_SECRET_DETECTED", "配置模板含非空模型密钥，拒绝公开交付", 409)


def build_manifest(root, paths=None, *, exclude_paths=()):
    root = _safe_root(root)
    excluded = {_relative(p) for p in exclude_paths}
    if paths is None:
        candidates = []
        for directory, dirs, names in os.walk(root, followlinks=False):
            directory = Path(directory)
            keep = []
            for name in dirs:
                relative = (directory / name).relative_to(root).as_posix()
                if _protected(relative):
                    continue
                if _linked(directory / name):
                    raise DomainError("DELIVERY_LINK_REJECTED", "交付目录不能包含链接", 409)
                keep.append(name)
            dirs[:] = keep
            for name in names:
                relative = (directory / name).relative_to(root).as_posix()
                if _protected(relative) or relative in excluded:
                    continue
                if _linked(directory / name):
                    raise DomainError("DELIVERY_LINK_REJECTED", "交付目录不能包含链接", 409)
                candidates.append(relative)
        candidates = [p for p in candidates if not _protected(p) and _source_file(p) and p not in excluded]
    else:
        if not isinstance(paths, (list, tuple)) or not paths:
            raise DomainError("DELIVERY_INVALID_PATHS", "指定交付路径必须是非空列表", 422)
        candidates = [_relative(p) for p in paths]
        if any(_protected(p) or not _source_file(p) or p in excluded for p in candidates):
            raise DomainError("DELIVERY_SENSITIVE_PATH", "不能显式交付密钥、原始资料、运行数据或模型文件", 409)
    if len(candidates) != len(set(candidates)):
        raise DomainError("DELIVERY_INVALID_PATHS", "交付路径重复", 422)
    records = []
    for relative in sorted(candidates):
        _relative(relative)
        path = root / relative
        for component in (path, *path.parents):
            if _linked(component):
                raise DomainError("DELIVERY_LINK_REJECTED", "交付文件路径不能经过链接", 409)
            if component == root:
                break
        if not path.is_file():
            raise DomainError("DELIVERY_FILE_MISSING", "交付文件不存在或不是普通文件", 404)
        if path.stat().st_size > MAX_PUBLIC_FILE_BYTES:
            raise DomainError("DELIVERY_FILE_TOO_LARGE", "交付清单不包含大型数据或模型文件", 409)
        data = path.read_bytes()
        _scan(data, relative)
        records.append({"path": relative, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    result = {"schema_version": "dalun-code-delivery-v1", "file_count": len(records), "files": records,
              "scope": "source code only; private databases, source documents, model weights and credentials excluded"}
    return result | {"manifest_sha256": sha(dump(result).encode())}
