import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Request, status

from app.config import Settings, get_settings
from app.database import Database
from app.jira import JiraClient, JiraError, parse_jira_webhook
from app.models import ApprovalStatus
from app.schemas import JiraWebhook, TelegramUpdate
from app.security import (
    InvalidDecisionToken,
    create_decision_token,
    secrets_match,
    verify_decision_token,
)
from app.telegram import TelegramClient, TelegramError

logger = logging.getLogger("approvals")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or get_settings()
    database = Database(config.async_database_url)
    jira = JiraClient(
        config.jira_base_url,
        config.jira_email,
        config.jira_api_token.get_secret_value(),
        config.http_timeout_seconds,
    )
    telegram = TelegramClient(
        config.telegram_bot_token.get_secret_value(), config.http_timeout_seconds
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await database.create_schema()
        yield
        await jira.close()
        await telegram.close()
        await database.close()

    app = FastAPI(
        title="Jira Telegram Purchase Approvals",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.database = database
    app.state.jira = jira
    app.state.telegram = telegram

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/jira", status_code=status.HTTP_202_ACCEPTED)
    async def jira_webhook(
        event: JiraWebhook,
        request: Request,
        x_webhook_secret: str | None = Header(default=None),
    ) -> dict[str, str]:
        cfg: Settings = request.app.state.settings
        if not secrets_match(
            x_webhook_secret, cfg.jira_webhook_secret.get_secret_value()
        ):
            raise HTTPException(status_code=401, detail="Invalid webhook secret")
        try:
            purchase = parse_jira_webhook(
                event.model_dump(),
                cfg.jira_product_field_id,
                cfg.jira_purchase_url_field_id,
                cfg.jira_amount_field_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if purchase.status.casefold() != cfg.jira_source_status.casefold():
            raise HTTPException(status_code=409, detail="Issue is not in expected status")

        signing_secret = cfg.app_signing_secret.get_secret_value()
        approve_raw, approve = create_decision_token(
            purchase.issue_key, "a", signing_secret, cfg.decision_token_ttl_seconds
        )
        reject_raw, reject = create_decision_token(
            purchase.issue_key, "r", signing_secret, cfg.decision_token_ttl_seconds
        )
        approval, created = await request.app.state.database.create_pending(
            purchase.issue_key, approve, reject
        )
        if not created:
            return {"status": "duplicate", "issue_key": purchase.issue_key}
        try:
            message_id = await request.app.state.telegram.send_purchase(
                cfg.telegram_admin_chat_id,
                purchase,
                approve_raw,
                reject_raw,
                cfg.jira_base_url,
            )
            await request.app.state.database.record_message(
                purchase.issue_key, cfg.telegram_admin_chat_id, message_id
            )
        except TelegramError as exc:
            logger.error("Could not deliver approval notification for %s", purchase.issue_key)
            await request.app.state.database.delete_undelivered(approval.id)
            raise HTTPException(status_code=502, detail="Telegram delivery failed") from exc
        return {"status": "sent", "issue_key": purchase.issue_key}

    @app.post("/webhooks/telegram")
    async def telegram_webhook(
        update: TelegramUpdate,
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, str]:
        cfg: Settings = request.app.state.settings
        if not secrets_match(
            x_telegram_bot_api_secret_token,
            cfg.telegram_webhook_secret.get_secret_value(),
        ):
            raise HTTPException(status_code=401, detail="Invalid webhook secret")
        callback = update.callback_query
        if callback is None:
            return {"status": "ignored"}
        if callback.from_.id != cfg.telegram_admin_user_id:
            try:
                await request.app.state.telegram.answer_callback(
                    callback.id, "No estás autorizado para decidir."
                )
            except TelegramError:
                pass
            raise HTTPException(status_code=403, detail="Unauthorized Telegram user")
        if callback.data is None or len(callback.data.encode()) > 64:
            raise HTTPException(status_code=400, detail="Invalid callback data")

        parts = callback.data.split(".")
        if len(parts) != 5:
            raise HTTPException(status_code=400, detail="Invalid callback token")
        nonce = parts[1]
        found = await request.app.state.database.lookup_by_nonce(nonce)
        if found is None:
            raise HTTPException(status_code=400, detail="Unknown callback token")
        approval, stored_token = found
        try:
            token = verify_decision_token(
                callback.data,
                approval.issue_key,
                cfg.app_signing_secret.get_secret_value(),
            )
        except InvalidDecisionToken as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if token.action != stored_token.action or stored_token.used_at is not None:
            raise HTTPException(status_code=409, detail="Decision already processed")
        claimed = await request.app.state.database.claim_decision(
            approval.id, stored_token.id, callback.from_.id
        )
        if not claimed:
            raise HTTPException(status_code=409, detail="Decision already processed")

        approved = token.action == "a"
        decision = "approved" if approved else "rejected"
        target = cfg.jira_approved_status if approved else cfg.jira_rejected_status
        actor = (
            f"@{callback.from_.username}"
            if callback.from_.username
            else " ".join(
                part
                for part in (callback.from_.first_name, callback.from_.last_name)
                if part
            )
            or f"Telegram user {callback.from_.id}"
        )
        try:
            current_status = await request.app.state.jira.get_status(approval.issue_key)
            if current_status.casefold() != cfg.jira_source_status.casefold():
                raise JiraError(
                    f"Issue is no longer in '{cfg.jira_source_status}'"
                )
            await request.app.state.jira.transition_to(approval.issue_key, target)
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            verb = "aprobó" if approved else "rechazó"
            await request.app.state.jira.add_comment(
                approval.issue_key,
                f"{actor} {verb} la compra vía Telegram el {timestamp}. "
                f"Telegram user ID: {callback.from_.id}.",
            )
            await request.app.state.database.finish_decision(
                approval.id, decision, success=True
            )
        except JiraError as exc:
            await request.app.state.database.finish_decision(
                approval.id, decision, success=False
            )
            logger.error("Jira decision failed for %s", approval.issue_key)
            try:
                await request.app.state.telegram.answer_callback(
                    callback.id, "No se pudo registrar la decisión en Jira."
                )
            except TelegramError:
                pass
            raise HTTPException(status_code=502, detail="Jira update failed") from exc

        if callback.message:
            try:
                await request.app.state.telegram.mark_decided(
                    callback.message.chat.id,
                    callback.message.message_id,
                    approved,
                    actor,
                    callback.message.text,
                )
            except TelegramError:
                logger.error(
                    "Jira was updated but Telegram message update failed for %s",
                    approval.issue_key,
                )
        try:
            await request.app.state.telegram.answer_callback(
                callback.id, "Decisión registrada."
            )
        except TelegramError:
            pass
        return {"status": decision, "issue_key": approval.issue_key}

    return app
