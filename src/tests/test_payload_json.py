from dataclasses import replace

import pytest
from privacy_gateway.application.gateway import Gateway
from privacy_gateway.domain.redaction import PrivacyError
from privacy_gateway.domain.serialization import strict_json


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1e999}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b'{"x":["\\ud800"]}',
        b'{"\\udfff":0}',
        '{"x":1}'.encode("utf-16"),
    ],
)
def test_strict_json_rejects_nonfinite_non_utf8_and_non_scalar_strings(raw: bytes) -> None:
    with pytest.raises(PrivacyError, match="FIXED_ERROR"):
        strict_json(raw, code="FIXED_ERROR")


def test_strict_json_accepts_scalar_surrogate_pair_and_finite_float() -> None:
    assert strict_json(b'{"x":"\\ud83d\\ude42","y":1e2}') == {"x": "🙂", "y": 100.0}


@pytest.mark.parametrize(
    "body",
    [
        b'{"model":"template-v1","messages":[{"role":"user","content":"hidden"}],"messages":[{"role":"user","content":"public"}]}',
        b'{"model":"template-v1","messages":[{"role":"user","content":"hidden","content":"public"}]}',
        b'{"model":"template-v1","messages":[{"role":"user","content":"hidden","\\u0063ontent":"public"}]}',
        b'{"model":"template-v1","messages":[{"role":"user","content":"public","extra":NaN}]}',
    ],
)
def test_gate_rejects_ambiguous_json_before_exact_bytes_egress(body: bytes) -> None:
    gateway = Gateway()
    session = gateway.create_session("owner")
    prepared = gateway.prepare(session, "public", ())
    with pytest.raises(PrivacyError, match="INVALID_PAYLOAD"):
        gateway.gate(replace(prepared, body=body), session)
