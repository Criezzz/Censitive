import hashlib
from dataclasses import dataclass

from privacy_gateway.domain.detection import Detection
from privacy_gateway.domain.redaction import Binding, validate_tokens
from privacy_gateway.domain.scoped import compile_source
from privacy_gateway.domain.state import DecisionStamp, State, dirty_sources


@dataclass(frozen=True)
class CompiledSource:
    stamp: DecisionStamp
    text: str
    detections: tuple[Detection, ...]


@dataclass(frozen=True)
class Compilation:
    sources: tuple[CompiledSource, ...]
    recomputed: int
    reused: int
    full_fallback: bool


def compile_state(
    state: State,
    literals: tuple[str, ...],
    vault: dict[str, Binding],
    previous: tuple[CompiledSource, ...] = (),
    *,
    resolver_signature: str,
    full: bool = False,
) -> Compilation:
    signature = hashlib.sha256(repr((resolver_signature, literals)).encode()).hexdigest()
    stamps = tuple(item.stamp for item in previous)
    dirty = set(dirty_sources(state, stamps, signature))
    known = {item.stamp.source_id: item for item in previous}
    if full:
        dirty = {source.id for source in state.sources}
    items = []
    reused = 0
    for source in state.sources:
        if source.id not in dirty:
            validate_tokens(known[source.id].text, vault)
            items.append(known[source.id])
            reused += 1
            continue
        text, detections = compile_source(state, source, literals, vault)
        items.append(
            CompiledSource(
                DecisionStamp(
                    source.id,
                    source.revision,
                    source.digest,
                    state.protection_epoch,
                    signature,
                    state.protection_digest,
                ),
                text,
                detections,
            )
        )
    return Compilation(
        tuple(items), len(items) - reused, reused, bool(previous) and bool(items) and reused == 0
    )
