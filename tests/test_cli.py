"""Tests for the dokployer CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

import dokployer.cli as cli_mod
from dokployer.errors import DeployFailedError
from dokployer.inspector import DokployInspector
from dokployer.logs import ContainerLogStreamer
from dokployer.stack_deployer import StackDeployer


@pytest.fixture
def mock_deployer() -> MagicMock:
    return MagicMock(spec=StackDeployer)


def test_main_parses_arguments(
    tmp_path: Path,
    mock_deployer: MagicMock,
) -> None:
    compose_tmpl = tmp_path / "stack.yml"
    compose_tmpl.write_text("version: '3'\n", encoding="utf-8")
    env_tmpl = tmp_path / ".env"
    env_tmpl.write_text("FOO=bar\n", encoding="utf-8")

    with patch.object(cli_mod, "StackDeployer", return_value=mock_deployer):
        exit_code = cli_mod.main(
            [
                "stack-name",
                "-f",
                str(compose_tmpl),
                "--env",
                str(env_tmpl),
                "--wait",
            ],
        )

    assert exit_code == 0
    mock_deployer.deploy.assert_called_once()
    call_args = mock_deployer.deploy.call_args
    assert call_args.args[0] == "stack-name"
    assert call_args.kwargs["template_path"] == compose_tmpl
    assert call_args.kwargs["env_template_path"] == env_tmpl
    assert call_args.kwargs["wait"] is True


def test_main_returns_zero_on_success(
    tmp_path: Path,
    mock_deployer: MagicMock,
) -> None:
    compose_tmpl = tmp_path / "stack.yml"
    compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

    with patch.object(cli_mod, "StackDeployer", return_value=mock_deployer):
        exit_code = cli_mod.main(
            [
                "stack-name",
                "-f",
                str(compose_tmpl),
            ],
        )

    assert exit_code == 0


def test_main_parses_canonical_deploy_command(
    tmp_path: Path,
    mock_deployer: MagicMock,
) -> None:
    compose_tmpl = tmp_path / "stack.yml"
    compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

    with patch.object(cli_mod, "StackDeployer", return_value=mock_deployer):
        exit_code = cli_mod.main(
            [
                "deploy",
                "app-name",
                "-f",
                str(compose_tmpl),
                "--wait",
            ],
        )

    assert exit_code == 0
    call_args = mock_deployer.deploy.call_args
    assert call_args.args[0] == "app-name"
    assert call_args.kwargs["template_path"] == compose_tmpl
    assert call_args.kwargs["wait"] is True


def test_main_parses_inspect_containers_command(capsys: pytest.CaptureFixture[str]) -> None:
    inspector = MagicMock(spec=DokployInspector)
    inspector.containers.return_value = [{"name": "app_api.1.abc", "state": "running"}]

    with patch.object(cli_mod, "DokployInspector", return_value=inspector):
        exit_code = cli_mod.main(["inspect", "containers", "--running"])

    assert exit_code == 0
    inspector.containers.assert_called_once_with(None, running=True)
    assert "name\tstate" in capsys.readouterr().out


def test_main_prints_inspect_json_when_requested(capsys: pytest.CaptureFixture[str]) -> None:
    inspector = MagicMock(spec=DokployInspector)
    inspector.containers.return_value = [{"name": "app_api.1.abc", "state": "running"}]

    with patch.object(cli_mod, "DokployInspector", return_value=inspector):
        exit_code = cli_mod.main(["inspect", "containers", "--running", "--json"])

    assert exit_code == 0
    assert '"state": "running"' in capsys.readouterr().out


def test_main_prints_inspect_app_text(capsys: pytest.CaptureFixture[str]) -> None:
    inspector = MagicMock(spec=DokployInspector)
    inspector.app.return_value = {"composeId": "cmp-001", "name": "my-app"}

    with patch.object(cli_mod, "DokployInspector", return_value=inspector):
        exit_code = cli_mod.main(["inspect", "app"])

    assert exit_code == 0
    inspector.app.assert_called_once_with(None)
    assert "composeId\tcmp-001" in capsys.readouterr().out


def test_main_parses_inspect_services_command(capsys: pytest.CaptureFixture[str]) -> None:
    inspector = MagicMock(spec=DokployInspector)
    inspector.services.return_value = [{"name": "api"}]

    with patch.object(cli_mod, "DokployInspector", return_value=inspector):
        exit_code = cli_mod.main(["inspect", "services", "my-app"])

    assert exit_code == 0
    inspector.services.assert_called_once_with("my-app")
    assert "name\napi" in capsys.readouterr().out


def test_main_parses_inspect_deployments_command(capsys: pytest.CaptureFixture[str]) -> None:
    inspector = MagicMock(spec=DokployInspector)
    inspector.deployments.return_value = [{"deploymentId": "dep-1", "status": "done"}]

    with patch.object(cli_mod, "DokployInspector", return_value=inspector):
        exit_code = cli_mod.main(["inspect", "deployments", "--limit", "1"])

    assert exit_code == 0
    inspector.deployments.assert_called_once_with(1, None)
    assert "status\tdeploymentId" in capsys.readouterr().out


def test_main_parses_logs_command() -> None:
    streamer = MagicMock(spec=ContainerLogStreamer)

    with patch.object(cli_mod, "ContainerLogStreamer", return_value=streamer):
        exit_code = cli_mod.main(
            [
                "logs",
                "worker",
                "--app-name",
                "my-app",
                "--ssh-host",
                "prod-host",
                "--lines",
                "50",
                "--follow",
            ],
        )

    assert exit_code == 0
    streamer.stream.assert_called_once_with(
        service="worker",
        app_name="my-app",
        ssh_host="prod-host",
        lines=50,
        follow=True,
    )


def test_command_index_stops_after_separator() -> None:
    assert cli_mod._command_index(["--", "inspect"], "inspect") is None


def test_main_returns_one_on_dokploy_error(
    tmp_path: Path,
    mock_deployer: MagicMock,
) -> None:
    compose_tmpl = tmp_path / "stack.yml"
    compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

    mock_deployer.deploy.side_effect = DeployFailedError("deploy failed")

    with patch.object(cli_mod, "StackDeployer", return_value=mock_deployer):
        exit_code = cli_mod.main(
            [
                "stack-name",
                "-f",
                str(compose_tmpl),
            ],
        )

    assert exit_code == 1


def test_main_quiet_mode_sets_logging_to_warning(
    tmp_path: Path,
    mock_deployer: MagicMock,
) -> None:
    compose_tmpl = tmp_path / "stack.yml"
    compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

    with (
        patch.object(cli_mod, "StackDeployer", return_value=mock_deployer),
        patch.object(cli_mod, "_configure_logging") as mock_configure,
    ):
        cli_mod.main(
            [
                "-q",
                "stack-name",
                "-f",
                str(compose_tmpl),
            ],
        )
        mock_configure.assert_called_once_with(verbose=False, quiet=True)


def test_main_verbose_mode_sets_logging_to_debug(
    tmp_path: Path,
    mock_deployer: MagicMock,
) -> None:
    compose_tmpl = tmp_path / "stack.yml"
    compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

    with (
        patch.object(cli_mod, "StackDeployer", return_value=mock_deployer),
        patch.object(cli_mod, "_configure_logging") as mock_configure,
    ):
        cli_mod.main(
            [
                "-v",
                "stack-name",
                "-f",
                str(compose_tmpl),
            ],
        )
        mock_configure.assert_called_once_with(verbose=True, quiet=False)
