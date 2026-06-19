"""Skill: Interview Questions — generate possible interview Q&A for a job.

This is a standalone skill file. The user may edit it independently.
"""
from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """\
You are an interview preparation coach. Generate 5-7 likely technical and \
behavioural interview questions for the given position, with tailored \
answers based on the user's cv.md and knowledge base.

For each question:
- **### Question**: The interview question (be specific — reference the \
company's tech stack or domain).
- **Answer**: A strong, structured answer the user could deliver, \
incorporating their actual experience.

Cover a mix of:
- **Technical questions**: relevant to the job's required skills and tools.
- **Behavioural questions**: situational, leadership, conflict resolution.
- **Role-specific questions**: industry or domain knowledge.
- **System design or architecture questions** (for senior roles).

Format as a markdown list. Each Q&A pair should be self-contained.
Return ONLY the questions and answers — no extra commentary."""


def build_prompt(
    job_data: dict[str, Any],
    cv_context: str,
    llmwiki_context: str,
) -> list[dict[str, str]]:
    """Build the user messages array for the interview-questions skill.

    Args:
        job_data: Raw job listing dictionary from the API.
        cv_context: The user's CV in markdown format.
        llmwiki_context: Relevant wiki knowledge base content.

    Returns:
        A list of message dicts for the LLM API call.
    """
    import json

    job_title = job_data.get("job_title", "the position")
    employer = job_data.get("employer_name", "the company")

    return [
        {
            "role": "user",
            "content": (
                f"## Job Listing\n\n```json\n{json.dumps(job_data, indent=2, default=str)}\n```\n\n"
                f"## CV\n\n{cv_context}\n\n"
                f"## Knowledge Base\n\n{llmwiki_context}\n\n"
                f"Generate interview Q&A for the **{job_title}** role at **{employer}**."
            ),
        },
    ]
