"""Foundation: four schemas, the app role's grants, and the append-only raw capture table.

  raw       original captures, append-only (BR-107). Insert and read, never change or delete.
  core      candidates, field provenance, evaluations, criteria versions.
  pipeline  requisitions, applications, stage events.
  audit     attributed actions and job runs. Append-only for the app.

The app role gets no DELETE anywhere: records are archived with a reason, never deleted
(BR-205). Retention purge (CR-03) will run under its own role when it is built.

Append-only on raw is enforced twice: the app role has no UPDATE/DELETE grant, and a trigger
refuses UPDATE, DELETE and TRUNCATE for every role, the owner included.

Revision ID: 0001
Revises:
Create Date: 2026-09-14
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'talent_rw') THEN
            CREATE ROLE talent_rw NOLOGIN;
          END IF;
        END $$;
        """
    )

    op.execute("CREATE SCHEMA raw")
    op.execute("CREATE SCHEMA core")
    op.execute("CREATE SCHEMA pipeline")
    op.execute("CREATE SCHEMA audit")
    op.execute("GRANT USAGE ON SCHEMA raw, core, pipeline, audit TO talent_rw")

    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA raw, audit GRANT SELECT, INSERT ON TABLES TO talent_rw"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA core, pipeline "
        "GRANT SELECT, INSERT, UPDATE ON TABLES TO talent_rw"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA raw, core, pipeline, audit "
        "GRANT USAGE, SELECT ON SEQUENCES TO talent_rw"
    )

    op.execute(
        """
        CREATE TABLE raw.capture (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          source          text        NOT NULL,
          external_id     text,
          content_sha256  bytea       NOT NULL CHECK (octet_length(content_sha256) = 32),
          blob_key        text        NOT NULL,
          media_type      text        NOT NULL,
          byte_size       bigint      NOT NULL CHECK (byte_size >= 0),
          received_at     timestamptz NOT NULL DEFAULT now(),
          received_by     text        NOT NULL
        )
        """
    )
    op.execute("COMMENT ON TABLE raw.capture IS 'Original submissions, append-only (BR-107)'")
    # Re-importing the same thing from the same source is a no-op, never a duplicate (BR-106).
    op.execute(
        "CREATE UNIQUE INDEX capture_idempotency "
        "ON raw.capture (source, coalesce(external_id, ''), content_sha256)"
    )
    op.execute("GRANT SELECT, INSERT ON raw.capture TO talent_rw")

    op.execute(
        """
        CREATE FUNCTION raw.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'raw.% is append-only: % refused (BR-107)', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER capture_append_only BEFORE UPDATE OR DELETE ON raw.capture "
        "FOR EACH ROW EXECUTE FUNCTION raw.refuse_change()"
    )
    op.execute(
        "CREATE TRIGGER capture_no_truncate BEFORE TRUNCATE ON raw.capture "
        "FOR EACH STATEMENT EXECUTE FUNCTION raw.refuse_change()"
    )


def downgrade() -> None:
    raise RuntimeError("0001 is not reversible: dropping raw would destroy original captures.")
