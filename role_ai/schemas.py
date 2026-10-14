from typing import Literal

from pydantic import BaseModel, Field, field_validator

ProviderName = Literal["ollama", "vllm", "openai", "mock"]
SkillName = Literal["assistant", "professor", "developer", "doctor", "counselor"]


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if "@" not in value or any(char.isspace() for char in value):
            raise ValueError("이메일 형식을 확인해 주세요.")
        return value


class ConversationCreate(BaseModel):
    title: str = Field(default="새 대화", min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value):
        if not value.strip():
            raise ValueError("제목을 입력해 주세요.")
        return value.strip()


class ChatRequest(BaseModel):
    engine: Literal["provider_fallback", "openclaw"] = "provider_fallback"
    content: str = Field(min_length=1, max_length=4000)
    provider: ProviderName | None = None
    skill: SkillName = "assistant"

    @field_validator("content")
    @classmethod
    def validate_content(cls, value):
        if not value.strip():
            raise ValueError("메시지를 입력해 주세요.")
        return value.strip()
