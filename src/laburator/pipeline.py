"""LangGraph pipeline for the job-search workflow.

Defines the :class:`JobSearchPipeline` that wires together fetch, cache,
context-loading, LLM processing, and output-saving nodes.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from laburator.api import JobSearchAdapter, JobSearchFilters, create_search_adapter
from laburator.config import LaburatorConfig
from laburator.llm.service import LLMService
from laburator.skills import (
    SKILL_FILENAMES,
    SKILL_LABELS,
    SKILL_NAMES,
    build_user_messages,
    load_skill,
    response_format,
)
from laburator.state import State
from laburator.store import JobIndexStore

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert arbitrary text to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text[:80].rstrip("-")


class JobSearchPipeline:
    """Orchestrates the end-to-end job-search pipeline via LangGraph.

    Builds a graph with nodes for parsing, fetching, caching, context
    loading, LLM processing, and output saving.
    """

    def __init__(
        self,
        config: LaburatorConfig,
        search_adapter: JobSearchAdapter | None = None,
        source: str | None = None,
    ) -> None:
        self.config = config
        self.llm = LLMService(config)
        self._default_source = (source or getattr(config, "job_search_source", "openninja") or "openninja")
        if search_adapter is None:
            try:
                search_adapter = create_search_adapter(self._default_source, config)
            except Exception:
                # allow construction without credentials for non-search flows; fetch will fail explicitly
                from laburator.api import OpenNinjaJobSearchClient
                search_adapter = JobSearchAdapter(OpenNinjaJobSearchClient(getattr(config, "job_search_api_key", "")))
        self.search = search_adapter
        # Backward-compatible attribute name used internally.
        self.api = search_adapter

    def _adapter_for_source(self, source: str | None) -> JobSearchAdapter:
        src = (source or self._default_source or getattr(self.config, "job_search_source", "openninja") or "openninja")
        if src == self.search.source_name:
            return self.search
        return create_search_adapter(src, self.config)

    # ── Graph nodes ─────────────────────────────────────────────────────

    async def parse_query_node(self, state: State) -> dict[str, Any]:
        """Parse and store the search query; set mode and filters from state."""
        query = state.get("query", "")
        mode = state.get("mode", "fetch")
        run_date = date.today().isoformat()
        return {
            "query": query,
            "mode": mode,
            "num_pages": state.get("num_pages", 1),
            "remote_only": state.get("remote_only", False),
            "country": state.get("country", "us"),
            "date_posted": state.get("date_posted", "all"),
            "employment_types": state.get("employment_types", ""),
            "page": state.get("page", 1),
            "source": state.get("source", getattr(self.search, "source_name", "openninja")),
            "refresh": state.get("refresh", False),
            "force": state.get("force", False),
            "run_date": run_date,
            "errors": [],
            "outputs": {},
            "all_jobs": [],
            "cv_context": "",
            "llmwiki_context": "",
        }

    async def fetch_jobs_node(self, state: State) -> dict[str, Any]:
        """Fetch job listings from the configured search source."""
        query = state.get("query", "")
        if not query:
            return {"errors": state.get("errors", []) + ["No query provided"]}

        src = state.get("source") or self._default_source
        print(f"\n🔍 Searching for '{query}' ({src})...", end=" ", flush=True)
        adapter = None
        try:
            adapter = self._adapter_for_source(src)
            filters = JobSearchFilters(
                query=query,
                num_pages=state.get("num_pages", 1),
                country=state.get("country", "us"),
                date_posted=state.get("date_posted", "all"),
                remote_only=state.get("remote_only", False),
                employment_types=state.get("employment_types", ""),
                page=state.get("page", 1),
                source=src,
            )
            response = await adapter.search(filters)
            jobs = [job.model_dump() for job in response.data]
            print(f"{len(jobs)} job(s) found ✓", flush=True)
            return {"all_jobs": jobs, "_search_result": response, "_search_filters": filters}
        except Exception as exc:
            print(f"failed ✗", flush=True)
            logger.exception("Failed to fetch jobs for query '%s'", query)
            return {"errors": state.get("errors", []) + [f"Fetch failed: {exc}"]}
        finally:
            if adapter is not None and adapter is not self.search:
                try:
                    await adapter.close()
                except Exception:
                    pass

    async def cache_jobs_node(self, state: State) -> dict[str, Any]:
        """Save fetched jobs to legacy cache and search index."""
        jobs = state.get("all_jobs", [])
        query = state.get("query", "")
        if not jobs:
            return {}

        print("💾 Saving cache...", end=" ", flush=True)
        cache_dir = self.config.resolved_output_dir / "cache" / _slugify(query)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / "jobs.json"

        try:
            cache_path.write_text(
                json.dumps(jobs, indent=2, default=str), encoding="utf-8"
            )
            logger.info("Cached %d jobs to %s", len(jobs), cache_path)
        except Exception as exc:
            print(f"failed: {exc}", flush=True)
            logger.warning("Failed to cache jobs: %s", exc)
            return {}

        # Persist to index when search result metadata is available
        try:
            result = state.get("_search_result")
            filters = state.get("_search_filters")
            if result is not None:
                store = JobIndexStore(self.config.resolved_output_dir)
                store.save_search(result, filters)
        except Exception as exc:
            logger.warning("Failed to save search index: %s", exc)

        print(f"✓", flush=True)
        return {}  # No state mutation needed

    async def load_context_node(self, state: State) -> dict[str, Any]:
        """Read cv.md and llmwiki .md files into state."""
        cv_context = ""
        llmwiki_context = ""

        # Load CV
        cv_path = self.config.resolved_cv_path
        if cv_path.exists():
            try:
                cv_context = cv_path.read_text(encoding="utf-8")
                print(f"📄 CV loaded ({len(cv_context)} chars)", flush=True)
                logger.info("Loaded CV from %s (%d chars)", cv_path, len(cv_context))
            except Exception as exc:
                logger.warning("Failed to read CV at %s: %s", cv_path, exc)
        else:
            print("⚠️  cv.md not found — skills will run without personal context", flush=True)

        # Load LLM wiki content
        wiki_dir = self.config.resolved_llmwiki_dir
        if wiki_dir.is_dir():
            md_files = sorted(wiki_dir.glob("*.md"))
            parts: list[str] = []
            for md_file in md_files:
                try:
                    content = md_file.read_text(encoding="utf-8")
                    heading = md_file.stem
                    parts.append(f"## {heading}\n\n{content}")
                except Exception as exc:
                    logger.warning("Failed to read %s: %s", md_file, exc)
            llmwiki_context = "\n\n---\n\n".join(parts)
            print(f"📚 Wiki loaded: {len(md_files)} page(s) ({len(llmwiki_context)} chars)", flush=True)
            logger.info("Loaded %d wiki pages (%d chars)", len(md_files), len(llmwiki_context))
        else:
            print(f"⚠️  LLM wiki directory not found: {wiki_dir}", flush=True)

        return {
            "cv_context": cv_context,
            "llmwiki_context": llmwiki_context,
        }

    async def process_jobs_node(self, state: State) -> dict[str, Any]:
        """Process each job through every configured skill (skips existing outputs unless force)."""
        jobs = state.get("all_jobs", [])
        cv_ctx = state.get("cv_context", "")
        wiki_ctx = state.get("llmwiki_context", "")
        errors: list[str] = list(state.get("errors", []))
        outputs: dict[str, str] = dict(state.get("outputs", {}))
        force = bool(state.get("force", False))
        run_date = state.get("run_date", date.today().isoformat())

        total = len(jobs) * len(SKILL_NAMES)
        completed = 0
        skipped = 0

        for job_idx, job in enumerate(jobs):
            employer = job.get("employer_name", "unknown")
            job_title = job.get("job_title", "unknown")
            print(f"\n  [{job_idx + 1}/{len(jobs)}] {job_title} @ {employer}", flush=True)

            for skill_name in SKILL_NAMES:
                label = SKILL_LABELS.get(skill_name, skill_name)
                completed += 1
                out_path = self._output_path(run_date, job, skill_name)
                existing_path = None if force else self._find_existing_output_path(job, skill_name)
                if existing_path is not None:
                    try:
                        existing = existing_path.read_text(encoding="utf-8")
                    except Exception:
                        existing = ""
                    output_key = f"{job_idx}_{skill_name}"
                    outputs[output_key] = existing
                    skipped += 1
                    print(f"    ↷ {label} — skipped (exists: {existing_path.parent.parent.name}) ({completed}/{total})", flush=True)
                    continue

                print(f"    ⏳ {label}... ({completed}/{total})", end="", flush=True)

                try:
                    system_prompt = load_skill(skill_name)
                    user_messages = build_user_messages(skill_name, job, cv_ctx, wiki_ctx)
                    fmt = response_format(skill_name)

                    result = await self.llm.generate(
                        system_prompt=system_prompt,
                        user_messages=user_messages,
                        response_format=fmt,
                    )

                    output_key = f"{job_idx}_{skill_name}"
                    outputs[output_key] = result
                    print(f"\r    ✅ {label} — {len(result)} chars     ", flush=True)

                    # Save immediately so user can see files appear
                    self._save_individual_output(run_date, job, skill_name, result)

                except Exception as exc:
                    emsg = str(exc)
                    low = emsg.lower()
                    if "429" in emsg or "rate" in low or "quota" in low or "too many" in low:
                        emsg = f"rate limit/quota exceeded ({emsg})"
                    print(f"\r    ❌ {label} — failed: {emsg}     ", flush=True)
                    msg = f"Skill '{skill_name}' failed for job {job_idx}: {emsg}"
                    logger.exception(msg)
                    errors.append(msg)

        if skipped:
            print(f"\n  ↷ Skipped {skipped} existing output(s) (use --force-process to regenerate)", flush=True)
        return {"outputs": outputs, "errors": errors}

    async def save_output_node(self, state: State) -> dict[str, Any]:
        """Final pass — ensure all outputs are written to disk."""
        outputs = state.get("outputs", {})
        jobs = state.get("all_jobs", [])
        run_date = state.get("run_date", date.today().isoformat())
        errors: list[str] = list(state.get("errors", []))
        saved = 0

        output_root = self.config.resolved_output_dir / run_date

        for key, content in outputs.items():
            parts = key.rsplit("_", 1)
            if len(parts) != 2:
                continue
            job_idx_str, skill_name = parts
            try:
                job_idx = int(job_idx_str)
            except ValueError:
                continue
            if job_idx >= len(jobs):
                continue

            self._save_individual_output(run_date, jobs[job_idx], skill_name, content)
            saved += 1

        if saved:
            print(f"\n💾 {saved} file(s) saved to {output_root}", flush=True)
        return {"errors": errors}

    # ── Output helpers ──────────────────────────────────────────────────

    def _output_path(self, run_date: str, job: dict, skill_name: str) -> Path:
        company_slug = _slugify(job.get("employer_name", "unknown"))
        job_slug = _slugify(job.get("job_title", "unknown"))
        filename = SKILL_FILENAMES.get(skill_name, skill_name) + ".md"
        out_dir = self.config.resolved_output_dir / run_date / f"{company_slug}-{job_slug}"
        return out_dir / filename

    def _find_existing_output_path(self, job: dict, skill_name: str) -> Path | None:
        """Find existing output for job+skill across all run-date folders (latest first)."""
        company_slug = _slugify(job.get("employer_name", "unknown"))
        job_slug = _slugify(job.get("job_title", "unknown"))
        filename = SKILL_FILENAMES.get(skill_name, skill_name) + ".md"
        folder = f"{company_slug}-{job_slug}"
        root = self.config.resolved_output_dir
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

    def _save_individual_output(
        self, run_date: str, job: dict, skill_name: str, content: str,
    ) -> None:
        """Write a single skill output to disk immediately."""
        out_path = self._output_path(run_date, job, skill_name)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_path.write_text(content, encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to write %s: %s", out_path, exc)

    # ── Graph construction ──────────────────────────────────────────────

    def build_graph(self) -> StateGraph:
        """Build and compile the LangGraph ``StateGraph``.

        The graph routes based on ``mode``::

            START → parse_query → (conditional)
              ├── mode="fetch"   → fetch_jobs → cache_jobs → END
              └── mode="process" → fetch_jobs → cache_jobs → load_context
                                  → process_jobs → save_output → END
        """
        builder = StateGraph(State)

        builder.add_node("parse_query", self.parse_query_node)
        builder.add_node("fetch_jobs", self.fetch_jobs_node)
        builder.add_node("cache_jobs", self.cache_jobs_node)
        builder.add_node("load_context", self.load_context_node)
        builder.add_node("process_jobs", self.process_jobs_node)
        builder.add_node("save_output", self.save_output_node)

        builder.add_edge(START, "parse_query")

        def _route_after_parse(state: State) -> str:
            return state.get("mode", "fetch")

        builder.add_conditional_edges(
            "parse_query",
            _route_after_parse,
            {
                "fetch": "fetch_jobs",
                "process": "fetch_jobs",
            },
        )

        builder.add_edge("fetch_jobs", "cache_jobs")

        def _route_after_cache(state: State) -> str:
            return state.get("mode", "fetch")

        builder.add_conditional_edges(
            "cache_jobs",
            _route_after_cache,
            {
                "fetch": END,
                "process": "load_context",
            },
        )

        builder.add_edge("load_context", "process_jobs")
        builder.add_edge("process_jobs", "save_output")
        builder.add_edge("save_output", END)

        graph = builder.compile()
        logger.debug("Job-search pipeline graph compiled")
        return graph

    async def run(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        """Convenience wrapper — build graph and invoke with initial state.

        Args:
            initial_state: The initial state dictionary (must include at least
                ``query`` and ``mode``).

        Returns:
            The final state after all nodes have executed.
        """
        graph = self.build_graph()
        try:
            return await graph.ainvoke(initial_state)
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        """Release held resources."""
        await self.llm.close()
        await self.search.close()
