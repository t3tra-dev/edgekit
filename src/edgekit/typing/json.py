from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]


def is_json_value(value: object) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True

    if isinstance(value, Mapping):
        mapping_value = cast(Mapping[object, object], value)
        return all(isinstance(key, str) and is_json_value(item) for key, item in mapping_value.items())

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence_value = cast(Sequence[object], value)
        return all(is_json_value(item) for item in sequence_value)

    return False
