"""Compose/env template loading and `$${VAR}` interpolation."""

from __future__ import annotations

import os
import re
import sys
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from pathlib import Path


class TemplateManager:
    """Load stack YAML from file or stdin and expand `$${VAR}` placeholders."""

    _VAR_PATTERN = re.compile(r"\$\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

    def _die(self, msg: str) -> NoReturn:
        sys.stderr.write(f"dokployer: {msg}\n")
        raise SystemExit(1)

    def interpolate(self, template: str) -> str:
        """Expand `$${VAR}` placeholders while leaving Dokploy and Compose syntax intact."""

        def _replace(match: re.Match[str]) -> str:  # noqa: RET503
            name = match.group(1)
            default = match.group(2)
            value = os.environ.get(name)
            if value is not None:
                return value
            if default is not None:
                return default
            self._die(f"template references $${{{name}}} but {name} is not set")

        return self._VAR_PATTERN.sub(_replace, template)

    def read_compose_template(self, compose_template: Path | None) -> str:
        """Return stack YAML from ``compose_template`` or stdin when ``None``."""
        if compose_template is not None:
            if not compose_template.is_file():
                self._die(f"compose template not found: {compose_template}")
            return compose_template.read_text(encoding="utf-8")

        if sys.stdin.isatty():
            self._die(
                "compose template not provided: pass -f/--compose-template or pipe YAML to stdin",
            )

        raw_template = sys.stdin.read()
        if not raw_template.strip():
            self._die("compose template is empty")
        return raw_template
