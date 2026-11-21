import re
from dataclasses import dataclass


EMAIL_LOCAL = r"a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-"
EMAIL = re.compile(rf"(?<![{EMAIL_LOCAL}])[{EMAIL_LOCAL}]++@[a-zA-Z0-9-]++(?:\.[a-zA-Z0-9-]++)++")
LABELED_SECRET = re.compile(
    r"\b(?:password|passwd|api[_ -]?key|access[_ -]?token|mật khẩu)"
    r"\s*(?:is\b|là\b|[:=])\s*[\"']?([^\s\"';,]{4,})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Detection:
    start: int
    end: int
    category: str
    restore_allowed: bool


def credential_detections(text: str) -> tuple[Detection, ...]:
    result: dict[tuple[int, int], Detection] = {}
    for match in LABELED_SECRET.finditer(text):
        value = match[1].rstrip(".")
        if value:
            start, end = match.start(1), match.start(1) + len(value)
            result[(start, end)] = Detection(start, end, "credential", False)
    return tuple(result.values())
