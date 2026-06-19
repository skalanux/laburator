"""LangGraph pipeline for the job-search workflow.

Defines the :class:`JobSearchPipeline` that wires together fetch, cache,
context-loading, LLM processing, and output-saving nodes.
"""
from __future__ import annotations

import importlib
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from laburator.api.client import JobSearchClient
from laburator.config import LaburatorConfig
from laburator.llm.service import LLMService
from laburator.state import State

logger = logging.getLogger(__name__)

# Ensure project root is on sys.path so skills/ can be imported
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


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

    def __init__(self, config: LaburatorConfig) -> None:
        self.config = config
        self.llm = LLMService(config)
        self.api = JobSearchClient(config.job_search_api_key)

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
            "refresh": state.get("refresh", False),
            "run_date": run_date,
            "errors": [],
            "outputs": {},
            "all_jobs": [],
            "cv_context": "",
            "llmwiki_context": "",
        }

    async def fetch_jobs_node(self, state: State) -> dict[str, Any]:
        """Fetch job listings from the API."""
        query = state.get("query", "")
        if not query:
            return {"errors": state.get("errors", []) + ["No query provided"]}

        print(f"\n🔍 Buscando trabajos para '{query}'...", end=" ", flush=True)
        try:
            response = await self.api.search_v2(
                query=query,
                num_pages=state.get("num_pages", 1),
                country=state.get("country", "us"),
                remote_jobs_only=state.get("remote_only", False),
            )
            jobs = [job.model_dump() for job in response.data]
            print(f"{len(jobs)} trabajo(s) encontrados ✓", flush=True)
            return {"all_jobs": jobs}
        except Exception as exc:
            print(f"falló ✗", flush=True)
            logger.exception("Failed to fetch jobs for query '%s'", query)
            return {"errors": state.get("errors", []) + [f"Fetch failed: {exc}"]}

    async def cache_jobs_node(self, state: State) -> dict[str, Any]:
        """Save fetched jobs to a JSON cache file."""
        jobs = state.get("all_jobs", [])
        query = state.get("query", "")
        if not jobs:
            return {}

        print("💾 Guardando caché...", end=" ", flush=True)
        cache_dir = self.config.resolved_output_dir / "cache" / _slugify(query)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / "jobs.json"

        try:
            cache_path.write_text(
                json.dumps(jobs, indent=2, default=str), encoding="utf-8"
            )
            print(f"✓", flush=True)
            logger.info("Cached %d jobs to %s", len(jobs), cache_path)
        except Exception as exc:
            print(f"falló: {exc}", flush=True)
            logger.warning("Failed to cache jobs: %s", exc)

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
                print(f"📄 CV cargado ({len(cv_context)} chars)", flush=True)
                logger.info("Loaded CV from %s (%d chars)", cv_path, len(cv_context))
            except Exception as exc:
                logger.warning("Failed to read CV at %s: %s", cv_path, exc)
        else:
            print("⚠️  cv.md no encontrado — los skills se generarán sin contexto personal", flush=True)

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
            print(f"📚 Wiki cargada: {len(md_files)} página(s) ({len(llmwiki_context)} chars)", flush=True)
            logger.info("Loaded %d wiki pages (%d chars)", len(md_files), len(llmwiki_context))
        else:
            print(f"⚠️  Directorio llmwiki no encontrado: {wiki_dir}", flush=True)

        return {
            "cv_context": cv_context,
            "llmwiki_context": llmwiki_context,
        }

    async def process_jobs_node(self, state: State) -> dict[str, Any]:
        """Process each job through every configured skill."""
        jobs = state.get("all_jobs", [])
        cv_ctx = state.get("cv_context", "")
        wiki_ctx = state.get("llmwiki_context", "")
        errors: list[str] = list(state.get("errors", []))
        outputs: dict[str, str] = dict(state.get("outputs", {}))

        skill_names = [
            "jobsynthesis",
            "createcv",
            "presentationletter",
            "interviewquestions",
        ]
        skill_labels = {
            "jobsynthesis": "📋 Análisis del trabajo",
            "createcv": "📄 CV personalizado",
            "presentationletter": "✉️ Carta de presentación",
            "interviewquestions": "❓ Preguntas de entrevista",
        }

        total = len(jobs) * len(skill_names)
        completed = 0

        for job_idx, job in enumerate(jobs):
            employer = job.get("employer_name", "unknown")
            job_title = job.get("job_title", "unknown")
            print(f"\n  [{job_idx + 1}/{len(jobs)}] {job_title} @ {employer}", flush=True)

            for skill_name in skill_names:
                label = skill_labels.get(skill_name, skill_name)
                completed += 1
                print(f"    ⏳ {label}... ({completed}/{total})", end="", flush=True)

                try:
                    skill_module = importlib.import_module(f"skills.{skill_name}")
                    system_prompt = skill_module.SYSTEM_PROMPT
                    user_messages = skill_module.build_prompt(job, cv_ctx, wiki_ctx)
                    response_format = "json_object" if skill_name == "jobsynthesis" else "text"

                    result = await self.llm.generate(
                        system_prompt=system_prompt,
                        user_messages=user_messages,
                        response_format=response_format,
                    )

                    output_key = f"{job_idx}_{skill_name}"
                    outputs[output_key] = result
                    print(f"\r    ✅ {label} — {len(result)} chars     ", flush=True)

                    # Save immediately so user can see files appear
                    run_date = state.get("run_date", date.today().isoformat())
                    self._save_individual_output(run_date, job, skill_name, result)

                except Exception as exc:
                    print(f"\r    ❌ {label} — falló: {exc}     ", flush=True)
                    msg = f"Skill '{skill_name}' failed for job {job_idx}: {exc}"
                    logger.exception(msg)
                    errors.append(msg)

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
            print(f"\n💾 {saved} archivo(s) guardados en {output_root}", flush=True)
        return {"errors": errors}

    # ── Output helpers ──────────────────────────────────────────────────

    SKILL_FILENAMES = {
        "jobsynthesis": "job",
        "createcv": "cv",
        "presentationletter": "presentation",
        "interviewquestions": "interview",
    }

    def _save_individual_output(
        self, run_date: str, job: dict, skill_name: str, content: str,
    ) -> None:
        """Write a single skill output to disk immediately."""
        company_slug = _slugify(job.get("employer_name", "unknown"))
        job_slug = _slugify(job.get("job_title", "unknown"))
        filename = self.SKILL_FILENAMES.get(skill_name, skill_name) + ".md"
        out_dir = self.config.resolved_output_dir / run_date / f"{company_slug}-{job_slug}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
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
        await self.api.close()
