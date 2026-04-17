"""Tests for the dokployer CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import pytest

import dokployer.cli as cli_mod
from dokployer.dokploy_client import DokployClient


def test_cli_parses_arguments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compose_tmpl = tmp_path / "stack.yml"
    compose_tmpl.write_text("version: '3'\n", encoding="utf-8")
    env_tmpl = tmp_path / ".env"
    env_tmpl.write_text("FOO=bar\n", encoding="utf-8")

    captured: dict[str, Any] = {}

    def fake_deploy_stack(
        _self: DokployClient,
        stack_name: str,
        compose_template: Path | None = None,
        *,
        env_file: Path | None = None,
        wait: bool = False,
    ) -> None:
        captured["stack_name"] = stack_name
        captured["compose_template"] = compose_template
        captured["env_file"] = env_file
        captured["wait"] = wait

    monkeypatch.setattr(DokployClient, "deploy_stack", fake_deploy_stack)

    exit_code = cli_mod.cli(
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
    assert captured == {
        "stack_name": "stack-name",
        "compose_template": compose_tmpl,
        "env_file": env_tmpl,
        "wait": True,
    }
