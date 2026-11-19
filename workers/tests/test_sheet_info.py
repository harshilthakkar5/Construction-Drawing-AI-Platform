"""The title-block step's sheet read: one call per batch for sheet number, title,
levels and kind, with every answer held to what the sheet prints."""
import json
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import classify  # noqa: E402
import llm  # noqa: E402
import scrape  # noqa: E402
import sheet_info  # noqa: E402
from sheet_info import SheetInfo  # noqa: E402


# --- Grounding: the model may choose among printed levels, never invent one ------------

def test_a_printed_level_is_kept_and_an_invented_one_dropped():
    text = "FORMING PLAN - LEVEL 3"
    assert sheet_info.grounded_level("LEVEL 3", text) == "LEVEL 3"
    assert sheet_info.grounded_level("Level 03", text) == "LEVEL 3"
    assert sheet_info.grounded_level("LEVEL 7", text) is None


def test_levels_written_together_each_count():
    text = "FRAMING PLAN LEVEL 3 AND 4"
    assert sheet_info.grounded_level("LEVEL 4", text) == "LEVEL 4"
    assert sheet_info.grounded_level("LEVEL 3-4", text) == "LEVEL 3-4"
    assert sheet_info.grounded_level("LEVEL 7-9", "PT PLAN - LEVELS 7-9") == "LEVEL 7-9"


def test_a_number_in_a_reference_or_a_sheet_number_is_not_a_floor():
    assert sheet_info.grounded_level("LEVEL 4", "SEE 4/S5.01\nFOUNDATION PLAN") is None
    assert sheet_info.grounded_level("LEVEL 2", "LEVEL SLAB ON A2") is None
    assert sheet_info.grounded_level("LEVEL 2", "SEE S2.01 FOR LEVELS") is None


def test_floor_words_and_named_levels():
    assert sheet_info.grounded_level("LEVEL 2", "SECOND FLOOR PLAN") == "LEVEL 2"
    assert sheet_info.grounded_level("LEVEL B2", "BASEMENT 2 - FOUNDATION PLAN") == "LEVEL B2"
    assert sheet_info.grounded_level("ROOF", "ROOF FRAMING PLAN") == "ROOF"
    assert sheet_info.grounded_level("ROOF", "FOUNDATION PLAN") is None
    assert sheet_info.grounded_level("the third one", "LEVEL 3 PLAN") is None
    assert sheet_info.grounded_level(None, "LEVEL 3 PLAN") is None


def test_a_title_must_be_printed():
    assert sheet_info.grounded_title("Forming plan - Level 3", "FORMING PLAN - LEVEL 3") == "Forming plan - Level 3"
    assert sheet_info.grounded_title("ROOF FRAMING PLAN", "FORMING PLAN - LEVEL 3") is None
    assert sheet_info.grounded_title("", "X") is None


def test_ranges_expand_to_one_level_per_floor_and_fold_back():
    assert sheet_info.expand(["LEVEL 7-9", "ROOF", "LEVEL 8"]) == ["LEVEL 7", "LEVEL 8", "LEVEL 9", "ROOF"]
    assert sheet_info.expand(["LEVEL 1-200"]) == ["LEVEL 1-200"]  # not one sheet's floors
    assert sheet_info.summary_level(["LEVEL 7", "LEVEL 8", "LEVEL 9"]) == "LEVEL 7-9"
    assert sheet_info.summary_level(["LEVEL 3"]) == "LEVEL 3"
    assert sheet_info.summary_level(["LEVEL 3", "ROOF"]) is None
    assert sheet_info.summary_level(["LEVEL 3", "LEVEL 5"]) is None
    assert sheet_info.summary_level([]) is None


# --- Parsing the batch reply --------------------------------------------------------------

def _texts(*titles):
    return [sheet_info.snippet(None, title, None) for title in titles]


def test_the_reply_is_aligned_and_fenced():
    texts = _texts("FORMING PLAN - LEVEL 3", "WALL SECTIONS")
    raw = json.dumps({"sheets": [
        {"index": 1, "sheet_number": "S2.03", "prefix": "S", "confidence": 0.9,
         "title": "FORMING PLAN - LEVEL 3", "levels": ["LEVEL 3", "LEVEL 9"], "kind": "plan"},
        {"index": 2, "sheet_number": "A5.01", "prefix": "Z", "confidence": 0.9,
         "title": "INVENTED TITLE", "levels": [], "kind": "spaceship"},
        {"index": 7, "sheet_number": "M1", "prefix": "M", "confidence": 0.9},
    ]})
    got = sheet_info.parse_batch(raw, texts)
    assert set(got) == {0, 1}
    assert got[0] == SheetInfo(("S2.03", "structural"), "FORMING PLAN - LEVEL 3", ["LEVEL 3"], "plan")
    # The discipline comes from the table, by the sheet number, never from the model's prefix.
    assert got[1].sheet == ("A5.01", "architectural")
    assert got[1].title is None and got[1].kind is None


def test_an_index_answered_twice_is_trusted_for_neither():
    texts = _texts("LEVEL 1 PLAN", "LEVEL 2 PLAN")
    raw = json.dumps({"sheets": [
        {"index": 1, "sheet_number": "A1", "prefix": "A", "confidence": 0.9, "levels": ["LEVEL 1"]},
        {"index": 1, "sheet_number": "A2", "prefix": "A", "confidence": 0.9, "levels": ["LEVEL 2"]},
        {"index": "2", "sheet_number": "A2", "prefix": "A", "confidence": 0.9, "levels": ["LEVEL 2"]},
    ]})
    got = sheet_info.parse_batch(raw, texts)
    assert set(got) == {1} and got[1].levels == ["LEVEL 2"]
    assert sheet_info.parse_batch("not json", texts) == {}


# --- The call: one per batch, cached, nothing invented on failure -------------------------

class FakeRedis(dict):
    def get(self, key):
        return super().get(key)

    def set(self, key, value, ex=None):
        self[key] = value


def _answer_all(calls):
    def complete_json(system, user, project_id=None, max_tokens=None):
        calls.append(user)
        count = user.count("<sheet index=")
        return json.dumps({"sheets": [
            {"index": i + 1, "sheet_number": f"A{i + 1}", "prefix": "A", "confidence": 0.9,
             "title": None, "levels": ["LEVEL 1"], "kind": "plan"} for i in range(count)
        ]})
    return complete_json


def test_one_call_per_batch_and_the_second_read_is_free(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(sheet_info.sheetllm, "complete_json", _answer_all(calls))
    monkeypatch.setattr(classify, "SHEET_BATCH_SIZE", 25)
    items = [(f"A{n}", f"FLOOR PLAN - LEVEL 1\nSHEET {n}", None) for n in range(30)]
    redis = FakeRedis()
    first = sheet_info.read(items, redis)
    assert len(calls) == 2  # 30 distinct sheets, 25 a call
    assert all(isinstance(info, SheetInfo) and info.levels == ["LEVEL 1"] for info in first)
    second = sheet_info.read(items, redis)
    assert len(calls) == 2 and [i.to_json() for i in second] == [i.to_json() for i in first]


def test_no_answer_is_a_miss_and_is_not_cached(monkeypatch):
    monkeypatch.setattr(sheet_info.sheetllm, "complete_json", lambda *a, **k: None)
    redis = FakeRedis()
    got = sheet_info.read([("A1", "LEVEL 1 PLAN", None)], redis)
    assert sheet_info.is_miss(got[0]) and not redis


def test_with_ai_off_nothing_is_asked(monkeypatch):
    monkeypatch.setenv("SHEET_EXTRACTION", "rules")
    monkeypatch.setattr(sheet_info.sheetllm, "complete_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    assert sheet_info.is_miss(sheet_info.read([("A1", "LEVEL 1 PLAN", None)])[0])


def test_code_fills_in_when_the_model_did_not_answer():
    # No model answer: the title, the index entry and the kind come from code.
    levels, title, kind = sheet_info.final(sheet_info._MISS, "S2.03", "FORMING PLAN - LEVEL 3", None)
    assert levels == ["LEVEL 3"] and title is None and kind == "plan"
    levels, title, kind = sheet_info.final(sheet_info._MISS, "S2.06", "", "Forming Plan - Level 6")
    assert levels == ["LEVEL 6"] and title == "Forming Plan - Level 6"
    # The model answered and found a range: one entry per floor.
    info = SheetInfo(("S3.01", "structural"), "PT PLAN - LEVELS 7-9", ["LEVEL 7-9"], "plan")
    assert sheet_info.final(info, "", "", None) == (["LEVEL 7", "LEVEL 8", "LEVEL 9"], "PT PLAN - LEVELS 7-9", "plan")


def test_the_sheet_number_ladder_takes_the_numbers_already_read(monkeypatch):
    monkeypatch.setattr(classify, "extract_sheets_from_regions",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked twice")))
    items = [("S-003.0", None), ("SHEET NO. ? REV 2 A501 SEE S-201", None), ("nothing here", None)]
    preread = [None, ("A501", "architectural"), None]
    got = classify.classify_region_batch(items, None, preread)
    assert got[0] == ("S-003.0", "structural", "region_rules")  # certain: no model needed
    assert got[1] == ("A501", "architectural", "region_ai")
    assert got[2][2] != "region_ai"


# --- Stage 1: what a page prints -----------------------------------------------------------

def _page(lines):
    doc = fitz.open()
    page = doc.new_page(width=1000, height=700)
    for y, size, text in lines:
        page.insert_text((40, y), text, fontsize=size)
    return doc, page


def test_a_page_gives_its_titles_references_and_index():
    doc, page = _page([
        (60, 28, "FORMING PLAN - LEVEL 3"),
        (120, 8, "SEE 5/S5.01 FOR TYPICAL WALL. REFER TO SHEET A-301."),
        (140, 8, "COLUMN BASE PLATE, SEE S3.2"),
        (200, 14, "SHEET INDEX"),
        (220, 8, "S2.01 FOUNDATION PLAN"),
        (235, 8, "S2.02 FORMING PLAN - LEVEL 2"),
        (250, 8, "S2.03 FORMING PLAN - LEVEL 3"),
        (265, 8, "S3.01 DETAILS"),
        (280, 8, "S5.01 WALL SECTIONS"),
    ])
    facts = sheet_info.page_text_facts(page)
    assert "FORMING PLAN - LEVEL 3" in facts["title_lines"].splitlines()
    assert set(facts["refs"]) == {"S5.01", "A-301", "S3.2"}
    assert {e["sheet"]: e["title"] for e in facts["index"]}["S2.02"] == "FORMING PLAN - LEVEL 2"
    doc.close()


def test_a_page_that_is_not_an_index_lists_no_entries():
    assert sheet_info.index_entries("S2.01 FOUNDATION PLAN\nS2.02 FORMING PLAN") == []
    assert sheet_info.references("S2.01 FOUNDATION PLAN") == []


# --- The title-block step uses it ---------------------------------------------------------

def test_the_title_block_step_stores_levels_titles_and_kinds(monkeypatch):
    rows = [
        {"page_id": "p1", "combined_page": 1, "region_text": "S2.03", "filename": "s.pdf", "discipline": None,
         "discipline_source": None, "sheet_number": None, "title_lines": "FORMING PLAN - LEVEL 3"},
        {"page_id": "p2", "combined_page": 2, "region_text": "?", "filename": "s.pdf", "discipline": None,
         "discipline_source": None, "sheet_number": None, "title_lines": "WALL SECTIONS"},
    ]
    info = {"p1": SheetInfo(("S2.03", "structural"), "FORMING PLAN - LEVEL 3", ["LEVEL 3"], "plan"),
            "p2": SheetInfo(("S5.01", "structural"), "WALL SECTIONS", [], "section")}
    stored, passed = {}, {}
    monkeypatch.setattr(scrape.db, "pages_to_classify", lambda _p: [dict(r) for r in rows])
    monkeypatch.setattr(scrape.db, "project_index_titles", lambda _p: {})
    monkeypatch.setattr(scrape.db, "set_page_info", lambda pid, *a: stored.__setitem__(pid, a))
    monkeypatch.setattr(scrape.db, "set_page_sheet", lambda *a: None)
    monkeypatch.setattr(scrape.db, "upsert_portions", lambda *a: None)
    monkeypatch.setattr(scrape.db, "assign_chunk_portions", lambda *a: None)
    monkeypatch.setattr(scrape, "_get_redis", lambda: None)
    monkeypatch.setattr(scrape.sheet_info, "read", lambda items, _r: [info["p1"], info["p2"]])

    def classify_batch(items, _redis, preread=None):
        passed["preread"] = preread
        return [("S2.03", "structural", "region_rules"), ("S5.01", "structural", "region_ai")]

    monkeypatch.setattr(scrape.classify, "classify_region_batch", classify_batch)
    scrape._classify_pages("proj")
    assert passed["preread"] == [("S2.03", "structural"), ("S5.01", "structural")]
    assert stored["p1"] == (["LEVEL 3"], "LEVEL 3", "FORMING PLAN - LEVEL 3", "plan")
    assert stored["p2"] == ([], None, "WALL SECTIONS", "section")


def test_haiku_5_5_is_sent_thinking_off_as_it_accepts_it():
    # Haiku 5.5 thinks by default and accepts `disabled` at its default effort;
    # `between_tools` is Sonnet 5.5's word for it and would be refused.
    assert llm._claude_off("claude-haiku-5-5") == {"type": "disabled"}


def test_the_sheet_read_sends_thinking_off_even_when_the_chat_has_it_on(monkeypatch):
    # CLAUDE_THINKING=on is a chat setting; inherited, Haiku 5.5 thought by
    # default and returned no text ("stop_reason=max_tokens, 1x thinking").
    import sheetllm

    monkeypatch.setattr(llm, "_CLAUDE_THINKING", "on")
    monkeypatch.delenv("SHEET_THINKING", raising=False)
    sent = {}

    class Messages:
        def create(self, **request):
            sent.update(request)
            from types import SimpleNamespace as NS
            return NS(content=[NS(type="text", text="{}")], stop_reason="end_turn",
                      usage=NS(input_tokens=1, output_tokens=1))

    monkeypatch.setattr(llm, "anthropic_client", lambda: type("C", (), {"messages": Messages()})())
    monkeypatch.setattr(sheetllm, "provider", lambda: "claude")
    import usage
    monkeypatch.setattr(usage, "record_message", lambda *a, **k: None)
    assert sheetllm.complete_json("sys", "user") == "{}"
    assert sent["thinking"] == {"type": "disabled"}


def test_an_unescaped_inch_mark_does_not_lose_the_batch():
    # A real batch of 25 came back "unanswered" and was asked again: a title's 1/4" broke the JSON.
    texts = ['<titles>\nENLARGED PLAN - LEVEL 2\n1/4" = 1\'-0"\n</titles>']
    raw = '{"sheets": [{"index": 1, "sheet_number": "A5.01", "prefix": "A", "confidence": 0.9, ' \
          '"title": "ENLARGED PLAN 1/4" = 1\'-0"", "levels": ["LEVEL 2"], "kind": "enlarged_plan"}]}'
    answered = sheet_info.parse_batch(raw, texts)
    assert answered[0].levels == ["LEVEL 2"] and answered[0].kind == "enlarged_plan"


def test_batches_are_sent_together_and_billed_to_the_project(monkeypatch):
    import threading

    together = threading.Barrier(2, timeout=5)  # breaks if the second call waits for the first
    billed = []
    answer = _answer_all([])

    def complete_json(system, user, project_id=None, max_tokens=None):
        together.wait()
        billed.append(classify.usage_project())
        return answer(system, user)

    monkeypatch.setattr(sheet_info.sheetllm, "complete_json", complete_json)
    monkeypatch.setattr(classify, "SHEET_BATCH_SIZE", 25)
    monkeypatch.setattr(sheet_info, "READ_CONCURRENCY", 4)
    classify.set_usage_project("p-1")
    try:
        infos = sheet_info.read([(f"A{n}", f"FLOOR PLAN - LEVEL 1\nSHEET {n}", None) for n in range(30)], FakeRedis())
    finally:
        classify.set_usage_project(None)
    assert billed == ["p-1", "p-1"]
    assert all(isinstance(info, SheetInfo) for info in infos)


def test_small_drawing_titles_that_name_a_level_are_kept():
    # UT Law S2.201: its levels are printed only in 12.6 pt drawing titles, never in the big lines.
    sized = [(31.5, "UT LAW STUDENT HOUSING"), (24.9, "Enlarged Forming"), (12.6, "A"),
             (12.6, "Enlarged Forming Plan - Elev./Stair Core - Level 1"),
             (12.6, "Enlarged Forming Plan - Elev./Stair Core - Level 3-4"),
             (12.6, "Enlarged Forming Plan - Elev./Stair Core - Level 3-4")]
    lines = sheet_info.level_lines(sized)
    assert lines == ["Enlarged Forming Plan - Elev./Stair Core - Level 1",
                     "Enlarged Forming Plan - Elev./Stair Core - Level 3-4"]
    assert sheet_info.final(None, None, "\n".join(lines), None)[0] == ["LEVEL 1", "LEVEL 3", "LEVEL 4"]


def test_height_marks_details_and_pointers_are_not_drawing_titles():
    sized = [(10, "LEVEL 01"), (10, "LEVEL 02"),                      # a section's height marks (A1.70)
             (14, "PRIMARY ROOF DRAIN DETAIL"),                       # a detail, not a plan (A7.56)
             (9, "SEE FORMING PLAN - LEVEL 2 FOR SLAB EDGE"),         # a pointer to another sheet
             (9, "ALL WORK ON THIS PLAN SHALL BE COORDINATED WITH THE LEVEL 2 SLAB EDGE AND OPENINGS")]  # a note
    assert sheet_info.level_lines(sized) == []


def test_the_index_entry_adds_the_floors_it_lists():
    info = SheetInfo(sheet="S2.103", title="Forming Plan", levels=["LEVEL 3"], kind="plan")
    assert sheet_info.final(info, None, "Forming Plan - Level 3", "Forming Plan - Level 3-4")[0] == ["LEVEL 3", "LEVEL 4"]


def test_a_broken_reply_is_asked_once_more_before_the_batch_is_split(monkeypatch):
    calls = []
    good = _answer_all([])

    def complete_json(system, user, project_id=None, max_tokens=None):
        calls.append(user.count("<sheet index="))
        return '{"sheets": [{"index": 1, "title": "oops' if len(calls) == 1 else good(system, user)

    monkeypatch.setattr(sheet_info.sheetllm, "complete_json", complete_json)
    infos = sheet_info._ask([f"FLOOR PLAN - LEVEL 1\nSHEET {n}" for n in range(10)])
    assert calls == [10, 10]  # the same batch again, not two halves
    assert all(isinstance(info, SheetInfo) for info in infos)
