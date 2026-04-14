from __future__ import annotations

import json

from ..core.errors import SerializeError
from ..typing.json import JSONValue

type BodyValue = str | bytes | bytearray | JSONValue | None


def body_to_text(body: BodyValue) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8")
    return json.dumps(body, ensure_ascii=False)


def body_to_bytes(body: BodyValue) -> bytes:
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, bytearray):
        return bytes(body)
    if isinstance(body, str):
        return body.encode("utf-8")
    return dump_json_bytes(body)


def load_json_body(body: BodyValue) -> JSONValue:
    if body is None:
        return None
    if isinstance(body, (dict, list, str, int, float, bool)):
        if isinstance(body, str):
            try:
                return json.loads(body)
            except json.JSONDecodeError as exc:
                raise SerializeError("Body does not contain valid JSON text") from exc
        return body
    try:
        return json.loads(body_to_text(body))
    except json.JSONDecodeError as exc:
        raise SerializeError("Body does not contain valid JSON bytes") from exc


def dump_json_bytes(value: JSONValue) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
