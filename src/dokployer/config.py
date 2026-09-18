"""Runtime configuration resolution for Dokploy targets."""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from dokployer.constants import (
    DEFAULT_INTERPOLATION_PREFIX,
    DOKPLOY_API_KEY,
    DOKPLOY_APP_ID,
    DOKPLOY_APP_NAME,
    DOKPLOY_ENV_ID,
    DOKPLOY_URL,
    DOKPLOYER_INTERPOLATION_PREFIX,
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
    interpolation_prefix: str = DEFAULT_INTERPOLATION_PREFIX


def _env_value(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None or value == "":
        return None
    return value


def _required(env: Mapping[str, str], name: str) -> str:
    value = _env_value(env, name)
    if value is None:
        msg = f"missing required environment variable: {name}"
        raise ConfigurationError(msg)
    return value


def _validate_base_url(raw_url: str) -> str:
    url = raw_url.rstrip("/")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc == "":
        msg = "invalid DOKPLOY_URL: expected http(s) URL with scheme and host"
        raise ConfigurationError(msg)
    return url


def _interpolation_prefix(env: Mapping[str, str]) -> str:
    prefix = env.get(DOKPLOYER_INTERPOLATION_PREFIX, DEFAULT_INTERPOLATION_PREFIX)
    if prefix == "":
        msg = f"{DOKPLOYER_INTERPOLATION_PREFIX} must not be empty"
        raise ConfigurationError(msg)
    return prefix


def resolve_config(env: Mapping[str, str] | None = None) -> DokployConfig:
    """Resolve Dokploy config from canonical environment variables."""
    environ = os.environ if env is None else env
    return DokployConfig(
        base_url=_validate_base_url(_required(environ, DOKPLOY_URL)),
        api_key=_required(environ, DOKPLOY_API_KEY),
        environment_id=_env_value(environ, DOKPLOY_ENV_ID),
        app_name=_env_value(environ, DOKPLOY_APP_NAME),
        app_id=_env_value(environ, DOKPLOY_APP_ID),
        interpolation_prefix=_interpolation_prefix(environ),
    )
