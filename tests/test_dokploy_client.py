"""Tests for DokployClient."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import pytest

import dokployer.dokploy_client as dpc
import dokployer.template_manager as tm_mod
from dokployer.dokploy_client import DokployClient
from dokployer.template_manager import TemplateManager


class _FakeStdin(io.StringIO):
    def __init__(self, value: str, *, is_tty: bool) -> None:
        super().__init__(value)
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_compose_id_for_stack_found() -> None:
    payload: dict[str, object] = {
        "compose": [{"name": "my-stack", "composeId": "abc123"}],
    }
    assert DokployClient._compose_id_for_stack(payload, "my-stack") == "abc123"


def test_compose_id_for_stack_not_found() -> None:
    payload: dict[str, object] = {"compose": [{"name": "other", "composeId": "xyz"}]}
    assert DokployClient._compose_id_for_stack(payload, "my-stack") == ""


def test_compose_id_for_stack_empty_list() -> None:
    assert DokployClient._compose_id_for_stack({"compose": []}, "x") == ""


def test_check_api_error_silent_on_success() -> None:
    DokployClient(TemplateManager())._check_api_error("label", '{"result": "ok"}')


def test_check_api_error_silent_on_non_json() -> None:
    DokployClient(TemplateManager())._check_api_error("label", "not json at all")


def test_check_api_error_exits_on_error_code() -> None:
    with pytest.raises(SystemExit):
        DokployClient(TemplateManager())._check_api_error(
            "label",
            '{"code": "SOME_ERROR", "message": "boom"}',
        )


def test_deploy_stack_happy_path_existing_stack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compose_tmpl = tmp_path / "my-stack.stack.yml"
    compose_tmpl.write_text(
        "version: '3'\nservices:\n  app:\n    image: $${DEPLOY_IMAGE}\n",
        encoding="utf-8",
    )

    compose_id = "cmp-001"
    responses = iter(
        [
            {"compose": [{"name": "my-stack", "composeId": compose_id}]},
            {"composeId": compose_id},
            {"status": "queued"},
        ],
    )
    monkeypatch.setattr(
        DokployClient,
        "_api",
        lambda _c, _m, _p, _body=None: next(responses),
    )

    monkeypatch.setenv("DOKPLOY_URL", "http://dokploy.test")
    monkeypatch.setenv("DOKPLOY_API_KEY", "test-key")
    monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
    monkeypatch.setenv("DEPLOY_IMAGE", "myimage:latest")

    DokployClient(TemplateManager()).deploy_stack("my-stack", compose_tmpl)


def test_deploy_stack_with_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compose_tmpl = tmp_path / "my-stack.stack.yml"
    compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

    env_tmpl = tmp_path / "prd.env"
    env_tmpl.write_text(
        "LOG_LEVEL=${{environment.LOG_LEVEL}}\nDEPLOY_IMAGE=$${DEPLOY_IMAGE}\n",
        encoding="utf-8",
    )

    compose_id = "cmp-001"
    update_payloads: list[dict[str, Any]] = []

    def fake_api(
        _self: DokployClient,
        method: str,
        path: str,
        body: dict[str, str] | None = None,
    ) -> dict[str, object]:
        if (method, path) == ("GET", "/api/environment.one?environmentId=env-001"):
            return {"compose": [{"name": "my-stack", "composeId": compose_id}]}
        if (method, path) == ("POST", "/api/compose.update"):
            if body is None:
                msg = "compose.update must receive a body"
                raise AssertionError(msg)
            update_payloads.append(body)
            return {"composeId": compose_id}
        if (method, path) == ("POST", "/api/compose.deploy"):
            return {"status": "queued"}
        msg = f"unexpected API call: {method} {path}"
        raise AssertionError(msg)

    monkeypatch.setattr(DokployClient, "_api", fake_api)
    monkeypatch.setenv("DOKPLOY_URL", "http://dokploy.test")
    monkeypatch.setenv("DOKPLOY_API_KEY", "test-key")
    monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
    monkeypatch.setenv("DEPLOY_IMAGE", "myimage:latest")

    DokployClient(TemplateManager()).deploy_stack("my-stack", compose_tmpl, env_file=env_tmpl)

    assert update_payloads[0]["env"] == (
        "LOG_LEVEL=${{environment.LOG_LEVEL}}\nDEPLOY_IMAGE=myimage:latest\n"
    )


def test_deploy_stack_happy_path_creates_new_stack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compose_tmpl = tmp_path / "new-stack.stack.yml"
    compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

    compose_id = "cmp-new"
    responses = iter(
        [
            {"compose": []},
            {"composeId": compose_id},
            {"composeId": compose_id},
            {"status": "queued"},
        ],
    )
    monkeypatch.setattr(
        DokployClient,
        "_api",
        lambda _c, _m, _p, _body=None: next(responses),
    )

    monkeypatch.setenv("DOKPLOY_URL", "http://dokploy.test")
    monkeypatch.setenv("DOKPLOY_API_KEY", "test-key")
    monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")

    DokployClient(TemplateManager()).deploy_stack("new-stack", compose_tmpl)


def test_deploy_stack_reads_compose_template_from_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_id = "cmp-001"
    update_payloads: list[dict[str, str]] = []

    def fake_api(
        _self: DokployClient,
        method: str,
        path: str,
        body: dict[str, str] | None = None,
    ) -> dict[str, object]:
        if (method, path) == ("GET", "/api/environment.one?environmentId=env-001"):
            return {"compose": [{"name": "my-stack", "composeId": compose_id}]}
        if (method, path) == ("POST", "/api/compose.update"):
            if body is None:
                msg = "compose.update must receive a body"
                raise AssertionError(msg)
            update_payloads.append(body)
            return {"composeId": compose_id}
        if (method, path) == ("POST", "/api/compose.deploy"):
            return {"status": "queued"}
        msg = f"unexpected API call: {method} {path}"
        raise AssertionError(msg)

    monkeypatch.setattr(DokployClient, "_api", fake_api)
    monkeypatch.setattr(
        tm_mod.sys,
        "stdin",
        _FakeStdin(
            "version: '3'\nservices:\n  app:\n    image: $${DEPLOY_IMAGE}\n",
            is_tty=False,
        ),
    )

    monkeypatch.setenv("DOKPLOY_URL", "http://dokploy.test")
    monkeypatch.setenv("DOKPLOY_API_KEY", "test-key")
    monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
    monkeypatch.setenv("DEPLOY_IMAGE", "myimage:latest")

    DokployClient(TemplateManager()).deploy_stack("my-stack")

    assert update_payloads[0]["composeFile"] == (
        "version: '3'\nservices:\n  app:\n    image: myimage:latest\n"
    )
    assert "env" not in update_payloads[0]


def test_deploy_stack_wait_polls_until_done(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compose_tmpl = tmp_path / "my-stack.stack.yml"
    compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

    compose_id = "cmp-001"
    responses = iter(
        [
            {"compose": [{"name": "my-stack", "composeId": compose_id}]},
            {"composeId": compose_id},
            {"status": "queued"},
            {"composeStatus": "running"},
            {"composeStatus": "done"},
        ],
    )
    monkeypatch.setattr(
        DokployClient,
        "_api",
        lambda _c, _m, _p, _body=None: next(responses),
    )

    sleep_calls: list[int] = []
    monotonic_values = iter([0, 0, 5, 10])

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(dpc.time, "sleep", fake_sleep)
    monkeypatch.setattr(dpc.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setenv("DOKPLOY_URL", "http://dokploy.test")
    monkeypatch.setenv("DOKPLOY_API_KEY", "test-key")
    monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")

    DokployClient(TemplateManager()).deploy_stack("my-stack", compose_tmpl, wait=True)

    assert sleep_calls == [5, 5]


def test_deploy_stack_wait_times_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compose_tmpl = tmp_path / "my-stack.stack.yml"
    compose_tmpl.write_text("version: '3'\n", encoding="utf-8")

    compose_id = "cmp-001"

    def fake_api(
        _self: DokployClient,
        method: str,
        path: str,
        body: dict[str, str] | None = None,
    ) -> dict[str, object]:
        response_map: dict[tuple[str, str], dict[str, object]] = {
            ("GET", "/api/environment.one?environmentId=env-001"): {
                "compose": [{"name": "my-stack", "composeId": compose_id}],
            },
            ("GET", f"/api/compose.one?composeId={compose_id}"): {
                "composeStatus": "running",
            },
            ("POST", "/api/compose.update"): {"composeId": compose_id},
            ("POST", "/api/compose.deploy"): {"status": "queued"},
        }
        _ = body
        return response_map[(method, path)]

    monkeypatch.setattr(DokployClient, "_api", fake_api)

    sleep_calls: list[int] = []
    monotonic_values = iter([0, 0, 5, 10])

    def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(dpc.time, "sleep", fake_sleep)
    monkeypatch.setattr(dpc.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setenv("DOKPLOY_URL", "http://dokploy.test")
    monkeypatch.setenv("DOKPLOY_API_KEY", "test-key")
    monkeypatch.setenv("DOKPLOY_ENVIRONMENT_ID", "env-001")
    monkeypatch.setenv("WAIT_TIMEOUT", "10")

    with pytest.raises(SystemExit):
        DokployClient(TemplateManager()).deploy_stack("my-stack", compose_tmpl, wait=True)

    assert sleep_calls == [5, 5]
