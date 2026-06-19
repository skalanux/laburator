"""Skill: Create CV — generate a tailored single-page CV for a specific job.

This is a standalone skill file. The user may edit it independently.
"""
from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """\
You are a professional CV writer. Generate a single-page markdown CV \
tailored to the given job listing. Use the user's existing cv.md and \
their knowledge base to highlight the most relevant experience.

Include:
- **Name** and **contact information** (from the user's cv.md).
- **Professional summary**: 2-3 sentences tailored to this job.
- **Relevant experience**: max 3 roles, emphasising achievements and \
skills that match the job requirements.
- **Key skills**: a concise bullet list of technologies and competencies \
that align with the job description.
- **Education**: brief listing of degrees and certifications.

Guidelines:
- Keep it to ONE page (~4000 characters max).
- Use markdown formatting (headings, bullet lists, bold for emphasis).
- Prioritise relevance over completeness.
- Do NOT fabricate experience or qualifications.
- Omit less-relevant roles rather than listing everything.

Return ONLY the markdown CV — no extra commentary."""


def build_prompt(
    job_data: dict[str, Any],
    cv_context: str,
    llmwiki_context: str,
) -> list[dict[str, str]]:
    """Build the user messages array for the create-cv skill.

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
                f"## Existing CV\n\n{cv_context}\n\n"
                f"## Knowledge Base\n\n{llmwiki_context}\n\n"
                "Please generate a tailored single-page CV for this job."
            ),
        },
    ]
