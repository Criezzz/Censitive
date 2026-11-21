import json
from privacy_gateway.domain.redaction import PrivacyError


def strict_json(raw: str | bytes, *, code: str = "INVALID_INPUT") -> object:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise PrivacyError(code) from None
