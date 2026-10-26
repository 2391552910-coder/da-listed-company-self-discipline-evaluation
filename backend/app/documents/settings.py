from dataclasses import dataclass, field
from pathlib import Path
import os

BACKEND = Path(__file__).resolve().parents[2]

@dataclass(frozen=True)
class Settings:
    database_url: str
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
            os.getenv("APP_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/self_discipline"),
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
