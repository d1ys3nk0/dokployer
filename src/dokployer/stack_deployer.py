"""Stack deployment workflow using DokployClient and ComposeTemplate."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import yaml

from dokployer.constants import (
    DEFAULT_DEPLOY_POLL_INTERVAL_SECONDS,
    DEFAULT_DEPLOY_WAIT_TIMEOUT_SECONDS,
    WAIT_INTERVAL,
    WAIT_TIMEOUT,
    ComposeStatus,
)
from dokployer.errors import (
    ConfigurationError,
    DeployFailedError,
    DeployTimeoutError,
    DokployAPIError,
)
from dokployer.models import parse_compose_created, parse_environment_response

if TYPE_CHECKING:
    from pathlib import Path

    from dokployer.config import DokployConfig
    from dokployer.dokploy_client import DokployClient
    from dokployer.template_manager import ComposeTemplate


logger = logging.getLogger(__name__)
MAX_UNKNOWN_STATUS_POLLS = 3


@dataclass(frozen=True, slots=True)
class ExpectedService:
    """Expected service state derived from the interpolated stack."""

    name: str
    image: str
    replicas: int


class StackDeployer:
    """Orchestrates compose template interpolation and deployment via DokployClient."""

    def __init__(
        self,
        client: DokployClient,
        template: ComposeTemplate,
        config: DokployConfig,
    ) -> None:
        """Initialize StackDeployer with a client and template."""
        self._client = client
        self._templates = template
        self._config = config

    def _find_compose_id(self, env_data: dict[str, object], stack_name: str) -> str | None:
        """Find compose ID for a stack by name from environment data."""
        try:
            env_resp = parse_environment_response(env_data)
        except TypeError:
            return None
        for compose in env_resp.compose:
            if compose.name == stack_name:
                return compose.compose_id
        return None

    def _deploy_failure_message(self, compose_id: str, stack_name: str) -> str:
        lines = [f"deploy failed: {stack_name}"]
        try:
            deployments = self._client.get_deployments_by_compose(compose_id)
        except DokployAPIError as exc:
            lines.append(f"unable to fetch deployment metadata: {exc}")
            return "\n".join(lines)

        latest = deployments[0] if deployments and isinstance(deployments[0], dict) else None
        if latest is None:
            lines.append("latest deployment metadata: not found")
            return "\n".join(lines)

        deployment_id = latest.get("deploymentId")
        if isinstance(deployment_id, str) and deployment_id:
            lines.append(f"latest deployment: {deployment_id}")
        log_path = latest.get("logPath")
        if isinstance(log_path, str) and log_path:
            lines.append(f"deployment log path: {log_path}")

        error_message = latest.get("errorMessage")
        if isinstance(error_message, str) and error_message:
            lines.append(error_message)

        if not error_message:
            lines.append("deployment log: not available")

        return "\n".join(lines)

    def _deployment_id(self, deployment: object) -> str | None:
        if not isinstance(deployment, dict):
            return None
        deployment_id = deployment.get("deploymentId")
        return deployment_id if isinstance(deployment_id, str) and deployment_id else None

    def _deployment_status(self, deployment: object) -> str | None:
        if not isinstance(deployment, dict):
            return None
        status = deployment.get("status") or deployment.get("state")
        return status if isinstance(status, str) and status else None

    def _latest_deployment(self, compose_id: str) -> dict[str, object] | None:
        deployments = self._client.get_deployments_by_compose(compose_id)
        return deployments[0] if deployments and isinstance(deployments[0], dict) else None

    def _latest_deployment_id(self, compose_id: str) -> str | None:
        return self._deployment_id(self._latest_deployment(compose_id))

    def _wait_value(self, name: str, default: int) -> int:
        raw = os.environ.get(name, str(default))
        try:
            value = int(raw)
        except ValueError as exc:
            msg = f"invalid {name}: expected a positive integer, got {raw!r}"
            raise ConfigurationError(msg) from exc
        if value <= 0:
            msg = f"invalid {name}: expected a positive integer, got {raw!r}"
            raise ConfigurationError(msg)
        return value

    def _parse_expected_services(self, compose_file_content: str) -> list[ExpectedService]:
        raw = yaml.safe_load(compose_file_content)
        if not isinstance(raw, dict):
            msg = "stack YAML must be a mapping"
            raise ConfigurationError(msg)

        raw_services = raw.get("services")
        if not isinstance(raw_services, dict) or not raw_services:
            msg = "stack YAML must define at least one service"
            raise ConfigurationError(msg)

        expected: list[ExpectedService] = []
        for raw_name, raw_service in raw_services.items():
            if not isinstance(raw_name, str) or not raw_name:
                msg = "stack service names must be non-empty strings"
                raise ConfigurationError(msg)
            if not isinstance(raw_service, dict):
                msg = f"stack service must be a mapping: {raw_name}"
                raise ConfigurationError(msg)

            image = raw_service.get("image")
            if not isinstance(image, str) or not image:
                msg = f"stack service must define image for readiness checks: {raw_name}"
                raise ConfigurationError(msg)

            raw_deploy = raw_service.get("deploy")
            deploy = raw_deploy if isinstance(raw_deploy, dict) else {}
            mode = deploy.get("mode")
            if mode == "global":
                msg = f"global service mode is not supported for readiness checks: {raw_name}"
                raise ConfigurationError(msg)
            replicas = self._replica_count(raw_name, deploy.get("replicas", 1))
            if replicas > 0:
                expected.append(ExpectedService(name=raw_name, image=image, replicas=replicas))

        return expected

    def _replica_count(self, service_name: str, raw_replicas: object) -> int:
        if isinstance(raw_replicas, bool) or not isinstance(raw_replicas, int):
            msg = f"deploy.replicas must be a non-negative integer for service: {service_name}"
            raise ConfigurationError(msg)
        if raw_replicas < 0:
            msg = f"deploy.replicas must be a non-negative integer for service: {service_name}"
            raise ConfigurationError(msg)
        return raw_replicas

    def _wait_for_deploy(
        self,
        compose_id: str,
        stack_name: str,
        previous_deployment_id: str | None,
    ) -> None:
        """Poll until deploy completes or times out."""
        timeout = self._wait_value(WAIT_TIMEOUT, DEFAULT_DEPLOY_WAIT_TIMEOUT_SECONDS)
        interval = self._wait_value(WAIT_INTERVAL, DEFAULT_DEPLOY_POLL_INTERVAL_SECONDS)
        deadline = time.monotonic() + timeout
        unknown_polls = 0
        target_deployment_id: str | None = None

        while time.monotonic() < deadline:
            time.sleep(interval)
            status_str = self._client.get_compose_status(compose_id)
            logger.info("status: %s", status_str)
            if status_str == ComposeStatus.UNKNOWN:
                unknown_polls += 1
                if unknown_polls == 1:
                    logger.warning("compose.one returned no composeStatus for %s", stack_name)
                if unknown_polls > MAX_UNKNOWN_STATUS_POLLS:
                    msg = f"compose.one returned unknown status {unknown_polls} times: {stack_name}"
                    raise DeployFailedError(msg)
            else:
                unknown_polls = 0

            latest = self._latest_deployment(compose_id)
            latest_deployment_id = self._deployment_id(latest)
            if (
                target_deployment_id is None
                and latest_deployment_id is not None
                and latest_deployment_id != previous_deployment_id
            ):
                target_deployment_id = latest_deployment_id

            if target_deployment_id is None:
                continue

            deployment_status = self._deployment_status(latest)
            effective_status = deployment_status or status_str
            if effective_status == ComposeStatus.DONE:
                logger.info("Deploy OK: %s", stack_name)
                return
            if effective_status == ComposeStatus.ERROR:
                raise DeployFailedError(self._deploy_failure_message(compose_id, stack_name))

        msg = f"deploy timed out after {timeout}s: {stack_name}"
        raise DeployTimeoutError(msg)

    def _compose_app_name(self, stack_name: str | None, compose_id: str) -> str:
        app_name = stack_name or self._config.app_name
        if app_name is not None:
            return app_name

        compose = self._client.get_compose(compose_id)
        name = compose.get("name")
        if isinstance(name, str) and name:
            return name
        compose_app_name = compose.get("appName")
        if isinstance(compose_app_name, str) and compose_app_name:
            return compose_app_name

        msg = "missing app name: DOKPLOY_APP_ID target did not expose a compose name"
        raise ConfigurationError(msg)

    def _wait_for_containers(
        self,
        app_name: str,
        expected_services: list[ExpectedService],
        timeout: int,
    ) -> None:
        deadline = time.monotonic() + timeout
        interval = self._wait_value(WAIT_INTERVAL, DEFAULT_DEPLOY_POLL_INTERVAL_SECONDS)
        last_report = "container status: not checked"

        while time.monotonic() < deadline:
            containers = self._client.get_stack_containers_by_app_name(app_name)
            ready, report = self._containers_ready(containers, expected_services)
            last_report = report
            if ready:
                logger.info("Containers OK: %s", app_name)
                return
            logger.info("containers not ready: %s", report)
            time.sleep(interval)

        msg = f"container readiness timed out after {timeout}s: {app_name}\n{last_report}"
        raise DeployTimeoutError(msg)

    def _containers_ready(
        self,
        containers: list[object],
        expected_services: list[ExpectedService],
    ) -> tuple[bool, str]:
        observed: dict[str, list[str]] = {service.name: [] for service in expected_services}
        ready_counts = {service.name: 0 for service in expected_services}
        expected_by_name = {service.name: service for service in expected_services}

        for container in containers:
            observation = self._container_readiness_observation(container, expected_by_name)
            if observation is None:
                continue
            service_name, report, ready = observation
            observed[service_name].append(report)
            if ready:
                ready_counts[service_name] += 1

        missing = [
            f"{service.name} {ready_counts[service.name]}/{service.replicas}"
            for service in expected_services
            if ready_counts[service.name] < service.replicas
        ]
        if not missing:
            return True, "all expected containers are ready"

        details = []
        for service in expected_services:
            service_observed = observed[service.name]
            if service_observed:
                details.append(f"{service.name}: {'; '.join(service_observed)}")
            else:
                details.append(f"{service.name}: no containers observed")
        return False, f"missing ready replicas: {', '.join(missing)}; {' | '.join(details)}"

    def _container_readiness_observation(
        self,
        container: object,
        expected_by_name: dict[str, ExpectedService],
    ) -> tuple[str, str, bool] | None:
        if not isinstance(container, dict):
            return None
        name = container.get("name")
        if not isinstance(name, str) or not name:
            return None
        service_name = _service_name_from_container(name)
        expected = expected_by_name.get(service_name)
        if expected is None:
            return None

        container_id = container.get("containerId")
        if (
            not isinstance(container_id, str)
            or not container_id
            or container_id == "No container id"
        ):
            return service_name, "missing container id", False

        try:
            config = self._client.get_container_config(container_id)
        except DokployAPIError as exc:
            return service_name, f"{container_id}: inspect failed: {exc}", False

        state = self._container_state(config)
        health = self._container_health(config)
        image = self._container_image(config)
        report = (
            f"{container_id}: state={state or 'unknown'} "
            f"health={health or 'n/a'} image={image or 'unknown'}"
        )
        ready = self._container_matches(expected, state=state, health=health, image=image)
        return service_name, report, ready

    def _container_matches(
        self,
        expected: ExpectedService,
        *,
        state: str | None,
        health: str | None,
        image: str | None,
    ) -> bool:
        return (
            state == "running"
            and (health is None or health == "healthy")
            and image is not None
            and _image_matches(expected.image, image)
        )

    def _container_state(self, config: dict[str, object]) -> str | None:
        raw_state = config.get("State")
        if not isinstance(raw_state, dict):
            return None
        status = raw_state.get("Status")
        return status.lower() if isinstance(status, str) else None

    def _container_health(self, config: dict[str, object]) -> str | None:
        raw_state = config.get("State")
        if not isinstance(raw_state, dict):
            return None
        raw_health = raw_state.get("Health")
        if not isinstance(raw_health, dict):
            return None
        status = raw_health.get("Status")
        return status.lower() if isinstance(status, str) else None

    def _container_image(self, config: dict[str, object]) -> str | None:
        raw_config = config.get("Config")
        if isinstance(raw_config, dict):
            image = raw_config.get("Image")
            if isinstance(image, str) and image:
                return image
        image = config.get("Image")
        return image if isinstance(image, str) and image else None

    def deploy(
        self,
        stack_name: str | None,
        *,
        template_path: Path | None = None,
        env_template_path: Path | None = None,
        wait: int | None = None,
    ) -> None:
        """Upload the stack to Dokploy, trigger deploy, and optionally wait for completion."""
        config = self._config
        app_name = stack_name or config.app_name or config.app_id
        if app_name is None:
            msg = "missing app target: pass APP_NAME or set DOKPLOY_APP_NAME or DOKPLOY_APP_ID"
            raise ConfigurationError(msg)

        raw_template = self._templates.load(template_path)
        compose_file_content = self._templates.interpolate(raw_template)
        expected_services = (
            self._parse_expected_services(compose_file_content) if wait is not None else None
        )

        env_content: str | None = None
        if env_template_path is not None:
            if not env_template_path.is_file():
                msg = f"env file not found: {env_template_path}"
                raise ConfigurationError(msg)
            env_content = self._templates.interpolate(
                env_template_path.read_text(encoding="utf-8"),
            )

        existing_id = config.app_id
        environment_id = config.environment_id
        if existing_id is None:
            if environment_id is None:
                msg = (
                    "missing required environment variable: "
                    "DOKPLOY_ENV_ID or DOKPLOY_ENVIRONMENT_ID"
                )
                raise ConfigurationError(msg)
            env_data = self._client.get_environment(environment_id)
            existing_id = self._find_compose_id(env_data, app_name)

        if existing_id:
            compose_id = existing_id
            logger.info(
                "Using existing compose stack '%s' (%s)",
                app_name,
                compose_id,
            )
        else:
            logger.info("Compose stack '%s' not found; creating...", app_name)
            created = self._client.create_compose(
                name=app_name,
                environment_id=cast("str", environment_id),
            )
            try:
                compose_id = parse_compose_created(created)
            except TypeError as e:
                msg = f"compose.create did not return composeId: {e}"
                raise DokployAPIError(msg) from e

        self._client.update_compose(
            compose_id=compose_id,
            compose_file=compose_file_content,
            env_content=env_content,
        )
        previous_deployment_id = self._latest_deployment_id(compose_id)
        self._client.deploy_compose(compose_id)

        logger.info("compose.deploy accepted for %s (%s)", compose_id, app_name)
        self._wait_for_deploy(compose_id, app_name, previous_deployment_id)
        if wait is not None and expected_services is not None:
            container_app_name = self._compose_app_name(stack_name, compose_id)
            self._wait_for_containers(container_app_name, expected_services, wait)


def _service_name_from_container(container_name: str) -> str:
    parts = container_name.split("_", maxsplit=1)
    scoped = parts[1] if len(parts) == _SCOPED_CONTAINER_PARTS else container_name
    return scoped.split(".", maxsplit=1)[0]


def _image_matches(expected: str, observed: str) -> bool:
    return observed == expected or observed.startswith(f"{expected}@sha256:")


_SCOPED_CONTAINER_PARTS = 2
