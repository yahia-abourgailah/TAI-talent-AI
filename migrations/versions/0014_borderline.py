"""Borderline scores (week 7: BR-310, BR-407).

  core.criteria_borderline  how close to a tier line is borderline, for one criteria version, and
                            who signed it. One row per version and never changed: a different
                            number is a new criteria version with a replay behind it (BR-303).
                            A version with no row opens no borderline items.
  pipeline.review_item      a new kind, borderline_score: an evaluation close to a tier line,
                            naming the evaluation and the tiers either side. The candidate keeps
                            the tier the score gives; a person looks.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-17
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TIERS = ("P1", "P2", "P3", "P4", "T1", "T2", "T3", "T4")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE core.criteria_borderline (
          criteria_version_id  text        PRIMARY KEY REFERENCES core.criteria_version (id),
          rule                 text        NOT NULL CHECK (rule IN ('band', 'below_line')),
          points               integer     NOT NULL CHECK (points BETWEEN 1 AND 9),
          signed_by            text        NOT NULL CHECK (signed_by ~ '\S'),
          signed_on            date        NOT NULL,
          ruling               text        NOT NULL CHECK (ruling ~ '\S'),
          recorded_by          text        NOT NULL CHECK (recorded_by ~ '\S'),
          recorded_at          timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE core.criteria_borderline IS "
        "'Immutable: a different borderline number is a new criteria version'"
    )
    op.execute(
        "CREATE TRIGGER criteria_borderline_append_only BEFORE UPDATE OR DELETE "
        "ON core.criteria_borderline FOR EACH ROW EXECUTE FUNCTION core.refuse_change()"
    )
    op.execute(
        "CREATE TRIGGER criteria_borderline_no_truncate BEFORE TRUNCATE "
        "ON core.criteria_borderline FOR EACH STATEMENT EXECUTE FUNCTION core.refuse_change()"
    )
    op.execute("GRANT SELECT, INSERT ON core.criteria_borderline TO talent_rw")

    op.execute(
        f"""
        ALTER TABLE pipeline.review_item
          ADD COLUMN evaluation_id bigint REFERENCES core.evaluation (id),
          ADD COLUMN tier_above text CHECK (tier_above IN {_in(TIERS)}),
          ADD COLUMN tier_below text CHECK (tier_below IN {_in(TIERS)})
        """
    )
    # Both were last named by migration 0012.
    op.execute("ALTER TABLE pipeline.review_item DROP CONSTRAINT review_item_kind")
    op.execute("ALTER TABLE pipeline.review_item DROP CONSTRAINT review_item_candidate_reason")
    op.execute(
        """
        ALTER TABLE pipeline.review_item
          ADD CONSTRAINT review_item_kind CHECK (
            kind IN ('proposed_rejection', 'flagged_document', 'unverified_candidate',
                     'possible_duplicate', 'borderline_score')
          ),
          ADD CONSTRAINT review_item_candidate_reason CHECK (
            kind = 'proposed_rejection'
            OR (kind = 'flagged_document' AND reason_code IN
                ('hidden_content', 'ocr_failed', 'ocr_timed_out', 'ocr_unavailable',
                 'ocr_rejected', 'ocr_answer_unreadable', 'document_missing'))
            OR (kind = 'unverified_candidate' AND reason_code IN ('manual_entry'))
            OR (kind = 'possible_duplicate' AND reason_code IN ('possible_duplicate'))
            OR (kind = 'borderline_score' AND reason_code IN ('near_tier_line'))
          ),
          ADD CONSTRAINT review_item_borderline CHECK (
            (kind = 'borderline_score')
            = (evaluation_id IS NOT NULL AND tier_above IS NOT NULL AND tier_below IS NOT NULL)
          )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX review_item_borderline_once ON pipeline.review_item (evaluation_id) "
        "WHERE kind = 'borderline_score'"
    )
    op.execute(
        """
        CREATE FUNCTION pipeline.check_borderline_item() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM core.evaluation e
            WHERE e.id = NEW.evaluation_id AND e.candidate_id = NEW.candidate_id
          ) THEN
            RAISE EXCEPTION 'review item: evaluation % is not about candidate %',
              NEW.evaluation_id, NEW.candidate_id USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER review_item_borderline_evaluation BEFORE INSERT ON pipeline.review_item "
        "FOR EACH ROW WHEN (NEW.kind = 'borderline_score') "
        "EXECUTE FUNCTION pipeline.check_borderline_item()"
    )


def downgrade() -> None:
    raise RuntimeError("0014 is not reversible: dropping it would lose signed rulings.")
