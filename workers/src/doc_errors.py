"""Turn a processing failure into a kind the UI can explain in plain words.

The kinds are the keys of DOCUMENT_PROBLEMS in packages/shared/src/index.ts;
tests/test_doc_errors.py fails if one is missing there. The raw text is kept as
`detail` for support, never shown as the headline.
"""
from __future__ import annotations

import re

# Checked in order: the first match wins, so the specific ones come first
# (a "quota exceeded" 429 is out of credit, not merely busy).
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("disk", re.compile(r"no space left on device|disk quota", re.I)),
    ("memory", re.compile(r"memoryerror|out of memory|cannot allocate|could not create a primitive", re.I)),
    ("storage", re.compile(r"nosuchkey|nosuchbucket|accessdenied|getobject|headobject|endpoint url|botocore", re.I)),
    ("ai_credits", re.compile(
        r"credit balance|insufficient[_ ](?:quota|balance|funds)|billing|payment required|\b402\b"
        r"|exceeded your (?:current )?quota|plan limit|trial key|out of credits?",
        re.I,
    )),
    ("ai_key", re.compile(
        r"invalid[ _-]?(?:x-)?api[ _-]?key|api key not valid|incorrect api key|authentication[_ ]error"
        r"|unauthori[sz]ed|\b401\b|permission[_ ]denied",
        re.I,
    )),
    ("ai_busy", re.compile(
        r"rate[ _-]?limit|too many requests|\b429\b|overloaded|\b529\b|resource[_ ]exhausted"
        r"|service unavailable|\b50[234]\b|internal server error",
        re.I,
    )),
    ("pdf_damaged", re.compile(
        r"filedataerror|cannot open broken|broken document|format error|password|encrypted|not a pdf"
        r"|no objects found|zlib",
        re.I,
    )),
    ("connection", re.compile(
        r"connection (?:refused|reset|aborted|error)|timed? ?out|operationalerror|qdrant|could not connect",
        re.I,
    )),
]

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


def classify(exc: BaseException) -> tuple[str, str]:
    """(kind, detail) for a failure. Never raises."""
    text = _text(exc)
    kind = next((k for k, pattern in _RULES if pattern.search(text)), "unknown")
    return kind, _SECRETS.sub("[hidden]", text)[:500]
