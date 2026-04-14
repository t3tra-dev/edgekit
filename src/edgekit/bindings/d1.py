from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar, cast, overload

from .._js import JSD1DatabaseLike, JSD1PreparedStatementLike, js_to_py
from .._utils import instantiate_type, maybe_await
from ..core.errors import BindingError

type SQLValue = str | int | float | bool | bytes | None
T = TypeVar("T")


@dataclass(slots=True)
class D1ExecResult:
    raw: Mapping[str, object]
    count: int
    duration: float


@dataclass(slots=True)
class D1RunResult:
    raw: Mapping[str, object]
    success: bool
    meta: Mapping[str, object]
    results: list[object]

    @property
    def rows_affected(self) -> int:
        changes = _extract_int(self.meta.get("changes"))
        if changes != 0:
            return changes
        return _extract_int(self.meta.get("rows_written"))


class D1PreparedStatement:
    def __init__(self, raw: JSD1PreparedStatementLike) -> None:
        self._raw = raw

    @property
    def raw(self) -> JSD1PreparedStatementLike:
        return self._raw

    def bind(self, *params: SQLValue) -> D1PreparedStatement:
        return D1PreparedStatement(self._raw.bind(*params))

    @overload
    async def first(self, column_name: str | None = None, *, type: None = None) -> object | None: ...

    @overload
    async def first(self, column_name: str | None = None, *, type: type[T]) -> T | None: ...

    async def first(self, column_name: str | None = None, *, type: type[T] | None = None) -> T | object | None:
        result = js_to_py(await maybe_await(self._raw.first(column_name)))
        if result is None:
            return None
        return cast(T | object, instantiate_type(type, result))

    @overload
    async def all(self, *, type: None = None) -> list[object]: ...

    @overload
    async def all(self, *, type: type[T]) -> list[T]: ...

    async def all(self, *, type: type[T] | None = None) -> list[T] | list[object]:
        result = _coerce_result_mapping(js_to_py(await maybe_await(self._raw.all())), operation="all()")
        rows = _coerce_result_rows(result)
        return [cast(T | object, instantiate_type(type, row)) for row in rows]

    async def run(self) -> D1RunResult:
        result = _coerce_result_mapping(js_to_py(await maybe_await(self._raw.run())), operation="run()")
        return D1RunResult(
            raw=result,
            success=bool(result.get("success", True)),
            meta=_coerce_result_meta(result),
            results=_coerce_result_rows(result),
        )


class D1Database:
    _binding_kind = "d1"

    def __init__(self, raw: JSD1DatabaseLike) -> None:
        self._raw = raw

    @classmethod
    def wrap(cls, raw: JSD1DatabaseLike | D1Database) -> "D1Database":
        if isinstance(raw, D1Database):
            return raw
        return cls(raw)

    @property
    def raw(self) -> JSD1DatabaseLike:
        return self._raw

    @property
    def binding_kind(self) -> str:
        return self._binding_kind

    def prepare(self, sql: str) -> D1PreparedStatement:
        return D1PreparedStatement(self._raw.prepare(sql))

    async def exec(self, sql: str) -> D1ExecResult:
        result = js_to_py(await maybe_await(self._raw.exec(sql)))
        if not isinstance(result, Mapping):
            raise BindingError("D1 binding returned a non-mapping exec() result")
        result_mapping = cast(Mapping[str, object], result)
        return D1ExecResult(
            raw=result_mapping,
            count=_extract_int(result_mapping.get("count")),
            duration=_extract_float(result_mapping.get("duration")),
        )


def _coerce_result_mapping(result: object, *, operation: str) -> Mapping[str, object]:
    if isinstance(result, Mapping):
        return cast(Mapping[str, object], result)
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        sequence_result = cast(Sequence[object], result)
        return {"results": [js_to_py(item) for item in sequence_result]}
    raise BindingError(f"D1 binding returned a non-mapping {operation} result")


def _coerce_result_meta(result: Mapping[str, object]) -> Mapping[str, object]:
    meta = result.get("meta", {})
    if isinstance(meta, Mapping):
        return cast(Mapping[str, object], meta)
    raise BindingError("D1 binding returned a non-mapping meta payload")


def _coerce_result_rows(result: Mapping[str, object]) -> list[object]:
    rows = result.get("results")
    if rows is None:
        return []
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
        sequence_rows = cast(Sequence[object], rows)
        return [js_to_py(row) for row in sequence_rows]
    raise BindingError("D1 binding returned a non-sequence results payload")


def _extract_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _extract_float(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0
