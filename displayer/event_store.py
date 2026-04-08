"""Thread-safe event store with SQLite persistence and bounded memory."""

import asyncio
import json
import sqlite3
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock

_MAX_MEMORY_EVENTS = 500


@dataclass
class Event:
    id: int
    timestamp: float
    event_type: str  # "narration", "action", "state"
    text: str
    tool_name: str = ""
    raw_data: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class EventStore:
    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = Path(__file__).parent / "data" / "events.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = Lock()
        self._events: deque[Event] = deque(maxlen=_MAX_MEMORY_EVENTS)
        self._next_id = 1
        self._subscribers: list[asyncio.Queue] = []

        self._init_db()
        self._load_history()

    def _init_db(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                text TEXT NOT NULL,
                tool_name TEXT DEFAULT '',
                raw_data TEXT DEFAULT ''
            )
        """)
        conn.commit()
        conn.close()

    def _load_history(self):
        """Load only the most recent events into memory."""
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT id, timestamp, event_type, text, tool_name, raw_data "
            "FROM events ORDER BY id DESC LIMIT ?",
            (_MAX_MEMORY_EVENTS,),
        ).fetchall()
        conn.close()

        for row in reversed(rows):
            self._events.append(Event(
                id=row[0], timestamp=row[1], event_type=row[2],
                text=row[3], tool_name=row[4], raw_data=row[5],
            ))

        if self._events:
            self._next_id = self._events[-1].id + 1

    def append(
        self,
        text: str,
        event_type: str = "action",
        tool_name: str = "",
        raw_data: dict | None = None,
    ) -> Event:
        with self._lock:
            event = Event(
                id=self._next_id,
                timestamp=time.time(),
                event_type=event_type,
                text=text,
                tool_name=tool_name,
                raw_data=json.dumps(raw_data, ensure_ascii=False) if raw_data else "",
            )
            self._next_id += 1
            self._events.append(event)

            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                "INSERT INTO events (id, timestamp, event_type, text, tool_name, raw_data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (event.id, event.timestamp, event.event_type,
                 event.text, event.tool_name, event.raw_data),
            )
            conn.commit()
            conn.close()

            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

            return event

    def get_history(self, since_id: int = 0) -> list[Event]:
        with self._lock:
            return [e for e in self._events if e.id > since_id]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not q]

    def clear(self):
        with self._lock:
            self._events.clear()
            self._next_id = 1
            conn = sqlite3.connect(str(self._db_path))
            conn.execute("DELETE FROM events")
            conn.commit()
            conn.close()
            for q in self._subscribers:
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass
