"""Dokploy API client and stack deployment workflow."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.client import HTTPException
from typing import TYPE_CHECKING, NoReturn, cast

if TYPE_CHECKING:
    from pathlib import Path

    from dokployer.template_manager import TemplateManager


class DokployClient:
    """HTTP client for Dokploy and interpolated Swarm stack deploy."""

    def __init__(self, template_manager: TemplateManager) -> None:
        """Create a client that uses ``template_manager`` for compose/env templates."""
        self._templates = template_manager

    def _die(self, msg: str) -> NoReturn:
        sys.stderr.write(f"dokployer: {msg}\n")
        raise SystemExit(1)

    def _require(self, name: str) -> str:
        value = os.environ.get(name)
        if value is None:
            self._die(f"missing required environment variable: {name}")
        return value

    def _check_api_error(self, label: str, text: str) -> None:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return

        if isinstance(obj, dict) and obj.get("code") is not None:
            sys.stderr.write(f"{obj['code']}: {obj.get('message', '')}\n")
            self._die(f"{label}: Dokploy API error")

    def _api(
        self,
        method: str,
        path: str,
        body: dict[str, str] | None = None,
    ) -> dict[str, object]:
        base = os.environ["DOKPLOY_URL"].rstrip("/")
        url = f"{base}{path}"
        headers = {
            "accept": "application/json",
            "x-api-key": os.environ["DOKPLOY_API_KEY"],
        }
        data = None
        if body is not None:
            headers["content-type"] = "application/json"
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                if not isinstance(raw, bytes):
                    self._die(f"{path}: API response body was not bytes")
                text = raw.decode("utf-8")
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            self._die(f"HTTP {err.code} {path}: {err_body}")
        except (HTTPException, OSError, urllib.error.URLError) as err:
            self._die(f"request failed {path}: {err}")

        self._check_api_error(path, text)
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return {}

        return obj if isinstance(obj, dict) else {}

    @staticmethod
    def _compose_id_for_stack(data: dict[str, object], stack_name: str) -> str:
        compose_items = data.get("compose")
        if not isinstance(compose_items, list):
            return ""

        for item in compose_items:
            if isinstance(item, dict):
                compose_entry = cast("dict[str, object]", item)
                if compose_entry.get("name") != stack_name:
                    continue

                compose_id = compose_entry.get("composeId")
                if compose_id:
                    return str(compose_id)
        return ""

    def _wait_for_deploy(self, compose_id: str, stack_name: str) -> None:
        timeout = int(os.environ.get("WAIT_TIMEOUT", "300"))
        interval = int(os.environ.get("WAIT_INTERVAL", "5"))
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            time.sleep(interval)
            data = self._api("GET", f"/api/compose.one?composeId={compose_id}")
            status = str(data.get("composeStatus", "unknown"))
            sys.stdout.write(f"  status: {status}\n")
            if status == "done":
                sys.stdout.write(f"Deploy OK: {stack_name}\n")
                return
            if status == "error":
                self._die(f"deploy failed: {stack_name}")

        self._die(f"deploy timed out after {timeout}s: {stack_name}")

    def deploy_stack(
        self,
        stack_name: str,
        compose_template: Path | None = None,
        *,
        env_file: Path | None = None,
        wait: bool = False,
    ) -> None:
        """Upload the stack to Dokploy, trigger deploy, and optionally wait for completion."""
        base_url = self._require("DOKPLOY_URL").rstrip("/")
        os.environ["DOKPLOY_URL"] = base_url

        self._require("DOKPLOY_API_KEY")
        environment_id = self._require("DOKPLOY_ENVIRONMENT_ID")

        raw_template = self._templates.read_compose_template(compose_template)
        compose_file_content = self._templates.interpolate(raw_template)

        env_content: str | None = None
        if env_file is not None:
            if not env_file.is_file():
                self._die(f"env file not found: {env_file}")
            env_content = self._templates.interpolate(env_file.read_text(encoding="utf-8"))

        env_data = self._api("GET", f"/api/environment.one?environmentId={environment_id}")
        existing_id = self._compose_id_for_stack(env_data, stack_name)

        if existing_id:
            compose_id = existing_id
            sys.stdout.write(
                f"Using existing compose stack '{stack_name}' ({compose_id})\n",
            )
        else:
            sys.stdout.write(f"Compose stack '{stack_name}' not found; creating...\n")
            created = self._api(
                "POST",
                "/api/compose.create",
                {
                    "name": stack_name,
                    "environmentId": environment_id,
                    "composeType": "stack",
                },
            )
            compose_id = str(created.get("composeId") or "")
            if not compose_id:
                self._die("compose.create did not return composeId")

        update_body = {
            "composeId": compose_id,
            "composeFile": compose_file_content,
            "composeType": "stack",
            "sourceType": "raw",
        }
        if env_content is not None:
            update_body["env"] = env_content

        self._api("POST", "/api/compose.update", update_body)
        self._api("POST", "/api/compose.deploy", {"composeId": compose_id})

        sys.stdout.write(f"compose.deploy accepted for {compose_id} ({stack_name})\n")
        if wait:
            self._wait_for_deploy(compose_id, stack_name)
