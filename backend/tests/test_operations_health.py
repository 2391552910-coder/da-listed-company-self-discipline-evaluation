"""Health probes use real loopback HTTP; no model or external network is used."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from app.operations.health import api_health
from app.operations.preflight import http_checks, run_preflight


@pytest.fixture
def endpoint():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            calls.append(self.path)
            if self.path.startswith("/failure"):
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"sensitive-provider-detail-never-exposed")
                return
            body = {"healthy": True, "live_count": 1} if self.path == "/health/workers" else {"status": "ok"}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:" + str(server.server_port), calls
    server.shutdown()
    server.server_close()


def test_api_probe_only_checks_readiness(endpoint):
    base, calls = endpoint
    assert api_health(base) == {"healthy": True}
    assert calls == ["/health/ready"]


def test_http_only_preflight_requires_api_storage_and_worker_readiness(endpoint):
    base, calls = endpoint
    result = run_preflight(None, http_base=base, http_only=True)
    assert result["healthy"] is True
    assert calls == ["/health/live", "/health/ready", "/health/workers"]
    assert result["generation_network_verified"] is False
    assert all(check["status"] == "passed" for check in result["checks"])


def test_http_failure_sanitizes_response_body(endpoint):
    base, _ = endpoint
    result = api_health(base + "/failure")
    assert result["healthy"] is False
    assert "sensitive-provider" not in repr(result)
    checks = http_checks(base + "/failure")
    assert all(check["status"] == "failed" for check in checks)
    assert "sensitive-provider" not in repr(checks)


@pytest.mark.parametrize("url", ["http://user:private-key@127.0.0.1:1234", "http://127.0.0.1:1234?secret=value", "file:///tmp/private", "http://127.0.0.1:1234#secret", "http://[invalid"])
def test_probe_rejects_credential_urls_without_echoing_them(url):
    for result in (api_health(url), http_checks(url)[0]):
        assert result.get("healthy", False) is False
        assert result["error_code"] == "INVALID_HEALTH_URL"
        assert "private-key" not in repr(result) and "secret" not in repr(result)


def test_http_only_without_checks_is_not_healthy():
    result = run_preflight(None, http_only=True)
    assert result["healthy"] is False
