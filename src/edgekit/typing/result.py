from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar, cast

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")
F = TypeVar("F")


class _MissingResultValue:
    __slots__ = ()


_MISSING = _MissingResultValue()


@dataclass(slots=True, frozen=True)
class Result(Generic[T, E]):
    _is_ok: bool
    _value: T | _MissingResultValue = _MISSING
    _error: E | _MissingResultValue = _MISSING

    @classmethod
    def ok(cls, value: T) -> Result[T, E]:
        return cast(Result[T, E], cls(_is_ok=True, _value=value))

    @classmethod
    def err(cls, error: E) -> Result[T, E]:
        return cast(Result[T, E], cls(_is_ok=False, _error=error))

    @property
    def is_ok(self) -> bool:
        return self._is_ok

    @property
    def is_err(self) -> bool:
        return not self._is_ok

    @property
    def value(self) -> T:
        if not self._is_ok or isinstance(self._value, _MissingResultValue):
            raise ValueError("Cannot read value from an error result")
        return self._value

    @property
    def error(self) -> E:
        if self._is_ok or isinstance(self._error, _MissingResultValue):
            raise ValueError("Cannot read error from a successful result")
        return self._error

    def unwrap(self) -> T:
        return self.value

    def unwrap_error(self) -> E:
        return self.error

    def map(self, func: Callable[[T], U]) -> Result[U, E]:
        if self._is_ok:
            return Result[U, E](_is_ok=True, _value=func(self.value))
        return Result[U, E](_is_ok=False, _error=self.error)

    def map_error(self, func: Callable[[E], F]) -> Result[T, F]:
        if self._is_ok:
            return Result[T, F](_is_ok=True, _value=self.value)
        return Result[T, F](_is_ok=False, _error=func(self.error))
