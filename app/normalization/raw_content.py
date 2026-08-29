from __future__ import annotations

from typing import Any, Protocol


class RawBodyRecord(Protocol):
    body_json: dict[str, Any] | list[Any] | None


def decoded_raw_json(raw: RawBodyRecord) -> dict[str, Any] | list[Any] | None:
    decoder = getattr(raw, "decoded_body_json", None)
    if callable(decoder):
        return decoder()
    return raw.body_json
