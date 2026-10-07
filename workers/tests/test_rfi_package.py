"""Marked-up RFI packages: the cover form and the drawing sheets with clouds,
callouts and leaders as PDF annotations — the shape of the team's own RFIs.

The geometry is tested at 0/90/180/270 because every rotated CAD sheet in the
client's set is where a mark would land in the wrong place: evidence boxes and
annotations live in the page's UNROTATED space, and the callout is placed in
the DISPLAYED one.
"""

import datetime
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import rfi_package as rp  # noqa: E402

TEST_DB = os.environ.get("RFI_TEST_DATABASE_URL")
CLIENT_PDF = Path(os.environ.get("RFI_REVIEW_TEST_PDF") or "/nonexistent")


def _sheet(rotation: int = 0, markup: bool = False) -> fitz.Document:
    """A 36x24 drawing with one word to cloud, at the given /Rotate."""
    doc = fitz.open()
    page = doc.new_page(width=2592, height=1728)
    page.insert_text((1200, 900), "C-6", fontsize=24)
    page.draw_rect(fitz.Rect(100, 100, 2492, 1628), color=(0, 0, 0))
    if markup:
        page.add_text_annot((300, 300), "someone else's markup")
    page.set_rotation(rotation)
    return doc


def _box(page: fitz.Page, word: str) -> dict:
    """Where a word is, as a chunk box: get_text reports the page's UNROTATED
    space whatever its /Rotate, which is the space evidence is stored in."""
    for w in page.get_text("words"):
        if w[4] == word:
            return {"x": w[0], "y": w[1], "width": w[2] - w[0], "height": w[3] - w[1]}
    raise AssertionError(word)


def _clouded_words(sheet: fitz.Page) -> set[str]:
    """The words INSIDE the cloud, read independently of any matrix: the
    cloud's rect is unrotated and so is get_text's clip."""
    cloud = next(a for a in sheet.annots() if a.type[1] == "Polygon")
    return {w[4] for w in sheet.get_text("words", clip=cloud.rect)}


def _item(marks, draft=False, number=2) -> rp.Item:
    return rp.Item(
        "rfi", "id", None if draft else number, "CONFIRM THE COLUMN LOCATION",
        "Please confirm the location of column C-6, as the plans disagree.",
        "structural", datetime.datetime(2026, 1, 1), "AA", None, "UT Law Student Housing",
        draft=draft, marks=marks, reasoning="The two plans disagree." if draft else None,
    )


# --- geometry ------------------------------------------------------------------------


def test_a_cloud_is_the_evidence_padded_and_kept_on_the_page():
    page = fitz.Rect(0, 0, 1000, 800)
    assert rp.cloud_rect({"x": 100, "y": 100, "width": 50, "height": 20}, page, 10) == fitz.Rect(90, 90, 160, 130)
    assert rp.cloud_rect({"x": 0, "y": 0, "width": 30, "height": 30}, page, 10) == fitz.Rect(0, 0, 40, 40)


def test_no_cloud_round_nothing_or_round_the_whole_sheet():
    page = fitz.Rect(0, 0, 1000, 800)
    assert rp.cloud_rect(None, page, 10) is None
    assert rp.cloud_rect({"x": 0, "y": 0, "width": 0, "height": 5}, page, 10) is None
    assert rp.cloud_rect({"x": 0, "y": 0, "width": 1000, "height": 800}, page, 10) is None
    assert rp.cloud_rect({"x": "a"}, page, 10) is None


def test_clouds_that_nearly_touch_become_one():
    merged = rp.merge_rects([fitz.Rect(0, 0, 10, 10), fitz.Rect(15, 0, 25, 10), fitz.Rect(500, 500, 510, 510)], gap=6)
    assert sorted((r.x0, r.x1) for r in merged) == [(0, 25), (500, 510)]


def test_a_callout_sits_beside_the_cloud_on_the_sheet_and_off_the_cloud():
    page = fitz.Rect(0, 0, 2592, 1728)
    cloud = fitz.Rect(1200, 800, 1300, 900)
    r = rp.place_callout((400, 100), [cloud], page, [], gap=40)
    assert page.contains(r) and not r.intersects(cloud)
    assert r.x0 == cloud.x1 + 40  # right of it, first choice


def test_a_callout_near_the_edge_goes_to_the_other_side():
    page = fitz.Rect(0, 0, 2592, 1728)
    cloud = fitz.Rect(2400, 800, 2500, 900)
    r = rp.place_callout((400, 100), [cloud], page, [], gap=40)
    assert page.contains(r) and not r.intersects(cloud) and r.x1 <= cloud.x0


def test_a_leader_runs_from_the_callout_edge_to_the_cloud_edge():
    a, b = rp.leader(fitz.Rect(500, 100, 700, 200), fitz.Rect(100, 120, 200, 180))
    assert (a.x, b.x) == (500, 200) and 120 <= a.y <= 180


# --- marking a sheet at every rotation ---------------------------------------------------


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_the_cloud_lands_on_the_evidence_and_the_callout_on_the_sheet(rotation):
    src = _sheet(rotation)
    box = _box(src[0], "C-6")
    out, notes = rp.render([_item([rp.Mark("d", 1, 1, "S2.105", box)])], lambda d: src)
    assert notes == [] and out.page_count == 2
    sheet = out[1]
    assert sheet.rotation == rotation
    kinds = {a.type[1]: a for a in sheet.annots()}
    assert set(kinds) == {"Polygon", "FreeText", "Line"}
    cloud = kinds["Polygon"]
    assert cloud.border["clouds"] == 2 and tuple(round(c, 1) for c in cloud.colors["stroke"]) == rp.RED
    # The cloud is round the word itself — read back by clipping the drawing,
    # not by comparing the cloud with the box it was made from.
    assert "C-6" in _clouded_words(sheet)
    # The callout is on the sheet as displayed, clear of the cloud, and upright.
    shown = fitz.Rect(kinds["FreeText"].rect * sheet.rotation_matrix).normalize()
    assert sheet.rect.contains(shown)
    assert not shown.intersects(fitz.Rect(cloud.rect * sheet.rotation_matrix).normalize())
    assert "RFI 002" in kinds["FreeText"].info["content"]


def test_existing_markup_on_the_source_sheet_is_not_carried_over():
    src = _sheet(markup=True)
    out, _ = rp.render([_item([rp.Mark("d", 1, 1, "S2.105", _box(src[0], "C-6"))])], lambda d: src)
    assert "Text" not in {a.type[1] for a in out[1].annots()}


def test_the_sheet_is_copied_as_a_drawing_not_a_picture():
    src = _sheet()
    out, _ = rp.render([_item([rp.Mark("d", 1, 1, "S2.105", _box(src[0], "C-6"))])], lambda d: src)
    assert "C-6" in out[1].get_text() and not out[1].get_images()


# --- the cover form ----------------------------------------------------------------------


def test_the_cover_carries_the_forms_fields_and_a_picture_of_the_cloud():
    src = _sheet(90)
    out, _ = rp.render([_item([rp.Mark("d", 1, 1, "S2.105", _box(src[0], "C-6"))])], lambda d: src)
    cover = out[0]
    text = cover.get_text()
    for field in ("PROJECT - UT LAW STUDENT HOUSING", "RFI: 002", "Date Issued:", "01/01/2026", "Author:", "AA",
                  "Discipline:", "STRUCTURAL", "Description:", "CONFIRM THE COLUMN LOCATION", "Plan/Sheet:",
                  "S2.105", "Issue:", "Q.1) Please confirm the location of column C-6"):
        assert field in text, field
    assert len(cover.get_images()) == 1


def test_a_draft_has_no_number_and_says_it_is_not_issued_on_every_page():
    src = _sheet()
    out, _ = rp.render([_item([rp.Mark("d", 1, 1, "S2.105", _box(src[0], "C-6"))], draft=True)], lambda d: src)
    assert "DRAFT RFI" in out[0].get_text() and "RFI: 0" not in out[0].get_text()
    assert "not issued" in out[0].get_text()
    note = next(a for a in out[1].annots() if a.type[1] == "FreeText")
    assert note.info["content"].startswith("DRAFT RFI")


def test_a_sheet_no_longer_in_the_project_is_said_not_skipped():
    out, notes = rp.render([_item([rp.Mark(None, 1, 1, "S2.105", None)])], lambda d: None)
    assert out.page_count == 1 and "no longer in the project" in out[0].get_text()
    assert notes and "no longer" in notes[0]


def test_several_items_each_get_their_own_cover():
    src = _sheet()
    box = _box(src[0], "C-6")
    out, _ = rp.render([_item([rp.Mark("d", 1, 1, "S2.105", box)], number=n) for n in (3, 4)], lambda d: src)
    assert out.page_count == 4
    assert "RFI: 003" in out[0].get_text() and "RFI: 004" in out[2].get_text()


def test_evidence_that_is_a_models_account_is_not_clouded():
    marks = rp._marks_from_evidence([
        {"documentId": "d", "pageNumber": 1, "bbox": {"x": 1, "y": 1, "width": 5, "height": 5}, "kind": "text"},
        {"documentId": "d", "pageNumber": 1, "bbox": None, "kind": "description"},
        {"documentId": "d", "pageNumber": 1, "bbox": None, "kind": "aid"},
        {"documentId": "d"},
    ])
    assert len(marks) == 1


def test_author_is_written_as_initials_like_the_form():
    assert rp._initials("Abhishek Agarwal") == "AA" and rp._initials(None) is None


# --- the client's own sheets -------------------------------------------------------------


@pytest.mark.skipif(not CLIENT_PDF.exists(), reason="set RFI_REVIEW_TEST_PDF to the client's S2.105/A3.01 set")
def test_the_client_sheets_mark_up_like_rfi_002():
    """RFI 002's own cloud (the row-bubble strip down S2.105's left edge) and a
    mark on the /Rotate 90 A3.01."""
    src = fitz.open(CLIENT_PDF)
    strip = {"x": 150, "y": 140, "width": 220, "height": 1420}
    out, notes = rp.render(
        [_item([rp.Mark("d", 1, 1, "S2.105", strip), rp.Mark("d", 2, 2, "A3.01", _box(src[1], "A4B"))])],
        lambda d: src,
    )
    assert notes == [] and out.page_count == 3
    assert out[2].rotation == 90
    assert "A4B" in _clouded_words(out[2])
    for sheet in (out[1], out[2]):
        shown = next(fitz.Rect(a.rect * sheet.rotation_matrix).normalize() for a in sheet.annots() if a.type[1] == "FreeText")
        assert sheet.rect.contains(shown)
    assert "S2.105, A3.01" in out[0].get_text()


# --- the job against a real database ------------------------------------------------------


@pytest.mark.skipif(not TEST_DB, reason="set RFI_TEST_DATABASE_URL to a migrated database")
def test_the_job_renders_an_rfi_and_a_finding_and_stores_the_pdf(monkeypatch, tmp_path):
    import config
    import db
    import storage

    monkeypatch.setattr(config, "DATABASE_URL", TEST_DB)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_unavailable", True)
    src = tmp_path / "set.pdf"
    doc = _sheet(90)
    box = _box(doc[0], "C-6")
    doc.save(src)
    monkeypatch.setattr(storage, "download_to_file", lambda key, path: shutil.copy(src, path))
    stored = {}
    monkeypatch.setattr(storage, "put_bytes", lambda key, data, ct: stored.__setitem__(key, (data, ct)))

    project, document, user, rfi, cand, pkg = (str(uuid.uuid4()) for _ in range(6))
    with db.connect() as conn:
        conn.execute("INSERT INTO users (id, email, name, \"passwordHash\") VALUES (%s, %s, 'Abhishek Agarwal', 'x')",
                     (user, f"{user}@test.invalid"))
        conn.execute("INSERT INTO projects (id, name) VALUES (%s, 'UT Law Student Housing')", (project,))
        conn.execute('INSERT INTO documents (id, "projectId", filename, "spacesKey", pages, status) '
                     "VALUES (%s, %s, 'set.pdf', 'k', 1, 'completed')", (document, project))
        conn.execute('INSERT INTO rfis (id, "projectId", number, subject, question, status, discipline, "createdById", "updatedAt") '
                     "VALUES (%s, %s, 7, 'Confirm the column location', 'Where is C-6?', 'open', 'structural', %s, now())",
                     (rfi, project, user))
        conn.execute('INSERT INTO rfi_locations (id, "rfiId", "documentId", "pageNumber", "combinedPageNumber", bbox, "sheetNumber") '
                     "VALUES (%s, %s, %s, 1, 1, %s, 'S2.105')", (str(uuid.uuid4()), rfi, document, json.dumps(box)))
        conn.execute('INSERT INTO rfi_candidates (id, "projectId", fingerprint, "checkType", confidence, subject, question, '
                     '"questionSource", evidence, "updatedAt") VALUES (%s, %s, %s, \'column_mismatch\', \'high\', '
                     "'Column C-6 missing', 'Is C-6 on A3.01?', 'template', %s, now())",
                     (cand, project, f"fp-{cand}", json.dumps([{"documentId": document, "pageNumber": 1, "bbox": box,
                                                                  "sheetNumber": "S2.105", "kind": "text"}])))
        conn.execute("INSERT INTO rfi_packages (id, \"projectId\", items) VALUES (%s, %s, %s)",
                     (pkg, project, json.dumps([{"type": "rfi", "id": rfi}, {"type": "candidate", "id": cand}])))
    try:
        result = rp.run(pkg)
        assert result["items"] == 2 and result["pages"] == 4
        with db.connect() as conn:
            status, key, pages = conn.execute('SELECT status, key, pages FROM rfi_packages WHERE id = %s', (pkg,)).fetchone()
        assert (status, pages) == ("ready", 4) and key == f"projects/{project}/rfi-packages/{pkg}.pdf"
        data, content_type = stored[key]
        assert content_type == "application/pdf"
        out = fitz.open(stream=data, filetype="pdf")
        assert "RFI: 007" in out[0].get_text() and "AA" in out[0].get_text()
        assert "DRAFT RFI" in out[2].get_text()
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = %s", (project,))
            conn.execute("DELETE FROM users WHERE id = %s", (user,))


@pytest.mark.skipif(not TEST_DB, reason="set RFI_TEST_DATABASE_URL to a migrated database")
def test_a_package_of_nothing_fails_and_says_why(monkeypatch):
    import config
    import db

    monkeypatch.setattr(config, "DATABASE_URL", TEST_DB)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_unavailable", True)
    project, pkg = str(uuid.uuid4()), str(uuid.uuid4())
    with db.connect() as conn:
        conn.execute("INSERT INTO projects (id, name) VALUES (%s, 'empty')", (project,))
        conn.execute("INSERT INTO rfi_packages (id, \"projectId\", items) VALUES (%s, %s, %s)",
                     (pkg, project, json.dumps([{"type": "rfi", "id": str(uuid.uuid4())}])))
    try:
        assert "failed" in rp.run(pkg)
        with db.connect() as conn:
            status, error = conn.execute('SELECT status, error FROM rfi_packages WHERE id = %s', (pkg,)).fetchone()
        assert status == "failed" and "none of the requested" in error
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = %s", (project,))


# --- the issue the sheets were printed under ------------------------------------------


def _titled(lines, body=()):
    """A 36x24 sheet with `lines` [(x, y, text)] in it, title block on the right."""
    doc = fitz.open()
    page = doc.new_page(width=2592, height=1728)
    for x, y, text in list(lines) + list(body):
        page.insert_text((x, y), text, fontsize=8)
    return doc


def test_the_title_block_issue_is_read_and_the_plot_stamp_is_not():
    """A reviewer's critique: "Revision: Unknown" over sheets whose title
    blocks read 03/31/26 90% MNAA-AIR REVIEW SUBMITTAL."""
    doc = _titled(
        [(2400, 1500, "3/30/2026 7:36:39 AM"), (2240, 1600, "03/31/26"), (2300, 1600, "90% MNAA-AIR REVIEW SUBMITTAL")],
        body=[(300, 300, "ISSUED FOR CONSTRUCTION DRAWINGS SHALL BE STAMPED")],
    )
    assert rp.title_block_issue(doc[0]) == "03/31/26 90% MNAA-AIR REVIEW SUBMITTAL"


def test_a_not_for_construction_stamp_is_a_status_never_an_issue():
    doc = _titled([(2240, 1600, "03/31/26"), (2300, 1600, "95% MNAA-AIR REVIEW SUB"),
                   (2400, 1400, "NOT FOR"), (2400, 1410, "CONSTRUCTION")])
    assert rp.title_block_issue(doc[0]) == "03/31/26 95% MNAA-AIR REVIEW SUB (NOT FOR CONSTRUCTION)"
    alone = _titled([(2400, 1400, "NOT FOR CONSTRUCTION")])
    assert rp.title_block_issue(alone[0]) == "NOT FOR CONSTRUCTION"
    assert rp.title_block_issue(_titled([])[0]) is None


def test_the_latest_issue_in_a_revision_list_wins():
    doc = _titled([(2240, 1580, "01/15/26"), (2300, 1580, "ISSUED FOR PERMIT"),
                   (2240, 1600, "03/31/26"), (2300, 1600, "UPDATE FOR PRICING")])
    assert rp.title_block_issue(doc[0]) == "03/31/26 UPDATE FOR PRICING"


def test_the_cover_shows_each_sheets_issue_when_they_differ():
    a = _titled([(2240, 1600, "03/31/26"), (2300, 1600, "UPDATE FOR PRICING")])
    s = _titled([(2240, 1600, "03/31/26"), (2300, 1600, "95% MNAA-AIR REVIEW SUB")])
    docs = {"a": a, "s": s}
    item = _item([rp.Mark("a", 1, 1, "A1.01", {"x": 100, "y": 100, "width": 50, "height": 50}),
                  rp.Mark("s", 1, 1, "S2.01", {"x": 100, "y": 100, "width": 50, "height": 50})])
    assert rp.sheet_issues(item, docs.get) == "A1.01: 03/31/26 UPDATE FOR PRICING; S2.01: 03/31/26 95% MNAA-AIR REVIEW SUB"
    item = _item([rp.Mark("a", 1, 1, "A1.01", {"x": 100, "y": 100, "width": 50, "height": 50})])
    assert rp.sheet_issues(item, docs.get) == "03/31/26 UPDATE FOR PRICING"
