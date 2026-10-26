from pathlib import Path
import json

import pytest

from app.documents.common import DomainError
from app.operations.manifest import build_manifest


def write(root, relative, data=b"fixture"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_delivery_inventory_excludes_private_runtime_and_is_reproducible(tmp_path):
    write(tmp_path, "app/main.py", b"print('code')\n")
    write(tmp_path, "frontend/src/assets/icon.svg", b"<svg/>")
    write(tmp_path, ".env.example", b"LLM_API_KEY=\nLLM_MODEL=example-model\n")
    write(tmp_path, "backend/requirements-web.txt", b"fastapi\n")
    write(tmp_path, "frontend/package.json", b"{}")
    for path in [".env", ".env.production", ".aws/credentials", "backend/runtime/original.txt", "rawsource/report.txt", "models/model.safetensors", "node_modules/package/index.js", ".git/config", "test-results/log.txt", "backend/.trvis/qa.txt", "secret.pem", "report.pdf", "research_data/annual.txt", "actual_metrics.csv", "actual_metrics.json", "backend/backend/app.py", "notes.txt"]:
        write(tmp_path, path)
    first = build_manifest(tmp_path)
    second = build_manifest(tmp_path)
    assert first == second
    assert {record["path"] for record in first["files"]} == {"app/main.py", "frontend/src/assets/icon.svg", ".env.example", "backend/requirements-web.txt", "frontend/package.json"}
    assert str(tmp_path) not in json.dumps(first)
    write(tmp_path, "app/main.py", b"changed code")
    assert build_manifest(tmp_path)["manifest_sha256"] != first["manifest_sha256"]


@pytest.mark.parametrize("relative", [".env", "runtime/original.txt", "models/model.pt", "credentials.json", "rawsource/report.txt", "actual_metrics.csv", "actual_metrics.tsv", "actual_metrics.json", "notes.txt", "backend/backend/app.py"])
def test_explicit_sensitive_path_is_refused_not_silently_included(tmp_path, relative):
    write(tmp_path, relative)
    with pytest.raises(DomainError) as error:
        build_manifest(tmp_path, [relative])
    assert error.value.code == "DELIVERY_SENSITIVE_PATH"


@pytest.mark.parametrize("relative", ["../outside", "C:/outside", "/outside", "app\\main.py", "app/../main.py"])
def test_explicit_path_cannot_escape(tmp_path, relative):
    with pytest.raises(DomainError):
        build_manifest(tmp_path, [relative])


@pytest.mark.parametrize("name", [".env.example", "deploy/native.env.example", ".env.server.example"])
def test_nonempty_key_in_template_is_rejected(tmp_path, name):
    write(tmp_path, name, b"LLM_API_KEY=accidentally-filled\n")
    with pytest.raises(DomainError) as error:
        build_manifest(tmp_path)
    assert error.value.code == "DELIVERY_SECRET_DETECTED"


def test_known_secret_in_ordinary_source_is_rejected(tmp_path):
    write(tmp_path, "accidental.py", b"sk-" + b"A" * 32)
    with pytest.raises(DomainError) as error:
        build_manifest(tmp_path)
    assert error.value.code == "DELIVERY_SECRET_DETECTED"


def test_unprotected_link_is_not_followed(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.txt")
    outside.write_bytes(b"private")
    try:
        (tmp_path / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(DomainError) as error:
        build_manifest(tmp_path)
    assert error.value.code == "DELIVERY_LINK_REJECTED"


def test_delivery_cli_creates_new_manifest_only(tmp_path, capsys):
    from app.operations.cli import main
    write(tmp_path, "app.py", b"pass\n")
    output = tmp_path / "delivery.json"
    assert main(["delivery-manifest", str(tmp_path), "--output", str(output)]) == 0
    value = json.loads(output.read_text(encoding="utf-8"))
    assert value["file_count"] == 1
    before = output.read_bytes()
    assert main(["delivery-manifest", str(tmp_path), "--output", str(output)]) == 1
    assert output.read_bytes() == before
    assert "DELIVERY_OUTPUT_EXISTS" in capsys.readouterr().out
