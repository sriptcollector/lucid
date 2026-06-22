"""Local speaker identification — no tokens, no external services, no credits.

Enrolls a person's voiceprint from a short clean sample (resemblyzer speaker
embedding), then labels each transcript segment with the voice it matches. The
enrolled user is named; other speakers are clustered into Speaker 2/3/… This is
what makes "your voice" accurate instead of the AI guessing.

Audio is decoded with PyAV (bundled with faster-whisper) so MP3/Opus/WAV all
work without ffmpeg installed.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import numpy as np

from ..config import settings

_encoder = None
_lock = threading.RLock()


def _enc():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder
        _encoder = VoiceEncoder(device="cpu")
    return _encoder


def _vp_path() -> Path:
    return settings.data_path / "voiceprints.json"


def _load() -> dict:
    try:
        return json.loads(_vp_path().read_text())
    except Exception:
        return {}


def _save(d: dict) -> None:
    p = _vp_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)


def has_enrollment() -> bool:
    return bool(_load())


def enrolled_names() -> list[str]:
    return list(_load().keys())


# --------------------------------------------------------------------------- #
# audio decode (PyAV -> 16 kHz mono float32)
# --------------------------------------------------------------------------- #
def _decode_16k_mono(path: str) -> np.ndarray | None:
    import av

    try:
        container = av.open(path)
    except Exception:
        return None
    try:
        stream = container.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=16000)
        chunks = []
        for frame in container.decode(stream):
            frame.pts = None
            out = resampler.resample(frame)
            for rf in (out if isinstance(out, list) else [out]):
                if rf is not None:
                    chunks.append(rf.to_ndarray().reshape(-1))
        if not chunks:
            return None
        wav = np.concatenate(chunks).astype(np.float32)
        m = np.max(np.abs(wav))
        return wav / m if m > 0 else wav
    except Exception:
        return None
    finally:
        container.close()


# --------------------------------------------------------------------------- #
# enrollment + labeling
# --------------------------------------------------------------------------- #
def enroll(audio_path: str, name: str) -> bool:
    """Compute and store a voiceprint for `name` from a sample clip."""
    wav = _decode_16k_mono(audio_path)
    if wav is None or len(wav) < 16000:          # need ~1s of audio
        return False
    try:
        from resemblyzer import preprocess_wav
        wav = preprocess_wav(wav, source_sr=16000)   # trims silence + normalizes
    except Exception:
        pass
    if wav is None or len(wav) < 16000:
        return False
    emb = _enc().embed_utterance(wav)
    with _lock:
        prints = _load()
        prints[name] = emb.astype(float).tolist()
        _save(prints)
    return True


def remove(name: str) -> None:
    with _lock:
        prints = _load()
        prints.pop(name, None)
        _save(prints)


def label_segments(audio_path: str, segments: list, threshold: float | None = None) -> list:
    """Set `.speaker` on each segment: an enrolled name when the voice matches,
    otherwise a clustered 'Speaker N'."""
    prints = _load()
    if not prints or not segments:
        return segments
    wav = _decode_16k_mono(audio_path)
    if wav is None:
        return segments

    sr = 16000
    thr = settings.voiceid_threshold if threshold is None else threshold
    names = list(prints.keys())
    refs = np.array([prints[n] for n in names], dtype=np.float32)
    refs = refs / (np.linalg.norm(refs, axis=1, keepdims=True) + 1e-9)
    enc = _enc()

    unknown_i, unknown_e = [], []
    for i, s in enumerate(segments):
        a, b = int(max(0, s.start) * sr), int(min(len(wav), (s.end or 0) * sr))
        clip = wav[a:b]
        if len(clip) < int(0.6 * sr):            # too short to embed reliably
            s.speaker = None
            continue
        try:
            e = enc.embed_utterance(clip)
        except Exception:
            s.speaker = None
            continue
        e = e / (np.linalg.norm(e) + 1e-9)
        sims = refs @ e
        j = int(np.argmax(sims))
        if sims[j] >= thr:
            s.speaker = names[j]
        else:
            s.speaker = None
            unknown_i.append(i)
            unknown_e.append(e)

    if unknown_e:
        labels = _cluster(np.array(unknown_e))
        for idx, lab in zip(unknown_i, labels):
            segments[idx].speaker = f"Speaker {int(lab) + 2}"

    _fill_gaps(segments)
    return segments


def _cluster(embs: np.ndarray):
    n = len(embs)
    if n <= 1:
        return [0] * n
    try:
        from sklearn.cluster import AgglomerativeClustering
        cl = AgglomerativeClustering(
            n_clusters=None, distance_threshold=0.65, metric="cosine", linkage="average"
        )
        return cl.fit_predict(embs)
    except Exception:
        return [0] * n


def _fill_gaps(segments) -> None:
    """Carry a speaker label across the short/unscored gaps between turns."""
    last = None
    for s in segments:
        if s.speaker:
            last = s.speaker
        elif last:
            s.speaker = last
    nxt = None
    for s in reversed(segments):
        if s.speaker:
            nxt = s.speaker
        elif nxt:
            s.speaker = nxt
