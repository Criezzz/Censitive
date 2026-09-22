"""Small, explicit rule coverage for the local demonstration (not semantic detection)."""

import re
from dataclasses import dataclass

from privacy_gateway.domain.redaction import TOKEN, PrivacyError
from privacy_gateway.domain.semantic import Protection

EMAIL_LOCAL = r"a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-"
EMAIL = re.compile(rf"(?<![{EMAIL_LOCAL}])[{EMAIL_LOCAL}]++@[a-zA-Z0-9-]++(?:\.[a-zA-Z0-9-]++)++")
DEMO_SECRET = re.compile(r"\bDEMO_SECRET_[A-Za-z0-9_]+\b")
LABELED_SECRET = re.compile(
    r"\b(?:password|passwd|api[_ -]?key|access[_ -]?token|mật khẩu)"
    r"\s*(?:is\b|là\b|[:=])\s*[\"']?([^\s\"';,]{4,200})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Detection:
    start: int
    end: int
    category: str
    restore_allowed: bool


def detect(
    text: str,
    terms: tuple[str, ...],
    *,
    history: bool = False,
    protections: tuple[Protection, ...] = (),
    merge_overlaps: bool = False,
) -> list[Detection]:
    segments: list[tuple[int, str]] = []
    offset = 0
    if history:
        for token in TOKEN.finditer(text):
            segments.append((offset, text[offset : token.start()]))
            offset = token.end()
    segments.append((offset, text[offset:]))
    candidates: dict[tuple[int, int], Detection] = {}

    def add(start: int, end: int, category: str, restore: bool) -> None:
        if (start, end) not in candidates:
            if len(candidates) >= 512:
                raise PrivacyError("DETECTION_LIMIT")
            candidates[(start, end)] = Detection(start, end, category, restore)
        elif not restore:
            candidates[(start, end)] = Detection(start, end, category, False)

    for base, segment in segments:
        for pattern, category, restore in (
            (EMAIL, "email", True),
            (DEMO_SECRET, "demo_secret", False),
        ):
            for match in pattern.finditer(segment):
                add(base + match.start(), base + match.end(), category, restore)
        for term in terms:
            if not term:
                raise PrivacyError("INVALID_TERMS")
            offset = 0
            while (start := segment.find(term, offset)) >= 0:
                add(base + start, base + start + len(term), "manual", True)
                offset = start + 1
        for match in LABELED_SECRET.finditer(segment):
            value = match[1].rstrip(".")
            if value:
                add(base + match.start(1), base + match.start(1) + len(value), "credential", False)
        for protection in protections:
            offset = 0
            while (start := segment.find(protection.quote, offset)) >= 0:
                add(
                    base + start,
                    base + start + len(protection.quote),
                    "semantic_" + protection.category,
                    protection.restore_allowed,
                )
                offset = start + 1
    ordered = sorted(candidates.values(), key=lambda item: (item.start, item.end))
    if merge_overlaps:
        merged: list[Detection] = []
        for item in ordered:
            if merged and item.start < merged[-1].end:
                previous = merged.pop()
                category = (
                    item.category if item.category.startswith("semantic_") else previous.category
                )
                merged.append(
                    Detection(
                        previous.start,
                        max(previous.end, item.end),
                        category,
                        previous.restore_allowed and item.restore_allowed,
                    )
                )
            else:
                merged.append(item)
        return merged
    previous_end = 0
    for item in ordered:
        if item.start < previous_end:
            raise PrivacyError("OVERLAPPING_DETECTIONS")
        previous_end = item.end
    return ordered
