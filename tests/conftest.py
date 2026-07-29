from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="test-bot-token",
        telegram_admin_chat_id=-100123,
        telegram_admin_user_id=4242,
        telegram_webhook_secret="telegram-secret-value",
        jira_base_url="https://example.atlassian.net",
        jira_email="bot@example.com",
        jira_api_token="test-jira-token",
        jira_webhook_secret="jira-secret-value",
        jira_product_field_id="customfield_10001",
        jira_purchase_url_field_id="customfield_10002",
        jira_amount_field_id="customfield_10003",
        app_base_url="https://approvals.example.com",
        app_signing_secret="a-test-signing-secret-with-32-characters-minimum",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
    )


@pytest.fixture
def client(settings: Settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        app.state.jira.get_status = AsyncMock(return_value="Aviso por Telegram")
        app.state.jira.transition_to = AsyncMock()
        app.state.jira.add_comment = AsyncMock()
        app.state.telegram.send_purchase = AsyncMock(return_value=99)
        app.state.telegram.answer_callback = AsyncMock()
        app.state.telegram.mark_decided = AsyncMock()
        yield test_client


@pytest.fixture
def jira_payload() -> dict:
    return {
        "issue": {
            "key": "SEAL-123",
            "fields": {
                "summary": "Comprar monitor",
                "customfield_10001": "Monitor 27 pulgadas",
                "customfield_10002": "https://shop.example/monitor",
                "customfield_10003": 250000,
                "reporter": {"displayName": "Usuario Solicitante"},
                "status": {"name": "Aviso por Telegram"},
            },
        }
    }
