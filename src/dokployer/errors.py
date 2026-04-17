"""Exception hierarchy for dokployer."""

from __future__ import annotations


class DokployerError(Exception):
    """Root exception for all dokployer errors."""


class ConfigurationError(DokployerError):
    """Raised when required configuration (env vars, files) is missing."""


class TemplateError(DokployerError):
    """Raised when template loading or interpolation fails."""


class DokployAPIError(DokployerError):
    """Raised when an API-level error occurs (HTTP or application-level)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        api_code: str | None = None,
        path: str | None = None,
    ) -> None:
        """Initialize DokployAPIError with message and optional details."""
        super().__init__(message)
        self.status_code = status_code
        self.api_code = api_code
        self.message = message
        self.path = path

    def __str__(self) -> str:
        """Return a formatted string with error details."""
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"status_code={self.status_code}")
        if self.api_code is not None:
            parts.append(f"api_code={self.api_code}")
        if self.path is not None:
            parts.append(f"path={self.path}")
        return "; ".join(parts)


class DeployFailedError(DokployerError):
    """Raised when the deploy reaches a terminal error state."""


class DeployTimeoutError(DokployerError):
    """Raised when the deploy times out while waiting."""
