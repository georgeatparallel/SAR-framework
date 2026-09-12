"""Search service — federated search, content fetching, and caching.

No LLM dependency. This service owns:
- Backend registration and fan-out (engine.py)
- URL → full text extraction (fetch.py)
- Persistent URL cache with TTL (cache.py)
- Individual search backend implementations (backends/)
"""
