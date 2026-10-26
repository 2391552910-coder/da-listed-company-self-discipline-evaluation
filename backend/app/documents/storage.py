from pathlib import Path
import os
from app.documents.common import DomainError, sha, uid

class Storage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        if not isinstance(key, str) or not key or Path(key).is_absolute():
            raise DomainError("INVALID_STORAGE_KEY", "存储键无效")
        candidate = (self.root / key).resolve()
        if candidate == self.root or not candidate.is_relative_to(self.root):
            raise DomainError("INVALID_STORAGE_KEY", "存储键越界")
        return candidate

    def write(self, key, data):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise DomainError("IMMUTABLE_CONFLICT", "不可变文件已存在", 409)
            return
        temporary = path.with_name(path.name + "." + uid() + ".tmp")
        try:
            temporary.write_bytes(data)
            # Never replace a different immutable object.
            with path.open("xb") as output:
                output.write(temporary.read_bytes())
        except FileExistsError:
            if path.read_bytes() != data:
                raise DomainError("IMMUTABLE_CONFLICT", "并发文件内容冲突", 409)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, key, expected=None):
        path = self.path(key)
        if not path.is_file():
            raise DomainError("SOURCE_UNAVAILABLE", "原文件或制品不可用", 404)
        data = path.read_bytes()
        if expected and sha(data) != expected:
            raise DomainError("HASH_MISMATCH", "文件完整性校验失败", 409)
        return data
