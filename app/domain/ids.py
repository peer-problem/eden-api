from __future__ import annotations

import re
import uuid

EDEN_ID_PATTERN = re.compile(r"^eden_(area|place|country|shop|alert)_[a-z0-9_-]+$")
NAMESPACE = uuid.UUID("6ec94ce1-737c-43af-9064-24902205202f")


def stable_eden_id(entity: str, source: str, external_id: str) -> str:
    """Create a deterministic public ID without exposing credentials or mutable labels."""
    if entity not in {"area", "place", "country", "shop", "alert"}:
        raise ValueError(f"Unsupported EDEN entity: {entity}")
    token = uuid.uuid5(NAMESPACE, f"{entity}:{source}:{external_id}").hex[:20]
    return f"eden_{entity}_{token}"


def is_eden_id(value: str) -> bool:
    return bool(EDEN_ID_PATTERN.fullmatch(value))
