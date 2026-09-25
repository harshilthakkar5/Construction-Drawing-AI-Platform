"""Summary size, thinking, per-run isolation, crash safety.

Each of these was a way a summary run could quietly go wrong:
  * a dialog promising 25 points over a worker that writes 8,
  * two concurrent runs sharing module globals (one run's calls billed to
    another project, written with its role focus),
  * a worker that stops at page 90 of 100 throwing away 90 paid answers,
  * a crashed run leaving the button locked for ever,
  * batch calls ignoring the thinking settings direct calls honour.
"""

import json
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

import llm  # noqa: E402
import summarize  # noqa: E402

SHARED = Path(__file__).resolve().parents[2] / "packages/shared/src/index.ts"
ID = "11111111-aaaa-4bbb-8ccc-000000000001"


def items(n: int) -> str:
    return json.dumps(
        {"overview": "o", "items": [{"text": f"fact {i}", "chunkIds": [ID]} for i in range(n)]}
    )


@pytest.fixture(autouse=True)
def fresh_context(monkeypatch):
    monkeypatch.delenv("SUMMARY_DETAIL", raising=False)
    monkeypatch.delenv("SUMMARY_THINKING", raising=False)
    summarize._local.ctx = summarize.RunContext()
    yield
    summarize._local.ctx = summarize.RunContext()


# --- sizes -------------------------------------------------------------------------


def test_sizes_mirror_the_table_the_dialog_offers():
    """The dialog prices and promises SUMMARY_DETAILS; the worker writes
    DETAIL_LEVELS. A drift charges for one size and delivers another."""
    block = re.search(r"export const SUMMARY_DETAILS = \{(.*?)\} as const;", SHARED.read_text(), re.S)
    assert block, "SUMMARY_DETAILS not found in @cdip/shared"
    shared = {
        key: {"points": int(points), "overview": overview}
        for key, points, overview in re.findall(
            r'(\w+): \{ label: "[^"]+", points: (\d+), overview: "([^"]+)" \}', block.group(1)
        )
    }
    assert shared == summarize.DETAIL_LEVELS
    assert list(shared) == list(summarize.DETAIL_LEVELS), "the picker's order"


def test_standard_is_the_size_summaries_always_had():
    assert summarize.DETAIL_LEVELS["standard"]["points"] == summarize.MAX_ITEMS
    assert summarize.rollup_max_tokens(summarize.MAX_ITEMS) == summarize.MAX_TOKENS


def test_bigger_sizes_get_more_room_and_never_less_than_the_setting(monkeypatch):
    points = [level["points"] for level in summarize.DETAIL_LEVELS.values()]
    caps = [summarize.rollup_max_tokens(p) for p in points]
    assert caps == sorted(caps)
    assert caps[-1] > summarize.MAX_TOKENS
    monkeypatch.setattr(summarize, "MAX_TOKENS", 9000)
    assert summarize.rollup_max_tokens(25) == 9000, "raising SUMMARY_MAX_TOKENS raises every tier"


def test_the_size_falls_back_to_the_env_default_then_standard(monkeypatch, caplog):
    assert summarize.resolve_detail(None) == "standard"
    monkeypatch.setenv("SUMMARY_DETAIL", "detailed")
    assert summarize.resolve_detail(None) == "detailed"
    assert summarize.resolve_detail("brief") == "brief", "a request beats the default"
    assert summarize.resolve_detail("huge") == "detailed"
    monkeypatch.setenv("SUMMARY_DETAIL", "enormous")
    assert summarize.resolve_detail(None) == "standard"
    assert "SUMMARY_DETAIL" in caplog.text


@pytest.mark.parametrize("detail,points", [("brief", 5), ("full", 25)])
def test_a_rollup_asks_for_and_keeps_its_size(monkeypatch, detail, points):
    summarize._local.ctx = summarize.RunContext(detail=detail)
    sent = []

    def fake_direct(prompt, max_tokens=None):
        sent.append((prompt, max_tokens))
        return items(40), "end_turn"

    monkeypatch.setattr(summarize, "_call_direct", fake_direct)
    lower = [{"overview": "p", "items": [{"text": "t", "chunkIds": [ID]}]}]
    result = summarize._rollup("portion", "the Structural portion", lower, {ID: 1})

    prompt, max_tokens = sent[0]
    assert f"at most {points} items" in prompt
    assert summarize.DETAIL_LEVELS[detail]["overview"] in prompt
    assert max_tokens == summarize.rollup_max_tokens(points)
    assert len(result["items"]) == points, "the parser cuts at the size asked for"
    assert result["detail"] == detail


def test_the_page_tier_keeps_one_size_whatever_the_run_asked_for():
    """Page summaries are reused by every later run, so they have one shape."""
    summarize._local.ctx = summarize.RunContext(detail="full")
    page = {"combined_page": 3, "chunks": [{"id": ID, "text": "t"}]}
    assert f"at most {summarize.MAX_ITEMS} items" in summarize.page_prompt(page)


def test_a_bigger_merge_fallback_keeps_more_points():
    lower = [{"overview": "", "items": [{"text": f"i{n}", "chunkIds": [ID]} for n in range(30)]}]
    assert len(summarize._merge_lower(lower, {ID: 1}, 25)["items"]) == 25
    assert len(summarize._merge_lower(lower, {ID: 1})["items"]) == summarize.MAX_ITEMS


# --- thinking ---------------------------------------------------------------------------


class _Db:
    def __init__(self, roles=None):
        self.roles = roles or []

    def project_roles(self, project_id):
        return self.roles


def test_summary_thinking_reaches_direct_and_batch_calls(monkeypatch):
    monkeypatch.setitem(sys.modules, "db", _Db())
    monkeypatch.setenv("SUMMARY_THINKING", "low")
    summarize._begin("proj", None)
    seen = {}
    monkeypatch.setattr(
        llm, "complete", lambda *a, **kw: seen.setdefault("direct", kw) and None
    )
    monkeypatch.setattr(
        llm, "complete_batch", lambda prompts, **kw: seen.setdefault("batch", kw) and {}
    )
    summarize._call_direct("p")
    summarize._call_batch({"page-0": "p"})
    assert seen["direct"]["thinking"] == "low"
    assert seen["batch"]["thinking"] == "low"
    assert seen["direct"]["project_id"] == "proj"


def test_unset_summary_thinking_leaves_the_global_defaults(monkeypatch):
    monkeypatch.setitem(sys.modules, "db", _Db())
    summarize._begin("proj", None)
    seen = {}
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: seen.update(kw))
    summarize._call_direct("p")
    assert seen["thinking"] is None


# --- per-run isolation ---------------------------------------------------------------


def test_concurrent_runs_do_not_share_a_context(monkeypatch):
    """Two discipline summaries run at once in two threads. With module
    globals, the second overwrote the first's project and roles mid-run."""

    class RolesDb:
        def project_roles(self, project_id):
            return [f"roles-of-{project_id}"]

    monkeypatch.setitem(sys.modules, "db", RolesDb())
    barrier = threading.Barrier(2)
    seen: dict[str, tuple] = {}

    def run(project, detail):
        summarize._begin(project, detail)
        barrier.wait()  # both have started before either reads back
        ctx = summarize._ctx()
        seen[project] = (ctx.project_id, ctx.roles, ctx.detail)

    threads = [
        threading.Thread(target=run, args=("A", "brief")),
        threading.Thread(target=run, args=("B", "full")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen["A"] == ("A", ["roles-of-A"], "brief")
    assert seen["B"] == ("B", ["roles-of-B"], "full")


# --- crash safety -------------------------------------------------------------------------


class _PageDb:
    def __init__(self):
        self.inserted: list[int] = []

    def existing_page_summaries(self, project_id):
        return {}

    def insert_summary(self, project_id, portion_id, level, summary, sources):
        self.inserted.append(summary["pageNumber"])

    def delete_page_summary(self, *a):
        pass


def test_each_page_is_saved_before_the_next_is_asked_for(monkeypatch):
    """A worker that stopped at page 3 of 5 used to throw away the two paid
    answers already in hand, because nothing was written until every call
    had returned."""
    db = _PageDb()
    monkeypatch.setitem(sys.modules, "db", db)
    monkeypatch.setattr(summarize, "USE_BATCH", False)
    calls = []

    def fake_direct(prompt, max_tokens=None):
        calls.append(prompt)
        if len(calls) == 3:
            raise RuntimeError("worker restarted")
        return items(2), "end_turn"

    monkeypatch.setattr(summarize, "_call_direct", fake_direct)
    pages = [
        {"document_id": "d", "page_number": n, "combined_page": n, "chunks": [{"id": ID, "text": "t"}]}
        for n in range(1, 6)
    ]
    with pytest.raises(RuntimeError):
        summarize._summarize_pages(pages, {ID: 1}, "proj")
    assert db.inserted == [1, 2], "the two finished pages are kept for the next run to reuse"


def test_the_heartbeat_beats_while_the_run_lasts_and_stops_after(monkeypatch):
    beats = []

    class BeatDb:
        def touch_portion_heartbeat(self, portion_id):
            beats.append(portion_id)

    monkeypatch.setitem(sys.modules, "db", BeatDb())
    monkeypatch.setattr(summarize, "HEARTBEAT_SECONDS", 0.01)
    with summarize._heartbeat("portion-1"):
        time.sleep(0.08)
    assert len(beats) >= 2 and set(beats) == {"portion-1"}
    after = len(beats)
    time.sleep(0.05)
    assert len(beats) == after, "a finished run stops beating"


def test_a_failing_heartbeat_never_fails_the_run(monkeypatch):
    class BrokenDb:
        def touch_portion_heartbeat(self, portion_id):
            raise RuntimeError("database away")

    monkeypatch.setitem(sys.modules, "db", BrokenDb())
    monkeypatch.setattr(summarize, "HEARTBEAT_SECONDS", 0.01)
    with summarize._heartbeat("portion-1"):
        time.sleep(0.03)  # would raise into the run if the error escaped


# --- batch thinking (llm.py) ------------------------------------------------------------


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Entry:
    def __init__(self, custom_id, ok=True):
        self.custom_id = custom_id
        message = type("M", (), {"content": [_Block("{}")], "usage": None})()
        self.result = type("R", (), {"type": "succeeded" if ok else "errored", "message": message})()


class _FakeAnthropic:
    """Records every batch submitted; the first `fail_first` batches error."""

    def __init__(self, fail_first=0):
        self.submitted: list[list[dict]] = []
        self.fail_first = fail_first
        outer = self

        class Batches:
            def create(self, requests):
                outer.submitted.append(requests)
                return type("B", (), {"id": f"batch-{len(outer.submitted)}"})()

            def retrieve(self, batch_id):
                return type("J", (), {"processing_status": "ended"})()

            def results(self, batch_id):
                ok = len(outer.submitted) > outer.fail_first
                return [_Entry(r["custom_id"], ok) for r in outer.submitted[-1]]

        self.messages = type("Msgs", (), {"batches": Batches()})()


@pytest.fixture
def fake_anthropic(monkeypatch):
    def install(fail_first=0):
        client = _FakeAnthropic(fail_first)
        monkeypatch.setattr(llm, "anthropic_client", lambda: client)
        monkeypatch.setattr(llm, "await_batch", lambda label, refresh, finished: refresh())
        import usage

        monkeypatch.setattr(usage, "record_message", lambda *a, **k: None)
        monkeypatch.setattr(llm, "_no_thinking_param", set())
        return client

    return install


def _batch(**kw):
    return llm.complete_batch(
        {"page-0": "p"},
        system="s",
        provider="claude",
        claude_model="claude-sonnet-5",
        gemini_model="unused",
        max_tokens=2000,
        kind="summary",
        **kw,
    )


def test_a_claude_batch_sends_the_thinking_field_a_single_call_would(fake_anthropic):
    """It sent none, and on Sonnet 5 an omitted field means ADAPTIVE thinking:
    CLAUDE_THINKING=off held for direct calls and not for batches."""
    client = fake_anthropic()
    assert _batch() == {"page-0": "{}"}
    params = client.submitted[0][0]["params"]
    assert params["thinking"] == {"type": "disabled"}
    assert params["max_tokens"] == 2000


def test_a_claude_batch_takes_the_stage_setting_and_its_headroom(fake_anthropic):
    client = fake_anthropic()
    _batch(thinking="high")
    params = client.submitted[0][0]["params"]
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"] == {"effort": "high"}
    assert params["max_tokens"] == 2000 + llm._CLAUDE_EFFORT_HEADROOM["high"]


def test_a_batch_refusing_the_thinking_field_is_resubmitted_once_without_it(fake_anthropic):
    client = fake_anthropic(fail_first=1)
    assert _batch() == {"page-0": "{}"}
    assert len(client.submitted) == 2
    assert "thinking" not in client.submitted[1][0]["params"]


def test_a_gemini_batch_takes_the_stage_setting_and_its_headroom(monkeypatch):
    seen = {}

    def fake_run(prompts, **kw):
        seen.update(kw)
        return {"page-0": "{}"}, False

    monkeypatch.setattr(llm, "_run_gemini_batch", fake_run)
    monkeypatch.setattr(llm, "_stage_thinking_latched", {})
    llm.complete_batch(
        {"page-0": "p"},
        system="s",
        provider="gemini",
        claude_model="unused",
        gemini_model="gemini-3.6-flash",
        max_tokens=2000,
        kind="summary",
        thinking="low",
    )
    assert seen["thinking"] == {"thinking_level": "low"}
    assert seen["max_tokens"] == 2000 + llm._GEMINI_STAGE_HEADROOM["low"]
