"""Dokploy API client (pure HTTP transport layer)."""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException
from importlib.metadata import PackageNotFoundError, version

from dokployer.config import DokployConfig
from dokployer.constants import DEFAULT_HTTP_TIMEOUT_SECONDS
from dokployer.errors import DokployAPIError
from dokployer.models import (
    parse_compose_status,
)

logger = logging.getLogger(__name__)


class DokployClient:
    """HTTP client for Dokploy API."""

    def __init__(
        self,
        base_url: str | DokployConfig,
        api_key: str | None = None,
        timeout: int = DEFAULT_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize DokployClient with explicit connection config."""
        if isinstance(base_url, DokployConfig):
            self.base_url = base_url.base_url.rstrip("/")
            self.api_key = base_url.api_key
        else:
            if api_key is None:
                msg = "api_key is required when base_url is passed directly"
                raise TypeError(msg)
            self.base_url = base_url.rstrip("/")
            self.api_key = api_key
        self.timeout = timeout

    def _raise_if_api_error(self, label: str, text: str) -> None:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return

        if (
            isinstance(obj, dict)
            and obj.get("code") is not None
            and (obj.get("message") is not None or obj.get("error") is True)
        ):
            msg = f"{label}: Dokploy API error"
            raise DokployAPIError(
                msg,
                api_code=str(obj.get("code", "")),
                path=label,
            )

    def _user_agent(self) -> str:
        try:
            package_version = version("dokployer")
        except PackageNotFoundError:
            package_version = "0.0.0"
        return f"dokployer/{package_version}"

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, str] | None = None,
        *,
        retry_get: bool = False,
    ) -> object:
        url = f"{self.base_url}{path}"
        headers = {
            "accept": "application/json",
            "user-agent": self._user_agent(),
            "x-api-key": self.api_key,
        }
        data = None
        if body is not None:
            headers["content-type"] = "application/json"
            data = json.dumps(body).encode("utf-8")

        attempts = 3 if method == "GET" and retry_get else 1
        for attempt in range(1, attempts + 1):
            req = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if not isinstance(raw, bytes):
                        msg = f"{path}: API response body was not bytes"
                        raise DokployAPIError(
                            msg,
                            path=path,
                        )
                    text = raw.decode("utf-8")
                break
            except urllib.error.HTTPError as err:
                if attempt < attempts and err.code in {502, 503, 504}:
                    self._sleep_before_retry(attempt)
                    continue
                err_body = err.read().decode("utf-8", errors="replace")
                msg = f"HTTP {err.code} {path}: {err_body}"
                raise DokployAPIError(
                    msg,
                    status_code=err.code,
                    path=path,
                ) from err
            except (HTTPException, OSError, urllib.error.URLError) as err:
                if attempt < attempts:
                    self._sleep_before_retry(attempt)
                    continue
                msg = f"request failed {path}: {err}"
                raise DokployAPIError(
                    msg,
                    path=path,
                ) from err

        self._raise_if_api_error(path, text)
        try:
            obj: object = json.loads(text)
        except json.JSONDecodeError:
            return {}

        return obj

    def _sleep_before_retry(self, attempt: int) -> None:
        delay = min(0.25 * (2 ** (attempt - 1)) + random.uniform(0, 0.1), 2.0)  # noqa: S311
        time.sleep(delay)

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, str] | None = None,
        *,
        retry_get: bool = False,
    ) -> dict[str, object]:
        obj = self._request_json(method, path, body, retry_get=retry_get)
        return obj if isinstance(obj, dict) else {}

    def get_environment(self, environment_id: str) -> dict[str, object]:
        """Fetch environment data from Dokploy."""
        return self._request(
            "GET",
            f"/api/environment.one?environmentId={environment_id}",
        )

    def create_compose(
        self,
        name: str,
        environment_id: str,
        compose_type: str = "stack",
    ) -> dict[str, object]:
        """Create a new compose stack."""
        return self._request(
            "POST",
            "/api/compose.create",
            {
                "name": name,
                "environmentId": environment_id,
                "composeType": compose_type,
            },
        )

    def update_compose(
        self,
        compose_id: str,
        compose_file: str,
        compose_type: str = "stack",
        env_content: str | None = None,
    ) -> dict[str, object]:
        """Update an existing compose stack."""
        body: dict[str, str] = {
            "composeId": compose_id,
            "composeFile": compose_file,
            "composeType": compose_type,
            "sourceType": "raw",
        }
        if env_content is not None:
            body["env"] = env_content
        return self._request("POST", "/api/compose.update", body)

    def deploy_compose(self, compose_id: str) -> dict[str, object]:
        """Trigger deployment of a compose stack."""
        return self._request("POST", "/api/compose.deploy", {"composeId": compose_id})

    def get_compose_status(self, compose_id: str) -> str:
        """Poll compose status and return status string."""
        data = self._request("GET", f"/api/compose.one?composeId={compose_id}", retry_get=True)
        return parse_compose_status(data)

    def get_compose(self, compose_id: str) -> dict[str, object]:
        """Fetch compose app details."""
        return self._request("GET", f"/api/compose.one?composeId={compose_id}", retry_get=True)

    def get_stack_containers_by_app_name(self, app_name: str) -> list[object]:
        """Fetch stack containers for a compose app name."""
        query = urllib.parse.urlencode({"appName": app_name})
        data = self._request_json(
            "GET",
            f"/api/docker.getStackContainersByAppName?{query}",
            retry_get=True,
        )
        return data if isinstance(data, list) else []

    def get_deployments_by_compose(self, compose_id: str) -> list[object]:
        """Fetch deployments for a compose app."""
        input_json = json.dumps({"json": {"composeId": compose_id}}, separators=(",", ":"))
        query = urllib.parse.urlencode({"input": input_json})
        data = self._request(
            "GET",
            f"/api/trpc/deployment.allByCompose?{query}",
            retry_get=True,
        )
        result = data.get("result")
        if not isinstance(result, dict):
            return []
        result_data = result.get("data")
        if not isinstance(result_data, dict):
            return []
        json_data = result_data.get("json")
        return json_data if isinstance(json_data, list) else []

    def get_container_config(self, container_id: str) -> dict[str, object]:
        """Fetch Docker inspect data for a container through Dokploy."""
        input_json = json.dumps({"json": {"containerId": container_id}}, separators=(",", ":"))
        query = urllib.parse.urlencode({"input": input_json})
        data = self._request(
            "GET",
            f"/api/trpc/docker.getConfig?{query}",
            retry_get=True,
        )
        result = data.get("result")
        if not isinstance(result, dict):
            return {}
        result_data = result.get("data")
        if not isinstance(result_data, dict):
            return {}
        json_data = result_data.get("json")
        return json_data if isinstance(json_data, dict) else {}
