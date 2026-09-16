"""A candidate who asks us to stop keeping their data (week 6: BR-504, BR-205).

  core.consent_withdrawal  a candidate asked us to stop keeping their data, and a TA member
                           recorded it: how they asked, when, in whose words, and who recorded it.
                           While a withdrawal stands, the record is locked: it is not read, not
                           searched, not scored, not listed and not matched with anyone, and no one
                           but an admin sees that it is there.

Nothing is deleted (BR-205). The record and its history stay exactly as they are, because a
withdrawal recorded by mistake has to be undoable and because the counts behind past reports must
still add up. Lifting a withdrawal is the only change the table takes, and it carries a reason.

Consent itself stays append-only: a withdrawal is a later record, never an edit of what the
candidate agreed to (migration 0011).

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-16
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

HOW = ("phone", "whatsapp", "email", "in_person", "letter", "other")


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE core.consent_withdrawal (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          candidate_id  bigint      NOT NULL REFERENCES core.candidate (id),
          asked_how     text        NOT NULL CHECK (asked_how IN
                          ('phone', 'whatsapp', 'email', 'in_person', 'letter', 'other')),
          asked_at      timestamptz NOT NULL,
          note          text        CHECK (note IS NULL OR note ~ '\S'),
          recorded_by   text        NOT NULL CHECK (recorded_by ~ '\S'),
          recorded_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
          lifted_at     timestamptz,
          lifted_by     text,
          lifted_reason text,
          CONSTRAINT withdrawal_asked_before_it_was_recorded CHECK (
            asked_at <= recorded_at + interval '1 hour'
          ),
          CONSTRAINT withdrawal_lifted_with_a_reason CHECK (
            (lifted_at IS NULL AND lifted_by IS NULL AND lifted_reason IS NULL)
            OR (lifted_at IS NOT NULL AND lifted_by ~ '\S' AND lifted_reason ~ '\S')
          )
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX consent_withdrawn_once ON core.consent_withdrawal (candidate_id) "
        "WHERE lifted_at IS NULL"
    )
    op.execute(
        """
        CREATE FUNCTION core.guard_consent_withdrawal() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'core.consent_withdrawal keeps every request: % refused', TG_OP
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF OLD.lifted_at IS NOT NULL THEN
            RAISE EXCEPTION 'withdrawal %: it is already lifted', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF (NEW.candidate_id, NEW.asked_how, NEW.asked_at, NEW.note, NEW.recorded_by,
              NEW.recorded_at)
             IS DISTINCT FROM
             (OLD.candidate_id, OLD.asked_how, OLD.asked_at, OLD.note, OLD.recorded_by,
              OLD.recorded_at) THEN
            RAISE EXCEPTION 'withdrawal %: lifting is the only change it takes', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          NEW.lifted_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER consent_withdrawal_guard BEFORE UPDATE OR DELETE "
        "ON core.consent_withdrawal "
        "FOR EACH ROW EXECUTE FUNCTION core.guard_consent_withdrawal()"
    )
    op.execute(
        "CREATE TRIGGER consent_withdrawal_no_truncate BEFORE TRUNCATE "
        "ON core.consent_withdrawal "
        "FOR EACH STATEMENT EXECUTE FUNCTION core.refuse_change()"
    )
    op.execute("GRANT SELECT, INSERT ON core.consent_withdrawal TO talent_rw")
    op.execute(
        "GRANT UPDATE (lifted_at, lifted_by, lifted_reason) ON core.consent_withdrawal TO talent_rw"
    )

    op.execute(
        """
        CREATE VIEW core.candidate_locked AS
        SELECT w.candidate_id, w.id AS withdrawal_id, w.recorded_at AS locked_at
        FROM core.consent_withdrawal w
        WHERE w.lifted_at IS NULL
        """
    )
    op.execute("GRANT SELECT ON core.candidate_locked TO talent_rw")


def downgrade() -> None:
    raise RuntimeError(
        "0013 is not reversible: dropping it would lose candidates' requests to be left alone."
    )
