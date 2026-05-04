"""Tests for StackDeployer workflow."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from dokployer.config import resolve_config
from dokployer.dokploy_client import DokployClient
from dokployer.errors import (
    ConfigurationError,
    DeployFailedError,
    DeployTimeoutError,
)
from dokployer.stack_deployer import ExpectedService, StackDeployer
from dokployer.template_manager import ComposeTemplate


def _deployer(client: object, template: ComposeTemplate) -> StackDeployer:
    return StackDeployer(client, template, resolve_config())


def _fast_wait_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 0.0

    def monotonic() -> float:
        nonlocal now
        now += 1.0
        return now

    monkeypatch.setattr("dokployer.stack_deployer.time.monotonic", monotonic)
    monkeypatch.setattr("dokployer.stack_deployer.time.sleep", lambda _seconds: None)


def _successful_deploy_status(client: MagicMock) -> None:
    client.get_compose_status.return_value = "done"
    client.get_deployments_by_compose.side_effect = [
        [],
        [{"deploymentId": "dep-001", "status": "done"}],
    ]


class TestStackDeployerWorkflow:
    """Tests for StackDeployer deployment workflow."""

    def test_find_compose_id_returns_id_when_found(self) -> None:
        client = MagicMock(spec=DokployClient)
        template = ComposeTemplate()
        deployer = StackDeployer(
            client,
            template,
            resolve_config({"DOKPLOY_URL": "http://localhost", "DOKPLOY_API_KEY": "key"}),
        )

        env_data = {"compose": [{"name": "my-stack", "composeId": "cmp-abc"}]}
        result = deployer._find_compose_id(env_data, "my-stack")

        assert result == "cmp-abc"

    def test_find_compose_id_returns_none_when_not_found(self) -> None:
        client = MagicMock(spec=DokployClient)
        template = ComposeTemplate()
        deployer = StackDeployer(
            client,
            template,
            resolve_config({"DOKPLOY_URL": "http://localhost", "DOKPLOY_API_KEY": "key"}),
        )

        env_data = {"compose": [{"name": "other", "composeId": "cmp-xyz"}]}
        result = deployer._find_compose_id(env_data, "my-stack")

        assert result is None

    def test_find_compose_id_returns_none_on_malformed_data(self) -> None:
        client = MagicMock(spec=DokployClient)
        template = ComposeTemplate()
        deployer = StackDeployer(
            client,
            template,
            resolve_config({"DOKPLOY_URL": "http://localhost", "DOKPLOY_API_KEY": "key"}),
        )

        env_data = {"compose": "not-a-list"}
        result = deployer._find_compose_id(env_data, "my-stack")

        assert result is None

    def test_deploy_raises_configuration_error_when_dokploy_url_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("DOKPLOY_URL", raising=False)
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            resolve_config()
        assert "DOKPLOY_URL" in str(exc_info.value)

    def test_deploy_raises_configuration_error_when_api_key_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.delenv("DOKPLOY_API_KEY", raising=False)
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        with pytest.raises(ConfigurationError) as exc_info:
            resolve_config()
        assert "DOKPLOY_API_KEY" in str(exc_info.value)

    def test_deploy_raises_configuration_error_when_environment_id_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.delenv("DOKPLOY_ENV_ID", raising=False)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(ConfigurationError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl)
        assert "DOKPLOY_ENV_ID" in str(exc_info.value)

    def test_deploy_raises_configuration_error_when_env_file_not_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(ConfigurationError) as exc_info:
            deployer.deploy(
                "my-stack",
                template_path=compose_tmpl,
                env_template_path=tmp_path / "nonexistent.env",
            )
        assert "env file not found" in str(exc_info.value)

    def test_deploy_uses_existing_compose_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _fast_wait_clock(monkeypatch)
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("MY_VAR", "test-value")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: $${MY_VAR}\n",
            encoding="utf-8",
        )

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-existing"}]
        }
        _successful_deploy_status(client)

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

        client.update_compose.assert_called_once()
        call_kwargs = client.update_compose.call_args.kwargs
        assert call_kwargs["compose_id"] == "cmp-existing"

    def test_deploy_creates_new_compose_when_not_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _fast_wait_clock(monkeypatch)
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("MY_VAR", "test-value")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: $${MY_VAR}\n",
            encoding="utf-8",
        )

        client = MagicMock()
        client.get_environment.return_value = {"compose": []}
        client.create_compose.return_value = {"composeId": "cmp-new"}
        _successful_deploy_status(client)

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

        client.create_compose.assert_called_once_with(
            name="my-stack",
            environment_id="env-001",
        )
        client.update_compose.assert_called_once()
        call_kwargs = client.update_compose.call_args.kwargs
        assert call_kwargs["compose_id"] == "cmp-new"

    def test_deploy_uses_canonical_env_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _fast_wait_clock(monkeypatch)
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-new")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {"compose": []}
        client.create_compose.return_value = {"composeId": "cmp-new"}
        _successful_deploy_status(client)

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

        client.create_compose.assert_called_once_with(
            name="my-stack",
            environment_id="env-new",
        )

    def test_deploy_uses_app_id_without_environment_lookup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _fast_wait_clock(monkeypatch)
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.delenv("DOKPLOY_ENV_ID", raising=False)
        monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        _successful_deploy_status(client)
        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

        client.get_environment.assert_not_called()
        client.create_compose.assert_not_called()
        client.update_compose.assert_called_once()
        call_kwargs = client.update_compose.call_args.kwargs
        assert call_kwargs["compose_id"] == "cmp-direct"

    def test_deploy_allows_app_id_without_app_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _fast_wait_clock(monkeypatch)
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        _successful_deploy_status(client)
        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy(None, template_path=compose_tmpl)

        client.get_environment.assert_not_called()
        client.update_compose.assert_called_once()
        assert client.update_compose.call_args.kwargs["compose_id"] == "cmp-direct"

    def test_deploy_calls_deploy_compose(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _fast_wait_clock(monkeypatch)
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("MY_VAR", "test-value")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: $${MY_VAR}\n",
            encoding="utf-8",
        )

        client = MagicMock()
        client.get_environment.return_value = {"compose": []}
        client.create_compose.return_value = {"composeId": "cmp-new"}
        _successful_deploy_status(client)

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

        client.deploy_compose.assert_called_once_with("cmp-new")

    def test_deploy_interpolates_template(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _fast_wait_clock(monkeypatch)
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("DEPLOY_IMAGE", "myimage:latest")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: $${DEPLOY_IMAGE}\n",
            encoding="utf-8",
        )

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        _successful_deploy_status(client)

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

        call_kwargs = client.update_compose.call_args.kwargs
        assert "myimage:latest" in call_kwargs["compose_file"]

    def test_wait_raises_deploy_failed_error_on_error_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        client.get_compose_status.return_value = "error"
        client.get_deployments_by_compose.side_effect = [
            [],
            [{"deploymentId": "dep-001", "status": "error"}],
            [{"deploymentId": "dep-001", "status": "error"}],
        ]

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(DeployFailedError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl)
        assert "deploy failed" in str(exc_info.value)

    def test_wait_includes_latest_deployment_metadata_without_ssh(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        client.get_compose_status.return_value = "error"
        deployment = {
            "deploymentId": "dep-001",
            "status": "error",
            "logPath": "/etc/dokploy/logs/my-stack/my-stack.log",
            "errorMessage": "Invalid environment variable: environment.INFISICAL_ENCRYPTION_KEY",
        }
        client.get_deployments_by_compose.side_effect = [[], [deployment], [deployment]]

        run = MagicMock()
        monkeypatch.setattr("subprocess.run", run)

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(DeployFailedError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl)

        message = str(exc_info.value)
        assert "latest deployment: dep-001" in message
        assert "deployment log path: /etc/dokploy/logs/my-stack/my-stack.log" in message
        assert "Invalid environment variable: environment.INFISICAL_ENCRYPTION_KEY" in message
        run.assert_not_called()

    def test_wait_raises_deploy_timeout_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "2")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        client.get_compose_status.return_value = "running"
        client.get_deployments_by_compose.return_value = []

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(DeployTimeoutError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl)
        assert "timed out" in str(exc_info.value)

    def test_wait_succeeds_when_status_becomes_done(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        client.get_compose_status.side_effect = ["running", "done"]
        client.get_deployments_by_compose.side_effect = [
            [],
            [{"deploymentId": "dep-001", "status": "running"}],
            [{"deploymentId": "dep-001", "status": "done"}],
        ]

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

    def test_wait_ignores_removed_wait_env_names(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("WAIT_TIMEOUT", "foo")
        monkeypatch.setenv("WAIT_INTERVAL", "foo")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        client.get_compose_status.side_effect = ["running", "done"]
        client.get_deployments_by_compose.side_effect = [
            [],
            [{"deploymentId": "dep-001", "status": "running"}],
            [{"deploymentId": "dep-001", "status": "done"}],
        ]

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

    def test_wait_ignores_previous_done_status_until_new_deployment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        client.get_compose_status.side_effect = ["done", "done"]
        client.get_deployments_by_compose.side_effect = [
            [{"deploymentId": "dep-old", "status": "done"}],
            [{"deploymentId": "dep-old", "status": "done"}],
            [{"deploymentId": "dep-new", "status": "done"}],
        ]

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

        assert client.get_compose_status.call_count == 2

    @pytest.mark.parametrize(
        "name",
        [
            "DEPLOY_POLL_TIMEOUT",
            "DEPLOY_POLL_INTERVAL",
            "STACK_POLL_TIMEOUT",
            "STACK_POLL_INTERVAL",
        ],
    )
    @pytest.mark.parametrize("value", ["foo", "0"])
    def test_wait_rejects_invalid_wait_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        name: str,
        value: str,
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv(name, value)
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: app:latest\n",
            encoding="utf-8",
        )

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        _successful_deploy_status(client)

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(ConfigurationError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl, wait=True)

        assert name in str(exc_info.value)

    def test_wait_raises_after_repeated_unknown_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        client.get_compose_status.return_value = "unknown"
        client.get_deployments_by_compose.side_effect = [
            [],
            [{"deploymentId": "dep-001", "status": "running"}],
            [{"deploymentId": "dep-001", "status": "running"}],
            [{"deploymentId": "dep-001", "status": "running"}],
            [{"deploymentId": "dep-001", "status": "running"}],
        ]

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(DeployFailedError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl)

        assert "unknown status" in str(exc_info.value)

    def test_container_wait_succeeds_for_running_expected_images(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        monkeypatch.setenv("STACK_POLL_INTERVAL", "1")
        monkeypatch.setenv("STACK_POLL_TIMEOUT", "300")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            """version: '3'
services:
  app:
    image: myimage:latest
    deploy:
      replicas: 2
  worker:
    image: worker:latest
    deploy:
      replicas: 0
""",
            encoding="utf-8",
        )

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        _successful_deploy_status(client)
        client.get_stack_containers_by_app_name.return_value = [
            {"containerId": "ctr-1", "name": "my-stack_app.1.abc"},
            {"containerId": "ctr-2", "name": "my-stack_app.2.def"},
        ]
        client.get_container_config.side_effect = [
            {
                "State": {"Status": "running", "Health": {"Status": "healthy"}},
                "Config": {"Image": "myimage:latest"},
            },
            {
                "State": {"Status": "running"},
                "Config": {"Image": "myimage:latest@sha256:abc"},
            },
        ]

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl, wait=60)

        client.get_stack_containers_by_app_name.assert_called_once_with("my-stack")

    def test_container_wait_uses_stack_poll_timeout_for_bare_wait(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        monkeypatch.setenv("STACK_POLL_TIMEOUT", "2")
        monkeypatch.setenv("STACK_POLL_INTERVAL", "1")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: new:latest\n",
            encoding="utf-8",
        )

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        _successful_deploy_status(client)
        client.get_stack_containers_by_app_name.return_value = [
            {"containerId": "ctr-1", "name": "my-stack_app.1.abc"}
        ]
        client.get_container_config.return_value = {
            "State": {"Status": "running"},
            "Config": {"Image": "old:latest"},
        }

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(DeployTimeoutError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl, wait=True)

        assert "container readiness timed out after 2s" in str(exc_info.value)

    def test_container_wait_times_out_on_stale_image(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        monkeypatch.setenv("STACK_POLL_INTERVAL", "1")
        monkeypatch.setenv("STACK_POLL_TIMEOUT", "300")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: new:latest\n",
            encoding="utf-8",
        )

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        _successful_deploy_status(client)
        client.get_stack_containers_by_app_name.return_value = [
            {"containerId": "ctr-1", "name": "my-stack_app.1.abc"}
        ]
        client.get_container_config.return_value = {
            "State": {"Status": "running"},
            "Config": {"Image": "old:latest"},
        }

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(DeployTimeoutError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl, wait=2)

        message = str(exc_info.value)
        assert "container readiness timed out after 2s" in message
        assert "image=old:latest" in message

    def test_container_wait_requires_service_image(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\nservices:\n  app: {}\n", encoding="utf-8")

        client = MagicMock()
        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(ConfigurationError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl, wait=60)

        assert "must define image" in str(exc_info.value)
        client.get_environment.assert_not_called()

    def test_container_wait_rejects_global_mode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-001")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            """version: '3'
services:
  app:
    image: myimage:latest
    deploy:
      mode: global
""",
            encoding="utf-8",
        )

        client = MagicMock()
        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(ConfigurationError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl, wait=60)

        assert "global service mode" in str(exc_info.value)

    def test_container_wait_resolves_app_name_from_compose_metadata(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        monkeypatch.setenv("STACK_POLL_INTERVAL", "1")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: myimage:latest\n",
            encoding="utf-8",
        )

        client = MagicMock()
        _successful_deploy_status(client)
        client.get_compose.return_value = {"composeId": "cmp-direct", "appName": "real-stack"}
        client.get_stack_containers_by_app_name.return_value = [
            {"containerId": "ctr-1", "name": "real-stack_app.1.abc"}
        ]
        client.get_container_config.return_value = {
            "State": {"Status": "running"},
            "Config": {"Image": "myimage:latest"},
        }

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy(None, template_path=compose_tmpl, wait=60)

        client.get_stack_containers_by_app_name.assert_called_once_with("real-stack")

    def test_container_wait_prefers_compose_metadata_for_direct_app_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        monkeypatch.setenv("STACK_POLL_INTERVAL", "1")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: myimage:latest\n",
            encoding="utf-8",
        )

        client = MagicMock()
        _successful_deploy_status(client)
        client.get_compose.return_value = {"composeId": "cmp-direct", "name": "real-stack"}
        client.get_stack_containers_by_app_name.return_value = [
            {"containerId": "ctr-1", "name": "real-stack_app.1.abc"}
        ]
        client.get_container_config.return_value = {
            "State": {"Status": "running"},
            "Config": {"Image": "myimage:latest"},
        }

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("cli-alias", template_path=compose_tmpl, wait=60)

        client.get_stack_containers_by_app_name.assert_called_once_with("real-stack")

    def test_container_wait_requires_resolved_app_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")
        monkeypatch.setenv("DEPLOY_POLL_TIMEOUT", "10")
        monkeypatch.setenv("DEPLOY_POLL_INTERVAL", "1")
        monkeypatch.setenv("STACK_POLL_INTERVAL", "1")
        _fast_wait_clock(monkeypatch)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: myimage:latest\n",
            encoding="utf-8",
        )

        client = MagicMock()
        _successful_deploy_status(client)
        client.get_compose.return_value = {"composeId": "cmp-direct"}

        template = ComposeTemplate()
        deployer = _deployer(client, template)

        with pytest.raises(ConfigurationError) as exc_info:
            deployer.deploy(None, template_path=compose_tmpl, wait=60)

        assert "missing app name" in str(exc_info.value)

    def test_parse_expected_services_rejects_invalid_stack_shapes(self) -> None:
        client = MagicMock()
        template = ComposeTemplate()
        deployer = StackDeployer(
            client,
            template,
            resolve_config({"DOKPLOY_URL": "http://localhost", "DOKPLOY_API_KEY": "key"}),
        )

        invalid_stacks = [
            "[]\n",
            "version: '3'\n",
            "services:\n  app: []\n",
            "services:\n  app:\n    image: app:latest\n    deploy:\n      replicas: many\n",
            "services:\n  app:\n    image: app:latest\n    deploy:\n      replicas: -1\n",
        ]

        for stack in invalid_stacks:
            with pytest.raises(ConfigurationError):
                deployer._parse_expected_services(stack)

    def test_containers_ready_reports_uninspectable_containers(self) -> None:
        client = MagicMock()
        client.get_container_config.side_effect = DeployFailedError("inspect failed")
        template = ComposeTemplate()
        deployer = StackDeployer(
            client,
            template,
            resolve_config({"DOKPLOY_URL": "http://localhost", "DOKPLOY_API_KEY": "key"}),
        )

        ready, report = deployer._containers_ready(
            [
                "not a container",
                {"name": ""},
                {"name": "stack_other.1.abc", "containerId": "ctr-other"},
                {"name": "stack_app.1.abc", "containerId": "No container id"},
            ],
            [ExpectedService(name="app", image="app:latest", replicas=1)],
        )

        assert ready is False
        assert "missing container id" in report
        client.get_container_config.assert_not_called()

    def test_container_config_helpers_handle_missing_fields(self) -> None:
        client = MagicMock()
        template = ComposeTemplate()
        deployer = StackDeployer(
            client,
            template,
            resolve_config({"DOKPLOY_URL": "http://localhost", "DOKPLOY_API_KEY": "key"}),
        )

        assert deployer._container_state({}) is None
        assert deployer._container_health({}) is None
        assert deployer._container_image({"Image": "sha256:abc"}) == "sha256:abc"


class CaplogForDeployer:
    """Context manager to capture logs at INFO level for deployer tests."""

    def __init__(self, deployer: StackDeployer) -> None:
        self._deployer = deployer
        self._handler: logging.Handler | None = None
        self._logger: logging.Logger | None = None

    def __enter__(self) -> logging.LogRecord | None:
        self._logger = logging.getLogger("dokployer.stack_deployer")
        self._handler = logging.Handler()
        self._handler.setLevel(logging.INFO)
        self._logger.addHandler(self._handler)
        return None

    def __exit__(self, *args: object) -> None:
        if self._handler and self._logger:
            self._logger.removeHandler(self._handler)
