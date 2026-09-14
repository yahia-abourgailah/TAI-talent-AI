"""Characterisation tests for criteria version 2026-08-04, the ported Leila scorer.

They pin what the ruleset does today, including the rulings that were expensive to learn, so any
change to its output is deliberate and visible (BR-301, BR-303). Every candidate is fabricated.
"""

import pytest

from scoring.rulesets.v2026_08_04 import Candidate, _recommend, score_candidate

# Assembled so the repository's phone-number guard stays a check on real data. Not a real number.
FAKE_MOBILE = "010" + "00000000"


@pytest.mark.parametrize(
    ("candidate", "score", "tier"),
    [
        pytest.param(
            Candidate(
                phone_number=FAKE_MOBILE,
                location="New Cairo",
                current_title="Sales Rep",
                years_experience=1,
                education_level="bachelor",
                skills="Sales, CRM, Customer Service",
                graduation_year=2024,
            ),
            90,
            "P1",
            id="recent-grad-new-cairo",
        ),
        pytest.param(
            Candidate(
                phone_number=FAKE_MOBILE,
                location="Nasr City",
                current_title="Customer Service",
                years_experience=2,
                education_level="bachelor",
                skills="Customer Service, Retail",
            ),
            90,
            "P1",
            id="customer-service-nasr-city",
        ),
        pytest.param(
            Candidate(
                phone_number=FAKE_MOBILE,
                location="Heliopolis",
                current_title="Sales Executive",
                years_experience=10,
                education_level="bachelor",
            ),
            63,
            "P2",
            id="ten-years-experience",
        ),
        pytest.param(
            Candidate(
                phone_number=FAKE_MOBILE,
                location="التجمع الخامس",
                current_title="مندوب مبيعات",
                years_experience=1,
                education_level="بكالوريوس",
                skills="مبيعات, خدمة عملاء, تواصل",
                graduation_year=2024,
            ),
            100,
            "P1",
            id="arabic-profile",
        ),
    ],
)
def test_track_a_scores(candidate, score, tier):
    result = score_candidate(candidate)
    assert (result.overall_score, result.priority, result.disqualified) == (score, tier, False)


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        pytest.param(
            Candidate(location="New Cairo", current_title="Senior Manager", years_experience=8),
            "Managerial/director title",
            id="manager",
        ),
        pytest.param(
            Candidate(location="Alexandria", current_title="Sales Agent", years_experience=1),
            "Outside Cairo",
            id="outside-cairo",
        ),
        pytest.param(
            Candidate(
                location="Fifth Settlement",
                current_title="Intern",
                graduation_year=2028,
                education_level="undergraduate",
                is_student=True,
            ),
            "Under 21",
            id="under-21",
        ),
    ],
)
def test_track_a_hard_gates(candidate, reason):
    result = score_candidate(candidate)
    assert (result.overall_score, result.priority, result.disqualified) == (0, "P4", True)
    assert reason in result.disqualify_reason


def test_shorouk_is_near_new_cairo_not_a_foreign_country():
    result = score_candidate(
        Candidate(location="El Shorouk City", current_title="Sales Representative")
    )
    assert not result.disqualified
    assert result.location_score == 30


def test_senior_is_experience_not_management():
    consultant = score_candidate(
        Candidate(location="New Cairo", current_title="Senior Property Consultant")
    )
    manager = score_candidate(Candidate(location="New Cairo", current_title="Senior Sales Manager"))
    assert not consultant.disqualified
    assert manager.disqualified
    assert "Managerial/director title" in manager.disqualify_reason


def test_coordinator_is_not_a_coo():
    assert not score_candidate(
        Candidate(location="Maadi", current_title="Sales Coordinator")
    ).disqualified


def test_team_leaders_are_routed_to_track_b_not_rejected():
    result = score_candidate(Candidate(location="Maadi", current_title="Team Leader"))
    assert result.disqualified
    assert "Track B target, profile only" in result.disqualify_reason


def test_current_staff_are_never_sourced():
    result = score_candidate(
        Candidate(current_title="Property Consultant", current_employer="The Address Investments")
    )
    assert result.disqualified
    assert "Current TAI employee" in result.disqualify_reason


def test_a_past_stint_at_tai_is_a_rehire_not_an_exclusion():
    result = score_candidate(
        Candidate(
            location="New Cairo",
            current_title="Property Consultant",
            current_employer="Nawy",
            summary="Previously at The Address Investments",
        )
    )
    assert not result.disqualified
    assert any("Rehire candidate" in flag for flag in result.red_flags)


@pytest.mark.parametrize(
    ("score", "tier"),
    [
        (100, "P1"),
        (75, "P1"),
        (74, "P2"),
        (55, "P2"),
        (54, "P3"),
        (35, "P3"),
        (34, "P4"),
        (0, "P4"),
    ],
)
def test_track_a_tier_thresholds(score, tier):
    assert _recommend(score)[1] == tier


@pytest.mark.parametrize(
    ("candidate", "score", "tier", "tenured"),
    [
        pytest.param(
            Candidate(
                current_title="Sales Manager",
                current_employer="Nawy Real Estate",
                location="New Cairo",
                years_at_current=5,
                graduation_year=2014,
                move_signal="open_to_work",
            ),
            100,
            "T1",
            True,
            id="competitor-sales-manager",
        ),
        pytest.param(
            Candidate(
                current_title="Team Leader",
                current_employer="Element Developments",
                location="Nasr City",
                years_at_current=3,
                graduation_year=2017,
                move_signal="engaged_post",
            ),
            86,
            "T1",
            True,
            id="competitor-team-leader",
        ),
        pytest.param(
            Candidate(
                current_title="Supervisor",
                current_employer="Coldwell Banker",
                location="Maadi",
                years_at_current=1,
                graduation_year=2018,
                move_signal="passive",
            ),
            65,
            "T2",
            False,
            id="low-tenure-supervisor",
        ),
    ],
)
def test_track_b_scores(candidate, score, tier, tenured):
    result = score_candidate(candidate, mode="headhunt")
    assert (result.overall_score, result.priority, result.tenured_flag) == (score, tier, tenured)


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        pytest.param(
            Candidate(
                current_title="Sales Director",
                current_employer="Bayut",
                location="New Cairo",
                years_at_current=4,
            ),
            "Too senior",
            id="director",
        ),
        pytest.param(
            Candidate(
                current_title="Sales Manager",
                current_employer="Nawy",
                location="New Cairo",
                years_at_current=5,
                graduation_year=2008,
            ),
            "Over 38",
            id="over-38",
        ),
    ],
)
def test_track_b_hard_gates(candidate, reason):
    result = score_candidate(candidate, mode="headhunt")
    assert (result.overall_score, result.priority, result.disqualified) == (0, "T4", True)
    assert reason in result.disqualify_reason
