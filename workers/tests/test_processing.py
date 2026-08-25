"""Per-page parallel extraction.

A page is independent work, so the pages of one document are processed by a
thread pool. What must NOT change is the failure contract the pipeline rests
on: every page commits on its own, a crash leaves the finished ones saved, and
a retry resumes. These exercise that with the storage/database calls faked, so
the concurrency itself is what is under test.
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import time  # noqa: E402
import tables  # noqa: E402
import fitz  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture
def pdf_path(tmp_path):
    """A small multi-page PDF with real text, written to disk — the extractor
    reopens the file per thread, so it has to be a genuine file."""
    doc = fitz.open()
    for n in range(12):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 200), f"SHEET A-{n + 101}", fontsize=18)
    path = tmp_path / "set.pdf"
    doc.save(path)
    doc.close()
    return str(path)


@pytest.fixture
def processing(monkeypatch):
    """processing with storage and the database stubbed out."""
    import processing as module

    monkeypatch.setattr(module.storage, "put_bytes", lambda *a, **k: None)
    monkeypatch.setattr(module.storage, "page_image_key", lambda *a: "img")
    monkeypatch.setattr(module.storage, "page_thumb_key", lambda *a: "thumb")
    monkeypatch.setattr(module.storage, "page_text_key", lambda *a: "text")
    monkeypatch.setattr(module.db, "upsert_page", lambda *a, **k: None)
    monkeypatch.setattr(module.db, "replace_page_chunks", lambda *a, **k: None)
    return module


class TestParallelExtraction:
    def test_every_page_is_processed_exactly_once(self, processing, pdf_path, monkeypatch):
        seen: list[int] = []
        lock = threading.Lock()
        original = processing._process_page

        def record(project_id, document_id, pdf, index, offset):
            with lock:
                seen.append(index)
            return original(project_id, document_id, pdf, index, offset)

        monkeypatch.setattr(processing, "_process_page", record)
        processed, ocr = processing._extract_pages(
            "p", "d", pdf_path, list(range(12)), 0, 12, "test"
        )
        assert processed == 12
        assert ocr == 0
        assert sorted(seen) == list(range(12))

    def test_pages_really_do_overlap(self, processing, pdf_path, monkeypatch):
        """The point of the change: more than one page in flight at a time."""
        monkeypatch.setattr(processing.config, "PAGE_CONCURRENCY", 4)
        inside = 0
        peak = 0
        lock = threading.Lock()
        start = threading.Event()

        def slow(project_id, document_id, pdf, index, offset):
            nonlocal inside, peak
            with lock:
                inside += 1
                peak = max(peak, inside)
            # Hold long enough that serial execution could not overlap.
            start.wait(0.05)
            with lock:
                inside -= 1
            return False

        monkeypatch.setattr(processing, "_process_page", slow)
        processing._extract_pages("p", "d", pdf_path, list(range(12)), 0, 12, "test")
        assert peak > 1, "pages were still processed one at a time"

    def test_one_thread_still_works(self, processing, pdf_path, monkeypatch):
        """PAGE_CONCURRENCY=1 must behave exactly like the old serial loop."""
        monkeypatch.setattr(processing.config, "PAGE_CONCURRENCY", 1)
        processed, _ = processing._extract_pages(
            "p", "d", pdf_path, list(range(5)), 0, 5, "test"
        )
        assert processed == 5

    def test_nothing_to_do_is_not_an_error(self, processing, pdf_path):
        assert processing._extract_pages("p", "d", pdf_path, [], 0, 0, "test") == (0, 0)

    def test_resume_only_processes_the_pages_it_was_given(self, processing, pdf_path):
        """Resuming after a crash hands in the remaining indices only."""
        processed, _ = processing._extract_pages(
            "p", "d", pdf_path, [7, 8, 9, 10, 11], 0, 12, "test"
        )
        assert processed == 5


class TestFailureContract:
    def test_a_failing_page_raises_so_bullmq_retries(
        self, processing, pdf_path, monkeypatch
    ):
        def explode(project_id, document_id, pdf, index, offset):
            if index == 3:
                raise RuntimeError("page 4 is corrupt")
            return False

        monkeypatch.setattr(processing, "_process_page", explode)
        with pytest.raises(RuntimeError, match="corrupt"):
            processing._extract_pages("p", "d", pdf_path, list(range(12)), 0, 12, "test")

    def test_pages_that_succeeded_before_a_failure_are_kept(
        self, processing, pdf_path, monkeypatch
    ):
        """Partial results survive — the whole reason pages commit individually."""
        monkeypatch.setattr(processing.config, "PAGE_CONCURRENCY", 2)
        committed: list[int] = []
        lock = threading.Lock()

        def half_broken(project_id, document_id, pdf, index, offset):
            if index == 9:
                raise RuntimeError("boom")
            with lock:
                committed.append(index)
            return False

        monkeypatch.setattr(processing, "_process_page", half_broken)
        with pytest.raises(RuntimeError):
            processing._extract_pages("p", "d", pdf_path, list(range(12)), 0, 12, "test")
        assert committed, "no page was committed before the failure"
        assert 9 not in committed


class TestThumbnail:
    def test_rendered_from_the_page_not_the_full_size_png(self, processing, pdf_path):
        """Regression guard: the thumbnail takes a page, not PNG bytes. Passing
        the big PNG back in was ~18 MP of decode per page."""
        doc = fitz.open(pdf_path)
        jpeg = processing._thumbnail_jpg(doc[0])
        doc.close()
        assert jpeg.startswith(b"\xff\xd8"), "not a JPEG"

    def test_thumbnail_is_the_configured_width(self, processing, pdf_path):
        from PIL import Image
        import io

        doc = fitz.open(pdf_path)
        jpeg = processing._thumbnail_jpg(doc[0])
        doc.close()
        with Image.open(io.BytesIO(jpeg)) as img:
            # MuPDF rounds to whole pixels when scaling.
            assert abs(img.width - processing.config.THUMB_WIDTH) <= 2


class TestTableDetectionBudget:
    """Detection cost is unbounded on drawing-heavy sheets — measured at 32s a
    page on a real structural set, against 0.1-0.8s on ordinary ones, which made
    ingest 4.8x slower. The budget caps the damage at ONE slow page per
    document rather than every page."""

    class _Page:
        """A page whose find_tables takes however long the test says."""

        def __init__(self, seconds: float, number: int = 0):
            self.seconds = seconds
            self.number = number

        def find_tables(self):
            time.sleep(self.seconds)
            return type("Found", (), {"tables": []})()

        def get_cdrawings(self):
            return []

    def setup_method(self):
        tables.reset_budget("doc")

    def test_a_fast_page_does_not_trip_it(self, monkeypatch):
        monkeypatch.setattr(tables.config, "TABLE_DETECTION_BUDGET_SECONDS", 0.5)
        tables.find_page_tables(self._Page(0.0), "doc")
        assert "doc" not in tables._gave_up

    def test_one_slow_page_stops_detection_for_the_whole_document(self, monkeypatch):
        monkeypatch.setattr(tables.config, "TABLE_DETECTION_BUDGET_SECONDS", 0.05)
        tables.find_page_tables(self._Page(0.12), "doc")
        assert "doc" in tables._gave_up

    def test_later_pages_return_immediately_without_scanning(self, monkeypatch):
        """The point: the remaining 86 pages must not each pay the 32 seconds."""
        monkeypatch.setattr(tables.config, "TABLE_DETECTION_BUDGET_SECONDS", 0.05)
        tables.find_page_tables(self._Page(0.12), "doc")

        scanned = {"count": 0}

        class _Counting(self._Page):
            def find_tables(self_inner):
                scanned["count"] += 1
                return super().find_tables()

        started = time.perf_counter()
        assert tables.find_page_tables(_Counting(5.0), "doc") == []
        assert scanned["count"] == 0
        assert time.perf_counter() - started < 0.5

    def test_documents_do_not_infect_each_other(self, monkeypatch):
        """Two documents extract concurrently; a drawing set giving up must not
        disable schedules on an architectural set in the next job."""
        monkeypatch.setattr(tables.config, "TABLE_DETECTION_BUDGET_SECONDS", 0.05)
        tables.find_page_tables(self._Page(0.12), "slow-doc")
        assert "slow-doc" in tables._gave_up
        assert "other-doc" not in tables._gave_up
        tables.reset_budget("slow-doc")

    def test_a_retry_gets_a_clean_budget(self, monkeypatch):
        """process_document calls reset_budget on every attempt: a retry
        re-reads the document and must not inherit the give-up."""
        monkeypatch.setattr(tables.config, "TABLE_DETECTION_BUDGET_SECONDS", 0.05)
        tables.find_page_tables(self._Page(0.12), "doc")
        tables.reset_budget("doc")
        assert "doc" not in tables._gave_up

    def test_disabled_entirely_never_scans(self, monkeypatch):
        monkeypatch.setattr(tables.config, "TABLE_EXTRACTION_ENABLED", False)
        assert tables.find_page_tables(self._Page(9.0), "doc") == []
