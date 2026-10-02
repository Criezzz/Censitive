import json
import math
from typing import Any

from privacy_gateway.domain.redaction import PrivacyError


def strict_json(raw: str | bytes, *, code: str = "INVALID_PAYLOAD") -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise ValueError("non-finite")

    def finite(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite")
        return result

    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        result = json.loads(
            raw, object_pairs_hook=unique, parse_constant=constant, parse_float=finite
        )
        pending = [result]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise PrivacyError(code) from None
