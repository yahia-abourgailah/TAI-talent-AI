"""What the duplicate matcher matches on, without a database. Made-up people only."""

import pytest

from candidates.duplicates import (
    MOST_PER_KEY,
    STRONG,
    check_report,
    email_key,
    find_pairs,
    groups,
    keys_of,
    name_keys,
    phone_key,
    profile_key,
)

# Fabricated numbers that belong to no one.
MOBILE = "010" + "00000021"


@pytest.mark.parametrize(
    "written",
    [MOBILE, "+20 100 000 0021", "0020 100 000 0021", "010-0000-0021", "٠١٠٠٠٠٠٠٠٢١"],
)
def test_one_number_written_many_ways_is_one_key(written):
    assert phone_key(written) == phone_key(MOBILE)


def test_a_number_too_short_to_be_a_mobile_is_no_key():
    assert phone_key("12345") is None


def test_emails_and_profiles_are_matched_on_what_they_point_at():
    assert email_key("  Made.Up@Example.COM ") == "made.up@example.com"
    assert email_key("not-an-email") is None
    assert profile_key("https://www.linkedin.com/in/made-up-person/?trk=x") == profile_key(
        "linkedin.com/in/made-up-person"
    )


ARABIC = "محمد علي"  # Mohamed Ali
ARABIC_WITH_MARKS = "مُحَمَّد على"


def test_the_same_arabic_name_written_differently_is_one_key():
    assert name_keys(ARABIC)["name_arabic"] == name_keys(ARABIC_WITH_MARKS)["name_arabic"]
    # ال before a word, and taa marbuta against haa, are brought together.
    assert name_keys("السيد مصطفى")["name_arabic"] == name_keys("سيد مصطفى")["name_arabic"]
    assert name_keys("فاطمة علي")["name_arabic"] == name_keys("فاطمه علي")["name_arabic"]


@pytest.mark.parametrize("latin", ["Mohamed Ali", "Mohammed Aly", "Muhammad Ali"])
def test_an_arabic_name_and_its_english_spellings_meet(latin):
    assert name_keys(latin)["name_latin"] == name_keys(ARABIC)["name_latin"]


def test_a_single_word_is_never_a_name_key():
    assert name_keys("Mohamed") == {}
    assert keys_of("full_name", "Mohamed") == {}


def test_a_shared_number_is_a_match_and_a_shared_name_alone_is_not():
    identities = [
        (1, "phone", MOBILE),
        (2, "whatsapp", "+20 100 000 0021"),
        (3, "full_name", "Made Up Person"),
        (4, "full_name", "Made-up  PERSON"),
    ]
    found = find_pairs(identities)
    by_pair = {(pair.lower_id, pair.higher_id): pair for pair in found.pairs}
    assert by_pair[(1, 2)].strength == STRONG
    assert by_pair[(1, 2)].evidence == ("phone",)
    # Two people called the same thing are not one person, and nobody is asked about them.
    assert (3, 4) not in by_pair
    assert found.name_only == 1
    assert found.too_common == {}


def test_a_name_is_kept_as_detail_on_a_pair_that_matched_on_something_real():
    identities = [
        (1, "phone", MOBILE),
        (1, "full_name", "Made Up Person"),
        (2, "whatsapp", "+20 100 000 0021"),
        (2, "full_name", "Made-up  PERSON"),
    ]
    (pair,) = find_pairs(identities).pairs
    assert pair.strength == STRONG
    assert set(pair.evidence) == {"phone", "name_arabic", "name_latin"}


def test_a_key_shared_by_too_many_records_is_left_out():
    shared = [(number, "email", "made.up@example.com") for number in range(MOST_PER_KEY + 2)]
    found = find_pairs(shared)
    assert found.pairs == []
    assert found.too_common["email"] == 1


def test_records_matched_to_each_other_become_one_group():
    identities = [
        (1, "phone", MOBILE),
        (2, "phone", MOBILE),
        (2, "email", "made.up@example.com"),
        (3, "email", "made.up@example.com"),
        (7, "email", "someone.else@example.com"),
        (8, "email", "someone.else@example.com"),
    ]
    assert groups(find_pairs(identities).pairs) == [[1, 2, 3], [7, 8]]


def test_the_hand_check_reports_the_wrong_join_rate():
    rows = [
        {"candidate_a": "1", "candidate_b": "2", "strength": STRONG, "decision": "same"},
        {"candidate_a": "3", "candidate_b": "4", "strength": STRONG, "decision": "different"},
        {"candidate_a": "5", "candidate_b": "6", "strength": STRONG, "decision": "same"},
        {"candidate_a": "7", "candidate_b": "8", "strength": STRONG, "decision": "unclear"},
        {"candidate_a": "9", "candidate_b": "10", "strength": STRONG, "decision": ""},
    ]
    report = check_report(rows)
    assert (report["checked"], report["wrong"]) == (4, 1)
    assert report["wrong_join_rate"] == 0.25
    assert report["unclear"] == 1
    assert report["wrong_pairs"] == [("3", "4")]


def test_a_cell_holding_a_number_and_a_link_is_read_as_both():
    """Some rows arrived with two things in the contact column, split by a bar."""
    keys = keys_of("phone", f"+2{MOBILE}  |  https://www.linkedin.com/in/made-up-person")
    assert keys["phone"] == [phone_key(MOBILE)]
    assert keys["profile_url"] == [profile_key("linkedin.com/in/made-up-person")]


def test_the_digits_inside_a_link_are_never_taken_for_a_number():
    keys = keys_of("phone", "https://wuzzuf.net/talent/5411569 | linkedin.com/in/b24646144")
    assert "phone" not in keys
    assert len(keys["profile_url"]) == 2


def test_two_numbers_in_one_cell_are_two_keys():
    other = "0111" + "0000021"
    keys = keys_of("whatsapp", f"{MOBILE}, {other}")
    assert keys["phone"] == [phone_key(MOBILE), phone_key(other)]
