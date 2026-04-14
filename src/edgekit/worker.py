from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from functools import wraps
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, cast, get_args, get_origin, runtime_checkable

from ._js import JSEnv, JSExecutionContext, JSResponse, is_js_instance, is_js_proxy
from .core import bind_env
from .core.env import RawEnv
from .ffi import unwrap_raw
from .webapi.request import Request
from .webapi.response import Response

if TYPE_CHECKING:
    from typing_extensions import TypeVar as DefaultTypeVar
    from workers import WorkerEntrypoint as _NativeWorkerEntrypoint

    EnvT = DefaultTypeVar("EnvT", default=JSEnv)
else:
    try:
        from workers import WorkerEntrypoint as _NativeWorkerEntrypoint
    except ModuleNotFoundError:

        class _NativeWorkerEntrypoint:
            ctx: JSExecutionContext
            env: JSEnv

            def __init__(self, ctx: JSExecutionContext, env: JSEnv) -> None:
                self.ctx = ctx
                self.env = env

            def __init_subclass__(cls, **_kwargs: object) -> None:
                super().__init_subclass__()

    EnvT = TypeVar("EnvT")

type FetchResult = Response | JSResponse
type FetchHandler[EnvT] = Callable[[WorkerEntrypoint[EnvT], Request], Awaitable[FetchResult] | FetchResult]
type RuntimeFetchHandler[EnvT] = Callable[[WorkerEntrypoint[EnvT], object], Awaitable[JSResponse]]


@runtime_checkable
class SupportsJSObjectResponse(Protocol):
    @property
    def js_object(self) -> JSResponse: ...


class WorkerEntrypoint(_NativeWorkerEntrypoint, Generic[EnvT]):
    ctx: JSExecutionContext
    env: EnvT
    raw_env_source: RawEnv | None = None
    __edgekit_env_spec__: type[Any] | None = None

    @property
    def raw_env(self) -> JSEnv:
        raw_env = self.raw_env_source
        if raw_env is None or isinstance(raw_env, Mapping):
            raise RuntimeError("WorkerEntrypoint.raw_env is only available for a JavaScript-backed env")
        return raw_env

    def __class_getitem__(cls, env_spec: object) -> type[object]:
        if not isinstance(env_spec, type):
            return cls
        return _specialize_worker_entrypoint(cls, env_spec)

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        env_spec = _resolve_env_spec(cls)
        cls.__edgekit_env_spec__ = env_spec
        cls.__init__ = _wrap_init(cast(type[object], cls), env_spec)
        fetch_handler = cls.__dict__.get("fetch")
        if not callable(fetch_handler):
            return
        cls.fetch = _wrap_fetch(cast(FetchHandler[object], fetch_handler))


def _wrap_fetch(handler: FetchHandler[EnvT]) -> RuntimeFetchHandler[EnvT]:
    @wraps(handler)
    async def wrapped(instance: WorkerEntrypoint[EnvT], request: object) -> JSResponse:
        result = handler(instance, Request.wrap(request))
        if isawaitable(result):
            result = await result
        return _coerce_fetch_result(result)

    return wrapped


def _coerce_fetch_result(result: FetchResult) -> JSResponse:
    if isinstance(result, Response):
        return result.raw
    if is_js_proxy(result) and is_js_instance(result, "Response"):
        return cast(JSResponse, result)
    if isinstance(result, SupportsJSObjectResponse):
        return result.js_object
    try:
        raw_result = unwrap_raw(result)
    except TypeError as exc:
        raise TypeError(f"Expected Response-like result, got {type(result).__name__}") from exc
    if not is_js_instance(raw_result, "Response"):
        raise TypeError(f"Expected Response-like result, got {type(result).__name__}")
    return cast(JSResponse, raw_result)


def _resolve_env_spec(cls: type[object]) -> type[Any] | None:
    own_spec = getattr(cls, "__dict__", {}).get("__edgekit_env_spec__")
    if isinstance(own_spec, type):
        return cast(type[Any], own_spec)
    for base in getattr(cls, "__orig_bases__", ()):
        if get_origin(base) is WorkerEntrypoint:
            args = get_args(base)
            if args and isinstance(args[0], type):
                return cast(type[Any], args[0])
    for base in cls.__bases__:
        inherited_spec = getattr(base, "__edgekit_env_spec__", None)
        if inherited_spec is not None:
            return cast(type[Any], inherited_spec)
    return None


def _wrap_init(
    cls: type[object],
    env_spec: type[Any] | None,
) -> Callable[..., None]:
    original_init = cast(Callable[..., None], getattr(cls, "__init__"))

    def wrapped_init(self: WorkerEntrypoint[object], *args: object, **kwargs: object) -> None:
        raw_env = _extract_env_argument(args, kwargs)
        original_init(self, *args, **kwargs)
        if raw_env is None:
            return
        self.raw_env_source = raw_env
        if env_spec is not None:
            self.env = cast(object, bind_env(env_spec, raw_env))

    return wrapped_init


def _extract_env_argument(args: tuple[object, ...], kwargs: dict[str, object]) -> RawEnv | None:
    if len(args) > 1:
        return cast(RawEnv, args[1])
    env_value = kwargs.get("env")
    if env_value is None:
        return None
    return cast(RawEnv, env_value)


def _specialize_worker_entrypoint(cls: type[object], env_spec: type[Any]) -> type[object]:
    return type(
        cls.__name__,
        (cls,),
        {
            "__module__": cls.__module__,
            "__edgekit_env_spec__": env_spec,
        },
    )
