"""Serve an isolated browser acceptance stack; never calls an external model.

Sources, usage values and provider output are labelled synthetic test fixtures.
The application still uses its real document parser, BM25 subprocess, persistent
worker, HTTP model transport, citation validator and HTML/PDF export services.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

FIXTURE_COMPANY = "浏览器验收合成公司"
FIXTURE_STOCK = "019997"
FIXTURE_MODEL = "browser-fixture-not-a-research-model"


class FixtureProvider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 2 * 1024 * 1024:
            self.send_error(413)
            return
        request = json.loads(self.rfile.read(length))
        user = json.loads(request["messages"][-1]["content"])
        context = user["final_context"]
        citations = []
        for evidence_id in ("A1", "R1"):
            pattern = r"\[" + evidence_id + r"\] [^\n]+\n.*?Source text:\n(.*?)(?=\n\n\[[AR]\d+\] |\Z)"
            match = re.search(pattern, context, re.S)
            if not match:
                raise ValueError("browser fixture expected annual and regulation evidence")
            quote = next(line.strip() for line in match.group(1).splitlines() if line.strip())
            citations.append({"evidence_id": evidence_id, "quote": quote})
        answer = {
            "answer": "浏览器验收合成回答：已读取合成年报[A1]与合成规则[R1]。此响应仅验证系统流程，不构成研究结论。",
            "evidence_status": "insufficient",
            "limitations": ["所有文档和模型响应均为自动化浏览器验收fixture，不能作为真实模型效果证据。"],
            "claims": [{"id": "C1", "text": "当前测试上下文包含合成年报与合成规则片段。",
                        "support_status": "unknown", "citations": citations}],
        }
        payload = {"id": "browser-fixture-" + str(uuid4()), "model": FIXTURE_MODEL,
                   "choices": [{"message": {"content": json.dumps(answer, ensure_ascii=False)}, "finish_reason": "stop"}],
                   # These numbers are explicit fixture usage, not measured tokens.
                   "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200}}
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def seed(root):
    import fitz
    from app.documents.indexes import Indexes
    from app.documents.service import Documents
    from app.documents.settings import Settings
    from app.documents.worker import work_once

    docs = Documents(Settings("sqlite:///" + str(root / "browser.sqlite"), root / "assets", sys.executable))
    docs.db.migrate()
    company = docs.company(FIXTURE_STOCK, FIXTURE_COMPANY)
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((40, 50), FIXTURE_COMPANY + " " + FIXTURE_STOCK + " 2024年年度报告\n"
                     "仅供浏览器自动化验收的合成资料。\n审计委员会履行监督职责，董事会披露履职信息。",
                     fontname="china-s", fontsize=12)
    annual = docs.upload(pdf.tobytes(), "browser-fixture-annual.pdf", "annual_report",
                         "浏览器验收合成年报（非研究资料）", company["id"], 2024)
    pdf.close()
    law = docs.upload("浏览器验收合成规则（非真实法规）。\n审计委员会应当监督内部控制与信息披露。".encode(),
                      "browser-fixture-law.txt", "regulation", "浏览器验收合成规则（非真实法规）")
    parses = []
    for version in (annual, law):
        docs.enqueue_parse(version["id"])
        job = work_once(docs)
        if job["status"] != "succeeded":
            raise RuntimeError("Browser source fixture failed: " + str(job["error_code"]))
        parses.append(job["result"]["parse_id"])
    Indexes(docs).enqueue(parses, "bm25_diagnostic", False)
    job = work_once(docs)
    if job["status"] != "succeeded":
        raise RuntimeError("Browser index fixture failed: " + str(job["error_code"]))
    return {"index_id": job["result"]["index_id"], "stock_code": FIXTURE_STOCK,
            "report_year": 2024, "model": FIXTURE_MODEL, "data_kind": "synthetic_browser_fixture"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve() if args.root else Path(tempfile.mkdtemp(prefix="dalun-browser-fixture-"))
    root.mkdir(parents=True, exist_ok=True)
    manifest = seed(root)
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    if not args.serve:
        return 0
    provider = ThreadingHTTPServer(("127.0.0.1", 0), FixtureProvider)
    provider.daemon_threads = True
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    env = dict(os.environ, PYTHONPATH=str(BACKEND), PYTHONUTF8="1",
               APP_DATABASE_URL="sqlite:///" + str(root / "browser.sqlite"), APP_DATA_ROOT=str(root / "assets"),
               MODEL_PYTHON=sys.executable, LLM_BASE_URL=f"http://127.0.0.1:{provider.server_port}/v1",
               LLM_API_KEY="synthetic-browser-fixture-key", LLM_MODEL=FIXTURE_MODEL,
               LLM_JSON_MODE="true", LLM_TEMPERATURE="")
    env.pop("DALUN_ENV_FILE", None)
    processes = [subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"], cwd=BACKEND, env=env),
                 subprocess.Popen([sys.executable, "-m", "app.documents.cli", "worker"], cwd=BACKEND, env=env)]
    stopped = threading.Event()
    def stop(*_):
        stopped.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopped.wait(0.3):
            if any(child.poll() is not None for child in processes):
                raise RuntimeError("Browser acceptance API or worker exited unexpectedly")
    finally:
        for child in processes:
            if child.poll() is None:
                child.terminate()
        for child in processes:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        provider.shutdown()
        provider.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
