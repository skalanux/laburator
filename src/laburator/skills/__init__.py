"""Skill loader — reads system prompts from .md files.

Resolution order:
1. ``~/.config/laburator/skills/<name>.md`` (user override)
2. ``<package_dir>/skills/<name>.md`` (built-in default)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: System prompt topic keys passed to every skill call by the generic builder.
_USER_PROMPT_TOPICS = {
    "jobsynthesis": (
        "job listing",
        "Please produce a detailed job synthesis following your instructions.",
    ),
    "createcv": (
        "existing CV",
        "Please generate a tailored single-page CV for this job.",
    ),
    "presentationletter": ("CV", "Please write a professional cover letter."),
    "interviewquestions": ("CV", "Generate interview Q&A for this role."),
    "generarcv": (
        "proposal",
        "Please generate a tailored CV for this proposal, following the tips provided.",
    ),
}


def _override_dir() -> Path:
    """Return ``~/.config/laburator/skills/`` (respects ``$XDG_CONFIG_HOME``)."""
    import os

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "laburator" / "skills"


def _builtin_dir() -> Path:
    """Return the path to the built-in skills shipped with the package."""
    return Path(__file__).resolve().parent


def load_skill(skill_name: str) -> str:
    """Load a skill's system prompt from disk.

    Checks the user override directory first, then falls back to the
    built-in skill bundled with the package.

    Args:
        skill_name: One of ``jobsynthesis``, ``createcv``,
            ``presentationletter``, ``interviewquestions``.

    Returns:
        The system prompt text.

    Raises:
        FileNotFoundError: If neither the override nor the built-in file
            exists.
    """
    # 1. User override
    override = _override_dir() / f"{skill_name}.md"
    if override.exists():
        logger.info("Loading skill '%s' from override: %s", skill_name, override)
        return override.read_text(encoding="utf-8")

    # 2. Built-in default
    builtin = _builtin_dir() / f"{skill_name}.md"
    if builtin.exists():
        logger.debug("Loading skill '%s' from built-in: %s", skill_name, builtin)
        return builtin.read_text(encoding="utf-8")

    raise FileNotFoundError(
        f"Skill '{skill_name}' not found — checked override ({override}) "
        f"and built-in ({builtin})"
    )


def build_proposal_messages(
    proposal: str,
    tips: str,
    cv_context: str,
    llmwiki_context: str,
) -> list[dict[str, str]]:
    """Build the user messages for the generar_cv proposal-based skill.

    Takes a proposal text and tips instead of a job listing, using the
    existing CV as base material.

    Args:
        proposal: The proposal/opportunity text (job listing, project, etc.).
        tips: User-provided emphasis points and directions.
        cv_context: User's CV markdown.
        llmwiki_context: Wiki / knowledge base markdown.

    Returns:
        A list of one ``user`` message dict.
    """
    return [
        {
            "role": "user",
            "content": (
                f"## Proposal\n\n{proposal}\n\n"
                f"## Tips\n\n{tips}\n\n"
                f"## Existing CV\n\n{cv_context}\n\n"
                f"## Knowledge Base\n\n{llmwiki_context}\n\n"
                f"Generate the tailored CV following the tips above."
            ),
        },
    ]


def build_cv_to_tex_messages(
    cv_markdown: str,
    personal_info: str,
) -> list[dict[str, str]]:
    """Build the user messages for the generarcvtex skill.

    Takes a markdown CV and converts it to LaTeX moderncv format.

    Args:
        cv_markdown: The markdown CV to convert.
        personal_info: Personal info (name, title, contact, quote) for the
            LaTeX preamble.

    Returns:
        A list of one ``user`` message dict.
    """
    return [
        {
            "role": "user",
            "content": (
                f"## Personal Information (for LaTeX preamble)\n\n{personal_info}\n\n"
                f"## Markdown CV to convert\n\n{cv_markdown}\n\n"
                f"Convert the above markdown CV to a complete LaTeX moderncv document."
            ),
        },
    ]


def build_user_messages(
    skill_name: str,
    job_data: dict[str, Any],
    cv_context: str,
    llmwiki_context: str,
) -> list[dict[str, str]]:
    """Build the user messages for a skill call.

    All skills receive the job listing as JSON, the user's CV, and the
    wiki knowledge base as context.

    Args:
        skill_name: Name of the skill being invoked.
        job_data: Raw job listing from the API.
        cv_context: User's CV markdown.
        llmwiki_context: Wiki / knowledge base markdown.

    Returns:
        A list of one ``user`` message dict.
    """
    label, closing = _USER_PROMPT_TOPICS.get(skill_name, ("job listing", "Proceed."))

    return [
        {
            "role": "user",
            "content": (
                f"## Job Listing\n\n```json\n{json.dumps(job_data, indent=2, default=str)}\n```\n\n"
                f"## {label}\n\n{cv_context}\n\n"
                f"## Knowledge Base\n\n{llmwiki_context}\n\n"
                f"{closing}"
            ),
        },
    ]


def response_format(skill_name: str) -> str:
    """Return the LLM response format for a skill."""
    return "json_object" if skill_name == "jobsynthesis" else "text"


# ── Skill registry ─────────────────────────────────────────────────────

SKILL_NAMES = [
    "jobsynthesis",
    "createcv",
    "presentationletter",
    "interviewquestions",
    "generarcv",
    "generarcvtex",
]

SKILL_LABELS = {
    "jobsynthesis": "📋 Job analysis",
    "createcv": "📄 Tailored CV",
    "presentationletter": "✉️ Cover letter",
    "interviewquestions": "❓ Interview questions",
    "generarcv": "🎯 Generated CV",
    "generarcvtex": "📝 LaTeX CV",
}

SKILL_FILENAMES = {
    "jobsynthesis": "job",
    "createcv": "cv",
    "presentationletter": "presentation",
    "interviewquestions": "interview",
    "generarcv": "cv ats",
    "generarcvtex": "cv",
}
