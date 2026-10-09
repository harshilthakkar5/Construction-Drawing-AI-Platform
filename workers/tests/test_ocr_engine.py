"""The PaddleOCR engine is shared by every thread in a worker and is not
thread-safe. A scan logged "could not create a primitive" on page after page:
after one bad run the engine fails every call. These pin the three guards —
one call at a time, rebuild and retry smaller after a failure, and a scan
that stops asking a broken engine instead of failing a hundred pages."""

import sys
import threading
import time
import weakref
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import ocr  # noqa: E402


class FakeEngine:
    inside = 0
    most_inside = 0
    built = 0
    shapes: list = []

    def __init__(self, fail_above=None, always_fail=False, error="unsupported input layout"):
        FakeEngine.built += 1
        self.fail_above = fail_above
        self.always_fail = always_fail
        self.error = error
        self.weights = bytearray(1024)  # stands in for the model in memory

    def ocr(self, img, cls=True):
        FakeEngine.inside += 1
        FakeEngine.most_inside = max(FakeEngine.most_inside, FakeEngine.inside)
        FakeEngine.shapes.append(img.shape[:2])
        try:
            time.sleep(0.01)
            if self.always_fail or (self.fail_above and max(img.shape[:2]) > self.fail_above):
                raise RuntimeError(self.error)
            h, w = img.shape[:2]
            return [[[[[w / 2, h / 2], [w / 2 + 10, h / 2], [w / 2 + 10, h / 2 + 5], [w / 2, h / 2 + 5]],
                      ("RTU-3", 0.99)]]]
        finally:
            FakeEngine.inside -= 1


@pytest.fixture
def engines(monkeypatch):
    FakeEngine.inside = FakeEngine.most_inside = FakeEngine.built = 0
    FakeEngine.shapes = []
    made = {"kwargs": {}, "alive_at_build": []}
    monkeypatch.setattr(ocr, "_halted", None)
    monkeypatch.setattr(ocr, "_calls", 0)
    # Guards off unless a test turns one on: these tests are about the engine.
    monkeypatch.setattr(ocr.config, "OCR_MIN_FREE_MB", 0)
    monkeypatch.setattr(ocr.config, "OCR_MAX_RSS_MB", 0)
    monkeypatch.setattr(ocr.config, "OCR_ENGINE_RECYCLE", 0)

    def install(**kwargs):
        made["kwargs"] = kwargs
        monkeypatch.setattr(ocr, "_engine", None)
        monkeypatch.setattr(ocr, "_unavailable", False)
        monkeypatch.setattr(ocr, "_get_engine", lambda: ocr._engine or _build())
        return made

    def _build():
        # How many earlier engines are still in memory when a new one is made.
        made["alive_at_build"].append(sum(1 for r in made.setdefault("refs", []) if r() is not None))
        ocr._engine = FakeEngine(**made["kwargs"])
        made["refs"].append(weakref.ref(ocr._engine))
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
    made = engines(fail_above=ocr.FALLBACK_LIMIT)
    img = np.zeros((1600, 1200, 3), dtype=np.uint8)
    [[item]] = ocr._predict(img)
    assert FakeEngine.built == 2  # thrown away and rebuilt
    # ...and the failed engine was gone BEFORE the new one was built: the old
    # recovery kept it alive through the exception's traceback, so a worker
    # already short of memory briefly held two engines.
    assert made["alive_at_build"] == [0, 0]
    (x0, y0), *_ = item[0]
    assert (round(x0), round(y0)) == (600, 800)  # centre of the 1200x1600 image, not of the small copy
    assert item[1] == ("RTU-3", 0.99)


def test_an_engine_that_fails_every_time_raises_rather_than_reading_nothing(engines):
    engines(always_fail=True)
    with pytest.raises(ocr.OcrError, match="unsupported"):
        ocr._predict(np.zeros((50, 50, 3), dtype=np.uint8))
    assert ocr.halted() is None  # not a memory failure: the next page may still read


def test_a_memory_failure_stops_ocr_in_the_worker_instead_of_building_another_engine(engines):
    # "could not create a primitive" is oneDNN failing to allocate. Retrying
    # builds a second engine on a machine already out of memory — the step
    # that took Docker and the terminal down with the worker.
    engines(always_fail=True, error="could not create a primitive")
    img = np.zeros((1600, 1600, 3), dtype=np.uint8)
    with pytest.raises(ocr.OcrError, match="running out of memory"):
        ocr._predict(img)
    assert FakeEngine.built == 1 and ocr._engine is None  # released, never rebuilt
    assert ocr.halted()
    calls = len(FakeEngine.shapes)
    with pytest.raises(ocr.OcrError):
        ocr._predict(img)
    assert len(FakeEngine.shapes) == calls and FakeEngine.built == 1  # never asked again
    assert ocr.available() is False


def test_low_free_memory_refuses_the_call_without_stopping_ocr(engines, monkeypatch):
    engines()
    monkeypatch.setattr(ocr.config, "OCR_MIN_FREE_MB", 1024)
    monkeypatch.setattr(ocr, "_free_mb", lambda: 300.0)
    with pytest.raises(ocr.OcrError, match="300 MB of memory free"):
        ocr._predict(np.zeros((50, 50, 3), dtype=np.uint8))
    assert FakeEngine.shapes == [] and ocr.halted() is None
    monkeypatch.setattr(ocr, "_free_mb", lambda: 5000.0)
    assert ocr._predict(np.zeros((50, 50, 3), dtype=np.uint8))


def test_a_worker_too_large_releases_the_engine_and_stops_only_if_that_does_not_help(engines, monkeypatch):
    engines()
    monkeypatch.setattr(ocr.config, "OCR_MAX_RSS_MB", 4000)
    readings = iter([4500.0, 3000.0])  # over the cap, and under it once the engine is gone
    monkeypatch.setattr(ocr, "_rss_mb", lambda: next(readings))
    ocr._predict(np.zeros((50, 50, 3), dtype=np.uint8))
    assert ocr.halted() is None
    monkeypatch.setattr(ocr, "_rss_mb", lambda: 4500.0)
    with pytest.raises(ocr.OcrError, match="OCR_MAX_RSS_MB=4000"):
        ocr._predict(np.zeros((50, 50, 3), dtype=np.uint8))
    assert ocr.halted()


def test_the_engine_is_rebuilt_every_n_images(engines, monkeypatch):
    engines()
    monkeypatch.setattr(ocr.config, "OCR_ENGINE_RECYCLE", 3)
    for _ in range(7):
        ocr._predict(np.zeros((50, 50, 3), dtype=np.uint8))
    assert FakeEngine.built == 3  # images 1-3, 4-6, 7


def test_every_tile_reaches_the_engine_at_one_size_with_its_own_coordinates(engines):
    import fitz

    engines()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1200, 700), False)
    pix.set_rect(pix.irect, (255, 255, 255))
    [(quad, text, conf)] = ocr.image_lines(pix, pad_to=1600)
    assert FakeEngine.shapes == [(1600, 1600)]
    # The fake reads at the image's centre; padding on the right and bottom
    # leaves the original pixels where they were.
    assert quad[0] == [800, 800] and text == "RTU-3"


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
    monkeypatch.setattr(ocr, "_halted", None)
    notes = rfi_scan.ocr_pending("project-1")
    assert len(calls) == rfi_scan.OCR_FAILURE_STREAK  # not ten
    assert marked == []  # nothing marked examined: a working worker reads them later
    [note] = notes
    assert "OCR FAILED on 10 page(s)" in note and "could not create a primitive" in note


def test_the_scan_stops_at_once_when_ocr_was_stopped_for_memory(monkeypatch):
    import contextlib

    import page_ocr
    import rfi_review
    import rfi_scan

    todo = [("doc-1", "k", n) for n in range(1, 11)]
    monkeypatch.setattr(rfi_scan.config, "OCR_ENABLED", True)
    monkeypatch.setattr(rfi_scan.db, "pages_needing_ocr", lambda *a: todo)
    monkeypatch.setattr(rfi_scan, "_sheet_names", lambda pid: {})
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(page_ocr, "plan", lambda page: "text drawn as shapes")
    monkeypatch.setattr(ocr, "_halted", None)
    calls = []

    def out_of_memory(page):
        calls.append(page)
        monkeypatch.setattr(ocr, "_halted", "the worker ran out of memory")
        raise ocr.OcrError("the worker ran out of memory")

    monkeypatch.setattr(page_ocr, "read_page", out_of_memory)

    @contextlib.contextmanager
    def documents(project_id, ids):
        yield lambda d, n: object()

    monkeypatch.setattr(rfi_review, "_documents", documents)
    notes = rfi_scan.ocr_pending("project-1")
    assert len(calls) == 1  # the first failure, then no more asking
    assert "OCR FAILED on 10 page(s)" in notes[0]
