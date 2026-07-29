"""Typer CLI for laburator.

Provides ``search``, ``synth``, ``run``, ``generar-cv``, ``list``, and
``config`` commands that drive the job-search pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import typer

from laburator.config import LaburatorConfig
from laburator.pipeline import JobSearchPipeline
from laburator.skills import build_cv_to_tex_messages, build_proposal_messages, build_user_messages, load_skill, response_format

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
