from __future__ import annotations

from dataclasses import dataclass

from application.supervisor import ChatResult, SupervisorAgent
from domain.errors import DomainError
from domain.ports import UserContext

MAX_MESSAGE_CHARS = 2000


@dataclass
class HandleChatUseCase:
    supervisor: SupervisorAgent

    async def execute(self, message: str, ctx: UserContext) -> ChatResult:
        message = message.strip()
        if not message:
            raise DomainError("Empty message")
        if len(message) > MAX_MESSAGE_CHARS:
            raise DomainError("Message too long")
        return await self.supervisor.handle(message, ctx)
