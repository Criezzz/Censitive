from decimal import Decimal

from pydantic import Field, field_validator

from privacy_gateway.domain.private_ledger import (
    AddAmount,
    LedgerEvent,
    LedgerEvidence,
    LedgerRevision,
    NoLedgerChanges,
    ReplaceAmount,
    ScalarField,
    SetScalar,
    StrictLedgerModel,
    _parse_number_quote,
    _parse_scalar_quote,
    build_revision,
    validate_revision,
)
from privacy_gateway.domain.redaction import PrivacyError


class StructuredAmount(StrictLedgerModel):
    label: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=64)

    @field_validator("label", "value")
    @classmethod
    def single_line(cls, value: str) -> str:
        if value != value.strip() or any(char in value for char in "\r\n"):
            raise ValueError("single-line form value required")
        return value


class StructuredLedgerValues(StrictLedgerModel):
    records: tuple[StructuredAmount, ...] = Field(default=(), max_length=24)
    surcharge_percent: str | None = Field(default=None, min_length=1, max_length=64)
    budget: str | None = Field(default=None, min_length=1, max_length=64)
    recipient: str | None = Field(default=None, min_length=1, max_length=256)
    project: str | None = Field(default=None, min_length=1, max_length=256)
    unit: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("records", mode="before")
    @classmethod
    def freeze_records(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @field_validator("surcharge_percent", "budget", "recipient", "project", "unit")
    @classmethod
    def single_line(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or any(char in value for char in "\r\n")):
            raise ValueError("single-line form value required")
        return value


def structured_source(values: StructuredLedgerValues) -> str:
    lines = ["User-entered structured ledger values:"]
    lines.extend(f"record: {item.label} = {item.value}" for item in values.records)
    for field in ("surcharge_percent", "budget", "recipient", "project", "unit"):
        value = getattr(values, field)
        if value is not None:
            lines.append(f"{field}: {value}")
    source = "\n".join(lines)
    if len(source) > 4_000:
        raise PrivacyError("LEDGER_SOURCE_INVALID")
    return source


def structured_revision(
    values: StructuredLedgerValues, base: LedgerRevision | None
) -> LedgerRevision | NoLedgerChanges:
    source = structured_source(values)
    existing = {record.label: record for record in base.records} if base else {}
    seen: set[str] = set()
    events: list[LedgerEvent] = []
    for item in values.records:
        if item.label in seen:
            raise PrivacyError("LEDGER_DUPLICATE_LABEL")
        seen.add(item.label)
        amount = _parse_number_quote(item.value)
        evidence = LedgerEvidence(quote=f"record: {item.label} = {item.value}")
        previous = existing.get(item.label)
        if previous is None:
            events.append(
                AddAmount(
                    label=item.label, value_quote=item.value, amount=amount, evidence=evidence
                )
            )
        elif not previous.active:
            raise PrivacyError("LEDGER_NO_REACTIVATION")
        elif amount != previous.amount:
            events.append(
                ReplaceAmount(
                    amount_id=previous.amount_id,
                    value_quote=item.value,
                    amount=amount,
                    evidence=evidence,
                )
            )
    fields: tuple[ScalarField, ...] = (
        "surcharge_percent",
        "budget",
        "recipient",
        "project",
        "unit",
    )
    for field in fields:
        quote = getattr(values, field)
        if quote is None:
            continue
        value: Decimal | str = (
            _parse_scalar_quote(quote, field, 2)
            if field in {"surcharge_percent", "budget"}
            else quote
        )
        if base is None or value != getattr(base, field):
            events.append(
                SetScalar(
                    field=field,
                    value_quote=quote,
                    value=value,
                    evidence=LedgerEvidence(quote=f"{field}: {quote}"),
                )
            )
    if not events:
        return NoLedgerChanges.NO_CHANGES
    revision = build_revision(source, events, base).model_copy(update={"origin": "user"})
    return validate_revision(revision, base)
