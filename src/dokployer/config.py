"""Runtime configuration resolution for Dokploy targets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from dokployer.constants import (
    DOKPLOY_API_KEY,
    DOKPLOY_APP,
    DOKPLOY_APP_ID,
    DOKPLOY_APP_NAME,
    DOKPLOY_ENV_ID,
    DOKPLOY_ENVIRONMENT_ID,
    DOKPLOY_SERVICE_ID,
    DOKPLOY_URL,
)
from dokployer.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class DokployConfig:
    """Resolved Dokploy connection and app target configuration."""

    base_url: str
    api_key: str
    environment_id: str | None = None
    app_name: str | None = None
    app_id: str | None = None


def _env_value(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None or value == "":
        return None
    return value


def _resolve_alias(
    env: Mapping[str, str],
    *,
    canonical: str,
    alias: str,
) -> str | None:
    canonical_value = _env_value(env, canonical)
    alias_value = _env_value(env, alias)
    if canonical_value is not None and alias_value is not None and canonical_value != alias_value:
        msg = f"conflicting environment variables: {canonical} and {alias}"
        raise ConfigurationError(msg)
    return canonical_value or alias_value


def _required(env: Mapping[str, str], name: str) -> str:
    value = _env_value(env, name)
    if value is None:
        msg = f"missing required environment variable: {name}"
        raise ConfigurationError(msg)
    return value


def resolve_config(env: Mapping[str, str] | None = None) -> DokployConfig:
    """Resolve Dokploy config from canonical variables and compatibility aliases."""
    environ = os.environ if env is None else env
    return DokployConfig(
        base_url=_required(environ, DOKPLOY_URL).rstrip("/"),
        api_key=_required(environ, DOKPLOY_API_KEY),
        environment_id=_resolve_alias(
            environ,
            canonical=DOKPLOY_ENV_ID,
            alias=DOKPLOY_ENVIRONMENT_ID,
        ),
        app_name=_resolve_alias(
            environ,
            canonical=DOKPLOY_APP_NAME,
            alias=DOKPLOY_APP,
        ),
        app_id=_resolve_alias(
            environ,
            canonical=DOKPLOY_APP_ID,
            alias=DOKPLOY_SERVICE_ID,
        ),
    )
