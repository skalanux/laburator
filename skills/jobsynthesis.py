"""Skill: Job Synthesis — analyze a job listing in detail.

This is a standalone skill file. The user may edit it independently.
"""
from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """\
You are a job analyst. Given a job listing, the user's CV, and their \
knowledge base, produce a detailed synthesis of the job. Include:

- **Company overview**: industry, size, reputation, culture.
- **Tech stack**: languages, frameworks, tools, platforms mentioned.
- **Role analysis**: responsibilities, seniority level, team structure.
- **Salary & benefits**: compensation range, perks, equity (if listed).
- **Location & remote**: office location, remote policy, time zones.
- **Required vs nice-to-have**: must-have skills vs preferred qualifications.
- **Fit assessment**: how well the user's profile matches this role.
- **Risks & red flags**: ambiguous requirements, low ratings, outdated tech.

Be objective, thorough, and specific. Do NOT make up data — use only what \
is in the job listing, the CV, and the knowledge base provided.

Return your analysis as a markdown document with clear section headings."""


def build_prompt(
    job_data: dict[str, Any],
    cv_context: str,
    llmwiki_context: str,
) -> list[dict[str, str]]:
    """Build the user messages array for the job synthesis skill.

    Args:
        job_data: Raw job listing dictionary from the API.
        cv_context: The user's CV in markdown format.
        llmwiki_context: Relevant wiki knowledge base content.

    Returns:
        A list of message dicts for the LLM API call.
    """
    import json

    return [
        {
            "role": "user",
            "content": (
                f"## Job Listing\n\n```json\n{json.dumps(job_data, indent=2, default=str)}\n```\n\n"
                f"## CV\n\n{cv_context}\n\n"
                f"## Knowledge Base\n\n{llmwiki_context}\n\n"
                "Please produce a detailed job synthesis following your instructions."
            ),
        },
    ]
