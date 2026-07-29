import pytest

from app.jira import parse_jira_webhook


def test_parse_configurable_custom_fields(jira_payload):
    purchase = parse_jira_webhook(
        jira_payload,
        "customfield_10001",
        "customfield_10002",
        "customfield_10003",
    )
    assert purchase.issue_key == "SEAL-123"
    assert purchase.product == "Monitor 27 pulgadas"
    assert purchase.amount == "250000"
    assert purchase.requester == "Usuario Solicitante"


def test_parse_rejects_missing_issue():
    with pytest.raises(ValueError, match="Missing issue"):
        parse_jira_webhook({}, "a", "b", "c")


def test_parse_rejects_unsafe_issue_key(jira_payload):
    jira_payload["issue"]["key"] = "../../secrets"
    with pytest.raises(ValueError, match="Invalid issue key"):
        parse_jira_webhook(jira_payload, "a", "b", "c")
