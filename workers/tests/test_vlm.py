"""The vision pass: what it sends, and what it refuses to store.

Two things here can go wrong quietly. The render can hand a model an image it
cannot read (a 42in sheet at the wrong scale is 5px text), and a failed call can
put an apology into retrieval where it will be cited as if it described a
drawing. Both are covered below; the describe path is exercised against a fake
transport so no test spends money.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import re  # noqa: E402

import fitz  # noqa: E402
import pytest  # noqa: E402

import llm  # noqa: E402
import vlm  # noqa: E402


def sheet(width_in=42.0, height_in=30.0, rotation=0):
    doc = fitz.open()
    page = doc.new_page(width=width_in * 72, height=height_in * 72)
    page.insert_text((72, 144), "S-100.0 FOUNDATION PLAN", fontsize=9.6)
    if rotation:
        page.set_rotation(rotation)
    return doc, page


# --- render ----------------------------------------------------------------


def test_render_scales_the_long_edge_to_the_cap():
    doc, page = sheet()
    pix = fitz.Pixmap(vlm.render(page, max_edge=2576))
    assert max(pix.width, pix.height) == pytest.approx(2576, abs=2)
    doc.close()


def test_render_upscales_a_drawn_sheet_to_the_cap():
    """This test used to assert the opposite, and the opposite was wrong.

    "Extra pixels carry no extra information" is true of a RASTER page and
    false of a vector one: a PDF page re-rendered above 1.0 draws its glyphs
    again at a higher sampling rate. Because 42in at 72pt/in is 3024pt, the old
    clamp pinned every sheet in this pass at 72 DPI or below — so setting
    VLM_MAX_EDGE=5000 to test whether resolution was the column tag's limit
    rendered 3024px and logged "72 DPI". The experiment could not run.
    """
    doc, page = sheet(width_in=8.5, height_in=11.0)
    pix = fitz.Pixmap(vlm.render(page, max_edge=2576))
    assert max(pix.width, pix.height) == pytest.approx(2576, abs=2)
    doc.close()


def test_render_never_upscales_a_page_with_no_text_layer():
    """A scan is already fixed at its own resolution, so there the original
    rule holds: bigger is empty pixels at full price — a Gemini tile costs 258
    tokens whether or not anything is in it."""
    doc = fitz.open()
    page = doc.new_page(width=8.5 * 72, height=11.0 * 72)  # nothing drawn on it
    pix = fitz.Pixmap(vlm.render(page, max_edge=2576))
    assert max(pix.width, pix.height) == pytest.approx(792, abs=2)
    doc.close()


def test_render_follows_the_rotation_the_reader_sees():
    """get_pixmap is rotation-aware, so a /Rotate 90 sheet renders landscape.
    The trap this avoids is region.py's: get_text(clip=) is NOT."""
    doc, page = sheet(width_in=30.0, height_in=42.0, rotation=90)
    pix = fitz.Pixmap(vlm.render(page, max_edge=1000))
    assert pix.width > pix.height
    doc.close()


# --- describe --------------------------------------------------------------


@pytest.fixture
def fake_transport(monkeypatch):
    """Capture what would have been sent, and reply with whatever the test wants."""
    sent = {}

    def fake_complete(system, user, **kwargs):
        sent["system"] = system
        sent["user"] = user
        sent.update(kwargs)
        return sent.get("_reply")

    monkeypatch.setattr(vlm.llm, "complete", fake_complete)
    return sent


def test_describe_sends_the_image_and_the_geometry_instruction(fake_transport):
    fake_transport["_reply"] = llm.Reply(text="x" * 400, stop_reason="end_turn")
    out = vlm.describe_page(b"\x89PNG-bytes", sheet_number="S-100.0")
    assert out == "x" * 400
    assert fake_transport["images"] == [b"\x89PNG-bytes"]
    assert "S-100.0" in fake_transport["user"]
    # The instruction that makes this pass worth running at all: pairings, not
    # a second reading of text the extractor already has exactly.
    assert "WRITE THE PAIRINGS" in fake_transport["system"]
    assert "already been extracted and indexed" in fake_transport["system"]


def test_prompt_demands_a_full_grid_coordinate_on_every_pairing(fake_transport):
    """The single measured difference between a description worth 84% on grid
    questions and one worth 47%.

    The weaker one named row B's intersections and then gave rows C and F as
    ordered lists of marks under a heading — which is precisely the shape the
    TEXT LAYER already has, and the shape this whole pass exists to replace.
    Row B scored 6/7; the two listed rows scored 3/12.
    """
    vlm.describe_page(b"png")
    system = fake_transport["system"]
    assert "<column line>/<row line>" in system
    assert "The coordinate is not optional" in system
    # The three shapes measured to fail, quoted at the model as counter-examples.
    assert "no coordinates at all" in system
    assert "not an intersection" in system
    assert "two grid lines, one entry" in system


def test_prompt_makes_it_name_the_grid_before_pairing_anything(fake_transport):
    """The pairing can be right while the row name is wrong, and that failure
    is invisible: one reading of this sheet got the footing mark AND the member
    size right at 2, 3 and 7 and labelled the whole row D where the bubbles say
    B. Fourteen of the eval's forty questions ask about that row, and they had
    nothing to answer from — reported as fifteen abstentions, which reads like
    a model that could not see rather than one that mislabelled."""
    vlm.describe_page(b"png")
    system = fake_transport["system"]
    assert "FIRST, NAME THE GRID" in system
    assert "Column lines, left to right" in system and "Row lines, top to bottom" in system
    # The escape hatch matters as much as the rule: a guessed letter is worse
    # than an admitted gap, because a guess still answers questions.
    assert "rather than inventing a letter" in " ".join(system.split())


def test_prompt_forbids_using_one_coordinate_twice(fake_transport):
    """The merged-grid-line failure in its third costume. Not two lines in one
    entry this time — one label over two physical rows, which the model itself
    flagged as a "second column line row" while writing 8/C twice with
    different footings. Both entries are then unusable."""
    vlm.describe_page(b"png")
    system = fake_transport["system"]
    assert "ONE ENTRY PER INTERSECTION" in system
    assert "the column line comes first" in " ".join(system.split())


def test_prompt_puts_every_intersection_before_anything_optional(fake_transport):
    """The old wording said "only with the room left over" and then listed
    dimensions as permitted — so a description spent its last third on nine
    grid-to-grid spacings, every one already in the text layer, having never
    described one of the sheet's grid rows. Room left over is for the
    intersections, and the ordering has to say so."""
    vlm.describe_page(b"png")
    system = fake_transport["system"]
    assert "ONLY once every grid intersection on the sheet has a line of its own" in system
    assert "Room left over is for intersections you have not covered" in " ".join(system.split())


def test_the_prompt_never_seeds_an_answer_from_the_sheet_under_test(fake_transport):
    """Every example value is synthetic ON PURPOSE. A counter-example is still
    text in the prompt: the shapes it quotes carried this sheet's real footing
    marks for a while — including F9, which is exactly the label a frequency
    prior guesses and the one the majority-class baseline scores 32% with. A
    model falling back on the prompt's own examples would then produce the
    behaviour the benchmark exists to punish, and the run would be scoring the
    prompt rather than the drawing."""
    vlm.describe_page(b"png")
    system = fake_transport["system"]
    for mark in ("F6", "F7", "F8", "F9", "F10", "F11", "F12", "F13"):
        assert re.search(rf"(?<![A-Z0-9]){mark}(?![0-9])", system) is None, mark
    for size in ("HSS8X8", "HSS6X6", "HSS10X10", "HSS5X5", "HSS7X5"):
        assert size not in system, size
    for detail in ("S-300.0", "S-301.0", "S-302.0", "S-100.0"):
        assert detail not in system, detail


def test_prompt_forbids_transcribing_what_the_text_layer_holds(fake_transport):
    """Two thirds of one description's budget went on schedules, the title
    block, the seal and loose dimension strings — all indexed word for word
    already, all of it displacing the pairings nothing else can supply."""
    vlm.describe_page(b"png")
    assert "DO NOT TRANSCRIBE" in fake_transport["system"]


def test_prompt_forbids_carrying_an_unreadable_value_forward(fake_transport):
    """Both measured descriptions answered nearly every column with one size.
    Neither ever said it could not read one; each picked a value and repeated
    it, which is the failure that reads most like an answer."""
    vlm.describe_page(b"png")
    flat = " ".join(fake_transport["system"].split())
    assert "illegible" in flat
    assert "Not the last value you managed to read" in flat


def test_describe_discards_a_reply_too_short_to_be_a_description(fake_transport):
    """"I'm sorry, I can't see an image." must never reach a chunk — stored, it
    would be retrieved and cited as though it described the drawing."""
    fake_transport["_reply"] = llm.Reply(text="I cannot see the image.", stop_reason="end_turn")
    assert vlm.describe_page(b"png") is None


def test_describe_returns_none_when_the_provider_is_unavailable(fake_transport):
    # llm.complete returns None for no key / missing SDK / a raised call.
    fake_transport["_reply"] = None
    assert vlm.describe_page(b"png") is None


def test_describe_keeps_a_truncated_description(fake_transport):
    """Unlike the JSON every other caller parses, prose does not stop being
    readable where it was cut: the pairings already written are still true."""
    fake_transport["_reply"] = llm.Reply(text="y" * 400, stop_reason="max_tokens")
    assert vlm.describe_page(b"png") == "y" * 400


def test_untrusted_drawing_text_is_named_as_such(fake_transport):
    fake_transport["_reply"] = llm.Reply(text="z" * 400, stop_reason="end_turn")
    vlm.describe_page(b"png")
    assert "UNTRUSTED" in fake_transport["system"]


# --- transport: images are additive ---------------------------------------


def test_a_call_without_images_is_unchanged_on_both_providers():
    """Prompt caching is a prefix match. If adding this parameter reshaped the
    user turn for callers that pass no image, every existing call site would
    lose its cached prefix the day it shipped."""
    assert llm._user_content_claude("hello", None) == "hello"
    assert llm._user_content_claude("hello", []) == "hello"
    assert llm._user_content_gemini("hello", None) == "hello"


def test_images_precede_the_text_on_both_providers():
    claude = llm._user_content_claude("describe it", [b"\x89PNG"])
    assert [b["type"] for b in claude] == ["image", "text"]
    assert claude[0]["source"]["media_type"] == "image/png"

    gemini = llm._user_content_gemini("describe it", [b"\x89PNG"])
    assert gemini[0]["inline_data"]["mime_type"] == "image/png"
    assert gemini[-1] == "describe it"


def test_claude_images_are_base64_and_gemini_images_are_raw_bytes():
    """The two SDKs want different things and neither raises on the other's —
    Anthropic takes a base64 string, google-genai takes bytes."""
    import base64

    claude = llm._user_content_claude("q", [b"\x89PNG"])
    assert base64.b64decode(claude[0]["source"]["data"]) == b"\x89PNG"
    gemini = llm._user_content_gemini("q", [b"\x89PNG"])
    assert gemini[0]["inline_data"]["data"] == b"\x89PNG"


# --- Why a description failed, not just that it did -----------------------
#
# Both providers failed on the same sheet at VLM_MAX_TOKENS=1500 and neither
# log line named the budget, so both looked like model quality: Claude
# truncated mid-grid (8 of 19 footing questions lost), and Gemini 3.1 Pro spent
# the whole budget reasoning and returned 98 characters.


def test_a_short_reply_that_hit_max_tokens_says_the_budget_ran_out(fake_transport, caplog):
    """"description was 98 chars — discarding" reads as a refusal. It was a
    thinking model spending every token before it wrote a word, which is a
    completely different thing to fix."""
    fake_transport["_reply"] = llm.Reply(text="Here is the description:", stop_reason="max_tokens")
    with caplog.at_level("WARNING"):
        assert vlm.describe_page(b"png") is None
    message = caplog.text
    assert "max_tokens" in message
    assert "VLM_MAX_TOKENS" in message
    # The cause an operator cannot guess from a length: reasoning is billed
    # from the same budget as the answer.
    assert "thinking model" in message


def test_a_short_reply_that_simply_ended_reports_what_was_said(fake_transport, caplog):
    """A real refusal keeps the old meaning, and now shows the text so nobody
    has to query the database to find out the model said it saw no image."""
    fake_transport["_reply"] = llm.Reply(text="I cannot see an image.", stop_reason="end_turn")
    with caplog.at_level("WARNING"):
        assert vlm.describe_page(b"png") is None
    assert "I cannot see an image." in caplog.text
    assert "VLM_MAX_TOKENS" not in caplog.text


def test_a_truncated_description_is_kept_but_says_what_was_lost(
    fake_transport, monkeypatch, caplog
):
    """Keeping it is right — the pairings it managed are real. But the cut
    lands part-way through the sheet, and an intersection after it is not a
    wrong answer later, it is a question the chat cannot answer at all."""
    monkeypatch.setattr(vlm, "MAX_TOKENS", 300)
    fake_transport["_reply"] = llm.Reply(text="At 8/B: footing F12. " * 40, stop_reason="max_tokens")
    with caplog.at_level("WARNING"):
        out = vlm.describe_page(b"png")
    assert out is not None
    assert "TRUNCATED" in caplog.text
    assert "no description at all" in caplog.text
    assert "Raise VLM_MAX_TOKENS" in caplog.text


def test_a_short_description_that_hit_max_tokens_blames_the_reasoning_not_the_budget(
    fake_transport, monkeypatch, caplog
):
    """The measured case: 64 tokens stored against a 4000-token budget, logged
    as "it is TRUNCATED ... Raise VLM_MAX_TOKENS". Both halves of that are
    wrong. The model wrote forty words and spent the rest of the budget
    reasoning, so a bigger budget buys more reasoning; and what was stored
    describes a corner of the sheet while being indexed as the description of
    all of it. Without the LENGTH in the line, this is indistinguishable from a
    description that genuinely ran out of room."""
    monkeypatch.setattr(vlm, "MAX_TOKENS", 4000)
    fake_transport["_reply"] = llm.Reply(
        text="At 8/B: footing F12, column HSS8X8X3/8. " * 5, stop_reason="max_tokens"
    )
    with caplog.at_level("WARNING"):
        out = vlm.describe_page(b"png")
    assert out is not None  # still kept: forty real words beat none
    assert "did not go to the description" in caplog.text
    assert "buys more reasoning" in caplog.text
    # The two failures must not read alike — this one is NOT a room problem.
    assert "TRUNCATED" not in caplog.text


def test_both_max_tokens_branches_report_how_much_was_actually_written(
    fake_transport, monkeypatch, caplog
):
    """The one field that separates them. A budget figure alone says nothing:
    4000 appears in the log whether the model wrote 3900 tokens or 64."""
    for budget in (300, 4000):
        caplog.clear()
        monkeypatch.setattr(vlm, "MAX_TOKENS", budget)
        fake_transport["_reply"] = llm.Reply(
            text="At 8/B: footing F12. " * 40, stop_reason="max_tokens"
        )
        with caplog.at_level("WARNING"):
            vlm.describe_page(b"png")
        assert "~208 tokens" in caplog.text, budget


def test_every_outcome_names_the_provider_and_model_that_produced_it(
    fake_transport, monkeypatch, caplog
):
    """VLM_PROVIDER is read here, at ingest. A benchmark reading the chunks
    afterwards cannot know it, and neither could the log — so "was that run
    Claude or Gemini?" was unanswerable for two projects whose owner was sure
    of the answer. Every line this function emits now carries it."""
    monkeypatch.setattr(vlm, "MAX_TOKENS", 300)
    monkeypatch.setenv("VLM_PROVIDER", "gemini")
    monkeypatch.setattr(vlm, "GEMINI_MODEL", "gemini-x")
    long_enough = "At 8/B: footing F12, column HSS8X8X3/8. " * 8

    for reply, level in [
        (llm.Reply(text="", stop_reason="max_tokens"), "WARNING"),          # budget gone
        (llm.Reply(text="nope", stop_reason="end_turn"), "WARNING"),        # refusal
        (llm.Reply(text=long_enough, stop_reason="max_tokens"), "WARNING"), # truncated
        (llm.Reply(text=long_enough, stop_reason="end_turn"), "INFO"),      # it worked
    ]:
        caplog.clear()
        fake_transport["_reply"] = reply
        with caplog.at_level("INFO"):
            vlm.describe_page(b"png")
        assert "gemini/gemini-x" in caplog.text, reply.stop_reason
        assert any(r.levelname == level for r in caplog.records), reply.stop_reason


def test_prompt_forbids_writing_a_value_it_has_just_called_illegible(fake_transport):
    """The rule used to forbid only REPEATING the last value read, and the
    failure that got through was a fresh guess at the unreadable part: "column
    HSS8X8X1/8 (marking illegible beyond HSS8X8, exact thickness not
    readable)". Both halves are in one sentence, and only the value survives
    retrieval — the chat answered with the size and dropped the caveat, which
    the scorer then recorded as an INVENTED member size."""
    vlm.describe_page(b"png")
    flat = " ".join(fake_transport["system"].split())
    assert "write NO VALUE FOR IT AT ALL" in flat
    assert "a value written beside the word \"illegible\" is still a value" in flat
    # And the counter-example stays synthetic, like every other one.
    assert "HSS8X8" not in fake_transport["system"]


class TestGridCoverage:
    """A description is measured against the grid IT NAMED.

    "Why is this description short?" has two answers with opposite fixes, and
    the token count separates neither. A reply cut off at max_tokens needs more
    room; a reply that ended cleanly at 255 tokens decided it was done, and
    raising VLM_MAX_TOKENS from 4000 to 20000 buys the identical description.
    """

    GRID = (
        "Column lines, left to right: 1, 3, 5, 5.5, 8, 12\n"
        "Row lines, top to bottom: J, K, L, N\n\n"
    )

    def test_counts_the_grid_it_named_and_the_coordinates_it_wrote(self):
        text = self.GRID + (
            "At 12/K: footing F42, column HSS4X4X1/4.\n"
            "At 12/L: footing F31.\n"
            "At 1/J: nothing at this intersection.\n"
        )
        assert vlm.grid_coverage(text) == (6, 4, 3)

    def test_a_repeated_coordinate_is_one_intersection(self):
        """The prompt forbids writing one twice; counting it twice would hide
        exactly the failure that rule exists to catch."""
        text = self.GRID + "At 12/K: footing F42.\nAt 12/k: footing F31 (second row).\n"
        assert vlm.grid_coverage(text)[2] == 1

    def test_prose_in_a_grid_line_is_not_a_grid_line(self):
        text = (
            "Column lines, left to right: 1, 3, the rest could not be read\n"
            "Row lines, top to bottom: J, K\n\nAt 1/J: footing F42.\n"
        )
        assert vlm.grid_coverage(text) == (2, 2, 1)

    def test_a_sheet_with_no_grid_is_not_measured(self):
        """A detail or schedule sheet is explicitly allowed to have no grid, so
        there is nothing to measure against — and nothing to warn about."""
        assert vlm.grid_coverage("This sheet has no grid. Detail 9 shows...") is None

    def test_covering_a_third_of_its_own_grid_is_reported(self, caplog):
        text = self.GRID + "".join(f"At 12/{row}: footing F42.\n" for row in "JKL")
        with caplog.at_level("WARNING"):
            vlm._report_grid_coverage(text, "S-100.0", "gemini/gemini-3.6-flash")
        assert "24 intersections" in caplog.text
        assert "wrote 3 coordinate lines" in caplog.text
        # The advice that would be WRONG here must not appear: nothing was cut
        # off, so more budget buys nothing.
        assert "VLM_MAX_TOKENS is not the lever" in caplog.text

    def test_a_covered_grid_says_nothing(self, caplog):
        rows = "JKLN"
        text = self.GRID + "".join(
            f"At {col}/{row}: footing F42.\n" for col in ("1", "3", "5", "5.5", "8", "12")
            for row in rows
        )
        with caplog.at_level("WARNING"):
            vlm._report_grid_coverage(text, "S-100.0", "who")
        assert caplog.text == ""


class TestThePromptAsksForEveryIntersection:
    def test_it_asks_the_model_to_count_its_own_grid(self):
        """255 tokens that ended cleanly is a model that thought it had
        finished. The prompt had told it to STOP once the intersections were
        covered without ever saying how many that is."""
        system = " ".join(vlm.SYSTEM.split())
        assert "the count is how many lines you owe" in system
        assert "including the ones where nothing is built" in system

    def test_short_is_only_a_virtue_after_the_count_is_met(self):
        system = " ".join(vlm.SYSTEM.split())
        assert "Short is a virtue AFTER that count is met and never before it" in system
        # The older wording invited the failure: it praised a short description
        # without tying "short" to having covered anything.
        assert "a short description that named every intersection beats" not in system


class TestTheCountDoesNotForceAValue:
    """The count rule and the illegible rule pull against each other, and the
    count was winning.

    Demanding a line at every intersection lifted the footing tag from 74% to
    95% and dropped the column tag from 57% to 24%: forced to fill a line for
    every intersection, the model supplied one member size for nearly all of
    them — the size it could not read at this resolution — reproducing the exact
    failure the illegible rule exists to prevent. Footing marks it CAN read, so
    that half of every line got better while the other half got worse.
    """

    def test_a_line_is_owed_but_a_value_is_not(self):
        system = " ".join(vlm.SYSTEM.split())
        assert "You owe a LINE at every intersection. You do not owe a VALUE" in system
        assert "column size illegible" in system

    def test_it_names_the_repeated_value_as_the_signature(self):
        system = " ".join(vlm.SYSTEM.split())
        assert "the same size five times in a row" in system
        assert "you are filling the count, not reading the drawing" in system


class TestResolutionIsReported:
    """The number that decides what can be read, which nothing printed.

    61 DPI resolves a footing mark in a bubble and does not reliably resolve a
    member size with a fraction — a footing tag at 79-95% beside a column tag
    at 19% on the same description, with the column callouts SITTING CLOSER to
    their intersections than the footing marks that are read correctly.
    """

    class _Rect:
        width, height = 42 * 72, 30 * 72

    def test_it_says_the_dpi_once(self, monkeypatch, caplog):
        monkeypatch.setattr(vlm, "_resolution_reported", False)
        with caplog.at_level("INFO"):
            vlm._report_resolution(self._Rect(), 2576 / (42 * 72), 2576)
            vlm._report_resolution(self._Rect(), 2576 / (42 * 72), 2576)
        assert caplog.text.count("vision pass renders") == 1
        assert "61 DPI" in caplog.text
        assert "42x30in" in caplog.text

    def test_it_reports_the_read_dpi_beside_the_rendered_one(self, monkeypatch, caplog):
        """The whole cost of this line's first version. It printed 119 DPI for
        a render that Gemini read at 73, and the run that produced it was
        filed as evidence that resolution is not the column tag's limit."""
        monkeypatch.setattr(vlm, "_resolution_reported", False)
        monkeypatch.setattr(vlm, "provider", lambda: "gemini")
        with caplog.at_level("INFO"):
            vlm._report_resolution(self._Rect(), 5000 / (42 * 72), 5000)
        assert "119 DPI" in caplog.text
        assert "73 DPI reaches the model" in caplog.text

    def test_past_the_ceiling_is_billed_for_nothing_on_both_providers(
        self, monkeypatch, caplog
    ):
        """It is not a Claude-only warning. The comment this replaces said
        Gemini "has no such ceiling: it tiles" — it caps at 3072, which on this
        sheet is 73 DPI against Claude's 61. A 12-DPI spread, not a wall on one
        side and open road on the other."""
        for who, ceiling in (("claude", 2576), ("gemini", 3072)):
            caplog.clear()
            monkeypatch.setattr(vlm, "_resolution_reported", False)
            monkeypatch.setattr(vlm, "provider", lambda who=who: who)
            with caplog.at_level("INFO"):
                vlm._report_resolution(self._Rect(), 5000 / (42 * 72), 5000)
            assert f"scales an image down to {ceiling} px" in caplog.text
            assert "the rendered DPI is not the read DPI" in caplog.text

    def test_no_warning_when_the_render_is_inside_the_ceiling(self, monkeypatch, caplog):
        monkeypatch.setattr(vlm, "_resolution_reported", False)
        monkeypatch.setattr(vlm, "provider", lambda: "gemini")
        with caplog.at_level("INFO"):
            vlm._report_resolution(self._Rect(), 2576 / (42 * 72), 2576)
        assert "scales an image down" not in caplog.text
        assert "61 DPI reaches the model" in caplog.text


class TestZoneMarkersAreNotGridLines:
    def test_the_prompt_says_a_grid_line_is_circled_and_has_a_line(self):
        """One reading named 12 column lines and 8 row lines on a sheet with 8
        and 3 — the drawing frame's zone markers counted as grid. It is the
        same trap drawing_truth.py documents on the generator side, where an
        uncircled zone letter read as a grid line put the wrong footing at
        7/C."""
        system = " ".join(vlm.SYSTEM.split())
        assert "ending in a CIRCLED label" in system
        assert "zone markers" in system
        assert "Counting them doubles your grid" in system


class TestUpscalingAVectorPage:
    """The clamp that made the resolution experiment impossible to run.

    "Never scale UP" was written for a raster page, where extra pixels are
    empty and billed. A PDF page is VECTOR, and 42in at 72pt/in is 3024pt — so
    min(1.0, max_edge/longest) pinned the whole vision pass at 72 DPI. Setting
    VLM_MAX_EDGE=5000 to test whether resolution was the column tag's limit
    rendered 3024px and logged "72 DPI": the experiment did not run, and only
    the log line added for that same hypothesis revealed it.
    """

    class _Page:
        text = "HSS8X8X3/8"

        class rect:
            width, height = 42 * 72, 30 * 72

        def get_text(self, *_a):
            return self.text

        def get_pixmap(self, matrix=None):
            self.zoom = matrix.a

            class _Pixmap:
                def tobytes(self, _fmt):
                    return b""

            return _Pixmap()

    def _render(self, monkeypatch, page, max_edge):
        monkeypatch.setattr(vlm, "_resolution_reported", True)
        vlm.render(page, max_edge)
        return page.zoom

    def test_a_drawn_page_renders_above_1x(self, monkeypatch):
        page = self._Page()
        zoom = self._render(monkeypatch, page, 5000)
        assert round(72 * zoom) == 119, "5000px on a 3024pt page is 119 DPI, not 72"

    def test_a_scan_is_still_never_upscaled(self, monkeypatch):
        """No text layer means the page is already fixed at its own
        resolution, and there the original rule holds: bigger is empty pixels
        at full price."""
        page = self._Page()
        page.text = "   "
        assert self._render(monkeypatch, page, 5000) == 1.0

    def test_the_default_is_unchanged(self, monkeypatch):
        """2576 is BELOW the page's 3024pt, so the default still downscales and
        this fix changes nothing until someone raises the setting."""
        page = self._Page()
        assert round(72 * self._render(monkeypatch, page, 2576)) == 61


class TestCropsAtEveryIntersection:
    """The geometry of the crop plan, checked against the real sheet.

    Not a synthetic grid invented for the test. The bubbles below are placed at
    the coordinates the CHECKED-IN eval set derived from the PDF itself, so
    what this asserts is that `vlm.crops` and `drawing_truth.py` name the same
    intersections at the same points — the disagreement that would otherwise
    be invisible, because from Phase A both read `grid.py` and could be wrong
    together.

    No model call and no rendering. Phase B is the geometry alone.
    """

    SET = Path(__file__).resolve().parents[2] / "benchmarks" / "drawing_eval_set.json"
    # EVERY checked-in set. The seeding rule is about the sheets under test,
    # and there are two of them now with disjoint vocabularies — guarding only
    # the first would leave PC1/C4 free to appear in the prompt.
    SETS = sorted(
        (Path(__file__).resolve().parents[2] / "benchmarks").glob("drawing_eval_set*.json")
    )

    @staticmethod
    def _bubble(page, x, y, label):
        page.draw_circle(fitz.Point(x, y), 18)
        page.insert_text((x - 10, y + 4), label, fontsize=9)

    @classmethod
    def _real_sheet(cls):
        """An ARCH E1 page carrying the real sheet's grid and nothing else."""
        import json

        cases = json.loads(cls.SET.read_text())
        columns, rows = {}, {}
        for case in cases:
            d = case["derivation"]
            columns[d["gridColumn"]] = d["intersectionPt"][0]
            rows[d["gridRow"]] = d["intersectionPt"][1]
        doc = fitz.open()
        page = doc.new_page(width=42 * 72, height=30 * 72)
        for label, x in columns.items():
            cls._bubble(page, x, 400, label)
        for label, y in rows.items():
            cls._bubble(page, 950, y, label)
        return doc, page, cases, columns, rows

    def test_every_crop_is_centred_on_the_sets_own_intersection(self):
        doc, page, cases, columns, rows = self._real_sheet()
        boxes = dict(vlm.crops(page))
        assert len(boxes) == len(columns) * len(rows)
        for case in cases:
            d = case["derivation"]
            rect = boxes[f"{d['gridColumn']}/{d['gridRow']}"]
            want_x, want_y = d["intersectionPt"]
            assert abs((rect.x0 + rect.x1) / 2 - want_x) < 1
            assert abs((rect.y0 + rect.y1) / 2 - want_y) < 1
        doc.close()

    def test_every_case_s_label_falls_inside_its_own_crop(self):
        """The check that decides whether 0.6 bays is the right size. A crop
        that does not contain the label it exists to carry cannot be answered,
        and the furthest footing on this sheet sits 83.7pt out."""
        doc, page, cases, _, _ = self._real_sheet()
        boxes = dict(vlm.crops(page))
        worst = max(c["derivation"]["labelDistancePt"] for c in cases)
        for case in cases:
            d = case["derivation"]
            rect = boxes[f"{d['gridColumn']}/{d['gridRow']}"]
            reach = min(rect.width, rect.height) / 2
            assert d["labelDistancePt"] <= reach, (
                f"{d['gridColumn']}/{d['gridRow']} label is {d['labelDistancePt']}pt "
                f"away, crop reaches {reach:.1f}pt"
            )
        assert worst > 80  # the test is not passing because the sheet is easy
        doc.close()

    def test_a_crop_is_much_smaller_than_the_sheet(self):
        """The whole point. Both providers cap what they read — 61 DPI for
        Claude on this sheet, 73 for Gemini — so the only way to more pixels
        per glyph is fewer points per image."""
        doc, page, _, _, _ = self._real_sheet()
        label, rect = vlm.crops(page)[0]
        area = (rect.width * rect.height) / (page.rect.width * page.rect.height)
        assert area < 0.01, f"{label} is {area:.1%} of the sheet"
        doc.close()

    def test_crops_are_display_space_so_get_pixmap_takes_them_directly(self):
        """`get_pixmap(clip=)` is rotation-aware and `get_text(clip=)` is not —
        the asymmetry region.py exists to document. A crop that came back in
        unrotated space would render the wrong part of a /Rotate 90 sheet."""
        doc, page, _, _, _ = self._real_sheet()
        upright = dict(vlm.crops(page))
        page.set_rotation(90)
        turned = dict(vlm.crops(page))
        # Same intersections, same names — `grid.orient` is what keeps "2/B"
        # from becoming "B/2" when the page is turned.
        assert set(upright) == set(turned)
        for label, rect in turned.items():
            assert rect in page.rect
            # The crop follows the drawing: width and height swap with the page.
            assert abs(rect.width - upright[label].height) < 1
        doc.close()

    def test_a_page_with_no_grid_gets_no_crops(self):
        """Most pages. This is a structural-plan device, not a general one, and
        a page it cannot place must produce nothing rather than a guess."""
        doc = fitz.open()
        page = doc.new_page(width=42 * 72, height=30 * 72)
        page.insert_text((100, 100), "GENERAL NOTES", fontsize=12)
        assert vlm.crops(page) == []
        doc.close()


# --- Phase C: one crop per intersection ------------------------------------


class TestCropAlignment:
    """Which image a line is about, checked twice.

    This is the sheet-batch lesson (`parse_sheet_batch_response`) applied to
    images, and it matters more here than it did there. A drifted sheet answer
    gives a page the wrong discipline; a drifted crop answer puts a real footing
    mark at an intersection it does not belong to — which is the exact failure
    this whole mode exists to remove, reintroduced by the mechanism meant to
    remove it. Every discard below must become an ABSENCE, never a confident
    wrong placement.
    """

    def test_a_well_formed_batch_is_read(self):
        got = vlm.parse_crop_batch(
            "1. 2/B: footing F9, column HSS8X8X3/8\n"
            "2. 4/B: footing -, column HSS6X6X3/8\n",
            ["2/B", "4/B"],
        )
        assert got == {"2/B": ("F9", "HSS8X8X3/8"), "4/B": (None, "HSS6X6X3/8")}

    def test_an_index_outside_the_batch_is_discarded(self):
        assert vlm.parse_crop_batch("7. 2/B: footing F9, column HSS8X8X3/8", ["2/B"]) == {}

    def test_an_index_answered_twice_discards_BOTH_answers(self):
        """Not the second one — both. Two answers for one image means neither
        can be trusted, and keeping either is choosing a wrong placement at
        random over an honest gap."""
        got = vlm.parse_crop_batch(
            "1. 2/B: footing F9, column A\n"
            "1. 2/B: footing F7, column B\n"
            "2. 4/B: footing F1, column C\n",
            ["2/B", "4/B"],
        )
        assert got == {"4/B": ("F1", "C")}

    def test_a_coordinate_that_disagrees_with_its_index_is_discarded(self):
        """The index says which image; the coordinate says which intersection
        the model thought it was. Either alone can drift in silence."""
        assert vlm.parse_crop_batch("1. 9/F: footing F9, column HSS8X8X3/8", ["2/B"]) == {}

    def test_a_hedged_value_is_no_value(self):
        """"appears to be F9" and "HSS8X8 (illegible)" are the failure the
        illegible rule was widened twice to close: only the label survives
        retrieval and the caveat is dropped."""
        got = vlm.parse_crop_batch(
            "1. 2/B: footing appears to be F9, column HSS8X8 (illegible)", ["2/B"]
        )
        assert got == {"2/B": (None, None)}

    def test_the_absent_vocabulary_is_wider_than_the_dash_the_prompt_asks_for(self):
        for said in ("-", "--", "none", "N/A", "not legible", "illegible", "not visible"):
            assert vlm._crop_value(said) is None, said
        assert vlm._crop_value("F9") == "F9"
        assert vlm._crop_value("HSS8X8X3/8.") == "HSS8X8X3/8"


class TestCropResolution:
    def test_a_crop_spends_the_same_ceiling_on_far_less_drawing(self):
        """The whole argument for the mode, as a number. Same cap, same page —
        the crop resolves the drawing an order of magnitude finer because the
        budget covers 0.6% of the area."""
        doc, page = sheet()
        rect = fitz.Rect(1000, 800, 1190, 1020)
        whole = fitz.Pixmap(vlm.render(page, max_edge=3072))
        crop = fitz.Pixmap(vlm.render_crop(page, rect, max_edge=3072))
        whole_dpi = max(whole.width, whole.height) / (max(page.rect.width, page.rect.height) / 72)
        crop_dpi = max(crop.width, crop.height) / (max(rect.width, rect.height) / 72)
        assert crop_dpi > whole_dpi * 10
        doc.close()

    def test_a_crop_of_a_scan_is_not_upscaled_either(self):
        """A page with no text layer is fixed at its own resolution, and there
        the original rule holds exactly: bigger is empty pixels at full price."""
        doc = fitz.open()
        page = doc.new_page(width=42 * 72, height=30 * 72)
        rect = fitz.Rect(100, 100, 290, 320)
        pix = fitz.Pixmap(vlm.render_crop(page, rect, max_edge=3072))
        assert max(pix.width, pix.height) <= max(rect.width, rect.height) + 1
        doc.close()


class TestCropPrompt:
    """Matched against the prompt with its line wrapping flattened.

    A rule that spans two source lines is the same rule, and a test that misses
    it because of where the paragraph broke would pass the day the rule was
    deleted — which is the opposite of what these assert.
    """

    @staticmethod
    def flat():
        return re.sub(r"\s+", " ", vlm.CROP_SYSTEM)

    def test_the_coordinate_is_given_and_may_not_be_second_guessed(self):
        """The half of this that is not about resolution. The model is never
        asked where it is, so the bubble visible at a crop's edge must not be
        allowed to overrule the coordinate handed to it."""
        assert re.search(r"coordinate is given to you", self.flat(), re.I)
        assert re.search(r"never infer it", self.flat(), re.I)
        assert re.search(r"grid bubble visible inside a crop", self.flat(), re.I)

    def test_it_forbids_carrying_a_value_between_crops(self):
        """Crops look alike, which makes this the likeliest failure of the mode:
        the same size five times in a row is filling in the count, not reading."""
        assert re.search(r"never carry a value from one crop to the next", self.flat(), re.I)
        assert re.search(r"same size on five lines in a row", self.flat(), re.I)

    def test_a_line_is_owed_but_a_value_is_not(self):
        assert re.search(r"a line is owed for every crop", self.flat(), re.I)
        assert re.search(r"a value is not", self.flat(), re.I)

    def test_a_neighbours_label_is_not_the_answer(self):
        assert re.search(r"neighbouring intersection may be visible", self.flat(), re.I)

    def test_untrusted_drawing_text_is_named_as_such(self):
        assert re.search(r"untrusted input", self.flat(), re.I)

    def test_the_prompt_never_seeds_an_answer_from_the_sheet_under_test(self):
        """Same rule as the sheet prompt, for the same reason: an example
        carrying this drawing's own marks would let a model score the prompt."""
        import json

        for path in TestCropsAtEveryIntersection.SETS:
            for case in json.loads(path.read_text()):
                for label in (case["expected"], case.get("distractor")):
                    if label:
                        assert label.upper() not in vlm.CROP_SYSTEM.upper(), (
                            path.name,
                            label,
                        )


class TestDescribeFromCrops:
    """The assembled description, against the real sheet's grid."""

    @staticmethod
    def _answering(monkeypatch, *, answer=True, calls=None):
        """A transport that reads the crop listing out of the user turn and
        answers every crop correctly. The point of the tests below is what the
        ASSEMBLY does, so the model is made reliable and the failures are
        injected one at a time."""

        def fake_complete(system, user, **kwargs):
            labels = re.findall(r"^\s*(\d+)\.\s+(\S+)$", user, re.M)
            if calls is not None:
                calls.append([label for _, label in labels])
            if not answer:
                return llm.Reply(text="I cannot read these.", stop_reason="end_turn")
            body = "\n".join(
                f"{i}. {label}: footing F{i}, column HSS8X8X3/8" for i, label in labels
            )
            return llm.Reply(text=body, stop_reason="end_turn")

        monkeypatch.setattr(vlm.llm, "complete", fake_complete)

    def test_the_grid_header_comes_from_geometry_not_from_the_model(self, monkeypatch):
        """The failure this removes outright. A description that got three
        pairings right and labelled the row with its neighbour's letter cost 14
        of 40 eval questions and reported as abstentions — because the model was
        asked to name the grid. Here the names come off the same bubbles that
        decided where to crop, and no reply can change them."""
        doc, page, _, columns, rows = TestCropsAtEveryIntersection._real_sheet()
        self._answering(monkeypatch)
        text = vlm.describe_crops(page)
        named = vlm.grid_coverage(text)
        assert named is not None
        across, down, written = named
        assert (across, down) == (len(columns), len(rows))
        assert written == len(columns) * len(rows), "a line is owed at every intersection"
        doc.close()

    def test_its_lines_are_the_shape_the_sheet_pass_writes(self, monkeypatch):
        """Identical on purpose: the same `At 4/B: ...` the whole-sheet prompt
        demands, so a crop run and a sheet run are directly comparable and the
        experiment measures the method instead of the format."""
        doc, page, _, _, _ = TestCropsAtEveryIntersection._real_sheet()
        self._answering(monkeypatch)
        text = vlm.describe_crops(page)
        assert re.search(r"^At \S+/\S+: footing F\d+, column HSS8X8X3/8\.$", text, re.M)
        doc.close()

    def test_an_intersection_with_nothing_legible_still_gets_its_line(self, monkeypatch):
        def fake_complete(system, user, **kwargs):
            labels = re.findall(r"^\s*(\d+)\.\s+(\S+)$", user, re.M)
            return llm.Reply(
                text="\n".join(f"{i}. {label}: footing -, column -" for i, label in labels),
                stop_reason="end_turn",
            )

        monkeypatch.setattr(vlm.llm, "complete", fake_complete)
        doc, page, _, columns, rows = TestCropsAtEveryIntersection._real_sheet()
        text = vlm.describe_crops(page)
        assert text.count("nothing legible") == len(columns) * len(rows)
        doc.close()

    def test_a_page_with_no_grid_falls_back_rather_than_inventing_one(self, monkeypatch):
        self._answering(monkeypatch)
        doc = fitz.open()
        page = doc.new_page(width=42 * 72, height=30 * 72)
        page.insert_text((100, 100), "GENERAL NOTES", fontsize=12)
        assert vlm.describe_crops(page) is None
        doc.close()

    def test_a_grid_too_big_to_afford_is_refused_out_loud(self, monkeypatch, caplog):
        """One image per intersection, per page, for a whole document. The
        refusal has to name the spend, because the alternative is finding it on
        an invoice."""
        doc, page, _, columns, rows = TestCropsAtEveryIntersection._real_sheet()
        monkeypatch.setattr(vlm, "CROP_MAX", 4)
        self._answering(monkeypatch)
        with caplog.at_level("WARNING"):
            assert vlm.describe_crops(page) is None
        assert "VLM_CROP_MAX" in caplog.text
        assert str(len(columns) * len(rows)) in caplog.text
        doc.close()

    def test_a_reply_nothing_survives_falls_back_instead_of_storing_silence(
        self, monkeypatch, caplog
    ):
        doc, page, _, _, _ = TestCropsAtEveryIntersection._real_sheet()
        self._answering(monkeypatch, answer=False)
        with caplog.at_level("WARNING"):
            assert vlm.describe_crops(page) is None
        assert "falling back to the whole-sheet pass" in caplog.text
        doc.close()

    def test_a_few_unanswered_crops_are_retried_one_at_a_time(self, monkeypatch):
        doc, page, _, _, _ = TestCropsAtEveryIntersection._real_sheet()
        skipped = {"2/B"}
        seen = []

        def fake_complete(system, user, **kwargs):
            labels = re.findall(r"^\s*(\d+)\.\s+(\S+)$", user, re.M)
            seen.append([label for _, label in labels])
            keep = [(i, l) for i, l in labels if l not in skipped or len(labels) == 1]
            return llm.Reply(
                text="\n".join(f"{i}. {l}: footing F1, column HSS8X8X3/8" for i, l in keep),
                stop_reason="end_turn",
            )

        monkeypatch.setattr(vlm.llm, "complete", fake_complete)
        text = vlm.describe_crops(page)
        assert seen[-1] == ["2/B"], "the one that went unanswered is asked again alone"
        assert "At 2/B:" in text
        doc.close()

    def test_a_batch_that_answers_nothing_is_not_retried_crop_by_crop(
        self, monkeypatch, caplog
    ):
        """More than a batch's worth unanswered is the reply FORMAT failing, and
        asking again one at a time buys twenty more images and the same
        silence."""
        doc, page, _, columns, rows = TestCropsAtEveryIntersection._real_sheet()
        answered = []

        def fake_complete(system, user, **kwargs):
            labels = re.findall(r"^\s*(\d+)\.\s+(\S+)$", user, re.M)
            answered.append(len(labels))
            first = labels[:1]
            return llm.Reply(
                text="\n".join(f"{i}. {l}: footing F1, column HSS8X8X3/8" for i, l in first),
                stop_reason="end_turn",
            )

        monkeypatch.setattr(vlm.llm, "complete", fake_complete)
        with caplog.at_level("WARNING"):
            vlm.describe_crops(page)
        batches = -(-len(columns) * len(rows) // vlm.CROP_BATCH)
        assert len(answered) == batches, "no per-crop retry"
        assert "points at the reply FORMAT" in caplog.text
        doc.close()

    def test_what_it_costs_is_logged_on_every_page(self, monkeypatch, caplog):
        doc, page, _, columns, rows = TestCropsAtEveryIntersection._real_sheet()
        self._answering(monkeypatch)
        with caplog.at_level("INFO"):
            vlm.describe_crops(page, sheet_number="S-100.0")
        assert "where the whole-sheet pass sends 1" in caplog.text
        assert f"{len(columns) * len(rows)} intersections" in caplog.text
        doc.close()

    def test_the_settings_line_names_the_crop_numbers_when_cropping(self, monkeypatch, caplog):
        monkeypatch.setattr(vlm, "CROP_MODE", "intersections")
        with caplog.at_level("INFO"):
            vlm._report_settings()
        assert "VLM_CROP=intersections" in caplog.text
        assert "VLM_CROP_BATCH" in caplog.text
        assert "VLM_CROP_MAX_TOKENS" in caplog.text


class TestTheCropPassStillReportsItself:
    """Adding a mode must not delete the log lines the last two commits added.

    `render` reports the DPI and, through it, the settings. The crop pass never
    calls `render` — so shipping it without this removed both from the log of
    the run that most needs them, which is exactly the class of failure
    `_report_settings` exists to prevent.
    """

    def test_a_crop_run_still_says_what_dpi_reaches_the_model(self, monkeypatch, caplog):
        monkeypatch.setattr(vlm, "_resolution_reported", False)
        monkeypatch.setattr(vlm, "provider", lambda: "gemini")
        doc, page = sheet()
        with caplog.at_level("INFO"):
            vlm.render_crop(page, fitz.Rect(1000, 800, 1190, 1020), max_edge=3072)
        assert "reaches the model" in caplog.text
        assert "against 73 for the whole sheet" in caplog.text
        doc.close()

    def test_a_crop_run_still_says_what_configuration_produced_it(self, monkeypatch, caplog):
        monkeypatch.setattr(vlm, "_resolution_reported", False)
        monkeypatch.setattr(vlm, "CROP_MODE", "intersections")
        doc, page = sheet()
        with caplog.at_level("INFO"):
            vlm.render_crop(page, fitz.Rect(1000, 800, 1190, 1020), max_edge=3072)
        assert "vision pass settings" in caplog.text
        assert "VLM_CROP=intersections" in caplog.text
        doc.close()

    def test_it_is_said_once_per_process_like_the_whole_sheet_line(self, monkeypatch, caplog):
        monkeypatch.setattr(vlm, "_resolution_reported", False)
        doc, page = sheet()
        with caplog.at_level("INFO"):
            for _ in range(3):
                vlm.render_crop(page, fitz.Rect(1000, 800, 1190, 1020), max_edge=3072)
        assert caplog.text.count("reaches the model") == 1
        doc.close()


class TestTheFoundationFieldIsNotOneSpecies:
    """The measured failure that produced this class.

    S101P's crop run scored ZERO wrong, off-target, invented or hedged across
    51 cases and still abstained on 16 of 36 pile caps. Geometry was ruled out
    first — every one of those 36 marks falls inside its own crop — so the
    model saw them and declined. The prompt asked for "the footing mark (a
    short mark in a bubble or box, e.g. F31)", and this sheet has no footings:
    it has PILE CAPS, printed as a mark above an elevation rather than in a
    bubble. The column field scored 15 of 15 on the same crops, because
    "column" names an element the sheet HAS while "footing" named one species
    of foundation it does not.

    It is the same lesson the GENERATOR learned one sheet earlier — the
    vocabulary belonged to the first sheet — arriving at the prompt.
    """

    def test_the_crop_prompt_names_the_category_not_one_species(self):
        for element in ("pile cap", "pier", "pad"):
            assert element in vlm.CROP_SYSTEM.lower(), element

    def test_the_sheet_prompt_names_them_too(self):
        # The two prompts produce the SAME line format on purpose, so a
        # vocabulary that widens on one side and not the other makes a crop run
        # and a sheet run incomparable in exactly the way they must not be.
        for element in ("pile cap", "pier", "pad"):
            assert element in vlm.SYSTEM.lower(), element

    def test_the_parser_accepts_the_prompt_s_own_example_lines(self):
        """The contract the substring check could not express.

        Renaming the field in the instructions while the parser still expects
        `footing ..., column ...` is a silent break: the model does as it is
        told, every line is discarded on a regex miss, and the page falls back
        to the whole-sheet pass with nothing saying why. So the format is
        asserted where it actually lives — the parser must accept the very
        lines the prompt holds up as the shape to write.
        """
        import re as _re

        examples = [
            line.strip()
            for line in vlm.CROP_SYSTEM.splitlines()
            if _re.match(r"\s*\d+\.\s+\S+/\S+:", line)
        ]
        assert examples, "the crop prompt shows no example line at all"
        # Every answer-shaped line the prompt shows must carry its INDEX. The
        # index is half the alignment check — the coordinate says which
        # intersection the model thought it was, the index says which image it
        # was actually handed — so an example missing one teaches the shape
        # that drifts in silence.
        shown = [
            line
            for line in vlm.CROP_SYSTEM.splitlines()
            if ": footing" in line and ", column" in line
        ]
        for line in shown:
            assert _re.match(r"\s*\d+\.\s", line), f"example without an index: {line!r}"
        for line in examples:
            index = int(line.split(".", 1)[0])
            label = line.split(":", 1)[0].split(".", 1)[1].strip()
            # A batch long enough to contain this example's own index.
            labels = [f"X{n}/Y" for n in range(index)]
            labels[index - 1] = label
            got = vlm.parse_crop_batch(line, labels)
            assert label in got, (line, got)

    def test_both_still_demand_the_footing_field_name(self):
        # The FIELD NAME is the format, and the format is load-bearing:
        # grid_coverage counts these lines, split_description splits on them
        # and drawing_eval.mjs scores them. Widening what may go in the field
        # must not rename it, or every run in this repo's history stops being
        # comparable with the next one.
        assert "footing" in vlm.CROP_SYSTEM and "column" in vlm.CROP_SYSTEM
        assert "footing" in vlm.SYSTEM and "column" in vlm.SYSTEM

    def test_a_dash_is_refused_as_a_way_out_of_an_unfamiliar_notation(self):
        """The dash must stay available for illegible, and stop being
        available for "these instructions did not name my notation" — those
        read identically downstream and mean opposite things."""
        # Whitespace-collapsed: the prompt is wrapped prose, so a phrase that
        # happens to straddle a line break is still the phrase.
        said = " ".join(vlm.CROP_SYSTEM.lower().split())
        assert "illegible" in said
        assert "never because the drawing uses a notation" in said

    def test_the_illegible_rule_is_not_weakened(self):
        # Widening the vocabulary must not buy back the guess this prompt has
        # been narrowed twice to forbid.
        said = " ".join(vlm.CROP_SYSTEM.lower().split())
        assert "never carry a value from one crop to the next" in said
        assert "a value written beside the word illegible is still a value" in said

    def test_a_schedule_mark_is_an_acceptable_column_answer(self):
        # The column field scored 15/15 on marks it was never told to accept.
        # That it worked is luck the next sheet should not need.
        assert "column schedule" in vlm.CROP_SYSTEM.lower()
        assert "column schedule" in vlm.SYSTEM.lower()


class TestTheGatingRule:
    """`crop_decision` — feasibility and cost, in one place, before anything
    is spent.

    It was three refusals scattered through `describe_crops`, each logged where
    it happened and one of them missing entirely. A rule nobody can name is a
    rule nobody can count: "how many pages of this 400-page set were cropped?"
    had no answer short of grepping the log.
    """

    @staticmethod
    def _drawn_page(intersections=4):
        """A page with vector text, so the scan gate passes."""
        doc = fitz.open()
        page = doc.new_page(width=42 * 72, height=30 * 72)
        page.insert_text((100, 100), "S101P", fontsize=10)
        return doc, page

    def test_a_scan_is_refused_before_a_single_image_is_sent(self, monkeypatch):
        """The gate that was missing. A scan is fixed at its own resolution, so
        a crop of one is empty pixels at full price — and the cost is identical
        to the case where cropping works."""
        doc = fitz.open()
        page = doc.new_page(width=42 * 72, height=30 * 72)  # no text at all
        monkeypatch.setattr(vlm, "crops", lambda p: pytest.fail("geometry before the scan gate"))
        decision = vlm.crop_decision(page)
        assert decision.crop is False
        assert "vector text" in decision.reason
        doc.close()

    def test_a_page_with_no_grid_is_refused_quietly(self, monkeypatch):
        doc, page = self._drawn_page()
        monkeypatch.setattr(vlm, "crops", lambda p: [])
        decision = vlm.crop_decision(page)
        assert decision.crop is False and decision.intersections == 0
        assert decision.loud is False, "a detail sheet with no grid is routine"
        doc.close()

    def test_a_grid_over_the_cap_is_refused_LOUDLY(self, monkeypatch):
        """A cost cap is a decision someone may want to raise for this
        document, and burying it among a thousand info lines is how a set
        quietly runs the whole-sheet pass on every page it meant to crop."""
        doc, page = self._drawn_page()
        monkeypatch.setattr(vlm, "crops", lambda p: [("x", None)] * (vlm.CROP_MAX + 1))
        decision = vlm.crop_decision(page)
        assert decision.crop is False
        assert decision.loud is True
        assert "VLM_CROP_MAX" in decision.reason
        assert str(vlm.CROP_MAX + 1) in decision.reason
        doc.close()

    def test_a_grid_at_the_cap_is_allowed(self, monkeypatch):
        # The cap is a maximum, not a strict bound — off-by-one here silently
        # halves the pages a borderline document crops.
        doc, page = self._drawn_page()
        monkeypatch.setattr(vlm, "crops", lambda p: [("x", None)] * vlm.CROP_MAX)
        assert vlm.crop_decision(page).crop is True
        doc.close()

    def test_overlap_is_NOT_a_gate(self, monkeypatch):
        """The tempting fourth rule, which the measurements forbid. On the
        first sheet ALL 22 intersections had a neighbour's label reachable
        inside their crop, and that is the sheet the crop pass scored 98% on
        with no wrong answer of any kind. Gating on overlap would refuse the
        page the mode works best on."""
        doc, page = self._drawn_page()
        monkeypatch.setattr(vlm, "crops", lambda p: [("x", None)] * 22)
        assert vlm.crop_decision(page).crop is True
        doc.close()


class TestTheSettingsTravelWithTheDescription:
    """`chunks.sourceSettings` is what replaces `--label`, typed by hand off a
    log line. A wrong label manufactures a measurement rather than merely
    lacking one."""

    def test_the_snapshot_names_the_model_that_wrote_it(self):
        snap = vlm.settings_snapshot()
        assert snap["provider"] == vlm.provider()
        assert snap["model"] and snap["model"] in vlm.source_model()

    def test_the_snapshot_carries_every_setting_the_log_line_does(self, monkeypatch):
        """The two must not drift: the log is what a person reads during a run
        and the snapshot is what the benchmark reads afterwards, and a setting
        in one but not the other is a run whose configuration is half
        recoverable."""
        monkeypatch.setattr(vlm, "CROP_MODE", "intersections")
        snap = vlm.settings_snapshot()
        for key in ("VLM_MAX_TOKENS", "VLM_CROP", "VLM_CROP_BAYS", "VLM_CROP_MAX"):
            assert key in snap, key

    def test_a_whole_sheet_run_does_not_claim_crop_budgets(self, monkeypatch):
        """A settings record naming the budget that was NOT in force is the
        same class of lie as the DPI line that reported what it rendered
        rather than what the model read."""
        monkeypatch.setattr(vlm, "CROP_MODE", "off")
        snap = vlm.settings_snapshot()
        assert "VLM_CROP_MAX" not in snap and "VLM_CROP_BATCH" not in snap

    def test_two_runs_at_one_configuration_snapshot_identically(self):
        # This is the whole point: equal snapshots are the same experiment
        # repeated, which is what makes an error bar computable without anyone
        # remembering to pass a flag.
        assert vlm.settings_snapshot() == vlm.settings_snapshot()
