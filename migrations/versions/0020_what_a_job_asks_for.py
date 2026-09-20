"""A job that is not sales lists the skills it asks for, and each CV is matched against them.

The description says what the job is, in prose, for a person to read. It is not something a
machine can check a CV against line by line. So an opening of kind `other` now also carries a
list: each skill it asks for and the level it wants.

  pipeline.opening.requirements   [{"skill": "Python", "level": "advanced",
                                    "category": "Technical"}, ...]

The level is one of the four the CV reader uses, because that is what the match is done against:
beginner, intermediate, advanced, expert. A list nobody can read against those levels would give
a percentage that means nothing, so the database refuses it.

Like everything else about an opening, the list never changes once the opening exists: a
requisition whose requirements move is a different requisition, and every assessment made under
the old ones would quietly become an assessment of something else.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-20
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

LEVELS = ("beginner", "intermediate", "advanced", "expert")
MAX_SKILLS = 40


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION pipeline.requirements_are_readable(value jsonb) RETURNS boolean
        LANGUAGE sql IMMUTABLE AS $$
          SELECT jsonb_typeof(value) = 'array'
             AND jsonb_array_length(value) BETWEEN 1 AND 40
             AND NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements(value) skill
               WHERE jsonb_typeof(skill) <> 'object'
                  OR coalesce(skill ->> 'skill', '') !~ '\S'
                  OR length(skill ->> 'skill') > 80
                  OR (skill ->> 'level') IS NULL
                  OR NOT ((skill ->> 'level') = ANY (
                       ARRAY['beginner', 'intermediate', 'advanced', 'expert']))
                  OR length(coalesce(skill ->> 'category', '')) > 40
             )
        $$
        """
    )
    op.execute(
        """
        ALTER TABLE pipeline.opening
          ADD COLUMN requirements jsonb,
          ADD CONSTRAINT opening_requirements CHECK (
            CASE WHEN job_type = 'other'
                 THEN pipeline.requirements_are_readable(requirements)
                 ELSE requirements IS NULL END
          )
        """
    )
    # The list is part of what the opening is, so it never changes either.
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
              NEW.public, NEW.job_type, NEW.description, NEW.requirements)
             IS DISTINCT FROM
             (OLD.brand, OLD.department, OLD.track, OLD.headcount, OLD.owner_recruiter, OLD.team,
              OLD.criteria_version_id, OLD.created_at, OLD.created_by, OLD.title, OLD.location,
              OLD.public, OLD.job_type, OLD.description, OLD.requirements) THEN
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


def downgrade() -> None:
    raise RuntimeError(
        "0020 is not reversible: dropping the requirements would leave assessments that were "
        "made against a list nobody can see any more."
    )
