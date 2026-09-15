"""Every new application queues its scoring (OBJ-06, BR-301).

  pipeline.application  an AFTER INSERT trigger queues a score_application job in the same
                        transaction, so no application can exist without its scoring queued,
                        whichever code path created it. The worker writes the evaluation.
  core.evaluation       an index for "the first computed score for this candidate and criteria
                        version after the application arrived", which the time report reads.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-16
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION pipeline.queue_scoring() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          INSERT INTO jobs.job (kind, params, requested_by)
          VALUES ('score_application', jsonb_build_object('application_id', NEW.id),
                  NEW.created_by);
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER application_queues_scoring AFTER INSERT ON pipeline.application "
        "FOR EACH ROW EXECUTE FUNCTION pipeline.queue_scoring()"
    )
    op.execute(
        "CREATE INDEX evaluation_computed_by_candidate ON core.evaluation "
        "(candidate_id, criteria_version_id, evaluated_at) WHERE origin = 'computed'"
    )
    op.execute("CREATE INDEX job_queued_by_kind ON jobs.job (kind) WHERE status = 'queued'")


def downgrade() -> None:
    op.execute("DROP INDEX jobs.job_queued_by_kind")
    op.execute("DROP INDEX core.evaluation_computed_by_candidate")
    op.execute("DROP TRIGGER application_queues_scoring ON pipeline.application")
    op.execute("DROP FUNCTION pipeline.queue_scoring()")
