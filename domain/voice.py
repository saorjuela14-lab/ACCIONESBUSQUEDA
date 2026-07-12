"""Voice assistant domain models."""

from pydantic import BaseModel, Field


class VoiceCommandResult(BaseModel):
    intent: str
    success: bool = True
    speech: str
    params: dict = Field(default_factory=dict)
    ui_action: str | None = None
    data: dict | None = None


class VoiceHelpItem(BaseModel):
    phrase: str
    description: str
