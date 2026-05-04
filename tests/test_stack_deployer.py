"""Tests for StackDeployer workflow."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from dokployer.dokploy_client import DokployClient
from dokployer.errors import (
    ConfigurationError,
    DeployFailedError,
    DeployTimeoutError,
)
from dokployer.stack_deployer import StackDeployer
from dokployer.template_manager import ComposeTemplate


class TestStackDeployerWorkflow:
    """Tests for StackDeployer deployment workflow."""

    def test_find_compose_id_returns_id_when_found(self) -> None:
        client = MagicMock(spec=DokployClient)
        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        env_data = {"compose": [{"name": "my-stack", "composeId": "cmp-abc"}]}
        result = deployer._find_compose_id(env_data, "my-stack")

        assert result == "cmp-abc"

    def test_find_compose_id_returns_none_when_not_found(self) -> None:
        client = MagicMock(spec=DokployClient)
        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        env_data = {"compose": [{"name": "other", "composeId": "cmp-xyz"}]}
        result = deployer._find_compose_id(env_data, "my-stack")

        assert result is None

    def test_find_compose_id_returns_none_on_malformed_data(self) -> None:
        client = MagicMock(spec=DokployClient)
        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        env_data = {"compose": "not-a-list"}
        result = deployer._find_compose_id(env_data, "my-stack")

        assert result is None

    def test_deploy_raises_configuration_error_when_dokploy_url_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("DOKPLOY_URL", raising=False)
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = DokployClient()
        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with pytest.raises(ConfigurationError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl)
        assert "DOKPLOY_URL" in str(exc_info.value)

    def test_deploy_raises_configuration_error_when_api_key_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.delenv("DOKPLOY_API_KEY", raising=False)
        monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = DokployClient()
        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with pytest.raises(ConfigurationError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl)
        assert "DOKPLOY_API_KEY" in str(exc_info.value)

    def test_deploy_raises_configuration_error_when_environment_id_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.delenv("DOKPLOY_ENVIRONMENT_ID", raising=False)

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = DokployClient()
        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with pytest.raises(ConfigurationError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl)
        assert "DOKPLOY_ENVIRONMENT_ID" in str(exc_info.value)

    def test_deploy_raises_configuration_error_when_env_file_not_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

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
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
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

        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

        client.update_compose.assert_called_once()
        call_kwargs = client.update_compose.call_args.kwargs
        assert call_kwargs["compose_id"] == "cmp-existing"

    def test_deploy_creates_new_compose_when_not_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
        monkeypatch.setenv("MY_VAR", "test-value")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: $${MY_VAR}\n",
            encoding="utf-8",
        )

        client = MagicMock()
        client.get_environment.return_value = {"compose": []}
        client.create_compose.return_value = {"composeId": "cmp-new"}

        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

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
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENV_ID", "env-new")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {"compose": []}
        client.create_compose.return_value = {"composeId": "cmp-new"}

        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

        client.create_compose.assert_called_once_with(
            name="my-stack",
            environment_id="env-new",
        )

    def test_deploy_uses_app_id_without_environment_lookup(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.delenv("DOKPLOY_ENVIRONMENT_ID", raising=False)
        monkeypatch.delenv("DOKPLOY_ENV_ID", raising=False)
        monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

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
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy(None, template_path=compose_tmpl)

        client.get_environment.assert_not_called()
        client.update_compose.assert_called_once()
        assert client.update_compose.call_args.kwargs["compose_id"] == "cmp-direct"

    def test_deploy_calls_deploy_compose(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
        monkeypatch.setenv("MY_VAR", "test-value")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text(
            "version: '3'\nservices:\n  app:\n    image: $${MY_VAR}\n",
            encoding="utf-8",
        )

        client = MagicMock()
        client.get_environment.return_value = {"compose": []}
        client.create_compose.return_value = {"composeId": "cmp-new"}

        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

        client.deploy_compose.assert_called_once_with("cmp-new")

    def test_deploy_interpolates_template(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
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

        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl)

        call_kwargs = client.update_compose.call_args.kwargs
        assert "myimage:latest" in call_kwargs["compose_file"]

    def test_wait_raises_deploy_failed_error_on_error_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
        monkeypatch.setenv("WAIT_TIMEOUT", "10")
        monkeypatch.setenv("WAIT_INTERVAL", "1")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        client.get_compose_status.return_value = "error"

        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with pytest.raises(DeployFailedError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl, wait=True)
        assert "deploy failed" in str(exc_info.value)

    def test_wait_includes_latest_deployment_log_on_error_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
        monkeypatch.setenv("DOKPLOY_SSH_HOST", "deploy-host")
        monkeypatch.setenv("WAIT_TIMEOUT", "10")
        monkeypatch.setenv("WAIT_INTERVAL", "1")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        client.get_compose_status.return_value = "error"
        client.get_deployments_by_compose.return_value = [
            {
                "deploymentId": "dep-001",
                "status": "error",
                "logPath": "/etc/dokploy/logs/my-stack/my-stack.log",
            }
        ]

        run = MagicMock(
            return_value=subprocess.CompletedProcess(
                ["ssh"],
                0,
                stdout=(
                    "Initializing deployment\n"
                    "Invalid environment variable: environment.INFISICAL_ENCRYPTION_KEY\n"
                    "Error occurred, check the logs for details.\n"
                ),
                stderr="",
            )
        )
        monkeypatch.setattr("subprocess.run", run)

        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with pytest.raises(DeployFailedError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl, wait=True)

        message = str(exc_info.value)
        assert "latest deployment: dep-001" in message
        assert "Invalid environment variable: environment.INFISICAL_ENCRYPTION_KEY" in message
        assert run.call_args.args[0] == [
            "ssh",
            "deploy-host",
            "sudo",
            "tail",
            "-n",
            "200",
            "/etc/dokploy/logs/my-stack/my-stack.log",
        ]

    def test_wait_raises_deploy_timeout_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
        monkeypatch.setenv("WAIT_TIMEOUT", "2")
        monkeypatch.setenv("WAIT_INTERVAL", "1")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        client.get_compose_status.return_value = "running"

        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with pytest.raises(DeployTimeoutError) as exc_info:
            deployer.deploy("my-stack", template_path=compose_tmpl, wait=True)
        assert "timed out" in str(exc_info.value)

    def test_wait_succeeds_when_status_becomes_done(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")
        monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
        monkeypatch.setenv("WAIT_TIMEOUT", "10")
        monkeypatch.setenv("WAIT_INTERVAL", "1")

        compose_tmpl = tmp_path / "stack.yml"
        compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

        client = MagicMock()
        client.get_environment.return_value = {
            "compose": [{"name": "my-stack", "composeId": "cmp-001"}]
        }
        client.get_compose_status.side_effect = ["running", "done"]

        template = ComposeTemplate()
        deployer = StackDeployer(client, template)

        with CaplogForDeployer(deployer):
            deployer.deploy("my-stack", template_path=compose_tmpl, wait=True)


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
