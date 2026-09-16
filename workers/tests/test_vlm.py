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


def test_render_never_upscales_a_small_sheet():
    """Extra pixels carry no extra information and are billed all the same —
    a Gemini tile costs 258 tokens whether or not anything is in it."""
    doc, page = sheet(width_in=8.5, height_in=11.0)
    pix = fitz.Pixmap(vlm.render(page, max_edge=2576))
    # 11in at 72dpi is 792pt -> 792px unscaled, not stretched up to 2576.
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
