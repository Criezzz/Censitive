from dataclasses import dataclass
from enum import StrEnum


class SpanErrorCode(StrEnum):
    INVALID_MESSAGE_ID = "INVALID_MESSAGE_ID"
    INVALID_REVISION = "INVALID_REVISION"
    INVALID_OFFSET = "INVALID_OFFSET"
    INVALID_RANGE = "INVALID_RANGE"
    INVALID_TEXT = "INVALID_TEXT"
    INVALID_SPAN = "INVALID_SPAN"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    STALE_SPAN = "STALE_SPAN"
    INVALID_TOKEN = "INVALID_TOKEN"


class SpanValidationError(ValueError):
    def __init__(self, code: SpanErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


def _validate_source_identity(message_id: str, message_revision: int) -> None:
    if not isinstance(message_id, str) or not message_id.strip():
        raise SpanValidationError(SpanErrorCode.INVALID_MESSAGE_ID)
    if type(message_revision) is not int or message_revision < 1:
        raise SpanValidationError(SpanErrorCode.INVALID_REVISION)


@dataclass(frozen=True)
class SourceSpan:
    message_id: str
    message_revision: int
    start: int
    end: int

    def __post_init__(self) -> None:
        _validate_source_identity(self.message_id, self.message_revision)
        if type(self.start) is not int or type(self.end) is not int:
            raise SpanValidationError(SpanErrorCode.INVALID_OFFSET)
        if not 0 <= self.start < self.end:
            raise SpanValidationError(SpanErrorCode.INVALID_RANGE)


def validate_span(
    span: SourceSpan,
    *,
    text: str,
    message_id: str,
    message_revision: int,
) -> None:
    if not isinstance(span, SourceSpan):
        raise SpanValidationError(SpanErrorCode.INVALID_SPAN)
    if not isinstance(text, str):
        raise SpanValidationError(SpanErrorCode.INVALID_TEXT)
    _validate_source_identity(message_id, message_revision)
    if span.message_id != message_id:
        raise SpanValidationError(SpanErrorCode.SOURCE_MISMATCH)
    if span.message_revision != message_revision:
        raise SpanValidationError(SpanErrorCode.STALE_SPAN)
    if span.end > len(text):
        raise SpanValidationError(SpanErrorCode.INVALID_RANGE)


@dataclass(frozen=True)
class Replacement:
    span: SourceSpan
    token: str

    def __post_init__(self) -> None:
        if not isinstance(self.span, SourceSpan):
            raise SpanValidationError(SpanErrorCode.INVALID_SPAN)
        if not isinstance(self.token, str) or not self.token.strip():
            raise SpanValidationError(SpanErrorCode.INVALID_TOKEN)
