"""An application to a job that is not sales is assessed, not scored (week 8: BR-305).

Every application queues work the moment it exists (migration 0008). Which work depends on what
kind of job it is: a sales job is scored by the criteria version, and anything else is read against
its own description. The decision belongs here, beside the insert, so no code path can create an
application that quietly gets the wrong one.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-20
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pipeline.queue_scoring() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          kind_of_job text;
        BEGIN
          SELECT o.job_type INTO kind_of_job FROM pipeline.opening o WHERE o.id = NEW.opening_id;
          INSERT INTO jobs.job (kind, params, requested_by)
          VALUES (
            CASE WHEN kind_of_job = 'other' THEN 'assess_application' ELSE 'score_application' END,
            jsonb_build_object('application_id', NEW.id),
            NEW.created_by
          );
          RETURN NULL;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pipeline.queue_scoring() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          INSERT INTO jobs.job (kind, params, requested_by)
          VALUES ('score_application', jsonb_build_object('application_id', NEW.id),
                  NEW.created_by);
          RETURN NULL;
        END
        $$
        """
    )
