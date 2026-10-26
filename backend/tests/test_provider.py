"""Real loopback HTTP transport tests; responses are explicit test fixtures."""
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading
import time

import pytest

from app.documents.common import DomainError
from app.documents.settings import Settings
from app.evaluation.provider import Provider


@pytest.fixture
def endpoint(tmp_path):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers.get("Authorization"), request))
            if request["model"] == "slow":
                time.sleep(1)
            if request["model"] == "redirect":
                self.send_response(307)
                self.send_header("Location", "/leak")
                self.end_headers()
                return
            if request["model"] == "failure":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"provider-secret-never-exposed")
                return
            data = json.dumps({"id": "fixture-request", "model": request["model"],
                               "choices": [{"message": {"content": '{"evaluation":"fixture"}'},
                                            "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}).encode()
            self.send_response(200)
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = Settings("sqlite:///"+str(tmp_path/"test.sqlite"), tmp_path, sys.executable,
                        llm_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                        llm_api_key="fixture-key", llm_model="fixture")
    yield settings, received
    server.shutdown()
    server.server_close()


def call(settings, heartbeat=lambda: None, timeout=5, model="fixture"):
    return Provider(settings).complete([{"role": "user", "content": "Return JSON"}], model,
                                       100, None, timeout, heartbeat)


def test_complete_uses_one_request_and_safe_identity(endpoint):
    settings, received = endpoint
    result = call(settings)
    assert result["mock"] is False and result["usage"]["total_tokens"] == 15
    assert len(received) == 1
    path, key, request = received[0]
    assert path == "/v1/chat/completions" and key == "Bearer fixture-key"
    assert request["response_format"] == {"type": "json_object"}
    assert "temperature" not in request and request["max_tokens"] == 100
    assert "fixture-key" not in repr(settings) + repr(Provider(settings).identity()) + repr(result)


@pytest.mark.parametrize("base", ["http://example.com/v1", "https://key@example.com/v1", "https://example.com/v1?api_key=key", "https://example.com/v1#key", ""])
def test_invalid_endpoint_does_not_disclose_settings(endpoint, base):
    settings, _ = endpoint
    with pytest.raises(DomainError) as error:
        Provider(replace(settings, llm_base_url=base)).identity()
    assert error.value.code == "MODEL_PROFILE_UNAVAILABLE"
    assert "fixture-key" not in str(error.value)


@pytest.mark.parametrize("model", ["failure", "redirect"])
def test_failure_is_sanitized_and_never_retried(endpoint, model):
    settings, received = endpoint
    with pytest.raises(DomainError) as error:
        call(settings, model=model)
    assert error.value.code == "MODEL_HTTP_ERROR"
    assert len(received) == 1 and received[0][0] != "/leak"
    assert "provider-secret" not in str(error.value)


def test_timeout_closes_local_request_and_does_not_retry(endpoint):
    settings, received = endpoint
    started = time.monotonic()
    with pytest.raises(DomainError) as error:
        call(settings, timeout=0.5, model="slow")
    assert error.value.code == "MODEL_TIMEOUT"
    assert time.monotonic() - started < 2
    assert len(received) <= 1


def test_cancel_is_checked_during_wait(endpoint):
    settings, received = endpoint
    calls = []
    def heartbeat():
        calls.append(1)
        if received:
            raise DomainError("TASK_CANCELLED", "fixture cancel", 409)
    with pytest.raises(DomainError) as error:
        call(settings, heartbeat, model="slow")
    assert error.value.code == "TASK_CANCELLED" and len(received) == 1
    assert len(calls) > 1
