"""Hybrid chunker (CLAUDE.md "Chunking strategy").

1. Structural split first: PyMuPDF text blocks are the structural units —
   they follow drawing notes, schedules, sections, and title-block cells.
2. Size split second: blocks are packed into 400–800-token chunks with a
   100-token overlap carried between consecutive chunks; oversized single
   blocks are split into word windows with the same overlap.

Every chunk carries a bbox {x, y, width, height} in PDF points — the union
of its blocks' rectangles (or the block's rect for word-window splits) —
which is what the click-to-highlight verification chain resolves to.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_TOKENS = 400
MAX_TOKENS = 800
OVERLAP_TOKENS = 100

# Claude/Voyage tokenizers average ~1.3 tokens per English word; a cheap
# estimator is fine because the bounds above are soft targets.
TOKENS_PER_WORD = 1.3


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text.split()) * TOKENS_PER_WORD)) if text.strip() else 0


@dataclass
class Block:
    """A structural unit: one PyMuPDF text block."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass
class Chunk:
    text: str
    bbox: dict  # {x, y, width, height} in PDF points
    token_count: int


def _bbox_union(blocks: list[Block]) -> dict:
    x0 = min(b.x0 for b in blocks)
    y0 = min(b.y0 for b in blocks)
    x1 = max(b.x1 for b in blocks)
    y1 = max(b.y1 for b in blocks)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def _block_bbox(block: Block) -> dict:
    return {
        "x": block.x0,
        "y": block.y0,
        "width": block.x1 - block.x0,
        "height": block.y1 - block.y0,
    }


def _split_oversized_block(block: Block) -> list[Chunk]:
    """A single block above MAX_TOKENS becomes word windows with overlap.
    Word positions within a block aren't tracked, so each window keeps the
    whole block's bbox — the highlight region stays truthful, just coarser."""
    words = block.text.split()
    window = int(MAX_TOKENS / TOKENS_PER_WORD)
    step = window - int(OVERLAP_TOKENS / TOKENS_PER_WORD)
    chunks = []
    for start in range(0, len(words), step):
        piece = " ".join(words[start : start + window])
        if not piece:
            continue
        chunks.append(Chunk(text=piece, bbox=_block_bbox(block), token_count=estimate_tokens(piece)))
        if start + window >= len(words):
            break
    return chunks


def _overlap_tail(blocks: list[Block]) -> list[Block]:
    """Trailing blocks summing to ~OVERLAP_TOKENS, carried into the next chunk."""
    tail: list[Block] = []
    total = 0
    for block in reversed(blocks):
        tokens = estimate_tokens(block.text)
        if total + tokens > OVERLAP_TOKENS and tail:
            break
        tail.insert(0, block)
        total += tokens
        if total >= OVERLAP_TOKENS:
            break
    return tail if tail != blocks else []


def chunk_blocks(blocks: list[Block]) -> list[Chunk]:
    """Pack structural blocks into size-bounded chunks with overlap."""
    blocks = [b for b in blocks if b.text.strip()]
    if not blocks:
        return []

    chunks: list[Chunk] = []
    current: list[Block] = []
    current_tokens = 0

    def close_current() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        text = "\n".join(b.text.strip() for b in current)
        chunks.append(Chunk(text=text, bbox=_bbox_union(current), token_count=estimate_tokens(text)))
        carried = _overlap_tail(current)
        current = list(carried)
        current_tokens = sum(estimate_tokens(b.text) for b in current)

    for block in blocks:
        tokens = estimate_tokens(block.text)
        if tokens > MAX_TOKENS:
            close_current()
            # flush any carried overlap into the oversized split's first window
            current = []
            current_tokens = 0
            chunks.extend(_split_oversized_block(block))
            continue
        if current_tokens + tokens > MAX_TOKENS and current_tokens >= MIN_TOKENS:
            close_current()
        current.append(block)
        current_tokens += tokens

    # final remainder (most drawing pages land here: total text < MIN_TOKENS)
    if current:
        text = "\n".join(b.text.strip() for b in current)
        chunks.append(Chunk(text=text, bbox=_bbox_union(current), token_count=estimate_tokens(text)))

    return chunks


def chunk_page(raw_blocks: list[tuple]) -> list[Chunk]:
    """Entry point for the pipeline: raw_blocks from fitz page.get_text("blocks"),
    i.e. (x0, y0, x1, y1, text, block_no, block_type). Image blocks (type 1)
    are skipped — image regions become chunk content in a later phase."""
    blocks = [
        Block(x0=b[0], y0=b[1], x1=b[2], y1=b[3], text=b[4])
        for b in raw_blocks
        if len(b) >= 7 and b[6] == 0 and str(b[4]).strip()
    ]
    return chunk_blocks(blocks)
