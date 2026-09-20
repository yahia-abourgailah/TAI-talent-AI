"""What we send the CV service, and what we will accept back (BR-305, CR-05).

A percentage is a number somebody will act on, so it is only recorded when the lines behind it
are there too. Nothing here calls the service.
"""

import json

import pytest

from assess.match import LEVELS, MatchUnreadable, parse, requirements_payload

ASKED = [
    {"skill": "Python", "level": "advanced"},
    {"skill": "Arabic", "level": "intermediate", "category": "Language"},
]


def _answer(**report) -> bytes:
    body = {
        "cv": {"name": "Made Up Person"},
        "match_report": {"job_title": "A Job", "overall_match_percentage": 50.0, **report},
        "metadata": {"extraction_method": "pdf_parser", "requirements_hash": "abc123"},
    }
    return json.dumps(body).encode()


def test_the_payload_is_the_job_s_own_list_grouped_by_category():
    sent = json.loads(requirements_payload("  ML Engineer  ", ASKED))
    assert sent["job_title"] == "ML Engineer"
    assert [group["name"] for group in sent["skill_types"]] == ["Technical", "Language"]
    assert sent["skill_types"][0]["skills"] == [{"name": "Python", "required_level": "advanced"}]
    # The four levels the service grades against are sent with every group: a level it does not
    # know would give a percentage that means nothing.
    assert all(group["levels"] == list(LEVELS) for group in sent["skill_types"])


def test_a_job_with_no_title_is_still_asked_about():
    assert json.loads(requirements_payload("   ", ASKED))["job_title"] == "the role"


def test_nothing_to_ask_is_refused_before_a_cv_leaves_the_building():
    with pytest.raises(MatchUnreadable):
        requirements_payload("A Job", [])


def test_a_report_becomes_lines_a_person_can_read():
    found = parse(
        _answer(
            matched=[
                {
                    "category": "Technical",
                    "skill": "Python",
                    "required_level": "advanced",
                    "detected_level": "expert",
                    "evidence": "Ten years of Python",
                    "status": "matched",
                }
            ],
            below=[
                {
                    "category": "Technical",
                    "skill": "Docker",
                    "required_level": "advanced",
                    "detected_level": "beginner",
                    "evidence": "Used Docker once",
                }
            ],
            missing=[
                {"category": "Technical", "skill": "Kubernetes", "required_level": "intermediate"}
            ],
        )
    )
    assert found.percentage == 50.0
    assert found.method == "pdf_parser"
    assert found.requirements_hash == "abc123"
    assert [line.as_text() for line in found.met] == [
        "Python: expert, advanced asked for — “Ten years of Python”"
    ]
    assert [line.as_text() for line in found.unmet] == [
        "Docker: beginner, below the advanced asked for — “Used Docker once”",
        "Kubernetes: not in the CV, intermediate asked for",
    ]


@pytest.mark.parametrize(
    "body",
    [
        b"not json at all",
        json.dumps({"cv": {}}).encode(),
        json.dumps({"match_report": {"overall_match_percentage": 50}}).encode(),
        json.dumps({"match_report": {"overall_match_percentage": 50, "matched": []}}).encode(),
    ],
)
def test_an_answer_we_cannot_tie_to_skills_is_refused(body):
    """A percentage alone is a number with nothing behind it. It is not recorded."""
    with pytest.raises(MatchUnreadable):
        parse(body)


@pytest.mark.parametrize("percentage", [-1, 101, "half", None])
def test_a_percentage_that_is_not_one_is_refused(percentage):
    with pytest.raises(MatchUnreadable):
        parse(
            _answer(
                matched=[{"skill": "Python", "required_level": "advanced"}],
                overall_match_percentage=percentage,
            )
        )


def test_a_line_without_a_skill_or_a_level_is_dropped_not_guessed():
    found = parse(
        _answer(
            matched=[
                {"skill": "Python", "required_level": "advanced"},
                {"skill": "", "required_level": "advanced"},
                {"skill": "Docker"},
                "not even an object",
            ]
        )
    )
    assert [line.skill for line in found.lines] == ["Python"]
