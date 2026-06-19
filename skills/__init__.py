"""Standalone skill modules for job-search output generation.

Each module exports:
    SYSTEM_PROMPT: str
    build_prompt(job_data: dict, cv_context: str, llmwiki_context: str) -> list[dict]
"""
