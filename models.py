"""The PDF's API contracts, validated at each public boundary."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Category = Literal["subscription", "utility", "bank", "investment", "pension", "insurance", "crypto", "payment_app", "government", "debt", "digital_legacy"]
Action = Literal["cancel", "transfer", "claim", "notify", "memorialize"]
Frequency = Literal["monthly", "annual", "balance", "one_time", "none"]
Status = Literal["open", "in_progress", "done"]

BUCKETS = {"subscription": "leaving", "utility": "leaving", "bank": "waiting", "investment": "waiting", "pension": "notify", "insurance": "waiting", "crypto": "waiting", "payment_app": "waiting", "government": "notify", "debt": "owed", "digital_legacy": "legacy"}
ACTIONS = {"subscription": "cancel", "utility": "transfer", "bank": "claim", "investment": "claim", "pension": "notify", "insurance": "claim", "crypto": "claim", "payment_app": "claim", "government": "notify", "debt": "notify", "digital_legacy": "memorialize"}


class Account(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    id: str
    institution: str = Field(min_length=1, max_length=150)
    category: Category
    action: Action
    bucket: Literal["leaving", "waiting", "notify", "owed", "legacy"]
    amount: float | None = Field(default=None, ge=0)
    frequency: Frequency = "none"
    next_date: str | None = None
    days_until: int | None = None
    urgent: bool = False
    why_it_matters: str
    evidence_ids: list[str] = Field(min_length=1)
    email_count: int = Field(default=0, ge=0)
    first_seen: str
    last_seen: str
    active: bool = True
    sources: list[Literal["email", "bank"]]
    status: Status = "open"
    assigned_to: str | None = None


class Persona(BaseModel):
    name: str = ""
    email: str = ""
    age: int | None = None
    city: str = ""
    date_of_death: str
    bio: str = ""


class Stats(BaseModel):
    emails: int
    candidates: int
    senders: int
    accounts: int


class Totals(BaseModel):
    monthly_drain: float
    charged_since_death: float
    assets_found: float
    debts_found: float
    death_certificates: int
    accounts: int


class Estate(BaseModel):
    persona: Persona
    today: str
    stats: Stats
    totals: Totals
    accounts: list[Account]
    estate_id: int | None = None
    analysis: dict | None = None


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status | None = None
    assigned_to: str | None = Field(default=None, max_length=80)

    @field_validator("assigned_to")
    @classmethod
    def clean_assignment(cls, value: str | None) -> str | None:
        if value is not None and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("An assignment must not contain control characters.")
        return value.strip() or None if value is not None else None


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Please enter a question.")
        return value.strip()


class CallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_number: str = Field(min_length=9, max_length=16, pattern=r"^\+[1-9][0-9]{7,14}$")


class VoiceConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_-]+$")


class ClaimUpdate(BaseModel):
    """A report from Lastly's agent about one relayed claim."""
    model_config = ConfigDict(extra="forbid")
    status: Literal["sent", "opened", "rejected", "failed"]
    responder: str = Field(default="", max_length=120)
    claim_number: str | None = Field(default=None, max_length=40, pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$")
    required_documents: list[str] = Field(default_factory=list, max_length=8)
    message: str = Field(default="", max_length=600)

    @field_validator("required_documents")
    @classmethod
    def short_documents(cls, value: list[str]) -> list[str]:
        if any(not isinstance(item, str) or not item.strip() or len(item) > 200 for item in value):
            raise ValueError("Each required document must be 1-200 characters.")
        return [item.strip() for item in value]
