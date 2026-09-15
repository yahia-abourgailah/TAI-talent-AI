"""Archiving, background jobs, and the columns the TAI_Master import needs.

  core.candidate        archived_at, archived_reason, archived_by (BR-205): a removed or invalid
                        record is archived with a reason and the person who did it, never deleted.
                        Once set, an archive cannot be changed or cleared, by any role.
                        pipeline_state: "not_recorded" when every pipeline cell was blank (BR-703).
  core.candidate_field  source_ref (raw capture and column) and inference (stated, inferred,
                        unknown), so a value that may have been derived cannot pass as stated.
  core.evaluation       recommendation, signals, flags, call_priority, copied as stored.
  core.criteria_version the row for 2026-08-04, which the stored scores are saved under.
  jobs.job              the queue: queued, running, succeeded or failed.
  audit.job_run         one row per run: counts, skips, and what it left unresolved (BR-604).
                        Append-only for every role.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-14
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE core.candidate
          ADD COLUMN pipeline_state   text NOT NULL DEFAULT 'not_recorded'
            CHECK (pipeline_state IN ('not_recorded', 'recorded_in_raw')),
          ADD COLUMN archived_at      timestamptz,
          ADD COLUMN archived_reason  text,
          ADD COLUMN archived_by      text,
          ADD CONSTRAINT candidate_archive_complete CHECK (
            (archived_at IS NULL AND archived_reason IS NULL AND archived_by IS NULL)
            OR (
              archived_at IS NOT NULL
              AND archived_reason IS NOT NULL AND btrim(archived_reason) <> ''
              AND archived_by IS NOT NULL AND btrim(archived_by) <> ''
            )
          )
        """
    )
    op.execute(
        """
        CREATE FUNCTION core.keep_archive() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.archived_at IS NOT NULL
             AND (NEW.archived_at, NEW.archived_reason, NEW.archived_by)
                 IS DISTINCT FROM (OLD.archived_at, OLD.archived_reason, OLD.archived_by) THEN
            RAISE EXCEPTION 'core.candidate %: an archive is permanent', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER candidate_archive_permanent BEFORE UPDATE ON core.candidate "
        "FOR EACH ROW EXECUTE FUNCTION core.keep_archive()"
    )

    op.execute(
        """
        ALTER TABLE core.candidate_field
          ADD COLUMN source_ref  text,
          ADD COLUMN inference   text CHECK (inference IN ('stated', 'inferred', 'unknown'))
        """
    )

    op.execute(
        """
        ALTER TABLE core.evaluation
          ADD COLUMN recommendation  text,
          ADD COLUMN signals         text[],
          ADD COLUMN flags           text[],
          ADD COLUMN call_priority   text
        """
    )

    op.execute(
        """
        INSERT INTO core.criteria_version
          (id, ruleset_module, description, effective_from, created_by)
        VALUES
          ('2026-08-04', 'scoring.rulesets.v2026_08_04',
           'Legacy hiring criteria as ratified on 4 August 2026 '
           '(docs/criteria/CRITERIA_2026-08-04.md)',
           '2026-08-04', 'migration 0003')
        ON CONFLICT (id) DO NOTHING
        """
    )

    op.execute("CREATE SCHEMA jobs")
    op.execute("GRANT USAGE ON SCHEMA jobs TO talent_rw")
    op.execute(
        """
        CREATE TABLE jobs.job (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          kind          text        NOT NULL,
          params        jsonb       NOT NULL DEFAULT '{}',
          status        text        NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
          requested_by  text        NOT NULL,
          requested_at  timestamptz NOT NULL DEFAULT now(),
          started_at    timestamptz,
          finished_at   timestamptz
        )
        """
    )
    op.execute("CREATE INDEX job_queued ON jobs.job (id) WHERE status = 'queued'")
    op.execute("GRANT SELECT, INSERT, UPDATE ON jobs.job TO talent_rw")

    op.execute(
        """
        CREATE TABLE audit.job_run (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          job_id        bigint      NOT NULL REFERENCES jobs.job (id),
          kind          text        NOT NULL,
          outcome       text        NOT NULL CHECK (outcome IN ('succeeded', 'failed')),
          started_at    timestamptz NOT NULL,
          finished_at   timestamptz NOT NULL,
          run_by        text        NOT NULL,
          input_sha256  text,
          counts        jsonb       NOT NULL DEFAULT '{}',
          skipped       jsonb       NOT NULL DEFAULT '{}',
          unresolved    jsonb       NOT NULL DEFAULT '[]',
          error         text,
          CHECK (finished_at >= started_at),
          CHECK ((outcome = 'failed') = (error IS NOT NULL))
        )
        """
    )
    op.execute("CREATE INDEX job_run_by_job ON audit.job_run (job_id)")
    op.execute("GRANT SELECT, INSERT ON audit.job_run TO talent_rw")
    op.execute(
        """
        CREATE FUNCTION audit.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit.% is append-only: % refused (BR-604)', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER job_run_append_only BEFORE UPDATE OR DELETE ON audit.job_run "
        "FOR EACH ROW EXECUTE FUNCTION audit.refuse_change()"
    )
    op.execute(
        "CREATE TRIGGER job_run_no_truncate BEFORE TRUNCATE ON audit.job_run "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit.refuse_change()"
    )


def downgrade() -> None:
    raise RuntimeError("0003 is not reversible: dropping audit.job_run would destroy run history.")
