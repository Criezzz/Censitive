from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

from privacy_gateway.adapters.local_censitive import LocalCensitiveMethod
from privacy_gateway.domain import generalize
from privacy_gateway.domain.presend import literal_pattern
from privacy_gateway.domain.redaction import PrivacyError


LOCKED = {"credential", "manual"}
USER_LABEL = re.compile(r"\[REDACTED-\d+\]")


@dataclass(frozen=True)
class MethodPreparation:
    created: str
    checked: str
    literals: tuple[tuple[str, str], ...]
    denied: tuple[str, ...]
    sent: str
    mapping: tuple[tuple[str, str], ...]
    released: tuple[str, ...] = ()
    masked: tuple[str, ...] = ()

    def _outbound(self, original: str) -> tuple[str, dict[str, str], list[dict[str, object]]]:
        return outbound(
            original,
            self.created,
            self.checked,
            self.literals,
            self.released,
            self.masked,
        )

    def changes(self, original: str) -> list[dict[str, object]]:
        _, _, rows = self._outbound(original)
        rows += [
            {
                "original": value,
                "sent": value,
                "kind": "released",
                "releasable": True,
                "released": True,
            }
            for value in self.released
        ]
        locked = self.denied + tuple(
            value for value, category in self.literals if category in LOCKED
        )
        for row in rows:
            kind = row["kind"]
            row["releasable"] = row.get("releasable", True) and (
                kind not in LOCKED and kind not in {"candidate", "rewritten"}
            ) and not any(
                secret and secret.casefold() in str(row["original"]).casefold()
                for secret in locked
            )
            row.setdefault("released", False)
        return rows

    def adjust(
        self,
        original: str,
        released: tuple[str, ...],
        masked: tuple[str, ...],
    ) -> MethodPreparation:
        released = tuple(dict.fromkeys(released))
        masked = tuple(dict.fromkeys(m.strip() for m in masked if m.strip()))
        allowed = {str(r["original"]) for r in self.changes(original) if r["releasable"]}
        if not set(released) <= allowed:
            raise PrivacyError("RELEASE_FORBIDDEN")
        draft = replace(self, released=released, masked=masked)
        sent, mapping, _ = draft._outbound(original)
        if not sent.strip():
            raise PrivacyError("METHOD_INVALID_OUTPUT")
        return replace(draft, sent=sent, mapping=tuple(mapping.items()))


def kept_items(items: list[object], original: str, released: tuple[str, ...]) -> list[object]:
    kept = []
    for item in items:
        span = item.get("span") if isinstance(item, dict) else None
        if isinstance(span, str) and span.strip():
            found = re.search(literal_pattern(span), original, re.IGNORECASE)
            if (found.group() if found else span) in released:
                continue
        kept.append(item)
    return kept


def outbound(
    original: str,
    created: str,
    checked: str,
    literals: tuple[tuple[str, str], ...],
    released: tuple[str, ...] = (),
    masked: tuple[str, ...] = (),
) -> tuple[str, dict[str, str], list[dict[str, object]]]:
    text, mapping = generalize.substitute(
        created,
        kept_items(generalize.parse_items(checked), original, released),
        original,
        generalize.candidates(original, created),
    )
    rows: list[dict[str, object]] = [
        {"original": value, "sent": label, "kind": "generalized"}
        for label, value in mapping.items()
    ]
    scrub = [
        (v, c, f"[{c.upper()}]")
        for v, c in literals
        if c in LOCKED or v.strip() not in released
    ]
    scrub += [(v, "user", f"[REDACTED-{i + 1}]") for i, v in enumerate(masked)]
    spans = []
    for value, category, label in scrub:
        value = value.strip()
        if not value:
            continue
        pattern = re.escape(value) if category == "user" else literal_pattern(value)
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if category == "user" and not matches:
            raise PrivacyError("MASK_NOT_FOUND")
        spans.extend(
            (match.start(), match.end(), value, category, label)
            for match in matches
        )
    unions = []
    for start, end, value, category, label in sorted(spans, key=lambda span: (span[0], -span[1])):
        if unions and start < unions[-1][1]:
            left, right, members = unions[-1]
            unions[-1] = (left, max(right, end), members + [(value, category, label)])
        else:
            unions.append((start, end, [(value, category, label)]))
    edits = []
    next_label = len(masked) + 1
    for start, end, members in unions:
        user = next((label for _, category, label in members if category == "user"), None)
        label = user or next(
            (label for _, category, label in members if category in LOCKED), members[0][2]
        )
        if user:
            value = text[start:end]
            while label in mapping and mapping[label] != value:
                label = f"[REDACTED-{next_label}]"
                next_label += 1
            mapping[label] = value
        locked = any(category in LOCKED for _, category, _ in members)
        for value, category, _ in members:
            row = {
                "original": value,
                "sent": label,
                "kind": category,
                "releasable": not locked,
            }
            if row not in rows:
                rows.append(row)
        edits.append((start, end, label))
    covered = " | ".join(text[start:end] for start, end, _ in unions).casefold()
    for start, end, label in reversed(edits):
        text = text[:start] + label + text[end:]
    for value in generalize.candidates(original, created):
        if value not in released and re.search(literal_pattern(value), text, re.IGNORECASE):
            rows.append({"original": value, "sent": value, "kind": "candidate"})
    covered += " | " + " | ".join(str(row["original"]) for row in rows).casefold()
    covered += " | " + " | ".join(released + masked).casefold()
    for value in generalize.candidates(original, original):
        if (
            value.casefold() not in covered
            and value not in released
            and not re.search(literal_pattern(value), text, re.IGNORECASE)
        ):
            rows.append({"original": value, "sent": "", "kind": "rewritten"})
    return text, mapping, rows


def public_prompt(
    original: str,
    created: str,
    checked: str,
    literals: tuple[tuple[str, str], ...],
) -> tuple[str, dict[str, str]]:
    text, mapping, _ = outbound(original, created, checked, literals)
    return text, mapping


async def prepare_method(
    worker: LocalCensitiveMethod,
    original: str,
    literals: tuple[tuple[str, str], ...],
    denied: tuple[str, ...],
) -> MethodPreparation:
    created = await worker.create(original)
    candidates = generalize.candidates(original, created)
    checked = await worker.check(original, created, candidates)
    try:
        sent, mapping = public_prompt(original, created, checked, literals)
    except (PrivacyError, ValueError, TypeError):
        raise PrivacyError("METHOD_INVALID_OUTPUT") from None
    if not sent.strip():
        raise PrivacyError("METHOD_INVALID_OUTPUT")
    return MethodPreparation(created, checked, literals, denied, sent, tuple(mapping.items()))


async def finish_method(
    worker: LocalCensitiveMethod,
    original: str,
    prepared: MethodPreparation,
    response: str,
) -> tuple[str, bool]:
    denied = prepared.denied
    request = original
    for value in sorted({value for value in denied if value}, key=len, reverse=True):
        request = re.sub(re.escape(value), "[private value withheld]", request, flags=re.I)
    allowed = {
        label: value
        for label, value in prepared.mapping
        if not any(secret and secret.casefold() in value.casefold() for secret in denied)
    }
    answer = await worker.aggregate(request, response, allowed)
    unexpected = generalize.new_numbers(answer, request, response)
    if unexpected:
        answer = await worker.aggregate(
            request,
            response,
            allowed,
            "Remove these unsupported numeric values from your answer: "
            + json.dumps(unexpected)
            + ". Do not invent replacement facts.",
        )
    for label, value in allowed.items():
        if USER_LABEL.fullmatch(label):
            answer = answer.replace(label, value)
    remaining = bool(generalize.new_numbers(answer, request, response))
    if not answer.strip():
        raise PrivacyError("METHOD_INVALID_OUTPUT")
    if any(value and value in answer for value in denied):
        raise PrivacyError("FINAL_ANSWER_FORBIDDEN")
    return answer, remaining
