from __future__ import annotations


class EdgeKitError(Exception):
    """Base exception for edgekit."""


class FFIConversionError(EdgeKitError):
    """Raised when a value cannot cross the FFI boundary safely."""


class BindingError(EdgeKitError):
    """Raised when a binding operation fails."""


class SerializeError(EdgeKitError):
    """Raised when serialization or deserialization fails."""


class TypeCoercionError(EdgeKitError):
    """Raised when a value cannot be coerced into the requested Python type."""


class RuntimeCapabilityError(EdgeKitError):
    """Raised when the current runtime does not expose a required capability."""
