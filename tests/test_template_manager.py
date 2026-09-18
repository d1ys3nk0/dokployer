"""Tests for ComposeTemplate."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

import dokployer.template_manager as tm_mod
from dokployer.errors import TemplateError
from dokployer.template_manager import ComposeTemplate


class _FakeStdin(io.StringIO):
    def __init__(self, value: str, *, is_tty: bool) -> None:
        super().__init__(value)
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


def test_interpolate_replaces_dollar_dollar_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_VAR", "hello")
    assert ComposeTemplate().interpolate("prefix $${MY_VAR} suffix") == "prefix hello suffix"


def test_interpolate_leaves_dokploy_double_brace_with_dot() -> None:
    assert (
        ComposeTemplate().interpolate("${{environment.LOG_LEVEL}}") == "${{environment.LOG_LEVEL}}"
    )


def test_interpolate_leaves_dokploy_double_brace_without_dot() -> None:
    assert ComposeTemplate().interpolate("${{DATABASE_USER}}") == "${{DATABASE_USER}}"


def test_interpolate_leaves_dollar_brace_literal() -> None:
    assert ComposeTemplate().interpolate("${SOME_VAR}") == "${SOME_VAR}"


def test_interpolate_leaves_dollar_var_literal() -> None:
    assert ComposeTemplate().interpolate("$SOME_VAR") == "$SOME_VAR"


def test_interpolate_no_vars_returns_unchanged() -> None:
    assert ComposeTemplate().interpolate("no vars here") == "no vars here"


def test_interpolate_raises_on_missing_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(TemplateError) as exc_info:
        ComposeTemplate().interpolate("$${MISSING_VAR}")

    assert str(exc_info.value) == ("template references $${MISSING_VAR} but MISSING_VAR is not set")


def test_interpolate_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    assert ComposeTemplate().interpolate("$${MISSING_VAR:-}") == ""


def test_interpolate_default_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    assert ComposeTemplate().interpolate("$${MISSING_VAR:-fallback}") == "fallback"


def test_interpolate_set_var_ignores_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_VAR", "actual")
    assert ComposeTemplate().interpolate("$${MY_VAR:-fallback}") == "actual"


@pytest.mark.parametrize(
    ("placeholder", "expected"),
    [
        ("%{MY_VAR}", "actual"),
        ("%{MISSING_VAR:-}", ""),
        ("%{MISSING_VAR:-fallback}", "fallback"),
    ],
)
def test_interpolate_custom_prefix_forms(
    monkeypatch: pytest.MonkeyPatch,
    placeholder: str,
    expected: str,
) -> None:
    monkeypatch.setenv("MY_VAR", "actual")
    monkeypatch.delenv("MISSING_VAR", raising=False)

    assert ComposeTemplate("%").interpolate(placeholder) == expected


def test_interpolate_custom_prefix_leaves_other_syntax_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAR", "value")
    template = "$${VAR} ${VAR} $VAR ${{environment.VAR}}"

    assert ComposeTemplate("%").interpolate(template) == template


def test_interpolate_treats_regex_metacharacter_prefix_literally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VAR", "value")

    assert ComposeTemplate(".+").interpolate(".+{VAR} %{VAR}") == "value %{VAR}"


def test_interpolate_custom_prefix_error_uses_configured_syntax(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)

    with pytest.raises(TemplateError) as exc_info:
        ComposeTemplate("%").interpolate("%{MISSING_VAR}")

    assert str(exc_info.value) == ("template references %{MISSING_VAR} but MISSING_VAR is not set")


def test_load_reads_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tm_mod.sys,
        "stdin",
        _FakeStdin("version: '3'\n", is_tty=False),
    )
    assert ComposeTemplate().load(None) == "version: '3'\n"


def test_load_rejects_missing_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tm_mod.sys, "stdin", _FakeStdin("", is_tty=True))
    with pytest.raises(TemplateError):
        ComposeTemplate().load(None)


def test_load_from_file(tmp_path: Path) -> None:
    p = tmp_path / "stack.yml"
    p.write_text("x: 1\n", encoding="utf-8")
    assert ComposeTemplate().load(p) == "x: 1\n"
