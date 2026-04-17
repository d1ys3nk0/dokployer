"""Typed DTOs for Dokploy API responses."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ComposeSummary:
    """Summary info for a compose stack."""

    name: str
    compose_id: str


@dataclass(frozen=True, slots=True)
class EnvironmentResponse:
    """Response from GET /api/environment.one."""

    compose: list[ComposeSummary]


@dataclass(frozen=True, slots=True)
class ComposeStatusResponse:
    """Response from GET /api/compose.one (status polling)."""

    compose_status: str


@dataclass(frozen=True, slots=True)
class ComposeCreated:
    """Response from POST /api/compose.create."""

    compose_id: str


def parse_compose_summary(raw: dict[str, object]) -> ComposeSummary:
    """Parse a single compose item from environment list."""
    name = raw.get("name")
    if not isinstance(name, str):
        msg = "malformed response: missing compose 'name'"
        raise TypeError(msg)
    compose_id = raw.get("composeId")
    if not isinstance(compose_id, str) or not compose_id:
        msg = "malformed response: missing compose 'composeId'"
        raise TypeError(msg)
    return ComposeSummary(name=name, compose_id=compose_id)


def parse_environment_response(raw: dict[str, object]) -> EnvironmentResponse:
    """Parse environment API response."""
    compose_items = raw.get("compose")
    if not isinstance(compose_items, list):
        msg = "malformed response: 'compose' is not a list"
        raise TypeError(msg)
    summaries = [parse_compose_summary(item) for item in compose_items if isinstance(item, dict)]
    return EnvironmentResponse(compose=summaries)


def parse_compose_status(raw: dict[str, object]) -> str:
    """Extract compose status string from status poll response."""
    status = raw.get("composeStatus")
    if isinstance(status, str):
        return status
    return "unknown"


def parse_compose_created(raw: dict[str, object]) -> str:
    """Extract composeId from compose.create response."""
    compose_id = raw.get("composeId")
    if isinstance(compose_id, str) and compose_id:
        return compose_id
    msg = "malformed response: missing 'composeId'"
    raise TypeError(msg)
