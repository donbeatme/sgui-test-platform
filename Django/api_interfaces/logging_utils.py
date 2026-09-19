from collections.abc import Mapping
from typing import Any
from uuid import uuid4


def new_trace_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def summarize_for_log(value: Any, max_keys: int = 20, max_item_types: int = 5) -> dict:
    """Return a small, non-sensitive summary for request payload logging."""
    if value is None:
        return {"type": "NoneType", "is_empty": True}

    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value), "is_empty": len(value) == 0}

    if isinstance(value, str):
        return {"type": "str", "length": len(value), "is_empty": value == ""}

    if isinstance(value, Mapping):
        keys = [str(key) for key in list(value.keys())[:max_keys]]
        return {
            "type": type(value).__name__,
            "size": len(value),
            "is_empty": len(value) == 0,
            "keys": keys,
            "truncated_keys": len(value) > max_keys,
        }

    if isinstance(value, list):
        item_types = [type(item).__name__ for item in value[:max_item_types]]
        return {
            "type": "list",
            "length": len(value),
            "is_empty": len(value) == 0,
            "item_types": item_types,
            "truncated_items": len(value) > max_item_types,
        }

    return {"type": type(value).__name__, "is_empty": False}
