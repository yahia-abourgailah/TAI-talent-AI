"""A CV is read once by each reader, not once forever (week 8: BR-102, BR-106).

intake.cv_reading held one row per file. That was right while one reader existed, and wrong the
day it changed: the stand-in's answer stayed the file's answer, so a candidate re-uploading the CV
they sent last week was shown what the stand-in had invented, under their own name.

Now a file has one reading per reader, and the newest is the one in force. Nothing is edited or
removed: the stand-in's answer stays exactly where it is, beside the real one, which is how we can
still say what a candidate was shown and when (BR-201, BR-205).

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-20
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE intake.cv_reading DROP CONSTRAINT cv_reading_capture_id_key")
    op.execute(
        "ALTER TABLE intake.cv_reading "
        "ADD CONSTRAINT cv_reading_once_per_reader UNIQUE (capture_id, reader)"
    )
    # Every question about a file asks for its newest reading, so the index is built for that.
    op.execute(
        "CREATE INDEX cv_reading_newest_per_capture ON intake.cv_reading (capture_id, id DESC)"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0016 is not reversible: a file may now hold a reading from each reader, and going back "
        "would mean choosing which answer to throw away."
    )
