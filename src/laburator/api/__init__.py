"""Job-search API client package."""

from __future__ import annotations

from typing import TYPE_CHECKING

from laburator.api.adapter import JobSearchAdapter
from laburator.api.apify_indeed import ApifyIndeedJobSearchClient
from laburator.api.apify_linkedin import ApifyLinkedInJobSearchClient
from laburator.api.arbeitnow import ArbeitnowJobSearchClient
from laburator.api.base import JobSearchClient
from laburator.api.dtos import JobSearchFilters, JobSearchResult
from laburator.api.jobicy import JobicyJobSearchClient
from laburator.api.openninja import OpenNinjaJobSearchClient
from laburator.api.remotive import RemotiveJobSearchClient

if TYPE_CHECKING:
    from laburator.config import LaburatorConfig


def create_search_adapter(source: str, config: "LaburatorConfig") -> JobSearchAdapter:
    """Build a JobSearchAdapter for the requested source."""
    src = (source or getattr(config, "job_search_source", "openninja") or "openninja").lower().strip()
    if src == "openninja":
        if not getattr(config, "job_search_api_key", ""):
            raise ValueError("JOB_SEARCH_API_KEY is not configured for source 'openninja'.")
        return JobSearchAdapter(OpenNinjaJobSearchClient(config.job_search_api_key))
    if src == "arbeitnow":
        return JobSearchAdapter(ArbeitnowJobSearchClient())
    if src == "remotive":
        return JobSearchAdapter(RemotiveJobSearchClient())
    if src == "jobicy":
        return JobSearchAdapter(JobicyJobSearchClient())
    if src == "apify_linkedin":
        if not getattr(config, "apify_api_token", ""):
            raise ValueError("APIFY_API_TOKEN is not configured for source 'apify_linkedin'.")
        actor = getattr(config, "apify_linkedin_actor_id", "bebity/linkedin-jobs-scraper") or "bebity/linkedin-jobs-scraper"
        return JobSearchAdapter(ApifyLinkedInJobSearchClient(config.apify_api_token, actor_id=actor))
    if src == "apify_indeed":
        if not getattr(config, "apify_api_token", ""):
            raise ValueError("APIFY_API_TOKEN is not configured for source 'apify_indeed'.")
        actor = getattr(config, "apify_indeed_actor_id", "valig/indeed-jobs-scraper") or "valig/indeed-jobs-scraper"
        return JobSearchAdapter(ApifyIndeedJobSearchClient(config.apify_api_token, actor_id=actor))
    raise ValueError(
        f"Unknown search source '{source}'. Available: openninja, arbeitnow, remotive, jobicy, apify_linkedin, apify_indeed."
    )


__all__ = [
    "ApifyIndeedJobSearchClient",
    "ApifyLinkedInJobSearchClient",
    "ArbeitnowJobSearchClient",
    "JobSearchAdapter",
    "JobSearchClient",
    "JobSearchFilters",
    "JobSearchResult",
    "JobicyJobSearchClient",
    "OpenNinjaJobSearchClient",
    "RemotiveJobSearchClient",
    "create_search_adapter",
]
