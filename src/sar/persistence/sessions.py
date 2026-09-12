"""Session store: SQLite-backed completed research session persistence.

Stores completed pipeline runs with reports, citations, and conversation
history. Supports semantic deduplication to avoid re-running similar queries.

Uses FTS5 for broad candidate retrieval and difflib.SequenceMatcher for
fine-grained similarity scoring.

Migrated from: agentic_sar/cache_service.py (session portion)

NOT WIRED: SessionStore session resume and similarity lookup is
implemented and exported, but no code path in
``sar`` constructs it. It is retained deliberately -- deleting it is a
product decision, not a cleanup -- but it is not covered by the
pipeline's tests and must not be assumed to work until a caller exists.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionStore:
    """Persistent storage for completed research sessions.

    Each session is a completed pipeline run, stored with its query,
    report, citations, and iteration history. Supports full-text search
    and semantic similarity matching to detect near-duplicate queries.

    Attributes:
        db_path: Path to the SQLite database file.
        similarity_threshold: Minimum ratio for semantic match (0.0-1.0).
    """

    def __init__(
        self,
        db_path: str | Path = "cache/sessions.db",
        similarity_threshold: float = 0.92,
    ) -> None:
        """Initialize the session store.

        Creates the database, tables, and FTS5 index if they don't exist.

        Args:
            db_path: Path to SQLite database file.
            similarity_threshold: SequenceMatcher threshold for dedup.
        """
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._similarity_threshold = similarity_threshold
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    @property
    def db_path(self) -> Path:
        """Path to the SQLite database file."""
        return self._db_path

    @property
    def similarity_threshold(self) -> float:
        """Minimum SequenceMatcher ratio for duplicate detection."""
        return self._similarity_threshold

    def _create_tables(self) -> None:
        """Create sessions table and FTS5 index if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                report TEXT NOT NULL,
                citations TEXT NOT NULL,
                history TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                query,
                content=sessions,
                content_rowid=rowid,
                tokenize='unicode61'
            );

            CREATE TRIGGER IF NOT EXISTS sessions_ai AFTER INSERT ON sessions BEGIN
                INSERT INTO sessions_fts(rowid, query)
                VALUES (new.rowid, new.query);
            END;

            CREATE TRIGGER IF NOT EXISTS sessions_ad AFTER DELETE ON sessions BEGIN
                INSERT INTO sessions_fts(sessions_fts, rowid, query)
                VALUES ('delete', old.rowid, old.query);
            END;

            CREATE TRIGGER IF NOT EXISTS sessions_au AFTER UPDATE ON sessions BEGIN
                INSERT INTO sessions_fts(sessions_fts, rowid, query)
                VALUES ('delete', old.rowid, old.query);
                INSERT INTO sessions_fts(rowid, query)
                VALUES (new.rowid, new.query);
            END;
        """)
        self._conn.commit()
        logger.debug("Session store initialized at %s", self._db_path)

    def save(
        self,
        query: str,
        report: str,
        citations: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> str:
        """Save a completed session.

        Args:
            query: The original research query.
            report: Final Markdown report.
            citations: List of citation dicts.
            history: List of iteration records.

        Returns:
            Session UUID.
        """
        session_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO sessions (id, query, report, citations, history, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                query,
                report,
                json.dumps(citations),
                json.dumps(history),
                now,
            ),
        )
        self._conn.commit()
        logger.info("Session saved: %s for query: '%s'", session_id, query)
        return session_id

    def find_similar(self, query: str) -> dict[str, Any] | None:
        """Find a cached session with a semantically similar query.

        Uses FTS5 for candidate retrieval, then SequenceMatcher for
        fine-grained similarity scoring.

        Args:
            query: Query to match against.

        Returns:
            Session dict if a match is found above threshold, else None.
        """
        # Build FTS5 query from tokens longer than 3 characters
        fts_query = " OR ".join(
            f'"{w}"' for w in query.split() if len(w) > 3
        )
        if not fts_query:
            return None

        try:
            rows = self._conn.execute(
                """SELECT s.id, s.query
                   FROM sessions_fts f
                   JOIN sessions s ON f.rowid = s.rowid
                   WHERE sessions_fts MATCH ?
                   ORDER BY rank
                   LIMIT 10""",
                (fts_query,),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS index might be empty or corrupted; fall back to recent
            rows = self._conn.execute(
                "SELECT id, query FROM sessions ORDER BY created_at DESC LIMIT 10"
            ).fetchall()

        if not rows:
            return None

        # Score candidates with SequenceMatcher
        q_lower = query.lower().strip()
        best_ratio = 0.0
        best_session_id: str | None = None

        for row in rows:
            ratio = SequenceMatcher(
                None, q_lower, row["query"].lower().strip()
            ).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_session_id = row["id"]

        if best_ratio >= self._similarity_threshold and best_session_id is not None:
            logger.info(
                "Semantic cache HIT (ratio=%.3f) for query: '%s'",
                best_ratio,
                query,
            )
            return self.get(best_session_id)

        return None

    def get(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve a session by ID.

        Args:
            session_id: UUID of the session.

        Returns:
            Session dict, or None if not found.
        """
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_recent(self, limit: int = 30) -> list[dict[str, Any]]:
        """List the most recent sessions.

        Args:
            limit: Maximum number of sessions to return.

        Returns:
            List of session dicts, newest first.
        """
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert a database row to a session dict.

        Parses JSON-serialized citations and history fields back to
        Python objects.

        Args:
            row: A sqlite3.Row from the sessions table.

        Returns:
            Session dict with parsed JSON fields.
        """
        entry = dict(row)
        if entry.get("citations"):
            entry["citations"] = json.loads(entry["citations"])
        if entry.get("history"):
            entry["history"] = json.loads(entry["history"])
        return entry

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
