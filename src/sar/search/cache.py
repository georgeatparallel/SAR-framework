"""URL cache: SQLite-backed persistent cache with TTL.

Stores fetched URL content to avoid redundant network requests.
TTL is configurable; expired entries are lazily purged on read
and eagerly purged via ``purge_expired``.

Migrated from: agentic_sar/cache_service.py (URL cache portion)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class URLCache:
    """Persistent URL content cache backed by SQLite.

    Stores URL → content mappings with expiration timestamps.
    Thread-safe via SQLite's built-in serialized locking
    (``check_same_thread=False`` with the default serialized mode).

    Attributes:
        db_path: Path to the SQLite database file.
        ttl_days: Time-to-live for cache entries in days.
    """

    def __init__(self, db_path: str | Path = "cache/url_cache.db", ttl_days: int = 7) -> None:
        """Initialize the URL cache.

        Creates the parent directory and SQLite database with the
        ``url_cache`` table if they do not already exist.

        Args:
            db_path: Path to the SQLite database file.  Relative paths
                are resolved from the current working directory.
            ttl_days: How many days before a cached entry is considered
                expired.  Must be a positive integer.

        Raises:
            ValueError: If ``ttl_days`` is not a positive integer.
            OSError: If the parent directory cannot be created.
        """
        if ttl_days < 1:
            raise ValueError(f"ttl_days must be >= 1, got {ttl_days}")

        self.db_path = Path(db_path)
        self.ttl_days = ttl_days

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS url_cache (
                url        TEXT PRIMARY KEY,
                content    TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def get(self, url: str) -> str | None:
        """Retrieve cached content for a URL.

        Returns ``None`` if the URL is not in the cache or if the
        cached entry has exceeded the configured TTL.  Expired entries
        are *not* deleted on read; call ``purge_expired`` to reclaim
        space.

        Args:
            url: The URL to look up.

        Returns:
            Cached content string, or ``None`` on miss or expiry.
        """
        row = self._conn.execute(
            "SELECT content, fetched_at FROM url_cache WHERE url = ?",
            (url,),
        ).fetchone()

        if row is None:
            return None

        content, fetched_at_str = row
        fetched_at = datetime.fromisoformat(fetched_at_str)

        # Ensure offset-aware comparison.
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)

        age = datetime.now(timezone.utc) - fetched_at
        if age.days >= self.ttl_days:
            return None  # Entry has expired; treat as a cache miss.

        return content

    def set(self, url: str, content: str) -> None:
        """Store content for a URL.

        Overwrites any existing entry for the same URL with fresh
        content and an updated ``fetched_at`` timestamp.

        Args:
            url: The URL key.
            content: The text content to cache.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO url_cache (url, content, fetched_at) VALUES (?, ?, ?)",
            (url, content, now_iso),
        )
        self._conn.commit()

    def purge_expired(self) -> int:
        """Delete all expired entries from the cache.

        An entry is expired when its ``fetched_at`` timestamp is older
        than ``now - ttl_days``.  The cutoff is computed in UTC.

        Returns:
            Number of rows deleted.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.ttl_days)).isoformat()
        cursor = self._conn.execute(
            "DELETE FROM url_cache WHERE fetched_at < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cursor.rowcount

    def clear(self) -> None:
        """Delete all entries from the cache.

        Intended for use in tests and manual cache resets.  Does not
        drop or alter the table schema.
        """
        self._conn.execute("DELETE FROM url_cache")
        self._conn.commit()

    def __del__(self) -> None:
        """Close the SQLite connection on garbage collection."""
        try:
            self._conn.close()
        except Exception:
            pass
