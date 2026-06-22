"""Translation of transcript segments into a target language.

Two strategies:
  whisper  Re-run Whisper in translate mode (English target only). Fast, free,
           but English-only and re-decodes the audio.
  claude   Batch-translate the already-transcribed text with Claude into ANY
           language, preserving segment boundaries. Default — highest quality,
           works for the timeline/translator use case.

Segments are translated in place: each Segment.text_translated is populated.
"""
from __future__ import annotations

import json

from ..config import settings
from ..models import Segment


def translate(segments: list[Segment], source_lang: str | None) -> list[Segment]:
    target = settings.translate_target
    if not target or not segments:
        return segments
    # Skip if already in target language (best-effort check).
    if source_lang and target.lower().startswith(source_lang.lower()[:2]):
        return segments

    if settings.translate_backend == "claude":
        return _claude_translate(segments, target)
    # whisper backend translates at transcription time; nothing to do here.
    return segments


def _claude_translate(segments: list[Segment], target: str) -> list[Segment]:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # Translate in batches to stay well within context and keep alignment.
    BATCH = 80
    for i in range(0, len(segments), BATCH):
        chunk = segments[i : i + BATCH]
        numbered = [{"i": j, "text": s.text} for j, s in enumerate(chunk)]
        msg = client.messages.create(
            model=settings.analysis_model,
            max_tokens=8000,
            system=(
                f"You are a professional translator. Translate each item's text "
                f"into {target}. Preserve meaning, tone, names and numbers. "
                f"Return ONLY a JSON array of objects {{\"i\": int, \"text\": str}} "
                f"with the same indices. No commentary."
            ),
            messages=[{"role": "user", "content": json.dumps(numbered, ensure_ascii=False)}],
        )
        text = _first_text(msg)
        try:
            out = json.loads(_strip_fences(text))
            by_i = {o["i"]: o["text"] for o in out}
            for j, seg in enumerate(chunk):
                seg.text_translated = by_i.get(j, seg.text)
        except Exception:
            # On a parse failure, fall back to original text for this batch.
            for seg in chunk:
                seg.text_translated = seg.text
    return segments


def _first_text(msg) -> str:
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: -3]
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    return s.strip()
