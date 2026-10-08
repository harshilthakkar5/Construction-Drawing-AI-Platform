"""The PaddleOCR engine is shared by every thread in a worker and is not
thread-safe. A scan logged "could not create a primitive" on page after page:
after one bad run the engine fails every call. These pin the three guards —
one call at a time, rebuild and retry smaller after a failure, and a scan
that stops asking a broken engine instead of failing a hundred pages."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import ocr  # noqa: E402


class FakeEngine:
    inside = 0
    most_inside = 0
    built = 0

    def __init__(self, fail_above=None, always_fail=False):
        FakeEngine.built += 1
        self.fail_above = fail_above
        self.always_fail = always_fail

    def ocr(self, img, cls=True):
        FakeEngine.inside += 1
        FakeEngine.most_inside = max(FakeEngine.most_inside, FakeEngine.inside)
        try:
            time.sleep(0.01)
            if self.always_fail or (self.fail_above and max(img.shape[:2]) > self.fail_above):
                raise RuntimeError("could not create a primitive")
            h, w = img.shape[:2]
            return [[[[[w / 2, h / 2], [w / 2 + 10, h / 2], [w / 2 + 10, h / 2 + 5], [w / 2, h / 2 + 5]],
                      ("RTU-3", 0.99)]]]
        finally:
            FakeEngine.inside -= 1


@pytest.fixture
def engines(monkeypatch):
    FakeEngine.inside = FakeEngine.most_inside = FakeEngine.built = 0
    made = {"kwargs": {}}

    def install(**kwargs):
        made["kwargs"] = kwargs
        monkeypatch.setattr(ocr, "_engine", None)
        monkeypatch.setattr(ocr, "_unavailable", False)
        monkeypatch.setattr(ocr, "_get_engine", lambda: ocr._engine or _build())

    def _build():
        ocr._engine = FakeEngine(**made["kwargs"])
        return ocr._engine

    return install


def test_threads_never_run_the_engine_at_once(engines):
    engines()
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    threads = [threading.Thread(target=ocr._predict, args=(img,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert FakeEngine.most_inside == 1


def test_a_failed_image_is_retried_smaller_on_a_new_engine_in_the_callers_pixels(engines):
    engines(fail_above=ocr.FALLBACK_LIMIT)
    img = np.zeros((1600, 1200, 3), dtype=np.uint8)
    [[item]] = ocr._predict(img)
    assert FakeEngine.built == 2  # thrown away and rebuilt
    (x0, y0), *_ = item[0]
    assert (round(x0), round(y0)) == (600, 800)  # centre of the 1200x1600 image, not of the small copy
    assert item[1] == ("RTU-3", 0.99)


def test_an_engine_that_fails_every_time_raises_rather_than_reading_nothing(engines):
    engines(always_fail=True)
    with pytest.raises(ocr.OcrError, match="primitive"):
        ocr._predict(np.zeros((50, 50, 3), dtype=np.uint8))


def test_the_scan_stops_asking_a_broken_engine_and_says_so(monkeypatch):
    import contextlib

    import page_ocr
    import rfi_scan

    todo = [("doc-1", "k", n) for n in range(1, 11)]
    monkeypatch.setattr(rfi_scan.config, "OCR_ENABLED", True)
    monkeypatch.setattr(rfi_scan.db, "pages_needing_ocr", lambda *a: todo)
    monkeypatch.setattr(rfi_scan, "_sheet_names", lambda pid: {})
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(page_ocr, "plan", lambda page: "text drawn as shapes")
    calls = []

    def broken(page):
        calls.append(page)
        raise ocr.OcrError("could not create a primitive")

    monkeypatch.setattr(page_ocr, "read_page", broken)
    marked = []
    monkeypatch.setattr(rfi_scan.db, "set_page_ocr", lambda *a: marked.append(a))
    monkeypatch.setattr(rfi_scan.db, "replace_page_ocr", lambda *a: marked.append(a))

    @contextlib.contextmanager
    def documents(project_id, ids):
        yield lambda d, n: object()

    import rfi_review

    monkeypatch.setattr(rfi_review, "_documents", documents)
    notes = rfi_scan.ocr_pending("project-1")
    assert len(calls) == rfi_scan.OCR_FAILURE_STREAK  # not ten
    assert marked == []  # nothing marked examined: a working worker reads them later
    [note] = notes
    assert "OCR FAILED on 10 page(s)" in note and "could not create a primitive" in note
