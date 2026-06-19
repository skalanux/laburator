"""Skill: Cover Letter — write a professional cover letter for a specific job.

This is a standalone skill file. The user may edit it independently.
"""
from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """\
You are a professional cover letter writer. Write a compelling, \
professional cover letter as markdown for the given job listing and user.

Structure:
- **Salutation**: Address the company or hiring manager (use "Hiring Manager" \
if no name is available).
- **Opening paragraph**: Express interest in the role and company. Mention \
the specific position title.
- **Body paragraph**: Connect 2-3 of the user's key achievements or skills \
(drawn from cv.md and the knowledge base) to the job's requirements. Be \
specific — mention technologies, outcomes, or projects.
- **Closing paragraph**: Reiterate enthusiasm, thank the reader, and invite \
further discussion.

Guidelines:
- 2-3 paragraphs maximum.
- Professional tone, warm but not informal.
- Do NOT exceed 3000 characters.
- Do NOT invent experience.
- Return ONLY the markdown letter — no extra commentary."""


def build_prompt(
    job_data: dict[str, Any],
    cv_context: str,
    llmwiki_context: str,
) -> list[dict[str, str]]:
    """Build the user messages array for the cover-letter skill.

    Args:
        job_data: Raw job listing dictionary from the API.
        cv_context: The user's CV in markdown format.
        llmwiki_context: Relevant wiki knowledge base content.

    Returns:
        A list of message dicts for the LLM API call.
    """
    import json

    employer_name = job_data.get("employer_name", "the Company")
    job_title = job_data.get("job_title", "the position")

    return [
        {
            "role": "user",
            "content": (
                f"## Job Listing\n\n```json\n{json.dumps(job_data, indent=2, default=str)}\n```\n\n"
                f"## CV\n\n{cv_context}\n\n"
                f"## Knowledge Base\n\n{llmwiki_context}\n\n"
                f"Please write a professional cover letter for the **{job_title}** "
                f"role at **{employer_name}**."
            ),
        },
    ]
