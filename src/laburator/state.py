"""LangGraph state definition for the job-search pipeline."""
from __future__ import annotations

from typing import Optional, TypedDict


class State(TypedDict):
    """Shared state passed between pipeline nodes.

    Each node reads from and writes to this state as it moves through the
    pipeline.
    """

    query: str                                # Search query string
    mode: str                                 # "fetch" | "process"
    num_pages: int                            # Number of result pages to fetch
    remote_only: bool                         # Only remote jobs
    country: str                              # Two-letter country code
    refresh: bool                             # Re-fetch even if cached
    all_jobs: list[dict]                      # All fetched job listings
    cv_context: str                           # Content of cv.md
    llmwiki_context: str                      # Content from llmwiki .md files
    outputs: dict[str, str]                   # skill_name -> generated content
    run_date: str                             # ISO date string for this run
    errors: list[str]                         # Accumulated errors
