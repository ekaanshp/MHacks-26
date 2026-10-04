"""The PDF's API contracts, validated at each public boundary."""
from __future__ import annotations

from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    evidence_ids: list[str] = Field(default_factory=list)
    email_count: int = Field(default=0, ge=0)
    first_seen: str
    last_seen: str
    active: bool = True
    sources: list[Literal["email", "bank", "family"]]
    status: Status = "open"
    assigned_to: str | None = None
    charged_since_death: float = Field(default=0, ge=0)
    # Family workspace, kept with the account so every relative (and Neon) sees the same thing.
    review: dict | None = None          # {"state": "confirmed" | "dismissed", "reason", "by", "at"}
    corrections: dict | None = None     # Family-reviewed institution/amount/category/frequency
    original: dict | None = None        # The discovered values a correction replaced (view only)
    needs_review: bool = False          # Thin evidence worth a family check (view only)
    notes: list[dict] = Field(default_factory=list)
    followups: list[dict] = Field(default_factory=list)
    documents: dict[str, str] = Field(default_factory=dict)
    outcome: dict | None = None         # Latest company answer from a call, voice or agent conversation
    added_by: str | None = None

    @model_validator(mode="after")
    def evidence_or_family(self):
        # Discovered accounts need proof; an account the family added is its own source.
        if not self.evidence_ids and "family" not in self.sources:
            raise ValueError("A discovered account needs at least one evidence ID.")
        return self


class Relative(BaseModel):
    name: str
    relationship: str
    executor: bool = False


class Persona(BaseModel):
    name: str = ""
    email: str = ""
    age: int | None = None
    city: str = ""
    date_of_death: str
    bio: str = ""
    # Synthetic multi-family demo: pronoun for display, the executor and the relatives.
    pronoun: str = "she"
    executor: str = ""
    relatives: list[Relative] = Field(default_factory=list)


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


class AgentTaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["sent", "awaiting_details", "details_sent", "completed", "pending", "rejected", "failed"]
    responder: str = Field(default="", max_length=120)
    reference_number: str | None = Field(default=None, max_length=40, pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$")
    required_documents: list[str] = Field(default_factory=list, max_length=8)
    message: str = Field(default="", max_length=1000)

    @field_validator("required_documents")
    @classmethod
    def short_documents(cls, value: list[str]) -> list[str]:
        return ClaimUpdate.short_documents(value)


class Identify(BaseModel):
    """Start-screen details: the deceased person's exact full name and the relative signing in."""
    model_config = ConfigDict(extra="forbid")
    deceased: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=60)
    relationship: str = Field(min_length=1, max_length=40)


FollowupKind = Literal["callback", "document", "reply", "deadline", "other"]
NoteKind = Literal["note", "handoff", "help"]
DocumentStatus = Literal["needed", "ready", "sent", "not_needed"]


def _clean_text(value: str, *, label: str) -> str:
    value = " ".join(value.split()) if "\n" not in value else value.strip()
    if not value or any((ord(char) < 32 and char not in "\n\t") or ord(char) == 127 for char in value):
        raise ValueError(f"Enter a {label} without control characters.")
    return value


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["confirmed", "dismissed", "open"]
    reason: str = Field(default="", max_length=300)


class Correction(BaseModel):
    """Family-reviewed details. Omitted fields keep the discovered value; reset restores them all."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    institution: str | None = Field(default=None, min_length=1, max_length=150)
    amount: float | None = Field(default=None, ge=0, le=100_000_000)
    category: Category | None = None
    frequency: Frequency | None = None
    reset: bool = False

    @field_validator("institution")
    @classmethod
    def clean_institution(cls, value: str | None) -> str | None:
        return _clean_text(value, label="company name") if value is not None else None


class NewAccount(BaseModel):
    """An account the family knows about that discovery missed."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    institution: str = Field(min_length=1, max_length=150)
    category: Category
    amount: float | None = Field(default=None, ge=0, le=100_000_000)
    frequency: Frequency = "none"
    note: str = Field(default="", max_length=1000)

    @field_validator("institution")
    @classmethod
    def clean_institution(cls, value: str) -> str:
        return _clean_text(value, label="company name")


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1500)
    kind: NoteKind = "note"
    to: str | None = Field(default=None, max_length=60)   # Hand the account to this relative

    @field_validator("text")
    @classmethod
    def clean(cls, value: str) -> str:
        return _clean_text(value, label="note")


class FollowupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: FollowupKind = "other"
    title: str = Field(min_length=1, max_length=300)
    due: Date | None = None
    reference: str | None = Field(default=None, max_length=60)

    @field_validator("title")
    @classmethod
    def clean(cls, value: str) -> str:
        return _clean_text(value, label="follow-up")


class FollowupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    done: bool


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    status: DocumentStatus

    @field_validator("name")
    @classmethod
    def clean(cls, value: str) -> str:
        return _clean_text(value, label="document name")
