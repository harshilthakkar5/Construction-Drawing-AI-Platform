"""A "By element" review: library questions asked in the client's words, and
the code checks those questions (and the catalogue questions) link to."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import rfi_review as rr  # noqa: E402

SCOPE = {
    "library": {
        "element": {"id": "CE-03-01", "name": "Drilled piers", "variants": "Straight shaft, belled",
                    "attributes": "Diameter, bell size, tip elevation", "triggers": "Pier mark not in schedule"},
        "questions": [
            {"id": "CE-03-01-Q01", "text": "Does every pier mark on the plan appear in the pier schedule?",
             "category": "Documentation / Missing Info", "source": "library", "codeChecks": ["unscheduled_mark", "unmarked_element"]},
            {"id": "c-1", "text": "Does every pier mark … ? → Which mark is the pier at A1/BK?", "category": None,
             "source": "custom", "codeChecks": []},
        ],
    }
}


def test_library_questions_become_review_questions_in_their_own_words():
    lib = rr.library_checks(SCOPE)
    assert list(lib) == ["CE-03-01-Q01", "c-1"]
    q = lib["CE-03-01-Q01"]
    assert q["originalQuestion"] == "Does every pier mark on the plan appear in the pier schedule?"
    assert q["requiredObservations"] == ["Diameter", "bell size", "tip elevation"]
    assert "Pier mark not in schedule" in q["candidateRules"]
    assert lib["c-1"]["source"] == "custom"
    assert rr.library_checks({}) == {}


def test_prompts_carry_catalogue_and_library_questions():
    lib = rr.library_checks(SCOPE)
    catalogue = {**rr.CHECKS, **lib}
    system = rr.discovery_system(["F04", "CE-03-01-Q01", "c-1"], catalogue)
    assert "F04" in system and "Which mark is the pier at A1/BK?" in system
    assert "Pier mark not in schedule" in rr.reasoning_system(["CE-03-01-Q01"], catalogue)
    # The default catalogue is untouched for every other caller.
    assert "CE-03-01-Q01" not in rr.discovery_system(["F04"])


def test_code_checks_credit_every_question_they_answer():
    lib = rr.library_checks(SCOPE)
    linked = rr.code_checks_for(["F04", "G01", "CE-03-01-Q01", "c-1"], lib)
    assert linked["unmarked_element"] == ["F04", "CE-03-01-Q01"]
    assert linked["unscheduled_mark"] == ["CE-03-01-Q01"]
    assert "grid_mismatch" in linked and linked["grid_mismatch"] == ["G01"]
    assert rr.code_checks_for(["c-1"], lib) == {}


def test_outcomes_cover_library_questions():
    lib = rr.library_checks(SCOPE)
    asked = ["F04", *lib]
    results = rr.check_results(list(rr.CHECKS) + list(lib), asked, {}, [], [], [], {"CE-03-01-Q01": 1})
    assert results["CE-03-01-Q01"]["outcome"] == "candidate_found"
    assert results["c-1"]["outcome"] == "insufficient_evidence"
    assert results["G02"]["outcome"] == "not_selected"
