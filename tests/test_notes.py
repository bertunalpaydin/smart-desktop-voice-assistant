from datetime import datetime

from jarvis import notes


def test_notes_are_added_and_the_latest_read_back(tmp_path):
    path = tmp_path / "notes.txt"
    assert notes.recent(path) == []
    for i in range(7):
        notes.add(path, f"note {i}", datetime(2026, 10, 1, 9, i))
    assert notes.recent(path, 3) == ["note 4", "note 5", "note 6"]
    assert "2026-10-01 09:06  note 6" in path.read_text(encoding="utf-8")
