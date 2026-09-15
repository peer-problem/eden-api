from __future__ import annotations

import hashlib
import json
from typing import Any


def lookup_key(**scope: Any) -> str:
    normalized = {key: value for key, value in scope.items() if value is not None}
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def lookup_key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()
