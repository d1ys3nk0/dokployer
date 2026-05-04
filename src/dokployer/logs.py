"""SSH-backed Docker log streaming for Dokploy containers."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from dokployer.constants import (
    DEFAULT_DOKPLOY_SSH_HOST,
    DEFAULT_LOG_LINES,
    DEFAULT_LOG_SERVICE,
    DOKPLOY_SSH_HOST,
)
from dokployer.errors import ConfigurationError, DokployerError
from dokployer.inspector import DokployInspector

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dokployer.inspector import DokployInspectClient


class CommandRunner(Protocol):
    """Subprocess runner protocol used for tests."""

    def __call__(
        self,
        args: Sequence[str],
        *,
        check: bool = False,
        capture_output: bool = False,
        text: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command."""
        ...


@dataclass(frozen=True, slots=True)
class RunningContainer:
    """Dokploy running container metadata needed for Docker logs."""

    task_id: str
    name: str


class ContainerLogStreamer:
    """Resolve a Dokploy service container and stream Docker logs over SSH."""

    def __init__(
        self,
        client: DokployInspectClient,
        *,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        """Initialize the log streamer."""
        self._inspector = DokployInspector(client)
        self._runner = runner

    def _resolve_running_container(
        self,
        service: str,
        app_name: str | None,
    ) -> RunningContainer:
        containers = self._inspector.containers(app_name, running=True)
        running_names: list[str] = []
        for container in containers:
            if not isinstance(container, dict):
                continue
            name = container.get("name")
            task_id = container.get("containerId")
            if isinstance(name, str) and name:
                running_names.append(name)
            if isinstance(name, str) and isinstance(task_id, str) and f"_{service}." in name:
                return RunningContainer(task_id=task_id, name=name)

        running = ", ".join(running_names) if running_names else "none"
        msg = f"no running Dokploy container found for service {service!r}; running: {running}"
        raise ConfigurationError(msg)

    def _resolve_docker_container_id(
        self,
        *,
        ssh_host: str,
        container: RunningContainer,
    ) -> str:
        result = self._runner(
            [
                "ssh",
                ssh_host,
                "sudo",
                "docker",
                "ps",
                "--filter",
                f"name={container.name}.{container.task_id}",
                "--format",
                "{{.ID}}",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            msg = f"failed to resolve Docker container id on {ssh_host}"
            raise DokployerError(msg)
        docker_id = result.stdout.splitlines()[0] if result.stdout.splitlines() else ""
        if not docker_id:
            msg = f"no Docker container id found on {ssh_host} for {container.name}"
            raise ConfigurationError(msg)
        return docker_id

    def stream(
        self,
        *,
        service: str = DEFAULT_LOG_SERVICE,
        app_name: str | None = None,
        ssh_host: str | None = None,
        lines: int = DEFAULT_LOG_LINES,
        follow: bool = False,
    ) -> None:
        """Stream Docker logs for a resolved Dokploy service container."""
        target_host = ssh_host or os.environ.get(DOKPLOY_SSH_HOST) or DEFAULT_DOKPLOY_SSH_HOST
        container = self._resolve_running_container(service, app_name)
        docker_id = self._resolve_docker_container_id(
            ssh_host=target_host,
            container=container,
        )
        command = [
            "ssh",
            target_host,
            "sudo",
            "docker",
            "logs",
            "--tail",
            str(lines),
        ]
        if follow:
            command.append("-f")
        command.append(docker_id)
        self._runner(command, check=True, capture_output=False, text=False)
