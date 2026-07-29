from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Approval, ApprovalStatus, Base, StoredDecisionToken
from app.security import DecisionToken


class Database:
    def __init__(self, url: str):
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def create_pending(
        self,
        issue_key: str,
        approve: DecisionToken,
        reject: DecisionToken,
    ) -> tuple[Approval, bool]:
        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(Approval).where(Approval.issue_key == issue_key)
            )
            if existing:
                return existing, False
            approval = Approval(issue_key=issue_key)
            session.add(approval)
            await session.flush()
            for token in (approve, reject):
                session.add(
                    StoredDecisionToken(
                        approval_id=approval.id,
                        nonce=token.nonce,
                        action=token.action,
                        expires_at=datetime.fromtimestamp(
                            token.expires_at, tz=timezone.utc
                        ),
                    )
                )
            return approval, True

    async def record_message(
        self, issue_key: str, chat_id: int, message_id: int
    ) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(Approval)
                .where(Approval.issue_key == issue_key)
                .values(telegram_chat_id=chat_id, telegram_message_id=message_id)
            )

    async def delete_undelivered(self, approval_id: int) -> None:
        """Allow Jira to retry when no Telegram message was ever created."""
        async with self.sessions.begin() as session:
            eligible = select(Approval.id).where(
                Approval.id == approval_id,
                Approval.telegram_message_id.is_(None),
                Approval.status == ApprovalStatus.pending,
            )
            await session.execute(
                delete(StoredDecisionToken).where(
                    StoredDecisionToken.approval_id.in_(eligible)
                )
            )
            await session.execute(
                delete(Approval).where(
                    Approval.id == approval_id,
                    Approval.telegram_message_id.is_(None),
                    Approval.status == ApprovalStatus.pending,
                )
            )

    async def lookup_by_nonce(
        self, nonce: str
    ) -> tuple[Approval, StoredDecisionToken] | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(Approval, StoredDecisionToken)
                    .join(StoredDecisionToken)
                    .where(StoredDecisionToken.nonce == nonce)
                )
            ).one_or_none()
            return row if row else None

    async def claim_decision(
        self, approval_id: int, token_id: int, user_id: int
    ) -> bool:
        now = datetime.now(timezone.utc)
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(Approval)
                .where(
                    Approval.id == approval_id,
                    Approval.status.in_(
                        [ApprovalStatus.pending, ApprovalStatus.failed]
                    ),
                )
                .values(
                    status=ApprovalStatus.processing,
                    telegram_user_id=user_id,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return False
            token_result = await session.execute(
                update(StoredDecisionToken)
                .where(
                    StoredDecisionToken.id == token_id,
                    StoredDecisionToken.used_at.is_(None),
                    StoredDecisionToken.expires_at >= now,
                )
                .values(used_at=now)
            )
            if token_result.rowcount != 1:
                raise RuntimeError("Decision token cannot be claimed")
            return True

    async def finish_decision(
        self, approval_id: int, decision: str, success: bool
    ) -> None:
        now = datetime.now(timezone.utc)
        status = (
            ApprovalStatus.approved
            if success and decision == "approved"
            else ApprovalStatus.rejected
            if success
            else ApprovalStatus.failed
        )
        async with self.sessions.begin() as session:
            await session.execute(
                update(Approval)
                .where(Approval.id == approval_id)
                .values(
                    status=status,
                    decision=decision if success else None,
                    decided_at=now if success else None,
                    updated_at=now,
                )
            )
