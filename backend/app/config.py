from dataclasses import dataclass
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class Tier:
    llm_tokens_per_minute: int
    max_llm_tokens_per_job: int
    max_concurrent_jobs: int
    max_throttle_wait_s: int


TIERS: dict[str, Tier] = {
    "free": Tier(40_000, 200_000, 1, 90),
    "pro": Tier(200_000, 2_000_000, 3, 300),
    "enterprise": Tier(1_000_000, 10_000_000, 10, 600),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./dev.db"
    redis_url: str = ""
    secret_key: str = "change-me-in-production"
    token_encryption_key: str = ""
    access_token_minutes: int = 60 * 12
    cors_origins: str = "http://localhost:3000"
    public_app_url: str = "http://localhost:3000"
    public_api_url: str = "http://localhost:8000"
    default_tier: str = "free"
    auth_attempts_per_minute: int = 10

    workdir: str = "./data/repos"
    allowed_git_hosts: str = "github.com,gitlab.com"
    clone_timeout_s: int = 120
    max_repo_mb: int = 200
    max_file_bytes: int = 200_000
    max_files_for_llm: int = 60
    task_time_limit_s: int = 900
    repo_retention_hours: int = 72
    maintenance_interval_s: int = 600
    trusted_proxies: str = ""

    llm_provider_order: str = "anthropic,ollama"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    ollama_url: str = ""
    ollama_model: str = "llama3.1:8b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_api_url: str = "https://generativelanguage.googleapis.com"
    llm_timeout_s: float = 120.0
    llm_retries: int = 2
    circuit_failure_threshold: int = 3
    circuit_cooldown_s: int = 60
    embedding_dim: int = 256

    pii_engine: str = "regex"

    github_app_id: str = ""
    github_app_slug: str = ""
    github_app_private_key: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"

    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "ai-code-reviewer"
    prompts_dir: str = "prompts"
    prompt_cache_ttl_s: int = 30

    sandbox_enabled: bool = False
    sandbox_image: str = "python:3.12-slim"
    sandbox_repos_volume: str = ""

    @property
    def eager(self) -> bool:
        return not self.redis_url

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def host_allowlist(self) -> set[str]:
        return {h.strip().lower() for h in self.allowed_git_hosts.split(",") if h.strip()}

    @property
    def provider_order(self) -> list[str]:
        return [p.strip() for p in self.llm_provider_order.split(",") if p.strip()]

    @property
    def github_enabled(self) -> bool:
        return bool(self.github_app_id and self.github_client_id and self.github_client_secret and self.github_app_private_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def tier_for(name: str | None) -> Tier:
    return TIERS.get(name or "", TIERS["free"])
