import asyncio
import json
from dataclasses import replace

import pytest
from privacy_gateway.adapters.fake_provider import FakeProvider
from privacy_gateway.application.gateway import Gateway, Message
from privacy_gateway.domain.redaction import Binding, PrivacyError, tokens_in
from privacy_gateway.domain.semantic import SemanticReport


@pytest.mark.parametrize("first_plain", [False, True])
def test_merged_credential_constituent_stays_denied_across_turns(first_plain: bool) -> None:
    provider = SpyProvider("ACK")
    gateway = Gateway(provider)
    session = gateway.create_session("owner")
    report = SemanticReport((), "synthetic-empty-report", 0, 1)
    if first_plain:
        initial = gateway.prepare(session, "Contact alice@example.test", (), report=report)
        asyncio.run(gateway.send(session, initial.id))
    labeled = gateway.prepare(session, "password=alice@example.test", (), report=report)
    assert any(
        binding.value == "alice@example.test" and not binding.restore_allowed
        for binding in labeled.staged_vault.values()
    )
    asyncio.run(gateway.send(session, labeled.id))
    repeated = gateway.prepare(session, "repeat alice@example.test", (), report=report)
    assert repeated.detections and all(not item.restore_allowed for item in repeated.detections)
    provider.response = repeated.sanitized
    assert "alice@example.test" not in asyncio.run(gateway.send(session, repeated.id))
    assert all(
        not binding.restore_allowed
        for binding in session.vault.values()
        if "alice@example.test" in binding.value
    )


class SpyProvider(FakeProvider):
    def __init__(self, response: str | None = None, *, failure: bool = False) -> None:
        self.calls: list[bytes] = []
        self.response = response
        self.failure = failure

    async def complete(self, body: bytes) -> str:
        self.calls.append(body)
        if self.failure:
            raise RuntimeError("synthetic-provider-exception-canary")
        if self.response is not None:
            return self.response
        result: str = json.loads(body)["messages"][-1]["content"]
        return result


def test_prepare_never_dispatches_and_send_uses_exact_preview_bytes() -> None:
    provider = SpyProvider()
    gateway = Gateway(provider)
    session = gateway.create_session("owner")
    original = "Gửi Lan tại lan@example.com về Maple 🙂"
    prepared = gateway.prepare(session, original, ("Lan", "Maple"))
    assert provider.calls == []
    assert session.vault == {}
    assert all(value not in prepared.body.decode() for value in ("Lan", "lan@example.com", "Maple"))
    assert len(prepared.manifest) == 3
    assert asyncio.run(gateway.send(session, prepared.id)) == original
    assert provider.calls == [prepared.body]
    assert session.private_history == [
        Message("user", original),
        Message("assistant", original, "local-demo"),
    ]
    assert all("Lan" not in m.content for m in session.provider_history)
    assert original not in json.dumps(session.audit)
    with pytest.raises(PrivacyError, match="STALE_PREVIEW"):
        asyncio.run(gateway.send(session, prepared.id))
    assert len(provider.calls) == 1


def test_history_is_refiltered_and_previous_protection_cannot_silently_disappear() -> None:
    provider = SpyProvider("Hẹn Lan về Maple")
    gateway = Gateway(provider)
    session = gateway.create_session("owner")
    first = gateway.prepare(session, "Chào Lan", ("Lan",))
    asyncio.run(gateway.send(session, first.id))
    second = gateway.prepare(session, "Lan trao đổi tiếp", ("Maple",))
    body = second.body.decode()
    assert "Lan" not in body and "Maple" not in body
    assert tokens_in(first.sanitized) <= tokens_in(second.sanitized)
    assert session.private_history[-1].content == "Hẹn Lan về Maple"
    assert len(json.loads(body)["messages"]) == 3


def test_credential_restore_deny_wins_over_exact_manual_rule() -> None:
    gateway = Gateway(SpyProvider())
    session = gateway.create_session("owner")
    prepared = gateway.prepare(session, "Mã DEMO_SECRET_ALPHA", ("DEMO_SECRET_ALPHA",))
    result = asyncio.run(gateway.send(session, prepared.id))
    assert result == "Mã [bí mật không phục hồi]"
    assert "DEMO_SECRET_ALPHA" not in session.provider_history[-1].content


@pytest.mark.parametrize(
    "mutation",
    ["new_prepare", "failed_prepare", "discard", "expiry", "model", "serializer", "provider"],
)
def test_changed_snapshot_fails_before_dispatch(mutation: str) -> None:
    provider = SpyProvider()
    gateway = Gateway(provider)
    session = gateway.create_session("owner")
    first = gateway.prepare(session, "Lan", ("Lan",))
    if mutation == "new_prepare":
        gateway.prepare(session, "Khác", ())
    elif mutation == "failed_prepare":
        with pytest.raises(PrivacyError):
            gateway.prepare(session, "[[PG_forged", ())
    elif mutation == "discard":
        gateway.discard(session)
    elif mutation == "expiry":
        session.prepared = replace(first, expires_at=0)
    elif mutation == "model":
        provider.model = "changed"
    elif mutation == "serializer":
        provider.serializer_version = "changed"
    else:
        provider.provider_id = "changed"
    with pytest.raises(PrivacyError, match="STALE_PREVIEW"):
        asyncio.run(gateway.send(session, first.id))
    assert provider.calls == [] and session.vault == {}


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"model": "template-v1", "messages": []},
        {"model": "template-v1", "messages": [{"role": [], "content": "public"}]},
        {"model": "template-v1", "messages": [{"role": "tool", "content": "public"}]},
        {"model": "template-v1", "messages": [{"role": "user", "content": "public", "image": "x"}]},
        {
            "model": "template-v1",
            "messages": [{"role": "user", "content": "public"}],
            "metadata": "private",
        },
    ],
)
def test_exact_payload_schema_checked_before_transport(payload: object) -> None:
    provider = SpyProvider()
    gateway = Gateway(provider)
    session = gateway.create_session("owner")
    prepared = gateway.prepare(session, "public", ())
    session.prepared = replace(prepared, body=json.dumps(payload).encode())
    with pytest.raises(PrivacyError, match="INVALID_PAYLOAD"):
        asyncio.run(gateway.send(session, prepared.id))
    assert provider.calls == []


@pytest.mark.parametrize(
    "content", ["lan@example.com", "Lan", "[[PG_forged", "[[PG_v1_" + "f" * 32 + "]]"]
)
def test_gate_scans_every_message_for_raw_values_and_unissued_tokens(content: str) -> None:
    provider = SpyProvider()
    gateway = Gateway(provider)
    session = gateway.create_session("owner")
    prepared = gateway.prepare(session, "Lan", ("Lan",))
    payload = json.loads(prepared.body)
    payload["messages"].insert(0, {"role": "assistant", "content": content})
    session.prepared = replace(prepared, body=json.dumps(payload).encode())
    with pytest.raises(PrivacyError):
        asyncio.run(gateway.send(session, prepared.id))
    assert provider.calls == []


def test_manifest_is_immutable_and_scoped_to_current_request() -> None:
    provider = SpyProvider()
    gateway = Gateway(provider)
    first = gateway.create_session("owner")
    prepared = gateway.prepare(first, "Lan", ("Lan",))
    token = next(iter(prepared.manifest))
    with pytest.raises(TypeError):
        prepared.manifest[token] = prepared.manifest[token]  # type: ignore[index]
    asyncio.run(gateway.send(first, prepared.id))
    provider.response = token
    second = gateway.create_session("owner")
    request = gateway.prepare(second, "public", ())
    with pytest.raises(PrivacyError, match="UNKNOWN_TOKEN"):
        asyncio.run(gateway.send(second, request.id))
    assert second.private_history == [] and second.vault == {}
    first.provider_history.clear()
    request = gateway.prepare(first, "public", ())
    with pytest.raises(PrivacyError, match="UNKNOWN_TOKEN"):
        asyncio.run(gateway.send(first, request.id))


@pytest.mark.parametrize("kind", ["failure", "malformed"])
def test_failed_provider_has_no_partial_commit_or_raw_error(kind: str) -> None:
    gateway = Gateway(SpyProvider("[[PG_bad", failure=kind == "failure"))
    session = gateway.create_session("owner")
    prepared = gateway.prepare(session, "Lan", ("Lan",))
    with pytest.raises(PrivacyError) as caught:
        asyncio.run(gateway.send(session, prepared.id))
    assert "canary" not in str(caught.value)
    assert not session.busy and session.prepared is None
    assert session.private_history == session.provider_history == []
    assert session.vault == {}


@pytest.mark.parametrize("action", ["cancel", "delete", "double_send", "prepare", "discard"])
def test_inflight_commands_with_a_transport_barrier(action: str) -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        class BarrierProvider(SpyProvider):
            async def complete(self, body: bytes) -> str:
                self.calls.append(body)
                entered.set()
                await release.wait()
                return str(json.loads(body)["messages"][-1]["content"])

        provider = BarrierProvider()
        gateway = Gateway(provider)
        session = gateway.create_session("owner")
        prepared = gateway.prepare(session, "Lan", ("Lan",))
        pending = asyncio.create_task(gateway.send(session, prepared.id))
        await asyncio.wait_for(entered.wait(), timeout=2)
        if action in {"cancel", "delete"}:
            getattr(gateway, action)(session)
        else:
            with pytest.raises(PrivacyError, match="SESSION_BUSY"):
                if action == "double_send":
                    await gateway.send(session, prepared.id)
                elif action == "prepare":
                    gateway.prepare(session, "new policy", ())
                else:
                    gateway.discard(session)
        release.set()
        if action in {"cancel", "delete"}:
            with pytest.raises(PrivacyError, match="CANCELLED"):
                await pending
            assert session.private_history == session.provider_history == []
            assert session.vault == {}
        else:
            assert await pending == "Lan"
        assert len(provider.calls) == 1 and not session.busy
        if action == "delete":
            assert session.id not in gateway.sessions and session.audit == []

    asyncio.run(scenario())


def test_overlap_requires_review_without_fallback() -> None:
    provider = SpyProvider()
    gateway = Gateway(provider)
    session = gateway.create_session("owner")
    with pytest.raises(PrivacyError, match="OVERLAPPING_DETECTIONS"):
        gateway.prepare(session, "Lan Nguyen", ("Lan", "Lan Nguyen"))
    assert session.prepared is None and provider.calls == []


def test_owner_is_required_to_retrieve_session() -> None:
    gateway = Gateway()
    session = gateway.create_session("owner")
    with pytest.raises(PrivacyError, match="SESSION_NOT_FOUND"):
        gateway.session(session.id, "someone-else")


def test_gate_does_not_join_text_across_placeholders() -> None:
    gateway = Gateway(SpyProvider())
    session = gateway.create_session("owner")
    request = gateway.prepare(session, "ab aXb", ("ab", "X"))
    assert asyncio.run(gateway.send(session, request.id)) == "ab aXb"


def test_delayed_discard_and_cancel_only_target_the_named_snapshot() -> None:
    gateway = Gateway(SpyProvider())
    session = gateway.create_session("owner")
    first = gateway.prepare(session, "first", ())
    second = gateway.prepare(session, "second", ())
    gateway.discard(session, first.id)
    gateway.cancel(session, first.id)
    assert asyncio.run(gateway.send(session, second.id)) == "second"


def test_credential_deny_applies_to_all_occurrences_and_future_turns() -> None:
    gateway = Gateway(SpyProvider())
    session = gateway.create_session("owner")
    first = gateway.prepare(session, "DEMO_SECRET_ALPHA xDEMO_SECRET_ALPHA", ("DEMO_SECRET_ALPHA",))
    assert not any(d.restore_allowed for d in first.detections)
    assert "DEMO_SECRET_ALPHA" not in asyncio.run(gateway.send(session, first.id))
    second = gateway.prepare(session, "xDEMO_SECRET_ALPHA", ())
    assert "DEMO_SECRET_ALPHA" not in asyncio.run(gateway.send(session, second.id))
    assert all(not binding.restore_allowed for binding in session.vault.values())


def test_new_deny_covers_historical_tokens_and_rolls_back_on_failure() -> None:
    provider = SpyProvider()
    gateway = Gateway(provider)
    session = gateway.create_session("owner")
    first = gateway.prepare(session, "xDEMO_SECRET_ALPHA", ("DEMO_SECRET_ALPHA",))
    asyncio.run(gateway.send(session, first.id))
    token = next(iter(session.vault))
    assert session.vault[token].restore_allowed
    second = gateway.prepare(session, "DEMO_SECRET_ALPHA", ())
    assert not second.manifest[token].restore_allowed
    provider.failure = True
    with pytest.raises(PrivacyError, match="PROVIDER_FAILED"):
        asyncio.run(gateway.send(session, second.id))
    assert session.vault[token].restore_allowed
    provider.failure = False
    provider.response = token
    third = gateway.prepare(session, "DEMO_SECRET_ALPHA", ())
    assert asyncio.run(gateway.send(session, third.id)) == "[bí mật không phục hồi]"
    assert not session.vault[token].restore_allowed


@pytest.mark.parametrize(
    "response", ["x" * 200_001, "🙂" * 50_001], ids=["characters", "utf8-bytes"]
)
def test_provider_response_budget_rejects_before_state_commit(response: str) -> None:
    gateway = Gateway(SpyProvider(response))
    session = gateway.create_session("owner")
    request = gateway.prepare(session, "public", ())
    with pytest.raises(PrivacyError, match="RESPONSE_LIMIT"):
        asyncio.run(gateway.send(session, request.id))
    assert session.vault == {} and session.private_history == [] and not session.busy


def test_restored_output_expansion_is_bounded() -> None:
    provider = SpyProvider()
    gateway = Gateway(provider)
    session = gateway.create_session("owner")
    request = gateway.prepare(session, "x" * 200, ("x" * 200,))
    provider.response = next(iter(request.manifest)) * 1001
    with pytest.raises(PrivacyError, match="RESPONSE_LIMIT"):
        asyncio.run(gateway.send(session, request.id))
    assert not session.private_history


def test_detection_budget_fails_closed() -> None:
    gateway = Gateway(SpyProvider())
    session = gateway.create_session("owner")
    with pytest.raises(PrivacyError, match="DETECTION_LIMIT"):
        gateway.prepare(session, "a@b.co " * 513, ())
    assert session.prepared is None


def test_vault_limit_preserves_existing_state() -> None:
    gateway = Gateway(SpyProvider())
    session = gateway.create_session("owner")
    session.vault = {
        (token := f"[[PG_v1_{index:032x}]]"): Binding(token, f"value-{index:04d}")
        for index in range(1024)
    }
    before = session.vault.copy()
    with pytest.raises(PrivacyError, match="VAULT_LIMIT"):
        gateway.prepare(session, "fresh@example.com", ())
    assert session.vault == before and not session.prepared


def test_delayed_cancel_cannot_stop_a_new_active_turn() -> None:
    async def scenario() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        class BarrierProvider(SpyProvider):
            async def complete(self, body: bytes) -> str:
                entered.set()
                await release.wait()
                return await super().complete(body)

        gateway = Gateway(BarrierProvider())
        session = gateway.create_session("owner")
        old = gateway.prepare(session, "old", ())
        new = gateway.prepare(session, "new", ())
        pending = asyncio.create_task(gateway.send(session, new.id))
        await entered.wait()
        gateway.cancel(session, old.id)
        release.set()
        assert await pending == "new"

    asyncio.run(scenario())


def test_long_public_text_does_not_starve_detection() -> None:
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from privacy_gateway.domain.detection import detect; "
            "assert not detect('a' * 200000, ())",
        ],
        check=True,
        capture_output=True,
        timeout=3,
    )
