"""Voice assistant domain models."""

from pydantic import BaseModel, Field


class VoiceCommandResult(BaseModel):
    intent: str
    success: bool = True
    speech: str
    params: dict = Field(default_factory=dict)
    ui_action: str | None = None
    data: dict | None = None
    requires_confirmation: bool = False
    pending_action: dict | None = None


class VoiceHelpItem(BaseModel):
    phrase: str
    description: str


class VoiceChatMessage(BaseModel):
    role: str  # user | assistant | system
    content: str


class VoiceChatResult(BaseModel):
    speech: str
    success: bool = True
    mode: str = "chat"  # chat | command | fallback
    assistant_name: str = "Viernes"
    ui_action: str | None = None
    ui_actions: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    pending_action: dict | None = None
    tools_used: list[str] = Field(default_factory=list)
    data: dict | None = None
    session_id: str = ""
    messages: list[VoiceChatMessage] = Field(default_factory=list)
