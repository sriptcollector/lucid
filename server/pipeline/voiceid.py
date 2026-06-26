"""Local speaker identification — no tokens, no external services, no credits.

Enrolls a person's voice as a **profile of several embeddings** (resemblyzer
speaker embeddings), then labels each transcript segment with the voice it
matches. Accuracy comes from four things working together:

  • **Multi-embedding profiles** — a voice is stored as many reference vectors
    (from different moments/conditions), not one averaged blur. Each finished
    recording adds more, so recognition keeps improving.
  • **Best-of-references scoring** — a segment scores against a person as its
    closest reference, which tolerates natural variation in how someone sounds.
  • **Margin gating** — the top candidate must beat the runner-up by a margin,
    so look-alike voices aren't confidently confused.
  • **Windowing + smoothing** — very short clips are skipped (then filled from
    context) and lone single-segment flips are repaired.

Other speakers are clustered into Speaker 2/3/…. Audio is decoded with PyAV
(bundled with faster-whisper) so MP3/Opus/WebM/WAV all work without ffmpeg.

Voiceprint store (data/voiceprints.json): ``{name: [emb, emb, ...]}``. The old
``{name: emb}`` single-vector format is read transparently and upgraded on the
next write.
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
_MAX_REFS = 16          # references kept per person (most recent win)


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
# profile helpers (on-disk value -> list of unit vectors)
# --------------------------------------------------------------------------- #
def _as_vecs(val) -> list[list[float]]:
    """Normalize a stored value into a list-of-embeddings, old format included."""
    if not isinstance(val, list) or not val:
        return []
    if isinstance(val[0], (int, float)):     # old single-embedding format
        return [val]
    return [v for v in val if isinstance(v, list) and v]


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def _profiles() -> dict[str, np.ndarray]:
    """{name: ndarray (k, dim)} of unit reference vectors."""
    out: dict[str, np.ndarray] = {}
    for name, val in _load().items():
        vecs = _as_vecs(val)
        if vecs:
            arr = np.array(vecs, dtype=np.float32)
            arr = arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)
            out[name] = arr
    return out


def add_reference(name: str, emb) -> None:
    """Append one more reference embedding to ``name``'s profile (capped)."""
    if not name:
        return
    with _lock:
        d = _load()
        vecs = _as_vecs(d.get(name, []))
        vecs.append(_unit(emb).astype(float).tolist())
        d[name] = vecs[-_MAX_REFS:]
        _save(d)


def remove(name: str) -> None:
    with _lock:
        d = _load()
        d.pop(name, None)
        _save(d)


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
# enrollment
# --------------------------------------------------------------------------- #
def _window_embeddings(wav: np.ndarray, sr: int = 16000,
                       win: float = 5.0, hop: float = 3.0, maxn: int = 8) -> list[np.ndarray]:
    """Several embeddings sampled across a clip — a richer profile than one
    averaged vector, capturing how the voice varies over the sample."""
    embs: list[np.ndarray] = []
    w, hp, n = int(win * sr), int(hop * sr), len(wav)
    i = 0
    enc = _enc()
    while i < n and len(embs) < maxn:
        clip = wav[i:i + w]
        if len(clip) >= int(1.5 * sr):
            try:
                embs.append(enc.embed_utterance(clip))
            except Exception:
                pass
        i += hp
    return embs


def enroll(audio_path: str, name: str) -> bool:
    """Add ``name``'s voice from a sample clip. Multiple calls accumulate (each
    sample makes the profile stronger). ~30s of clean speech is ideal."""
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
    embs = _window_embeddings(wav)
    if not embs:
        try:
            embs = [_enc().embed_utterance(wav)]
        except Exception:
            return False
    with _lock:
        d = _load()
        vecs = _as_vecs(d.get(name, []))
        vecs.extend(_unit(e).astype(float).tolist() for e in embs)
        d[name] = vecs[-_MAX_REFS:]
        _save(d)
    return True


# --------------------------------------------------------------------------- #
# labeling
# --------------------------------------------------------------------------- #
def label_segments(audio_path: str, segments: list, threshold: float | None = None) -> list:
    """Set ``.speaker`` on each segment: an enrolled name when the voice matches
    confidently (and unambiguously), otherwise a clustered 'Speaker N'."""
    profs = _profiles()
    if not profs or not segments:
        return segments
    wav = _decode_16k_mono(audio_path)
    if wav is None:
        return segments

    sr = 16000
    thr = settings.voiceid_threshold if threshold is None else threshold
    margin = settings.voiceid_margin
    names = list(profs.keys())
    enc = _enc()

    unknown_i, unknown_e = [], []
    for idx, s in enumerate(segments):
        a, b = int(max(0, s.start) * sr), int(min(len(wav), (s.end or 0) * sr))
        clip = wav[a:b]
        if len(clip) < int(0.6 * sr):            # too short to embed reliably
            s.speaker = None
            continue
        try:
            e = _unit(enc.embed_utterance(clip))
        except Exception:
            s.speaker = None
            continue
        # score per person = similarity to its CLOSEST reference
        scores = np.array([float(np.max(profs[nm] @ e)) for nm in names])
        order = np.argsort(scores)[::-1]
        best = scores[order[0]]
        second = scores[order[1]] if len(order) > 1 else -1.0
        if best >= thr and (best - second) >= margin:
            s.speaker = names[order[0]]
        else:
            s.speaker = None
            unknown_i.append(idx)
            unknown_e.append(e)

    if unknown_e:
        labels = _cluster(np.array(unknown_e))
        for idx, lab in zip(unknown_i, labels):
            segments[idx].speaker = f"Speaker {int(lab) + 2}"

    _smooth(segments)
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


def _smooth(segments) -> None:
    """Repair lone single-segment flips: a short segment wedged between two
    segments that agree is almost always a misfire — adopt the neighbours."""
    for i in range(1, len(segments) - 1):
        a, c = segments[i - 1].speaker, segments[i + 1].speaker
        s = segments[i]
        dur = (s.end or 0) - (s.start or 0)
        if a and a == c and s.speaker != a and dur < 1.5:
            s.speaker = a


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
