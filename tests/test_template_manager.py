"""Tests for TemplateManager."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import pytest

import dokployer.template_manager as tm_mod
from dokployer.template_manager import TemplateManager


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
    assert TemplateManager().interpolate("prefix $${MY_VAR} suffix") == "prefix hello suffix"


def test_interpolate_leaves_dokploy_double_brace_with_dot() -> None:
    assert (
        TemplateManager().interpolate("${{environment.LOG_LEVEL}}") == "${{environment.LOG_LEVEL}}"
    )


def test_interpolate_leaves_dokploy_double_brace_without_dot() -> None:
    assert TemplateManager().interpolate("${{DATABASE_USER}}") == "${{DATABASE_USER}}"


def test_interpolate_leaves_dollar_brace_literal() -> None:
    assert TemplateManager().interpolate("${SOME_VAR}") == "${SOME_VAR}"


def test_interpolate_leaves_dollar_var_literal() -> None:
    assert TemplateManager().interpolate("$SOME_VAR") == "$SOME_VAR"


def test_interpolate_no_vars_returns_unchanged() -> None:
    assert TemplateManager().interpolate("no vars here") == "no vars here"


def test_interpolate_exits_on_missing_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(SystemExit):
        TemplateManager().interpolate("$${MISSING_VAR}")


def test_interpolate_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    assert TemplateManager().interpolate("$${MISSING_VAR:-}") == ""


def test_interpolate_default_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    assert TemplateManager().interpolate("$${MISSING_VAR:-fallback}") == "fallback"


def test_interpolate_set_var_ignores_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_VAR", "actual")
    assert TemplateManager().interpolate("$${MY_VAR:-fallback}") == "actual"


def test_read_compose_template_reads_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tm_mod.sys,
        "stdin",
        _FakeStdin("version: '3'\n", is_tty=False),
    )
    assert TemplateManager().read_compose_template(None) == "version: '3'\n"


def test_read_compose_template_rejects_missing_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tm_mod.sys, "stdin", _FakeStdin("", is_tty=True))
    with pytest.raises(SystemExit):
        TemplateManager().read_compose_template(None)


def test_read_compose_template_from_file(tmp_path: Path) -> None:
    p = tmp_path / "stack.yml"
    p.write_text("x: 1\n", encoding="utf-8")
    assert TemplateManager().read_compose_template(p) == "x: 1\n"
