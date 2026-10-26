import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import uuid4

class DomainError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status

def uid():
    return str(uuid4())

def now():
    return datetime.now(timezone.utc).isoformat()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def code(value):
    value = str(value).strip().upper()
    if not re.fullmatch(r"[0-9]{6}(?:\.(?:SH|SZ|BJ))?", value):
        raise DomainError("INVALID_STOCK_CODE", "股票代码须为六位数字，可附.SH/.SZ/.BJ")
    return value[:6]
