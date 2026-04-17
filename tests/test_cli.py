"""Tests for the dokployer CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

import dokployer.cli as cli_mod
from dokployer.errors import DeployFailedError
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
