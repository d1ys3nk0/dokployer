"""Tests for DokployClient transport layer."""

from __future__ import annotations

import json
import urllib.error
from typing import Self
from unittest.mock import MagicMock

import pytest

from dokployer.dokploy_client import DokployClient
from dokployer.errors import DokployAPIError


class MockResponse:
    """Mock response that works as a context manager for urllib."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class TestDokployClientTransport:
    """Tests for DokployClient HTTP transport methods."""

    def test_client_stores_init_params(self) -> None:
        client = DokployClient(base_url="http://localhost:8080", api_key="secret", timeout=60)
        assert client.base_url == "http://localhost:8080"
        assert client.api_key == "secret"
        assert client.timeout == 60

    def test_client_uses_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://env:9999")
        monkeypatch.setenv("DOKPLOY_API_KEY", "env-key")
        client = DokployClient()
        assert client.base_url == "http://env:9999"
        assert client.api_key == "env-key"

    def test_required_env_raises_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MISSING_VAR", raising=False)
        client = DokployClient()
        with pytest.raises(DokployAPIError) as exc_info:
            client._required_env("MISSING_VAR")
        assert "missing required environment variable: MISSING_VAR" in str(exc_info.value)

    def test_required_env_returns_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_VAR", "my-value")
        client = DokployClient()
        assert client._required_env("MY_VAR") == "my-value"

    def test_raise_if_api_error_silent_on_non_json(self) -> None:
        client = DokployClient()
        client._raise_if_api_error("/test", "not json at all")

    def test_raise_if_api_error_raises_on_error_code(self) -> None:
        client = DokployClient()
        with pytest.raises(DokployAPIError) as exc_info:
            client._raise_if_api_error(
                "/test",
                '{"code": "SOME_ERROR", "message": "boom"}',
            )
        assert "SOME_ERROR" in str(exc_info.value)
        assert "Dokploy API error" in str(exc_info.value)

    def test_request_builds_correct_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://test.local")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key123")

        client = DokployClient()

        mock_response = MockResponse(b'{"result": "ok"}')

        monkeypatch.setattr("urllib.request.urlopen", MagicMock(return_value=mock_response))

        client._request("GET", "/api/test")

        call_args = urllib.request.urlopen.call_args
        req = call_args[0][0]
        assert req.full_url == "http://test.local/api/test"
        assert req.method == "GET"

    def test_request_sends_api_key_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://test.local")
        monkeypatch.setenv("DOKPLOY_API_KEY", "secret-key")

        client = DokployClient()

        mock_response = MockResponse(b'{"result": "ok"}')

        monkeypatch.setattr("urllib.request.urlopen", MagicMock(return_value=mock_response))

        client._request("GET", "/api/test")

        call_args = urllib.request.urlopen.call_args
        req = call_args[0][0]
        assert req.headers.get("X-api-key") == "secret-key"

    def test_request_raises_dokploy_api_error_on_http_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://test.local")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")

        client = DokployClient()

        def raise_http_error(_req: object, **_kwargs: object) -> MagicMock:
            err = urllib.error.HTTPError(
                url="http://test.local/api/missing",
                code=404,
                msg="Not Found",
                hdrs={},
                fp=None,
            )
            raise err

        monkeypatch.setattr("urllib.request.urlopen", raise_http_error)

        with pytest.raises(DokployAPIError) as exc_info:
            client._request("GET", "/api/missing")
        assert exc_info.value.status_code == 404

    def test_request_raises_dokploy_api_error_on_network_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://test.local")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")

        client = DokployClient()

        def raise_url_error(_req: object, **_kwargs: object) -> MagicMock:
            msg = "connection refused"
            raise urllib.error.URLError(msg)

        monkeypatch.setattr("urllib.request.urlopen", raise_url_error)

        with pytest.raises(DokployAPIError) as exc_info:
            client._request("GET", "/api/test")
        assert "connection refused" in str(exc_info.value)

    def test_request_with_body_sends_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://test.local")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")

        client = DokployClient()

        mock_response = MockResponse(b'{"ok": true}')

        captured_data: dict[object, object] = {}

        def capture_urlopen(req: object, **_kwargs: object) -> MockResponse:
            if hasattr(req, "data") and req.data:
                captured_data["body"] = json.loads(req.data.decode("utf-8"))
            return mock_response

        monkeypatch.setattr("urllib.request.urlopen", capture_urlopen)

        client._request("POST", "/api/create", body={"name": "test"})

        assert captured_data["body"]["name"] == "test"
        req = captured_data.get("body", {})
        assert req.get("name") == "test"

    def test_get_environment_returns_parsed_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://test.local")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")

        client = DokployClient()

        mock_response = MockResponse(b'{"compose": []}')

        def capture_urlopen(_req: object, **_kwargs: object) -> MockResponse:
            return mock_response

        monkeypatch.setattr("urllib.request.urlopen", capture_urlopen)

        result = client.get_environment("env-001")

        assert result == {"compose": []}

    def test_create_compose_returns_parsed_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://test.local")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")

        client = DokployClient()

        mock_response = MockResponse(b'{"composeId": "cmp-123"}')

        def capture_urlopen(_req: object, **_kwargs: object) -> MockResponse:
            return mock_response

        monkeypatch.setattr("urllib.request.urlopen", capture_urlopen)

        result = client.create_compose(name="my-stack", environment_id="env-001")

        assert result == {"composeId": "cmp-123"}

    def test_update_compose_sends_correct_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://test.local")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")

        client = DokployClient()

        mock_response = MockResponse(b'{"ok": true}')

        captured_data: dict[object, object] = {}

        def capture_urlopen(req: object, **_kwargs: object) -> MockResponse:
            if hasattr(req, "data") and req.data:
                captured_data["body"] = json.loads(req.data.decode("utf-8"))
            return mock_response

        monkeypatch.setattr("urllib.request.urlopen", capture_urlopen)

        client.update_compose(
            compose_id="cmp-123",
            compose_file="version: '3'\nservices:\n  app:\n    image: test\n",
            env_content="FOO=bar\n",
        )

        assert captured_data["body"]["composeId"] == "cmp-123"
        assert "version: '3'" in captured_data["body"]["composeFile"]
        assert captured_data["body"]["env"] == "FOO=bar\n"

    def test_get_compose_status_returns_status_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://test.local")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")

        client = DokployClient()

        mock_response = MockResponse(b'{"composeStatus": "running"}')

        def capture_urlopen(_req: object, **_kwargs: object) -> MockResponse:
            return mock_response

        monkeypatch.setattr("urllib.request.urlopen", capture_urlopen)

        status = client.get_compose_status("cmp-123")

        assert status == "running"

    def test_get_stack_containers_by_app_name_returns_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://test.local")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")

        client = DokployClient()

        mock_response = MockResponse(b'[{"name": "app_api.1.abc", "state": "running"}]')

        monkeypatch.setattr("urllib.request.urlopen", MagicMock(return_value=mock_response))

        result = client.get_stack_containers_by_app_name("my app")

        call_args = urllib.request.urlopen.call_args
        req = call_args[0][0]
        assert req.full_url == (
            "http://test.local/api/docker.getStackContainersByAppName?appName=my+app"
        )
        assert result == [{"name": "app_api.1.abc", "state": "running"}]

    def test_get_deployments_by_compose_returns_trpc_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DOKPLOY_URL", "http://test.local")
        monkeypatch.setenv("DOKPLOY_API_KEY", "key")

        client = DokployClient()

        mock_response = MockResponse(b'{"result": {"data": {"json": [{"deploymentId": "dep-1"}]}}}')

        monkeypatch.setattr("urllib.request.urlopen", MagicMock(return_value=mock_response))

        result = client.get_deployments_by_compose("cmp-123")

        assert result == [{"deploymentId": "dep-1"}]
