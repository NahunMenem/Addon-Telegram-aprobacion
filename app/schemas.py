from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JiraWebhook(BaseModel):
    model_config = ConfigDict(extra="allow")
    issue: dict[str, Any]


class TelegramUser(BaseModel):
    id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class TelegramChat(BaseModel):
    id: int


class TelegramMessage(BaseModel):
    message_id: int
    chat: TelegramChat
    text: str | None = None


class CallbackQuery(BaseModel):
    id: str
    from_: TelegramUser = Field(alias="from")
    message: TelegramMessage | None = None
    data: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class TelegramUpdate(BaseModel):
    update_id: int
    callback_query: CallbackQuery | None = None
