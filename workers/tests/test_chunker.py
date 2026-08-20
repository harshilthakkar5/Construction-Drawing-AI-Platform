import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chunker import (  # noqa: E402
    MAX_TOKENS,
    MIN_TOKENS,
    OVERLAP_TOKENS,
    Block,
    Table,
    chunk_blocks,
    chunk_page,
    cluster_blocks,
    estimate_tokens,
    is_table_like,
    merge_small_clusters,
    normalize_rows,
    table_chunks,
    table_markdown,
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


def test_chunk_page_strips_nul_bytes():
    """PostgreSQL rejects NUL (0x00) in text columns; PDF extraction can emit
    them, so they must never reach chunk text."""
    raw = [(0, 0, 100, 20, "GENERAL\x00 NOTES \x00", 0, 0)]
    chunks = chunk_page(raw)
    assert len(chunks) == 1
    assert "\x00" not in chunks[0].text
    assert chunks[0].text == "GENERAL NOTES"


def test_chunk_page_drops_blocks_that_are_only_nul():
    raw = [(0, 0, 100, 20, "\x00\x00", 0, 0)]
    assert chunk_page(raw) == []


# D-size drawing sheet, the format most of a construction set arrives in.
SHEET_W, SHEET_H = 2448.0, 1584.0


class TestClusterBlocks:
    def test_nearby_blocks_share_a_cluster(self):
        clusters = cluster_blocks(
            [block("NOTE 1", y0=0, y1=20), block("NOTE 2", y0=25, y1=45)],
            SHEET_W,
            SHEET_H,
        )
        assert len(clusters) == 1

    def test_opposite_corners_do_not(self):
        """The bug this exists for: content-stream order put a top-left note
        and a bottom-right callout in one chunk whose bbox spanned the sheet."""
        clusters = cluster_blocks(
            [
                block("TOP LEFT NOTE", x0=10, y0=10, x1=200, y1=30),
                block("BOTTOM RIGHT CALLOUT", x0=2200, y0=1500, x1=2400, y1=1520),
            ],
            SHEET_W,
            SHEET_H,
        )
        assert len(clusters) == 2

    def test_transitively_connected_blocks_form_one_cluster(self):
        """A column of notes stays whole even though its ends are far apart."""
        ladder = [block(f"LINE {i}", y0=i * 25, y1=i * 25 + 20) for i in range(12)]
        assert len(cluster_blocks(ladder, SHEET_W, SHEET_H)) == 1

    def test_clusters_come_back_in_reading_order(self):
        far_right = block("RIGHT", x0=2000, y0=12, x1=2200, y1=30)
        left = block("LEFT", x0=10, y0=10, x1=200, y1=30)
        below = block("BELOW", x0=10, y0=900, x1=200, y1=920)
        clusters = cluster_blocks([below, far_right, left], SHEET_W, SHEET_H)
        assert [c[0].text for c in clusters] == ["LEFT", "RIGHT", "BELOW"]

    def test_threshold_scales_with_page_size(self):
        """A 25pt gap is ordinary whitespace inside one note group on a D-size
        sheet (threshold ~31pt), but a real separation on letter (18pt)."""
        pair = [block("A", y0=0, y1=20), block("B", y0=45, y1=65)]
        assert len(cluster_blocks(pair, SHEET_W, SHEET_H)) == 1
        assert len(cluster_blocks(pair, 612, 792)) == 2

    def test_huge_page_bails_out_to_one_cluster(self):
        """Pathological pages must not stall a worker thread."""
        from chunker import CLUSTER_MAX_BLOCKS

        many = [block(f"L{i}", y0=i * 1000, y1=i * 1000 + 5) for i in range(CLUSTER_MAX_BLOCKS + 1)]
        assert len(cluster_blocks(many, SHEET_W, SHEET_H)) == 1

    def test_empty(self):
        assert cluster_blocks([], SHEET_W, SHEET_H) == []


class TestMergeSmallClusters:
    def test_tiny_neighbours_are_merged(self):
        """Scattered dimension labels must not become one chunk each."""
        clusters = [[block(f"{i}'-6\"", x0=i * 60, x1=i * 60 + 40)] for i in range(10)]
        groups = merge_small_clusters(clusters, SHEET_W * SHEET_H)
        assert len(groups) < len(clusters)

    def test_area_cap_stops_a_merge_from_spanning_the_sheet(self):
        corners = [
            [block("A", x0=0, y0=0, x1=50, y1=20)],
            [block("B", x0=2300, y0=1500, x1=2400, y1=1520)],
        ]
        assert len(merge_small_clusters(corners, SHEET_W * SHEET_H)) == 2

    def test_full_size_cluster_is_left_alone(self):
        big = [block(words(400))]
        small = [block("TRAILING NOTE", y0=100, y1=120)]
        assert len(merge_small_clusters([big, small], SHEET_W * SHEET_H)) == 2


class TestTables:
    schedule = Table(
        x0=100,
        y0=200,
        x1=900,
        y1=400,
        rows=[
            ["MARK", "SIZE", "TYPE"],
            ["101", "3070", "HM"],
            ["102", "3068", "WD"],
        ],
    )

    def test_markdown_keeps_the_header(self):
        md = table_markdown(normalize_rows(self.schedule.rows))
        assert md.splitlines()[0] == "| MARK | SIZE | TYPE |"
        assert md.splitlines()[1] == "| --- | --- | --- |"
        assert "| 101 | 3070 | HM |" in md

    def test_chunk_carries_the_table_bbox(self):
        (chunk,) = table_chunks(self.schedule)
        assert chunk.bbox == {"x": 100, "y": 200, "width": 800, "height": 200}

    def test_none_cells_and_ragged_rows_are_padded(self):
        rows = normalize_rows([["A", None, "C"], ["1"]])
        assert rows == [["A", "", "C"], ["1", "", ""]]

    def test_pipes_in_cells_are_escaped(self):
        assert normalize_rows([["A|B", "C"]]) == [["A\\|B", "C"]]

    def test_single_column_region_is_rejected(self):
        assert not is_table_like([["ONE"], ["TWO"]])

    def test_prose_sized_cell_is_rejected(self):
        assert not is_table_like([["MARK", "NOTE"], ["1", "x" * 400]])

    def test_oversized_schedule_splits_and_repeats_the_header(self):
        rows = [["MARK", "DESCRIPTION"]]
        rows += [[f"D{i}", words(40, f"c{i}_")] for i in range(40)]
        chunks = table_chunks(Table(x0=0, y0=0, x1=500, y1=800, rows=rows))
        assert len(chunks) > 1
        for c in chunks:
            assert c.text.startswith("| MARK | DESCRIPTION |")
            assert c.token_count <= MAX_TOKENS + 60  # header repeat + last row
            assert c.bbox == {"x": 0, "y": 0, "width": 500, "height": 800}


class TestChunkPageWithTables:
    def test_schedule_becomes_its_own_chunk_and_is_not_double_indexed(self):
        table = Table(
            x0=0,
            y0=0,
            x1=300,
            y1=100,
            rows=[["MARK", "SIZE"], ["101", "3070"]],
        )
        raw = [
            (10, 10, 100, 30, "MARK 101 3070", 0, 0),  # a cell, inside the table
            (0, 900, 300, 950, "GENERAL NOTES APPLY TO ALL SHEETS", 1, 0),
        ]
        chunks = chunk_page(raw, SHEET_W, SHEET_H, tables=[table])
        assert len(chunks) == 2
        table_chunk = next(c for c in chunks if c.text.startswith("|"))
        assert "| 101 | 3070 |" in table_chunk.text
        others = [c for c in chunks if c is not table_chunk]
        assert not any("MARK 101" in c.text for c in others)
        assert any("GENERAL NOTES" in c.text for c in others)

    def test_rejected_table_leaves_its_text_in_the_block_stream(self):
        """A one-column false positive must not swallow the page's text."""
        bogus = Table(x0=0, y0=0, x1=300, y1=100, rows=[["JUST A COLUMN"], ["OF NOTES"]])
        raw = [(10, 10, 100, 30, "REAL PAGE TEXT", 0, 0)]
        chunks = chunk_page(raw, SHEET_W, SHEET_H, tables=[bogus])
        assert len(chunks) == 1
        assert "REAL PAGE TEXT" in chunks[0].text

    def test_distant_blocks_no_longer_share_a_chunk(self):
        raw = [
            (10, 10, 200, 30, "STRUCTURAL NOTE", 0, 0),
            (2200, 1500, 2400, 1520, "ELECTRICAL CALLOUT", 1, 0),
        ]
        chunks = chunk_page(raw, SHEET_W, SHEET_H)
        assert len(chunks) == 2
        for c in chunks:
            assert c.bbox["width"] < SHEET_W / 2

    def test_page_size_is_optional(self):
        raw = [(0, 0, 100, 20, "NOTES", 0, 0)]
        assert len(chunk_page(raw)) == 1
