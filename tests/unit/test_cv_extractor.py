"""The company OCR service's answer, translated into the shape the platform reads.

The example below is the service's documented shape (its own OpenAPI, 17 September 2026), filled
with a made-up person.
"""

import json

import pytest

from intake.answer import AnswerUnreadable, parse_answer
from intake.cv_extractor import SCHEMA, parse, translate

ANSWER = {
    "name": "Sara Made-Up Hassan",
    "email": "sara.madeup@example.com",
    "phone": "0100 000 0042",
    "address": "New Cairo, Cairo, Egypt",
    "experience": [
        {
            "role": "Sales Representative",
            "company": "Fictional Realty Co",
            "duration": "2023 - present",
        },
        {"role": "Junior Sales Agent", "company": "Invented Properties", "duration": "2021 - 2023"},
    ],
    "education": [
        {"degree": "Bachelor of Commerce", "institution": "Imaginary University", "year": "2021"}
    ],
    "skills": ["negotiation", "CRM"],
    "languages": ["Arabic", "English"],
    "inferred_skills": ["closing"],
    "links": {
        "linkedin": {"url": "https://linkedin.com/in/sara-made-up", "source": "text", "page": 1}
    },
}


def test_the_newest_job_is_the_current_one():
    answer = translate(ANSWER)
    assert answer.fields["current_title"].text == "Sales Representative"
    assert answer.fields["current_employer"].text == "Fictional Realty Co"


def test_what_the_cv_says_is_kept_as_written():
    answer = translate(ANSWER)
    assert answer.fields["full_name"].text == "Sara Made-Up Hassan"
    assert answer.fields["location"].text == "New Cairo, Cairo, Egypt"
    assert answer.fields["phone"].text == "0100 000 0042"
    assert answer.fields["education"].text == "Bachelor of Commerce"
    assert answer.fields["graduation_year"].text == "2021"
    assert answer.fields["profile_url"].text == "https://linkedin.com/in/sara-made-up"


def test_an_arabic_cv_keeps_its_arabic():
    arabic = {**ANSWER, "name": "سارة حسن", "address": "القاهرة الجديدة"}
    answer = translate(arabic)
    assert answer.fields["full_name"].text == "سارة حسن"
    assert answer.fields["full_name"].language == "ar"
    # English job titles beside an Arabic name make a mixed document.
    assert answer.document.language == "mixed"


def test_hidden_content_is_not_reported_rather_than_absent():
    """The service does not look for hidden text, so nobody has said the CV is clean (BR-308)."""
    answer = translate(ANSWER)
    assert answer.hidden_content.found is False
    assert answer.hidden_content.kinds == ["not_reported"]


def test_a_thin_answer_records_only_what_is_there():
    answer = translate({"name": "Only A Name"})
    assert set(answer.fields) == {"full_name"}
    assert answer.answer_schema == SCHEMA


@pytest.mark.parametrize("empty", [{"name": ""}, {"name": None}, {"name": "   "}])
def test_an_empty_value_is_not_a_field(empty):
    assert translate(empty).fields == {}


def test_the_platforms_own_shape_still_parses():
    ours = {
        "schema": "talent-ocr-answer/guess-2026-09-16",
        "document": {"language": "en", "pages": 1},
        "fields": {"full_name": {"text": "Made Up Person", "language": "en"}},
        "hidden_content": {"found": False, "kinds": [], "removed": False},
    }
    assert parse(json.dumps(ours).encode()) is None
    assert parse_answer(json.dumps(ours).encode()).fields["full_name"].text == "Made Up Person"


def test_the_services_shape_is_recognised_by_the_one_entry_point():
    answer = parse_answer(json.dumps(ANSWER).encode())
    assert answer.answer_schema == SCHEMA
    assert answer.fields["current_employer"].text == "Fictional Realty Co"


def test_an_answer_that_is_not_json_says_so():
    with pytest.raises(AnswerUnreadable, match="UTF-8 JSON"):
        parse_answer(b"<html>not json</html>")
