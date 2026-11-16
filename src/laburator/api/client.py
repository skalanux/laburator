"""Job-search API client contracts and compatibility exports.

The concrete provider implementation that was previously defined here now lives
in :mod:`laburator.api.openninja` as ``OpenNinjaJobSearchClient``. New code
should depend on :class:`laburator.api.base.JobSearchClient` (abstract
interface), :class:`laburator.api.adapter.JobSearchAdapter`, and the shared
DTOs in :mod:`laburator.api.dtos`.
"""
from __future__ import annotations

from laburator.api.base import JobSearchClient
from laburator.api.openninja import OpenNinjaJobSearchClient

__all__ = ["JobSearchClient", "OpenNinjaJobSearchClient"]
