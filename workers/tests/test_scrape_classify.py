"""The title-block sort run twice, as every processing run now re-reads it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import scrape  # noqa: E402


def test_an_inherited_discipline_survives_a_re_read(monkeypatch):
    # Page 2's title block reads nothing; it inherits page 1's discipline.
    pages = {
        f"p{n}": {"page_id": f"p{n}", "combined_page": n, "region_text": text, "filename": "set.pdf",
                  "discipline": None, "discipline_source": None, "sheet_number": None}
        for n, text in ((1, "C-1"), (2, ""), (3, "M-1"))
    }
    readings = {"C-1": ("C-1", "civil", "region_rules"), "": (None, None, None), "M-1": ("M-1", "mechanical", "region_rules")}
    writes: list[tuple] = []

    def set_page_sheet(page_id, sheet, discipline, source):
        writes.append((page_id, discipline, source))
        pages[page_id].update(sheet_number=sheet, discipline=discipline, discipline_source=source)

    monkeypatch.setattr(scrape.db, "pages_to_classify", lambda _p: [dict(r) for r in pages.values()])
    monkeypatch.setattr(scrape.db, "set_page_sheet", set_page_sheet)
    monkeypatch.setattr(scrape.db, "upsert_portions", lambda *a: None)
    monkeypatch.setattr(scrape.db, "assign_chunk_portions", lambda *a: None)
    monkeypatch.setattr(scrape, "_get_redis", lambda: None)
    monkeypatch.setattr(scrape.classify, "classify_region_batch",
                        lambda items, _r: [readings[text] for text, _f in items])

    scrape._classify_pages("proj")
    assert pages["p2"]["discipline"] == "civil" and pages["p2"]["discipline_source"] == "inherited"
    writes.clear()
    scrape._classify_pages("proj")  # nothing changed: nothing written, nothing wiped
    assert writes == [] and pages["p2"]["discipline"] == "civil"
