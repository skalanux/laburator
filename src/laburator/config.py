"""Application configuration via pydantic-settings.

Loads configuration from ``.env`` and environment variables.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class LaburatorConfig(BaseSettings):
    """Configuration for the job-search CLI pipeline.

    All values can be overridden via environment variables or a ``.env`` file.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── API credentials ─────────────────────────────────────────────────
    model_api_key: str = ""
    model_api_endpoint: str = "https://api.opencode.ai/v1"
    model_name: str = "big-pickle"
    job_search_api_key: str = ""
    job_search_source: str = "openninja"

    # Provider-specific credentials (only required when selected as source)
    apify_api_token: str = ""
    apify_linkedin_actor_id: str = "bebity/linkedin-jobs-scraper"
    apify_indeed_actor_id: str = "valig/indeed-jobs-scraper"

    # ── Paths ───────────────────────────────────────────────────────────
    cv_path: str = "cv.md"
    llmwiki_dir: str = "../llmwiki/wiki"
    output_dir: str = "~/.local/share/laburator/output"

    # ── Resolved properties ─────────────────────────────────────────────

    @property
    def resolved_cv_path(self) -> Path:
        """Return the absolute path to the CV file."""
        return Path(self.cv_path).expanduser().resolve()

    @property
    def resolved_llmwiki_dir(self) -> Path:
        """Return the absolute path to the LLM wiki directory."""
        return Path(self.llmwiki_dir).expanduser().resolve()

    @property
    def resolved_output_dir(self) -> Path:
        """Return the absolute path to the output directory."""
        return Path(self.output_dir).expanduser().resolve()
