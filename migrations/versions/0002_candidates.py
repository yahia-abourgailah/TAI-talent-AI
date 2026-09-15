"""Candidates, field provenance, criteria versions and evaluations.

  core.criteria_version  one row per criteria version. Never changed once written.
  core.candidate         one person, linked to the raw capture it came from.
  core.candidate_field   one value per field, where it came from, and whether anyone verified it.
                         A missing value is stored as NULL with a status, never guessed.
  core.evaluation        a score and tier, pinned to the criteria, model and prompt versions
                         that produced it. Never changed once written.

The app role has no DELETE on any table (BR-205), and no UPDATE on criteria versions or
evaluations: a changed rule is a new version, a new verdict is a new evaluation.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-14
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE core.criteria_version (
          id              text        PRIMARY KEY,
          ruleset_module  text        NOT NULL,
          description     text        NOT NULL,
          effective_from  date        NOT NULL,
          created_at      timestamptz NOT NULL DEFAULT now(),
          created_by      text        NOT NULL
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE core.criteria_version IS 'Immutable: a rule change is a new version'"
    )

    op.execute(
        """
        CREATE TABLE core.candidate (
          id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          capture_id  bigint      NOT NULL REFERENCES raw.capture (id),
          source_key  text        NOT NULL,
          created_at  timestamptz NOT NULL DEFAULT now(),
          created_by  text        NOT NULL
        )
        """
    )
    # The same source row imported twice is the same candidate, never a duplicate (BR-106).
    op.execute("CREATE UNIQUE INDEX candidate_source_key ON core.candidate (source_key)")

    op.execute(
        """
        CREATE TABLE core.candidate_field (
          id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          candidate_id         bigint      NOT NULL REFERENCES core.candidate (id),
          field                text        NOT NULL,
          value                text,
          source               text        NOT NULL,
          verification_status  text        NOT NULL DEFAULT 'unverified'
            CHECK (verification_status IN ('unverified', 'verified', 'not_recorded')),
          verified_at          timestamptz,
          verified_by          text,
          recorded_at          timestamptz NOT NULL DEFAULT now(),
          UNIQUE (candidate_id, field),
          -- Verified means someone, at some time. Anything else carries neither.
          CHECK (
            (verification_status = 'verified')
            = (verified_at IS NOT NULL AND verified_by IS NOT NULL)
          ),
          -- Not recorded means blank, never a filled-in guess.
          CHECK (verification_status <> 'not_recorded' OR value IS NULL)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE core.evaluation (
          id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          candidate_id         bigint        NOT NULL REFERENCES core.candidate (id),
          criteria_version_id  text          NOT NULL REFERENCES core.criteria_version (id),
          origin               text          NOT NULL CHECK (origin IN ('stored', 'computed')),
          score                numeric(6, 2),
          tier                 text,
          model_version        text,
          prompt_version       text,
          evaluated_at         timestamptz,
          recorded_at          timestamptz   NOT NULL DEFAULT now(),
          recorded_by          text          NOT NULL,
          UNIQUE (candidate_id, criteria_version_id, origin)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE core.evaluation IS "
        "'Immutable: pins the criteria, model and prompt versions that produced the verdict'"
    )

    # 0001's default privileges gave core SELECT, INSERT, UPDATE. Versions and verdicts lose UPDATE.
    op.execute("REVOKE UPDATE ON core.criteria_version, core.evaluation FROM talent_rw")

    op.execute(
        """
        CREATE FUNCTION core.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'core.% is immutable: % refused', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    for table in ("criteria_version", "evaluation"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON core.{table} "
            "FOR EACH ROW EXECUTE FUNCTION core.refuse_change()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON core.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION core.refuse_change()"
        )


def downgrade() -> None:
    raise RuntimeError("0002 is not reversible: dropping core would destroy evaluations.")
