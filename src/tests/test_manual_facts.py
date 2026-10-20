import json
from dataclasses import replace

import pytest
from privacy_gateway.domain.identity import DraftCreate, DraftIdentity, DraftOccurrence, Occurrence
from privacy_gateway.domain.incremental import compile_state
from privacy_gateway.domain.manual_facts import (
    CreateFact,
    DraftFactMark,
    DraftFacts,
    apply_draft_facts,
    apply_manual_fact,
    verify_draft_facts,
)
from privacy_gateway.domain.redaction import Binding, PrivacyError
from privacy_gateway.domain.semantic import Category
from privacy_gateway.domain.state import Source, State, unique_span
from pydantic import TypeAdapter, ValidationError


def occurrence(source: Source, quote: str, *, start: int | None = None) -> Occurrence:
    offset = source.text.index(quote) if start is None else start
    return Occurrence(
        source_id=source.id,
        source_revision=source.revision,
        source_digest=source.digest,
        start=offset,
        end=offset + len(quote),
        quote=quote,
    )


def draft(state: State, source: Source, quote: str) -> DraftFacts:
    evidence = occurrence(source, quote)
    return DraftFacts(
        expected_epoch=state.epoch,
        text_digest=source.digest,
        marks=(
            DraftFactMark(
                occurrence=DraftOccurrence(start=evidence.start, end=evidence.end, quote=quote),
                category="confidential",
            ),
        ),
    )


def test_manual_fact_protects_exact_unicode_occurrence_without_entity_propagation() -> None:
    base, source = State().add_source("🦊 Lan nghỉ việc; Lan viết sách.")
    base, entity = base.add_entity(unique_span(source, "Lan viết sách"))
    result = apply_manual_fact(
        base,
        CreateFact(
            expected_epoch=base.epoch, evidence=occurrence(source, "Lan"), category="confidential"
        ),
    )
    assert result.fact.scope == "fact" and result.fact.entity_id is None
    assert result.state.entities == (entity,) and not base.facts
    compiled = compile_state(result.state, (), {}, resolver_signature="test")
    assert compiled.sources[0].text.endswith("nghỉ việc; Lan viết sách.")
    assert not compiled.sources[0].text.startswith("🦊 Lan")
    assert result.state.sources == base.sources


@pytest.mark.parametrize("tamper", ["epoch", "revision", "digest", "quote", "offset"])
def test_manual_fact_rejects_stale_or_inexact_evidence_without_mutation(tamper: str) -> None:
    base, source = State().add_source("secret public")
    evidence = occurrence(source, "secret")
    epoch = base.epoch
    if tamper == "epoch":
        epoch += 1
    elif tamper == "revision":
        evidence = evidence.model_copy(update={"source_revision": 2})
    elif tamper == "digest":
        evidence = evidence.model_copy(update={"source_digest": "0" * 64})
    elif tamper == "quote":
        evidence = evidence.model_copy(update={"quote": "public"})
    else:
        evidence = evidence.model_copy(update={"start": 1})
    with pytest.raises(PrivacyError, match="STATE_STALE|STATE_EVIDENCE_MISMATCH"):
        apply_manual_fact(
            base, CreateFact(expected_epoch=epoch, evidence=evidence, category="confidential")
        )
    assert not base.facts


def test_draft_json_arrays_are_immutable_and_nested_fields_remain_strict() -> None:
    base = State()
    _, source = base.add_source("secret")
    value = draft(base, source, "secret").model_dump(mode="json")
    adapter = TypeAdapter(DraftFacts)
    parsed = adapter.validate_python(json.loads(json.dumps(value)))
    assert parsed == adapter.validate_json(json.dumps(value))
    assert type(parsed.marks) is tuple
    for bad in (
        {**value, "expected_epoch": True},
        {**value, "marks": []},
        {**value, "marks": value["marks"] * 17},
        {**value, "allow": True},
        {
            **value,
            "marks": [
                {
                    "occurrence": {"start": False, "end": 6, "quote": "secret"},
                    "category": "confidential",
                }
            ],
        },
        {
            **value,
            "marks": [
                {"occurrence": {"start": 0, "end": 6, "quote": "secret"}, "category": "public"}
            ],
        },
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)


def test_draft_facts_and_identity_share_one_source_without_mutating_base() -> None:
    base = State()
    staged, source = base.add_source("Lan has a private salary; other Lan is public.")
    marks = draft(base, source, "private salary")
    identity = DraftIdentity(
        expected_epoch=0,
        text_digest=source.digest,
        actions=(
            DraftCreate(occurrence=DraftOccurrence(start=0, end=3, quote="Lan"), protect=False),
        ),
    )
    result = apply_draft_facts(base, staged, source, marks, draft_identity=identity)
    verify_draft_facts(base, result, source, marks)
    assert len(result.sources) == len(result.facts) == len(result.entities) == 1
    assert not staged.facts and not base.sources
    assert result.facts[0].scope == "fact" and result.facts[0].entity_id is None


@pytest.mark.parametrize("tamper", ["extra_fact", "extra_source", "epoch", "digest"])
def test_draft_requires_exact_source_only_stage(tamper: str) -> None:
    base = State()
    staged, source = base.add_source("secret")
    marks = draft(base, source, "secret")
    if tamper == "extra_fact":
        staged, _ = staged.add_fact(unique_span(source, "secret"), "personal")
    elif tamper == "extra_source":
        staged, _ = staged.add_source("another")
    elif tamper == "epoch":
        marks = marks.model_copy(update={"expected_epoch": 1})
    else:
        marks = marks.model_copy(update={"text_digest": "0" * 64})
    with pytest.raises(PrivacyError, match="STATE_STALE"):
        apply_draft_facts(base, staged, source, marks)
    assert not base.facts


@pytest.mark.parametrize("tamper", ["missing", "category", "scope", "retired"])
def test_draft_postcondition_requires_exact_manual_policy(tamper: str) -> None:
    base = State()
    staged, source = base.add_source("secret")
    marks = draft(base, source, "secret")
    if tamper != "missing":
        staged = apply_draft_facts(base, staged, source, marks)
        fact = staged.facts[0]
        if tamper == "category":
            staged = replace(staged, facts=(replace(fact, category="personal"),))
        elif tamper == "scope":
            staged, entity = staged.add_entity(fact.assertion)
            staged = replace(staged, facts=(replace(fact, scope="entity", entity_id=entity.id),))
        else:
            staged, correction = staged.add_source("replacement")
            staged = staged.correct(
                fact.id, 1, staged.epoch, unique_span(correction, "replacement")
            )
    with pytest.raises(PrivacyError, match="STATE_FACT_NOT_APPLIED"):
        verify_draft_facts(base, staged, source, marks)


@pytest.mark.parametrize("reverse", [False, True])
def test_manual_broad_mark_never_weakens_overlapping_credential(reverse: bool) -> None:
    state, source = State().add_source("Private password: SYNTH_credential_741; public task.")
    marks: list[tuple[str, Category]] = [
        ("Private password: SYNTH_credential_741", "confidential"),
        ("SYNTH_credential_741", "credential"),
    ]
    if reverse:
        marks.reverse()
    for quote, category in marks:
        state = apply_manual_fact(
            state,
            CreateFact(
                expected_epoch=state.epoch, evidence=occurrence(source, quote), category=category
            ),
        ).state
    vault: dict[str, Binding] = {}
    compiled = compile_state(state, (), vault, resolver_signature="test")
    assert compiled.sources[0].text.endswith("; public task.")
    assert all(not b.restore_allowed for b in vault.values() if "SYNTH_credential_741" in b.value)
    assert "SYNTH_credential_741" in state.denied_literals
    state, _ = state.add_source("Repeat SYNTH_credential_741 only")
    later = compile_state(state, (), vault, compiled.sources, resolver_signature="test")
    assert "SYNTH_credential_741" not in later.sources[-1].text
    assert any(b.value == "SYNTH_credential_741" and not b.restore_allowed for b in vault.values())


def test_draft_failure_in_second_mark_or_cap_does_not_partially_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import privacy_gateway.domain.state as module

    base = State()
    staged, source = base.add_source("secret")
    marks = draft(base, source, "secret")
    bad = DraftFactMark(
        occurrence=DraftOccurrence(start=0, end=6, quote="wrong!"), category="personal"
    )
    with pytest.raises(PrivacyError, match="STATE_EVIDENCE_MISMATCH"):
        apply_draft_facts(
            base, staged, source, marks.model_copy(update={"marks": (*marks.marks, bad)})
        )
    monkeypatch.setattr(module, "MAX_FACTS", 0)
    with pytest.raises(PrivacyError, match="STATE_LIMIT"):
        apply_draft_facts(base, staged, source, marks)
    assert not staged.facts and not base.facts


@pytest.mark.parametrize(
    "forgery",
    [
        "bool_epoch",
        "float_epoch",
        "kind",
        "revision_bool",
        "start_bool",
        "evidence_none",
        "evidence_dict",
        "category",
        "missing",
    ],
)
def test_manual_fact_revalidates_forged_pydantic_instances(forgery: str) -> None:
    base, source = State().add_source("secret")
    evidence = occurrence(source, "secret")
    command = CreateFact(expected_epoch=base.epoch, evidence=evidence, category="confidential")
    if forgery == "bool_epoch":
        command = command.model_copy(update={"expected_epoch": True})
    elif forgery == "float_epoch":
        command = command.model_copy(update={"expected_epoch": 1.0})
    elif forgery == "kind":
        command = command.model_copy(update={"kind": "allow"})
    elif forgery in {"revision_bool", "start_bool"}:
        forged = evidence.model_copy(
            update={"source_revision": True} if forgery == "revision_bool" else {"start": False}
        )
        command = command.model_copy(update={"evidence": forged})
    elif forgery == "category":
        command = command.model_copy(update={"category": "public"})
    elif forgery == "missing":
        command = CreateFact.model_construct()
    else:
        command = command.model_copy(
            update={"evidence": None if forgery == "evidence_none" else {}}
        )
    with pytest.raises(PrivacyError, match="^STATE_INVALID$"):
        apply_manual_fact(base, command)
    assert not base.facts


@pytest.mark.parametrize(
    "forgery",
    [
        "bool_epoch",
        "float_epoch",
        "empty",
        "seventeen",
        "list",
        "none",
        "mark_none",
        "occurrence_none",
        "occurrence_dict",
        "offset_bool",
        "category",
        "missing",
    ],
)
@pytest.mark.parametrize("verify", [False, True])
def test_draft_apply_and_verifier_revalidate_every_nested_field(forgery: str, verify: bool) -> None:
    base = State()
    staged, source = base.add_source("secret")
    intent = draft(base, source, "secret")
    first = intent.marks[0]
    if forgery in {"bool_epoch", "float_epoch"}:
        intent = intent.model_copy(
            update={"expected_epoch": False if forgery == "bool_epoch" else 0.0}
        )
    elif forgery in {"empty", "seventeen", "list", "none", "mark_none"}:
        altered = {
            "empty": (),
            "seventeen": intent.marks * 17,
            "list": list(intent.marks),
            "none": None,
            "mark_none": (None,),
        }[forgery]
        intent = intent.model_copy(update={"marks": altered})
    elif forgery == "missing":
        intent = DraftFacts.model_construct()
    else:
        if forgery == "category":
            first = first.model_copy(update={"category": "allow"})
        else:
            altered_occurrence: object = (
                None
                if forgery == "occurrence_none"
                else {}
                if forgery == "occurrence_dict"
                else first.occurrence.model_copy(update={"start": False})
            )
            first = first.model_copy(update={"occurrence": altered_occurrence})
        intent = intent.model_copy(update={"marks": (first,)})
    with pytest.raises(PrivacyError, match="^STATE_INVALID$"):
        if verify:
            verify_draft_facts(base, staged, source, intent)
        else:
            apply_draft_facts(base, staged, source, intent)
    assert not staged.facts and not base.facts
