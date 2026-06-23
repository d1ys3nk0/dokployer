"""Stack deployment workflow using DokployClient and ComposeTemplate."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

import yaml

from dokployer.constants import (
    DEFAULT_DEPLOY_POLL_INTERVAL_SECONDS,
    DEFAULT_DEPLOY_POLL_TIMEOUT_SECONDS,
    DEFAULT_STACK_POLL_INTERVAL_SECONDS,
    DEFAULT_STACK_POLL_TIMEOUT_SECONDS,
    DEPLOY_POLL_INTERVAL,
    DEPLOY_POLL_TIMEOUT,
    STACK_POLL_INTERVAL,
    STACK_POLL_TIMEOUT,
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
StackWait = int | Literal[True] | None
ServiceReadinessMode = Literal["running", "completed"]


@dataclass(frozen=True, slots=True)
class ExpectedService:
    """Expected service state derived from the interpolated stack."""

    name: str
    image: str
    replicas: int
    mode: ServiceReadinessMode = "running"


@dataclass(frozen=True, slots=True)
class ContainerDiagnostic:
    """Container state derived from Dokploy list and Docker inspect data."""

    service_name: str
    name: str
    container_id: str | None
    state: str | None
    health: str | None
    image: str | None
    created_at: str | None
    started_at: str | None
    stopped_at: str | None
    healthcheck: str | None
    health_logs: list[str]
    inspect_error: str | None
    ready: bool
    exit_code: int | None = None


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

    def _stack_wait_timeout(self, wait: StackWait) -> int | None:
        if wait is None:
            return None
        if wait is True:
            return self._wait_value(STACK_POLL_TIMEOUT, DEFAULT_STACK_POLL_TIMEOUT_SECONDS)
        if wait <= 0:
            msg = f"invalid wait timeout: expected a positive integer, got {wait!r}"
            raise ConfigurationError(msg)
        return wait

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
                expected.append(
                    ExpectedService(
                        name=raw_name,
                        image=image,
                        replicas=replicas,
                        mode=self._service_readiness_mode(deploy),
                    ),
                )

        return expected

    def _service_readiness_mode(self, deploy: dict[object, object]) -> ServiceReadinessMode:
        restart_policy = deploy.get("restart_policy")
        if not isinstance(restart_policy, dict):
            return "running"
        condition = restart_policy.get("condition")
        if isinstance(condition, str) and condition.strip().lower() == "none":
            return "completed"
        return "running"

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
        timeout = self._wait_value(DEPLOY_POLL_TIMEOUT, DEFAULT_DEPLOY_POLL_TIMEOUT_SECONDS)
        interval = self._wait_value(DEPLOY_POLL_INTERVAL, DEFAULT_DEPLOY_POLL_INTERVAL_SECONDS)
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

        compose = self._client.get_compose(compose_id)
        compose_app_name = compose.get("appName")
        if isinstance(compose_app_name, str) and compose_app_name:
            return compose_app_name
        name = compose.get("name")
        if isinstance(name, str) and name:
            return name
        if app_name is not None:
            return app_name

        msg = "missing app name: DOKPLOY_APP_ID target did not expose a compose name"
        raise ConfigurationError(msg)

    def _wait_for_containers(
        self,
        app_name: str,
        expected_services: list[ExpectedService],
        timeout: int,
    ) -> None:
        deadline = time.monotonic() + timeout
        interval = self._wait_value(STACK_POLL_INTERVAL, DEFAULT_STACK_POLL_INTERVAL_SECONDS)
        last_report = "container status: not checked"
        last_summary = self._container_summary([])

        while time.monotonic() < deadline:
            containers = self._client.get_stack_containers_by_app_name(app_name)
            ready, report, summary = self._containers_ready(containers, expected_services)
            last_report = report
            last_summary = summary
            if ready:
                logger.info("Containers OK: %s", app_name)
                logger.info("%s", summary)
                return
            logger.info("containers not ready: %s", report)
            time.sleep(interval)

        msg = (
            f"container readiness timed out after {timeout}s: {app_name}\n"
            f"{last_report}\n{last_summary}"
        )
        raise DeployTimeoutError(msg)

    def _containers_ready(
        self,
        containers: list[object],
        expected_services: list[ExpectedService],
    ) -> tuple[bool, str, str]:
        observed: dict[str, list[str]] = {service.name: [] for service in expected_services}
        ready_counts = {service.name: 0 for service in expected_services}
        expected_by_name = {service.name: service for service in expected_services}
        diagnostics = self._container_diagnostics(containers, expected_by_name)

        for diagnostic in diagnostics:
            observed[diagnostic.service_name].append(self._container_readiness_report(diagnostic))
            if diagnostic.ready:
                ready_counts[diagnostic.service_name] += 1

        missing = [
            f"{service.name} {ready_counts[service.name]}/{service.replicas}"
            for service in expected_services
            if ready_counts[service.name] < service.replicas
        ]
        summary = self._container_summary(diagnostics)
        if not missing:
            return True, "all expected containers are ready", summary

        details = []
        for service in expected_services:
            service_observed = observed[service.name]
            if service_observed:
                details.append(f"{service.name}: {'; '.join(service_observed)}")
            else:
                details.append(f"{service.name}: no containers observed")
        report = f"missing ready replicas: {', '.join(missing)}; {' | '.join(details)}"
        return False, report, summary

    def _container_diagnostics(
        self,
        containers: list[object],
        expected_by_name: dict[str, ExpectedService],
    ) -> list[ContainerDiagnostic]:
        diagnostics = []
        for container in containers:
            diagnostic = self._container_diagnostic(container, expected_by_name)
            if diagnostic is not None:
                diagnostics.append(diagnostic)
        return diagnostics

    def _container_diagnostic(
        self,
        container: object,
        expected_by_name: dict[str, ExpectedService],
    ) -> ContainerDiagnostic | None:
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
            return ContainerDiagnostic(
                service_name=service_name,
                name=name,
                container_id=None,
                state=self._container_state({}, container),
                health=self._container_health({}, container),
                image=self._container_image({}, container),
                created_at=self._container_created_at({}, container),
                started_at=self._container_started_at({}, container),
                stopped_at=self._container_stopped_at({}, container),
                healthcheck=None,
                health_logs=[],
                inspect_error="missing container id",
                ready=False,
                exit_code=self._container_exit_code({}, container),
            )

        try:
            config = self._client.get_container_config(container_id)
        except DokployAPIError as exc:
            return ContainerDiagnostic(
                service_name=service_name,
                name=name,
                container_id=container_id,
                state=self._container_state({}, container),
                health=self._container_health({}, container),
                image=self._container_image({}, container),
                created_at=self._container_created_at({}, container),
                started_at=self._container_started_at({}, container),
                stopped_at=self._container_stopped_at({}, container),
                healthcheck=None,
                health_logs=[],
                inspect_error=f"inspect failed: {exc}",
                ready=False,
                exit_code=self._container_exit_code({}, container),
            )

        state = self._container_state(config, container)
        health = self._container_health(config, container)
        image = self._container_image(config, container)
        exit_code = self._container_exit_code(config, container)
        ready = self._container_matches(
            expected,
            state=state,
            health=health,
            image=image,
            exit_code=exit_code,
        )
        return ContainerDiagnostic(
            service_name=service_name,
            name=name,
            container_id=container_id,
            state=state,
            health=health,
            image=image,
            created_at=self._container_created_at(config, container),
            started_at=self._container_started_at(config, container),
            stopped_at=self._container_stopped_at(config, container),
            healthcheck=self._container_healthcheck(config),
            health_logs=self._container_health_logs(config),
            inspect_error=None,
            ready=ready,
            exit_code=exit_code,
        )

    def _container_readiness_report(self, diagnostic: ContainerDiagnostic) -> str:
        container_id = diagnostic.container_id or "missing container id"
        parts = [
            f"{container_id}: state={diagnostic.state or 'unknown'}",
            f"health={diagnostic.health or 'n/a'}",
            f"image={diagnostic.image or 'unknown'}",
        ]
        if diagnostic.exit_code is not None:
            parts.append(f"exit={diagnostic.exit_code}")
        if diagnostic.inspect_error is not None:
            parts.append(diagnostic.inspect_error)
        return " ".join(parts)

    def _container_summary(self, diagnostics: list[ContainerDiagnostic]) -> str:
        lines = ["Container summary:"]
        if not diagnostics:
            lines.append("  no matching containers observed")
            return "\n".join(lines)

        for diagnostic in sorted(diagnostics, key=_container_sort_key):
            lines.append(f"  service: {diagnostic.service_name}")
            lines.append(f"    container id: {diagnostic.container_id or 'missing'}")
            lines.append(f"    name: {diagnostic.name}")
            lines.append(f"    image: {diagnostic.image or 'unknown'}")
            lines.append(f"    state: {diagnostic.state or 'unknown'}")
            lines.extend(_exit_code_summary_lines(diagnostic.exit_code))
            if diagnostic.started_at is not None:
                lines.append(f"    started: {diagnostic.started_at}")
            if diagnostic.stopped_at is not None:
                lines.append(f"    stopped: {diagnostic.stopped_at}")
            if diagnostic.created_at is not None:
                lines.append(f"    created: {diagnostic.created_at}")
            if diagnostic.health is not None:
                lines.append(f"    health: {diagnostic.health}")
            if diagnostic.healthcheck is not None:
                lines.append(f"    healthcheck: {diagnostic.healthcheck}")
            if diagnostic.health_logs:
                lines.append("    healthcheck logs:")
                lines.extend(f"      {entry}" for entry in diagnostic.health_logs)
            if diagnostic.inspect_error is not None:
                lines.append(f"    inspect: {diagnostic.inspect_error}")
        return "\n".join(lines)

    def _container_matches(
        self,
        expected: ExpectedService,
        *,
        state: str | None,
        health: str | None,
        image: str | None,
        exit_code: int | None,
    ) -> bool:
        if image is None or not _image_matches(expected.image, image):
            return False
        if expected.mode == "completed":
            return state == "complete" or (state == "exited" and exit_code == 0)
        return state == "running" and (health is None or health == "healthy")

    def _container_state(
        self,
        config: dict[str, object],
        container: dict[str, object] | None = None,
    ) -> str | None:
        raw_state = config.get("State")
        if isinstance(raw_state, dict):
            status = raw_state.get("Status")
            if isinstance(status, str):
                return _normalize_container_state(status)
        if isinstance(raw_state, str):
            return _normalize_container_state(raw_state)
        raw_status = config.get("Status")
        if isinstance(raw_status, dict):
            state = raw_status.get("State")
            if isinstance(state, str):
                return _normalize_container_state(state)
        for source in (config, container):
            if source is None:
                continue
            for key in ("state", "status", "currentState"):
                value = source.get(key)
                if isinstance(value, str) and value:
                    return _normalize_container_state(value)
        return None

    def _container_health(
        self,
        config: dict[str, object],
        container: dict[str, object] | None = None,
    ) -> str | None:
        raw_state = config.get("State")
        if isinstance(raw_state, dict):
            raw_health = raw_state.get("Health")
            if isinstance(raw_health, dict):
                status = raw_health.get("Status")
                if isinstance(status, str):
                    return status.lower()
        for source in (config, container):
            if source is None:
                continue
            value = source.get("health")
            if isinstance(value, str) and value:
                return value.lower()
        return None

    def _container_exit_code(
        self,
        config: dict[str, object],
        container: dict[str, object] | None = None,
    ) -> int | None:
        raw_status = config.get("Status")
        sources = (
            (config.get("State"), ("ExitCode",)),
            (raw_status, ("ExitCode",)),
            (_container_status(raw_status), ("ExitCode",)),
            (config, ("ExitCode", "exitCode")),
            (container, ("ExitCode", "exitCode")),
        )
        for source, keys in sources:
            exit_code = _exit_code_from(source, keys)
            if exit_code is not None:
                return exit_code
        return None

    def _container_created_at(
        self,
        config: dict[str, object],
        container: dict[str, object] | None = None,
    ) -> str | None:
        for source in (config, container):
            if source is None:
                continue
            value = source.get("Created") or source.get("created") or source.get("createdAt")
            if isinstance(value, str) and _is_meaningful_timestamp(value):
                return value
        return None

    def _container_started_at(
        self,
        config: dict[str, object],
        container: dict[str, object] | None = None,
    ) -> str | None:
        raw_state = config.get("State")
        if isinstance(raw_state, dict):
            started_at = raw_state.get("StartedAt")
            if isinstance(started_at, str) and _is_meaningful_timestamp(started_at):
                return started_at
        raw_status = config.get("Status")
        if isinstance(raw_status, dict):
            for key in ("StartedAt", "StartTime", "Started"):
                timestamp = raw_status.get(key)
                if isinstance(timestamp, str) and _is_meaningful_timestamp(timestamp):
                    return timestamp
        for source in (config, container):
            if source is None:
                continue
            value = (
                source.get("startedAt")
                or source.get("StartedAt")
                or source.get("startTime")
                or source.get("StartTime")
            )
            if isinstance(value, str) and _is_meaningful_timestamp(value):
                return value
        return None

    def _container_stopped_at(
        self,
        config: dict[str, object],
        container: dict[str, object] | None = None,
    ) -> str | None:
        raw_state = config.get("State")
        if isinstance(raw_state, dict):
            finished_at = raw_state.get("FinishedAt")
            if isinstance(finished_at, str) and _is_meaningful_timestamp(finished_at):
                return finished_at
        raw_status = config.get("Status")
        if isinstance(raw_status, dict):
            timestamp = raw_status.get("Timestamp")
            state = self._container_state(config, container)
            if (
                state not in {None, "running"}
                and isinstance(timestamp, str)
                and _is_meaningful_timestamp(timestamp)
            ):
                return timestamp
        for source in (config, container):
            if source is None:
                continue
            value = source.get("finishedAt") or source.get("stoppedAt")
            if isinstance(value, str) and _is_meaningful_timestamp(value):
                return value
        return None

    def _container_healthcheck(self, config: dict[str, object]) -> str | None:
        raw_config = config.get("Config")
        if not isinstance(raw_config, dict):
            return None
        raw_healthcheck = raw_config.get("Healthcheck")
        if not isinstance(raw_healthcheck, dict):
            return None

        parts = []
        test = raw_healthcheck.get("Test")
        if isinstance(test, list):
            test_value = " ".join(str(part) for part in test)
            if test_value:
                parts.append(f"test={test_value}")
        elif isinstance(test, str) and test:
            parts.append(f"test={test}")

        field_names = {
            "Interval": "interval",
            "Timeout": "timeout",
            "Retries": "retries",
            "StartPeriod": "start_period",
        }
        for raw_name, display_name in field_names.items():
            value = raw_healthcheck.get(raw_name)
            if isinstance(value, str | int | float) and not isinstance(value, bool):
                parts.append(f"{display_name}={value}")

        return " ".join(parts) if parts else None

    def _container_health_logs(self, config: dict[str, object]) -> list[str]:
        raw_state = config.get("State")
        if not isinstance(raw_state, dict):
            return []
        raw_health = raw_state.get("Health")
        if not isinstance(raw_health, dict):
            return []
        raw_logs = raw_health.get("Log")
        if not isinstance(raw_logs, list):
            return []

        logs = []
        for raw_entry in raw_logs[-3:]:
            if not isinstance(raw_entry, dict):
                continue
            entry = self._container_health_log_entry(raw_entry)
            if entry is not None:
                logs.append(entry)
        return logs

    def _container_health_log_entry(self, raw_entry: dict[str, object]) -> str | None:
        output = raw_entry.get("Output")
        exit_code = raw_entry.get("ExitCode")
        start = raw_entry.get("Start")
        end = raw_entry.get("End")
        parts = []
        if isinstance(exit_code, int):
            parts.append(f"exit={exit_code}")
        if isinstance(start, str) and start:
            parts.append(f"start={start}")
        if isinstance(end, str) and end:
            parts.append(f"end={end}")
        if isinstance(output, str) and output:
            output_text = " ".join(output.split())
            parts.append(f"output={output_text}")
        return " ".join(parts) if parts else None

    def _container_image(
        self,
        config: dict[str, object],
        container: dict[str, object] | None = None,
    ) -> str | None:
        raw_config = config.get("Config")
        if isinstance(raw_config, dict):
            image = raw_config.get("Image")
            if isinstance(image, str) and image:
                return image
        raw_spec = config.get("Spec")
        if isinstance(raw_spec, dict):
            raw_container_spec = raw_spec.get("ContainerSpec")
            if isinstance(raw_container_spec, dict):
                image = raw_container_spec.get("Image")
                if isinstance(image, str) and image:
                    return image
        for source in (config, container):
            if source is None:
                continue
            for key in ("Image", "image", "imageName"):
                image = source.get(key)
                if isinstance(image, str) and image:
                    return image
        return None

    def deploy(
        self,
        stack_name: str | None,
        *,
        template_path: Path | None = None,
        env_template_path: Path | None = None,
        wait: StackWait = None,
    ) -> None:
        """Upload the stack to Dokploy, trigger deploy, and optionally wait for completion."""
        config = self._config
        app_name = stack_name or config.app_name or config.app_id
        if app_name is None:
            msg = "missing app target: pass APP_NAME or set DOKPLOY_APP_NAME or DOKPLOY_APP_ID"
            raise ConfigurationError(msg)

        raw_template = self._templates.load(template_path)
        compose_file_content = self._templates.interpolate(raw_template)
        wait_timeout = self._stack_wait_timeout(wait)
        expected_services = (
            self._parse_expected_services(compose_file_content)
            if wait_timeout is not None
            else None
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
                msg = "missing required environment variable: DOKPLOY_ENV_ID"
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
        self._wait_for_deploy_with_container_summary(
            compose_id=compose_id,
            app_name=app_name,
            stack_name=stack_name,
            previous_deployment_id=previous_deployment_id,
            expected_services=expected_services,
        )
        if wait_timeout is not None and expected_services is not None:
            container_app_name = self._compose_app_name(stack_name, compose_id)
            self._wait_for_containers(container_app_name, expected_services, wait_timeout)

    def _wait_for_deploy_with_container_summary(
        self,
        *,
        compose_id: str,
        app_name: str,
        stack_name: str | None,
        previous_deployment_id: str | None,
        expected_services: list[ExpectedService] | None,
    ) -> None:
        try:
            self._wait_for_deploy(compose_id, app_name, previous_deployment_id)
        except DeployFailedError as exc:
            if expected_services is None:
                raise
            raise DeployFailedError(
                self._message_with_best_effort_container_summary(
                    str(exc),
                    stack_name,
                    compose_id,
                    expected_services,
                ),
            ) from exc

    def _message_with_best_effort_container_summary(
        self,
        message: str,
        stack_name: str | None,
        compose_id: str,
        expected_services: list[ExpectedService],
    ) -> str:
        try:
            app_name = self._compose_app_name(stack_name, compose_id)
            containers = self._client.get_stack_containers_by_app_name(app_name)
            _, _, summary = self._containers_ready(containers, expected_services)
        except (ConfigurationError, DokployAPIError) as exc:
            summary = f"Container summary:\n  unavailable: {exc}"
        return f"{message}\n{summary}"


def _service_name_from_container(container_name: str) -> str:
    parts = container_name.split("_", maxsplit=1)
    scoped = parts[1] if len(parts) == _SCOPED_CONTAINER_PARTS else container_name
    return scoped.split(".", maxsplit=1)[0]


def _image_matches(expected: str, observed: str) -> bool:
    return observed == expected or observed.startswith(f"{expected}@sha256:")


def _normalize_container_state(value: str) -> str:
    return value.strip().split(maxsplit=1)[0].lower()


def _is_meaningful_timestamp(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped) and not stripped.startswith("0001-01-01T00:00:00")


def _container_status(raw_status: object) -> object:
    if not isinstance(raw_status, dict):
        return None
    return raw_status.get("ContainerStatus")


def _exit_code_from(source: object, keys: tuple[str, ...]) -> int | None:
    if not isinstance(source, dict):
        return None
    for key in keys:
        exit_code = source.get(key)
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            return exit_code
    return None


def _exit_code_summary_lines(exit_code: int | None) -> list[str]:
    if exit_code is None:
        return []
    return [f"    exit code: {exit_code}"]


def _container_sort_key(diagnostic: ContainerDiagnostic) -> tuple[bool, str, str, str]:
    timestamp = diagnostic.started_at or diagnostic.created_at or diagnostic.stopped_at or ""
    return not timestamp, timestamp, diagnostic.name, diagnostic.container_id or ""


_SCOPED_CONTAINER_PARTS = 2
