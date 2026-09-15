"""Events for the CRM, written by the database itself (API plan section 7, BR-410).

  integration.event     one row per event: application.stage_changed for every recorded move,
                        review.item_created for every review item, candidate.scored for every
                        computed evaluation (a migrated, stored score is not an event). Written
                        only by triggers, so no code path can change a step, open a review or
                        score someone without the CRM hearing of it. The app role may read it,
                        never write it. Payloads carry typed ids and codes, never candidate data.
  integration.delivery_attempt
                        every webhook attempt: when, which try, delivered or failed, the status
                        code and the kind of failure. Never a response body.

Both are append-only for every role.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-16
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_DEFINER = "LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp"


def upgrade() -> None:
    op.execute("CREATE SCHEMA integration")
    op.execute("GRANT USAGE ON SCHEMA integration TO talent_rw")
    op.execute(
        """
        CREATE TABLE integration.event (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          type            text        NOT NULL CHECK (type IN (
                            'application.stage_changed', 'review.item_created', 'candidate.scored'
                          )),
          occurred_at     timestamptz NOT NULL,
          application_id  bigint      REFERENCES pipeline.application (id),
          data            jsonb       NOT NULL,
          recorded_at     timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("CREATE INDEX event_application ON integration.event (application_id)")
    op.execute("CREATE INDEX event_recorded ON integration.event (recorded_at)")
    op.execute(
        """
        CREATE TABLE integration.delivery_attempt (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          event_id      bigint      NOT NULL REFERENCES integration.event (id),
          attempt       integer     NOT NULL CHECK (attempt > 0),
          attempted_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
          outcome       text        NOT NULL CHECK (outcome IN ('delivered', 'failed')),
          status_code   integer     CHECK (status_code BETWEEN 100 AND 599),
          failure       text        CHECK (failure IS NULL OR failure ~ '^[a-z_]{1,40}$'),
          UNIQUE (event_id, attempt),
          CHECK ((outcome = 'failed') = (failure IS NOT NULL))
        )
        """
    )

    # --- Events, from the tables they describe --------------------------------------------------
    op.execute(
        f"""
        CREATE FUNCTION integration.on_move() RETURNS trigger {_DEFINER} AS $$
        DECLARE
          app pipeline.application%ROWTYPE;
        BEGIN
          SELECT * INTO app FROM pipeline.application WHERE id = NEW.application_id;
          INSERT INTO integration.event (type, occurred_at, application_id, data)
          VALUES ('application.stage_changed', NEW.moved_at, NEW.application_id,
            jsonb_build_object(
              'application_id', 'app_' || NEW.application_id,
              'candidate_id', 'cand_' || app.candidate_id,
              'requisition_id', 'req_' || app.opening_id,
              'transition_id', 'trn_' || NEW.id,
              'from_stage', NEW.from_step,
              'to_stage', NEW.to_step
            ));
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER move_is_an_event AFTER INSERT ON pipeline.move "
        "FOR EACH ROW EXECUTE FUNCTION integration.on_move()"
    )
    op.execute(
        f"""
        CREATE FUNCTION integration.on_review_item() RETURNS trigger {_DEFINER} AS $$
        DECLARE
          app pipeline.application%ROWTYPE;
        BEGIN
          SELECT * INTO app FROM pipeline.application WHERE id = NEW.application_id;
          INSERT INTO integration.event (type, occurred_at, application_id, data)
          VALUES ('review.item_created', NEW.proposed_at, NEW.application_id,
            jsonb_build_object(
              'review_item_id', 'rvw_' || NEW.id,
              'kind', CASE NEW.kind WHEN 'proposed_rejection' THEN 'negative_verdict'
                                    ELSE NEW.kind END,
              'candidate_id', 'cand_' || app.candidate_id,
              'application_id', 'app_' || NEW.application_id
            ));
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER review_item_is_an_event AFTER INSERT ON pipeline.review_item "
        "FOR EACH ROW EXECUTE FUNCTION integration.on_review_item()"
    )
    op.execute(
        f"""
        CREATE FUNCTION integration.on_evaluation() RETURNS trigger {_DEFINER} AS $$
        BEGIN
          INSERT INTO integration.event (type, occurred_at, application_id, data)
          VALUES ('candidate.scored', coalesce(NEW.evaluated_at, NEW.recorded_at), NULL,
            jsonb_build_object(
              'candidate_id', 'cand_' || NEW.candidate_id,
              'evaluation_id', 'evl_' || NEW.id,
              'criteria_version', NEW.criteria_version_id,
              -- Until evaluations carry their gates, a disqualification flag is a failed gate.
              'outcome', CASE WHEN coalesce(NEW.flags_text, '') LIKE '%DISQUALIFIED%'
                              THEN 'failed_gate' ELSE 'passed' END,
              'tier', NEW.tier
            ));
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER computed_evaluation_is_an_event AFTER INSERT ON core.evaluation "
        "FOR EACH ROW WHEN (NEW.origin = 'computed') "
        "EXECUTE FUNCTION integration.on_evaluation()"
    )

    # --- Append-only, for every role ------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION integration.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'integration.% is append-only: % refused', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    for table in ("event", "delivery_attempt"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON integration.{table} "
            "FOR EACH ROW EXECUTE FUNCTION integration.refuse_change()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON integration.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION integration.refuse_change()"
        )

    op.execute("GRANT SELECT ON integration.event TO talent_rw")
    op.execute("GRANT SELECT, INSERT ON integration.delivery_attempt TO talent_rw")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA integration TO talent_rw")


def downgrade() -> None:
    raise RuntimeError("0007 is not reversible: dropping integration would lose the event record.")
