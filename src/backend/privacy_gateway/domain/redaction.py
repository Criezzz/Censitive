import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from privacy_gateway.domain.models import Replacement, validate_span

PREFIX = "[[PG_"
TOKEN = re.compile(r"\[\[PG_v1_[0-9a-f]{32}\]\]")


class PrivacyError(ValueError):
    pass


@dataclass(frozen=True)
class Binding:
    token: str
    value: str
    restore_allowed: bool = True


def apply_spans(
    text: str,
    replacements: Sequence[Replacement],
    *,
    message_id: str,
    message_revision: int,
    allow_existing_tokens: bool = False,
) -> str:
    if not allow_existing_tokens and PREFIX in text:
        raise PrivacyError("RESERVED_TOKEN")
    previous_end = 0
    ordered = sorted(replacements, key=lambda item: item.span.start)
    for item in ordered:
        validate_span(
            item.span, text=text, message_id=message_id, message_revision=message_revision
        )
        if not TOKEN.fullmatch(item.token):
            raise PrivacyError("INVALID_TOKEN")
        if item.span.start < previous_end:
            raise PrivacyError("OVERLAPPING_SPANS")
        previous_end = item.span.end
    result = text
    for item in reversed(ordered):
        result = result[: item.span.start] + item.token + result[item.span.end :]
    return result


def tokens_in(text: str) -> set[str]:
    return set(TOKEN.findall(text))


def validate_tokens(text: str, manifest: Mapping[str, Binding]) -> None:
    if PREFIX in TOKEN.sub("", text):
        raise PrivacyError("MALFORMED_TOKEN")
    if not tokens_in(text) <= manifest.keys():
        raise PrivacyError("UNKNOWN_TOKEN")


def restore_text(
    text: str, manifest: Mapping[str, Binding], *, max_bytes: int | None = None
) -> str:
    validate_tokens(text, manifest)
    pieces: list[str] = []
    used = 0

    def append(piece: str) -> None:
        nonlocal used
        if max_bytes is not None:
            used += len(piece.encode("utf-8"))
            if used > max_bytes:
                raise PrivacyError("RESPONSE_LIMIT")
        pieces.append(piece)

    end = 0
    for match in TOKEN.finditer(text):
        append(text[end : match.start()])
        binding = manifest[match.group()]
        if binding.token != match.group():
            raise PrivacyError("INVALID_BINDING")
        append(binding.value if binding.restore_allowed else "[bí mật không phục hồi]")
        end = match.end()
    append(text[end:])
    return "".join(pieces)
