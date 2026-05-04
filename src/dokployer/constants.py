"""Constants and environment variable names for dokployer."""

from __future__ import annotations

from enum import StrEnum


class ComposeStatus(StrEnum):
    """Possible states for a compose deployment."""

    DONE = "done"
    ERROR = "error"
    RUNNING = "running"
    UNKNOWN = "unknown"


DOKPLOY_URL = "DOKPLOY_URL"
DOKPLOY_API_KEY = "DOKPLOY_API_KEY"
DOKPLOY_ENV_ID = "DOKPLOY_ENV_ID"
DOKPLOY_ENVIRONMENT_ID = "DOKPLOY_ENVIRONMENT_ID"
DOKPLOY_APP_ID = "DOKPLOY_APP_ID"
DOKPLOY_SERVICE_ID = "DOKPLOY_SERVICE_ID"
DOKPLOY_APP_NAME = "DOKPLOY_APP_NAME"
DOKPLOY_APP = "DOKPLOY_APP"

WAIT_TIMEOUT = "WAIT_TIMEOUT"
WAIT_INTERVAL = "WAIT_INTERVAL"

DEFAULT_HTTP_TIMEOUT_SECONDS = 120
DEFAULT_DEPLOY_WAIT_TIMEOUT_SECONDS = 300
DEFAULT_DEPLOY_POLL_INTERVAL_SECONDS = 5

COMPOSE_API_PATH = "/api/compose.one"
COMPOSE_CREATE_API_PATH = "/api/compose.create"
COMPOSE_UPDATE_API_PATH = "/api/compose.update"
COMPOSE_DEPLOY_API_PATH = "/api/compose.deploy"
ENVIRONMENT_API_PATH = "/api/environment.one"
