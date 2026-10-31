import sqlite3
from pathlib import Path


class Store:
    """Remembers which mail ids have been looked at, so nothing is announced twice."""

    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.execute("CREATE TABLE IF NOT EXISTS seen (mail_id TEXT PRIMARY KEY)")
        self._db.commit()

    def is_empty(self):
        return self._db.execute("SELECT 1 FROM seen LIMIT 1").fetchone() is None

    def seen_ids(self):
        return {row[0] for row in self._db.execute("SELECT mail_id FROM seen")}

    def mark_seen(self, ids):
        self._db.executemany(
            "INSERT OR IGNORE INTO seen (mail_id) VALUES (?)", [(i,) for i in ids]
        )
        self._db.commit()

    def close(self):
        self._db.close()
