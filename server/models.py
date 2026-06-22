"""Data models shared across the pipeline and API."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Status(str, Enum):
    QUEUED = "queued"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    ANALYZING = "analyzing"
    DONE = "done"
    ERROR = "error"


class Segment(BaseModel):
    """A contiguous chunk of transcript with timing + optional speaker."""
    start: float                       # seconds
    end: float
    text: str
    speaker: Optional[str] = None
    text_translated: Optional[str] = None


class TimelineEvent(BaseModel):
    """A point on the interactable timeline produced by the analysis layer."""
    t: float                           # seconds into the recording
    title: str
    detail: str = ""
    kind: str = "moment"               # moment | decision | question | tension | action | topic_shift
    speaker: Optional[str] = None


class Topic(BaseModel):
    label: str
    start: float
    end: float
    summary: str = ""


class PsychSignal(BaseModel):
    """A psychological / interpersonal dynamic observed in the conversation."""
    t: float
    label: str                         # e.g. "rapport", "defensiveness", "persuasion attempt"
    observation: str
    speaker: Optional[str] = None
    confidence: float = 0.5
    valence: str = "neutral"           # positive (healthy) | negative (concerning) | neutral


class Plan(BaseModel):
    """Something someone intends to do / an arrangement going forward."""
    text: str
    who: Optional[str] = None
    t: Optional[float] = None


class Commitment(BaseModel):
    """An explicit promise / agreement someone makes."""
    text: str
    who: Optional[str] = None
    t: Optional[float] = None


class RelationshipDynamic(BaseModel):
    """A notable dynamic between people (power, support, conflict, trust…)."""
    people: str = ""                   # e.g. "Andrew & Maya"
    nature: str = ""                   # supportive | tense | controlling | warm | distrust | …
    description: str = ""
    t: Optional[float] = None


class ActionItem(BaseModel):
    text: str
    owner: Optional[str] = None
    due: Optional[str] = None
    t: Optional[float] = None


class Quote(BaseModel):
    """A verbatim, attributed, source-linked quote — the speaker's exact words."""
    text: str                          # VERBATIM, not paraphrased
    speaker: Optional[str] = None
    t: Optional[float] = None           # seconds, links back to the audio
    significance: str = ""              # one line: why this moment matters


class Perspective(BaseModel):
    """One person's stance on an idea — what they think and WHY."""
    person: str = ""                   # who holds this view
    stance: str = ""                   # proposed | supports | skeptical | against | wants to refine | neutral
    view: str = ""                     # their actual perspective + reasoning, in their framing


class Idea(BaseModel):
    """A concrete idea / proposal / concept discussed, with who thinks what."""
    title: str                         # short name of the idea
    summary: str = ""                  # what the idea IS, plainly
    details: str = ""                  # specifics: how it would work, scope, caveats, numbers
    proposed_by: str = ""              # who raised it
    status: str = ""                   # proposed | agreed | rejected | open | parked
    perspectives: list["Perspective"] = Field(default_factory=list)
    t: Optional[float] = None


class Person(BaseModel):
    """A participant the analysis believes is present."""
    label: str                         # stable handle used across the analysis
    name: str = ""                     # display name (user-editable)
    role: str = ""                     # who they are / relationship to others
    identity_quotes: list[Quote] = Field(default_factory=list)  # quotes revealing who they are


class Analysis(BaseModel):
    """The full smart-context output of the Anthropic layer."""
    summary: str = ""
    headline: str = ""
    people: list[Person] = Field(default_factory=list)
    ideas: list[Idea] = Field(default_factory=list)
    plans: list[Plan] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list)
    relationship_dynamics: list[RelationshipDynamic] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    notable_quotes: list[Quote] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    psychological_dynamics: list[PsychSignal] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    sentiment: str = ""                # overall tone
    speakers: dict[str, str] = Field(default_factory=dict)  # id -> inferred role/name


class Recording(BaseModel):
    id: str
    filename: str
    source: str = "usb"                # usb | upload | plaud_api | telegram
    status: Status = Status.QUEUED
    error: Optional[str] = None
    created_at: str = ""
    notify_chat: Optional[str] = None  # Telegram chat to reply to (overrides default)
    duration: Optional[float] = None
    language: Optional[str] = None
    segments: list[Segment] = Field(default_factory=list)
    analysis: Optional[Analysis] = None

    @property
    def full_text(self) -> str:
        return "\n".join(
            f"[{_ts(s.start)}] {s.speaker + ': ' if s.speaker else ''}{s.text}"
            for s in self.segments
        )

    @property
    def full_text_translated(self) -> str:
        return "\n".join(
            f"[{_ts(s.start)}] {s.speaker + ': ' if s.speaker else ''}"
            f"{s.text_translated or s.text}"
            for s in self.segments
        )


def _ts(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
