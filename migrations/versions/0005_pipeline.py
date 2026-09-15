"""The pipeline: openings, applications and recorded moves (BR-401 to BR-408).

  pipeline.step_list, step, allowed_move, rejection_reason
                        the steps, the moves allowed between them and the rejection reasons, as
                        rows. TA's final list is loaded as a new list and activated: no code
                        changes. The list seeded here follows the BRD order and is provisional.
  pipeline.list_activation
                        which list is in force: the latest activation.
  pipeline.opening      job openings (BR-401). Closing is the archive: it needs a reason and the
                        person, and a closed opening is final. Nothing else about it changes.
  pipeline.application  one candidate on one opening, with its owning recruiter and team (BR-108,
                        BR-408). Never changed. Migrated candidates get none until OPN-11 is ruled.
  pipeline.move         every step change: from, to, who, when (BR-403), and the only way a step
                        changes. Each move is checked against the active list; hired and rejected
                        are final; a rejection needs a listed reason and a person (BR-404, BR-405).
  pipeline.application_state
                        the current step, which is always the latest move and never a column.
  pipeline.review_item, review_resolution
                        an automated "reject" is only a review item. A person confirms it with a
                        rejection move, or dismisses it with a reason (BR-405).
  pipeline.reversal     reversing a rejection needs a reason and a person. The rejected application
                        stays rejected, and a new application reopens it (BR-406).
  pipeline.override_signal
                        dismissals and reversals, labelled, for future criteria reviews (BR-406).

Every table here is append-only for every role, the owner included, except that an open opening
may be closed once. Nothing is deleted.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-15
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

PROVISIONAL = "provisional-brd-2026-09"

# BR-402, in the BRD order. (code, label, outcome)
PROVISIONAL_STEPS = (
    ("new", "New", None),
    ("contacted", "Contacted", None),
    ("replied", "Replied", None),
    ("phone_screen", "Phone screen", None),
    ("hr_interview", "HR interview", None),
    ("aptitude_test", "Test", None),
    ("technical_interview", "Technical interview", None),
    ("offer", "Offer", None),
    ("hired", "Hired", "hired"),
    ("rejected", "Rejected", "rejected"),
)

# Placeholders until TA sends the rejection list (BR-404).
PROVISIONAL_REASONS = (
    ("does_not_meet_criteria", "Does not meet the criteria in force"),
    ("not_reachable", "Could not be reached"),
    ("no_response", "Did not respond"),
    ("withdrew", "Candidate withdrew"),
    ("not_suitable_after_interview", "Not suitable after interview"),
    ("did_not_pass_test", "Did not pass the test"),
    ("declined_offer", "Declined the offer"),
    ("opening_filled", "Opening filled or closed"),
)

_APPEND_ONLY = (
    "step_list",
    "step",
    "allowed_move",
    "rejection_reason",
    "list_activation",
    "application",
    "move",
    "review_item",
    "review_resolution",
    "reversal",
)


def _literal(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION pipeline.refuse_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'pipeline.% is append-only: % refused', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )

    # --- The step list, as data ------------------------------------------------------------------
    op.execute(
        r"""
        CREATE TABLE pipeline.step_list (
          version      text        PRIMARY KEY CHECK (version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
          provisional  boolean     NOT NULL,
          source       text        NOT NULL CHECK (source ~ '\S'),
          loaded_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
          loaded_by    text        NOT NULL CHECK (loaded_by ~ '\S')
        )
        """
    )
    op.execute(
        r"""
        CREATE TABLE pipeline.step (
          list_version  text    NOT NULL REFERENCES pipeline.step_list (version),
          code          text    NOT NULL CHECK (code ~ '^[a-z][a-z_]{0,39}$'),
          label         text    NOT NULL CHECK (label ~ '\S'),
          position      integer NOT NULL CHECK (position > 0),
          outcome       text    CHECK (outcome IN ('hired', 'rejected')),
          PRIMARY KEY (list_version, code),
          UNIQUE (list_version, position)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX step_one_per_outcome ON pipeline.step (list_version, outcome) "
        "WHERE outcome IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE pipeline.allowed_move (
          list_version  text NOT NULL,
          from_step     text NOT NULL,
          to_step       text NOT NULL,
          PRIMARY KEY (list_version, from_step, to_step),
          FOREIGN KEY (list_version, from_step) REFERENCES pipeline.step (list_version, code),
          FOREIGN KEY (list_version, to_step) REFERENCES pipeline.step (list_version, code),
          CHECK (from_step <> to_step)
        )
        """
    )
    op.execute(
        r"""
        CREATE TABLE pipeline.rejection_reason (
          list_version  text NOT NULL REFERENCES pipeline.step_list (version),
          code          text NOT NULL CHECK (code ~ '^[a-z][a-z_]{0,59}$'),
          label         text NOT NULL CHECK (label ~ '\S'),
          PRIMARY KEY (list_version, code)
        )
        """
    )
    op.execute(
        r"""
        CREATE TABLE pipeline.list_activation (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          list_version  text        NOT NULL REFERENCES pipeline.step_list (version),
          activated_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
          activated_by  text        NOT NULL CHECK (activated_by ~ '\S')
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION pipeline.active_list() RETURNS text LANGUAGE sql STABLE AS $$
          SELECT list_version FROM pipeline.list_activation
          ORDER BY activated_at DESC, id DESC LIMIT 1
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION pipeline.check_allowed_move() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pipeline.step
            WHERE list_version = NEW.list_version AND code = NEW.from_step AND outcome IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'list %: % is final, so no move may leave it',
              NEW.list_version, NEW.from_step USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER allowed_move_not_from_final BEFORE INSERT ON pipeline.allowed_move "
        "FOR EACH ROW EXECUTE FUNCTION pipeline.check_allowed_move()"
    )
    op.execute(
        """
        CREATE FUNCTION pipeline.check_activation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pipeline.step
                         WHERE list_version = NEW.list_version AND outcome = 'hired')
             OR NOT EXISTS (SELECT 1 FROM pipeline.step
                            WHERE list_version = NEW.list_version AND outcome = 'rejected') THEN
            RAISE EXCEPTION 'list %: needs a hired step and a rejected step', NEW.list_version
              USING ERRCODE = 'check_violation';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pipeline.rejection_reason
                         WHERE list_version = NEW.list_version) THEN
            RAISE EXCEPTION 'list %: needs at least one rejection reason', NEW.list_version
              USING ERRCODE = 'check_violation';
          END IF;
          NEW.activated_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER list_activation_complete BEFORE INSERT ON pipeline.list_activation "
        "FOR EACH ROW EXECUTE FUNCTION pipeline.check_activation()"
    )

    # --- Openings -------------------------------------------------------------------------------
    op.execute(
        r"""
        CREATE TABLE pipeline.opening (
          id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          brand                text        NOT NULL CHECK (brand ~ '\S'),
          department           text        NOT NULL CHECK (department ~ '\S'),
          track                text        NOT NULL CHECK (track IN ('A', 'B')),
          headcount            integer     NOT NULL CHECK (headcount > 0),
          status               text        NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'closed')),
          owner_recruiter      text        NOT NULL CHECK (owner_recruiter ~ '\S'),
          team                 text        NOT NULL CHECK (team ~ '\S'),
          criteria_version_id  text        NOT NULL REFERENCES core.criteria_version (id),
          created_at           timestamptz NOT NULL DEFAULT clock_timestamp(),
          created_by           text        NOT NULL CHECK (created_by ~ '\S'),
          closed_at            timestamptz,
          closed_reason        text,
          closed_by            text,
          CONSTRAINT opening_closed_with_reason CHECK (
            (status = 'open' AND closed_at IS NULL AND closed_reason IS NULL AND closed_by IS NULL)
            OR (
              status = 'closed' AND closed_at IS NOT NULL
              AND closed_reason IS NOT NULL AND closed_reason ~ '\S'
              AND closed_by IS NOT NULL AND closed_by ~ '\S'
            )
          )
        )
        """
    )
    op.execute("CREATE INDEX opening_owner ON pipeline.opening (owner_recruiter)")
    op.execute(
        """
        CREATE FUNCTION pipeline.guard_opening() RETURNS trigger LANGUAGE plpgsql AS $$
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
              NEW.criteria_version_id, NEW.created_at, NEW.created_by)
             IS DISTINCT FROM
             (OLD.brand, OLD.department, OLD.track, OLD.headcount, OLD.owner_recruiter, OLD.team,
              OLD.criteria_version_id, OLD.created_at, OLD.created_by) THEN
            RAISE EXCEPTION 'opening %: closing is the only change an opening takes', OLD.id
              USING ERRCODE = 'insufficient_privilege';
          END IF;
          IF NEW.status = 'closed' THEN
            NEW.closed_at := clock_timestamp();
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER opening_guard BEFORE UPDATE OR DELETE ON pipeline.opening "
        "FOR EACH ROW EXECUTE FUNCTION pipeline.guard_opening()"
    )
    op.execute(
        "CREATE TRIGGER opening_no_truncate BEFORE TRUNCATE ON pipeline.opening "
        "FOR EACH STATEMENT EXECUTE FUNCTION pipeline.guard_opening()"
    )

    # --- Applications ---------------------------------------------------------------------------
    op.execute(
        r"""
        CREATE TABLE pipeline.application (
          id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          opening_id              bigint      NOT NULL REFERENCES pipeline.opening (id),
          candidate_id            bigint      NOT NULL REFERENCES core.candidate (id),
          owner_recruiter         text        NOT NULL CHECK (owner_recruiter ~ '\S'),
          team                    text        NOT NULL CHECK (team ~ '\S'),
          reopens_application_id  bigint      UNIQUE REFERENCES pipeline.application (id),
          created_at              timestamptz NOT NULL DEFAULT clock_timestamp(),
          created_by              text        NOT NULL CHECK (created_by ~ '\S')
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX application_once_per_opening ON pipeline.application "
        "(candidate_id, opening_id) WHERE reopens_application_id IS NULL"
    )
    op.execute("CREATE INDEX application_owner ON pipeline.application (owner_recruiter)")

    # --- Moves ----------------------------------------------------------------------------------
    op.execute(
        r"""
        CREATE TABLE pipeline.move (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          application_id  bigint      NOT NULL REFERENCES pipeline.application (id),
          sequence        integer     NOT NULL CHECK (sequence > 0),
          list_version    text        NOT NULL,
          from_step       text,
          to_step         text        NOT NULL,
          reason_code     text,
          actor_kind      text        NOT NULL CHECK (actor_kind IN ('person', 'system')),
          moved_by        text        NOT NULL CHECK (moved_by ~ '\S'),
          moved_at        timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE (application_id, sequence),
          FOREIGN KEY (list_version, to_step) REFERENCES pipeline.step (list_version, code),
          FOREIGN KEY (list_version, reason_code)
            REFERENCES pipeline.rejection_reason (list_version, code),
          CHECK ((sequence = 1) = (from_step IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION pipeline.check_move() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          active         text := pipeline.active_list();
          last_step      text;
          last_sequence  integer;
          last_outcome   text;
          first_step     text;
          to_outcome     text;
        BEGIN
          -- One mover at a time per application; the unique sequence is the backstop.
          PERFORM pg_advisory_xact_lock(NEW.application_id);
          IF NEW.list_version IS NULL THEN
            NEW.list_version := active;
          ELSIF NEW.list_version IS DISTINCT FROM active THEN
            RAISE EXCEPTION 'moves follow the active step list %, not %', active, NEW.list_version
              USING ERRCODE = 'check_violation';
          END IF;

          SELECT outcome INTO to_outcome FROM pipeline.step
          WHERE list_version = active AND code = NEW.to_step;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'step % is not on the active step list %', NEW.to_step, active
              USING ERRCODE = 'check_violation';
          END IF;

          SELECT m.to_step, m.sequence, s.outcome INTO last_step, last_sequence, last_outcome
          FROM pipeline.move m
          LEFT JOIN pipeline.step s ON s.list_version = m.list_version AND s.code = m.to_step
          WHERE m.application_id = NEW.application_id
          ORDER BY m.sequence DESC LIMIT 1;

          IF last_sequence IS NULL THEN
            SELECT code INTO first_step FROM pipeline.step
            WHERE list_version = active ORDER BY position LIMIT 1;
            IF NEW.from_step IS NOT NULL OR NEW.to_step IS DISTINCT FROM first_step THEN
              RAISE EXCEPTION 'application %: the first move is to %', NEW.application_id,
                first_step USING ERRCODE = 'check_violation';
            END IF;
            NEW.sequence := 1;
          ELSE
            IF NEW.from_step IS DISTINCT FROM last_step THEN
              RAISE EXCEPTION 'application % is at %, not %', NEW.application_id, last_step,
                coalesce(NEW.from_step, 'no step') USING ERRCODE = 'check_violation';
            END IF;
            IF last_outcome IS NOT NULL THEN
              RAISE EXCEPTION 'application %: % is final, nothing moves out of it',
                NEW.application_id, last_step USING ERRCODE = 'check_violation';
            END IF;
            IF NOT EXISTS (
              SELECT 1 FROM pipeline.allowed_move
              WHERE list_version = active AND from_step = last_step AND to_step = NEW.to_step
            ) THEN
              RAISE EXCEPTION 'application %: % to % is not an allowed move', NEW.application_id,
                last_step, NEW.to_step USING ERRCODE = 'check_violation';
            END IF;
            NEW.sequence := last_sequence + 1;
          END IF;

          IF to_outcome = 'rejected' THEN
            IF NEW.actor_kind IS DISTINCT FROM 'person' THEN
              RAISE EXCEPTION 'application %: only a person rejects (BR-405)', NEW.application_id
                USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.reason_code IS NULL OR NOT EXISTS (
              SELECT 1 FROM pipeline.rejection_reason
              WHERE list_version = active AND code = NEW.reason_code
            ) THEN
              RAISE EXCEPTION 'application %: a rejection needs a reason from the list (BR-404)',
                NEW.application_id USING ERRCODE = 'check_violation';
            END IF;
          ELSIF NEW.reason_code IS NOT NULL THEN
            RAISE EXCEPTION 'application %: only a rejection carries a reason', NEW.application_id
              USING ERRCODE = 'check_violation';
          END IF;

          NEW.moved_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER move_allowed BEFORE INSERT ON pipeline.move "
        "FOR EACH ROW EXECUTE FUNCTION pipeline.check_move()"
    )

    # --- Reversals, reviews ---------------------------------------------------------------------
    op.execute(
        r"""
        CREATE TABLE pipeline.reversal (
          id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          application_id     bigint      NOT NULL REFERENCES pipeline.application (id),
          rejection_move_id  bigint      NOT NULL UNIQUE REFERENCES pipeline.move (id),
          label              text        NOT NULL DEFAULT 'rejection_reversed'
            CHECK (label IN ('rejection_reversed')),
          reason             text        NOT NULL CHECK (reason ~ '\S'),
          reversed_by        text        NOT NULL CHECK (reversed_by ~ '\S'),
          reversed_at        timestamptz NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION pipeline.check_reversal() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          latest_id       bigint;
          latest_outcome  text;
        BEGIN
          SELECT m.id, s.outcome INTO latest_id, latest_outcome
          FROM pipeline.move m
          JOIN pipeline.step s ON s.list_version = m.list_version AND s.code = m.to_step
          WHERE m.application_id = NEW.application_id
          ORDER BY m.sequence DESC LIMIT 1;
          IF latest_id IS DISTINCT FROM NEW.rejection_move_id
             OR latest_outcome IS DISTINCT FROM 'rejected' THEN
            RAISE EXCEPTION 'application %: only its rejection, as its latest move, is reversed',
              NEW.application_id USING ERRCODE = 'check_violation';
          END IF;
          NEW.reversed_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER reversal_of_a_rejection BEFORE INSERT ON pipeline.reversal "
        "FOR EACH ROW EXECUTE FUNCTION pipeline.check_reversal()"
    )

    op.execute(
        """
        CREATE FUNCTION pipeline.check_application() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          opening_status  text;
          old_candidate   bigint;
          old_opening     bigint;
        BEGIN
          SELECT status INTO opening_status FROM pipeline.opening WHERE id = NEW.opening_id;
          IF opening_status = 'closed' THEN
            RAISE EXCEPTION 'opening %: closed, so it takes no applications', NEW.opening_id
              USING ERRCODE = 'check_violation';
          END IF;
          IF EXISTS (
            SELECT 1 FROM core.candidate c JOIN raw.capture r ON r.id = c.capture_id
            WHERE c.id = NEW.candidate_id AND r.source = 'tai_master'
          ) THEN
            RAISE EXCEPTION 'candidate %: migrated candidates get no applications until OPN-11 '
              'is ruled', NEW.candidate_id USING ERRCODE = 'check_violation';
          END IF;
          IF NEW.reopens_application_id IS NOT NULL THEN
            SELECT candidate_id, opening_id INTO old_candidate, old_opening
            FROM pipeline.application WHERE id = NEW.reopens_application_id;
            IF (old_candidate, old_opening) IS DISTINCT FROM (NEW.candidate_id, NEW.opening_id) THEN
              RAISE EXCEPTION 'application %: a reopening is for the same candidate and opening',
                NEW.reopens_application_id USING ERRCODE = 'check_violation';
            END IF;
            IF NOT EXISTS (
              SELECT 1 FROM pipeline.reversal WHERE application_id = NEW.reopens_application_id
            ) THEN
              RAISE EXCEPTION 'application %: reopening needs a recorded reversal (BR-406)',
                NEW.reopens_application_id USING ERRCODE = 'check_violation';
            END IF;
          END IF;
          NEW.created_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER application_allowed BEFORE INSERT ON pipeline.application "
        "FOR EACH ROW EXECUTE FUNCTION pipeline.check_application()"
    )
    op.execute(
        """
        CREATE FUNCTION pipeline.start_application() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          INSERT INTO pipeline.move (application_id, from_step, to_step, actor_kind, moved_by)
          VALUES (
            NEW.id, NULL,
            (SELECT code FROM pipeline.step
             WHERE list_version = pipeline.active_list() ORDER BY position LIMIT 1),
            'person', NEW.created_by
          );
          RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER application_starts_with_a_move AFTER INSERT ON pipeline.application "
        "FOR EACH ROW EXECUTE FUNCTION pipeline.start_application()"
    )

    op.execute(
        r"""
        CREATE TABLE pipeline.review_item (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          application_id  bigint      NOT NULL REFERENCES pipeline.application (id),
          kind            text        NOT NULL DEFAULT 'proposed_rejection'
            CHECK (kind IN ('proposed_rejection')),
          list_version    text        NOT NULL,
          at_step         text        NOT NULL,
          reason_code     text        NOT NULL,
          proposed_by     text        NOT NULL CHECK (proposed_by ~ '\S'),
          proposed_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
          FOREIGN KEY (list_version, reason_code)
            REFERENCES pipeline.rejection_reason (list_version, code)
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION pipeline.check_review_item() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          current_step     text;
          current_outcome  text;
        BEGIN
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
          NEW.proposed_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER review_item_on_an_open_application BEFORE INSERT ON pipeline.review_item "
        "FOR EACH ROW EXECUTE FUNCTION pipeline.check_review_item()"
    )
    op.execute(
        r"""
        CREATE TABLE pipeline.review_resolution (
          id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          review_item_id  bigint      NOT NULL UNIQUE REFERENCES pipeline.review_item (id),
          outcome         text        NOT NULL CHECK (outcome IN ('confirmed', 'dismissed')),
          move_id         bigint      UNIQUE REFERENCES pipeline.move (id),
          reason          text,
          resolved_by     text        NOT NULL CHECK (resolved_by ~ '\S'),
          resolved_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
          CHECK ((outcome = 'confirmed') = (move_id IS NOT NULL)),
          CHECK (outcome = 'confirmed' OR (reason IS NOT NULL AND reason ~ '\S'))
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION pipeline.check_resolution() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.outcome = 'confirmed' AND NOT EXISTS (
            SELECT 1
            FROM pipeline.review_item r
            JOIN pipeline.move m ON m.id = NEW.move_id AND m.application_id = r.application_id
            JOIN pipeline.step s ON s.list_version = m.list_version AND s.code = m.to_step
            WHERE r.id = NEW.review_item_id
              AND s.outcome = 'rejected'
              AND m.actor_kind = 'person'
              AND m.moved_by = NEW.resolved_by
              AND m.reason_code = r.reason_code
          ) THEN
            RAISE EXCEPTION 'review item %: confirming needs the rejection move of the person '
              'confirming, with the proposed reason', NEW.review_item_id
              USING ERRCODE = 'check_violation';
          END IF;
          NEW.resolved_at := clock_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER review_resolution_by_a_person BEFORE INSERT ON pipeline.review_resolution "
        "FOR EACH ROW EXECUTE FUNCTION pipeline.check_resolution()"
    )

    # --- Views ----------------------------------------------------------------------------------
    op.execute(
        """
        CREATE VIEW pipeline.application_state AS
        SELECT a.id, a.opening_id, a.candidate_id, a.owner_recruiter, a.team,
               a.reopens_application_id, a.created_at, a.created_by,
               m.to_step AS current_step, m.sequence AS moves, m.moved_at AS step_since,
               m.moved_by AS step_by, s.outcome
        FROM pipeline.application a
        JOIN LATERAL (
          SELECT to_step, sequence, moved_at, moved_by, list_version
          FROM pipeline.move WHERE application_id = a.id
          ORDER BY sequence DESC LIMIT 1
        ) m ON true
        JOIN pipeline.step s ON s.list_version = m.list_version AND s.code = m.to_step
        """
    )
    op.execute(
        """
        CREATE VIEW pipeline.override_signal AS
        SELECT 'proposed_rejection_dismissed'::text AS label, i.application_id, i.reason_code,
               x.reason, x.resolved_by AS decided_by, x.resolved_at AS decided_at
        FROM pipeline.review_resolution x
        JOIN pipeline.review_item i ON i.id = x.review_item_id
        WHERE x.outcome = 'dismissed'
        UNION ALL
        SELECT v.label, v.application_id, m.reason_code, v.reason, v.reversed_by, v.reversed_at
        FROM pipeline.reversal v
        JOIN pipeline.move m ON m.id = v.rejection_move_id
        """
    )

    # --- Append-only, for every role ------------------------------------------------------------
    for table in _APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON pipeline.{table} "
            "FOR EACH ROW EXECUTE FUNCTION pipeline.refuse_change()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON pipeline.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION pipeline.refuse_change()"
        )

    # 0001's default privileges gave pipeline SELECT, INSERT, UPDATE on every table and view.
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA pipeline FROM talent_rw")
    op.execute("GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA pipeline TO talent_rw")
    op.execute(
        "REVOKE INSERT ON pipeline.application_state, pipeline.override_signal FROM talent_rw"
    )
    op.execute(
        "GRANT UPDATE (status, closed_reason, closed_by, closed_at) ON pipeline.opening "
        "TO talent_rw"
    )

    # --- The provisional list -------------------------------------------------------------------
    op.execute(
        "INSERT INTO pipeline.step_list (version, provisional, source, loaded_by) VALUES "
        f"({_literal(PROVISIONAL)}, true, "
        "'BRD BR-402 stage order, provisional until TA sends the final list', 'migration 0005')"
    )
    steps = ", ".join(
        f"({_literal(PROVISIONAL)}, {_literal(code)}, {_literal(label)}, {position}, "
        f"{_literal(outcome)})"
        for position, (code, label, outcome) in enumerate(PROVISIONAL_STEPS, start=1)
    )
    op.execute(
        f"INSERT INTO pipeline.step (list_version, code, label, position, outcome) VALUES {steps}"
    )
    open_steps = [code for code, _label, outcome in PROVISIONAL_STEPS if outcome is None]
    forward = list(zip(open_steps, [*open_steps[1:], "hired"], strict=True))
    to_rejected = [(code, "rejected") for code in open_steps]
    moves = ", ".join(
        f"({_literal(PROVISIONAL)}, {_literal(a)}, {_literal(b)})" for a, b in forward + to_rejected
    )
    op.execute(
        f"INSERT INTO pipeline.allowed_move (list_version, from_step, to_step) VALUES {moves}"
    )
    reasons = ", ".join(
        f"({_literal(PROVISIONAL)}, {_literal(code)}, {_literal(label)})"
        for code, label in PROVISIONAL_REASONS
    )
    op.execute(
        f"INSERT INTO pipeline.rejection_reason (list_version, code, label) VALUES {reasons}"
    )
    op.execute(
        "INSERT INTO pipeline.list_activation (list_version, activated_by) VALUES "
        f"({_literal(PROVISIONAL)}, 'migration 0005')"
    )


def downgrade() -> None:
    raise RuntimeError("0005 is not reversible: dropping pipeline would destroy recorded moves.")
