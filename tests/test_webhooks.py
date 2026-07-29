from app.telegram import TelegramError


def _jira_headers(secret="jira-secret-value"):
    return {"X-Webhook-Secret": secret}


def _telegram_headers(secret="telegram-secret-value"):
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


def _callback(token: str, user_id: int = 4242) -> dict:
    return {
        "update_id": 1,
        "callback_query": {
            "id": "callback-1",
            "from": {
                "id": user_id,
                "username": "admin",
                "first_name": "Admin",
            },
            "message": {
                "message_id": 99,
                "chat": {"id": -100123},
                "text": "Nueva solicitud de compra",
            },
            "data": token,
        },
    }


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_jira_webhook_requires_secret(client, jira_payload):
    response = client.post("/webhooks/jira", json=jira_payload)
    assert response.status_code == 401


def test_jira_webhook_rejects_wrong_status(client, jira_payload):
    jira_payload["issue"]["fields"]["status"]["name"] = "En progreso"
    response = client.post(
        "/webhooks/jira", headers=_jira_headers(), json=jira_payload
    )
    assert response.status_code == 409


def test_jira_webhook_sends_once(client, jira_payload):
    first = client.post(
        "/webhooks/jira", headers=_jira_headers(), json=jira_payload
    )
    second = client.post(
        "/webhooks/jira", headers=_jira_headers(), json=jira_payload
    )
    assert first.status_code == 202
    assert first.json()["status"] == "sent"
    assert second.json()["status"] == "duplicate"
    client.app.state.telegram.send_purchase.assert_awaited_once()


def test_failed_telegram_delivery_can_be_retried(client, jira_payload):
    client.app.state.telegram.send_purchase.side_effect = TelegramError("failed")
    failed = client.post(
        "/webhooks/jira", headers=_jira_headers(), json=jira_payload
    )
    assert failed.status_code == 502

    client.app.state.telegram.send_purchase.side_effect = None
    client.app.state.telegram.send_purchase.return_value = 100
    retried = client.post(
        "/webhooks/jira", headers=_jira_headers(), json=jira_payload
    )
    assert retried.status_code == 202
    assert retried.json()["status"] == "sent"


def test_approve_callback_transitions_comments_and_marks_message(client, jira_payload):
    response = client.post(
        "/webhooks/jira", headers=_jira_headers(), json=jira_payload
    )
    approve_token = client.app.state.telegram.send_purchase.await_args.args[2]

    decision = client.post(
        "/webhooks/telegram",
        headers=_telegram_headers(),
        json=_callback(approve_token),
    )

    assert decision.status_code == 200
    assert decision.json()["status"] == "approved"
    client.app.state.jira.get_status.assert_awaited_once_with("SEAL-123")
    client.app.state.jira.transition_to.assert_awaited_once_with(
        "SEAL-123", "Compra autorizada"
    )
    client.app.state.jira.add_comment.assert_awaited_once()
    client.app.state.telegram.mark_decided.assert_awaited_once()

    duplicate = client.post(
        "/webhooks/telegram",
        headers=_telegram_headers(),
        json=_callback(approve_token),
    )
    assert duplicate.status_code == 409


def test_reject_callback_uses_rejected_transition(client, jira_payload):
    client.post("/webhooks/jira", headers=_jira_headers(), json=jira_payload)
    reject_token = client.app.state.telegram.send_purchase.await_args.args[3]
    decision = client.post(
        "/webhooks/telegram",
        headers=_telegram_headers(),
        json=_callback(reject_token),
    )
    assert decision.json()["status"] == "rejected"
    client.app.state.jira.transition_to.assert_awaited_once_with(
        "SEAL-123", "Compra rechazada"
    )


def test_callback_revalidates_current_jira_status(client, jira_payload):
    client.post("/webhooks/jira", headers=_jira_headers(), json=jira_payload)
    token = client.app.state.telegram.send_purchase.await_args.args[2]
    client.app.state.jira.get_status.return_value = "Cancelada"
    response = client.post(
        "/webhooks/telegram",
        headers=_telegram_headers(),
        json=_callback(token),
    )
    assert response.status_code == 502
    client.app.state.jira.transition_to.assert_not_awaited()
    client.app.state.telegram.mark_decided.assert_not_awaited()


def test_callback_rejects_unauthorized_user(client, jira_payload):
    client.post("/webhooks/jira", headers=_jira_headers(), json=jira_payload)
    token = client.app.state.telegram.send_purchase.await_args.args[2]
    response = client.post(
        "/webhooks/telegram",
        headers=_telegram_headers(),
        json=_callback(token, user_id=999),
    )
    assert response.status_code == 403
    client.app.state.jira.transition_to.assert_not_awaited()


def test_callback_secret_and_size_are_validated(client):
    assert (
        client.post("/webhooks/telegram", json={"update_id": 1}).status_code == 401
    )
    response = client.post(
        "/webhooks/telegram",
        headers=_telegram_headers(),
        json=_callback("x" * 65),
    )
    assert response.status_code == 400
