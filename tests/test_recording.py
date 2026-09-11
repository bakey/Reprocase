from __future__ import annotations

import json
from pathlib import Path

from reprocase.recording.events import EventRecorder


def test_records_append_only_sequenced_events(tmp_path: Path) -> None:
    recorder = EventRecorder(tmp_path / "events.jsonl", "run_test")

    first = recorder.emit("first", source="test", payload={"value": 1})
    second = recorder.emit("second", source="test", payload={"value": 2})

    lines = [json.loads(line) for line in recorder.path.read_text().splitlines()]
    assert first.sequence == 1
    assert second.sequence == 2
    assert [line["sequence"] for line in lines] == [1, 2]
    assert [line["type"] for line in lines] == ["first", "second"]
