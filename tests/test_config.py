"""Tests for Dokploy configuration resolution."""

from __future__ import annotations

import pytest

from dokployer.config import resolve_config
from dokployer.errors import ConfigurationError


def test_resolve_config_prefers_canonical_aliases() -> None:
    config = resolve_config(
        {
            "DOKPLOY_URL": "http://dokploy.local/",
            "DOKPLOY_API_KEY": "key",
            "DOKPLOY_ENV_ID": "env-new",
            "DOKPLOY_ENVIRONMENT_ID": "env-new",
            "DOKPLOY_APP_NAME": "app-new",
            "DOKPLOY_APP": "app-new",
            "DOKPLOY_APP_ID": "cmp-new",
            "DOKPLOY_SERVICE_ID": "cmp-new",
        }
    )

    assert config.base_url == "http://dokploy.local"
    assert config.environment_id == "env-new"
    assert config.app_name == "app-new"
    assert config.app_id == "cmp-new"


@pytest.mark.parametrize(
    ("canonical", "alias"),
    [
        ("DOKPLOY_ENV_ID", "DOKPLOY_ENVIRONMENT_ID"),
        ("DOKPLOY_APP_NAME", "DOKPLOY_APP"),
        ("DOKPLOY_APP_ID", "DOKPLOY_SERVICE_ID"),
    ],
)
def test_resolve_config_rejects_conflicting_aliases(canonical: str, alias: str) -> None:
    env = {
        "DOKPLOY_URL": "http://dokploy.local",
        "DOKPLOY_API_KEY": "key",
        canonical: "new",
        alias: "legacy",
    }

    with pytest.raises(ConfigurationError) as exc_info:
        resolve_config(env)

    assert canonical in str(exc_info.value)
    assert alias in str(exc_info.value)
