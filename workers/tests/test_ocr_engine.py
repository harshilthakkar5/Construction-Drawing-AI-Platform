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
    monkeypatch.setattr(ocr, "_calls", {})
    monkeypatch.setattr(ocr, "_memory_strikes", 0)
    # Guards off unless a test turns one on: these tests are about the engine.
    monkeypatch.setattr(ocr.config, "OCR_MIN_FREE_MB", 0)
    monkeypatch.setattr(ocr.config, "OCR_MAX_RSS_MB", 0)
    monkeypatch.setattr(ocr.config, "OCR_ENGINE_RECYCLE", 0)

    def install(**kwargs):
        made["kwargs"] = kwargs
        monkeypatch.setattr(ocr, "_engines", {})
        monkeypatch.setattr(ocr, "_unavailable", set())
        monkeypatch.setattr(ocr, "_get_engine", lambda kind="paddle": ocr._engines.get(kind) or _build(kind))
        return made

    def _build(kind):
        # How many earlier engines are still in memory when a new one is made.
        made["alive_at_build"].append(sum(1 for r in made.setdefault("refs", []) if r() is not None))
        ocr._engines[kind] = FakeEngine(**made["kwargs"])
        made["refs"].append(weakref.ref(ocr._engines[kind]))
        return ocr._engines[kind]

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


def test_a_memory_failure_ends_the_engine_and_only_a_run_of_them_stops_ocr(engines):
    # "could not create a primitive" is oneDNN failing to allocate. The engine
    # lives in its own process, so ending it hands its memory back and the
    # next image starts fresh; retrying THIS image at once is what took Docker
    # and the terminal down, so it is never retried on the spot.
    engines(always_fail=True, error="could not create a primitive")
    img = np.zeros((1600, 1600, 3), dtype=np.uint8)
    for built in (1, 2):
        with pytest.raises(ocr.OcrError, match="running out of memory"):
            ocr._predict(img)
        assert FakeEngine.built == built and not ocr._engines  # ended, not rebuilt on the spot
        assert ocr.halted() is None  # one bad image is not a broken machine
    with pytest.raises(ocr.OcrError, match="3 times in a row"):
        ocr._predict(img)
    assert ocr.halted()
    calls = len(FakeEngine.shapes)
    with pytest.raises(ocr.OcrError):
        ocr._predict(img)
    assert len(FakeEngine.shapes) == calls  # never asked again
    assert ocr.available() is False


def test_a_success_between_memory_failures_resets_the_count(engines, monkeypatch):
    engines(always_fail=True, error="could not create a primitive")
    monkeypatch.setattr(ocr, "_memory_strikes", 0)
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    for _ in range(2):
        with pytest.raises(ocr.OcrError):
            ocr._predict(img)
    engines()  # a fresh, working engine
    assert ocr._predict(img)
    assert ocr._memory_strikes == 0


def test_low_free_memory_refuses_the_call_without_stopping_ocr(engines, monkeypatch):
    engines()
    monkeypatch.setattr(ocr.config, "OCR_MIN_FREE_MB", 1024)
    monkeypatch.setattr(ocr, "_free_mb", lambda: 300.0)
    with pytest.raises(ocr.OcrError, match="300 MB of memory free"):
        ocr._predict(np.zeros((50, 50, 3), dtype=np.uint8))
    assert FakeEngine.shapes == [] and ocr.halted() is None
    monkeypatch.setattr(ocr, "_free_mb", lambda: 5000.0)
    assert ocr._predict(np.zeros((50, 50, 3), dtype=np.uint8))


def test_an_engine_over_the_memory_cap_is_replaced_not_stopped(engines, monkeypatch):
    engines()
    monkeypatch.setattr(ocr.config, "OCR_MAX_RSS_MB", 4000)
    img = np.zeros((50, 50, 3), dtype=np.uint8)
    ocr._predict(img)
    ocr._engines["paddle"].rss_mb = lambda: 4500.0  # the engine's own process grew past the cap
    ocr._predict(img)
    assert FakeEngine.built == 2 and ocr.halted() is None


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
    monkeypatch.setattr(ocr, "available", lambda stage="upload": True)
    monkeypatch.setattr(page_ocr, "plan", lambda page: "text drawn as shapes")
    calls = []

    def broken(page, stage="upload"):
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
    assert "could not be read on 10 page(s)" in note
    # The raw error and the setting names go to the worker log, never to the screen.
    assert "could not create a primitive" not in note and "OCR_" not in note


def test_the_scan_stops_at_once_when_ocr_was_stopped_for_memory(monkeypatch):
    import contextlib

    import page_ocr
    import rfi_review
    import rfi_scan

    todo = [("doc-1", "k", n) for n in range(1, 11)]
    monkeypatch.setattr(rfi_scan.config, "OCR_ENABLED", True)
    monkeypatch.setattr(rfi_scan.db, "pages_needing_ocr", lambda *a: todo)
    monkeypatch.setattr(rfi_scan, "_sheet_names", lambda pid: {})
    monkeypatch.setattr(ocr, "available", lambda stage="upload": True)
    monkeypatch.setattr(page_ocr, "plan", lambda page: "text drawn as shapes")
    monkeypatch.setattr(ocr, "_halted", None)
    calls = []

    def out_of_memory(page, stage="upload"):
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
    assert "could not be read on 10 page(s)" in notes[0]


def test_a_letter_o_read_where_a_zero_belongs_is_a_zero():
    # Victaulic: every structural sheet is stroke text, and OCR read S0.1 as "SO.1"
    # twenty times, so no reference to the footing schedule reached its sheet.
    import ocr

    assert ocr.zeros_for_os("FOOTING SCHEDULE SEE SHEET SO.1.") == "FOOTING SCHEDULE SEE SHEET S0.1."
    assert ocr.zeros_for_os("SHEET SO.2 AND SO.3") == "SHEET S0.2 AND S0.3"
    assert ocr.zeros_for_os("DEAD LOAD = 1O psf, 3OOO psi, SCH 4O, IBC 17O4") == \
        "DEAD LOAD = 10 psf, 3000 psi, SCH 40, IBC 1704"
    assert ocr.zeros_for_os("AT 2O' AND 1OO’ INTERVAL") == "AT 20' AND 100’ INTERVAL"
    # Words keep their O: no digit run, no discipline prefix, or more letters around it.
    for kept in ("SO THAT", "NO.1", "TO BE", "O2 SENSOR", "4OD", "SCHOOL 2", "C-1O1A", "SOME 1.5"):
        assert ocr.zeros_for_os(kept) == kept, kept


def test_free_memory_respects_the_container_limit(tmp_path, monkeypatch):
    """Inside a container psutil reports the whole Docker VM; the room left
    under WORKER_MEM_LIMIT is what decides whether the next image fits."""
    (tmp_path / "memory.max").write_text(str(5 * 2**30))
    (tmp_path / "memory.current").write_text(str(int(4.8 * 2**30)))
    assert round(ocr._cgroup_free_mb(str(tmp_path))) == 205

    (tmp_path / "memory.max").write_text("max")  # no limit set
    assert ocr._cgroup_free_mb(str(tmp_path)) is None
    assert ocr._cgroup_free_mb(str(tmp_path / "missing")) is None  # not in a container

    monkeypatch.setattr(ocr, "_cgroup_free_mb", lambda root="": 205.0)
    assert ocr._free_mb() <= 205.0  # the tighter of the VM's and the container's


def test_with_ocr_off_a_page_that_needs_it_is_left_for_a_later_scan(monkeypatch):
    """Uploading with OCR_ENABLED=false must not record a textless page as
    'checked, nothing to read' — the scan would then never read it."""
    import fitz

    import page_ocr

    monkeypatch.setattr(ocr, "available", lambda stage="upload": False)
    doc = fitz.open()
    page = doc.new_page()  # no text layer at all
    reading = page_ocr.read_page(page)
    assert reading.reason is not None and not reading.ran  # processing leaves it unmarked


FAKE_PADDLEOCR = """
import os


class PaddleOCR:
    def __init__(self, **kwargs):
        if os.environ.get("FAKE_OCR_FAIL_START"):
            raise RuntimeError("model download dropped")
        self.mkldnn = kwargs.get("enable_mkldnn")

    def ocr(self, img, cls=True):
        if img.shape[0] == 13:  # the process dies, as the OS ends one out of memory
            os._exit(9)
        if img.shape[0] == 7:
            raise RuntimeError("unsupported input layout")
        return [[[[[0, 0], [1, 0], [1, 1], [0, 1]], ("MKLDNN" if self.mkldnn else "RTU-3", 0.99)]]]
"""


@pytest.fixture
def fake_paddle(tmp_path, monkeypatch):
    pkg = tmp_path / "paddleocr"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(FAKE_PADDLEOCR)
    monkeypatch.syspath_prepend(str(tmp_path))  # a spawned child gets the parent's sys.path
    return tmp_path


def test_the_engine_runs_in_its_own_process(fake_paddle):
    engine = ocr._ProcessEngine(960)
    try:
        assert engine._proc.pid != __import__("os").getpid()
        assert engine.ocr(np.zeros((20, 20, 3), dtype=np.uint8))[0][0][1][0] == "RTU-3"
        with pytest.raises(RuntimeError, match="unsupported input layout"):
            engine.ocr(np.zeros((7, 20, 3), dtype=np.uint8))
        assert engine.ocr(np.zeros((20, 20, 3), dtype=np.uint8))  # an error is not a dead engine
    finally:
        engine.close()
    assert not engine._proc.is_alive()  # closing ends the process and its memory


def test_a_process_that_dies_reads_as_running_out_of_memory(fake_paddle):
    engine = ocr._ProcessEngine(960)
    with pytest.raises(RuntimeError) as failure:
        engine.ocr(np.zeros((13, 20, 3), dtype=np.uint8))
    assert ocr._looks_like_memory(str(failure.value))
    assert not engine._proc.is_alive()


def test_an_engine_that_cannot_start_says_why(fake_paddle, monkeypatch):
    monkeypatch.setenv("FAKE_OCR_FAIL_START", "1")
    with pytest.raises(RuntimeError, match="model download dropped"):
        ocr._ProcessEngine(960)


def test_the_intel_speed_up_reaches_the_engine_process(fake_paddle):
    engine = ocr._ProcessEngine(960, True)
    try:
        assert engine.ocr(np.zeros((20, 20, 3), dtype=np.uint8))[0][0][1][0] == "MKLDNN"
    finally:
        engine.close()


def test_the_intel_speed_up_is_turned_off_after_the_first_failure(engines, monkeypatch):
    engines(fail_above=ocr.FALLBACK_LIMIT)
    monkeypatch.setattr(ocr, "_mkldnn", True)
    assert ocr._predict(np.zeros((1600, 1200, 3), dtype=np.uint8))  # retried smaller and read
    assert ocr._mkldnn is False  # every engine from now on is built without it


# --- choosing the engine per stage -------------------------------------------

FAKE_RAPIDOCR = """
class RapidOCR:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __call__(self, img):
        if img.shape[0] == 5:
            return None, None  # nothing found
        return [[[[0, 0], [1, 0], [1, 1], [0, 1]], "RTU-3", 0.97]], [0.1]
"""


def test_engine_settings_fall_back_and_refuse_a_typo(monkeypatch):
    import config

    monkeypatch.delenv("OCR_X", raising=False)
    assert config._ocr_engine("OCR_X", "paddle") == "paddle"
    monkeypatch.setenv("OCR_X", " Rapid ")
    assert config._ocr_engine("OCR_X", "paddle") == "rapid"
    monkeypatch.setenv("OCR_X", "tesseract")
    with pytest.raises(ValueError, match="paddle, rapid or off"):
        config._ocr_engine("OCR_X", "paddle")


def test_each_stage_uses_its_own_engine_and_keeps_it(engines, monkeypatch):
    import fitz

    engines()
    built = []
    monkeypatch.setattr(ocr, "_get_engine", lambda kind="paddle": ocr._engines.get(kind)
                        or ocr._engines.setdefault(kind, built.append(kind) or FakeEngine()))
    monkeypatch.setattr(ocr.config, "OCR_ENGINE_UPLOAD", "rapid")
    monkeypatch.setattr(ocr.config, "OCR_ENGINE_RFI", "paddle")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
    for stage in ("upload", "rfi", "upload", "rfi"):
        assert ocr.image_lines(pix, stage=stage)[0][1] == "RTU-3"
    # Built once each and both kept: an upload and a scan never rebuild each other's engine.
    assert built == ["rapid", "paddle"] and set(ocr._engines) == {"rapid", "paddle"}


def test_a_stage_set_off_reads_nothing_and_leaves_the_page_for_the_scan(engines, monkeypatch):
    import fitz

    import page_ocr

    engines()
    monkeypatch.setattr(ocr.config, "OCR_ENGINE_UPLOAD", "off")
    monkeypatch.setattr(ocr.config, "OCR_ENGINE_RFI", "paddle")
    assert ocr.available("upload") is False and ocr.available("rfi") is True
    page = fitz.open().new_page()  # no text layer: needs OCR
    assert page_ocr.read_page(page, "upload").ran is False  # unmarked, so a scan reads it
    assert page_ocr.read_page(page, "rfi").ran is True


def test_rapidocr_replies_are_read_in_paddles_shape():
    box = [[0, 0], [9, 0], [9, 4], [0, 4]]
    assert ocr._rapid_as_paddle([[box, "F6.0", 0.9]]) == [[[box, ("F6.0", 0.9)]]]
    assert ocr._rapid_as_paddle(None) == [[]]  # nothing found is an empty reading, not a failure


def test_the_rapid_engine_runs_in_its_own_process(tmp_path, monkeypatch):
    pkg = tmp_path / "rapidocr_onnxruntime"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(FAKE_RAPIDOCR)
    monkeypatch.syspath_prepend(str(tmp_path))
    engine = ocr._ProcessEngine(960, False, "rapid")
    try:
        [[(quad, (text, conf))]] = engine.ocr(np.zeros((20, 20, 3), dtype=np.uint8))
        assert text == "RTU-3" and conf == 0.97 and quad[2] == [1, 1]
        assert engine.ocr(np.zeros((5, 20, 3), dtype=np.uint8)) == [[]]
    finally:
        engine.close()


def test_rapid_without_its_english_model_is_refused_not_run(tmp_path, monkeypatch, caplog):
    """The model bundled with RapidOCR runs words together ("RTU-380MBH"), so
    with the English model missing the engine is unavailable, never degraded."""
    pkg = tmp_path / "rapidocr_onnxruntime"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(FAKE_RAPIDOCR)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(ocr, "_engines", {})
    monkeypatch.setattr(ocr, "_unavailable", set())
    monkeypatch.setattr(ocr.config, "OCR_ENABLED", True)
    monkeypatch.setattr(ocr.config, "OCR_RAPID_REC_MODEL", str(tmp_path / "missing.onnx"))
    with caplog.at_level("WARNING", logger=ocr.log.name):
        assert ocr._get_engine("rapid") is None
    assert "rapid" in ocr._unavailable and not ocr._engines
    # The fix it names is the model, not a package install that would change nothing.
    assert "download the model" in caplog.text and "pip install" not in caplog.text


def test_real_rapidocr_keeps_the_spaces_between_words():
    """Runs where RapidOCR and its English model are installed (the worker
    image); skipped elsewhere. CAD text sizes, read at page_ocr's 200 DPI."""
    import os

    pytest.importorskip("rapidocr_onnxruntime")
    if not os.path.isfile(ocr.config.OCR_RAPID_REC_MODEL):
        pytest.skip("RapidOCR's English model is not downloaded")
    import fitz

    page = fitz.open().new_page(width=400, height=160)
    page.insert_text((30, 50), "FOOTING SCHEDULE F6.0", fontsize=8)
    page.insert_text((30, 100), "RTU-3 80 MBH SEE 5/S-501", fontsize=8)
    pix = page.get_pixmap(dpi=200, alpha=False)
    engine = ocr._ProcessEngine(1600, False, "rapid")
    try:
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)[:, :, ::-1].copy()
        [lines] = engine.ocr(img)
    finally:
        engine.close()
    read = {text for _, (text, _) in lines}
    assert {"FOOTING SCHEDULE F6.0", "RTU-3 80 MBH SEE 5/S-501"} <= read


@pytest.mark.parametrize("cuda, device, expected", [
    (True, "gpu:0", "GPU"),
    (True, "cpu", "CPU (the GPU version is installed but no graphics card is visible"),
    (False, "cpu", "CPU"),
])
def test_the_engine_says_whether_it_runs_on_the_graphics_card(fake_paddle, cuda, device, expected):
    (fake_paddle / "paddle").mkdir()
    (fake_paddle / "paddle" / "__init__.py").write_text(
        f"import types\nis_compiled_with_cuda = lambda: {cuda}\n"
        f"device = types.SimpleNamespace(get_device=lambda: {device!r})\n")
    engine = ocr._ProcessEngine(960)
    try:
        assert engine.device.startswith(expected)
    finally:
        engine.close()


def test_no_paddle_to_ask_reads_as_cpu(fake_paddle):
    engine = ocr._ProcessEngine(960)  # the fake paddleocr ships without a paddle package
    try:
        assert engine.device == "CPU"
    finally:
        engine.close()
