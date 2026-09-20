"""Jobs that are not sales, judged against what the job asks for (BR-305, BR-306, CR-05).

The criteria version scores one thing: a sales hire, on one of two tracks. A requisition for an AI
engineer put through it scores nonsense — every candidate fails a sales keyword they were never
asked for. So a requisition now says what kind of job it is:

  sales   the criteria version, as today. Nothing changes for these.
  other   no rules score. The candidate's CV is read against the job's own description by the
          company's language model, on our own host (CR-01), and what comes back is an opinion for
          a person to read — never a tier, never a rejection.

  pipeline.opening   job_type and description. A job of kind `other` must say what it asks for:
                     an assessment nobody can check against the job is not evidence of anything.
  core.evaluation    origin gains 'ai'. An AI evaluation must name the model and the prompt that
                     produced it, and the application it was made for — the same person applying
                     to two jobs gets two different readings, and neither is about the other. It
                     **must not carry a tier or a call priority**: BR-306 says an AI never changes
                     a tier, and a column it cannot fill is a stronger promise than a rule
                     somebody has to remember.
  pipeline.review_item  a new kind, ai_assessment: a CV read against a job, waiting for a person.
                     Like the other kinds that are not a proposed rejection it is about a
                     candidate; which job was read is on the evaluation it points at.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-20
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

JOB_TYPES = ("sales", "other")
AI_REASONS = ("needs_a_person",)


def upgrade() -> None:
    op.execute(
        r"""
        ALTER TABLE pipeline.opening
          ADD COLUMN job_type text NOT NULL DEFAULT 'sales'
            CHECK (job_type IN ('sales', 'other')),
          ADD COLUMN description text CHECK (description IS NULL OR description ~ '\S'),
          ADD CONSTRAINT opening_other_jobs_say_what_they_ask_for CHECK (
            job_type <> 'other' OR (description IS NOT NULL AND length(description) >= 40)
          )
        """
    )
    # The kind and the description are part of what an opening is, so they never change either.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pipeline.guard_opening() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'UPDATE' THEN
            RAISE EXCEPTION 'pipeline.opening keeps every opening: % refused', TG_OP
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF OLD.status = 'closed' THEN
            RAISE EXCEPTION 'opening %: a closed opening is final', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF (NEW.brand, NEW.department, NEW.track, NEW.headcount, NEW.owner_recruiter, NEW.team,
              NEW.criteria_version_id, NEW.created_at, NEW.created_by, NEW.title, NEW.location,
              NEW.public, NEW.job_type, NEW.description)
             IS DISTINCT FROM
             (OLD.brand, OLD.department, OLD.track, OLD.headcount, OLD.owner_recruiter, OLD.team,
              OLD.criteria_version_id, OLD.created_at, OLD.created_by, OLD.title, OLD.location,
              OLD.public, OLD.job_type, OLD.description) THEN
            RAISE EXCEPTION 'opening %: closing is the only change an opening takes', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF NEW.status = 'closed' THEN
            NEW.closed_at := coalesce(NEW.closed_at, clock_timestamp());
          END IF;
          RETURN NEW;
        END
        $$
        """
    )

    op.execute("ALTER TABLE core.evaluation DROP CONSTRAINT evaluation_origin_check")
    op.execute(
        """
        ALTER TABLE core.evaluation
          ADD COLUMN application_id bigint REFERENCES pipeline.application (id),
          ADD CONSTRAINT evaluation_origin_check
            CHECK (origin IN ('stored', 'computed', 'ai')),
          ADD CONSTRAINT evaluation_ai_names_the_application CHECK (
            origin <> 'ai' OR application_id IS NOT NULL
          ),
          ADD CONSTRAINT evaluation_ai_names_what_produced_it CHECK (
            origin <> 'ai' OR (model_version IS NOT NULL AND prompt_version IS NOT NULL)
          ),
          ADD CONSTRAINT evaluation_ai_never_sets_a_tier CHECK (
            origin <> 'ai' OR (tier IS NULL AND call_priority IS NULL)
          )
        """
    )

    op.execute("ALTER TABLE pipeline.review_item DROP CONSTRAINT review_item_kind")
    op.execute("ALTER TABLE pipeline.review_item DROP CONSTRAINT review_item_candidate_reason")
    op.execute(
        """
        ALTER TABLE pipeline.review_item
          ADD CONSTRAINT review_item_kind CHECK (
            kind IN ('proposed_rejection', 'flagged_document', 'unverified_candidate',
                     'possible_duplicate', 'borderline_score', 'ai_assessment')
          ),
          ADD CONSTRAINT review_item_candidate_reason CHECK (
            kind = 'proposed_rejection'
            OR (kind = 'flagged_document' AND reason_code IN
                ('hidden_content', 'ocr_failed', 'ocr_timed_out', 'ocr_unavailable',
                 'ocr_rejected', 'ocr_answer_unreadable', 'document_missing'))
            OR (kind = 'unverified_candidate' AND reason_code IN ('manual_entry'))
            OR (kind = 'possible_duplicate' AND reason_code IN ('possible_duplicate'))
            OR (kind = 'borderline_score' AND reason_code IN ('near_tier_line'))
            OR (kind = 'ai_assessment' AND reason_code IN ('needs_a_person'))
          )
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0018 is not reversible: it would mean choosing what to do with assessments already made."
    )
