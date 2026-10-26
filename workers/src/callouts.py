"""Where a plan calls out a detail: section and detail symbols read off the
page's words, no model.

Two printed forms:
  * stacked — the detail number directly above the sheet it is drawn on,
    inside a circle ("3" over "S4.00"; Leland S8.13 cuts 3/S4.00 and 4/S4.00
    at BH/B7);
  * inline — the number and a slash, then the sheet, on one line beside a
    section triangle ("10/ S4.10" on Leland S2.31-A, RFI 009).

Positions are read in DISPLAY space, because "above" is how the sheet reads;
the box handed back is in the page's stored (unrotated) space like every other
stored box.
"""

from __future__ import annotations

import re

import fitz

import rfi_checks

_DETAIL_REF = re.compile(r"\b(\d+[A-Z]?)\s*/\s*([A-Z]{1,2}-?\d+(?:\.\d+)*[A-Z]?)\b", re.I)
_SYMBOL_NUMBER = re.compile(r"\d{1,3}[A-Z]?")
_INLINE_NUMBER = re.compile(r"^(\d{1,3}[A-Z]?)\s*/$")
_INLINE_WHOLE = re.compile(r"^(\d{1,3}[A-Z]?)/(.+)$")


def detail_ref(source: str | None) -> tuple[str, str] | None:
    """("3", "S4.00") from "detail 3/S4.00", or None. Pure."""
    m = _DETAIL_REF.search(source or "")
    return (m.group(1).upper(), m.group(2).upper()) if m else None


def display_words(page) -> list[tuple]:
    """A page's words with the box in DISPLAY space and the stored (unrotated) box kept."""
    m = page.rotation_matrix
    return [(*tuple(fitz.Rect(w[:4]) * m), w[4], tuple(w[:4])) for w in page.get_text("words")]


def _union(a: tuple, b: tuple) -> tuple:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _stored(w: tuple) -> tuple:
    return w[5] if len(w) > 5 else w[:4]


def section_callouts(words: list[tuple], sheet: str) -> list[dict]:
    """The section/detail symbols on a page that point at `sheet`. `words` are
    (x0, y0, x1, y1, text, unrotated_rect) with the box in DISPLAY space. Pure."""
    want = rfi_checks.normalize(sheet)
    if not want:
        return []
    numbers = [w for w in words if _SYMBOL_NUMBER.fullmatch(w[4].strip())]
    slashed = [w for w in words if _INLINE_NUMBER.match(w[4].strip())]
    out = []
    for s in words:
        text = s[4].strip(".,;:")
        whole = _INLINE_WHOLE.match(text)
        if whole and rfi_checks.normalize(whole.group(2)) == want:
            out.append({"detail": whole.group(1).upper(), "sheet": sheet.upper(), "x": (s[0] + s[2]) / 2,
                        "y": (s[1] + s[3]) / 2, "rect": _stored(s), "form": "inline"})
            continue
        if rfi_checks.normalize(text) != want:
            continue
        cx, height = (s[0] + s[2]) / 2, s[3] - s[1]
        above = [(s[1] - n[3], n) for n in numbers
                 if abs((n[0] + n[2]) / 2 - cx) <= (s[2] - s[0]) / 2 and -0.5 * height <= s[1] - n[3] <= 2 * height]
        if above:
            n = min(above, key=lambda t: t[0])[1]
            out.append({"detail": n[4].strip().upper(), "sheet": sheet.upper(), "x": cx, "y": (n[1] + s[3]) / 2,
                        "rect": _union(_stored(n), _stored(s)), "form": "stacked"})
            continue
        # "10/" then "S4.10" on one line: the number ends within a few points of the sheet.
        cy = (s[1] + s[3]) / 2
        left = [n for n in slashed if abs((n[1] + n[3]) / 2 - cy) <= height / 2 and 0 <= s[0] - n[2] <= height]
        if left:
            n = min(left, key=lambda w: s[0] - w[2])
            out.append({"detail": _INLINE_NUMBER.match(n[4].strip()).group(1).upper(), "sheet": sheet.upper(),
                        "x": (n[0] + s[2]) / 2, "y": cy, "rect": _union(_stored(n), _stored(s)), "form": "inline"})
    return out


_SHEET_WORD = re.compile(r"^[A-Z]{1,2}-?\d{1,3}(?:\.\d{1,3}){0,2}(?:-[A-Z]{1,3})?$")


def any_callouts(words: list[tuple]) -> list[dict]:
    """Every section/detail symbol on a page, whatever sheet it points at. Pure."""
    sheets = {w[4].strip(".,;:") for w in words if _SHEET_WORD.match(w[4].strip(".,;:"))}
    sheets |= {m.group(2) for w in words for m in [_INLINE_WHOLE.match(w[4].strip(".,;:"))]
               if m and _SHEET_WORD.match(m.group(2))}
    return [c for s in sorted(sheets) for c in section_callouts(words, s)]
