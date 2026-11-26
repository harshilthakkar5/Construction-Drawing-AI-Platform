"""Settings → Project scope in the review: an RFI about work the contractor does not build is never raised."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import rfi_review  # noqa: E402
from generated import RFI_SCOPE_TOPICS  # noqa: E402


def test_a_problem_about_rebar_is_out_of_scope_and_one_that_only_mentions_it_is_not():
    out = ["rebar"]
    about = lambda element, issue: rfi_review.out_of_scope_topic({"element": element, "issue": issue}, out)  # noqa: E731
    # The client's case: a beam schedule with no bars.
    assert about("Beam B1", "The beam schedule gives no bottom bars or stirrups for B1.") == "Rebar detailing"
    assert about("Grade beam GB-2", "Top and bottom reinforcement is not specified.") == "Rebar detailing"
    # In scope: the size and place of reinforced concrete, and a dowel named after the first sentence.
    assert about("Reinforced concrete wall", "Wall thickness is not given.") is None
    assert about("Retaining wall T.O.C.", "T.O.C. is SEE PLAN and the plan gives none. Dowels per schedule.") is None
    assert about("Column C-30", "Column C-30 is not located to the grid.") is None
    # Nothing held out of scope: nothing is.
    assert rfi_review.out_of_scope_topic({"element": "Beam B1", "issue": "No bottom bars."}, []) is None


def test_the_prompts_name_the_scope_only_when_a_project_sets_one():
    assert "<out_of_scope>" in rfi_review.reasoning_system(["C01"], None, ["rebar"])
    assert "<out_of_scope>" in rfi_review.verification_system(["rebar"])
    assert "<out_of_scope>" not in rfi_review.reasoning_system(["C01"]) + rfi_review.verification_system()
    assert "Rebar detailing" in rfi_review.out_of_scope_block(["rebar"])


def test_the_topics_are_the_shared_ones():
    assert [t["id"] for t in RFI_SCOPE_TOPICS] == ["rebar"]
    assert RFI_SCOPE_TOPICS[0]["categories"] == ["Reinforcement"]
