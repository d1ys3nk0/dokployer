"""Stack deployment workflow using DokployClient and ComposeTemplate."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from dokployer.constants import (
    DEFAULT_DEPLOY_POLL_INTERVAL_SECONDS,
    DEFAULT_DEPLOY_WAIT_TIMEOUT_SECONDS,
    DOKPLOY_API_KEY,
    DOKPLOY_ENVIRONMENT_ID,
    DOKPLOY_URL,
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

    from dokployer.dokploy_client import DokployClient
    from dokployer.template_manager import ComposeTemplate


logger = logging.getLogger(__name__)


class StackDeployer:
    """Orchestrates compose template interpolation and deployment via DokployClient."""

    def __init__(
        self,
        client: DokployClient,
        template: ComposeTemplate,
    ) -> None:
        """Initialize StackDeployer with a client and template."""
        self._client = client
        self._templates = template

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

    def _wait_for_deploy(self, compose_id: str, stack_name: str) -> None:
        """Poll until deploy completes or times out."""
        timeout = int(os.environ.get(WAIT_TIMEOUT, str(DEFAULT_DEPLOY_WAIT_TIMEOUT_SECONDS)))
        interval = int(os.environ.get(WAIT_INTERVAL, str(DEFAULT_DEPLOY_POLL_INTERVAL_SECONDS)))
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            time.sleep(interval)
            status_str = self._client.get_compose_status(compose_id)
            logger.info("  status: %s", status_str)
            if status_str == ComposeStatus.DONE:
                logger.info("Deploy OK: %s", stack_name)
                return
            if status_str == ComposeStatus.ERROR:
                msg = f"deploy failed: {stack_name}"
                raise DeployFailedError(msg)

        msg = f"deploy timed out after {timeout}s: {stack_name}"
        raise DeployTimeoutError(msg)

    def deploy(
        self,
        stack_name: str,
        *,
        template_path: Path | None = None,
        env_template_path: Path | None = None,
        wait: bool = False,
    ) -> None:
        """Upload the stack to Dokploy, trigger deploy, and optionally wait for completion."""
        base_url = os.environ.get(DOKPLOY_URL, "")
        if not base_url:
            msg = "missing required environment variable: DOKPLOY_URL"
            raise ConfigurationError(msg)
        api_key = os.environ.get(DOKPLOY_API_KEY, "")
        if not api_key:
            msg = "missing required environment variable: DOKPLOY_API_KEY"
            raise ConfigurationError(msg)
        environment_id = os.environ.get(DOKPLOY_ENVIRONMENT_ID, "")
        if not environment_id:
            msg = "missing required environment variable: DOKPLOY_ENVIRONMENT_ID"
            raise ConfigurationError(msg)

        self._client.base_url = base_url.rstrip("/")
        self._client.api_key = api_key

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

        env_data = self._client.get_environment(environment_id)
        existing_id = self._find_compose_id(env_data, stack_name)

        if existing_id:
            compose_id = existing_id
            logger.info(
                "Using existing compose stack '%s' (%s)",
                stack_name,
                compose_id,
            )
        else:
            logger.info("Compose stack '%s' not found; creating...", stack_name)
            created = self._client.create_compose(
                name=stack_name,
                environment_id=environment_id,
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
        self._client.deploy_compose(compose_id)

        logger.info("compose.deploy accepted for %s (%s)", compose_id, stack_name)
        if wait:
            self._wait_for_deploy(compose_id, stack_name)
