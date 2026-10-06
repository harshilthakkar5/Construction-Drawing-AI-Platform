"""Lining two plans up by the walls they both draw (wall_match), for sheets
with no grid. Pure tests on synthetic walls, plus the reader on a real PDF
page at every rotation. These prove what the code does; the measurement on a
client set is in docs/rfi-full-scan.md."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz  # noqa: E402
import pytest  # noqa: E402

import wall_match as wm  # noqa: E402
from wall_match import Segment, Walls  # noqa: E402


def floor_plan(seed=1, rooms=14):
    """Irregular walls, as a real floor plan has: rooms of different sizes."""
    rnd = random.Random(seed)
    h, v = [], []
    for _ in range(rooms):
        x, y = rnd.uniform(100, 1500), rnd.uniform(100, 1000)
        w, d = rnd.uniform(60, 400), rnd.uniform(60, 300)
        h += [Segment(round(y, 2), round(x, 2), round(x + w, 2)), Segment(round(y + d, 2), round(x, 2), round(x + w, 2))]
        v += [Segment(round(x, 2), round(y, 2), round(y + d, 2)), Segment(round(x + w, 2), round(y, 2), round(y + d, 2))]
        for _ in range(4):  # partitions and casework
            px = rnd.uniform(x, x + w)
            v.append(Segment(round(px, 2), round(y, 2), round(y + rnd.uniform(20, d), 2)))
            py = rnd.uniform(y, y + d)
            h.append(Segment(round(py, 2), round(x, 2), round(x + rnd.uniform(20, w), 2)))
    return Walls(h, v)


def shifted(walls, dx, dy, keep=1.0, seed=2):
    rnd = random.Random(seed)
    return Walls(
        [Segment(round(s.pos + dy, 2), round(s.lo + dx, 2), round(s.hi + dx, 2)) for s in walls.horizontal if rnd.random() < keep],
        [Segment(round(s.pos + dx, 2), round(s.lo + dy, 2), round(s.hi + dy, 2)) for s in walls.vertical if rnd.random() < keep],
    )


def test_a_copied_background_is_found_at_its_exact_shift():
    """The electrical plan carries most of the architect's walls, plus its own
    lines; the shift comes back to a fraction of a point."""
    a = floor_plan()
    b = shifted(a, -32.16, -147.0, keep=0.7)
    own = floor_plan(seed=9, rooms=6)
    b = Walls(b.horizontal + own.horizontal, b.vertical + own.vertical)
    found = wm.align(a, b)
    assert found is not None
    assert found.tx == pytest.approx(-32.16, abs=0.3) and found.ty == pytest.approx(-147.0, abs=0.3)
    assert found.matched >= wm.MIN_MATCHED and found.matched >= wm.MIN_RATIO * max(found.runner_up, 1)
    xs = [v for s in a.horizontal for v in (s.lo, s.hi)]
    assert found.extent[0] >= min(xs) - 1 and found.extent[2] <= max(xs) + 1


def test_two_unrelated_plans_do_not_line_up():
    assert wm.align(floor_plan(seed=1), floor_plan(seed=5)) is None


def test_a_regular_module_is_ambiguous_and_refused():
    """Identical bays line up with a copy shifted one bay nearly as well as with
    the right shift — the grid's own trap, and it must refuse the same way."""
    bays = Walls(
        [Segment(y, 0.0, 2000.0) for y in (100.0, 400.0)] * 1,
        [Segment(100.0 + 100.0 * i, 100.0 + 300.0 * j, 400.0 + 300.0 * j) for i in range(19) for j in range(4)],
    )
    assert wm.align(bays, shifted(bays, 30.0, 0.0)) is None


def test_the_shared_title_block_alone_is_not_a_match():
    """Every sheet of a set draws the same border and title block at the same
    place: 29 segments on the client set, which must never pass on their own."""
    rnd = random.Random(7)  # irregular, as a real title block's boxes are
    frame = Walls(
        [Segment(round(rnd.uniform(20, 2140), 2), 2600.0, round(rnd.uniform(2650, 2990), 2)) for _ in range(15)],
        [Segment(round(rnd.uniform(2600, 2990), 2), round(rnd.uniform(20, 1000), 2), 2140.0) for _ in range(14)],
    )
    a = Walls(frame.horizontal + floor_plan(seed=1).horizontal, frame.vertical + floor_plan(seed=1).vertical)
    b = Walls(frame.horizontal + floor_plan(seed=5).horizontal, frame.vertical + floor_plan(seed=5).vertical)
    assert wm.align(a, b) is None


def test_too_few_walls_are_refused():
    a = Walls([Segment(10.0, 0.0, 50.0)] * 1, [Segment(5.0, 0.0, 40.0)])
    assert wm.align(a, a) is None


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_walls_are_read_in_display_space_at_every_rotation(rotation):
    """A horizontal wall must stay horizontal on screen whatever /Rotate says,
    or two sheets drawn at different rotations vote for nonsense."""
    doc = fitz.open()
    page = doc.new_page(width=800, height=500)
    page.draw_line((100, 200), (400, 200))  # horizontal, 300 long
    page.draw_line((600, 50), (600, 150))  # vertical, 100 long
    page.draw_rect(fitz.Rect(100, 300, 160, 400))  # edges 60 x 100
    page.draw_line((100, 450), (105, 450))  # too short: hatching
    page.set_rotation(rotation)
    walls = wm.read_walls(page)
    to_display = page.rotation_matrix
    expected_h, expected_v = set(), set()
    for p, q in [((100, 200), (400, 200)), ((600, 50), (600, 150)),
                 ((100, 300), (160, 300)), ((160, 300), (160, 400)), ((160, 400), (100, 400)), ((100, 400), (100, 300))]:
        p, q = fitz.Point(p) * to_display, fitz.Point(q) * to_display
        if abs(p.y - q.y) < 0.05:
            expected_h.add((round(p.y), round(min(p.x, q.x)), round(max(p.x, q.x))))
        else:
            expected_v.add((round(p.x), round(min(p.y, q.y)), round(max(p.y, q.y))))
    assert {(round(s.pos), round(s.lo), round(s.hi)) for s in walls.horizontal} == expected_h
    assert {(round(s.pos), round(s.lo), round(s.hi)) for s in walls.vertical} == expected_v
