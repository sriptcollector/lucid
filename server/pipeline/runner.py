"""Orchestrates a recording through the full pipeline and persists each stage.

    transcribe -> translate -> analyze -> notify

Runs in a background thread (see main.py). Every stage updates the DB so the
web UI can show live status.
"""
from __future__ import annotations

import traceback
import wave
from pathlib import Path

from .. import storage
from ..config import settings
from ..models import Recording, Status
from ..notify import telegram
from . import analyze, directory, transcribe, translate


def process(rec_id: str) -> None:
    rec = storage.get(rec_id)
    if not rec:
        return
    audio = Path(rec.filename)
    try:
        rec.duration = _duration(audio)
        storage.save(rec)

        # 1) Transcribe
        storage.update_status(rec_id, Status.TRANSCRIBING)
        segments, lang = transcribe.transcribe(audio)
        rec = storage.get(rec_id)
        rec.segments = segments
        rec.language = lang
        # Fall back to transcript end time when container duration is unknown
        # (e.g. MP3/Opus from Plaud, which wave can't measure).
        if not rec.duration and segments:
            rec.duration = segments[-1].end
        storage.save(rec)

        # 1b) Speaker ID — label the enrolled user + cluster others
        try:
            from . import voiceid
            if settings.voiceid_enabled and voiceid.has_enrollment():
                rec.segments = voiceid.label_segments(str(audio), rec.segments)
                storage.save(rec)
        except Exception:
            pass

        # 2) Translate
        storage.update_status(rec_id, Status.TRANSLATING)
        rec = storage.get(rec_id)
        rec.segments = translate.translate(rec.segments, rec.language)
        storage.save(rec)

        # 3) Analyze (Anthropic smart context)
        storage.update_status(rec_id, Status.ANALYZING)
        rec = storage.get(rec_id)
        rec.analysis = analyze.analyze(rec)
        rec.status = Status.DONE

        # 3b) Recognise known people: auto-fill names the directory has learned,
        # then teach the directory everything new in this recording.
        try:
            directory.apply_known_names(rec)
        except Exception:
            pass
        storage.save(rec)
        try:
            directory.learn_from_recording(rec)
        except Exception:
            pass

        # 3c) Client manager: link people to CRM clients + log the note back.
        try:
            from ..integrations import notion_crm
            notion_crm.link_and_push(rec)
        except Exception:
            pass

        # 4) Notify
        telegram.notify_done(rec)

    except Exception as exc:  # noqa: BLE001
        storage.update_status(
            rec_id, Status.ERROR, error=f"{exc}\n{traceback.format_exc()}"
        )


def _duration(audio: Path) -> float | None:
    """Best-effort duration. WAV is read natively; other formats are left to
    the transcriber to report (returns None here)."""
    if audio.suffix.lower() == ".wav":
        try:
            with wave.open(str(audio), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except Exception:
            return None
    return None
