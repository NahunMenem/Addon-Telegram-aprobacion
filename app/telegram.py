import html
from typing import Any

import httpx

from app.jira import PurchaseRequest


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(
        self,
        token: str,
        timeout: float,
        client: httpx.AsyncClient | None = None,
    ):
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}",
            timeout=httpx.Timeout(timeout),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self.client.post(f"/{method}", json=payload)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise TelegramError("Telegram rejected the request")
            return data.get("result")
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramError("Telegram request failed") from exc

    async def send_purchase(
        self,
        chat_id: int,
        purchase: PurchaseRequest,
        approve_token: str,
        reject_token: str,
        jira_base_url: str,
    ) -> int:
        e = html.escape
        text = (
            "🛒 <b>Nueva solicitud de compra</b>\n\n"
            f"<b>Issue:</b> {e(purchase.issue_key)}\n"
            f"<b>Resumen:</b> {e(purchase.summary)}\n"
            f"<b>Producto:</b> {e(purchase.product)}\n"
            f"<b>Importe:</b> {e(purchase.amount)}\n"
            f"<b>Solicitante:</b> {e(purchase.requester)}\n"
            f"<b>Estado:</b> {e(purchase.status)}\n"
            f"<b>URL de compra:</b> {e(purchase.purchase_url)}"
        )
        result = await self._call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "✅ Aprobar", "callback_data": approve_token},
                            {"text": "❌ Rechazar", "callback_data": reject_token},
                        ],
                        [
                            {
                                "text": "🔗 Ver en Jira",
                                "url": f"{jira_base_url}/browse/{purchase.issue_key}",
                            }
                        ],
                    ]
                },
            },
        )
        if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
            raise TelegramError("Telegram sendMessage response is invalid")
        return result["message_id"]

    async def answer_callback(self, callback_id: str, text: str) -> None:
        await self._call(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": text[:200]},
        )

    async def mark_decided(
        self,
        chat_id: int,
        message_id: int,
        approved: bool,
        actor: str,
        original_text: str | None,
    ) -> None:
        result = "✅ COMPRA APROBADA" if approved else "❌ COMPRA RECHAZADA"
        decision_text = f"{result}\nDecisión registrada por {actor}."
        if original_text:
            await self._call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"{original_text}\n\n{decision_text}",
                    "disable_web_page_preview": True,
                    "reply_markup": {"inline_keyboard": []},
                },
            )
        else:
            await self._call(
                "editMessageReplyMarkup",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": {"inline_keyboard": []},
                },
            )
            await self._call(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "reply_to_message_id": message_id,
                    "text": decision_text,
                },
            )
