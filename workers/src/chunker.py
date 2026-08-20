"""Hybrid chunker (CLAUDE.md "Chunking strategy").

1. Structural split first, in two passes:
   a. Tables (schedules) are lifted out whole — a door/panel/beam schedule is
      one chunk of markdown with the table's own bbox, never shredded across
      block boundaries. Text blocks inside a table region are dropped, so the
      schedule is indexed once and not twice.
   b. The remaining PyMuPDF text blocks are grouped SPATIALLY (`cluster_blocks`)
      before anything is packed. A CAD sheet returns its blocks in content-
      stream order, so packing them in that order merged a note from the
      top-left with a callout from the bottom-right: the chunk read as two
      unrelated fragments and its bbox union covered the whole sheet, which
      made the FR-19 highlight useless. Clusters keep a chunk to one region.
2. Size split second: blocks are packed into 400–800-token chunks with a
   100-token overlap carried between consecutive chunks; oversized single
   blocks are split into word windows with the same overlap. Packing runs
   WITHIN a cluster group, so overlap is never carried across a spatial break.

Spatial grouping alone would over-fragment a sheet of scattered labels into
hundreds of three-word chunks, so small neighbouring clusters are merged back
together in reading order until they reach MIN_TOKENS — bounded by
MAX_BBOX_AREA_RATIO, which is what stops a merge from re-creating the
sheet-wide bbox this exists to prevent.

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

# --- Spatial clustering ---------------------------------------------------

# Two blocks join the same cluster when the gap between their rectangles is at
# most this, measured per axis. Expressed as a fraction of the page's SHORT
# side because drawing sheets span a huge size range: the same absolute gap
# that separates two note groups on a D-size sheet (34x22in) would swallow a
# whole letter-size page.
CLUSTER_GAP_RATIO = 0.02
# Floor for small pages, and the value used when the page size is unknown.
# Below roughly this, ordinary line spacing inside one note block starts
# splitting the block's own lines apart.
CLUSTER_GAP_FLOOR_PT = 18.0

# A chunk's bbox may not grow past this fraction of the page while merging
# small clusters. A highlight covering half the sheet points at nothing.
MAX_BBOX_AREA_RATIO = 0.25

# Clustering is O(n * neighbours) after an x-sweep, but a pathological page
# (every block at the same x) degenerates to O(n^2). Past this many blocks,
# skip clustering rather than stall a worker thread: the page falls back to
# the old single-group behaviour, which is worse but never slow.
CLUSTER_MAX_BLOCKS = 4000

# Reading order: cluster tops within this many points count as the same row,
# so a band of clusters reads left-to-right instead of by hairline y jitter.
_ROW_BAND_PT = 36.0


def strip_nul(text: str) -> str:
    """PDF text extraction (and OCR) can emit NUL (0x00) characters, which
    PostgreSQL text columns reject outright. Strip them at the extraction
    source so DB rows, stored .txt files, hashes, and prompts are all clean."""
    return text.replace("\x00", "")


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
class Table:
    """A detected table region: its rectangle plus its extracted cells.

    Built by workers/src/tables.py from PyMuPDF; kept as plain data here so
    every rule about how a schedule becomes chunk text stays unit-testable
    without a PDF.
    """

    x0: float
    y0: float
    x1: float
    y1: float
    rows: list[list[str]]


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


def _blocks_bbox_area(blocks: list[Block]) -> float:
    box = _bbox_union(blocks)
    return box["width"] * box["height"]


def _axis_gap(a: Block, b: Block) -> float:
    """Largest per-axis gap between two rectangles; 0 when they overlap.

    The max (not the diagonal distance) is deliberate: it asks "are these two
    within the threshold on BOTH axes", which is the question that keeps a
    column of notes together without also roping in the callout sitting the
    same distance away diagonally.
    """
    dx = max(0.0, a.x0 - b.x1, b.x0 - a.x1)
    dy = max(0.0, a.y0 - b.y1, b.y0 - a.y1)
    return max(dx, dy)


def cluster_gap(page_width: float | None, page_height: float | None) -> float:
    if not page_width or not page_height:
        return CLUSTER_GAP_FLOOR_PT
    return max(CLUSTER_GAP_FLOOR_PT, CLUSTER_GAP_RATIO * min(page_width, page_height))


def cluster_blocks(
    blocks: list[Block],
    page_width: float | None = None,
    page_height: float | None = None,
) -> list[list[Block]]:
    """Group blocks into spatially connected regions, in reading order.

    Union-find over "gap <= threshold" pairs. The candidate pairs come from an
    x-sweep: with blocks sorted by x0, once a later block starts further right
    than `threshold` past the current block's right edge, so does every block
    after it — hence the break. That turns the naive all-pairs scan into a
    narrow window on the wide sheets where it matters most.
    """
    blocks = [b for b in blocks if b.text.strip()]
    if not blocks:
        return []
    if len(blocks) > CLUSTER_MAX_BLOCKS:
        return [blocks]

    threshold = cluster_gap(page_width, page_height)
    parent = list(range(len(blocks)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    order = sorted(range(len(blocks)), key=lambda i: blocks[i].x0)
    for position, i in enumerate(order):
        for j in order[position + 1 :]:
            if blocks[j].x0 - blocks[i].x1 > threshold:
                break
            if _axis_gap(blocks[i], blocks[j]) <= threshold:
                union(i, j)

    grouped: dict[int, list[Block]] = {}
    for index, block in enumerate(blocks):
        grouped.setdefault(find(index), []).append(block)

    clusters = [sorted(group, key=lambda b: (b.y0, b.x0)) for group in grouped.values()]
    clusters.sort(key=lambda group: (int(group[0].y0 // _ROW_BAND_PT), group[0].x0))
    return clusters


def merge_small_clusters(
    clusters: list[list[Block]], page_area: float | None = None
) -> list[list[Block]]:
    """Combine neighbouring clusters that are too small to stand alone.

    Without this, a sheet whose text is a few hundred scattered dimension
    labels becomes a few hundred three-word chunks: useless to embed, and a
    Qdrant point each. Merging stops at MIN_TOKENS, and never past
    MAX_BBOX_AREA_RATIO of the page — the area cap is what keeps this from
    undoing the clustering it follows.
    """
    limit = page_area * MAX_BBOX_AREA_RATIO if page_area else None
    groups: list[list[Block]] = []
    # Running total rather than re-joining the group's text each time round:
    # the pages this exists for are the ones with the most clusters.
    tokens = 0
    for cluster in clusters:
        cluster_tokens = sum(estimate_tokens(b.text) for b in cluster)
        full = tokens >= MIN_TOKENS
        too_wide = (
            limit is not None
            and groups
            and _blocks_bbox_area(groups[-1] + list(cluster)) > limit
        )
        if not groups or full or too_wide:
            groups.append(list(cluster))
            tokens = cluster_tokens
            continue
        groups[-1].extend(cluster)
        tokens += cluster_tokens
    return groups


# --- Tables (schedules) ---------------------------------------------------

# A "table" of one column or one row is almost always a false positive — a
# column of notes, or a strip of the title block — and reads far worse as
# markdown than it does as ordinary text.
MIN_TABLE_ROWS = 2
MIN_TABLE_COLS = 2
# Cells hold values, not prose; a "cell" longer than this means the detector
# swallowed a note block and the region is not really a schedule.
MAX_CELL_CHARS = 300


def _clean_cell(value) -> str:
    """Cells arrive as str or None, and may hold the newlines PyMuPDF keeps
    from a wrapped cell — which would break the one-row-per-line markdown."""
    if value is None:
        return ""
    return strip_nul(str(value)).replace("|", "\\|").replace("\n", " ").strip()


def normalize_rows(rows: list[list]) -> list[list[str]]:
    """Clean every cell and pad short rows, so each row has the same width."""
    cleaned = [[_clean_cell(cell) for cell in row] for row in rows or []]
    cleaned = [row for row in cleaned if any(cell for cell in row)]
    if not cleaned:
        return []
    width = max(len(row) for row in cleaned)
    return [row + [""] * (width - len(row)) for row in cleaned]


def is_table_like(rows: list[list[str]]) -> bool:
    if len(rows) < MIN_TABLE_ROWS or len(rows[0]) < MIN_TABLE_COLS:
        return False
    return all(len(cell) <= MAX_CELL_CHARS for row in rows for cell in row)


def table_markdown(rows: list[list[str]]) -> str:
    """First row as the header, because that is what a schedule's first row is.

    Markdown (rather than the raw cell soup the block extractor produced) is
    what lets a retrieved schedule still say which column a value came from:
    "TYPE 3 | 3070 | HM" is unreadable, a header row makes it a door schedule.
    """
    if not rows:
        return ""
    header, body = rows[0], rows[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def table_chunks(table: Table) -> list[Chunk]:
    """One chunk per table, split by rows (header repeated) if it is oversized.

    Every piece keeps the WHOLE table's bbox: the row-level rectangles aren't
    tracked, so a coarser-but-truthful highlight is the honest answer — the
    same trade `_split_oversized_block` already makes.
    """
    rows = normalize_rows(table.rows)
    if not is_table_like(rows):
        return []
    bbox = {
        "x": table.x0,
        "y": table.y0,
        "width": table.x1 - table.x0,
        "height": table.y1 - table.y0,
    }
    header, body = rows[0], rows[1:]
    chunks: list[Chunk] = []
    current: list[list[str]] = []
    header_tokens = estimate_tokens(table_markdown([header]))

    def close() -> None:
        if not current:
            return
        text = table_markdown([header, *current])
        chunks.append(Chunk(text=text, bbox=dict(bbox), token_count=estimate_tokens(text)))

    running = header_tokens
    for row in body:
        row_tokens = estimate_tokens(" ".join(row))
        if current and running + row_tokens > MAX_TOKENS:
            close()
            current = []
            running = header_tokens
        current.append(row)
        running += row_tokens
    close()
    if not chunks:  # header-only survivors are not a schedule
        return []
    return chunks


def _covered_by_table(block: Block, tables: list[Table]) -> bool:
    """True when most of the block sits inside a detected table.

    Majority overlap rather than containment: the detector's rectangle and the
    text block's rectangle rarely agree to the point, and a block half outside
    a schedule is a caption, not a cell.
    """
    area = max(0.0, block.x1 - block.x0) * max(0.0, block.y1 - block.y0)
    if area <= 0:
        return True  # degenerate block; the table chunk already carries its text
    for table in tables:
        overlap_w = max(0.0, min(block.x1, table.x1) - max(block.x0, table.x0))
        overlap_h = max(0.0, min(block.y1, table.y1) - max(block.y0, table.y0))
        if overlap_w * overlap_h / area >= 0.5:
            return True
    return False


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


def _reading_key(bbox: dict) -> tuple[int, float]:
    return int(bbox["y"] // _ROW_BAND_PT), bbox["x"]


def chunk_page(
    raw_blocks: list[tuple],
    page_width: float | None = None,
    page_height: float | None = None,
    tables: list[Table] | None = None,
) -> list[Chunk]:
    """Entry point for the pipeline: raw_blocks from fitz page.get_text("blocks"),
    i.e. (x0, y0, x1, y1, text, block_no, block_type). Image blocks (type 1)
    are skipped — image regions become chunk content in a later phase.

    `tables` (from workers/src/tables.py) become chunks of their own and their
    cells are removed from the block stream, so a schedule is indexed once as a
    readable table instead of twice as scattered cell fragments. Passing the
    page size turns on size-relative clustering and the bbox area cap; without
    it the absolute floor applies and nothing else changes.
    """
    blocks = [
        Block(x0=b[0], y0=b[1], x1=b[2], y1=b[3], text=strip_nul(str(b[4])))
        for b in raw_blocks
        if len(b) >= 7 and b[6] == 0 and strip_nul(str(b[4])).strip()
    ]

    produced: list[Chunk] = []
    # Only tables that actually produced a chunk may swallow blocks — a region
    # rejected as not-a-schedule must leave its text in the block stream, or
    # a false positive would delete the page's content instead of adding to it.
    absorbing: list[Table] = []
    for table in tables or []:
        chunks = table_chunks(table)
        if chunks:
            produced.extend(chunks)
            absorbing.append(table)
    if absorbing:
        blocks = [b for b in blocks if not _covered_by_table(b, absorbing)]

    page_area = page_width * page_height if page_width and page_height else None
    for group in merge_small_clusters(
        cluster_blocks(blocks, page_width, page_height), page_area
    ):
        produced.extend(chunk_blocks(group))

    produced.sort(key=lambda c: _reading_key(c.bbox))
    return produced
