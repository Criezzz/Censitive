import secrets
from dataclasses import dataclass

from privacy_gateway.domain.detection import Detection, detect
from privacy_gateway.domain.models import Replacement, SourceSpan
from privacy_gateway.domain.redaction import Binding, PrivacyError, apply_spans
from privacy_gateway.domain.state import Source, State


@dataclass(frozen=True)
class Decision:
    span: SourceSpan
    targets: tuple[tuple[str, int], ...]
    category: str
    restore_allowed: bool


def decisions(state: State, source: Source, literals: tuple[str, ...]) -> tuple[Decision, ...]:
    state.validate()
    if state.source(source.id) != source:
        raise PrivacyError("STATE_SOURCE_MISMATCH")
    candidates = [
        Decision(
            SourceSpan(source.id, source.revision, d.start, d.end),
            (),
            d.category,
            d.restore_allowed,
        )
        for d in detect(source.text, literals, merge_overlaps=True)
    ]
    for fact in state.facts:
        if fact.assertion.message_id == source.id:
            candidates.append(
                Decision(
                    fact.assertion,
                    ((fact.id, fact.revision),),
                    "semantic_" + fact.category,
                    fact.restore_allowed,
                )
            )
    for link in state.links:
        if link.mention.message_id != source.id:
            continue
        if link.relation == "alias":
            for policy in state.facts:
                if policy.scope == "entity" and policy.entity_id == link.target:
                    candidates.append(
                        Decision(
                            link.mention,
                            ((policy.id, policy.revision),),
                            "semantic_" + policy.category,
                            policy.restore_allowed,
                        )
                    )
            continue
        fact = state.fact(link.target, link.target_revision)
        candidates.append(
            Decision(
                link.mention,
                ((fact.id, fact.revision),),
                "semantic_" + fact.category,
                fact.restore_allowed,
            )
        )
    denied = state.denied_literals
    for secret in denied:
        offset = 0
        while (start := source.text.find(secret, offset)) >= 0:
            candidates.append(
                Decision(
                    SourceSpan(source.id, source.revision, start, start + len(secret)),
                    (),
                    "credential",
                    False,
                )
            )
            if len(candidates) > 1024:
                raise PrivacyError("DETECTION_LIMIT")
            offset = start + 1
    merged: list[Decision] = []
    for item in sorted(candidates, key=lambda d: (d.span.start, d.span.end)):
        if merged and item.span.start < merged[-1].span.end:
            previous = merged.pop()
            merged.append(
                Decision(
                    SourceSpan(
                        source.id,
                        source.revision,
                        previous.span.start,
                        max(previous.span.end, item.span.end),
                    ),
                    tuple(sorted(set((*previous.targets, *item.targets)))),
                    item.category,
                    previous.restore_allowed and item.restore_allowed,
                )
            )
        else:
            merged.append(item)
    if len(merged) > 512:
        raise PrivacyError("DETECTION_LIMIT")
    return tuple(merged)


def compile_source(
    state: State, source: Source, literals: tuple[str, ...], vault: dict[str, Binding]
) -> tuple[str, tuple[Detection, ...]]:
    plan = decisions(state, source, literals)
    replacements = []
    rows = []
    for item in plan:
        value = state.quote(item.span)
        scope = f"{source.id}:{source.revision}:{item.span.start}:{item.span.end}:{item.targets}"
        binding = next(
            (
                b
                for b in vault.values()
                if b.scope == scope
                and b.value == value
                and b.restore_allowed == item.restore_allowed
            ),
            None,
        )
        if binding is None:
            if len(vault) >= 1024:
                raise PrivacyError("VAULT_LIMIT")
            token = f"[[PG_v1_{secrets.token_hex(16)}]]"
            while token in vault:
                token = f"[[PG_v1_{secrets.token_hex(16)}]]"
            binding = Binding(token, value, item.restore_allowed, scope)
            vault[token] = binding
        replacements.append(Replacement(item.span, binding.token))
        rows.append(Detection(item.span.start, item.span.end, item.category, item.restore_allowed))
    return apply_spans(
        source.text, replacements, message_id=source.id, message_revision=source.revision
    ), tuple(rows)
