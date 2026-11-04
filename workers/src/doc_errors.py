"""Turn a failure into a kind the UI can explain in plain words.

The rules live in ONE place, PROBLEM_RULES in packages/shared/src/index.ts,
copied here by codegen (generated.py): the API and the browser classify a
stored error with the same list. The raw text is kept as `detail` for
support, never shown as the headline.
"""
from __future__ import annotations

import re

from generated import PROBLEM_RULES

_RULES = [(kind, re.compile(pattern, re.I)) for kind, pattern in PROBLEM_RULES]

KINDS = [kind for kind, _ in _RULES] + ["unknown"]

# Retrying the same file a minute later fails the same way; these need a
# person (a fixed file, a top-up, a corrected key) and then the Retry button.
PERMANENT = {"pdf_damaged", "ai_credits", "ai_key"}

# Signed URLs and anything shaped like a key never reach the database.
_SECRETS = re.compile(r"(?:sk-[\w-]{8,}|AIza[\w-]{20,}|[?&](?:X-Amz-[^&\s]+|Signature|key)=[^&\s]+)")


def _text(exc: BaseException) -> str:
    """The exception and everything it was raised from, type names included."""
    parts, seen = [], set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        parts.append(f"{type(exc).__name__}: {exc}")
        exc = exc.__cause__ or exc.__context__
    return " | ".join(parts)


def scrub(text: str) -> str:
    """Error text safe to store and show: no keys, no signed URLs, bounded."""
    return _SECRETS.sub("[hidden]", text)[:500]


def classify_text(text: str) -> str:
    return next((k for k, pattern in _RULES if pattern.search(text or "")), "unknown")


def classify(exc: BaseException) -> tuple[str, str]:
    """(kind, detail) for a failure. Never raises."""
    text = _text(exc)
    return classify_text(text), scrub(text)
