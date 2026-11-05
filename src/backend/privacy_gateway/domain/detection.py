import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

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


class EmailReveal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    start: int = Field(ge=0, lt=20_000)
    end: int = Field(gt=0, le=20_000)


def credential_detections(text: str) -> tuple[Detection, ...]:
    result: dict[tuple[int, int], Detection] = {}
    for match in DEMO_SECRET.finditer(text):
        result[(match.start(), match.end())] = Detection(
            match.start(), match.end(), "demo_secret", False
        )
        if len(result) > 512:
            raise PrivacyError("DETECTION_LIMIT")
    for match in LABELED_SECRET.finditer(text):
        value = match[1].rstrip(".")
        if value:
            start, end = match.start(1), match.start(1) + len(value)
            result[(start, end)] = Detection(start, end, "credential", False)
            if len(result) > 512:
                raise PrivacyError("DETECTION_LIMIT")
    return tuple(result.values())


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
        for pattern, category, restore in ((EMAIL, "email", True),):
            for match in pattern.finditer(segment):
                add(base + match.start(), base + match.end(), category, restore)
        for term in terms:
            if not term:
                raise PrivacyError("INVALID_TERMS")
            offset = 0
            while (start := segment.find(term, offset)) >= 0:
                add(base + start, base + start + len(term), "manual", True)
                offset = start + 1
        for item in credential_detections(segment):
            add(base + item.start, base + item.end, item.category, False)
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
