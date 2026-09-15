"""After the week 2 review: history instead of overwrites, and runs that cannot vanish.

  core.candidate_field  append-only for every role. A correction or a verification is a new row;
                        core.candidate_field_current shows the latest row per field. Only the
                        TAI_Master import keeps one row per candidate and field. recorded_by
                        added. verified_at and verified_by only on a verified row.
  core.candidate        the app may update the three archive columns and nothing else, and no
                        role may change what a candidate was created from. pipeline_state has no
                        default: every insert says what it knows. Archive reason and actor must
                        contain more than whitespace.
  core.evaluation       the original Signals and Flags text is kept beside the split lists. Only
                        a stored evaluation is unique per candidate and criteria version; computed
                        ones may repeat under new model or prompt versions (BR-303).
  jobs.job              attempts counted. Kind, params and requester never change, a finished job
                        is final, and no job is deleted. A running job may go back to queued when
                        its worker stopped (NFR-04).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-15
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _drop_unique(table: str, definition: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE found text;
        BEGIN
          SELECT conname INTO found FROM pg_constraint
          WHERE conrelid = '{table}'::regclass AND contype = 'u'
            AND pg_get_constraintdef(oid) = '{definition}';
          IF found IS NOT NULL THEN
            EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', found);
          END IF;
        END $$;
        """
    )


def upgrade() -> None:
    # --- core.candidate_field: history, never overwrites --------------------------------------
    _drop_unique("core.candidate_field", "UNIQUE (candidate_id, field)")
    op.execute(
        "CREATE UNIQUE INDEX candidate_field_imported_once ON core.candidate_field "
        "(candidate_id, field) WHERE source = 'migrated from TAI_Master'"
    )
    op.execute("ALTER TABLE core.candidate_field ADD COLUMN recorded_by text")
    op.execute(
        """
        ALTER TABLE core.candidate_field ADD CONSTRAINT candidate_field_verified_only CHECK (
          verification_status = 'verified' OR (verified_at IS NULL AND verified_by IS NULL)
        )
        """
    )
    op.execute("REVOKE UPDATE ON core.candidate_field FROM talent_rw")
    op.execute(
        "CREATE TRIGGER candidate_field_append_only "
        "BEFORE UPDATE OR DELETE ON core.candidate_field "
        "FOR EACH ROW EXECUTE FUNCTION core.refuse_change()"
    )
    op.execute(
        "CREATE TRIGGER candidate_field_no_truncate BEFORE TRUNCATE ON core.candidate_field "
        "FOR EACH STATEMENT EXECUTE FUNCTION core.refuse_change()"
    )
    op.execute(
        """
        CREATE VIEW core.candidate_field_current AS
        SELECT DISTINCT ON (candidate_id, field) *
        FROM core.candidate_field
        ORDER BY candidate_id, field, recorded_at DESC, id DESC
        """
    )
    op.execute("GRANT SELECT ON core.candidate_field_current TO talent_rw")

    # --- core.candidate: archive columns only, identity fixed ----------------------------------
    op.execute("REVOKE UPDATE ON core.candidate FROM talent_rw")
    op.execute(
        "GRANT UPDATE (archived_at, archived_reason, archived_by) ON core.candidate TO talent_rw"
    )
    op.execute("ALTER TABLE core.candidate ALTER COLUMN pipeline_state DROP DEFAULT")
    op.execute(
        r"""
        ALTER TABLE core.candidate
          ADD CONSTRAINT candidate_archive_reason_visible
            CHECK (archived_reason IS NULL OR archived_reason ~ '\S'),
          ADD CONSTRAINT candidate_archive_by_visible
            CHECK (archived_by IS NULL OR archived_by ~ '\S')
        """
    )
    op.execute(
        """
        CREATE FUNCTION core.keep_candidate_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.capture_id, NEW.source_key, NEW.created_at, NEW.created_by, NEW.pipeline_state)
             IS DISTINCT FROM
             (OLD.capture_id, OLD.source_key, OLD.created_at, OLD.created_by, OLD.pipeline_state)
          THEN
            RAISE EXCEPTION 'core.candidate %: what a candidate was created from never changes',
              OLD.id USING ERRCODE = 'insufficient_privilege';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER candidate_identity_fixed BEFORE UPDATE ON core.candidate "
        "FOR EACH ROW EXECUTE FUNCTION core.keep_candidate_identity()"
    )

    # --- core.evaluation: original text, and repeatable computed verdicts ----------------------
    op.execute(
        "ALTER TABLE core.evaluation ADD COLUMN signals_text text, ADD COLUMN flags_text text"
    )
    _drop_unique("core.evaluation", "UNIQUE (candidate_id, criteria_version_id, origin)")
    op.execute(
        "CREATE UNIQUE INDEX evaluation_stored_once ON core.evaluation "
        "(candidate_id, criteria_version_id) WHERE origin = 'stored'"
    )

    # --- jobs.job: attempts, and history that stays true ---------------------------------------
    op.execute(
        "ALTER TABLE jobs.job ADD COLUMN attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0)"
    )
    op.execute(
        """
        CREATE FUNCTION jobs.guard_job() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.kind, NEW.params, NEW.requested_by, NEW.requested_at)
             IS DISTINCT FROM (OLD.kind, OLD.params, OLD.requested_by, OLD.requested_at) THEN
            RAISE EXCEPTION 'jobs.job %: kind, params and requester never change', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF OLD.status IN ('succeeded', 'failed') THEN
            RAISE EXCEPTION 'jobs.job %: a finished job is final', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF NOT (
            (OLD.status = 'queued' AND NEW.status IN ('queued', 'running'))
            OR (
              OLD.status = 'running'
              AND NEW.status IN ('running', 'queued', 'succeeded', 'failed')
            )
          ) THEN
            RAISE EXCEPTION 'jobs.job %: % cannot become %', OLD.id, OLD.status, NEW.status
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER job_guard BEFORE UPDATE ON jobs.job "
        "FOR EACH ROW EXECUTE FUNCTION jobs.guard_job()"
    )
    op.execute(
        """
        CREATE FUNCTION jobs.refuse_delete() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'jobs.job keeps every job: % refused', TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER job_no_delete BEFORE DELETE ON jobs.job "
        "FOR EACH ROW EXECUTE FUNCTION jobs.refuse_delete()"
    )
    op.execute(
        "CREATE TRIGGER job_no_truncate BEFORE TRUNCATE ON jobs.job "
        "FOR EACH STATEMENT EXECUTE FUNCTION jobs.refuse_delete()"
    )


def downgrade() -> None:
    raise RuntimeError("0004 is not reversible: it would reopen overwrites of recorded history.")
