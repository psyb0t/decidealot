"""Typed errors at Decidealot's public and provider boundaries."""

from collections.abc import Sequence
from typing import Any


class DecidealotError(Exception):
    """Base error for a failure with a safe public message."""


class InvalidRequestError(DecidealotError):
    """The request does not meet the public System One contract."""


class UnknownModelError(InvalidRequestError):
    """The requested model selector is not supported locally."""


class TypeSafeValidationError(DecidealotError):
    """The request does not satisfy the official TypeSafe request schema.

    Carries the official ``HTTPValidationError.detail`` entries describing every
    value that failed, so the boundary answers with the documented 422 envelope.
    """

    def __init__(self, detail: Sequence[dict[str, Any]]) -> None:
        super().__init__("request validation failed")
        self.detail = list(detail)


class UnauthorizedError(DecidealotError):
    """The optional API key was configured but not supplied correctly."""


class ProviderUnavailableError(DecidealotError):
    """The selected local provider cannot currently serve a request."""


class ProviderBusyError(DecidealotError):
    """The selected local provider has an in-flight request."""
