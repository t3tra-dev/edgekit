from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .._js import JSEnv, JSExecutionContext
from ..ffi.proxy import JSProxyLike

type BindingKind = Literal["kv", "r2", "d1", "queue", "durable_object", "assets", "unknown"]


@dataclass(slots=True, frozen=True)
class BindingDescriptor:
    name: str
    kind: BindingKind
    raw: JSProxyLike


@dataclass(slots=True)
class RuntimeContext:
    env: JSEnv
    ctx: JSExecutionContext | None = None
