import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chunker import (  # noqa: E402
    MAX_TOKENS,
    MIN_TOKENS,
    OVERLAP_TOKENS,
    Block,
    chunk_blocks,
    chunk_page,
    estimate_tokens,
)


def words(n: int, tag: str = "w") -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


def block(text: str, x0=0, y0=0, x1=100, y1=20) -> Block:
    return Block(x0=x0, y0=y0, x1=x1, y1=y1, text=text)


class TestEstimateTokens:
    def test_scales_with_words(self):
        assert estimate_tokens(words(100)) == 130

    def test_empty(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("   ") == 0


class TestChunkBlocks:
    def test_small_page_single_chunk(self):
        chunks = chunk_blocks([block("TITLE BLOCK"), block("GENERAL NOTES", y0=30, y1=50)])
        assert len(chunks) == 1
        assert "TITLE BLOCK" in chunks[0].text and "GENERAL NOTES" in chunks[0].text

    def test_empty_blocks_skipped(self):
        assert chunk_blocks([block("  "), block("")]) == []
        assert chunk_blocks([]) == []

    def test_chunks_respect_token_bounds(self):
        # 12 blocks of ~130 tokens: must split into chunks within [MIN, MAX]
        blocks = [block(words(100, f"b{i}_"), y0=i * 30, y1=i * 30 + 20) for i in range(12)]
        chunks = chunk_blocks(blocks)
        assert len(chunks) > 1
        for c in chunks[:-1]:  # every chunk except the remainder respects bounds
            assert MIN_TOKENS <= c.token_count <= MAX_TOKENS, c.token_count

    def test_overlap_between_consecutive_chunks(self):
        blocks = [block(words(100, f"b{i}_"), y0=i * 30, y1=i * 30 + 20) for i in range(12)]
        chunks = chunk_blocks(blocks)
        for prev, nxt in zip(chunks, chunks[1:]):
            prev_words = set(prev.text.split())
            nxt_words = set(nxt.text.split())
            shared = prev_words & nxt_words
            # ~100 tokens ≈ 77 words carried over
            assert len(shared) >= OVERLAP_TOKENS / 2, f"only {len(shared)} shared words"

    def test_bbox_is_union_of_member_blocks(self):
        chunks = chunk_blocks(
            [block("A", x0=10, y0=10, x1=50, y1=20), block("B", x0=40, y0=100, x1=200, y1=120)]
        )
        assert chunks[0].bbox == {"x": 10, "y": 10, "width": 190, "height": 110}

    def test_oversized_single_block_split_with_overlap(self):
        big = block(words(1000), x0=5, y0=5, x1=500, y1=300)  # ~1300 tokens
        chunks = chunk_blocks([big])
        assert len(chunks) >= 2
        for c in chunks:
            assert c.token_count <= MAX_TOKENS + 5
            assert c.bbox == {"x": 5, "y": 5, "width": 495, "height": 295}
        first_words = chunks[0].text.split()
        second_words = chunks[1].text.split()
        assert first_words[-1] in second_words  # overlap carried

    def test_all_text_preserved(self):
        blocks = [block(words(80, f"blk{i}_"), y0=i * 30, y1=i * 30 + 20) for i in range(10)]
        chunks = chunk_blocks(blocks)
        combined = " ".join(c.text for c in chunks)
        for i in range(10):
            assert f"blk{i}_0" in combined


class TestChunkPage:
    def test_pymupdf_block_tuples(self):
        raw = [
            (0, 0, 100, 20, "FOUNDATION PLAN NOTES", 0, 0),
            (0, 30, 100, 50, "1. ALL FOOTINGS BEAR ON UNDISTURBED SOIL", 1, 0),
            (0, 60, 100, 80, "", 2, 0),  # empty text block skipped
            (0, 90, 100, 110, "ignored image block", 3, 1),  # image block skipped
        ]
        chunks = chunk_page(raw)
        assert len(chunks) == 1
        assert "FOUNDATION" in chunks[0].text
        assert "ignored image block" not in chunks[0].text
