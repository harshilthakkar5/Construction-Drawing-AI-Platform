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
    assert "WHICH LABEL GOES WITH WHICH THING" in fake_transport["system"]
    assert "already been extracted and indexed" in fake_transport["system"]


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
