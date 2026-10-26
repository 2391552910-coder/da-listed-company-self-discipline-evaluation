"""Bounded OpenAI-compatible HTTP transport. No SDK retries or mock fallback.

The child receives credentials only on stdin. Cancellation terminates the local
connection; the provider may already have consumed the request. The service's
durable call ledger must therefore retain an unknown outcome in that case.
"""
import json
import math
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit

from app.documents.common import DomainError, dump

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class Provider:
    def __init__(self, settings):
        self.settings = settings

    def identity(self, model=None):
        base = self.settings.llm_base_url.strip().rstrip("/")
        selected = model or self.settings.llm_model
        try:
            url = urlsplit(base)
            valid = (url.hostname and not url.username and not url.password and
                     not url.query and not url.fragment and
                     (url.scheme == "https" or
                      (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})))
            if not valid or not self.settings.llm_api_key or not isinstance(selected, str) or not selected.strip():
                raise ValueError()
            if len(selected) > 200 or any(ord(c) < 32 for c in selected):
                raise ValueError()
            temperature = self.settings.llm_temperature
            if temperature is not None and (isinstance(temperature, bool) or
                    not math.isfinite(temperature) or not 0 <= temperature <= 2):
                raise ValueError()
        except (ValueError, TypeError):
            raise DomainError("MODEL_PROFILE_UNAVAILABLE", "请配置有效的模型地址、模型名称和服务端密钥", 503)
        return {"provider": "openai_compatible", "protocol": "chat-completions-v1",
                "model": selected.strip(), "base_url": urlunsplit(url),
                "temperature": temperature,
                "response_format": "json_object" if self.settings.llm_json_mode else None}

    def complete(self, messages, model, max_output_tokens, temperature,
                 timeout_seconds, heartbeat):
        identity = self.identity(model)
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 32000:
            raise DomainError("INVALID_MODEL_REQUEST", "模型输出预算不合法", 422)
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise DomainError("INVALID_MODEL_REQUEST", "模型超时预算不合法", 422)
        if not isinstance(messages, list) or not messages or any(
            not isinstance(m, dict) or m.get("role") not in {"system", "user", "assistant"}
            or not isinstance(m.get("content"), str) for m in messages
        ):
            raise DomainError("INVALID_MODEL_REQUEST", "模型消息格式不合法", 422)
        request = {"model": identity["model"], "messages": messages,
                   "max_tokens": max_output_tokens, "stream": False}
        if temperature is not None:
            if isinstance(temperature, bool) or not isinstance(temperature, (float, int)) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
                raise DomainError("INVALID_MODEL_REQUEST", "模型采样参数不合法", 422)
            request["temperature"] = temperature
        if identity["response_format"]:
            request["response_format"] = {"type": identity["response_format"]}
        heartbeat()
        started = time.monotonic()
        envelope = dump({"url": identity["base_url"] + "/chat/completions",
                         "key": self.settings.llm_api_key,
                         "request": request, "timeout": timeout_seconds}).encode()
        # Temporary files hold only normalized responses, never the credential.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            child = subprocess.Popen([sys.executable, "-m", "app.evaluation.provider"],
                                     stdin=subprocess.PIPE, stdout=output, stderr=errors)
            try:
                child.stdin.write(envelope)
                child.stdin.close()
                while child.poll() is None:
                    heartbeat()
                    if time.monotonic() - started >= timeout_seconds:
                        raise DomainError("MODEL_TIMEOUT", "模型调用超时；服务端消费情况未知", 504)
                    if output.tell() > MAX_RESPONSE_BYTES:
                        raise DomainError("MODEL_RESPONSE_TOO_LARGE", "模型响应超过上限", 502)
                    time.sleep(min(0.2, max(0.001, timeout_seconds - (time.monotonic() - started))))
                heartbeat()
                if output.tell() > MAX_RESPONSE_BYTES:
                    raise DomainError("MODEL_RESPONSE_TOO_LARGE", "模型响应超过上限", 502)
                output.seek(0)
                try:
                    response = json.loads(output.read(MAX_RESPONSE_BYTES + 1))
                except (ValueError, UnicodeError):
                    raise DomainError("MODEL_TRANSPORT_FAILED", "模型连接失败；消费情况未知", 502)
                if child.returncode or response.get("error"):
                    allowed = {"MODEL_HTTP_ERROR", "MODEL_TIMEOUT", "MODEL_TRANSPORT_FAILED", "MODEL_INVALID_RESPONSE", "MODEL_RESPONSE_TOO_LARGE"}
                    error = response.get("error")
                    raise DomainError(error if error in allowed else "MODEL_TRANSPORT_FAILED",
                                      "模型服务未返回可用回答；消费情况以调用账本为准", 502)
                return response
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=2)


def _transport():
    import socket
    from urllib.error import HTTPError, URLError
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    class NoRedirect(HTTPRedirectHandler):
        # Never forward a bearer token to another URL, including a login page.
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        value = json.loads(sys.stdin.buffer.read())
        body = dump(value["request"]).encode()
        request = Request(value["url"], data=body,
                          headers={"Authorization": "Bearer " + value["key"],
                                   "Content-Type": "application/json", "Accept": "application/json",
                                   "User-Agent": "dalun-thesis/0.5"}, method="POST")
        with build_opener(NoRedirect()).open(request, timeout=value["timeout"]) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            return {"error": "MODEL_RESPONSE_TOO_LARGE"}
        result = json.loads(data)
        choice = result["choices"][0]
        text = choice["message"]["content"]
        if not isinstance(text, str):
            raise ValueError()
        raw_usage = result.get("usage") or {}
        usage = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            n = raw_usage.get(key)
            usage[key] = n if type(n) is int and n >= 0 else None
        return {"text": text, "usage": usage, "request_id": result.get("id"),
                "model": result.get("model"), "finish_reason": choice.get("finish_reason"),
                "mock": False}
    except HTTPError:
        return {"error": "MODEL_HTTP_ERROR"}
    except (TimeoutError, socket.timeout):
        return {"error": "MODEL_TIMEOUT"}
    except URLError:
        return {"error": "MODEL_TRANSPORT_FAILED"}
    except Exception:
        return {"error": "MODEL_INVALID_RESPONSE"}


if __name__ == "__main__":
    sys.stdout.buffer.write(dump(_transport()).encode())
