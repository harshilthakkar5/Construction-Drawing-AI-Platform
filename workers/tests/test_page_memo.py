"""Per-sheet results reused between reviews, and the worker's PDF cache."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import fitz  # noqa: E402

import page_memo  # noqa: E402


def _page(document_id="doc", number=1):
    page = fitz.open().new_page()
    return page_memo.tag(page, document_id, number)


def test_a_tagged_page_is_computed_once_and_every_hit_is_a_copy():
    page_memo.clear()
    calls = []
    compute = lambda: calls.append(1) or {"along_x": {"A": 1.0}}  # noqa: E731
    first = page_memo.memo("t@1", _page(), compute)
    first["along_x"]["A"] = 99.0  # a caller editing its result
    again = page_memo.memo("t@1", _page(), compute)  # a NEW page object, same document page
    assert calls == [1] and again == {"along_x": {"A": 1.0}}


def test_an_untagged_page_and_another_page_or_version_are_computed_fresh():
    page_memo.clear()
    calls = []
    compute = lambda: calls.append(1) or 1  # noqa: E731
    page_memo.memo("t@1", fitz.open().new_page(), compute)
    page_memo.memo("t@1", fitz.open().new_page(), compute)
    page_memo.memo("t@1", _page(number=2), compute)
    page_memo.memo("t@2", _page(number=2), compute)
    assert len(calls) == 4


def test_memory_is_bounded():
    page_memo.clear()
    old, page_memo.MAX_ENTRIES = page_memo.MAX_ENTRIES, 2
    try:
        for n in range(3):
            page_memo.memo("t@1", _page(number=n), lambda: n)
        assert len(page_memo._mem) == 2
    finally:
        page_memo.MAX_ENTRIES = old


def test_the_shared_grid_reading_is_versioned_with_the_scans_grid_cache():
    import grid
    import rfi_scan

    assert grid.STYLED_VERSION == rfi_scan.GRID_CACHE_VERSION


def test_a_pdf_is_downloaded_once_and_the_cache_stays_under_its_cap(tmp_path):
    import rfi_review

    got = []

    def download(key, path):
        got.append(key)
        with open(path, "wb") as f:
            f.write(b"x" * 600_000)

    cache = str(tmp_path)
    a = rfi_review.cached_pdf("a", "k/a", cache, 1, download)
    assert rfi_review.cached_pdf("a", "k/a", cache, 1, download) == a and got == ["k/a"]
    rfi_review.cached_pdf("b", "k/b", cache, 1, download)  # 1.2 MB > 1 MB: the oldest goes
    assert sorted(os.listdir(cache)) == ["b.pdf"]
    rfi_review.cached_pdf("b", "k/b", cache, 0, download)  # cache off: always a fresh copy
    assert got == ["k/a", "k/b", "k/b"]
