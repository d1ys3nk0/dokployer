"""Compose/env template loading and environment interpolation."""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import TYPE_CHECKING

from dokployer.constants import DEFAULT_INTERPOLATION_PREFIX
from dokployer.errors import TemplateError

if TYPE_CHECKING:
    from pathlib import Path


logger = logging.getLogger(__name__)


class ComposeTemplate:
    """Load stack YAML from file or stdin and expand prefixed placeholders."""

    def __init__(
        self,
        interpolation_prefix: str = DEFAULT_INTERPOLATION_PREFIX,
    ) -> None:
        """Configure the literal prefix used to recognize placeholders."""
        self._interpolation_prefix = interpolation_prefix
        self._var_pattern = re.compile(
            rf"{re.escape(interpolation_prefix)}\{{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}}]*))?}}"
        )

    def interpolate(self, template: str) -> str:
        """Expand configured placeholders while leaving other syntax intact."""

        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            default = match.group(2)
            value = os.environ.get(name)
            if value is not None:
                return value
            if default is not None:
                return default
            placeholder = f"{self._interpolation_prefix}{{{name}}}"
            msg = f"template references {placeholder} but {name} is not set"
            raise TemplateError(
                msg,
            )

        return self._var_pattern.sub(_replace, template)

    def load(self, template_path: Path | None) -> str:
        """Return stack YAML from ``template_path`` or stdin when ``None``."""
        if template_path is not None:
            if not template_path.is_file():
                msg = f"compose template not found: {template_path}"
                raise TemplateError(msg)
            return template_path.read_text(encoding="utf-8")

        if sys.stdin.isatty():
            msg = "compose template not provided: pass -f/--compose-template or pipe YAML to stdin"
            raise TemplateError(
                msg,
            )

        raw_template = sys.stdin.read()
        if not raw_template.strip():
            msg = "compose template is empty"
            raise TemplateError(msg)
        return raw_template
