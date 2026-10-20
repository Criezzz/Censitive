import asyncio
import hashlib
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from privacy_gateway.adapters.state_resolver import LocalStateResolver, Resolution
from privacy_gateway.api.app import create_app
from privacy_gateway.application.gateway import Gateway, Message, Session
from privacy_gateway.domain.identity import (
    CreateEntity,
    DraftCreate,
    DraftIdentity,
    DraftOccurrence,
    Occurrence,
    apply_draft_identity,
)
from privacy_gateway.domain.manual_facts import CreateFact, DraftFacts, apply_draft_facts
from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.semantic import SemanticReport
from privacy_gateway.domain.state import Source, State
from privacy_gateway.domain.state_proposals import StateDelta


class Capture:
    provider_id = "manual-fact-capture"
    model = "capture"
    serializer_version = "v1"
    timeout = 1.0

    def __init__(self) -> None:
        self.calls: list[bytes] = []
        self.failure = False

    async def complete(self, body: bytes) -> str:
        self.calls.append(body)
        if self.failure:
            raise RuntimeError("synthetic failure")
        return "ACK"


class Resolver:
    model = "manual-fact-contract"

    def __init__(self, *, ignore: bool = False) -> None:
        self.ignore = ignore
        self.started: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def resolve(
        self,
        base: State,
        text: str,
        literals: tuple[str, ...] = (),
        *,
        full: bool = False,
        draft_identity: DraftIdentity | None = None,
        draft_facts: DraftFacts | None = None,
    ) -> Resolution:
        staged, current = base.add_source(text)
        if draft_facts is not None and not self.ignore:
            staged = apply_draft_facts(
                base, staged, current, draft_facts, draft_identity=draft_identity
            )
        elif draft_identity is not None:
            staged = apply_draft_identity(base, staged, current, draft_identity)
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        return Resolution(
            staged,
            current,
            SemanticReport((), self.model, 0, len(staged.sources)),
            tuple(s.id for s in staged.sources),
            True,
            0,
            StateDelta(
                base,
                staged,
                tuple(f for f in staged.facts if f not in base.facts),
                tuple(link for link in staged.links if link not in base.links),
            ),
            (),
        )


def command(state: State, source: Source) -> CreateFact:
    start = source.text.index("Lan")
    return CreateFact(
        expected_epoch=state.epoch,
        category="confidential",
        evidence=Occurrence(
            source_id=source.id,
            source_revision=source.revision,
            source_digest=source.digest,
            start=start,
            end=start + 3,
            quote="Lan",
        ),
    )


def seeded() -> tuple[Gateway, Session, Source]:
    gateway = Gateway(Capture(), resolver=Resolver())
    session = gateway.create_session("owner")
    session.state, source = session.state.add_source("Lan is private; other Lan is public.")
    session.private_history = [Message("user", source.text, source_id=source.id)]
    session.provider_history = list(session.private_history)
    return gateway, session, source


def marks(text: str, epoch: int = 0) -> DraftFacts:
    start = text.index("Lan")
    return DraftFacts.model_validate(
        {
            "expected_epoch": epoch,
            "text_digest": hashlib.sha256(text.encode()).hexdigest(),
            "marks": [
                {
                    "occurrence": {"start": start, "end": start + 3, "quote": "Lan"},
                    "category": "confidential",
                }
            ],
        }
    )


def test_manual_fact_replay_preserves_new_preview_and_refilters_only_exact_history_occurrence() -> (
    None
):
    gateway, session, source = seeded()
    request = command(session.state, source)
    receipt = gateway.create_fact(session, request, "a" * 32)
    committed = session.state
    preview = asyncio.run(gateway.prepare_message(session, "Public next turn.", ()))
    version = session.version
    assert gateway.create_fact(session, request, "a" * 32) is receipt
    assert session.prepared is preview and session.version == version and session.state is committed
    assert receipt.fact_id == committed.facts[0].id and receipt.fact_revision == 1
    history = json.loads(preview.body)["messages"][0]["content"]
    assert not history.startswith("Lan") and "other Lan is public." in history
    assert asyncio.run(gateway.send(session, preview.id)) == "ACK"
    assert len(session.commands) == 1


@pytest.mark.parametrize("prior", ["identity", "correction", "manual"])
def test_manual_fact_shared_ledger_conflict_invalidates_approval(prior: str) -> None:
    gateway, session, source = seeded()
    old_request = command(session.state, source)
    if prior == "identity":
        gateway.identity_command(
            session,
            CreateEntity(
                expected_epoch=session.state.epoch, evidence=old_request.evidence, protect=False
            ),
            "a" * 32,
        )
    elif prior == "correction":
        receipt = gateway.create_fact(session, old_request, "b" * 32)
        gateway.correct_fact(
            session, receipt.fact_id, 1, session.state.epoch, "Corrected private name", "a" * 32
        )
    else:
        gateway.create_fact(session, old_request, "a" * 32)
    preview = asyncio.run(gateway.prepare_message(session, "public", ()))
    before = session.state
    with pytest.raises(PrivacyError, match="STATE_COMMAND_CONFLICT"):
        gateway.create_fact(session, command(session.state, source), "a" * 32)
    assert session.prepared is None and session.state is before
    with pytest.raises(PrivacyError):
        asyncio.run(gateway.send(session, preview.id))


def test_manual_fact_stale_and_full_ledger_invalidate_without_committing() -> None:
    gateway, session, source = seeded()
    old_request = command(session.state, source)
    receipt = gateway.create_fact(session, old_request, "a" * 32)
    for expected_error in ("STATE_STALE", "STATE_COMMAND_LIMIT"):
        asyncio.run(gateway.prepare_message(session, "public", ()))
        before = session.state
        if expected_error == "STATE_COMMAND_LIMIT":
            session.commands.update({f"{n:032x}": receipt for n in range(127)})
        with pytest.raises(PrivacyError, match=expected_error):
            gateway.create_fact(session, old_request, "c" * 32)
        assert session.state is before and session.prepared is None


@pytest.mark.parametrize("failure", [False, True])
def test_draft_manual_facts_commit_only_after_successful_send(failure: bool) -> None:
    sink = Capture()
    sink.failure = failure
    gateway = Gateway(sink, resolver=Resolver())
    session = gateway.create_session("owner")
    text = "Lan is private; other Lan is public."
    preview = asyncio.run(gateway.prepare_message(session, text, (), draft_facts=marks(text)))
    assert not session.state.sources and not sink.calls
    assert preview.sanitized.endswith("other Lan is public.")
    if failure:
        with pytest.raises(PrivacyError, match="PROVIDER_FAILED"):
            asyncio.run(gateway.send(session, preview.id))
        assert not session.state.sources and not session.private_history
    else:
        asyncio.run(gateway.send(session, preview.id))
        assert len(session.state.sources) == len(session.state.facts) == 1


def test_gateway_postcondition_blocks_resolver_ignoring_manual_draft() -> None:
    gateway = Gateway(Capture(), resolver=Resolver(ignore=True))
    session = gateway.create_session("owner")
    with pytest.raises(PrivacyError, match="STATE_FACT_NOT_APPLIED"):
        asyncio.run(gateway.prepare_message(session, "Lan", (), draft_facts=marks("Lan")))
    assert session.prepared is None and session.state == State()


def test_committed_manual_fact_during_prepare_invalidates_waiting_result() -> None:
    async def scenario() -> None:
        gateway, session, source = seeded()
        resolver = gateway.resolver
        assert isinstance(resolver, Resolver)
        resolver.started, resolver.release = asyncio.Event(), asyncio.Event()
        waiting = asyncio.create_task(gateway.prepare_message(session, "public", ()))
        await resolver.started.wait()
        gateway.create_fact(session, command(session.state, source), "a" * 32)
        committed = session.state
        resolver.release.set()
        with pytest.raises(PrivacyError, match="STALE_PREVIEW"):
            await waiting
        assert session.state is committed and session.prepared is None

    asyncio.run(scenario())


def api_session(client: TestClient, gateway: Gateway) -> tuple[Session, dict[str, str], Source]:
    csrf = client.get("/api/bootstrap").json()["csrf"]
    headers = {"x-csrf-token": csrf}
    sid = client.post("/api/sessions", json={}, headers=headers).json()["id"]
    session = gateway.sessions[sid]
    session.state, source = session.state.add_source("Lan is private; other Lan is public.")
    return session, headers, source


def test_manual_fact_api_owner_csrf_exact_replay_and_scope() -> None:
    gateway = Gateway(Capture(), resolver=Resolver())
    app = create_app(gateway)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        session, headers, source = api_session(client, gateway)
        url = f"/api/sessions/{session.id}/facts/create"
        body = {"operation_id": "a" * 32, "command": command(session.state, source).model_dump()}
        assert client.post(url, json=body).status_code == 403
        with TestClient(app, base_url="http://127.0.0.1:8765") as other:
            csrf = other.get("/api/bootstrap").json()["csrf"]
            assert other.post(url, json=body, headers={"x-csrf-token": csrf}).json() == {
                "code": "SESSION_NOT_FOUND"
            }
        first = client.post(url, json=body, headers=headers)
        assert first.status_code == 200
        payload = first.json()
        assert payload["receipt"]["fact_id"] == session.state.facts[0].id
        assert payload["receipt"]["fact_revision"] == 1
        assert payload["state"]["facts"][0]["scope"] == "fact"
        preview = client.post(
            f"/api/sessions/{session.id}/prepare", json={"text": "public"}, headers=headers
        ).json()
        replay = client.post(url, json=body, headers=headers)
        assert replay.json()["receipt"] == payload["receipt"]
        assert session.prepared is not None and session.prepared.id == preview["id"]


@pytest.mark.parametrize("bad", ["extra", "epoch_bool", "quote", "json", "media", "large"])
def test_invalid_manual_api_drops_approval_without_echoing_input(bad: str) -> None:
    gateway = Gateway(Capture(), resolver=Resolver())
    with TestClient(create_app(gateway), base_url="http://127.0.0.1:8765") as client:
        session, headers, source = api_session(client, gateway)
        before = session.state
        client.post(
            f"/api/sessions/{session.id}/prepare", json={"text": "public"}, headers=headers
        ).raise_for_status()
        request = command(before, source).model_dump()
        body = {"operation_id": "a" * 32, "command": request}
        if bad == "extra":
            request["allow"] = "SYNTH_PRIVATE_CANARY"
        elif bad == "epoch_bool":
            request["expected_epoch"] = True
        elif bad == "quote":
            request["evidence"]["quote"] = "SYNTH_PRIVATE_CANARY"
        kwargs: dict[str, Any] = {"json": body, "headers": headers}
        if bad in {"json", "media", "large"}:
            kwargs = {
                "content": "SYNTH_PRIVATE_CANARY" * (6000 if bad == "large" else 1),
                "headers": {
                    **headers,
                    "content-type": "text/plain" if bad == "media" else "application/json",
                },
            }
        result = client.post(f"/api/sessions/{session.id}/facts/create", **kwargs)
        assert result.status_code in {409, 413, 415, 422}
        assert "SYNTH_PRIVATE_CANARY" not in result.text
        assert session.state is before and session.prepared is None and not session.commands


def test_actual_resolver_sees_manual_facts_and_identity_before_first_mock_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []
    original = httpx.AsyncClient

    def response(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][-1]["content"])
        seen.append(context)
        assert len(context["messages"]) == len(context["entities"]) == len(context["facts"]) == 1
        assert context["facts"][0]["scope"] == "fact"
        assert context["facts"][0]["assertion"] == "Lan"
        name = payload["response_format"]["json_schema"]["name"]
        output = (
            {"checks": [{"message": 0, "status": "none", "new_facts": []}]}
            if name == "indexed_extraction_checks"
            else {
                "review_required": False,
                "checks": [{"fact": 0, "status": "none", "mentions": []}],
            }
        )
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}],
            },
        )

    def client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return original(*args, **kwargs, transport=httpx.MockTransport(response))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    text = "Lan is private; other Lan is public."
    intent = marks(text)
    identity = DraftIdentity(
        expected_epoch=0,
        text_digest=intent.text_digest,
        actions=(
            DraftCreate(occurrence=DraftOccurrence(start=0, end=3, quote="Lan"), protect=False),
        ),
    )
    resolver = LocalStateResolver(key="synthetic-key")
    result = asyncio.run(
        resolver.resolve(State(), text, draft_facts=intent, draft_identity=identity)
    )
    assert result.model_calls == len(seen) == 2
    assert len(result.staged_state.sources) == 1 and len(result.staged_state.facts) == 1


@pytest.mark.parametrize("identity", [False, True])
def test_forged_unserializable_command_invalidates_before_fingerprinting(identity: bool) -> None:
    gateway, session, source = seeded()
    asyncio.run(gateway.prepare_message(session, "public", ()))
    before = session.state
    request = command(before, source)
    with pytest.raises(PrivacyError, match="^STATE_INVALID$"):
        if identity:
            forged = CreateEntity(
                expected_epoch=before.epoch, evidence=request.evidence, protect=True
            ).model_copy(update={"evidence": object()})
            gateway.identity_command(session, forged, "a" * 32)
        else:
            gateway.create_fact(
                session, request.model_copy(update={"evidence": object()}), "a" * 32
            )
    assert session.state is before and session.prepared is None and not session.commands
