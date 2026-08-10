"""Central configuration. Every value comes from the environment / .env — never from code
branches on "profile". Switching from the Ollama dev box to the DGX Spark is an .env edit.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Defaults mirror .env.example so the app boots on a clean checkout."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE if ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Owner role: migrations, seeding, checkpointer DDL. Never used to serve requests
    # once APP_DATABASE_URL is set.
    database_url: str = "postgresql://echomind:echomind@localhost:5432/echomind"
    # Runtime role for the API (echomind_app: CRUD on echomind, read on infinity and
    # reporting, no DDL). Empty falls back to the owner so a clean dev checkout still
    # works; any deployment should set it.
    app_database_url: str = ""
    jwt_secret: str = "dev-only-change-me"

    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5:7b-instruct"
    judge_model: str = "qwen2.5:7b-instruct"
    embed_base_url: str = "http://localhost:11434/v1"
    embed_model: str = "bge-m3"

    reranker: str = "none"
    # Where the cross-encoder lives. Empty means "beside the embedder".
    reranker_base_url: str = ""
    gate_min_top_score: float = 0.45

    escalation_enabled: bool = False
    frontier_base_url: str = ""

    langfuse_enabled: bool = False
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    api_port: int = 8080
    mcp_port: int = 8090

    # --- Gate thresholds (spec 04). Overridable via env, defaults are the spec values. ---
    gate_min_coverage: float = 0.30
    gate_min_agreement: int = 2
    faithfulness_min: float = 0.70

    # --- Retrieval knobs ---
    retrieval_top_k: int = 8
    retrieval_candidates: int = 30
    embed_dim: int = 1024

    # --- SQL guard (spec 02) ---
    sql_max_limit: int = 1000
    sql_timeout_ms: int = 5000

    readonly_db_user: str = "echomind_readonly"
    readonly_db_password: str = "echomind_readonly"

    llm_timeout_s: float = 120.0
    llm_temperature: float = 0.0

    # Extra JSON merged into every chat request, for options that are model- or
    # engine-specific rather than ours — e.g. Qwen3's
    # {"chat_template_kwargs": {"enable_thinking": false}}. Kept in env so switching
    # models stays a config change (spec 06: code must not branch on profile).
    llm_extra_body: str = ""
    # Structured-output mode for judge calls: auto | json_schema | json_object | off.
    # "auto" probes the endpoint once and uses the strongest mode it supports.
    llm_structured_output: str = "auto"

    logs_dir: Path = Field(default=REPO_ROOT / "logs")

    @field_validator("reranker", mode="before")
    @classmethod
    def _strip_inline_comment(cls, v: object) -> object:
        # `.env.example` documents this field as `RERANKER=none  # none | bge`. Most dotenv
        # parsers strip that, but a stray export (e.g. from a Makefile `include`) would not.
        if isinstance(v, str) and "#" in v:
            v = v.split("#", 1)[0]
        return v.strip() if isinstance(v, str) else v

    @property
    def extra_body(self) -> dict:
        import json as _json

        if not self.llm_extra_body.strip():
            return {}
        try:
            parsed = _json.loads(self.llm_extra_body)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}

    @property
    def runtime_database_url(self) -> str:
        """What the API actually connects as."""
        return self.app_database_url or self.database_url

    @property
    def runs_as_owner(self) -> bool:
        return not self.app_database_url

    @property
    def readonly_database_url(self) -> str:
        """Same database, read-only role. All agent SQL runs through this."""
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(self.database_url)
        netloc = f"{self.readonly_db_user}:{self.readonly_db_password}@{parts.hostname}"
        if parts.port:
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
