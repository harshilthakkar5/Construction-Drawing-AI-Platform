"""Chunk-text hashing (Phase 5): the revision embedding-reuse key.
Standalone module (no DB deps) so it is importable from tests and anywhere
the hash is needed without a psycopg install.
"""

import hashlib


def text_hash(text: str) -> str:
    """sha256 hex of chunk text — identical text ⇒ identical embedding."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
