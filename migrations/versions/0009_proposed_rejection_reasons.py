"""Puts the proposed rejection reasons in force (BR-404): list proposed-2026-09-15.

  pipeline.step_list, step, allowed_move, rejection_reason, list_activation
                        the list in docs/pipeline/lists/proposed-2026-09-15.json as it was on 15
                        September 2026, copied here so a later edit to that file never changes
                        what this migration did. The steps and moves are the BRD ones from 0005;
                        the 19 reasons replace its placeholders. Marked provisional until TA
                        confirms. Nothing is loaded if the list already exists, and it is activated
                        only if it is not already in force. TA's later changes arrive as a new list
                        through python -m pipeline.lists load.

Scoring on arrival (0008) needs these reasons: without them a disqualification opens no review item.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-16
"""

from alembic import op
from sqlalchemy import text

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

VERSION = "proposed-2026-09-15"
PROVISIONAL = True
SOURCE = (
    "Rejection reasons proposed 15 Sep 2026 for TA review (BR-404). Steps and moves "
    "unchanged from provisional-brd-2026-09."
)
PLACEHOLDER_LIST = "provisional-brd-2026-09"
LOADED_BY = "migration 0009"

# (code, label, outcome), in order.
STEPS = (
    ("new", "New", None),
    ("contacted", "Contacted", None),
    ("replied", "Replied", None),
    ("phone_screen", "Phone screen", None),
    ("hr_interview", "HR interview", None),
    ("aptitude_test", "Test", None),
    ("technical_interview", "Technical interview", None),
    ("offer", "Offer", None),
    ("hired", "Hired", "hired"),
    ("rejected", "Rejected", "rejected"),
)

MOVES = (
    ("new", "contacted"),
    ("contacted", "replied"),
    ("replied", "phone_screen"),
    ("phone_screen", "hr_interview"),
    ("hr_interview", "aptitude_test"),
    ("aptitude_test", "technical_interview"),
    ("technical_interview", "offer"),
    ("offer", "hired"),
    ("new", "rejected"),
    ("contacted", "rejected"),
    ("replied", "rejected"),
    ("phone_screen", "rejected"),
    ("hr_interview", "rejected"),
    ("aptitude_test", "rejected"),
    ("technical_interview", "rejected"),
    ("offer", "rejected"),
)

# (code, label)
REASONS = (
    ("outside_hiring_area", "Lives outside the hiring area"),
    ("age_outside_range", "Age outside the range for the role"),
    ("experience_not_a_fit", "Experience does not fit the role (too little or too senior)"),
    ("current_employee", "Currently works at The Address"),
    ("not_eligible_for_rehire", "Former employee, not eligible for rehire (HR confirmed)"),
    ("communication_below_need", "Communication or language below what the role needs"),
    ("salary_expectation_above_range", "Salary expectation above the role's range"),
    ("did_not_pass_test", "Did not pass the test"),
    ("not_suitable_after_interview", "Not suitable after interview"),
    ("checks_not_passed", "Documents or references did not check out"),
    ("opening_filled", "Opening filled or closed"),
    ("not_reachable", "Could not be reached (wrong, missing or switched-off number)"),
    ("no_response", "Stopped responding"),
    ("not_interested", "Not interested in the role"),
    ("no_show", "Did not attend the interview or test"),
    ("commute_or_hours", "Location, commute or working hours do not suit the candidate"),
    ("accepted_other_offer", "Accepted another offer"),
    ("declined_offer", "Declined our offer"),
    ("withdrew_other", "Withdrew for another reason"),
)


def upgrade() -> None:
    bind = op.get_bind()
    exists = bind.execute(
        text("SELECT 1 FROM pipeline.step_list WHERE version = :v"), {"v": VERSION}
    ).first()
    if not exists:
        bind.execute(
            text(
                "INSERT INTO pipeline.step_list (version, provisional, source, loaded_by) "
                "VALUES (:v, :provisional, :source, :by)"
            ),
            {"v": VERSION, "provisional": PROVISIONAL, "source": SOURCE, "by": LOADED_BY},
        )
        for position, (code, label, outcome) in enumerate(STEPS, start=1):
            bind.execute(
                text(
                    "INSERT INTO pipeline.step (list_version, code, label, position, outcome) "
                    "VALUES (:v, :code, :label, :position, :outcome)"
                ),
                {
                    "v": VERSION,
                    "code": code,
                    "label": label,
                    "position": position,
                    "outcome": outcome,
                },
            )
        for from_step, to_step in MOVES:
            bind.execute(
                text(
                    "INSERT INTO pipeline.allowed_move (list_version, from_step, to_step) "
                    "VALUES (:v, :from_step, :to_step)"
                ),
                {"v": VERSION, "from_step": from_step, "to_step": to_step},
            )
        for code, label in REASONS:
            bind.execute(
                text(
                    "INSERT INTO pipeline.rejection_reason (list_version, code, label) "
                    "VALUES (:v, :code, :label)"
                ),
                {"v": VERSION, "code": code, "label": label},
            )
    if bind.execute(text("SELECT pipeline.active_list()")).scalar_one() != VERSION:
        bind.execute(
            text(
                "INSERT INTO pipeline.list_activation (list_version, activated_by) VALUES (:v, :by)"
            ),
            {"v": VERSION, "by": LOADED_BY},
        )


def downgrade() -> None:
    # Lists are append-only: going back puts the BRD placeholder list in force again.
    op.get_bind().execute(
        text("INSERT INTO pipeline.list_activation (list_version, activated_by) VALUES (:v, :by)"),
        {"v": PLACEHOLDER_LIST, "by": "migration 0009 downgrade"},
    )
