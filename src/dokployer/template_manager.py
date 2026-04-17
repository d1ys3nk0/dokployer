"""Compose/env template loading and `$${VAR}` interpolation."""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import TYPE_CHECKING

from dokployer.errors import TemplateError

if TYPE_CHECKING:
    from pathlib import Path


logger = logging.getLogger(__name__)


class ComposeTemplate:
    """Load stack YAML from file or stdin and expand `$${VAR}` placeholders."""

    _VAR_PATTERN = re.compile(r"\$\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}")

    def interpolate(self, template: str) -> str:
        """Expand `$${VAR}` placeholders while leaving Dokploy and Compose syntax intact."""

        def _replace(match: re.Match[str]) -> str:
            name = match.group(1)
            default = match.group(2)
            value = os.environ.get(name)
            if value is not None:
                return value
            if default is not None:
                return default
            msg = f"template references $${{{name}}} but {name} is not set"
            raise TemplateError(
                msg,
            )

        return self._VAR_PATTERN.sub(_replace, template)

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
