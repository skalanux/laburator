"""Typer CLI for laburator.

Provides ``search``, ``synth``, ``run``, ``list``, and ``config``
commands that drive the job-search pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import typer

from laburator.config import LaburatorConfig
from laburator.pipeline import JobSearchPipeline
from laburator.skills import build_user_messages, load_skill, response_format

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="laburator",
    help="CLI job-search assistant powered by LangGraph.",
)


# ── Commands ────────────────────────────────────────────────────────────


@app.command()
def search(
    query: str = typer.Argument(..., help="Job title or keywords to search for"),
    pages: int = typer.Option(1, "--pages", "-p", help="Number of result pages to fetch"),
    remote: bool = typer.Option(False, "--remote", "-r", help="Only remote jobs"),
    country: str = typer.Option("us", "--country", "-c", help="Two-letter country code"),
) -> None:
    """Fetch job listings and cache them (fetch-only mode).

    Runs the pipeline in ``fetch`` mode — no LLM calls are made.
    Results are cached to the output directory for later use.
    """
    config = _load_config()

    pipeline = JobSearchPipeline(config)
    initial_state = {
        "query": query,
        "mode": "fetch",
        "num_pages": pages,
        "remote_only": remote,
        "country": country,
        "refresh": False,
    }

    result = asyncio.run(pipeline.run(initial_state))
    jobs = result.get("all_jobs", [])
    errors = result.get("errors", [])

    if errors:
        for err in errors:
            typer.echo(f"  ⚠ {err}", err=True)

    typer.echo(f"\nFound {len(jobs)} job(s) for '{query}':\n")
    for i, job in enumerate(jobs, 1):
        _print_job_summary(i, job)

    if not jobs:
        typer.echo("No jobs found. Try a different query or check your API key.")


@app.command(name="synth")
def synthesize(
    query: str = typer.Argument(..., help="Job title or keywords to search for"),
    pages: int = typer.Option(1, "--pages", "-p", help="Number of result pages to fetch"),
    remote: bool = typer.Option(False, "--remote", "-r", help="Only remote jobs"),
    country: str = typer.Option("us", "--country", "-c", help="Two-letter country code"),
    refresh: bool = typer.Option(
        False, "--refresh", "-f", help="Re-fetch jobs even if cached",
    ),
) -> None:
    """Run the full pipeline: fetch, analyze, and generate output files.

    Fetches job listings, loads CV and wiki context, runs all skills
    (job synthesis, tailored CV, cover letter, interview questions) via
    the LLM, and saves the results to the output directory.
    """
    config = _load_config()
    asyncio.run(_run_synthesize(config, query, pages, remote, country, refresh))


@app.command()
def run(
    job_id: str = typer.Argument(..., help="Job ID to process"),
    skill: str = typer.Option(
        ...,
        "--skill",
        "-s",
        help="Skill to run on the cached job",
        case_sensitive=False,
    ),
) -> None:
    """Run a single skill on a cached job.

    Loads the job from the cache and runs the specified skill through the
    LLM. The skill must be one of: ``createcv``, ``presentationletter``,
    ``interviewquestions``, ``jobsynthesis``.
    """
    config = _load_config()

    # Find job in cache
    cache_root = config.resolved_output_dir / "cache"
    if not cache_root.is_dir():
        typer.echo("No cached jobs found. Run 'laburator search' first.", err=True)
        raise typer.Exit(1)

    job = _find_cached_job(cache_root, job_id)
    if job is None:
        typer.echo(f"Job with ID '{job_id}' not found in cache.", err=True)
        raise typer.Exit(1)

    # Load context
    cv_context = _read_cv(config.resolved_cv_path)
    wiki_context = _read_wiki(config.resolved_llmwiki_dir)

    pipeline = JobSearchPipeline(config)

    try:
        system_prompt = load_skill(skill)
        user_messages = build_user_messages(skill, job, cv_context, wiki_context)
        fmt = response_format(skill)

        result_text = asyncio.run(
            pipeline.llm.generate(
                system_prompt=system_prompt,
                user_messages=user_messages,
                response_format=fmt,
            )
        )

        typer.echo(result_text)

    except FileNotFoundError:
        typer.echo(
            f"Unknown skill '{skill}'. Available: createcv, presentationletter, "
            f"interviewquestions, jobsynthesis",
            err=True,
        )
        raise typer.Exit(1)
    finally:
        asyncio.run(pipeline.aclose())


@app.command()
def list() -> None:
    """Show cached search results.

    Lists all previously cached job searches and their job counts.
    """
    config = _load_config()
    cache_root = config.resolved_output_dir / "cache"

    if not cache_root.is_dir():
        typer.echo("No cached searches found.")
        return

    searches = sorted(cache_root.iterdir())
    for search_dir in searches:
        if not search_dir.is_dir():
            continue
        jobs_file = search_dir / "jobs.json"
        if not jobs_file.exists():
            continue
        try:
            jobs = json.loads(jobs_file.read_text(encoding="utf-8"))
            query = search_dir.name.replace("-", " ").title()
            typer.echo(f"  {search_dir.name}: {len(jobs)} job(s)")
            for i, job in enumerate(jobs[:5], 1):
                title = job.get("job_title", "Unknown")
                employer = job.get("employer_name", "Unknown")
                job_id = job.get("job_id", "")
                typer.echo(f"    {i}. {title} @ {employer}  [{job_id}]")
            if len(jobs) > 5:
                typer.echo(f"    ... and {len(jobs) - 5} more")
        except Exception as exc:
            typer.echo(f"  {search_dir.name}: (error reading cache — {exc})")


@app.command()
def config() -> None:
    """Show current runtime configuration.

    Displays all config values with the API key partially masked.
    """
    cfg = LaburatorConfig()

    key = cfg.model_api_key
    if len(key) > 12:
        masked = key[:8] + "…" + key[-4:]
    elif key:
        masked = "(set)"
    else:
        masked = "(not set)"

    js_key = cfg.job_search_api_key
    if len(js_key) > 8:
        js_masked = js_key[:4] + "…" + js_key[-4:]
    elif js_key:
        js_masked = "(set)"
    else:
        js_masked = "(not set)"

    typer.echo("Laburator Configuration")
    typer.echo("========================")
    typer.echo(f"  Model:                 {cfg.model_name}")
    typer.echo(f"  API Endpoint:          {cfg.model_api_endpoint}")
    typer.echo(f"  Model API Key:         {masked}")
    typer.echo(f"  Job Search API Key:    {js_masked}")
    typer.echo(f"  CV path:               {cfg.resolved_cv_path}")
    typer.echo(f"  LLM Wiki dir:          {cfg.resolved_llmwiki_dir}")
    typer.echo(f"  Output dir:            {cfg.resolved_output_dir}")


# ── Helpers ─────────────────────────────────────────────────────────────


def _load_config() -> LaburatorConfig:
    """Load and validate the configuration."""
    config = LaburatorConfig()
    if not config.job_search_api_key:
        typer.echo(
            "Warning: JOB_SEARCH_API_KEY is not configured. "
            "Set it in a .env file or as an environment variable.\n",
            err=True,
        )
    return config


async def _run_synthesize(
    config: LaburatorConfig,
    query: str,
    pages: int = 1,
    remote: bool = False,
    country: str = "us",
    refresh: bool = False,
) -> None:
    """Run the full pipeline asynchronously."""
    pipeline = JobSearchPipeline(config)
    initial_state = {
        "query": query,
        "mode": "process",
        "num_pages": pages,
        "remote_only": remote,
        "country": country,
        "refresh": refresh,
    }

    try:
        result = await pipeline.run(initial_state)
        jobs = result.get("all_jobs", [])
        outputs = result.get("outputs", {})
        errors = result.get("errors", [])
        run_date = result.get("run_date", "")

        if errors:
            for err in errors:
                typer.echo(f"  ⚠ {err}", err=True)

        typer.echo(f"\nProcessed {len(jobs)} job(s) for '{query}':\n")
        for i, job in enumerate(jobs, 1):
            _print_job_summary(i, job)

        output_dir = config.resolved_output_dir / run_date
        skill_count = len(outputs)
        typer.echo(f"\nGenerated {skill_count} output(s) → {output_dir}")
    finally:
        await pipeline.aclose()


def _print_job_summary(index: int, job: dict) -> None:
    """Print a one-line summary for a job listing."""
    title = job.get("job_title", "Unknown")
    employer = job.get("employer_name", "Unknown")
    location = job.get("job_location", "Remote" if job.get("job_is_remote") else "Unknown")
    salary = job.get("job_salary", "")
    job_id = job.get("job_id", "")

    parts = [f"  {index:>2}. {title} @ {employer}"]
    if location:
        parts.append(f"  📍 {location}")
    if salary:
        parts.append(f"  💰 {salary}")

    typer.echo("".join(parts))
    typer.echo(f"      ID: {job_id}")


def _find_cached_job(cache_root: Path, job_id: str) -> dict | None:
    """Search all cached searches for a job with the given ID."""
    for search_dir in cache_root.iterdir():
        if not search_dir.is_dir():
            continue
        jobs_file = search_dir / "jobs.json"
        if not jobs_file.exists():
            continue
        try:
            jobs = json.loads(jobs_file.read_text(encoding="utf-8"))
            for job in jobs:
                if job.get("job_id") == job_id:
                    return job
        except Exception:
            continue
    return None


def _read_cv(cv_path: Path) -> str:
    """Read the CV markdown file."""
    if cv_path.exists():
        try:
            return cv_path.read_text(encoding="utf-8")
        except Exception:
            pass
    return ""


def _read_wiki(wiki_dir: Path) -> str:
    """Read all markdown files from the wiki directory."""
    if not wiki_dir.is_dir():
        return ""
    parts: list[str] = []
    for md_file in sorted(wiki_dir.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            parts.append(f"## {md_file.stem}\n\n{content}")
        except Exception:
            continue
    return "\n\n---\n\n".join(parts)


# ── Entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
