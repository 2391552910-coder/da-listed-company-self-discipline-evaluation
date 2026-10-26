"""Small healthcheck commands used by Compose and native service managers."""
import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


def api_health(base_url, timeout=3):
    try:
        value = urlsplit(base_url)
    except ValueError:
        return {"healthy": False, "error_code": "INVALID_HEALTH_URL"}
    if value.scheme not in {"http", "https"} or not value.hostname or value.username or value.password or value.query or value.fragment:
        return {"healthy": False, "error_code": "INVALID_HEALTH_URL"}
    try:
        with urlopen(base_url.rstrip("/") + "/health/ready", timeout=timeout) as response:
            body = json.loads(response.read(65536))
        return {"healthy": body.get("status") == "ok"}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return {"healthy": False, "error_code": "API_NOT_READY"}


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    api = commands.add_parser("api")
    api.add_argument("--base-url", default="http://127.0.0.1:8000")
    worker = commands.add_parser("worker")
    worker.add_argument("--max-age-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    if args.command == "api":
        result = api_health(args.base_url)
    else:
        if not 10 <= args.max_age_seconds <= 600:
            parser.error("max-age-seconds must be between 10 and 600")
        try:
            from app.documents.service import Documents
            from app.documents.settings import Settings
            from app.documents.worker_health import worker_health
            result = worker_health(Documents(Settings.from_env()), args.max_age_seconds)
        except Exception:
            result = {"healthy": False, "error_code": "WORKER_REGISTRY_UNAVAILABLE"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
