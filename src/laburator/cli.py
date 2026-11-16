"""Typer CLI for laburator.

Provides ``search``, ``synth``, ``run``, ``generar-cv``, ``list``, and
``config`` commands that drive the job-search pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, List

import typer

from laburator.api import create_search_adapter
from laburator.config import LaburatorConfig
from laburator.llm.service import LLMService
from laburator.pipeline import JobSearchPipeline, _slugify as _pipeline_slugify
from laburator.skills import (
    SKILL_FILENAMES,
    SKILL_NAMES,
    build_cv_to_tex_messages,
    build_proposal_messages,
    build_user_messages,
    load_skill,
    response_format,
)
from laburator.store import VALID_DECISIONS, JobIndexStore

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
    date_posted: str = typer.Option("all", "--date-posted", help="Date posted filter (e.g. all, today, 3days, week, month)"),
    employment_types: str = typer.Option("", "--employment-types", "-e", help="Comma-separated employment types (e.g. FULLTIME,CONTRACTOR)"),
    page: int = typer.Option(1, "--page", help="Result page number"),
    source: str = typer.Option("openninja", "--source", help="Search source/provider"),
) -> None:
    """Fetch job listings and cache/index them (fetch-only mode).

    Runs the pipeline in ``fetch`` mode — no LLM calls are made.
    Results are cached to the output directory and saved to the search index.
    """
    config = _load_config()

    pipeline = JobSearchPipeline(config)
    initial_state = {
        "query": query,
        "mode": "fetch",
        "num_pages": pages,
        "remote_only": remote,
        "country": country,
        "date_posted": date_posted,
        "employment_types": employment_types,
        "page": page,
        "source": source,
        "refresh": False,
    }

    result = asyncio.run(pipeline.run(initial_state))
    jobs = result.get("all_jobs", [])
    errors = result.get("errors", [])

    if errors:
        for err in errors:
            typer.echo(f"  ⚠ {err}", err=True)

    typer.echo(f"\nFound {len(jobs)} job(s) for '{query}' (source: {source}):\n")
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
    date_posted: str = typer.Option("all", "--date-posted", help="Date posted filter"),
    employment_types: str = typer.Option("", "--employment-types", "-e", help="Comma-separated employment types"),
    page: int = typer.Option(1, "--page", help="Result page number"),
    source: str = typer.Option("openninja", "--source", help="Search source/provider"),
    refresh: bool = typer.Option(
        False, "--refresh", "-r", help="Re-fetch jobs even if cached",
    ),
    force: bool = typer.Option(
        False, "--force-process", help="Regenerate outputs even if they already exist",
    ),
) -> None:
    """Run the full pipeline: fetch, analyze, and generate output files.

    Fetches job listings, loads CV and wiki context, runs all skills
    (job synthesis, tailored CV, cover letter, interview questions) via
    the LLM, and saves the results to the output directory.
    """
    config = _load_config()
    asyncio.run(
        _run_synthesize(
            config,
            query,
            pages,
            remote,
            country,
            date_posted,
            employment_types,
            page,
            source,
            refresh,
            force,
        )
    )


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

    job = _find_job(config, job_id)
    if job is None:
        typer.echo(f"Job with ID/key '{job_id}' not found in cache or index.", err=True)
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


@app.command(name="generar-cv")
def generar_cv(
    proposal: str = typer.Argument(..., help="Propuesta o descripción de la oportunidad"),
    tips: str = typer.Option(
        "",
        "--tips",
        "-t",
        help="Consejos o puntos de énfasis para el CV (qué resaltar, qué experiencia destacar)",
    ),
) -> None:
    """Genera un CV adaptado a una propuesta específica (markdown + LaTeX).

    Toma una propuesta (descripción de trabajo, proyecto, oportunidad) y consejos
    del usuario, y genera un CV personalizado en markdown y LaTeX (moderncv) usando
    tu cv.md como base.

    Ejemplo:
        laburator generar-cv "Busco desarrollador Python senior para..." --tips "Resaltar experiencia con Django y AWS"
    """
    config = _load_config()
    cv_context = _read_cv(config.resolved_cv_path)
    wiki_context = _read_wiki(config.resolved_llmwiki_dir)

    # Extract personal info from cv.md for the LaTeX preamble
    personal_info = _extract_personal_info(cv_context)

    pipeline = JobSearchPipeline(config)

    async def _generate_both() -> tuple[str, str]:
        """Generate both markdown and LaTeX CVs in a single event loop."""
        try:
            # Step 1: Generate markdown CV
            system_prompt = load_skill("generarcv")
            user_messages = build_proposal_messages(proposal, tips, cv_context, wiki_context)

            print("📄 Generando CV en markdown...", end=" ", flush=True)
            cv_md = await pipeline.llm.generate(
                system_prompt=system_prompt,
                user_messages=user_messages,
                response_format="text",
            )
            print("✓", flush=True)

            # Step 2: Generate LaTeX CV from the markdown
            system_prompt_tex = load_skill("generarcvtex")
            user_messages_tex = build_cv_to_tex_messages(cv_md, personal_info)

            print("📝 Generando CV en LaTeX...", end=" ", flush=True)
            cv_tex = await pipeline.llm.generate(
                system_prompt=system_prompt_tex,
                user_messages=user_messages_tex,
                response_format="text",
            )
            print("✓", flush=True)

            return cv_md, cv_tex
        finally:
            await pipeline.aclose()

    try:
        cv_markdown, cv_latex = asyncio.run(_generate_both())

        # Save both files
        from datetime import date
        run_date = date.today().isoformat()
        output_dir = config.resolved_output_dir / run_date / "generar-cv"
        output_dir.mkdir(parents=True, exist_ok=True)

        md_path = output_dir / "cv.md"
        md_path.write_text(cv_markdown, encoding="utf-8")

        tex_path = output_dir / "cv.tex"
        tex_path.write_text(cv_latex, encoding="utf-8")

        typer.echo(cv_markdown)
        typer.echo(f"\n💾 Archivos guardados:", err=True)
        typer.echo(f"   Markdown: {md_path}", err=True)
        typer.echo(f"   LaTeX:    {tex_path}", err=True)
    except:
        pass


@app.command()
def list() -> None:
    """Show cached and indexed search results."""
    config = _load_config()
    cache_root = config.resolved_output_dir / "cache"
    store = JobIndexStore(config.resolved_output_dir)

    shown_any = False

    searches = store.list_searches(limit=50)
    if searches:
        shown_any = True
        typer.echo("Indexed searches:")
        for rec in searches:
            sid = rec.get("id", "")
            query = rec.get("query", "")
            src = rec.get("source", "")
            count = rec.get("count", 0)
            created = rec.get("created_at", "")
            typer.echo(f"  [{sid}] {query} ({src}) — {count} job(s) @ {created}")
        typer.echo("")
        jobs = store.list_jobs(limit=20)
        if jobs:
            typer.echo("Recent indexed jobs:")
            for i, job in enumerate(jobs, 1):
                title = job.get("job_title", "Unknown")
                employer = job.get("employer_name", "Unknown")
                key = job.get("_job_key") or job.get("job_id", "")
                dec = job.get("_decision", "pending")
                score = job.get("_score")
                extra = f" score={score}" if score is not None else ""
                typer.echo(f"    {i}. {title} @ {employer} [{key}] ({dec}{extra})")
            typer.echo("")

    if cache_root.is_dir():
        legacy = sorted([p for p in cache_root.iterdir() if p.is_dir()])
        if legacy:
            shown_any = True
            typer.echo("Legacy cache:")
            for search_dir in legacy:
                jobs_file = search_dir / "jobs.json"
                if not jobs_file.exists():
                    continue
                try:
                    jobs = json.loads(jobs_file.read_text(encoding="utf-8"))
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

    if not shown_any:
        typer.echo("No cached or indexed searches found.")


@app.command()
def decide(
    job_id: str = typer.Argument(..., help="Job ID or job key to update"),
    decision: str = typer.Argument(..., help="Decision: pending, interested, or rejected"),
    notes: str = typer.Option("", "--notes", "-n", help="Optional notes"),
) -> None:
    """Set curation decision for an indexed job."""
    if decision not in VALID_DECISIONS:
        typer.echo(f"Invalid decision '{decision}'. Use one of: {', '.join(sorted(VALID_DECISIONS))}", err=True)
        raise typer.Exit(1)
    config = _load_config()
    store = JobIndexStore(config.resolved_output_dir)
    job = store.get_job(job_id)
    if job is None:
        typer.echo(f"Job '{job_id}' not found in index.", err=True)
        raise typer.Exit(1)
    key = job.get("_job_key") or job_id
    entry = store.set_decision(key, decision, notes)
    typer.echo(f"Decision set: {key} -> {entry.get('decision')}")


@app.command()
def enrich(
    job_id: str = typer.Argument(..., help="Job ID or job key to enrich"),
    source: str = typer.Option("", "--source", help="Search source/provider (defaults to job's source or config)"),
) -> None:
    """Fetch full job details on demand and merge into the index (no LLM)."""
    config = _load_config()
    store = JobIndexStore(config.resolved_output_dir)
    job = store.get_job(job_id)
    if job is None:
        typer.echo(f"Job '{job_id}' not found in index.", err=True)
        raise typer.Exit(1)
    key = job.get("_job_key") or job_id
    src = source or job.get("_source") or getattr(config, "job_search_source", "openninja")
    provider_job_id = str(job.get("job_id") or job_id)
    # if key is prefixed as source:id and job_id missing, try to extract id
    if ":" in provider_job_id and provider_job_id.startswith(src + ":"):
        provider_job_id = provider_job_id.split(":", 1)[1]

    try:
        adapter = create_search_adapter(src, config)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)

    async def _do() -> dict:
        try:
            details = await adapter.job_details(provider_job_id)
            if not isinstance(details, dict):
                details = {"raw_details": details}
            updated = store.merge_job_details(key, details)
            return updated or {}
        finally:
            try:
                await adapter.close()
            except Exception:
                pass

    typer.echo(f"Enriching {key} from source '{src}'...", nl=False)
    try:
        updated = asyncio.run(_do())
    except Exception as exc:
        typer.echo(f" failed ✗ ({_friendly_error(exc)})", err=True)
        raise typer.Exit(1)
    desc_len = len((updated.get("job_description") or "") if updated else "")
    typer.echo(f" done ✓ (description chars: {desc_len})")


@app.command(name="process-interested")
def process_interested(
    skills: List[str] = typer.Option(..., "--skill", "-s", help="Skill to run (repeatable). Required explicit selection."),
    force: bool = typer.Option(False, "--force", "-f", help="Regenerate even if output already exists"),
) -> None:
    """Process only jobs marked as interested, running only selected skills (explicit, on-demand)."""
    config = _load_config()
    for skill in skills:
        if skill not in SKILL_NAMES:
            typer.echo(f"Unknown skill '{skill}'. Available: {', '.join(SKILL_NAMES)}", err=True)
            raise typer.Exit(1)

    store = JobIndexStore(config.resolved_output_dir)
    jobs = store.list_jobs(decision="interested", limit=1000)
    if not jobs:
        typer.echo("No interested jobs found in index.")
        return

    run_date = date.today().isoformat()
    typer.echo(f"Processing {len(jobs)} interested job(s) with skills: {', '.join(skills)}")
    generated = 0
    skipped = 0
    failed = 0

    async def _run_all() -> None:
        nonlocal generated, skipped, failed
        total = len(jobs) * len(skills)
        done = 0
        for i, job in enumerate(jobs, 1):
            title = job.get("job_title", "Unknown")
            employer = job.get("employer_name", "Unknown")
            key = job.get("_job_key") or job.get("job_id", "")
            typer.echo(f"\n[{i}/{len(jobs)}] {title} @ {employer} [{key}]")
            job_data = {k: v for k, v in job.items() if not k.startswith("_")}
            for skill in skills:
                done += 1
                out_path = _skill_output_path(config, run_date, job_data, skill)
                existing = None if force else _find_existing_skill_output(config, job_data, skill)
                if existing is not None:
                    typer.echo(f"  ↷ skip {skill} (exists: {existing.parent.parent.name}, use --force to regenerate) [{done}/{total}]")
                    skipped += 1
                    continue
                typer.echo(f"  ⏳ {skill} [{done}/{total}]...", nl=False)
                try:
                    result = await _run_skill(config, job_data, skill)
                    _write_skill_output(out_path, result, job_data)
                    typer.echo(f" saved ✓ ({len(result)} chars)")
                    generated += 1
                except Exception as exc:
                    typer.echo(f" failed ✗ ({_friendly_error(exc)})")
                    failed += 1

    asyncio.run(_run_all())
    typer.echo(f"\nDone. generated={generated} skipped={skipped} failed={failed}")


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
    typer.echo(f"  Job Search Source:     {cfg.job_search_source}")
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


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if "429" in msg or "rate" in low or "quota" in low or "too many" in low:
        return f"rate limit/quota exceeded ({msg})"
    if "timeout" in low or "timed out" in low:
        return f"timeout ({msg})"
    return msg


async def _run_synthesize(
    config: LaburatorConfig,
    query: str,
    pages: int = 1,
    remote: bool = False,
    country: str = "us",
    date_posted: str = "all",
    employment_types: str = "",
    page: int = 1,
    source: str = "openninja",
    refresh: bool = False,
    force: bool = False,
) -> None:
    """Run the full pipeline asynchronously."""
    pipeline = JobSearchPipeline(config)
    initial_state = {
        "query": query,
        "mode": "process",
        "num_pages": pages,
        "remote_only": remote,
        "country": country,
        "date_posted": date_posted,
        "employment_types": employment_types,
        "page": page,
        "source": source,
        "refresh": refresh,
        "force": force,
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


def _find_job(config: LaburatorConfig, job_id: str) -> dict | None:
    """Search indexed jobs and legacy cache for a job by key or job_id."""
    store = JobIndexStore(config.resolved_output_dir)
    job = store.get_job(job_id)
    if job is not None:
        # strip meta keys for LLM context cleanliness
        return {k: v for k, v in job.items() if not k.startswith("_")}

    cache_root = config.resolved_output_dir / "cache"
    if not cache_root.is_dir():
        return None
    for search_dir in cache_root.iterdir():
        if not search_dir.is_dir():
            continue
        jobs_file = search_dir / "jobs.json"
        if not jobs_file.exists():
            continue
        try:
            jobs = json.loads(jobs_file.read_text(encoding="utf-8"))
            for job in jobs:
                if str(job.get("job_id", "")) == str(job_id):
                    return job
        except Exception:
            continue
    return None


def _skill_output_path(config: LaburatorConfig, run_date: str, job: dict[str, Any], skill_name: str) -> Path:
    company_slug = _pipeline_slugify(job.get("employer_name", "unknown"))
    job_slug = _pipeline_slugify(job.get("job_title", "unknown"))
    filename = SKILL_FILENAMES.get(skill_name, skill_name) + ".md"
    folder = f"{company_slug}-{job_slug}"[:80].rstrip("-") or "job"
    return config.resolved_output_dir / run_date / folder / filename


def _find_existing_skill_output(config: LaburatorConfig, job: dict[str, Any], skill_name: str) -> Path | None:
    """Find existing output for job+skill across all run-date folders (latest first)."""
    company_slug = _pipeline_slugify(job.get("employer_name", "unknown"))
    job_slug = _pipeline_slugify(job.get("job_title", "unknown"))
    filename = SKILL_FILENAMES.get(skill_name, skill_name) + ".md"
    folder = f"{company_slug}-{job_slug}"[:80].rstrip("-") or "job"
    root = config.resolved_output_dir
    if not root.is_dir():
        return None
    candidates: list[Path] = []
    for date_dir in root.iterdir():
        if not date_dir.is_dir() or date_dir.name in {"index", "cache"}:
            continue
        p = date_dir / folder / filename
        if p.exists():
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.parent.parent.name, p.stat().st_mtime), reverse=True)
    return candidates[0]


def _write_skill_output(path: Path, content: str, job: dict[str, Any], link: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    job_md = path.parent / "job.md"
    if not job_md.exists():
        parts = ["# Job Description\n"]
        lk = link or job.get("job_apply_link") or job.get("job_google_link") or ""
        if lk:
            parts.append(f"\n**Link:** {lk}\n")
        parts.append(f"\n**Title:** {job.get('job_title','')}\n")
        parts.append(f"**Employer:** {job.get('employer_name','')}\n")
        parts.append(f"**Location:** {job.get('job_location','')}\n\n")
        parts.append(job.get("job_description") or "")
        try:
            job_md.write_text("".join(parts), encoding="utf-8")
        except Exception:
            pass


async def _run_skill(config: LaburatorConfig, job: dict[str, Any], skill_name: str) -> str:
    cv_context = _read_cv(config.resolved_cv_path)
    wiki_context = _read_wiki(config.resolved_llmwiki_dir)
    system_prompt = load_skill(skill_name)
    user_messages = build_user_messages(skill_name, job, cv_context, wiki_context)
    fmt = response_format(skill_name)
    llm = LLMService(config)
    try:
        return await llm.generate(system_prompt=system_prompt, user_messages=user_messages, response_format=fmt)
    finally:
        await llm.close()


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


def _extract_personal_info(cv_content: str) -> str:
    """Extract personal info from cv.md for the LaTeX preamble.

    Parses the first lines of the CV to extract name, title, contact info,
    and professional summary.
    """
    lines = cv_content.strip().split("\n")
    info_parts = []

    for line in lines[:20]:  # Only look at the first 20 lines
        line = line.strip()
        if not line or line.startswith("---"):
            continue
        # Name (# Federico Gonzalez)
        if line.startswith("# "):
            info_parts.append(f"Name: {line[2:]}")
        # Title (**Full-Stack Developer & DevOps**)
        elif line.startswith("**") and line.endswith("**"):
            info_parts.append(f"Title: {line.strip('*')}")
        # Contact lines (contain links)
        elif "github.com" in line or "linkedin.com" in line or "gitlab.com" in line:
            info_parts.append(f"Contact: {line}")
        # Quote/Summary (starts with > or is a long paragraph)
        elif line.startswith(">") or (len(line) > 50 and not line.startswith("#")):
            info_parts.append(f"Summary: {line.lstrip('>').strip()}")
        # Stop at Resumen/Summary section
        elif line.startswith("## "):
            break

    return "\n".join(info_parts) if info_parts else cv_content[:500]


# ── Web ──────────────────────────────────────────────────────────────────


@app.command()
def web(
    port: int = typer.Option(8080, "--port", "-p", help="Port to bind the web server."),
):
    """Start the Laburator web UI."""
    import uvicorn

    from laburator.web.app import app as web_app

    uvicorn.run(web_app, host="0.0.0.0", port=port)


# ── Entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
