from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        populate_by_name=True,
    )

    telegram_bot_token: SecretStr = Field(validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_admin_chat_id: int = Field(validation_alias="TELEGRAM_ADMIN_CHAT_ID")
    telegram_admin_user_id: int = Field(validation_alias="TELEGRAM_ADMIN_USER_ID")
    telegram_webhook_secret: SecretStr = Field(
        validation_alias="TELEGRAM_WEBHOOK_SECRET", min_length=16
    )

    jira_base_url: str = Field(validation_alias="JIRA_BASE_URL")
    jira_email: str = Field(validation_alias="JIRA_EMAIL")
    jira_api_token: SecretStr = Field(validation_alias="JIRA_API_TOKEN")
    jira_webhook_secret: SecretStr = Field(
        validation_alias="JIRA_WEBHOOK_SECRET", min_length=16
    )
    jira_product_field_id: str = Field(validation_alias="JIRA_PRODUCT_FIELD_ID")
    jira_purchase_url_field_id: str = Field(
        validation_alias="JIRA_PURCHASE_URL_FIELD_ID"
    )
    jira_amount_field_id: str = Field(validation_alias="JIRA_AMOUNT_FIELD_ID")

    app_base_url: str = Field(validation_alias="APP_BASE_URL")
    app_signing_secret: SecretStr = Field(
        validation_alias="APP_SIGNING_SECRET", min_length=32
    )
    database_url: str = Field(
        default="sqlite:///./approvals.db", validation_alias="DATABASE_URL"
    )

    jira_source_status: str = Field(
        default="Aviso por Telegram", validation_alias="JIRA_SOURCE_STATUS"
    )
    jira_approved_status: str = Field(
        default="Compra autorizada", validation_alias="JIRA_APPROVED_STATUS"
    )
    jira_rejected_status: str = Field(
        default="Compra rechazada", validation_alias="JIRA_REJECTED_STATUS"
    )
    jira_decision_intermediate_status: str | None = Field(
        default="Autorizacion",
        validation_alias="JIRA_DECISION_INTERMEDIATE_STATUS",
    )
    decision_token_ttl_seconds: int = Field(
        default=86400, validation_alias="DECISION_TOKEN_TTL_SECONDS", ge=60
    )
    http_timeout_seconds: float = Field(
        default=10.0, validation_alias="HTTP_TIMEOUT_SECONDS", gt=0, le=60
    )

    @field_validator("jira_base_url", "app_base_url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("jira_decision_intermediate_status", mode="before")
    @classmethod
    def empty_intermediate_as_none(cls, value: str | None) -> str | None:
        return value or None

    @property
    def async_database_url(self) -> str:
        if self.database_url.startswith("sqlite:///"):
            return self.database_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
