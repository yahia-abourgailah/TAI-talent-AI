"""The same person, found twice (week 6: BR-203, BR-204, BR-206).

  core.candidate_match  two candidates the matcher believes are one person, with what it matched
                        on and how strong that is. Written by the matcher, never by a person, and
                        never acted on by itself: a person decides (BR-206).
  core.candidate_join   a person's decision that two records are one person: the joined candidate
                        is read under the primary one. Both records stay, with their own history
                        (BR-204). A join is undone by recording the undoing on the same row, the
                        only change these tables take; everything else is append-only.
  core.candidate_group  which candidate a record is read under today: itself, or the primary of
                        the join in force.
  pipeline.review_item  a new kind, possible_duplicate: a match waiting for a person, naming the
                        match it is about.

A joined candidate cannot itself be a primary, so a group is always one level deep.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-16
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

EVIDENCE = ("phone", "email", "profile_url", "name_arabic", "name_latin")
DUPLICATE_REASONS = ("possible_duplicate",)


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE core.candidate_match (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          lower_id      bigint      NOT NULL REFERENCES core.candidate (id),
          higher_id     bigint      NOT NULL REFERENCES core.candidate (id),
          strength      text        NOT NULL CHECK (strength IN ('strong', 'possible')),
          evidence      text[]      NOT NULL CHECK (
                          cardinality(evidence) > 0 AND evidence <@ ARRAY{list(EVIDENCE)}::text[]
                        ),
          found_by      text        NOT NULL CHECK (found_by ~ '\\S'),
          found_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE (lower_id, higher_id),
          CHECK (lower_id < higher_id)
        )
        """
    )
    op.execute("CREATE INDEX candidate_match_higher ON core.candidate_match (higher_id)")

    op.execute(
        r"""
        CREATE TABLE core.candidate_join (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          primary_id    bigint      NOT NULL REFERENCES core.candidate (id),
          joined_id     bigint      NOT NULL REFERENCES core.candidate (id),
          match_id      bigint      REFERENCES core.candidate_match (id),
          reason        text        NOT NULL CHECK (reason ~ '\S'),
          joined_by     text        NOT NULL CHECK (joined_by ~ '\S'),
          joined_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
          undone_at     timestamptz,
          undone_by     text,
          undone_reason text,
          CHECK (primary_id <> joined_id),
          CONSTRAINT join_undone_with_a_reason CHECK (
            (undone_at IS NULL AND undone_by IS NULL AND undone_reason IS NULL)
            OR (undone_at IS NOT NULL AND undone_by ~ '\S' AND undone_reason ~ '\S')
          )
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX candidate_joined_once ON core.candidate_join (joined_id) "
        "WHERE undone_at IS NULL"
    )
    op.execute("CREATE INDEX candidate_join_primary ON core.candidate_join (primary_id)")
    op.execute(
        """
        CREATE FUNCTION core.check_candidate_join() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM core.candidate_join
            WHERE joined_id = NEW.primary_id AND undone_at IS NULL
          ) THEN
            RAISE EXCEPTION 'candidate %: it is joined into another record, so nothing joins into '
              'it', NEW.primary_id USING ERRCODE = 'check_violation';
          END IF;
          IF EXISTS (
            SELECT 1 FROM core.candidate_join
            WHERE primary_id = NEW.joined_id AND undone_at IS NULL
          ) THEN
            RAISE EXCEPTION 'candidate %: other records are joined into it, so it cannot be joined '
              'into another', NEW.joined_id USING ERRCODE = 'check_violation';
          END IF;
          NEW.joined_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER candidate_join_is_one_level BEFORE INSERT ON core.candidate_join "
        "FOR EACH ROW EXECUTE FUNCTION core.check_candidate_join()"
    )
    op.execute(
        """
        CREATE FUNCTION core.guard_candidate_join() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'core.candidate_join keeps every join: % refused', TG_OP
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF OLD.undone_at IS NOT NULL THEN
            RAISE EXCEPTION 'join %: it is already undone', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF (NEW.primary_id, NEW.joined_id, NEW.match_id, NEW.reason, NEW.joined_by,
              NEW.joined_at)
             IS DISTINCT FROM
             (OLD.primary_id, OLD.joined_id, OLD.match_id, OLD.reason, OLD.joined_by,
              OLD.joined_at) THEN
            RAISE EXCEPTION 'join %: undoing is the only change a join takes', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          NEW.undone_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER candidate_join_guard BEFORE UPDATE OR DELETE ON core.candidate_join "
        "FOR EACH ROW EXECUTE FUNCTION core.guard_candidate_join()"
    )
    op.execute(
        "CREATE TRIGGER candidate_join_no_truncate BEFORE TRUNCATE ON core.candidate_join "
        "FOR EACH STATEMENT EXECUTE FUNCTION core.refuse_change()"
    )
    for table in ("candidate_match",):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON core.{table} "
            "FOR EACH ROW EXECUTE FUNCTION core.refuse_change()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON core.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION core.refuse_change()"
        )

    op.execute(
        """
        CREATE VIEW core.candidate_group AS
        SELECT c.id AS candidate_id,
               coalesce(j.primary_id, c.id) AS primary_id,
               j.id AS join_id
        FROM core.candidate c
        LEFT JOIN core.candidate_join j ON j.joined_id = c.id AND j.undone_at IS NULL
        """
    )
    op.execute("GRANT SELECT ON core.candidate_group TO talent_rw")
    op.execute("GRANT SELECT, INSERT ON core.candidate_match, core.candidate_join TO talent_rw")
    op.execute(
        "GRANT UPDATE (undone_at, undone_by, undone_reason) ON core.candidate_join TO talent_rw"
    )

    # --- A match waiting for a person -------------------------------------------------------------
    op.execute(
        "ALTER TABLE pipeline.review_item "
        "ADD COLUMN match_id bigint REFERENCES core.candidate_match (id)"
    )
    # Both were named by migration 0010.
    op.execute("ALTER TABLE pipeline.review_item DROP CONSTRAINT review_item_kind")
    op.execute("ALTER TABLE pipeline.review_item DROP CONSTRAINT review_item_candidate_reason")
    op.execute(
        f"""
        ALTER TABLE pipeline.review_item
          ADD CONSTRAINT review_item_kind CHECK (
            kind IN ('proposed_rejection', 'flagged_document', 'unverified_candidate',
                     'possible_duplicate')
          ),
          ADD CONSTRAINT review_item_candidate_reason CHECK (
            kind = 'proposed_rejection'
            OR (kind = 'flagged_document' AND reason_code IN
                ('hidden_content', 'ocr_failed', 'ocr_timed_out', 'ocr_unavailable',
                 'ocr_rejected', 'ocr_answer_unreadable', 'document_missing'))
            OR (kind = 'unverified_candidate' AND reason_code IN ('manual_entry'))
            OR (kind = 'possible_duplicate' AND reason_code IN {_in(DUPLICATE_REASONS)})
          ),
          ADD CONSTRAINT review_item_match CHECK (
            (kind = 'possible_duplicate') = (match_id IS NOT NULL)
          )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX review_item_duplicate_once ON pipeline.review_item (match_id) "
        "WHERE kind = 'possible_duplicate'"
    )


def downgrade() -> None:
    raise RuntimeError("0012 is not reversible: dropping joins would lose decisions people made.")
