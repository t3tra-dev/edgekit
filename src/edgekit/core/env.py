from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, TypeVar, cast, get_origin, get_type_hints, overload, runtime_checkable

from .._js import JSEnv, is_js_proxy, js_get_property, js_has_property
from ..core.context import BindingKind
from ..typing.protocols import SupportsRaw
from .context import BindingDescriptor
from .errors import RuntimeCapabilityError

type RawEnv = JSEnv | Mapping[str, object]
EnvT = TypeVar("EnvT")


@runtime_checkable
class SupportsBindingKind(Protocol):
    @property
    def binding_kind(self) -> str: ...


class SupportsWrap(Protocol):
    @classmethod
    def wrap(cls, raw: object) -> object: ...


class BoundEnv:
    def __init__(self, raw: RawEnv, annotations: Mapping[str, object]) -> None:
        self._raw = raw
        self._annotations = dict(annotations)
        self._bindings: dict[str, object] = {}

    @property
    def raw(self) -> JSEnv:
        if isinstance(self._raw, Mapping):
            raise RuntimeCapabilityError("BoundEnv.raw is only available for a JavaScript-backed env")
        return self._raw

    def __getattr__(self, name: str) -> object:
        return self._resolve_binding(name)

    def descriptor(self, name: str) -> BindingDescriptor:
        value = self._resolve_binding(name)
        kind = cast(BindingKind, value.binding_kind) if isinstance(value, SupportsBindingKind) else "unknown"
        if isinstance(value, SupportsRaw):
            raw = value.raw
        elif is_js_proxy(value):
            raw = value
        else:
            raise RuntimeCapabilityError(f"Binding '{name}' does not expose a JavaScript raw object")
        return BindingDescriptor(name=name, kind=kind, raw=raw)

    def keys(self) -> tuple[str, ...]:
        return tuple(self._annotations)

    def items(self) -> tuple[tuple[str, object], ...]:
        return tuple((name, self._resolve_binding(name)) for name in self._annotations)

    def __repr__(self) -> str:
        names = ", ".join(sorted(self._annotations))
        return f"BoundEnv({names})"

    def _resolve_binding(self, name: str) -> object:
        if name in self._bindings:
            return self._bindings[name]
        annotation = self._annotations.get(name)
        if annotation is None:
            raise AttributeError(name)
        try:
            raw_value = _read_binding(self._raw, name)
        except AttributeError as exc:
            raise RuntimeCapabilityError(f"Missing binding '{name}' in env") from exc
        binding = _wrap_binding(annotation, raw_value)
        self._bindings[name] = binding
        return binding


@overload
def bind_env(spec: type[EnvT], raw: RawEnv) -> EnvT: ...


@overload
def bind_env(spec: Mapping[str, object], raw: RawEnv) -> BoundEnv: ...


def bind_env(spec: type[EnvT] | Mapping[str, object], raw: RawEnv) -> EnvT | BoundEnv:
    bound_env = BoundEnv(raw=raw, annotations=_resolve_spec(spec))
    if isinstance(spec, Mapping):
        return bound_env
    return cast(EnvT, bound_env)


def _resolve_spec(spec: type[Any] | Mapping[str, object]) -> dict[str, object]:
    if isinstance(spec, Mapping):
        return dict(spec)
    return {
        name: annotation
        for name, annotation in get_type_hints(spec, include_extras=True).items()
        if not name.startswith("_")
    }


def _wrap_binding(annotation: object, raw_value: object) -> object:
    origin = get_origin(annotation) or annotation

    if isinstance(origin, type):
        if hasattr(origin, "wrap"):
            return cast(type[SupportsWrap], origin).wrap(raw_value)
        if origin in (str, int, float, bool, bytes):
            return _coerce_scalar_binding(origin, raw_value)

    return raw_value


def _read_binding(raw: RawEnv, name: str) -> object:
    if isinstance(raw, Mapping):
        if name not in raw:
            raise AttributeError(name)
        return raw[name]
    if not js_has_property(raw, name):
        raise AttributeError(name)
    return js_get_property(raw, name)


def _coerce_scalar_binding(binding_type: type[object], raw_value: object) -> object:
    if binding_type is str:
        return str(raw_value)
    if binding_type is int:
        if isinstance(raw_value, bool):
            return int(raw_value)
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, float):
            return int(raw_value)
        if isinstance(raw_value, str):
            return int(raw_value)
        raise TypeError(f"Cannot coerce {type(raw_value).__name__} to int")
    if binding_type is float:
        if isinstance(raw_value, bool):
            return float(raw_value)
        if isinstance(raw_value, (int, float)):
            return float(raw_value)
        if isinstance(raw_value, str):
            return float(raw_value)
        raise TypeError(f"Cannot coerce {type(raw_value).__name__} to float")
    if binding_type is bool:
        return bool(raw_value)
    if binding_type is bytes:
        if isinstance(raw_value, bytes):
            return raw_value
        if isinstance(raw_value, bytearray):
            return bytes(raw_value)
        if isinstance(raw_value, memoryview):
            return raw_value.tobytes()
        if isinstance(raw_value, str):
            return raw_value.encode("utf-8")
        raise TypeError(f"Cannot coerce {type(raw_value).__name__} to bytes")
    raise TypeError(f"Unsupported scalar binding type: {binding_type.__name__}")
