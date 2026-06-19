"""LLM service for OpenAI-compatible API calls.

Sends prompts to an OpenAI-compatible endpoint (e.g. OpenCode Zen) and
returns the generated text.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from laburator.config import LaburatorConfig

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
REQUEST_TIMEOUT = 120.0  # seconds


class LLMService:
    """Service that sends prompts to an OpenAI-compatible LLM API.

    Uses the ``/v1/chat/completions`` endpoint with a configurable model.
    Retries transient errors up to 3 times with exponential backoff.
    Auth errors (401, 403) fail immediately.
    """

    def __init__(self, config: LaburatorConfig) -> None:
        self.config = config
        self._client: httpx.AsyncClient | None = None

    async def generate(
        self,
        system_prompt: str,
        user_messages: list[dict[str, str]],
        response_format: str = "json_object",
    ) -> str:
        """Send a prompt to the LLM and return the generated text.

        Args:
            system_prompt: The system-level instruction prompt.
            user_messages: A list of message dicts (role/content) to send
                alongside the system prompt.
            response_format: ``"json_object"`` (default) or ``"text"``.

        Returns:
            The generated content string.

        Raises:
            ValueError: If the API key is not configured.
            httpx.HTTPStatusError: For auth errors (401/403).
            RuntimeError: After 3 failed attempts.
        """
        if not self.config.model_api_key:
            raise ValueError(
                "MODEL_API_KEY is not configured. "
                "Set it in your .env file or environment."
            )

        client = self._get_client()

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            *user_messages,
        ]

        payload: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": messages,
        }
        if response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            try:
                response = await client.post("/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
                return self._extract_content(data)

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (401, 403):
                    raise  # Auth errors — fail fast
                if status >= 500 or status == 429:
                    last_error = exc
                    if attempt < MAX_RETRIES - 1:
                        wait = 2**attempt
                        logger.warning(
                            "LLM API error (attempt %d/%d): HTTP %d. Retrying in %ds...",
                            attempt + 1, MAX_RETRIES, status, wait,
                        )
                        await asyncio.sleep(wait)
                    continue
                raise  # Other 4xx

            except (httpx.RequestError, json.JSONDecodeError, KeyError) as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    wait = 2**attempt
                    logger.warning(
                        "Transient error (attempt %d/%d): %s. Retrying in %ds...",
                        attempt + 1, MAX_RETRIES, exc, wait,
                    )
                    await asyncio.sleep(wait)
                continue

        raise RuntimeError(
            f"LLM generation failed after {MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Internal helpers ────────────────────────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.model_api_endpoint,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "Authorization": f"Bearer {self.config.model_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        """Extract the content string from an API response."""
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            logger.error("Unexpected LLM API response structure: %s", exc)
            return str(data)
