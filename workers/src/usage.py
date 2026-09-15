"""Token accounting for worker-side model calls.

One row per model call in usage_events, the same table the API writes from
apps/api/src/usage.ts — keep the column set in sync. The dashboard aggregates
this to report spend per project and per stage (chat / summary /
classification / embedding).

Recording must never break the pipeline: failures are logged and swallowed.
"""

from __future__ import annotations

import uuid

import logutil

log = logutil.get("usage")

# Must match the UsageKind enum in apps/api/prisma/schema.prisma. A kind that
# is missing here is not a missing dashboard row — it is a raised ValueError in
# the middle of a model call, thrown AFTER the request was paid for. That is how
# the first vision pass lost a description it had already bought: 200 OK from
# the API, 26 seconds of latency, then "unknown usage kind 'vlm'" and a
# discarded answer.
KINDS = ("chat", "summary", "classification", "embedding", "rerank", "vlm")


def record(
    project_id: str | None,
    kind: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> None:
    if kind not in KINDS:
        # Logged, not raised. This module's contract is that accounting never
        # breaks the pipeline, and every OTHER failure here already honours it
        # by sitting inside the try below — a dead database loses the row and
        # keeps the answer. An unknown kind was the one exception, and it threw
        # away a completed model call to report a typo in a constant. Now the
        # caller keeps what it paid for and the gap is loud in the log.
        log.error(
            "unknown usage kind %r — spend for this call is NOT recorded; "
            "add it to usage.KINDS and to the UsageKind enum",
            kind,
        )
        return
    try:
        import db

        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_events
                  (id, "projectId", kind, model, "inputTokens", "outputTokens",
                   "cacheReadTokens", "cacheWriteTokens")
                VALUES (%s, %s, %s::"UsageKind", %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    project_id,
                    kind,
                    model,
                    int(input_tokens),
                    int(output_tokens),
                    int(cache_read_tokens),
                    int(cache_write_tokens),
                ),
            )
    except Exception as exc:  # never fail a job over accounting
        log.warning("could not record %s usage: %s", kind, exc)


def record_message(project_id: str | None, kind: str, model: str, usage) -> None:
    """Record from an Anthropic response `usage` object (or a batch result's)."""
    record(
        project_id,
        kind,
        model,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )
