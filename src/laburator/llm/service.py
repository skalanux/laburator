"""LLM service for OpenAI-compatible API calls.

Sends prompts to an OpenAI-compatible endpoint (e.g. OpenCode Zen) and
returns the generated text.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from laburator.config import LaburatorConfig

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
REQUEST_TIMEOUT = 120.0  # seconds
MAX_BACKOFF = 30.0  # seconds


class LLMService:
    """Service that sends prompts to an OpenAI-compatible LLM API.

    Uses the ``/v1/chat/completions`` endpoint with a configurable model.
    Retries transient errors up to 5 times with exponential backoff + jitter,
    honoring ``Retry-After`` when present.
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
            RuntimeError: After 5 failed attempts.
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
        last_error_msg = ""

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
                    last_error_msg = self._extract_error_message(exc)
                    if attempt < MAX_RETRIES - 1:
                        wait = self._backoff(
                            attempt, self._retry_after(exc.response.headers)
                        )
                        logger.warning(
                            "LLM API error (attempt %d/%d): HTTP %d (%s). "
                            "Retrying in %.1fs...",
                            attempt + 1, MAX_RETRIES, status,
                            last_error_msg or "retryable", wait,
                        )
                        await asyncio.sleep(wait)
                    continue
                raise  # Other 4xx

            except (httpx.RequestError, ValueError) as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    wait = self._backoff(attempt)
                    logger.warning(
                        "Transient error (attempt %d/%d): %s. Retrying in %.1fs...",
                        attempt + 1, MAX_RETRIES, exc, wait,
                    )
                    await asyncio.sleep(wait)
                continue

        detail = str(last_error)
        if last_error_msg:
            detail = f"{detail} — {last_error_msg}"
        raise RuntimeError(
            f"LLM generation failed after {MAX_RETRIES} attempts. "
            f"Last error: {detail}"
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _extract_error_message(exc: httpx.HTTPStatusError) -> str:
        """Extract a human-readable message from an API error body.

        Some providers (e.g. Gemini's OpenAI-compatible endpoint) wrap the
        error object in a JSON array: ``[{"error": {"message": "..."}}]``.
        """
        try:
            data = exc.response.json()
        except Exception:
            return ""
        if isinstance(data, list):
            data = data[0] if data else {}
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                return str(err.get("message", "")).strip()
            if isinstance(err, str):
                return err.strip()
            if "message" in data:
                return str(data["message"]).strip()
        return ""

    @staticmethod
    def _retry_after(headers) -> float | None:
        """Parse the ``Retry-After`` header (seconds) if present."""
        value = headers.get("retry-after")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _backoff(attempt: int, retry_after: float | None = None) -> float:
        """Compute the retry wait with exponential backoff + jitter."""
        if retry_after is not None:
            return min(max(retry_after, 0.0), MAX_BACKOFF)
        return min(2**attempt, MAX_BACKOFF) + random.uniform(0, 1)

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
        """Extract the content string from an API response.

        Raises:
            ValueError: If the response structure is unexpected or the
                content is missing/empty, so callers never receive garbage.
        """
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Unexpected LLM API response structure: %s", exc)
            raise ValueError(f"Unexpected LLM API response structure: {exc}") from exc
        if not isinstance(content, str) or not content.strip():
            logger.error("LLM returned empty content")
            raise ValueError("LLM returned empty content")
        return content
