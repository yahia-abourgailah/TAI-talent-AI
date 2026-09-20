"""What the model is allowed to have said (BR-305, BR-307).

An assessment is only evidence if it points at the CV. These hold the answer to that: a reason
whose quote is not in the CV is dropped, and an answer where none of them is, is refused.
"""

import json

import pytest

from assess.answer import AssessmentUnreadable, parse

CV = """Current title: Senior Software Tester
Current employer: TravelYalla
Education: Bachelor of computer science

THE CV AS WRITTEN:
Wrote automated test suites in Python and Selenium. Built CI pipelines in GitHub Actions.
No machine learning experience."""


def answer(**overrides) -> str:
    body = {
        "score": 35,
        "summary": "Python, but no machine learning.",
        "reasons": [
            {"says": "Writes Python", "quote": "Wrote automated test suites in Python"},
            {"says": "Has a CS degree", "quote": "Bachelor of computer science"},
        ],
        "missing": ["production machine learning", "Arabic NLP"],
    }
    return json.dumps({**body, **overrides})


def test_an_answer_that_quotes_the_cv_is_kept_whole():
    found = parse(answer(), CV)
    assert (found.score, found.quotes_checked, found.quotes_found) == (35, 2, 2)
    assert [reason.says for reason in found.reasons] == ["Writes Python", "Has a CS degree"]
    assert found.missing == ("production machine learning", "Arabic NLP")
    assert found.as_signals()[0].startswith("Writes Python — ")
    assert found.as_flags() == [
        "The job asks for: production machine learning",
        "The job asks for: Arabic NLP",
    ]


def test_a_reason_the_cv_does_not_say_is_dropped():
    """The model's commonest failure: a plausible sentence about a document it half-remembers."""
    invented = answer(
        reasons=[
            {"says": "Writes Python", "quote": "Wrote automated test suites in Python"},
            {"says": "Led a team of five", "quote": "Led a team of five engineers"},
        ]
    )
    found = parse(invented, CV)
    assert [reason.says for reason in found.reasons] == ["Writes Python"]
    assert (found.quotes_checked, found.quotes_found) == (2, 1)


def test_an_answer_that_quotes_nothing_real_is_refused():
    with pytest.raises(AssessmentUnreadable, match="a document we did not send"):
        parse(
            answer(reasons=[{"says": "Ten years at Google", "quote": "Google, 2014-2024"}]),
            CV,
        )


def test_spacing_and_case_do_not_decide_whether_a_quote_is_real():
    found = parse(
        answer(reasons=[{"says": "Writes Python", "quote": "wrote   automated TEST suites"}]), CV
    )
    assert found.quotes_found == 1


@pytest.mark.parametrize("score,expected", [(-20, 0), (0, 0), (55.6, 56), (140, 100)])
def test_a_score_outside_the_scale_is_brought_onto_it(score, expected):
    assert parse(answer(score=score), CV).score == expected


@pytest.mark.parametrize("body", ["not json at all", "[1, 2, 3]", '{"summary": "no score"}'])
def test_an_answer_in_the_wrong_shape_is_refused(body):
    with pytest.raises(AssessmentUnreadable):
        parse(body, CV)


def test_an_answer_with_no_reasons_at_all_is_still_an_answer():
    """A CV that genuinely shows nothing the job asks for: a score of 0 and no quotes is honest."""
    found = parse(answer(score=0, reasons=[], missing=["everything the job asks for"]), CV)
    assert (found.score, found.reasons, found.quotes_checked) == (0, (), 0)
