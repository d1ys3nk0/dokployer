"""Read-only Dokploy inspection workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dokployer.config import DokployConfig, resolve_config
from dokployer.errors import ConfigurationError
from dokployer.models import parse_environment_response


@dataclass(frozen=True, slots=True)
class ResolvedApp:
    """Resolved Dokploy compose app target."""

    compose_id: str
    app_name: str | None


class DokployInspectClient(Protocol):
    """Client methods used by read-only inspection workflows."""

    base_url: str
    api_key: str

    def get_environment(self, environment_id: str) -> dict[str, object]:
        """Fetch environment data from Dokploy."""
        ...

    def get_compose(self, compose_id: str) -> dict[str, object]:
        """Fetch compose app details."""
        ...

    def get_stack_containers_by_app_name(self, app_name: str) -> list[object]:
        """Fetch stack containers by app name."""
        ...

    def get_deployments_by_compose(self, compose_id: str) -> list[object]:
        """Fetch compose deployments."""
        ...


class DokployInspector:
    """API-only Dokploy app inspection helper."""

    def __init__(self, client: DokployInspectClient) -> None:
        """Initialize inspector with a Dokploy API client."""
        self._client = client

    def _configure_client(self, config: DokployConfig) -> None:
        self._client.base_url = config.base_url
        self._client.api_key = config.api_key

    def _compose_name(self, compose: dict[str, object]) -> str | None:
        name = compose.get("name")
        if isinstance(name, str) and name:
            return name
        app_name = compose.get("appName")
        if isinstance(app_name, str) and app_name:
            return app_name
        return None

    def _resolve_app(self, app_name: str | None = None) -> ResolvedApp:
        config = resolve_config()
        self._configure_client(config)

        if config.app_id is not None:
            compose = self._client.get_compose(config.app_id)
            return ResolvedApp(
                compose_id=config.app_id,
                app_name=app_name or self._compose_name(compose) or config.app_name,
            )

        target_name = app_name or config.app_name
        if target_name is None:
            msg = "missing app name: pass APP_NAME or set DOKPLOY_APP_NAME"
            raise ConfigurationError(msg)
        if config.environment_id is None:
            msg = "missing required environment variable: DOKPLOY_ENV_ID or DOKPLOY_ENVIRONMENT_ID"
            raise ConfigurationError(msg)

        env_data = self._client.get_environment(config.environment_id)
        env_resp = parse_environment_response(env_data)
        for compose_summary in env_resp.compose:
            if compose_summary.name == target_name:
                return ResolvedApp(
                    compose_id=compose_summary.compose_id,
                    app_name=target_name,
                )

        msg = f"compose app not found in environment: {target_name}"
        raise ConfigurationError(msg)

    def app(self, app_name: str | None = None) -> dict[str, object]:
        """Return compose app details."""
        config = resolve_config()
        self._configure_client(config)
        if config.app_id is not None:
            return self._client.get_compose(config.app_id)
        app = self._resolve_app(app_name)
        return self._client.get_compose(app.compose_id)

    def containers(
        self,
        app_name: str | None = None,
        *,
        running: bool = False,
    ) -> list[object]:
        """Return containers for the resolved app."""
        app = self._resolve_app(app_name)
        if app.app_name is None:
            msg = "missing app name: DOKPLOY_APP_ID target did not expose a compose name"
            raise ConfigurationError(msg)
        containers = self._client.get_stack_containers_by_app_name(app.app_name)
        if not running:
            return containers
        return [
            container
            for container in containers
            if isinstance(container, dict) and container.get("state") == "running"
        ]

    def services(self, app_name: str | None = None) -> list[dict[str, object]]:
        """Return service names derived from app containers."""
        containers = self.containers(app_name)
        names: set[str] = set()
        for container in containers:
            if not isinstance(container, dict):
                continue
            name = container.get("name")
            if isinstance(name, str) and name:
                names.add(_service_name_from_container(name))
        return [{"name": name} for name in sorted(names)]

    def deployments(self, limit: int, app_name: str | None = None) -> list[object]:
        """Return recent deployments for the resolved app."""
        app = self._resolve_app(app_name)
        return self._client.get_deployments_by_compose(app.compose_id)[:limit]


def _service_name_from_container(container_name: str) -> str:
    parts = container_name.split("_", maxsplit=1)
    scoped = parts[1] if len(parts) == _SCOPED_CONTAINER_PARTS else container_name
    return scoped.split(".", maxsplit=1)[0]


_SCOPED_CONTAINER_PARTS = 2
