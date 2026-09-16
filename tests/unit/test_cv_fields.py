"""A1: an OCR answer becomes candidate fields (BR-309, NFR-07, BR-201, BR-703). Made-up values."""

import json
from datetime import date

import pytest

from intake.answer import AnswerUnreadable, language_of, parse_answer
from intake.cv_fields import STORED_FIELDS, map_answer, tidy_digits, tidy_phone
from intake.fake_ocr import sample_answer

TODAY = date(2026, 9, 16)


def _mapped(sample: str):
    return map_answer(parse_answer(sample_answer(sample)), TODAY)


def _answer(**fields: str) -> bytes:
    return json.dumps({"fields": {k: {"text": v} for k, v in fields.items()}}).encode()


def _values(mapped) -> dict[str, str | None]:
    return {f.field: f.value for f in mapped.fields}


def test_an_english_answer_becomes_the_expected_fields():
    mapped = _mapped("en")
    fields = mapped.by_name()
    assert [f.field for f in mapped.fields] == list(STORED_FIELDS)
    assert _values(mapped) == {
        "full_name": "Test Candidate Alpha",
        "phone": "+20 100 000 0000",
        "whatsapp": None,
        "email": "test.alpha@example.com",
        "location": "New Cairo, Cairo",
        "current_title": "Sales Representative",
        "current_employer": "Example Trading Co.",
        "education": "Bachelor of Commerce, Example University",
        "graduation_year": "2022",
        "years_experience": "2",
        "age": "25",
        "profile_url": None,
    }
    assert (fields["age"].status, fields["age"].inference) == ("unverified", "stated")
    assert (fields["full_name"].language, fields["whatsapp"].status) == ("en", "not_recorded")
    assert mapped.hidden_content is False
    assert mapped.unreadable == []


def test_an_arabic_answer_keeps_the_text_and_reads_arabic_digits_as_numbers():
    mapped = _mapped("ar")
    fields = mapped.by_name()
    # The name exactly as written, the double space included: never translated or tidied.
    assert fields["full_name"].value == "مرشح  تجريبي الأول"
    assert fields["full_name"].language == "ar"
    # A phone keeps its own digits; tidying is for matching only.
    assert fields["phone"].value == "٠١٠٠ ٠٠٠ ٠٠٠٠"
    assert tidy_phone(fields["phone"].value) == "1000000000"
    assert (fields["graduation_year"].value, fields["years_experience"].value) == ("2023", "1")
    # No stated age: worked out from the graduation year with the criteria's own rule (OPN-02).
    assert (fields["age"].value, fields["age"].inference) == ("25", "inferred")
    for missing in ("email", "current_employer", "profile_url", "whatsapp"):
        assert (fields[missing].value, fields[missing].status) == (None, "not_recorded")


def test_a_mixed_answer_is_read_without_guessing():
    mapped = _mapped("mixed")
    fields = mapped.by_name()
    assert fields["full_name"].value == "مرشح Test الثاني"
    assert fields["full_name"].language == "mixed"
    assert fields["current_employer"].language == "ar"
    # "3+ years" is not a plain number: not recorded, and counted, never guessed as 3.
    assert fields["years_experience"].status == "not_recorded"
    assert "years_experience" in mapped.unreadable
    # An age of 0 is no age; the date of birth gives it instead, marked inferred.
    assert (fields["age"].value, fields["age"].inference) == ("27", "inferred")
    assert fields["profile_url"].value == "https://www.linkedin.com/in/example-test-profile"
    assert mapped.ignored_fields == 1  # "hobbies" is not a form field
    assert "date_of_birth" not in _values(mapped)


def test_hidden_content_is_reported_and_the_cleaned_text_is_still_mapped():
    mapped = _mapped("hidden")
    assert mapped.hidden_content is True
    assert mapped.by_name()["full_name"].value == "Test Candidate Gamma"


@pytest.mark.parametrize("age", ["0", "٠", " 0 "])
def test_an_age_of_zero_is_no_age(age):
    fields = map_answer(parse_answer(_answer(age=age)), TODAY).by_name()
    assert (fields["age"].value, fields["age"].status) == (None, "not_recorded")


@pytest.mark.parametrize(
    ("given", "expected", "unreadable"),
    [
        ("١٢", "12", []),
        ("3.5", "3.5", []),
        ("three", None, ["years_experience"]),
        ("99", None, ["years_experience"]),
        ("", None, []),
    ],
)
def test_years_of_experience_are_read_as_plain_numbers_only(given, expected, unreadable):
    mapped = map_answer(parse_answer(_answer(years_experience=given)), TODAY)
    assert mapped.by_name()["years_experience"].value == expected
    assert mapped.unreadable == unreadable


def test_a_stated_age_wins_over_a_date_of_birth_and_a_graduation_year():
    answer = _answer(age="30", date_of_birth="2000-01-01", graduation_year="2020")
    fields = map_answer(parse_answer(answer), TODAY).by_name()
    assert (fields["age"].value, fields["age"].inference) == ("30", "stated")


def test_a_date_of_birth_is_read_day_first_and_an_unclear_one_is_not_guessed():
    born = map_answer(parse_answer(_answer(date_of_birth="17/09/2000")), TODAY).by_name()
    assert born["age"].value == "25"  # turns 26 tomorrow
    unclear = map_answer(parse_answer(_answer(date_of_birth="Sept 2000")), TODAY)
    assert unclear.by_name()["age"].value is None
    assert unclear.unreadable == ["date_of_birth"]


def test_a_postgraduate_graduation_year_gives_no_age():
    answer = _answer(graduation_year="2024", education="Master of Business Administration")
    assert map_answer(parse_answer(answer), TODAY).by_name()["age"].value is None


def test_blank_text_is_not_recorded():
    fields = map_answer(parse_answer(_answer(full_name="   ")), TODAY).by_name()
    assert (fields["full_name"].value, fields["full_name"].status) == (None, "not_recorded")


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2]",
        b'{"fields": {"full_name": "Fake Person Name"}}',
        "\xff".encode("latin-1"),
    ],
)
def test_an_answer_in_the_wrong_shape_is_refused_without_quoting_it(body):
    with pytest.raises(AnswerUnreadable) as caught:
        parse_answer(body)
    assert "Fake Person" not in str(caught.value)


def test_languages_and_digits():
    assert (language_of("محمد"), language_of("Mona"), language_of("محمد Ali")) == (
        "ar",
        "en",
        "mixed",
    )
    assert language_of("+20 100") is None
    assert tidy_digits("٢٠٢٣ ۱۲") == "2023 12"
    assert tidy_phone("0020 100-000-0001") == tidy_phone("+20 1000000001") == "1000000001"
