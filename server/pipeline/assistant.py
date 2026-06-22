"""In-app AI assistant scoped to a single recording.

Answers questions, explains things WITH evidence (verbatim quotes + timestamps
the UI can play), and can edit people's names. Uses a forced tool so the reply
is always structured: {answer, quotes[], edits[]}. The transcript is sent with
prompt caching so multi-turn chat stays cheap.
"""
from __future__ import annotations

import anthropic

from ..config import settings

SYSTEM = """You are Lucid's assistant for ONE recording. Help the user understand \
it, explain things with evidence, and edit people's names when asked.

Rules:
- Ground every claim in the transcript. Never invent quotes, timestamps, or events.
- When you explain or assert anything substantive, attach 1-3 VERBATIM quotes as \
proof — the speaker's exact words, the timestamp (seconds), and who said it.
- If the user asks to rename someone or fix a name ("the friend is actually Sam", \
"call her Maya"), add an edit {from, to} where `from` is that person's CURRENT \
name/label exactly as it appears, and `to` is the new name.
- Be concise, clean, and direct. No filler, no hedging."""

TOOL = {
    "name": "reply",
    "description": "Reply to the user with an answer, supporting quotes, and any edits.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "clean, concise answer/explanation"},
            "quotes": {
                "type": "array",
                "description": "0-3 verbatim quotes that prove the answer",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "exact words"},
                        "t": {"type": "number", "description": "timestamp in seconds"},
                        "speaker": {"type": "string"},
                    },
                    "required": ["text"],
                },
            },
            "edits": {
                "type": "array",
                "description": "name changes to apply",
                "items": {
                    "type": "object",
                    "properties": {"from": {"type": "string"}, "to": {"type": "string"}},
                    "required": ["from", "to"],
                },
            },
        },
        "required": ["answer"],
    },
}


def respond(rec, message: str, history: list[dict]) -> dict:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    transcript = rec.full_text_translated or rec.full_text
    a = rec.analysis
    people = "; ".join(
        f"{p.name or p.label} — {p.role}" for p in (a.people if a else [])
    ) or "(not yet identified)"
    context = (
        f"PEOPLE: {people}\n\n"
        f"TRANSCRIPT (each line prefixed with its [hh:mm:ss] or [mm:ss] start time):\n"
        f"{transcript}"
    )

    msgs: list[dict] = []
    for m in (history or [])[-8:]:
        role, content = m.get("role"), m.get("content")
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": message})

    resp = client.messages.create(
        model=settings.analysis_model,
        max_tokens=2000,
        system=[
            {"type": "text", "text": SYSTEM},
            {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
        ],
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "reply"},
        messages=msgs,
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "reply":
            out = block.input
            return {
                "answer": out.get("answer", ""),
                "quotes": out.get("quotes", []) or [],
                "edits": out.get("edits", []) or [],
            }
    return {"answer": "(no response)", "quotes": [], "edits": []}
