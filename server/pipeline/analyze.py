"""The Anthropic 'smart context' analysis layer.

Takes a transcript (with timestamps + optional speakers) and uses Claude with a
forced-tool schema to produce structured intelligence: a headline + summary,
key points, topic spans, an interactable timeline, psychological/interpersonal
dynamics, action items and overall sentiment.

Prompt caching is applied to the long system prompt so re-analysis of many
recordings is cheap.
"""
from __future__ import annotations

import json

import anthropic

from ..config import settings
from ..models import (
    ActionItem,
    Analysis,
    Commitment,
    Idea,
    Person,
    Perspective,
    Plan,
    PsychSignal,
    Quote,
    Recording,
    RelationshipDynamic,
    TimelineEvent,
    Topic,
)

SYSTEM = """You are Lucid's analysis engine. You read a timestamped transcript \
of a real conversation or recording and extract a rich, accurate, structured \
understanding of it.

This output is used as a faithful working record — someone (or an AI coding \
assistant) will act on it later without hearing the audio. So be EXHAUSTIVE and \
SPECIFIC: capture every substantive idea and each person's real perspective, with \
their reasoning and the concrete details. Never flatten disagreement into \
consensus or drop a detail because it seems minor. Prefer completeness over \
brevity for ideas, perspectives, plans, and details.

Principles:
- Ground every item in the transcript. Never invent events, claims, or people.
- Timestamps you emit (the `t`, `start`, `end` fields, in seconds) MUST come \
from the bracketed [hh:mm:ss] / [mm:ss] markers in the transcript.
- ideas: THE MOST IMPORTANT SECTION. Capture EVERY distinct idea, proposal, \
concept, design, plan-of-attack, or hypothesis discussed — not just the winning \
ones. For each: a short `title`, a plain `summary` of what it is, rich `details` \
(how it would work, scope, specifics, numbers, names, tradeoffs, caveats, open \
questions), who `proposed_by` raised it, a `status` (proposed / agreed / \
rejected / open / parked), and `perspectives`: for EACH person who weighed in, \
their `stance` (proposed / supports / skeptical / against / wants to refine / \
neutral) and their `view` — what they actually think and WHY, in their own \
framing. Make perspectives clear and distinct so disagreements are visible. \
Include half-formed and tangential ideas too. Be thorough; err on capturing more.
- people: list everyone who speaks or is clearly present. For each, give a short \
stable `label` (their first name if stated, otherwise a role like \
"Ex-boyfriend"), set `name` to the same (or their real name if clearly stated), \
a one-line `role` (who they are / relationship to others), and 1-3 \
`identity_quotes` — VERBATIM lines that reveal who this person is (their role, \
values, stake, or self-description). Use the EXACT `label` string in every \
speaker / who / people field elsewhere so names stay consistent.
- plans: concrete intentions or arrangements for the future (what someone plans \
to do, where/when, next steps). Include who and the timestamp.
- commitments: explicit promises, agreements, or commitments someone makes \
("I'll…", "I promise…", "we agreed…"). Include who and the timestamp.
- relationship_dynamics: notable dynamics BETWEEN people — power/control, \
support, affection, conflict, trust, dependence. Give the people involved, a \
short `nature` (e.g. supportive, controlling, tense, warm, distrust), a \
description, and a timestamp.
- psychological_dynamics: observable interpersonal/psychological patterns \
(rapport, tension, persuasion, defensiveness, dominance, evasion, emotional \
shifts, manipulation, vulnerability, empathy). Note BOTH healthy/good patterns \
AND concerning/bad ones, and set `valence` to "positive", "negative", or \
"neutral" accordingly. Tie each to a moment, set confidence honestly (0.0-1.0). \
Describe behavior; do NOT diagnose mental-health conditions.
- The timeline should be the spine a user scrubs: 8-25 events marking the \
moments that matter (decisions, questions, topic shifts, tension, actions, key \
reveals).
- key_points: the 5-8 MOST important takeaways, ranked by importance (most \
important first). Substantive and specific — not generic restatements.
- notable_quotes: 3-6 VERBATIM quotes — the speaker's EXACT words copied from \
the transcript, never paraphrased or cleaned up. Pick the lines that are most \
revealing, decisive, emotional, surprising, or that capture someone's authentic \
voice. Attribute each to its speaker, give its timestamp, and add one short line \
on why it matters. If the transcript is in another language, quote the original \
words (the translated transcript still carries them).
- Be concise and specific. No filler."""

# Tool schema = the structured contract Claude must fill.
ANALYSIS_TOOL = {
    "name": "emit_analysis",
    "description": "Return the structured analysis of the transcript.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "One-line gist (<=12 words)."},
            "summary": {"type": "string", "description": "2-5 sentence neutral summary."},
            "sentiment": {"type": "string", "description": "Overall tone in a few words."},
            "key_points": {
                "type": "array",
                "items": {"type": "string"},
                "description": "5-8 most important takeaways, ranked (most important first).",
            },
            "notable_quotes": {
                "type": "array",
                "description": "3-6 verbatim, attributed, source-linked quotes.",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string",
                                 "description": "VERBATIM — the speaker's exact words."},
                        "speaker": {"type": "string"},
                        "t": {"type": "number", "description": "timestamp in seconds"},
                        "significance": {"type": "string",
                                         "description": "one line: why this quote matters"},
                    },
                    "required": ["text"],
                },
            },
            "speakers": {
                "type": "object",
                "description": "Map of speaker id/label -> inferred role or name.",
                "additionalProperties": {"type": "string"},
            },
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                        "summary": {"type": "string"},
                    },
                    "required": ["label", "start", "end"],
                },
            },
            "timeline": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "t": {"type": "number"},
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": ["moment", "decision", "question",
                                     "tension", "action", "topic_shift"],
                        },
                        "speaker": {"type": "string"},
                    },
                    "required": ["t", "title", "kind"],
                },
            },
            "psychological_dynamics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "t": {"type": "number"},
                        "label": {"type": "string"},
                        "observation": {"type": "string"},
                        "speaker": {"type": "string"},
                        "confidence": {"type": "number"},
                        "valence": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral"],
                            "description": "positive = healthy/good, negative = concerning/bad",
                        },
                    },
                    "required": ["t", "label", "observation"],
                },
            },
            "ideas": {
                "type": "array",
                "description": "EVERY distinct idea/proposal/concept discussed, with each person's perspective + reasoning. Be exhaustive.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "short name of the idea"},
                        "summary": {"type": "string", "description": "what the idea is, plainly"},
                        "details": {"type": "string",
                                    "description": "specifics: how it works, scope, numbers, tradeoffs, caveats, open questions"},
                        "proposed_by": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["proposed", "agreed", "rejected", "open", "parked"],
                        },
                        "perspectives": {
                            "type": "array",
                            "description": "each person who weighed in: their stance + their actual view/reasoning",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "person": {"type": "string"},
                                    "stance": {
                                        "type": "string",
                                        "enum": ["proposed", "supports", "skeptical",
                                                 "against", "wants to refine", "neutral"],
                                    },
                                    "view": {"type": "string",
                                             "description": "what they think and WHY, in their framing"},
                                },
                                "required": ["person", "view"],
                            },
                        },
                        "t": {"type": "number"},
                    },
                    "required": ["title", "summary"],
                },
            },
            "people": {
                "type": "array",
                "description": "Everyone present; names are user-editable later.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "short stable handle"},
                        "name": {"type": "string"},
                        "role": {"type": "string"},
                        "identity_quotes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                    "t": {"type": "number"},
                                    "significance": {"type": "string"},
                                },
                                "required": ["text"],
                            },
                        },
                    },
                    "required": ["label"],
                },
            },
            "plans": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "who": {"type": "string"},
                        "t": {"type": "number"},
                    },
                    "required": ["text"],
                },
            },
            "commitments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "who": {"type": "string"},
                        "t": {"type": "number"},
                    },
                    "required": ["text"],
                },
            },
            "relationship_dynamics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "people": {"type": "string"},
                        "nature": {"type": "string"},
                        "description": {"type": "string"},
                        "t": {"type": "number"},
                    },
                    "required": ["description"],
                },
            },
            "action_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "owner": {"type": "string"},
                        "due": {"type": "string"},
                        "t": {"type": "number"},
                    },
                    "required": ["text"],
                },
            },
        },
        "required": ["headline", "summary", "key_points", "timeline"],
    },
}


def analyze(rec: Recording) -> Analysis:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    transcript = rec.full_text_translated or rec.full_text
    if not transcript.strip():
        return Analysis(summary="(empty transcript)")

    duration = f"{rec.duration:.0f}s" if rec.duration else "unknown"
    user_msg = (
        f"Recording duration: {duration}. Language: {rec.language or 'unknown'}.\n"
        f"Number of segments: {len(rec.segments)}.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )

    msg = client.messages.create(
        model=settings.analysis_model,
        max_tokens=16000,
        system=[
            {"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}
        ],
        tools=[ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "emit_analysis"},
        messages=[{"role": "user", "content": user_msg}],
    )

    data = _extract_tool_input(msg)
    return _to_analysis(data)


def _extract_tool_input(msg) -> dict:
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_analysis":
            return block.input
    # Fallback: try to parse any text block as JSON.
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except Exception:
                pass
    return {}


def _person(p: dict) -> Person:
    label = p.get("label") or p.get("name") or "Someone"
    return Person(
        label=label,
        name=p.get("name") or label,
        role=p.get("role", ""),
        identity_quotes=[Quote(**q) for q in p.get("identity_quotes", []) or []],
    )


def _idea(i: dict) -> Idea:
    persps = []
    for p in i.get("perspectives", []) or []:
        persps.append(Perspective(
            person=p.get("person", ""),
            stance=p.get("stance", ""),
            view=p.get("view", ""),
        ))
    return Idea(
        title=i.get("title", "") or "Idea",
        summary=i.get("summary", ""),
        details=i.get("details", ""),
        proposed_by=i.get("proposed_by", ""),
        status=i.get("status", ""),
        perspectives=persps,
        t=i.get("t"),
    )


def _to_analysis(d: dict) -> Analysis:
    return Analysis(
        headline=d.get("headline", ""),
        summary=d.get("summary", ""),
        sentiment=d.get("sentiment", ""),
        key_points=d.get("key_points", []) or [],
        notable_quotes=[Quote(**q) for q in d.get("notable_quotes", []) or []],
        people=[_person(p) for p in d.get("people", []) or []],
        ideas=[_idea(i) for i in d.get("ideas", []) or []],
        plans=[Plan(**p) for p in d.get("plans", []) or []],
        commitments=[Commitment(**c) for c in d.get("commitments", []) or []],
        relationship_dynamics=[
            RelationshipDynamic(**r) for r in d.get("relationship_dynamics", []) or []
        ],
        speakers=d.get("speakers", {}) or {},
        topics=[Topic(**t) for t in d.get("topics", []) or []],
        timeline=[TimelineEvent(**e) for e in d.get("timeline", []) or []],
        psychological_dynamics=[
            PsychSignal(**p) for p in d.get("psychological_dynamics", []) or []
        ],
        action_items=[ActionItem(**a) for a in d.get("action_items", []) or []],
    )
