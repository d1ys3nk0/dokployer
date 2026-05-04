"""Shared test fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clear_dokploy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Dokploy env resolution tests isolated from the shell environment."""
    for name in (
        "DOKPLOY_URL",
        "DOKPLOY_API_KEY",
        "DOKPLOY_ENV_ID",
        "DOKPLOY_ENVIRONMENT_ID",
        "DOKPLOY_APP_NAME",
        "DOKPLOY_APP",
        "DOKPLOY_APP_ID",
        "DOKPLOY_SERVICE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
