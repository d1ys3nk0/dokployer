"""Tests for Dokploy configuration resolution."""

from __future__ import annotations

import pytest

from dokployer.config import resolve_config
from dokployer.errors import ConfigurationError


def test_resolve_config_reads_canonical_values() -> None:
    config = resolve_config(
        {
            "DOKPLOY_URL": "http://dokploy.local/",
            "DOKPLOY_API_KEY": "key",
            "DOKPLOY_ENV_ID": "env-new",
            "DOKPLOY_APP_NAME": "app-new",
            "DOKPLOY_APP_ID": "cmp-new",
        }
    )

    assert config.base_url == "http://dokploy.local"
    assert config.environment_id == "env-new"
    assert config.app_name == "app-new"
    assert config.app_id == "cmp-new"


def test_resolve_config_ignores_removed_aliases() -> None:
    config = resolve_config(
        {
            "DOKPLOY_URL": "http://dokploy.local",
            "DOKPLOY_API_KEY": "key",
            "DOKPLOY_ENVIRONMENT_ID": "env-legacy",
            "DOKPLOY_APP": "app-legacy",
            "DOKPLOY_SERVICE_ID": "cmp-legacy",
        }
    )

    assert config.environment_id is None
    assert config.app_name is None
    assert config.app_id is None


def test_resolve_config_rejects_url_without_scheme() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_config({"DOKPLOY_URL": "dokploy.local", "DOKPLOY_API_KEY": "key"})

    assert "DOKPLOY_URL" in str(exc_info.value)
    assert "scheme" in str(exc_info.value)
