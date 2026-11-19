"""Narrow, fail-soft transport for the optional measurement process."""
import json
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MeasurementGateway:
    def __init__(self, base_url):
        self.base_url = base_url.strip().rstrip("/")

    def _fetch(self, path):
        if not self.base_url:
            return {"status": "disabled"}
        try:
            url = urlsplit(self.base_url)
            if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
                    or url.query or url.fragment):
                raise ValueError("invalid measurement base URL")
            opener = build_opener(ProxyHandler({}), NoRedirect())
            with opener.open(self.base_url + "/internal/v1/measurement/" + path, timeout=2) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("measurement response exceeds size limit")
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("status") not in {"ready", "inputs_missing", "inputs_invalid", "not_found"}:
                raise ValueError("invalid measurement status")
            return data
        except (OSError, ValueError, RecursionError):
            return {"status": "unavailable"}

    def status(self):
        return self._fetch("status")

    def result(self, stock_code, report_year):
        result = self._fetch(f"results/{stock_code}?" + urlencode({"report_year": report_year}))
        if result["status"] != "ready":
            return result | {"result": None}
        values = result.get("result")
        if (not isinstance(values, dict) or values.get("stock_code") != stock_code
                or values.get("report_year") != report_year or type(values.get("score")) not in {int, float}
                or not 0 <= values["score"] <= 100.000000001 or not isinstance(values.get("dimensions"), dict)):
            return {"status": "unavailable", "result": None}
        return result
