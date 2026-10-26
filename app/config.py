import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    mode: str = "demo"
    data_dir: Path = ROOT / "data"
    database_url: str = ""
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    scheduler_enabled: bool = True
    agent_timeout: int = 180
    max_tool_calls: int = 24
    summary_review_enabled: bool = True
    summary_review_source_chars: int = 5000
    working_context_enabled: bool = True
    prefilter_enabled: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_security: str = "starttls"
    bash_executable: str = ""

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        mode = os.getenv("APP_MODE", "demo")
        if mode not in {"demo", "live"}:
            raise ValueError("APP_MODE must be demo or live")
        raw_dir = Path(os.getenv("DATA_DIR", "data"))
        return cls(
            mode=mode,
            data_dir=(raw_dir if raw_dir.is_absolute() else ROOT / raw_dir).resolve(),
            database_url=os.getenv("DATABASE_URL", ""),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            scheduler_enabled=os.getenv("SCHEDULER_ENABLED", "true").lower() == "true",
            agent_timeout=int(os.getenv("AGENT_TIMEOUT_SECONDS", "180")),
            max_tool_calls=int(os.getenv("MAX_TOOL_CALLS", "24")),
            summary_review_enabled=os.getenv("SUMMARY_REVIEW_ENABLED", "true").lower() == "true",
            summary_review_source_chars=int(os.getenv("SUMMARY_REVIEW_SOURCE_CHARS", "5000")),
            working_context_enabled=os.getenv("WORKING_CONTEXT_ENABLED", "true").lower() == "true",
            prefilter_enabled=os.getenv("PREFILTER_ENABLED", "true").lower() == "true",
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_user=os.getenv("SMTP_USER", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", ""),
            smtp_security=os.getenv("SMTP_SECURITY", "starttls"),
            bash_executable=os.getenv("BASH_EXECUTABLE", ""),
        )
