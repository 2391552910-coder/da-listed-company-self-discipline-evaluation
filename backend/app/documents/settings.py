from dataclasses import dataclass, field
from pathlib import Path
import os
import ipaddress
import re
from urllib.parse import quote

BACKEND = Path(__file__).resolve().parents[2]


def database_url_from_env():
    explicit=os.getenv("APP_DATABASE_URL")
    if explicit:
        return explicit
    host=os.getenv("APP_DATABASE_HOST")
    if not host:
        return "postgresql+psycopg://postgres:postgres@localhost:5432/self_discipline"
    try:
        host=host.strip()
        if ":" in host:
            host="["+str(ipaddress.IPv6Address(host.strip("[]")))+"]"
        elif not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?",host):
            raise ValueError()
        port=os.getenv("APP_DATABASE_PORT","5432")
        name=os.getenv("APP_DATABASE_NAME","self_discipline")
        user=os.getenv("APP_DATABASE_USER","postgres")
        if not port.isascii() or not port.isdigit() or not 1<=int(port)<=65535:
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,63}",name) or not user or len(user)>128:
            raise ValueError()
    except ValueError:
        from app.documents.common import DomainError
        raise DomainError("INVALID_DATABASE_CONFIG","数据库连接字段不合法；未输出凭据",422)
    password=os.getenv("APP_DATABASE_PASSWORD","")
    return f"postgresql+psycopg://{quote(user,safe='')}:{quote(password,safe='')}@{host}:{int(port)}/{name}"

@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    data_root: Path
    model_python: str
    max_upload_bytes: int = 100 * 1024 * 1024
    model_timeout: int = 3600
    reference_tokenizer_file: str | None = None
    reference_tokenizer_sha256: str | None = None
    llm_base_url: str = ""
    llm_api_key: str = field(default="", repr=False)
    llm_model: str = ""
    llm_temperature: float | None = None
    llm_json_mode: bool = True

    @classmethod
    def from_env(cls):
        # Only an explicitly chosen env file is loaded; never import xiaolun.config.
        env_file = os.getenv("DALUN_ENV_FILE")
        if env_file:
            from dotenv import load_dotenv
            load_dotenv(Path(env_file).resolve(), override=False)
        return cls(
            database_url_from_env(),
            Path(os.getenv("APP_DATA_ROOT", str(BACKEND / "runtime"))).resolve(),
            os.getenv("MODEL_PYTHON", "python"),
            int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024))),
            int(os.getenv("MODEL_TIMEOUT_SECONDS", "3600")),
            os.getenv("REFERENCE_TOKENIZER_FILE"),
            os.getenv("REFERENCE_TOKENIZER_SHA256"),
            os.getenv("LLM_BASE_URL", ""),
            os.getenv("LLM_API_KEY", ""),
            os.getenv("LLM_MODEL", ""),
            float(os.environ["LLM_TEMPERATURE"]) if os.getenv("LLM_TEMPERATURE") else None,
            os.getenv("LLM_JSON_MODE", "true").lower() == "true",
        )
