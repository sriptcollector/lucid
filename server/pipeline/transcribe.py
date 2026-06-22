"""Pluggable speech-to-text.

Three interchangeable backends behind one `transcribe()` function. Each returns
(segments, detected_language). Backends are lazy-imported so you only need the
dependency for the one you actually use.

  faster_whisper  local, open-source, free, runs on CPU or GPU      (default)
  openai          OpenAI Whisper API — high accuracy, low setup
  deepgram        streaming/batch w/ strong diarization + 100+ langs
"""
from __future__ import annotations

import os
from pathlib import Path

from ..config import settings
from ..models import Segment


def transcribe(audio_file: Path) -> tuple[list[Segment], str | None]:
    backend = settings.transcribe_backend
    if backend == "faster_whisper":
        return _faster_whisper(audio_file)
    if backend == "openai":
        return _openai(audio_file)
    if backend == "deepgram":
        return _deepgram(audio_file)
    raise ValueError(f"Unknown TRANSCRIBE_BACKEND: {backend}")


# --------------------------------------------------------------------------- #
# faster-whisper (local, default)
# --------------------------------------------------------------------------- #
_fw_model = None
_cuda_dlls_ready = False


def _ensure_cuda_dlls() -> None:
    """faster-whisper/CTranslate2 on Windows needs the CUDA runtime libraries
    (cuBLAS, cuDNN, cudart) on PATH — add_dll_directory alone isn't enough.
    They ship as pip wheels under site-packages/nvidia/*/bin."""
    global _cuda_dlls_ready
    if _cuda_dlls_ready:
        return
    try:
        import glob
        import nvidia
        base = list(nvidia.__path__)[0]
        bins = glob.glob(os.path.join(base, "**", "bin"), recursive=True)
        if bins:
            os.environ["PATH"] = os.pathsep.join(bins) + os.pathsep + os.environ.get("PATH", "")
            for d in bins:
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass
    except Exception:
        pass
    _cuda_dlls_ready = True


def _cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


_fw_batched = None
_fw_device = "cpu"


def _load_model() -> None:
    """Load large-v3 once. On GPU we wrap it in a BatchedInferencePipeline,
    which is several times faster on long recordings."""
    global _fw_model, _fw_batched, _fw_device
    device = settings.whisper_device
    compute = settings.whisper_compute_type
    if device == "auto":
        device = "cuda" if _cuda_available() else "cpu"
    if device == "cuda":
        _ensure_cuda_dlls()
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"

    from faster_whisper import WhisperModel, BatchedInferencePipeline
    print(f"[whisper] loading {settings.whisper_model} on {device}/{compute} …", flush=True)
    _fw_model = WhisperModel(settings.whisper_model, device=device, compute_type=compute)
    _fw_device = device
    if device == "cuda":
        try:
            _fw_batched = BatchedInferencePipeline(model=_fw_model)
        except Exception as exc:  # noqa: BLE001
            print(f"[whisper] batched pipeline unavailable ({exc}); using sequential", flush=True)
            _fw_batched = None
    print(f"[whisper] ready on {_fw_device}"
          + (" (batched)" if _fw_batched else ""), flush=True)


def _faster_whisper(audio_file: Path) -> tuple[list[Segment], str | None]:
    if _fw_model is None:
        _load_model()

    if _fw_batched is not None:
        # Fast path: GPU batched. VAD-chunks the audio and runs chunks in
        # parallel batches — big speedup on long recordings.
        segments_iter, info = _fw_batched.transcribe(
            str(audio_file),
            batch_size=settings.whisper_batch_size,
            beam_size=settings.whisper_beam_size,
            language=settings.whisper_language or None,
            vad_filter=settings.whisper_vad,
        )
    else:
        # Sequential (CPU) path: high-fidelity decoding with sampling fallback.
        segments_iter, info = _fw_model.transcribe(
            str(audio_file),
            beam_size=settings.whisper_beam_size,
            best_of=5,
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
            condition_on_previous_text=True,
            vad_filter=settings.whisper_vad,
            language=settings.whisper_language or None,
            initial_prompt=settings.whisper_prompt or None,
        )

    tag = settings.whisper_tag_nonspeech
    segments: list[Segment] = []
    for s in segments_iter:
        text = s.text.strip()
        if not text:
            continue
        # Flag chunks Whisper thinks aren't speech (singing / music / ambient).
        if tag and getattr(s, "no_speech_prob", 0.0) > 0.6:
            text = "♪ " + text
        segments.append(Segment(start=float(s.start), end=float(s.end), text=text))
    return segments, info.language


# --------------------------------------------------------------------------- #
# OpenAI Whisper API
# --------------------------------------------------------------------------- #
def _openai(audio_file: Path) -> tuple[list[Segment], str | None]:
    from openai import OpenAI  # lazy import

    client = OpenAI(api_key=settings.openai_api_key)
    with audio_file.open("rb") as fh:
        resp = client.audio.transcriptions.create(
            model="whisper-1",
            file=fh,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    def attr(s, key):  # SDK returns objects in verbose_json; be tolerant of dicts too
        return s[key] if isinstance(s, dict) else getattr(s, key)

    segs = [
        Segment(start=float(attr(s, "start")), end=float(attr(s, "end")),
                text=attr(s, "text").strip())
        for s in (resp.segments or [])
        if attr(s, "text").strip()
    ]
    return segs, getattr(resp, "language", None)


# --------------------------------------------------------------------------- #
# Deepgram (best built-in diarization)
# --------------------------------------------------------------------------- #
def _deepgram(audio_file: Path) -> tuple[list[Segment], str | None]:
    from deepgram import DeepgramClient, PrerecordedOptions, FileSource  # lazy

    dg = DeepgramClient(settings.deepgram_api_key)
    with audio_file.open("rb") as fh:
        payload: FileSource = {"buffer": fh.read()}
    options = PrerecordedOptions(
        model="nova-2",
        smart_format=True,
        diarize=True,
        punctuate=True,
        detect_language=True,
        utterances=True,
    )
    resp = dg.listen.rest.v("1").transcribe_file(payload, options)
    data = resp.to_dict()

    segs: list[Segment] = []
    for utt in data["results"].get("utterances", []):
        spk = utt.get("speaker")
        segs.append(
            Segment(
                start=float(utt["start"]),
                end=float(utt["end"]),
                text=utt["transcript"].strip(),
                speaker=f"Speaker {spk}" if spk is not None else None,
            )
        )
    lang = (
        data["results"]["channels"][0].get("detected_language")
        if data["results"].get("channels")
        else None
    )
    return segs, lang
