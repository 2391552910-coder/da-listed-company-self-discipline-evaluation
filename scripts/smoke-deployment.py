"""Nginx/Worker/persistence acceptance using acknowledged synthetic sources.

Run against a fresh, isolated acceptance Compose project only. Reopening the
saved state after a container restart verifies original files and frozen reports
without making another model request. No credential is accepted or serialized.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

MODEL = "deployment-fixture-not-a-research-model"
STOCK = "019996"
COMPANY = "部署验收合成公司"


def request(base, path, method="GET", value=None, headers=None, raw=False):
    data = json.dumps(value, ensure_ascii=False).encode() if value is not None else None
    actual_headers = {"Content-Type": "application/json"} if value is not None else {}
    actual_headers.update(headers or {})
    req = Request(base + path, data=data, method=method, headers=actual_headers)
    try:
        with urlopen(req, timeout=15) as response:
            payload, content_type = response.read(), response.headers.get("Content-Type", "")
    except HTTPError as error:
        raise RuntimeError("HTTP_ACCEPTANCE_FAILED:" + str(error.code)) from None
    if raw:
        return payload, content_type
    result = json.loads(payload)
    return result.get("data") if "data" in result else result


def upload(base, filename, data, fields):
    boundary = "dalun-acceptance-" + uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    body += data + f"\r\n--{boundary}--\r\n".encode()
    req = Request(base + "/api/v1/documents/upload", data=bytes(body),
                  headers={"Content-Type": "multipart/form-data; boundary=" + boundary}, method="POST")
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read())["data"]


def wait_job(base, job_id, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = request(base, "/api/v1/jobs/" + job_id)
        if result["status"] == "succeeded":
            return result
        if result["status"] not in {"queued", "running"}:
            raise RuntimeError("ACCEPTANCE_JOB_FAILED:" + str(result.get("error_code")))
        time.sleep(0.5)
    raise RuntimeError("ACCEPTANCE_JOB_TIMEOUT")


def wait_ready(base, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            ready = request(base, "/health/ready")
            workers = request(base, "/health/workers")
            if ready.get("status") == "ok" and workers.get("healthy") is True:
                return
        except (RuntimeError, OSError, URLError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("ACCEPTANCE_SERVICES_NOT_READY")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_state(base, state):
    wait_ready(base)
    run = request(base, "/api/v1/evaluations/" + state["run_id"])
    if run["status"] != "succeeded" or run["snapshot_sha256"] != state["snapshot_sha256"]:
        raise RuntimeError("PERSISTED_RUN_CHANGED")
    for source in state["sources"]:
        data, _ = request(base, "/api/v1/document-versions/" + source["version_id"] + "/file", raw=True)
        if digest(data) != source["file_sha256"]:
            raise RuntimeError("PERSISTED_SOURCE_CHANGED")
    for exported in state["exports"]:
        data, content_type = request(base, "/api/v1/exports/" + exported["id"] + "/file", raw=True)
        if digest(data) != exported["file_sha256"] or exported["media_type"] not in content_type:
            raise RuntimeError("PERSISTED_EXPORT_CHANGED")
    return {"status": "passed", "data_kind": "synthetic_deployment_fixture", "reopened_run_id": run["id"],
            "source_files_verified": len(state["sources"]), "frozen_exports_verified": len(state["exports"]),
            "model_calls_during_verification": 0}


def create_state(base):
    import fitz
    wait_ready(base)
    if request(base, "/api/v1/companies")["total"] != 0:
        raise RuntimeError("ACCEPTANCE_REQUIRES_FRESH_EMPTY_DATABASE")
    page, media = request(base, "/evaluations/not-a-real-run", raw=True)
    if "text/html" not in media or b"<html" not in page.lower():
        raise RuntimeError("SPA_DEEP_ROUTE_FAILED")
    try:
        request(base, "/api/v1/acceptance-route-does-not-exist")
    except RuntimeError as error:
        if str(error) != "HTTP_ACCEPTANCE_FAILED:404":
            raise
    else:
        raise RuntimeError("API_PATH_WAS_REWRITTEN_TO_SPA")
    company = request(base, "/api/v1/companies", "POST", {"stock_code": STOCK, "stock_name": COMPANY})
    pdf = fitz.open()
    sheet = pdf.new_page()
    sheet.insert_text((45, 65), COMPANY + " 019996 2024 年度报告", fontname="china-s", fontsize=13)
    sheet.insert_text((45, 95), "本材料是部署验收合成资料，不是真实上市公司年报。", fontname="china-s", fontsize=12)
    sheet.insert_text((45, 125), "审计委员会监督内部控制与信息披露。第三方资料不足。", fontname="china-s", fontsize=12)
    annual = upload(base, "synthetic-deployment-annual.pdf", pdf.tobytes(),
                    {"corpus_type": "annual_report", "title": "部署验收合成年报（非研究资料）", "company_id": company["id"], "report_year": 2024})
    pdf.close()
    law = upload(base, "synthetic-deployment-rule.txt", "部署验收合成规则，非真实法规。审计委员会应监督内部控制与信息披露。".encode(),
                 {"corpus_type": "regulation", "title": "部署验收合成规则（非真实法规）"})
    parse_ids = []
    for source in (annual, law):
        task = request(base, "/api/v1/document-versions/" + source["id"] + "/parse", "POST", {})
        parse_ids.append(wait_job(base, task["id"])["result"]["parse_id"])
    task = request(base, "/api/v1/indexes", "POST", {"parse_ids": parse_ids, "profile": "bm25_diagnostic", "rerank": False})
    index_id = wait_job(base, task["id"])["result"]["index_id"]
    task = request(base, "/api/v1/evaluations", "POST", {
        "index_id": index_id, "stock_code": STOCK, "report_year": 2024, "model": MODEL,
        "questions": [{"key": "DEPLOY-SINGLE", "dimension": "D2", "text": "审计委员会的内部控制与信息披露有哪些可观察依据和限制？"}],
        "retrieval": {"tokenizer_kind": "utf8_diagnostic", "max_reference_tokens": 6000},
        "budget": {"max_model_calls": 1, "max_output_tokens": 2000, "max_observed_tokens": 20000, "max_runtime_seconds": 180}})
    wait_job(base, task["id"])
    run = request(base, "/api/v1/evaluations/" + task["run_id"])
    if run["request"]["model"]["model"] != MODEL or run["snapshot"]["third_chapter_index"] is not None:
        raise RuntimeError("SYNTHETIC_PROFILE_IDENTITY_MISMATCH")
    for answer in run["answers"]:
        for record in answer["context"]["source_records"]:
            source = request(base, "/api/v1/chunks/" + record["chunk_id"] + "/source")
            if not source["source_available"] or source["frame_text"][record["char_start"]:record["char_end"]] != record["content"]:
                raise RuntimeError("ORIGINAL_SOURCE_CHECK_FAILED")
    exports = []
    for format in ("html", "pdf"):
        exported = request(base, "/api/v1/evaluations/" + run["id"] + "/exports", "POST", {"format": format})
        data, content_type = request(base, "/api/v1/exports/" + exported["id"] + "/file", raw=True)
        if format == "pdf" and not data.startswith(b"%PDF-"):
            raise RuntimeError("PDF_DOWNLOAD_FAILED")
        if digest(data) != exported["file_sha256"]:
            raise RuntimeError("EXPORT_HASH_MISMATCH")
        exports.append({"id": exported["id"], "format": format, "file_sha256": exported["file_sha256"],
                        "media_type": "application/pdf" if format == "pdf" else "text/html"})
    return {"schema_version": "deployment-acceptance-state-v1", "data_kind": "synthetic_deployment_fixture",
            "created_at": datetime.now(timezone.utc).isoformat(), "run_id": run["id"],
            "snapshot_sha256": run["snapshot_sha256"], "exports": exports,
            "sources": [{"version_id": v["id"], "file_sha256": v["file_sha256"]} for v in (annual, law)]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--verify-state", action="store_true")
    parser.add_argument("--acknowledge-synthetic-fixture", action="store_true", required=True)
    args = parser.parse_args(argv)
    value = urlsplit(args.base_url)
    if value.scheme not in {"http", "https"} or not value.hostname or value.username or value.password or value.query or value.fragment:
        parser.error("base URL must not contain credentials, query or fragment")
    base = args.base_url.rstrip("/")
    if args.verify_state:
        state = json.loads(args.state.read_text(encoding="utf-8"))
        if state.get("data_kind") != "synthetic_deployment_fixture":
            raise RuntimeError("NOT_A_SYNTHETIC_ACCEPTANCE_STATE")
        print(json.dumps(verify_state(base, state), ensure_ascii=False))
    else:
        if args.state.exists():
            parser.error("state path must be new; use --verify-state for a saved run")
        state = create_state(base)
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(verify_state(base, state), ensure_ascii=False))


if __name__ == "__main__":
    main()
