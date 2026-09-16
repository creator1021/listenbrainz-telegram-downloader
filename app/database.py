from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .matching import Track


@dataclass(frozen=True)
class PendingTrack:
    key: str
    artist: str
    title: str
    album: str
    attempts: int

    def as_track(self) -> Track:
        return Track(self.artist, self.title, listened_at=0, album=self.album)


@dataclass(frozen=True)
class DownloadedTrack:
    track: Track
    path: Path
    source_channel: str
    source_message_id: int | None


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tracks (
                track_key TEXT PRIMARY KEY,
                artist TEXT NOT NULL,
                title TEXT NOT NULL,
                album TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                last_listened_at INTEGER NOT NULL,
                next_attempt_at INTEGER NOT NULL DEFAULT 0,
                downloaded_path TEXT,
                source_channel TEXT,
                source_message_id INTEGER,
                last_error TEXT,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS listens (
                listen_key TEXT PRIMARY KEY,
                track_key TEXT NOT NULL REFERENCES tracks(track_key),
                listened_at INTEGER NOT NULL,
                inserted_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tracks_due
                ON tracks(status, next_attempt_at);
            """
        )
        columns = {
            str(row[1])
            for row in self.connection.execute("PRAGMA table_info(tracks)").fetchall()
        }
        if "album" not in columns:
            self.connection.execute(
                "ALTER TABLE tracks ADD COLUMN album TEXT NOT NULL DEFAULT ''"
            )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def get_meta_int(self, key: str) -> int | None:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return int(row["value"]) if row else None

    def set_meta_int(self, key: str, value: int) -> None:
        self.connection.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        self.connection.commit()

    def register_listen(self, track: Track) -> bool:
        now = int(time.time())
        existing = self.connection.execute(
            "SELECT status FROM tracks WHERE track_key = ?", (track.key,)
        ).fetchone()
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO tracks(
                    track_key, artist, title, album, created_at,
                    last_listened_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    track.key,
                    track.artist,
                    track.title,
                    track.album,
                    now,
                    track.listened_at,
                    now,
                ),
            )
        else:
            self.connection.execute(
                """
                UPDATE tracks
                SET artist=?, title=?,
                    album=CASE WHEN ? <> '' THEN ? ELSE album END,
                    last_listened_at=?, updated_at=?,
                    status=CASE WHEN status='expired' THEN 'pending' ELSE status END,
                    created_at=CASE WHEN status='expired' THEN ? ELSE created_at END,
                    next_attempt_at=CASE WHEN status='expired' THEN 0 ELSE next_attempt_at END
                WHERE track_key=?
                """,
                (
                    track.artist,
                    track.title,
                    track.album,
                    track.album,
                    track.listened_at,
                    now,
                    now,
                    track.key,
                ),
            )

        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO listens(listen_key, track_key, listened_at, inserted_at) "
            "VALUES (?, ?, ?, ?)",
            (track.listen_key, track.key, track.listened_at, now),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def expire_old(self, max_age_seconds: int) -> int:
        now = int(time.time())
        cursor = self.connection.execute(
            """
            UPDATE tracks SET status='expired', updated_at=?
            WHERE status='pending' AND created_at < ?
            """,
            (now, now - max_age_seconds),
        )
        self.connection.commit()
        return cursor.rowcount

    def due_tracks(self, limit: int) -> list[PendingTrack]:
        rows = self.connection.execute(
            """
            SELECT track_key, artist, title, album, attempts
            FROM tracks
            WHERE status='pending' AND next_attempt_at <= ?
            ORDER BY last_listened_at DESC
            LIMIT ?
            """,
            (int(time.time()), limit),
        ).fetchall()
        return [PendingTrack(row[0], row[1], row[2], row[3], row[4]) for row in rows]

    def downloaded_tracks(self) -> list[DownloadedTrack]:
        rows = self.connection.execute(
            """
            SELECT artist, title, album, downloaded_path,
                   source_channel, source_message_id
            FROM tracks
            WHERE status='downloaded' AND downloaded_path IS NOT NULL
            ORDER BY artist, title
            """
        ).fetchall()
        return [
            DownloadedTrack(
                Track(row[0], row[1], listened_at=0, album=row[2] or ""),
                Path(row[3]),
                str(row[4] or ""),
                int(row[5]) if row[5] is not None else None,
            )
            for row in rows
        ]

    def update_album(self, key: str, album: str) -> None:
        self.connection.execute(
            "UPDATE tracks SET album=?, updated_at=? WHERE track_key=?",
            (album, int(time.time()), key),
        )
        self.connection.commit()

    def mark_attempt(self, key: str, error: str, retry_seconds: int) -> None:
        now = int(time.time())
        self.connection.execute(
            """
            UPDATE tracks
            SET attempts=attempts+1, last_error=?, next_attempt_at=?, updated_at=?
            WHERE track_key=?
            """,
            (error[:500], now + retry_seconds, now, key),
        )
        self.connection.commit()

    def mark_downloaded(
        self, key: str, path: str, channel: str, message_id: int
    ) -> None:
        now = int(time.time())
        self.connection.execute(
            """
            UPDATE tracks
            SET status='downloaded', downloaded_path=?, source_channel=?,
                source_message_id=?, last_error=NULL, updated_at=?
            WHERE track_key=?
            """,
            (path, channel, message_id, now, key),
        )
        self.connection.commit()

    def counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS total FROM tracks GROUP BY status"
        ).fetchall()
        result = {"pending": 0, "downloaded": 0, "expired": 0}
        result.update({str(row[0]): int(row[1]) for row in rows})
        return result
