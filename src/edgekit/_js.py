from __future__ import annotations

from collections.abc import Awaitable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, NotRequired, Protocol, TypeAlias, TypedDict, TypeGuard, cast, runtime_checkable

import pyodide.ffi

from .ffi.proxy import JSProxyLike

if TYPE_CHECKING:
    import js as _js

    JSAbortSignal: TypeAlias = _js.AbortSignal
    JSBlob: TypeAlias = _js.Blob
    JSFormData: TypeAlias = _js.FormData
    JSHeaders: TypeAlias = _js.Headers
    JSReadableStream: TypeAlias = _js.ReadableStream
    JSRequest: TypeAlias = _js.Request
    JSResponse: TypeAlias = _js.Response
    JSURL: TypeAlias = _js.URL
    JSURLSearchParams: TypeAlias = _js.URLSearchParams
else:
    JSAbortSignal: TypeAlias = JSProxyLike
    JSBlob: TypeAlias = JSProxyLike
    JSFormData: TypeAlias = JSProxyLike
    JSHeaders: TypeAlias = JSProxyLike
    JSReadableStream: TypeAlias = JSProxyLike
    JSRequest: TypeAlias = JSProxyLike
    JSResponse: TypeAlias = JSProxyLike
    JSURL: TypeAlias = JSProxyLike
    JSURLSearchParams: TypeAlias = JSProxyLike

JSArrayBuffer: TypeAlias = JSProxyLike
JSArrayBufferView: TypeAlias = JSProxyLike
JSUint8Array: TypeAlias = JSProxyLike
JSBinaryBuffer: TypeAlias = JSArrayBuffer | pyodide.ffi.JsBuffer | bytes | bytearray | memoryview
JSHeadersInit: TypeAlias = JSHeaders | Mapping[str, str] | Sequence[tuple[str, str]] | Sequence[Sequence[str]]
JSBodyInit: TypeAlias = (
    JSReadableStream | JSBlob | JSArrayBuffer | JSArrayBufferView | JSFormData | JSURLSearchParams | str
)
JSRequestInfo: TypeAlias = JSRequest | str


class JSRequestInit(TypedDict, total=False):
    method: str
    headers: JSHeadersInit
    body: JSBodyInit | None
    signal: JSAbortSignal | None


class JSResponseInit(TypedDict, total=False):
    status: int
    headers: JSHeadersInit


JSEnv: TypeAlias = JSProxyLike
JSExecutionContext: TypeAlias = JSProxyLike
JSDurableObjectId: TypeAlias = JSProxyLike


@runtime_checkable
class JSImportConstructorLike(Protocol):
    name: str


@runtime_checkable
class JSImportInstanceLike(Protocol):
    constructor: JSImportConstructorLike


class JSImportReflectLike(Protocol):
    def get(self, target: object, property_key: str) -> object: ...

    def has(self, target: object, property_key: str) -> bool: ...


@runtime_checkable
class JSHrefLike(Protocol):
    href: str


@runtime_checkable
class JSImportURLLike(Protocol):
    url: str


@runtime_checkable
class JSURLObjectLike(Protocol):
    href: str
    searchParams: JSURLSearchParams


@runtime_checkable
class JSRequestObjectLike(Protocol):
    method: str
    url: str
    headers: object


@runtime_checkable
class JSRequestWrapperLike(Protocol):
    js_object: JSRequest
    method: object
    url: str
    headers: object


@runtime_checkable
class JSResponseObjectLike(Protocol):
    status: int
    headers: object


@runtime_checkable
class JSStringValueLike(Protocol):
    value: str


@runtime_checkable
class JSHeaderItemsLike(Protocol):
    def items(self) -> Iterable[tuple[object, object]]: ...


class JSKVListKey(TypedDict):
    name: str


class JSKVListData(TypedDict):
    keys: Sequence[JSKVListKey]
    cursor: NotRequired[str | None]
    list_complete: NotRequired[bool]


class JSKVNamespaceLike(Protocol):
    def get(self, key: str, type: str | None = None) -> Awaitable[Any | None]: ...

    def put(self, key: str, value: Any, **options: Any) -> Awaitable[None]: ...

    def delete(self, key: str) -> Awaitable[None]: ...

    def list(self, *, prefix: str | None = None, limit: int | None = None) -> Awaitable[JSKVListData]: ...


@runtime_checkable
class JSR2ObjectLike(Protocol):
    key: str
    size: int
    httpMetadata: Any

    def arrayBuffer(self) -> Awaitable[JSBinaryBuffer]: ...

    def text(self) -> Awaitable[str]: ...

    def json(self) -> Awaitable[Any]: ...


class JSR2BucketLike(Protocol):
    def get(self, key: str) -> Awaitable[JSR2ObjectLike | None]: ...

    def put(self, key: str, value: Any, **options: Any) -> Awaitable[JSR2ObjectLike | None]: ...

    def delete(self, key: str) -> Awaitable[None]: ...


@runtime_checkable
class JSR2HTTPMetadataLike(Protocol):
    contentType: str | None


class JSD1TimingsData(TypedDict):
    sql_duration_ms: NotRequired[float]


class JSD1MetaData(TypedDict):
    served_by: NotRequired[str]
    served_by_region: NotRequired[str]
    served_by_primary: NotRequired[bool]
    timings: NotRequired[JSD1TimingsData]
    duration: NotRequired[float]
    changes: NotRequired[int]
    last_row_id: NotRequired[int]
    changed_db: NotRequired[bool]
    size_after: NotRequired[int]
    rows_read: NotRequired[int]
    rows_written: NotRequired[int]
    total_attempts: NotRequired[int]


class JSD1ResultData(TypedDict):
    success: NotRequired[bool]
    meta: NotRequired[JSD1MetaData]
    results: NotRequired[Sequence[Any] | None]


class JSD1ExecResultData(TypedDict):
    count: NotRequired[int]
    duration: NotRequired[float]


class JSD1PreparedStatementLike(Protocol):
    def bind(self, *params: Any) -> "JSD1PreparedStatementLike": ...

    def first(self, column_name: str | None = None) -> Awaitable[Any]: ...

    def all(self) -> Awaitable[JSD1ResultData | Sequence[Any]]: ...

    def run(self) -> Awaitable[JSD1ResultData]: ...


class JSD1DatabaseLike(Protocol):
    def prepare(self, sql: str) -> JSD1PreparedStatementLike: ...

    def exec(self, sql: str) -> Awaitable[JSD1ExecResultData]: ...


class JSQueueProducerLike(Protocol):
    def send(self, message: Any) -> Awaitable[None]: ...

    def sendBatch(self, messages: Sequence[Any]) -> Awaitable[None]: ...


class JSDurableObjectStubLike(Protocol):
    def fetch(self, request: JSRequestInfo) -> Awaitable[JSResponse]: ...


class JSFetcherLike(Protocol):
    def fetch(self, request: JSRequestInfo) -> Awaitable[JSResponse]: ...


class JSDurableObjectNamespaceLike(Protocol):
    def idFromName(self, name: str) -> JSDurableObjectId: ...

    def get(self, object_id: JSDurableObjectId) -> JSDurableObjectStubLike: ...


class JSIterableEntries(Protocol):
    def entries(self) -> Iterable[tuple[object, object]]: ...


class JSBodyReaderLike(Protocol):
    def arrayBuffer(self) -> Awaitable[JSBinaryBuffer]: ...

    def text(self) -> Awaitable[str]: ...

    def json(self) -> Awaitable[Any]: ...


def import_js_module() -> Any | None:
    try:
        import js as js_module
    except ModuleNotFoundError:
        return None
    return js_module


def require_js_module() -> Any:
    js_module = import_js_module()
    if js_module is None:
        raise RuntimeError("The 'js' module is only available inside a Pyodide or Workers runtime")
    return js_module


def is_js_proxy(value: object) -> TypeGuard[JSProxyLike]:
    return isinstance(value, pyodide.ffi.JsProxy)


def is_js_instance(value: object, constructor_name: str) -> bool:
    if not is_js_proxy(value):
        return False
    actual_name = _js_constructor_name(value)
    return actual_name == constructor_name


def js_to_py(value: object) -> object:
    if is_js_proxy(value):
        return value.to_py()
    return value


def js_buffer_to_bytes(value: object) -> bytes:
    if isinstance(value, pyodide.ffi.JsBuffer):
        return value.to_bytes()
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    raise TypeError(f"Expected a JavaScript buffer value, got {type(value).__name__}")


def js_object_from_mapping(mapping: Mapping[str, object]) -> JSProxyLike:
    js_module = require_js_module()
    return cast(
        JSProxyLike,
        pyodide.ffi.to_js(mapping, dict_converter=js_module.Object.fromEntries),
    )


def js_headers_from_items(items: Iterable[tuple[str, str]]) -> JSHeaders:
    js_module = require_js_module()
    return cast(JSHeaders, js_module.Headers.new(list(items)))


def js_url_from_string(url: str) -> JSURL:
    js_module = require_js_module()
    return cast(JSURL, js_module.URL.new(url))


def js_url_search_params_from_pairs(pairs: Iterable[tuple[str, str]]) -> JSURLSearchParams:
    js_module = require_js_module()
    return cast(JSURLSearchParams, js_module.URLSearchParams.new([[key, value] for key, value in pairs]))


def js_get_property(target: JSProxyLike, property_name: str) -> object:
    js_module = require_js_module()
    reflect = cast(JSImportReflectLike, js_module.Reflect)
    return reflect.get(target, property_name)


def js_has_property(target: JSProxyLike, property_name: str) -> bool:
    js_module = require_js_module()
    reflect = cast(JSImportReflectLike, js_module.Reflect)
    return reflect.has(target, property_name)


def _js_constructor_name(value: JSProxyLike) -> str | None:
    if not js_has_property(value, "constructor"):
        return None
    constructor = js_get_property(value, "constructor")
    if isinstance(constructor, JSImportConstructorLike):
        return constructor.name
    if is_js_proxy(constructor) and js_has_property(constructor, "name"):
        name = js_get_property(constructor, "name")
        if isinstance(name, str):
            return name
    return None
