import pytest

from app.security import (
    InvalidDecisionToken,
    create_decision_token,
    secrets_match,
    verify_decision_token,
)


def test_signed_token_is_compact_bound_and_verifiable():
    raw, created = create_decision_token("SEAL-1", "a", "x" * 32, 300)
    verified = verify_decision_token(
        raw, "SEAL-1", "x" * 32, now=created.expires_at - 1
    )
    assert len(raw.encode()) <= 64
    assert verified.action == "a"
    assert verified.nonce == created.nonce


def test_token_cannot_be_used_for_another_issue():
    raw, _ = create_decision_token("SEAL-1", "r", "x" * 32, 300)
    with pytest.raises(InvalidDecisionToken, match="another issue"):
        verify_decision_token(raw, "SEAL-2", "x" * 32)


def test_expired_and_tampered_tokens_are_rejected():
    raw, token = create_decision_token("SEAL-1", "a", "x" * 32, 300)
    with pytest.raises(InvalidDecisionToken, match="Expired"):
        verify_decision_token(raw, "SEAL-1", "x" * 32, now=token.expires_at + 1)
    with pytest.raises(InvalidDecisionToken, match="signature"):
        verify_decision_token(raw[:-1] + ("A" if raw[-1] != "A" else "B"), "SEAL-1", "x" * 32)


def test_constant_time_secret_comparison_contract():
    assert secrets_match("same-secret", "same-secret")
    assert not secrets_match(None, "same-secret")
    assert not secrets_match("wrong", "same-secret")
