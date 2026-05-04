"""Tests for SSH-backed log streaming."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from dokployer.errors import ConfigurationError, DokployerError
from dokployer.logs import ContainerLogStreamer

if TYPE_CHECKING:
    from collections.abc import Sequence


class RecordingRunner:
    """Record subprocess calls and return configured results."""

    def __init__(self, results: list[subprocess.CompletedProcess[str]]) -> None:
        self.calls: list[Sequence[str]] = []
        self._results = results

    def __call__(
        self,
        args: Sequence[str],
        *,
        check: bool = False,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        _ = (capture_output, text)
        self.calls.append(args)
        result = self._results.pop(0)
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, args)
        return result


def test_stream_resolves_docker_id_and_runs_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

    client = MagicMock()
    client.get_compose.return_value = {"composeId": "cmp-direct", "name": "my-app"}
    client.get_stack_containers_by_app_name.return_value = [
        {"name": "my-app_api.1.abc", "state": "running", "containerId": "task-123"}
    ]
    runner = RecordingRunner(
        [
            subprocess.CompletedProcess(["docker", "ps"], 0, stdout="docker-123\n"),
            subprocess.CompletedProcess(["docker", "logs"], 0),
        ]
    )

    streamer = ContainerLogStreamer(client, runner=runner)

    streamer.stream(service="api", ssh_host="prod-host", lines=50, follow=True)

    assert runner.calls[0] == [
        "ssh",
        "prod-host",
        "sudo",
        "docker",
        "ps",
        "--filter",
        "name=my-app_api.1.abc.task-123",
        "--format",
        "{{.ID}}",
    ]
    assert runner.calls[1] == [
        "ssh",
        "prod-host",
        "sudo",
        "docker",
        "logs",
        "--tail",
        "50",
        "-f",
        "docker-123",
    ]


def test_stream_uses_default_ssh_host_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")
    monkeypatch.setenv("DOKPLOY_SSH_HOST", "env-host")

    client = MagicMock()
    client.get_compose.return_value = {"composeId": "cmp-direct", "name": "my-app"}
    client.get_stack_containers_by_app_name.return_value = [
        {"name": "my-app_worker.1.abc", "state": "running", "containerId": "task-123"}
    ]
    runner = RecordingRunner(
        [
            subprocess.CompletedProcess(["docker", "ps"], 0, stdout="docker-123\n"),
            subprocess.CompletedProcess(["docker", "logs"], 0),
        ]
    )

    ContainerLogStreamer(client, runner=runner).stream(service="worker")

    assert runner.calls[0][1] == "env-host"


def test_stream_raises_when_service_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

    client = MagicMock()
    client.get_compose.return_value = {"composeId": "cmp-direct", "name": "my-app"}
    client.get_stack_containers_by_app_name.return_value = [
        {"name": "my-app_api.1.abc", "state": "running", "containerId": "task-123"}
    ]

    with pytest.raises(ConfigurationError):
        ContainerLogStreamer(client).stream(service="worker")


def test_stream_raises_when_docker_ps_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOKPLOY_URL", "http://localhost")
    monkeypatch.setenv("DOKPLOY_API_KEY", "key")
    monkeypatch.setenv("DOKPLOY_APP_ID", "cmp-direct")

    client = MagicMock()
    client.get_compose.return_value = {"composeId": "cmp-direct", "name": "my-app"}
    client.get_stack_containers_by_app_name.return_value = [
        {"name": "my-app_api.1.abc", "state": "running", "containerId": "task-123"}
    ]
    runner = RecordingRunner([subprocess.CompletedProcess(["docker", "ps"], 1)])

    with pytest.raises(DokployerError):
        ContainerLogStreamer(client, runner=runner).stream(service="api")
