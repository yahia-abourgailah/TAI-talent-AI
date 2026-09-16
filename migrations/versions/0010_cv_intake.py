"""CVs in: uploads kept forever, OCR readings, and review items about candidates (week 5).

  intake.cv_upload      one row per upload: the raw capture of the file, the candidate it made,
                        and the hash of the upload token (never the token). The same file uploaded
                        again is the same capture and the same candidate (BR-106).
  intake.cv_reading     how reading a CV ended, once per file: read, or failed with a code. A
                        reading keeps the OCR's answer as its own raw capture (source ocr_answer),
                        exactly as it came, and whether the OCR found hidden content (BR-308).
  audit.document_access every time someone lists or downloads a candidate's file, with their name.
  core.candidate_field  language: ar, en or mixed, as the CV wrote the value (BR-309).
  jobs.job              run_after: a job is not claimed before it. Retries wait with growing gaps
                        (NFR-04).
  pipeline.review_item  two new kinds about a candidate rather than an application:
                          flagged_document      a CV a person must look at: hidden content, or
                                                reading failed (BR-308, NFR-04)
                          unverified_candidate  a candidate typed in by hand, not yet checked
                                                (BR-103, BR-202)
                        candidate_id is recorded on every new item; capture_id names the file.
  pipeline.review_resolution
                        a new outcome, checked: a person looked at the file or checked the
                        candidate. An unverified candidate is checked only once none of its
                        current fields is unverified. Confirming stays for proposed rejections.

Everything added is append-only for every role.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-16
"""

from alembic import op
from sqlalchemy import text

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

DOCUMENT_REASONS = (
    "hidden_content",
    "ocr_failed",
    "ocr_timed_out",
    "ocr_unavailable",
    "ocr_rejected",
    "ocr_answer_unreadable",
    "document_missing",
)
CANDIDATE_REASONS = ("manual_entry",)


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


def _drop_checks(table: str, *phrases: str) -> None:
    """Drops the unnamed CHECK constraints whose definition contains every phrase."""
    bind = op.get_bind()
    found = bind.execute(
        text(
            "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
            "WHERE conrelid = CAST(:table AS regclass) AND contype = 'c'"
        ),
        {"table": table},
    ).all()
    matched = [row.conname for row in found if all(p in row.definition for p in phrases)]
    if len(matched) != 1:
        raise RuntimeError(f"{table}: expected one CHECK matching {phrases}, found {matched}")
    op.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{matched[0]}"')


def _append_only(schema: str, table: str) -> None:
    op.execute(
        f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {schema}.{table} "
        f"FOR EACH ROW EXECUTE FUNCTION {schema}.refuse_change()"
    )
    op.execute(
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {schema}.{table} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {schema}.refuse_change()"
    )


def upgrade() -> None:
    # --- jobs: retries that wait -----------------------------------------------------------------
    op.execute("ALTER TABLE jobs.job ADD COLUMN run_after timestamptz")
    op.execute(
        "CREATE INDEX job_read_cv_capture ON jobs.job ((params ->> 'capture_id')) "
        "WHERE kind = 'read_cv'"
    )

    # --- core.candidate_field: the language a value was written in --------------------------------
    op.execute(
        "ALTER TABLE core.candidate_field ADD COLUMN language text "
        "CHECK (language IN ('ar', 'en', 'mixed'))"
    )
    # The view's SELECT * was expanded when 0004 made it; a new column is appended.
    op.execute(
        """
        CREATE OR REPLACE VIEW core.candidate_field_current AS
        SELECT DISTINCT ON (candidate_id, field) *
        FROM core.candidate_field
        ORDER BY candidate_id, field, recorded_at DESC, id DESC
        """
    )

    # --- intake ----------------------------------------------------------------------------------
    op.execute("CREATE SCHEMA intake")
    op.execute("GRANT USAGE ON SCHEMA intake TO talent_rw")
    op.execute(
        """
        CREATE FUNCTION intake.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'intake.% is append-only: % refused', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TABLE intake.cv_upload (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          capture_id    bigint      NOT NULL REFERENCES raw.capture (id),
          candidate_id  bigint      NOT NULL REFERENCES core.candidate (id),
          token_sha256  bytea       NOT NULL UNIQUE CHECK (octet_length(token_sha256) = 32),
          created_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
          expires_at    timestamptz NOT NULL,
          CHECK (expires_at > created_at)
        )
        """
    )
    op.execute("CREATE INDEX cv_upload_capture ON intake.cv_upload (capture_id)")
    op.execute("CREATE INDEX cv_upload_candidate ON intake.cv_upload (candidate_id)")
    op.execute(
        """
        CREATE FUNCTION intake.check_cv_upload() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM core.candidate c JOIN raw.capture r ON r.id = c.capture_id
            WHERE c.id = NEW.candidate_id AND c.capture_id = NEW.capture_id
              AND r.source = 'cv_upload'
          ) THEN
            RAISE EXCEPTION 'cv upload: candidate % was not made from capture %',
              NEW.candidate_id, NEW.capture_id USING ERRCODE = 'check_violation';
          END IF;
          NEW.created_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER cv_upload_of_its_candidate BEFORE INSERT ON intake.cv_upload "
        "FOR EACH ROW EXECUTE FUNCTION intake.check_cv_upload()"
    )

    op.execute(
        """
        CREATE TABLE intake.cv_reading (
          id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          capture_id         bigint      NOT NULL UNIQUE REFERENCES raw.capture (id),
          outcome            text        NOT NULL CHECK (outcome IN ('read', 'failed')),
          failure            text        CHECK (failure ~ '^[a-z_]{1,40}$'),
          hidden_content     boolean     NOT NULL DEFAULT false,
          answer_capture_id  bigint      REFERENCES raw.capture (id),
          reader             text        NOT NULL CHECK (reader ~ '^[a-z0-9-]{1,40}$'),
          attempts           integer     NOT NULL CHECK (attempts >= 0),
          job_id             bigint      REFERENCES jobs.job (id),
          read_at            timestamptz NOT NULL DEFAULT clock_timestamp(),
          CHECK ((outcome = 'failed') = (failure IS NOT NULL)),
          CHECK (outcome = 'failed' OR answer_capture_id IS NOT NULL),
          CHECK (outcome = 'read' OR NOT hidden_content)
        )
        """
    )
    for table in ("cv_upload", "cv_reading"):
        _append_only("intake", table)
    op.execute("GRANT SELECT, INSERT ON intake.cv_upload, intake.cv_reading TO talent_rw")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA intake TO talent_rw")

    # --- audit.document_access -------------------------------------------------------------------
    op.execute(
        r"""
        CREATE TABLE audit.document_access (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          capture_id    bigint      REFERENCES raw.capture (id),
          candidate_id  bigint      NOT NULL REFERENCES core.candidate (id),
          action        text        NOT NULL CHECK (action IN ('listed', 'downloaded')),
          accessed_by   text        NOT NULL CHECK (accessed_by ~ '\S'),
          request_id    text        CHECK (length(request_id) <= 64),
          accessed_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
          CHECK (action = 'listed' OR capture_id IS NOT NULL)
        )
        """
    )
    op.execute("CREATE INDEX document_access_candidate ON audit.document_access (candidate_id)")
    _append_only("audit", "document_access")
    op.execute("GRANT SELECT, INSERT ON audit.document_access TO talent_rw")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA audit TO talent_rw")

    # --- pipeline.review_item: items about a candidate -------------------------------------------
    op.execute(
        """
        ALTER TABLE pipeline.review_item
          ALTER COLUMN application_id DROP NOT NULL,
          ALTER COLUMN list_version DROP NOT NULL,
          ALTER COLUMN at_step DROP NOT NULL,
          ADD COLUMN candidate_id bigint REFERENCES core.candidate (id),
          ADD COLUMN capture_id bigint REFERENCES raw.capture (id)
        """
    )
    _drop_checks("pipeline.review_item", "kind", "proposed_rejection")
    op.execute(
        f"""
        ALTER TABLE pipeline.review_item
          ADD CONSTRAINT review_item_kind CHECK (
            kind IN ('proposed_rejection', 'flagged_document', 'unverified_candidate')
          ),
          ADD CONSTRAINT review_item_subject CHECK (
            (kind = 'proposed_rejection' AND application_id IS NOT NULL
             AND list_version IS NOT NULL AND at_step IS NOT NULL)
            OR (kind <> 'proposed_rejection' AND application_id IS NULL
                AND candidate_id IS NOT NULL AND list_version IS NULL AND at_step IS NULL)
          ),
          ADD CONSTRAINT review_item_document CHECK (
            (kind = 'flagged_document') = (capture_id IS NOT NULL)
          ),
          ADD CONSTRAINT review_item_candidate_reason CHECK (
            kind = 'proposed_rejection'
            OR (kind = 'flagged_document' AND reason_code IN {_in(DOCUMENT_REASONS)})
            OR (kind = 'unverified_candidate' AND reason_code IN {_in(CANDIDATE_REASONS)})
          )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX review_item_unverified_once ON pipeline.review_item (candidate_id) "
        "WHERE kind = 'unverified_candidate'"
    )
    op.execute(
        "CREATE UNIQUE INDEX review_item_document_once ON pipeline.review_item "
        "(capture_id, reason_code) WHERE kind = 'flagged_document'"
    )
    op.execute("CREATE INDEX review_item_candidate ON pipeline.review_item (candidate_id)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pipeline.check_review_item() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          current_step     text;
          current_outcome  text;
        BEGIN
          IF NEW.kind = 'proposed_rejection' THEN
            NEW.list_version := pipeline.active_list();
            SELECT m.to_step, s.outcome INTO current_step, current_outcome
            FROM pipeline.move m
            LEFT JOIN pipeline.step s ON s.list_version = m.list_version AND s.code = m.to_step
            WHERE m.application_id = NEW.application_id
            ORDER BY m.sequence DESC LIMIT 1;
            IF current_step IS NULL THEN
              RAISE EXCEPTION 'application %: no such application', NEW.application_id
                USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF current_outcome IS NOT NULL THEN
              RAISE EXCEPTION 'application %: % is final, nothing to review', NEW.application_id,
                current_step USING ERRCODE = 'check_violation';
            END IF;
            NEW.at_step := current_step;
            NEW.candidate_id := (
              SELECT candidate_id FROM pipeline.application WHERE id = NEW.application_id
            );
          ELSE
            IF NEW.application_id IS NOT NULL OR NEW.candidate_id IS NULL THEN
              RAISE EXCEPTION 'review item: % is about a candidate, not an application', NEW.kind
                USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.kind = 'flagged_document' AND NOT EXISTS (
              SELECT 1 FROM intake.cv_upload u
              WHERE u.capture_id = NEW.capture_id AND u.candidate_id = NEW.candidate_id
            ) THEN
              RAISE EXCEPTION 'review item: capture % is not a CV of candidate %',
                NEW.capture_id, NEW.candidate_id USING ERRCODE = 'check_violation';
            END IF;
            NEW.list_version := NULL;
            NEW.at_step := NULL;
          END IF;
          NEW.proposed_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )

    # --- pipeline.review_resolution: checked ------------------------------------------------------
    _drop_checks("pipeline.review_resolution", "outcome", "dismissed")
    _drop_checks("pipeline.review_resolution", "reason IS NOT NULL")
    op.execute(
        r"""
        ALTER TABLE pipeline.review_resolution
          ADD CONSTRAINT review_resolution_outcome CHECK (
            outcome IN ('confirmed', 'dismissed', 'checked')
          ),
          ADD CONSTRAINT review_resolution_dismissal_reason CHECK (
            outcome <> 'dismissed' OR reason IS NOT NULL
          ),
          ADD CONSTRAINT review_resolution_reason_visible CHECK (
            reason IS NULL OR reason ~ '\S'
          )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pipeline.check_resolution() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          item pipeline.review_item%ROWTYPE;
        BEGIN
          SELECT * INTO item FROM pipeline.review_item WHERE id = NEW.review_item_id;
          IF NEW.outcome = 'confirmed' THEN
            IF item.kind IS DISTINCT FROM 'proposed_rejection' THEN
              RAISE EXCEPTION 'review item %: only a proposed rejection is confirmed',
                NEW.review_item_id USING ERRCODE = 'check_violation';
            END IF;
            IF NOT EXISTS (
              SELECT 1
              FROM pipeline.move m
              JOIN pipeline.step s ON s.list_version = m.list_version AND s.code = m.to_step
              WHERE m.id = NEW.move_id
                AND m.application_id = item.application_id
                AND s.outcome = 'rejected'
                AND m.actor_kind = 'person'
                AND m.moved_by = NEW.resolved_by
                AND m.reason_code = item.reason_code
            ) THEN
              RAISE EXCEPTION 'review item %: confirming needs the rejection move of the person '
                'confirming, with the proposed reason', NEW.review_item_id
                USING ERRCODE = 'check_violation';
            END IF;
          ELSIF NEW.outcome = 'checked' THEN
            IF item.kind = 'proposed_rejection' THEN
              RAISE EXCEPTION 'review item %: a proposed rejection is confirmed or dismissed',
                NEW.review_item_id USING ERRCODE = 'check_violation';
            END IF;
            IF item.kind = 'unverified_candidate' AND EXISTS (
              SELECT 1 FROM core.candidate_field_current f
              WHERE f.candidate_id = item.candidate_id AND f.verification_status = 'unverified'
            ) THEN
              RAISE EXCEPTION 'review item %: candidate % still has unchecked fields',
                NEW.review_item_id, item.candidate_id USING ERRCODE = 'check_violation';
            END IF;
          END IF;
          NEW.resolved_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )

    # Only a dismissed proposed rejection is a signal for criteria reviews (BR-406).
    op.execute(
        """
        CREATE OR REPLACE VIEW pipeline.override_signal AS
        SELECT 'proposed_rejection_dismissed'::text AS label, i.application_id, i.reason_code,
               x.reason, x.resolved_by AS decided_by, x.resolved_at AS decided_at
        FROM pipeline.review_resolution x
        JOIN pipeline.review_item i ON i.id = x.review_item_id
        WHERE x.outcome = 'dismissed' AND i.kind = 'proposed_rejection'
        UNION ALL
        SELECT v.label, v.application_id, m.reason_code, v.reason, v.reversed_by, v.reversed_at
        FROM pipeline.reversal v
        JOIN pipeline.move m ON m.id = v.rejection_move_id
        """
    )

    # --- The event for a review item names its candidate and file --------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION integration.on_review_item() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        DECLARE
          app pipeline.application%ROWTYPE;
        BEGIN
          IF NEW.application_id IS NOT NULL THEN
            SELECT * INTO app FROM pipeline.application WHERE id = NEW.application_id;
          END IF;
          INSERT INTO integration.event (type, occurred_at, application_id, data)
          VALUES ('review.item_created', NEW.proposed_at, NEW.application_id,
            jsonb_build_object(
              'review_item_id', 'rvw_' || NEW.id,
              'kind', CASE NEW.kind WHEN 'proposed_rejection' THEN 'negative_verdict'
                                    ELSE NEW.kind END,
              'candidate_id', 'cand_' || coalesce(NEW.candidate_id, app.candidate_id),
              'application_id', 'app_' || NEW.application_id
            ) || CASE WHEN NEW.capture_id IS NULL THEN '{}'::jsonb
                      ELSE jsonb_build_object('document_id', 'doc_' || NEW.capture_id) END);
          RETURN NULL;
        END
        $$
        """
    )


def downgrade() -> None:
    raise RuntimeError("0010 is not reversible: dropping intake would lose kept CVs and readings.")
