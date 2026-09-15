"""Idempotency keys for the frozen API (API plan section 5).

  api.idempotency_key   the response to a create request, kept so a retry with the same
                        Idempotency-Key returns it instead of creating a second record. Keys are
                        per person and matched for 24 hours. Append-only for every role: an
                        expired key is not matched, and using it again adds a row. Responses hold
                        ids, codes and timestamps, never candidate data.

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
    op.execute("CREATE SCHEMA api")
    op.execute("GRANT USAGE ON SCHEMA api TO talent_rw")
    op.execute(
        r"""
        CREATE TABLE api.idempotency_key (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          subject         text        NOT NULL CHECK (subject ~ '\S'),
          key             uuid        NOT NULL,
          request_sha256  bytea       NOT NULL CHECK (octet_length(request_sha256) = 32),
          status_code     integer     NOT NULL CHECK (status_code BETWEEN 200 AND 299),
          response        jsonb       NOT NULL,
          created_at      timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        "CREATE INDEX idempotency_key_lookup ON api.idempotency_key (subject, key, created_at DESC)"
    )
    op.execute(
        """
        CREATE FUNCTION api.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'api.% is append-only: % refused', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER idempotency_key_append_only BEFORE UPDATE OR DELETE ON api.idempotency_key "
        "FOR EACH ROW EXECUTE FUNCTION api.refuse_change()"
    )
    op.execute(
        "CREATE TRIGGER idempotency_key_no_truncate BEFORE TRUNCATE ON api.idempotency_key "
        "FOR EACH STATEMENT EXECUTE FUNCTION api.refuse_change()"
    )
    op.execute("GRANT SELECT, INSERT ON api.idempotency_key TO talent_rw")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA api TO talent_rw")


def downgrade() -> None:
    # Only kept responses live here; dropping them makes a retry run again, and loses no record.
    op.execute("DROP SCHEMA api CASCADE")
