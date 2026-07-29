import httpx
import pytest

from app.jira import JiraClient, parse_jira_webhook


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


@pytest.mark.asyncio
async def test_transition_can_follow_configured_intermediate_status():
    requests: list[httpx.Request] = []
    transition_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal transition_reads
        requests.append(request)
        if request.method == "GET":
            transition_reads += 1
            target = "Autorizacion" if transition_reads == 1 else "Compra autorizada"
            transition_id = "10" if transition_reads == 1 else "20"
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {"id": transition_id, "to": {"name": target}}
                    ]
                },
            )
        return httpx.Response(204)

    http_client = httpx.AsyncClient(
        base_url="https://example.atlassian.net",
        transport=httpx.MockTransport(handler),
    )
    jira = JiraClient(
        "https://example.atlassian.net",
        "bot@example.com",
        "token",
        10,
        client=http_client,
    )
    try:
        await jira.transition_to(
            "SEAL-4", "Compra autorizada", intermediate_status="Autorizacion"
        )
    finally:
        await http_client.aclose()

    assert [request.method for request in requests] == ["GET", "POST", "GET", "POST"]
    assert b'"id":"10"' in requests[1].content
    assert b'"id":"20"' in requests[3].content
