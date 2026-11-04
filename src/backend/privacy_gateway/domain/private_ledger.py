from __future__ import annotations

import hashlib
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Literal, TypedDict

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from privacy_gateway.domain.redaction import PrivacyError

MAX_SOURCE_CHARS = 12_000
MAX_EVENTS = 32
MAX_REVISIONS = 32
MAX_TEXT_CHARS = 256
MAX_LABEL_CHARS = 128
MAX_QUOTE_CHARS = 800
MAX_AMOUNT_DIGITS = 50

AmountID = Annotated[str, Field(pattern=r"^amt_[0-9a-f]{32}$")]
ScalarField = Literal["surcharge_percent", "budget", "recipient", "project", "unit"]
ParserVersion = Literal[1, 2]
CURRENT_PARSER_VERSION: ParserVersion = 2
_PERCENT_GLYPHS = frozenset("%％﹪٪‰‱؉؊")

_NUMBER_TOKEN = re.compile(r"^-?[0-9]+(?:[.,][0-9]+)?$")
_GROUPED_COMMA = re.compile(r"^-?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?$")
_GROUPED_DOT = re.compile(r"^-?[0-9]{1,3}(?:\.[0-9]{3})+,[0-9]+$")
_AMBIGUOUS_COMMA_GROUP = re.compile(r"^-?[0-9]+,[0-9]{3}$")
_AMBIGUOUS_DOT_DECIMAL = re.compile(r"^-?[0-9]+\.[0-9]{3}$")
_AMBIGUOUS_COMMA_DECIMAL = re.compile(r"^-?[0-9]+,[0-9]{3}$")
_SIGN_CHARS = frozenset("_+-−＋－﹢﹣⁺⁻₊₋±∓➕➖\u02d6\u02d7")
_WIRE_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$")


def _wire_decimal(value: object) -> object:
    if type(value) is Decimal:
        return value
    if type(value) is not str or not _WIRE_DECIMAL.fullmatch(value):
        raise ValueError("decimal wire value")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise ValueError("non-finite decimal")
    return parsed


class StrictLedgerModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )


class LedgerEvidence(StrictLedgerModel):
    quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)


class AddAmount(StrictLedgerModel):
    kind: Literal["add"] = "add"
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    value_quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)
    amount: Decimal
    evidence: LedgerEvidence

    @field_validator("amount", mode="before")
    @classmethod
    def decode_amount(cls, value: object) -> object:
        return _wire_decimal(value)


class ReplaceAmount(StrictLedgerModel):
    kind: Literal["replace_amount"] = "replace_amount"
    amount_id: AmountID
    value_quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)
    amount: Decimal
    evidence: LedgerEvidence

    @field_validator("amount", mode="before")
    @classmethod
    def decode_amount(cls, value: object) -> object:
        return _wire_decimal(value)


class CancelAmount(StrictLedgerModel):
    kind: Literal["cancel"] = "cancel"
    amount_id: AmountID
    evidence: LedgerEvidence


class SetScalar(StrictLedgerModel):
    kind: Literal["set_scalar"] = "set_scalar"
    field: ScalarField
    value_quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)
    value: Decimal | str
    evidence: LedgerEvidence

    @model_validator(mode="before")
    @classmethod
    def decode_numeric_value(cls, value: object) -> object:
        if type(value) is not dict:
            return value
        field = value.get("field")
        if field in {"surcharge_percent", "budget"} and type(value.get("value")) is str:
            copied = dict(value)
            copied["value"] = _wire_decimal(value["value"])
            return copied
        return value


type LedgerEvent = Annotated[
    AddAmount | ReplaceAmount | CancelAmount | SetScalar,
    Field(discriminator="kind"),
]


class LedgerAmount(StrictLedgerModel):
    amount_id: AmountID
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    amount: Decimal
    source_quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)
    active: bool

    @field_validator("amount", mode="before")
    @classmethod
    def decode_amount(cls, value: object) -> object:
        return _wire_decimal(value)


class LedgerEffective(StrictLedgerModel):
    active_amounts: tuple[Decimal, ...] = Field(max_length=64)
    surcharge_percent: Decimal | None = None
    budget: Decimal | None = None
    recipient: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    project: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    unit: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)

    @field_validator("surcharge_percent", "budget", mode="before")
    @classmethod
    def decode_decimal(cls, value: object) -> object:
        return None if value is None else _wire_decimal(value)

    @field_validator("active_amounts", mode="before")
    @classmethod
    def freeze_amounts(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value


class LedgerRevision(StrictLedgerModel):
    parser_version: ParserVersion = 1
    origin: Literal["model", "user"] = Field(
        default="model", exclude_if=lambda value: value == "model"
    )
    revision: int = Field(ge=1, le=MAX_REVISIONS)
    parent_revision: int | None = Field(default=None, ge=1, le=MAX_REVISIONS)
    source_text: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    events: tuple[LedgerEvent, ...] = Field(min_length=1, max_length=MAX_EVENTS)
    records: tuple[LedgerAmount, ...] = Field(max_length=64)
    recipient: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    project: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    unit: str | None = Field(default=None, max_length=MAX_TEXT_CHARS)
    surcharge_percent: Decimal | None = None
    budget: Decimal | None = None

    @field_validator("parser_version", mode="before")
    @classmethod
    def strict_parser_version(cls, value: object) -> object:
        if type(value) is not int or value not in (1, 2):
            raise ValueError("ledger parser version")
        return value

    @field_validator("surcharge_percent", "budget", mode="before")
    @classmethod
    def decode_decimal(cls, value: object) -> object:
        return None if value is None else _wire_decimal(value)

    @field_validator("events", "records", mode="before")
    @classmethod
    def freeze_sequences(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value


class PrivateLedger(StrictLedgerModel):
    revisions: tuple[LedgerRevision, ...] = Field(max_length=MAX_REVISIONS)

    @field_validator("revisions", mode="before")
    @classmethod
    def freeze_revisions(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value


class RawAddEvent(StrictLedgerModel):
    kind: Literal["add"]
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    value_quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)
    evidence: LedgerEvidence


class RawReplaceEvent(StrictLedgerModel):
    kind: Literal["replace_amount"]
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    value_quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)
    evidence: LedgerEvidence


class RawCancelEvent(StrictLedgerModel):
    kind: Literal["cancel"]
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)
    evidence: LedgerEvidence


class RawSetScalarEvent(StrictLedgerModel):
    kind: Literal["set_scalar"]
    field: ScalarField
    value_quote: str = Field(min_length=1, max_length=MAX_QUOTE_CHARS)
    evidence: LedgerEvidence


type RawLedgerEvent = Annotated[
    RawAddEvent | RawReplaceEvent | RawCancelEvent | RawSetScalarEvent,
    Field(discriminator="kind"),
]


class NoLedgerChanges(Enum):
    NO_CHANGES = "no_changes"


class RawLedgerProposal(StrictLedgerModel):
    events: tuple[RawLedgerEvent, ...] = Field(min_length=0, max_length=MAX_EVENTS)

    @field_validator("events", mode="before")
    @classmethod
    def freeze_events(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value


class _ScalarValues(TypedDict):
    surcharge_percent: Decimal | None
    budget: Decimal | None
    recipient: str | None
    project: str | None
    unit: str | None


class LedgerCandidate(StrictLedgerModel):
    base_revision: int | None = Field(default=None, ge=1, le=MAX_REVISIONS)
    revision: LedgerRevision


def _fail(code: str) -> PrivacyError:
    return PrivacyError(code)


def source_digest(source: str) -> str:
    if type(source) is not str or not source or len(source) > MAX_SOURCE_CHARS:
        raise _fail("LEDGER_SOURCE_INVALID")
    try:
        encoded = source.encode("utf-8")
    except UnicodeError:
        raise _fail("LEDGER_SOURCE_INVALID") from None
    return hashlib.sha256(encoded).hexdigest()


def _validate_decimal(value: Decimal, *, code: str = "LEDGER_NUMBER_INVALID") -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise _fail(code)
    if len(value.as_tuple().digits) > MAX_AMOUNT_DIGITS:
        raise _fail(code)
    exponent = value.as_tuple().exponent
    if type(exponent) is not int or abs(exponent) > 100:
        raise _fail(code)
    return value


def _revalidate(model: type[BaseModel], value: object, code: str) -> BaseModel:
    try:
        return model.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        raise _fail(code) from None


def _occurrence(source: str, quote: str) -> tuple[int, int]:
    if type(quote) is not str or not quote or len(quote) > MAX_QUOTE_CHARS:
        raise _fail("LEDGER_EVIDENCE_INVALID")
    starts: list[int] = []
    cursor = 0
    while True:
        start = source.find(quote, cursor)
        if start < 0:
            break
        starts.append(start)
        cursor = start + 1
    if not starts:
        raise _fail("LEDGER_EVIDENCE_MISSING")
    if len(starts) != 1:
        raise _fail("LEDGER_EVIDENCE_AMBIGUOUS")
    return starts[0], starts[0] + len(quote)


def _number_boundary(text: str, start: int, end: int) -> None:
    for index in (start - 1, end):
        if not 0 <= index < len(text):
            continue
        char = text[index]
        if char.isalnum() or char in _SIGN_CHARS or unicodedata.category(char) == "Pd":
            raise _fail("LEDGER_NUMBER_UNGROUNDED")
        if char not in ".,":
            continue
        neighbour_index = index + 1 if index == end else index - 1
        neighbour = text[neighbour_index] if 0 <= neighbour_index < len(text) else ""
        if neighbour.isdigit():
            raise _fail("LEDGER_NUMBER_UNGROUNDED")


def _parse_number_quote(quote: str) -> Decimal:
    if type(quote) is not str or not quote or len(quote) > MAX_QUOTE_CHARS:
        raise _fail("LEDGER_NUMBER_INVALID")
    if _GROUPED_DOT.fullmatch(quote):
        normalized = quote.replace(".", "").replace(",", ".")
    elif _GROUPED_COMMA.fullmatch(quote):
        if _AMBIGUOUS_COMMA_GROUP.fullmatch(quote) and "." not in quote:
            raise _fail("LEDGER_NUMBER_AMBIGUOUS")
        normalized = quote.replace(",", "")
    elif _NUMBER_TOKEN.fullmatch(quote):
        if _AMBIGUOUS_DOT_DECIMAL.fullmatch(quote) or _AMBIGUOUS_COMMA_DECIMAL.fullmatch(quote):
            raise _fail("LEDGER_NUMBER_AMBIGUOUS")
        if "," in quote:
            normalized = quote.replace(",", ".")
        else:
            normalized = quote
    else:
        raise _fail("LEDGER_NUMBER_INVALID")
    try:
        return _validate_decimal(Decimal(normalized))
    except InvalidOperation:
        raise _fail("LEDGER_NUMBER_INVALID") from None


def _parse_scalar_quote(quote: str, field: ScalarField, parser_version: ParserVersion) -> Decimal:
    if parser_version == 2 and field == "surcharge_percent" and quote.endswith("%"):
        return _parse_number_quote(quote[:-1])
    return _parse_number_quote(quote)


def _strict_number_boundary(source: str, start: int, end: int, *, percent: bool) -> None:
    if start > 0 and source[start - 1] in ".,":
        raise _fail("LEDGER_NUMBER_UNGROUNDED")
    token_end = end
    if percent and not source[start:end].endswith("%") and source[end : end + 1] == "%":
        token_end += 1
    _number_boundary(source, start, token_end)
    for index, direction in ((start - 1, -1), (token_end, 1)):
        while 0 <= index < len(source) and (
            source[index] == "\t" or unicodedata.category(source[index]) == "Zs"
        ):
            index += direction
        if 0 <= index < len(source) and source[index] in _PERCENT_GLYPHS:
            raise _fail("LEDGER_NUMBER_UNGROUNDED")


def _value_quote_span(
    source: str,
    evidence: str,
    value_quote: str,
    *,
    numeric: bool,
    percent: bool = False,
    parser_version: ParserVersion = CURRENT_PARSER_VERSION,
) -> tuple[int, int]:
    evidence_start, evidence_end = _occurrence(source, evidence)
    if type(value_quote) is not str or not value_quote:
        raise _fail("LEDGER_NUMBER_INVALID")
    starts: list[int] = []
    cursor = 0
    while True:
        found = evidence.find(value_quote, cursor)
        if found < 0:
            break
        if numeric:
            starts.append(found)
        else:
            before = evidence[found - 1] if found else ""
            after_index = found + len(value_quote)
            after = evidence[after_index] if after_index < len(evidence) else ""
            if not before.isalnum() and not after.isalnum():
                starts.append(found)
        cursor = found + 1
    if len(starts) != 1:
        raise _fail("LEDGER_NUMBER_AMBIGUOUS" if starts else "LEDGER_NUMBER_MISSING")
    start = evidence_start + starts[0]
    end = start + len(value_quote)
    if end > evidence_end or source[start:end] != value_quote:
        raise _fail("LEDGER_NUMBER_UNGROUNDED")
    if numeric:
        if parser_version == 1:
            _number_boundary(source, start, end)
        else:
            _strict_number_boundary(source, start, end, percent=percent)
    return start, end


def _amount_id(source: str, revision: int, event_index: int) -> str:
    digest = hashlib.sha256(
        f"{source_digest(source)}:{revision}:{event_index}".encode("ascii")
    ).hexdigest()[:32]
    return f"amt_{digest}"


def _validate_event(event: LedgerEvent, parser_version: ParserVersion) -> LedgerEvent:
    kind = getattr(event, "kind", None)
    if type(kind) is not str:
        raise _fail("LEDGER_EVENT_INVALID")
    expected = {
        "add": AddAmount,
        "replace_amount": ReplaceAmount,
        "cancel": CancelAmount,
        "set_scalar": SetScalar,
    }.get(kind)
    if expected is None or type(event) is not expected:
        raise _fail("LEDGER_EVENT_INVALID")
    try:
        validated = expected.model_validate(event)
    except (ValidationError, ValueError, TypeError):
        raise _fail("LEDGER_EVENT_INVALID") from None
    if isinstance(validated, (AddAmount, ReplaceAmount)):
        _validate_decimal(validated.amount)
        _parse_number_quote(validated.value_quote)
    elif isinstance(validated, SetScalar):
        if validated.field in {"surcharge_percent", "budget"}:
            if type(validated.value) is not Decimal:
                raise _fail("LEDGER_VALUE_INVALID")
            _validate_decimal(validated.value)
            _parse_scalar_quote(validated.value_quote, validated.field, parser_version)
        elif type(validated.value) is not str or not validated.value:
            raise _fail("LEDGER_VALUE_INVALID")
    return validated


def _effective_values(records: tuple[LedgerAmount, ...], scalars: _ScalarValues) -> LedgerEffective:
    return LedgerEffective(
        active_amounts=tuple(record.amount for record in records if record.active),
        surcharge_percent=scalars["surcharge_percent"],
        budget=scalars["budget"],
        recipient=scalars["recipient"],
        project=scalars["project"],
        unit=scalars["unit"],
    )


def _structural_validate(revision: LedgerRevision) -> LedgerRevision:
    revision = _revalidate(LedgerRevision, revision, "LEDGER_STATE_INVALID")  # type: ignore[assignment]
    if source_digest(revision.source_text) != revision.source_sha256:
        raise _fail("LEDGER_STATE_INVALID")
    ids: set[str] = set()
    labels: set[str] = set()
    for record in revision.records:
        if record.amount_id in ids or record.label in labels:
            raise _fail("LEDGER_STATE_INVALID")
        ids.add(record.amount_id)
        labels.add(record.label)
        _validate_decimal(record.amount)
    scalars: _ScalarValues = {
        "surcharge_percent": revision.surcharge_percent,
        "budget": revision.budget,
        "recipient": revision.recipient,
        "project": revision.project,
        "unit": revision.unit,
    }
    if _effective_values(revision.records, scalars).active_amounts != tuple(
        record.amount for record in revision.records if record.active
    ):
        raise _fail("LEDGER_STATE_INVALID")
    record_labels = {record.amount_id: record.label for record in revision.records}
    previous_anchor = -1
    for event in revision.events:
        checked = _validate_event(event, revision.parser_version)
        target_label: str | None
        if isinstance(checked, AddAmount):
            target_label = checked.label
        elif isinstance(checked, (ReplaceAmount, CancelAmount)):
            target_label = record_labels.get(checked.amount_id)
            if target_label is None:
                raise _fail("LEDGER_STATE_INVALID")
        else:
            target_label = None
        anchor, _ = _check_event_source(
            revision.source_text, checked, target_label, revision.parser_version
        )
        if anchor <= previous_anchor:
            raise _fail("LEDGER_STATE_INVALID")
        previous_anchor = anchor
    return revision


def _base_values(
    base: LedgerRevision | None,
) -> tuple[list[LedgerAmount], _ScalarValues]:
    if base is None:
        return [], {
            "surcharge_percent": None,
            "budget": None,
            "recipient": None,
            "project": None,
            "unit": None,
        }
    base = _structural_validate(base)
    if base.parent_revision is not None and base.parent_revision >= base.revision:
        raise _fail("LEDGER_STATE_INVALID")
    return list(base.records), {
        "surcharge_percent": base.surcharge_percent,
        "budget": base.budget,
        "recipient": base.recipient,
        "project": base.project,
        "unit": base.unit,
    }


def _check_event_source(
    source: str,
    event: LedgerEvent,
    target_label: str | None,
    parser_version: ParserVersion,
) -> tuple[int, int]:
    target = target_label
    if isinstance(event, AddAmount):
        target = event.label
    elif isinstance(event, SetScalar):
        target = None
    evidence_start, evidence_end = _occurrence(source, event.evidence.quote)
    anchor = evidence_start
    target_start: int | None = None
    if target is not None:
        target_starts: list[int] = []
        cursor = 0
        while True:
            found = event.evidence.quote.find(target, cursor)
            if found < 0:
                break
            before = event.evidence.quote[found - 1] if found else ""
            after_index = found + len(target)
            after = (
                event.evidence.quote[after_index] if after_index < len(event.evidence.quote) else ""
            )
            if not before.isalnum() and not after.isalnum():
                target_starts.append(found)
            cursor = found + 1
        if len(target_starts) != 1:
            raise _fail("LEDGER_TARGET_EVIDENCE_INVALID")
        target_start = evidence_start + target_starts[0]
    if isinstance(event, (AddAmount, ReplaceAmount)):
        value_start, value_end = _value_quote_span(
            source,
            event.evidence.quote,
            event.value_quote,
            numeric=True,
            parser_version=parser_version,
        )
        value = _parse_number_quote(event.value_quote)
        if value != event.amount:
            raise _fail("LEDGER_VALUE_INVALID")
        if not evidence_start <= value_start < value_end <= evidence_end:
            raise _fail("LEDGER_NUMBER_UNGROUNDED")
        anchor = value_start
    elif isinstance(event, SetScalar):
        value_start, value_end = _value_quote_span(
            source,
            event.evidence.quote,
            event.value_quote,
            numeric=event.field in {"surcharge_percent", "budget"},
            percent=event.field == "surcharge_percent",
            parser_version=parser_version,
        )
        if event.field in {"surcharge_percent", "budget"}:
            value = _parse_scalar_quote(event.value_quote, event.field, parser_version)
            if type(event.value) is not Decimal or value != event.value:
                raise _fail("LEDGER_VALUE_INVALID")
        elif event.value != event.value_quote:
            raise _fail("LEDGER_VALUE_INVALID")
        if not evidence_start <= value_start < value_end <= evidence_end:
            raise _fail("LEDGER_NUMBER_UNGROUNDED")
        anchor = value_start
    elif target_start is not None:
        anchor = target_start
    return anchor, evidence_end


def build_revision(
    source: str,
    events: tuple[LedgerEvent, ...] | list[LedgerEvent],
    base: LedgerRevision | None = None,
) -> LedgerRevision:
    return _build_revision(source, events, base, parser_version=CURRENT_PARSER_VERSION)


def _build_revision(
    source: str,
    events: tuple[LedgerEvent, ...] | list[LedgerEvent],
    base: LedgerRevision | None,
    *,
    parser_version: ParserVersion,
    origin: Literal["model", "user"] = "model",
) -> LedgerRevision:
    digest = source_digest(source)
    if base is not None:
        base = _structural_validate(base)
        if parser_version < base.parser_version:
            raise _fail("LEDGER_STATE_INVALID")
        if base.revision >= MAX_REVISIONS:
            raise _fail("LEDGER_REVISION_LIMIT")
        revision = base.revision + 1
        parent_revision = base.revision
    else:
        revision = 1
        parent_revision = None
    if type(events) not in (tuple, list) or not events or len(events) > MAX_EVENTS:
        raise _fail("LEDGER_EVENTS_INVALID")
    records, scalars = _base_values(base)
    labels = {record.label for record in records}
    normalized: list[LedgerEvent] = []
    previous_anchor = -1
    for index, item in enumerate(events):
        event = _validate_event(item, parser_version)
        target_label: str | None = None
        if isinstance(event, AddAmount):
            if event.label in labels:
                raise _fail("LEDGER_DUPLICATE_LABEL")
            target_label = event.label
        elif isinstance(event, (ReplaceAmount, CancelAmount)):
            found = next(
                (record for record in records if record.amount_id == event.amount_id), None
            )
            if found is None:
                raise _fail("LEDGER_TARGET_MISSING")
            if not found.active:
                raise _fail("LEDGER_NO_REACTIVATION")
            target_label = found.label
        anchor, _ = _check_event_source(source, event, target_label, parser_version)
        if anchor <= previous_anchor:
            raise _fail("LEDGER_EVENT_ORDER_INVALID")
        previous_anchor = anchor
        if isinstance(event, AddAmount):
            records.append(
                LedgerAmount(
                    amount_id=_amount_id(source, revision, index),
                    label=event.label,
                    amount=event.amount,
                    source_quote=event.evidence.quote,
                    active=True,
                )
            )
            labels.add(event.label)
        elif isinstance(event, ReplaceAmount):
            found = next(record for record in records if record.amount_id == event.amount_id)
            records[records.index(found)] = found.model_copy(
                update={"amount": event.amount, "source_quote": event.evidence.quote}
            )
        elif isinstance(event, CancelAmount):
            found = next(record for record in records if record.amount_id == event.amount_id)
            records[records.index(found)] = found.model_copy(update={"active": False})
        elif event.field in {"surcharge_percent", "budget"}:
            if type(event.value) is not Decimal:
                raise _fail("LEDGER_VALUE_INVALID")
            scalars[event.field] = event.value
        else:
            if type(event.value) is not str:
                raise _fail("LEDGER_VALUE_INVALID")
            scalars[event.field] = event.value
        normalized.append(event)
    records_tuple = tuple(records)
    effective = _effective_values(records_tuple, scalars)
    return LedgerRevision(
        parser_version=parser_version,
        origin=origin,
        revision=revision,
        parent_revision=parent_revision,
        source_text=source,
        source_sha256=digest,
        events=tuple(normalized),
        records=records_tuple,
        recipient=effective.recipient,
        project=effective.project,
        unit=effective.unit,
        surcharge_percent=effective.surcharge_percent,
        budget=effective.budget,
    )


def validate_revision(
    revision: LedgerRevision,
    base: LedgerRevision | None = None,
    *,
    require_replay: bool = True,
) -> LedgerRevision:
    checked = _structural_validate(revision)
    if checked.parent_revision is None:
        if base is not None:
            raise _fail("LEDGER_STATE_INVALID")
    elif base is None:
        if require_replay:
            raise _fail("LEDGER_STATE_BASE_REQUIRED")
        return checked
    else:
        base = _structural_validate(base)
        if base.revision != checked.parent_revision:
            raise _fail("LEDGER_STATE_STALE")
    if require_replay:
        replay = _build_revision(
            checked.source_text,
            checked.events,
            base,
            parser_version=checked.parser_version,
            origin=checked.origin,
        )
        if replay != checked:
            raise _fail("LEDGER_STATE_INVALID")
    return checked


def append_revision(ledger: PrivateLedger | None, revision: LedgerRevision) -> PrivateLedger:
    revision = _revalidate(LedgerRevision, revision, "LEDGER_STATE_INVALID")  # type: ignore[assignment]
    if ledger is None:
        if revision.revision != 1 or revision.parent_revision is not None:
            raise _fail("LEDGER_STALE")
        validate_revision(revision)
        return PrivateLedger(revisions=(revision,))
    ledger = _revalidate(PrivateLedger, ledger, "LEDGER_STATE_INVALID")  # type: ignore[assignment]
    if not ledger.revisions:
        raise _fail("LEDGER_STATE_INVALID")
    previous: LedgerRevision | None = None
    for stored in ledger.revisions:
        validate_revision(stored, previous)
        previous = stored
    current = ledger.revisions[-1]
    if revision.parent_revision != current.revision or revision.revision != current.revision + 1:
        raise _fail("LEDGER_STALE")
    if len(ledger.revisions) >= MAX_REVISIONS:
        raise _fail("LEDGER_REVISION_LIMIT")
    validate_revision(revision, current)
    return PrivateLedger(revisions=(*ledger.revisions, revision))


def current_revision(ledger: PrivateLedger | None) -> LedgerRevision | None:
    if ledger is None:
        return None
    ledger = _revalidate(PrivateLedger, ledger, "LEDGER_STATE_INVALID")  # type: ignore[assignment]
    return ledger.revisions[-1] if ledger.revisions else None


def _raw_event_anchor(source: str, item: RawLedgerEvent) -> int:
    evidence_start, _ = _occurrence(source, item.evidence.quote)
    if isinstance(item, RawSetScalarEvent):
        return _value_quote_span(
            source,
            item.evidence.quote,
            item.value_quote,
            numeric=item.field in {"surcharge_percent", "budget"},
            percent=item.field == "surcharge_percent",
        )[0]
    if isinstance(item, (RawAddEvent, RawReplaceEvent)):
        return _value_quote_span(source, item.evidence.quote, item.value_quote, numeric=True)[0]
    if isinstance(item, RawCancelEvent):
        return _value_quote_span(source, item.evidence.quote, item.label, numeric=False)[0]
    return evidence_start


def _canonical_raw_events(
    source: str, events: tuple[RawLedgerEvent, ...]
) -> tuple[RawLedgerEvent, ...]:
    ranked = [(_raw_event_anchor(source, item), index, item) for index, item in enumerate(events)]
    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    for previous, current in zip(ranked, ranked[1:], strict=False):
        if previous[0] == current[0]:
            raise _fail("LEDGER_EVENT_ANCHOR_AMBIGUOUS")
    return tuple(item for _, _, item in ranked)


def _resolve_target(
    item: RawReplaceEvent | RawCancelEvent,
    labels: dict[str, str],
) -> str:
    target = labels.get(item.label)
    if target is None:
        raise _fail("LEDGER_TARGET_MISSING")
    return target


def normalize_raw_proposal(
    raw: object, source: str, base: LedgerRevision | None = None
) -> LedgerRevision | NoLedgerChanges:
    source_digest(source)
    try:
        proposal = RawLedgerProposal.model_validate(raw)
    except (ValidationError, ValueError, TypeError):
        raise _fail("LEDGER_PROPOSAL_INVALID") from None
    if base is not None:
        _structural_validate(base)
    if not proposal.events:
        return NoLedgerChanges.NO_CHANGES
    if base is not None:
        if base.revision >= MAX_REVISIONS:
            raise _fail("LEDGER_REVISION_LIMIT")
        next_revision = base.revision + 1
        records = list(base.records)
    else:
        next_revision = 1
        records = []
    labels: dict[str, str] = {}
    ids: set[str] = set()
    active: dict[str, bool] = {}
    for record in records:
        if record.label in labels or record.amount_id in ids:
            raise _fail("LEDGER_DUPLICATE_LABEL")
        labels[record.label] = record.amount_id
        ids.add(record.amount_id)
        active[record.amount_id] = record.active
    normalized: list[LedgerEvent] = []
    ordered_events = _canonical_raw_events(source, proposal.events)
    for index, item in enumerate(ordered_events):
        if isinstance(item, RawAddEvent):
            if item.label in labels:
                raise _fail("LEDGER_DUPLICATE_LABEL")
            amount = _parse_number_quote(item.value_quote)
            event = AddAmount(
                label=item.label,
                value_quote=item.value_quote,
                amount=amount,
                evidence=item.evidence,
            )
            labels[item.label] = _amount_id(source, next_revision, index)
            ids.add(labels[item.label])
            active[labels[item.label]] = True
            normalized.append(event)
        elif isinstance(item, RawReplaceEvent):
            target = _resolve_target(item, labels)
            if not active.get(target, False):
                raise _fail("LEDGER_NO_REACTIVATION")
            amount = _parse_number_quote(item.value_quote)
            normalized.append(
                ReplaceAmount(
                    amount_id=target,
                    value_quote=item.value_quote,
                    amount=amount,
                    evidence=item.evidence,
                )
            )
        elif isinstance(item, RawCancelEvent):
            target = _resolve_target(item, labels)
            if not active.get(target, False):
                raise _fail("LEDGER_NO_REACTIVATION")
            active[target] = False
            normalized.append(CancelAmount(amount_id=target, evidence=item.evidence))
        else:
            assert isinstance(item, RawSetScalarEvent)
            if item.field in {"surcharge_percent", "budget"}:
                value: Decimal | str = _parse_scalar_quote(
                    item.value_quote, item.field, CURRENT_PARSER_VERSION
                )
            else:
                value = item.value_quote
            normalized.append(
                SetScalar(
                    field=item.field,
                    value_quote=item.value_quote,
                    value=value,
                    evidence=item.evidence,
                )
            )
    return build_revision(source, normalized, base)


def public_input_instructions() -> tuple[dict[str, str], ...]:
    return (
        {
            "name": "active_amounts",
            "type": "numbers",
            "instruction": "Use active private amounts in source order.",
        },
        {
            "name": "surcharge_percent",
            "type": "number",
            "instruction": "Use the active surcharge percent.",
        },
        {"name": "budget", "type": "number", "instruction": "Use the active budget."},
        {"name": "recipient", "type": "text", "instruction": "Use the active recipient."},
        {"name": "project", "type": "text", "instruction": "Use the active project."},
        {"name": "unit", "type": "text", "instruction": "Use the active amount unit."},
    )
