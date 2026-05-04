"""Stack deployment workflow using DokployClient and ComposeTemplate."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, cast

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

    def deploy(
        self,
        stack_name: str | None,
        *,
        template_path: Path | None = None,
        env_template_path: Path | None = None,
        wait: bool = False,
    ) -> None:
        """Upload the stack to Dokploy, trigger deploy, and optionally wait for completion."""
        config = self._config
        app_name = stack_name or config.app_name or config.app_id
        if app_name is None:
            msg = "missing app target: pass APP_NAME or set DOKPLOY_APP_NAME or DOKPLOY_APP_ID"
            raise ConfigurationError(msg)

        raw_template = self._templates.load(template_path)
        compose_file_content = self._templates.interpolate(raw_template)

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
            except ValueError as e:
                msg = f"compose.create did not return composeId: {e}"
                raise DokployAPIError(msg) from e

        self._client.update_compose(
            compose_id=compose_id,
            compose_file=compose_file_content,
            env_content=env_content,
        )
        previous_deployment_id = self._latest_deployment_id(compose_id) if wait else None
        self._client.deploy_compose(compose_id)

        logger.info("compose.deploy accepted for %s (%s)", compose_id, app_name)
        if wait:
            self._wait_for_deploy(compose_id, app_name, previous_deployment_id)
