"""The project map and the missing-detail check (Leland "T.O.C. SEE 24/S4.02",
where S4.02 has details 6, 11, 21 and 26). Synthetic PDFs, no model, no database."""

import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import detail_map as dm  # noqa: E402
from rfi_checks import Page  # noqa: E402


def _page(pid, sheet, n):
    return Page(id=pid, document_id="d", page_number=n, combined_page_number=n, sheet_number=sheet)


def _set(target_details=("6", "11", "21"), untitled_big=None, note="T.O.C. SEE 24/S4.02"):
    """Page 1: S4.10 with a note pointing at a detail. Page 2: S4.02 titling
    `target_details` (number, title, scale line under), plus optionally a
    number printed as large as a detail number but with no title."""
    doc = fitz.open()
    src = doc.new_page(width=1200, height=800)
    src.insert_text((400, 300), note, fontsize=8)
    tgt = doc.new_page(width=1200, height=800)
    for i, n in enumerate(target_details):
        x = 100 + 260 * i
        tgt.insert_text((x, 400), n, fontsize=14)
        tgt.insert_text((x + 30, 400), f"Wall Section Type {i + 1}", fontsize=10)
        tgt.insert_text((x + 32, 412), "3/4\" = 1'-0\"", fontsize=7)
    if untitled_big:
        tgt.insert_text((900, 600), untitled_big, fontsize=14)
    return doc


def _entries(doc):
    return [(_page("p1", "S4.10", 1), dm.read_page(doc[0])), (_page("p2", "S4.02", 2), dm.read_page(doc[1]))]


def test_read_page_reads_titles_symbols_and_sizes():
    doc = _set()
    src, tgt = dm.read_page(doc[0]), dm.read_page(doc[1])
    assert src["links"] and src["links"][0]["detail"] == "24" and src["links"][0]["sheet"] == "S4.02"
    assert set(tgt["details"]) == {"6", "11", "21"} and tgt["printed"]["21"] == 14.0


def test_a_symbol_to_a_detail_the_sheet_lacks_is_found():
    [m] = dm.missing_details(_entries(_set()), _entries(_set()))
    assert (m["sheet"], m["detail"], m["details"]) == ("S4.02", "24", ["6", "11", "21"])
    assert m["places"][0][0].sheet_number == "S4.10"


@pytest.mark.parametrize("why, doc", [
    ("the detail is there", _set(target_details=("6", "11", "24"))),
    ("the number is printed as big as a detail number: its title was missed", _set(untitled_big="24")),
    ("too few titles read to know the sheet's details", _set(target_details=("6", "11"))),
    ("the sheet is not in the set: dangling_reference's finding", _set(note="T.O.C. SEE 24/S9.99")),
])
def test_left_alone(why, doc):
    assert dm.missing_details(_entries(doc), _entries(doc)) == [], why


def test_a_titled_detail_exists_however_small_its_number_is_printed():
    doc = _set()
    doc[1].insert_text((900, 600), "24", fontsize=6)
    doc[1].insert_text((920, 600), "Wall Section Type 4", fontsize=10)
    doc[1].insert_text((922, 612), "3/4\" = 1'-0\"", fontsize=7)
    assert "24" in dm.read_page(doc[1])["details"]
    assert dm.missing_details(_entries(doc), _entries(doc)) == []


def test_a_number_printed_small_on_the_sheet_is_still_missing():
    """A "24" in a dimension or a note is not a detail."""
    doc = _set()
    doc[1].insert_text((900, 600), "24", fontsize=6)
    assert len(dm.missing_details(_entries(doc), _entries(doc))) == 1


class _FakeDb:
    """db.connect() stand-in: pages.detailMap as a dict, every UPDATE recorded."""

    def __init__(self, stored=None):
        self.stored, self.updates = dict(stored or {}), []

    @contextmanager
    def connect(self):
        yield self

    def execute(self, sql, params):
        if sql.startswith("UPDATE"):
            self.updates.append(params[2])
            self.stored[params[2]] = (params[0], params[1])
        self._rows = [(pid, *self.stored[pid]) for pid in params[0] if pid in self.stored] if sql.startswith("SELECT") else []
        return self

    def fetchall(self):
        return self._rows


def test_findings_read_each_page_once_and_word_the_question(monkeypatch):
    fake = _FakeDb()
    monkeypatch.setitem(sys.modules, "db", fake)
    doc = _set()
    pages = [_page("p1", "S4.10", 1), _page("p2", "S4.02", 2)]
    opened = []
    open_page = lambda d, n: opened.append(n) or doc[n - 1]

    [f], notes = dm.findings(pages, [], open_page)
    assert f.check_type == "missing_detail" and notes == []
    assert f.question == "S4.10 refers to detail 24/S4.02, but S4.02 has details 6, 11, 21 and no detail 24. Which detail is intended?"
    assert [e["role"] for e in f.evidence] == ["finding", "context"]
    assert f.evidence[0]["sheetNumber"] == "S4.10" and f.evidence[1]["sheetNumber"] == "S4.02"
    # the box is the note itself, in stored space
    x, y, w, h = (f.evidence[0]["bbox"][k] for k in ("x", "y", "width", "height"))
    assert "24/S4.02" in doc[0].get_textbox(fitz.Rect(x, y, x + w, y + h))
    assert sorted(fake.updates) == ["p1", "p2"] and sorted(opened) == [1, 2]

    # the second scan reads nothing: the map is saved on the page
    opened.clear()
    assert dm.findings(pages, [], open_page)[0][0].fingerprint == f.fingerprint
    assert opened == [] and len(fake.updates) == 2


def test_an_old_map_version_is_read_again(monkeypatch):
    fake = _FakeDb({"p1": ({"details": {}, "links": [], "printed": {}}, dm.MAP_VERSION - 1)})
    monkeypatch.setitem(sys.modules, "db", fake)
    doc = _set()
    dm.ensure([_page("p1", "S4.10", 1)], lambda d, n: doc[n - 1])
    assert fake.updates == ["p1"]


def test_a_review_finds_symbols_on_its_sheets_pointing_at_a_sheet_it_did_not_pick(monkeypatch):
    monkeypatch.setitem(sys.modules, "db", _FakeDb())
    doc = _set()
    src, tgt = _page("p1", "S4.10", 1), _page("p2", "S4.02", 2)
    open_page = lambda d, n: doc[n - 1]
    assert len(dm.findings([src], [], open_page, every=[src, tgt])[0]) == 1
    # reviewing only the target sheet: its own symbols point nowhere wrong
    assert dm.findings([tgt], [], open_page, every=[src, tgt])[0] == []
