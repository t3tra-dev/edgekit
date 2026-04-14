from __future__ import annotations

from collections.abc import Awaitable
from contextvars import ContextVar, Token
from typing import TypeVar, cast, overload

from .core.errors import RuntimeCapabilityError

T = TypeVar("T")
EnvT = TypeVar("EnvT")

_CURRENT_ENV: ContextVar[object | None] = ContextVar("edgekit_current_env", default=None)


def await_sync(awaitable: Awaitable[T]) -> T:
    try:
        from pyodide.ffi import can_run_sync, run_sync # pyright: ignore[reportPrivateImportUsage]
    except ModuleNotFoundError as exc:
        raise RuntimeCapabilityError("Synchronous awaiting requires the Pyodide runtime bindings") from exc
    try:
        if not can_run_sync():
            raise RuntimeCapabilityError("The current runtime cannot synchronously await this operation")
    except NotImplementedError as exc:
        raise RuntimeCapabilityError("Synchronous awaiting is only available inside the Workers runtime") from exc
    return run_sync(awaitable)


@overload
def current_env() -> object: ...


@overload
def current_env(env_type: type[EnvT]) -> EnvT: ...


def current_env(env_type: type[EnvT] | None = None) -> EnvT | object:
    env = _CURRENT_ENV.get()
    if env is None:
        raise RuntimeCapabilityError("No EdgeKit env is active in the current execution context")
    return cast(EnvT | object, env)


def enter_env_scope(env: object) -> Token[object | None]:
    return _CURRENT_ENV.set(env)


def exit_env_scope(token: Token[object | None]) -> None:
    _CURRENT_ENV.reset(token)


__all__ = ["await_sync", "current_env"]
