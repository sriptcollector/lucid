"""Recording (de)serialization and derived text."""
from __future__ import annotations

from server.models import Analysis, Person, Recording, Segment, Status


def test_recording_json_roundtrip():
    rec = Recording(
        id="abc",
        filename="a.wav",
        source="upload",
        status=Status.DONE,
        created_at="2026-01-01",
        segments=[Segment(start=0, end=2, text="hi", speaker="Me")],
        analysis=Analysis(headline="H", summary="S", people=[Person(label="p1", name="Alex")]),
    )
    back = Recording.model_validate_json(rec.model_dump_json())
    assert back.id == "abc"
    assert back.status is Status.DONE
    assert back.analysis.headline == "H"
    assert back.analysis.people[0].name == "Alex"
    assert back.segments[0].text == "hi"


def test_status_serializes_to_value():
    rec = Recording(id="x", filename="a.wav", status=Status.ANALYZING)
    assert '"analyzing"' in rec.model_dump_json()


def test_full_text_formats_timestamps_and_speakers():
    rec = Recording(
        id="x",
        filename="a.wav",
        segments=[
            Segment(start=0, end=1, text="hello", speaker="Me"),
            Segment(start=65, end=70, text="world"),
        ],
    )
    ft = rec.full_text
    assert "Me: hello" in ft
    assert "[00:00]" in ft
    assert "[01:05]" in ft   # 65s -> 01:05
