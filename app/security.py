import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass


class InvalidDecisionToken(ValueError):
    pass


@dataclass(frozen=True)
class DecisionToken:
    action: str
    nonce: str
    expires_at: int


def secrets_match(received: str | None, expected: str) -> bool:
    if received is None:
        return False
    return hmac.compare_digest(received.encode(), expected.encode())


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _issue_digest(issue_key: str) -> str:
    return _b64(hashlib.sha256(issue_key.upper().encode()).digest()[:6])


def create_decision_token(
    issue_key: str, action: str, secret: str, ttl_seconds: int
) -> tuple[str, DecisionToken]:
    if action not in {"a", "r"}:
        raise ValueError("Unsupported action")
    nonce = _b64(secrets.token_bytes(9))
    expires_at = int(time.time()) + ttl_seconds
    expiry = format(expires_at, "x")
    payload = f"{action}.{nonce}.{expiry}.{_issue_digest(issue_key)}"
    signature = _b64(
        hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()[:8]
    )
    token = f"{payload}.{signature}"
    if len(token.encode()) > 64:
        raise RuntimeError("Decision token exceeds Telegram callback limit")
    return token, DecisionToken(action, nonce, expires_at)


def verify_decision_token(
    token: str, issue_key: str, secret: str, now: int | None = None
) -> DecisionToken:
    if len(token.encode()) > 64:
        raise InvalidDecisionToken("Token too long")
    try:
        action, nonce, expiry, issue_digest, signature = token.split(".")
        expires_at = int(expiry, 16)
    except (ValueError, TypeError) as exc:
        raise InvalidDecisionToken("Malformed token") from exc
    if action not in {"a", "r"} or not nonce:
        raise InvalidDecisionToken("Malformed token")
    payload = f"{action}.{nonce}.{expiry}.{issue_digest}"
    expected = _b64(
        hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()[:8]
    )
    if not hmac.compare_digest(signature, expected):
        raise InvalidDecisionToken("Invalid signature")
    if not hmac.compare_digest(issue_digest, _issue_digest(issue_key)):
        raise InvalidDecisionToken("Token belongs to another issue")
    if expires_at < (int(time.time()) if now is None else now):
        raise InvalidDecisionToken("Expired token")
    return DecisionToken(action, nonce, expires_at)
