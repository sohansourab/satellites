"""AI chat endpoint."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from .. import ai

router = APIRouter(prefix="/api", tags=["assistant"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question must not be blank")
        return v.strip()

    @field_validator("history")
    @classmethod
    def _valid_roles(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        bad = [m for m in v if m.role not in {"user", "assistant"}]
        if bad:
            raise ValueError("history messages must have role 'user' or 'assistant'")
        return v


@router.get("/assistant/status")
def assistant_status() -> dict[str, Any]:
    return ai.ai_status()


@router.post("/chat")
def chat(req: ChatRequest) -> dict[str, Any]:
    result = ai.chat(
        req.question,
        history=[{"role": m.role, "content": m.content} for m in req.history],
    )
    return {"question": req.question, **result}
