"""SQLite-Zugriff (thread-sicher, WAL) inkl. einfacher Migrationen."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MIGRATIONS: list[str] = [
    # v1
    """
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL DEFAULT 'url',          -- url | upload
        url TEXT NOT NULL,
        title TEXT,
        channel TEXT,
        thumbnail TEXT,
        duration REAL,
        format TEXT NOT NULL,
        options TEXT NOT NULL DEFAULT '{}',
        collection TEXT,
        status TEXT NOT NULL,                      -- queued|running|processing|done|failed|cancelled
        stage TEXT,
        progress REAL NOT NULL DEFAULT 0,
        speed REAL,
        eta INTEGER,
        error TEXT,
        media_id TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT
    );
    CREATE INDEX idx_jobs_status ON jobs(status, created_at);

    CREATE TABLE media (
        id TEXT PRIMARY KEY,
        job_id TEXT,
        source_url TEXT,
        source_id TEXT,
        title TEXT NOT NULL,
        artist TEXT,
        description TEXT,
        duration REAL,
        kind TEXT NOT NULL,                        -- audio | video
        format TEXT NOT NULL,
        path TEXT NOT NULL UNIQUE,                 -- relativ zu DOWNLOAD_PATH
        original_path TEXT,
        cover_path TEXT,                           -- relativ zu DOWNLOAD_PATH
        size INTEGER NOT NULL DEFAULT 0,
        collection TEXT,
        published_at TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_media_created ON media(created_at);
    CREATE INDEX idx_media_collection ON media(collection);

    CREATE TABLE kv (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # v2: mehrere Benutzer
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',         -- admin | user
        quota_mb INTEGER,                          -- NULL/0 = unbegrenzt
        max_jobs INTEGER,                          -- NULL/0 = unbegrenzt
        feed_token TEXT UNIQUE,
        disabled INTEGER NOT NULL DEFAULT 0,
        session_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    ALTER TABLE jobs ADD COLUMN user_id TEXT;
    ALTER TABLE media ADD COLUMN user_id TEXT;
    ALTER TABLE media ADD COLUMN original_size INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX idx_jobs_user ON jobs(user_id, status);
    CREATE INDEX idx_media_user ON media(user_id, created_at);
    """,
    # v3: eigene Feeds, Serien, automatische Abos
    """
    CREATE TABLE feeds (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        type TEXT NOT NULL DEFAULT 'episodic',     -- episodic | serial
        cover_path TEXT,                           -- relativ zu DOWNLOAD_PATH
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_feeds_user ON feeds(user_id);
    CREATE TABLE feed_items (
        feed_id TEXT NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
        media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        added_at TEXT NOT NULL,
        PRIMARY KEY (feed_id, media_id)
    );
    CREATE INDEX idx_feed_items_media ON feed_items(media_id);
    CREATE TABLE subscriptions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        url TEXT NOT NULL,
        kind TEXT NOT NULL,                        -- channel | playlist
        title TEXT NOT NULL,
        format TEXT NOT NULL,
        options TEXT NOT NULL DEFAULT '{}',
        collection TEXT,
        feed_id TEXT REFERENCES feeds(id) ON DELETE SET NULL,
        interval_hours INTEGER NOT NULL DEFAULT 6,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_checked_at TEXT,
        last_error TEXT,
        last_new_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_subscriptions_user ON subscriptions(user_id);
    CREATE TABLE subscription_seen (
        subscription_id TEXT NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
        video_id TEXT NOT NULL,
        seen_at TEXT NOT NULL,
        PRIMARY KEY (subscription_id, video_id)
    );
    ALTER TABLE jobs ADD COLUMN subscription_id TEXT;
    ALTER TABLE media ADD COLUMN track INTEGER;
    """,
]


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for idx, script in enumerate(MIGRATIONS[version:], start=version + 1):
                self._conn.executescript(f"BEGIN; {script}; PRAGMA user_version={idx}; COMMIT;")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return cur.rowcount

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    # --- Key/Value -----------------------------------------------------------
    def kv_get(self, key: str) -> str | None:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # --- Hilfen ----------------------------------------------------------------
    def insert(self, table: str, data: dict) -> None:
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        self.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", data.values())

    def update(self, table: str, row_id: str, data: dict) -> None:
        if not data:
            return
        sets = ", ".join(f"{k}=?" for k in data)
        self.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*data.values(), row_id])


def job_to_api(row: dict) -> dict:
    out = dict(row)
    try:
        out["options"] = json.loads(row.get("options") or "{}")
    except json.JSONDecodeError:
        out["options"] = {}
    # interne Felder (z. B. Upload-Pfad) nicht ausliefern
    out["options"].pop("upload_path", None)
    return out
