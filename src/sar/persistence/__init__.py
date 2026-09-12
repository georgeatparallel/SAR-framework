"""Persistence service -- session storage, audit logging, and citation management.

No LLM or search dependency. This service owns:
- Append-only structured audit log per pipeline run (audit.py)
- Session storage with semantic deduplication (sessions.py)
- Citation formatting and storage (citations.py)
"""

from sar.persistence.audit import AuditLog
from sar.persistence.citations import CitationStore
from sar.persistence.sessions import SessionStore

__all__ = ["AuditLog", "CitationStore", "SessionStore"]
