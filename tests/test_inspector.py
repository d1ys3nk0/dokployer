"""Tests for Dokploy API-only inspection workflows."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dokployer.errors import ConfigurationError
from dokployer.inspector import DokployInspector


def test_inspector_resolves_app_by_env_and_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
    monkeypatch.setenv("DOKPLOY_APP_NAME", "my-app")

    client = MagicMock()
    client.get_environment.return_value = {"compose": [{"name": "my-app", "composeId": "cmp-001"}]}
    client.get_compose.return_value = {"composeId": "cmp-001", "name": "my-app"}

    inspector = DokployInspector(client)

    assert inspector.app() == {"composeId": "cmp-001", "name": "my-app"}
    client.get_compose.assert_called_once_with("cmp-001")


def test_inspector_app_id_wins_over_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
    monkeypatch.setenv("DOKPLOY_APP_NAME", "my-app")
    monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

    client = MagicMock()
    client.get_compose.return_value = {"composeId": "cmp-direct", "name": "other-name"}

    inspector = DokployInspector(client)

    assert inspector.app() == {"composeId": "cmp-direct", "name": "other-name"}
    client.get_environment.assert_not_called()
    client.get_compose.assert_called_once_with("cmp-direct")


def test_inspector_containers_can_filter_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

    client = MagicMock()
    client.get_compose.return_value = {"composeId": "cmp-direct", "name": "my-app"}
    client.get_stack_containers_by_app_name.return_value = [
        {"name": "my-app_api.1.abc", "state": "running"},
        {"name": "my-app_worker.1.def", "state": "exited"},
    ]

    inspector = DokployInspector(client)

    assert inspector.containers(running=True) == [{"name": "my-app_api.1.abc", "state": "running"}]


def test_inspector_uses_compose_app_name_when_name_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

    client = MagicMock()
    client.get_compose.return_value = {"composeId": "cmp-direct", "appName": "my-app"}
    client.get_stack_containers_by_app_name.return_value = []

    inspector = DokployInspector(client)

    assert inspector.containers() == []
    client.get_stack_containers_by_app_name.assert_called_once_with("my-app")


def test_inspector_requires_app_name_for_app_id_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

    client = MagicMock()
    client.get_compose.return_value = {"composeId": "cmp-direct"}

    inspector = DokployInspector(client)

    with pytest.raises(ConfigurationError):
        inspector.containers()


def test_inspector_services_are_derived_from_container_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

    client = MagicMock()
    client.get_compose.return_value = {"composeId": "cmp-direct", "name": "my-app"}
    client.get_stack_containers_by_app_name.return_value = [
        {"name": "my-app_worker.1.def"},
        {"name": "my-app_api.1.abc"},
    ]

    inspector = DokployInspector(client)

    assert inspector.services() == [{"name": "api"}, {"name": "worker"}]


def test_inspector_deployments_honors_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

    client = MagicMock()
    client.get_compose.return_value = {"composeId": "cmp-direct", "name": "my-app"}
    client.get_deployments_by_compose.return_value = [
        {"deploymentId": "dep-1"},
        {"deploymentId": "dep-2"},
    ]

    inspector = DokployInspector(client)

    assert inspector.deployments(limit=1) == [{"deploymentId": "dep-1"}]


def test_inspector_requires_name_without_app_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")

    inspector = DokployInspector(MagicMock())

    with pytest.raises(ConfigurationError):
        inspector.app()


def test_inspector_requires_environment_without_app_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_APP_NAME", "my-app")

    inspector = DokployInspector(MagicMock())

    with pytest.raises(ConfigurationError) as exc_info:
        inspector.containers()

    assert "DOKPLOY_ENV_ID" in str(exc_info.value)


def test_inspector_raises_when_app_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
    monkeypatch.setenv("DOKPLOY_APP_NAME", "missing-app")

    client = MagicMock()
    client.get_environment.return_value = {"compose": []}

    inspector = DokployInspector(client)

    with pytest.raises(ConfigurationError) as exc_info:
        inspector.containers()

    assert "missing-app" in str(exc_info.value)
