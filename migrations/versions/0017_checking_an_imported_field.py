"""A person may check a field that came from the sheet (week 8: BR-201).

`candidate_field_imported_once` says the importer writes each field once: run the import twice and
the second run adds nothing. It was written for the importer, and it caught a person instead.

Checking a field writes a new row that keeps where the value came from — that is the point, the
value still came from the sheet — and sets verification_status to verified. For a migrated field
that row looked to the index like a second import, so the database refused it, and the API answered
"that already exists" to a recruiter pressing "Right as it is". Nobody could confirm any of the
66,820 migrated fields.

The rule now covers what it was written for: at most one *unchecked* import row per field. A row a
person verified is a person's, not an import's, and the old row stays beside it (BR-205).

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-20
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

IMPORT_SOURCE = "migrated from TAI_Master"


def upgrade() -> None:
    op.execute("DROP INDEX core.candidate_field_imported_once")
    op.execute(
        "CREATE UNIQUE INDEX candidate_field_imported_once ON core.candidate_field "
        f"(candidate_id, field) WHERE source = '{IMPORT_SOURCE}' "
        "AND verification_status IS DISTINCT FROM 'verified'"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0017 is not reversible: a field checked by a person would make the old index unbuildable, "
        "and dropping their check to rebuild it is not something a migration decides."
    )
