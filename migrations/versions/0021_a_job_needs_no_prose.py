"""A job that is not sales no longer has to be written out in prose.

`description` was added in 0018 for one reason: the candidate's CV was read against those words by
a language model. Since 0020 the match is done against `requirements` — the skills the job asks
for and the level wanted of each — and nothing reads the prose at all: not the matcher, not the
careers page, not a report.

So it stops being required. The column stays, nullable: what is already written is still readable,
a requisition may still carry a note, and anything that reads the field keeps working (the /v1
contract is frozen). Nothing is asked for that nothing uses.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-20
"""

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE pipeline.opening DROP CONSTRAINT opening_other_jobs_say_what_they_ask_for"
    )


def downgrade() -> None:
    # Putting it back would refuse every opening created without prose while it was gone.
    raise RuntimeError("0021 is not reversible.")
