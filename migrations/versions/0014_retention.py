"""Keeping a candidate's data only as long as we may (week 7: CR-03, and BR-205 stands).

  core.retention_policy    how long we keep a candidate's data, by what happened to them. Versioned
  core.retention_rule      and never edited, like the step list and the consent wording: a change is
  core.retention_activation a new version, and the one in force is the latest activation.
  core.candidate_retention which rule applies to each candidate, when their clock last restarted,
                           and the day their data is due to go. Empty while no policy is in force.
  core.erasure             what was erased, when, under which policy, and by whom. Counts only.

Erasure is the one place where data leaves this system, and it is a door with a lock on it:
core.erase_candidate runs as the owner, sets a flag the append-only triggers look for, and records
what it did. Nothing else can delete a field, a capture or a reading — the app role has no DELETE
grant at all, and the triggers refuse the owner too unless it is inside that function.

What goes: every stored field value, the original files, the OCR's answer, and the quoted text
inside an evaluation ("Near New Cairo: Nasr City"). What stays: the record itself, marked erased,
its score and tier, the steps it moved through, the consent it gave and the fact of the erasure —
none of which says who the person was, and all of which last quarter's numbers are built from.

No periods are set here. Legal owes us OPN-07; until a policy is activated, core.retention_in_force
returns nothing and the eraser refuses to run.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-17
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# What happened to the candidate, which decides how long we keep them.
APPLIES_TO = ("no_application", "in_process", "rejected", "hired", "withdrawn")
ERASURE_REASONS = ("retention", "request")
# The tables whose rows hold what a candidate told us. Erasure empties these and nothing else.
ERASING = "talent.erasing"


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE core.retention_policy (
          version     text        PRIMARY KEY CHECK (version ~ '^[a-z0-9][a-z0-9._-]{2,60}$'),
          provisional boolean     NOT NULL DEFAULT true,
          source      text        NOT NULL CHECK (source ~ '\S'),
          note        text,
          loaded_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
          loaded_by   text        NOT NULL CHECK (loaded_by ~ '\S')
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE core.retention_rule (
          policy_version text    NOT NULL REFERENCES core.retention_policy (version),
          applies_to     text    NOT NULL CHECK (applies_to IN
                           {tuple(APPLIES_TO)!r}),
          months         integer CHECK (months IS NULL OR months BETWEEN 1 AND 600),
          note           text,
          PRIMARY KEY (policy_version, applies_to)
        )
        """
    )
    op.execute(
        r"""
        CREATE TABLE core.retention_activation (
          id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          policy_version text        NOT NULL REFERENCES core.retention_policy (version),
          activated_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
          activated_by   text        NOT NULL CHECK (activated_by ~ '\S'),
          note           text
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION core.retention_in_force() RETURNS text LANGUAGE sql STABLE AS $$
          SELECT policy_version FROM core.retention_activation ORDER BY id DESC LIMIT 1
        $$
        """
    )
    op.execute(
        f"""
        CREATE TABLE core.erasure (
          id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          candidate_id   bigint      NOT NULL REFERENCES core.candidate (id),
          reason         text        NOT NULL CHECK (reason IN {tuple(ERASURE_REASONS)!r}),
          policy_version text        REFERENCES core.retention_policy (version),
          due_at         timestamptz,
          fields_erased  integer     NOT NULL DEFAULT 0 CHECK (fields_erased >= 0),
          files_erased   integer     NOT NULL DEFAULT 0 CHECK (files_erased >= 0),
          readings_erased integer    NOT NULL DEFAULT 0 CHECK (readings_erased >= 0),
          evaluations_cleared integer NOT NULL DEFAULT 0 CHECK (evaluations_cleared >= 0),
          erased_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
          erased_by      text        NOT NULL CHECK (erased_by ~ '\\S'),
          CHECK ((reason = 'retention') = (policy_version IS NOT NULL))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX erasure_once ON core.erasure (candidate_id)")
    for table in ("retention_policy", "retention_rule", "retention_activation", "erasure"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON core.{table} "
            "FOR EACH ROW EXECUTE FUNCTION core.refuse_change()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON core.{table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION core.refuse_change()"
        )
    op.execute(
        "GRANT SELECT ON core.retention_policy, core.retention_rule, core.retention_activation, "
        "core.erasure TO talent_rw"
    )

    # A capture whose file has been erased says so, rather than quietly pointing at bytes that
    # are gone. The key stays: it is a content hash, and it is how we know a file was there.
    op.execute("ALTER TABLE raw.capture ADD COLUMN erased_at timestamptz")

    # --- The door -------------------------------------------------------------------------------
    # The append-only triggers let a row go only inside core.erase_candidate: the flag below is set
    # there, and only the owner can be inside it, because the function is SECURITY DEFINER and the
    # app role has no DELETE grant on anything.
    for schema, message in (
        ("core", "core.% is immutable: % refused"),
        ("raw", "raw.% is append-only: % refused (BR-107)"),
        ("intake", "intake.% is append-only: % refused"),
    ):
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {schema}.refuse_change() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
              IF current_setting('{ERASING}', true) = 'on'
                 AND current_user = 'talent_owner' AND TG_OP IN ('UPDATE', 'DELETE') THEN
                RETURN CASE TG_OP WHEN 'DELETE' THEN OLD ELSE NEW END;
              END IF;
              RAISE EXCEPTION '{message}', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
            END
            $$
            """
        )

    op.execute(
        f"""
        CREATE FUNCTION core.erase_candidate(
          for_candidate bigint, why text, under_policy text, by_whom text,
          due_on timestamptz DEFAULT NULL
        ) RETURNS TABLE (
          fields_erased integer, files_erased integer, readings_erased integer,
          evaluations_cleared integer, blob_keys text[]
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
          keys text[];
          fields integer;
          files integer;
          readings integer;
          evaluations integer;
        BEGIN
          IF why NOT IN {tuple(ERASURE_REASONS)!r} THEN
            RAISE EXCEPTION 'erasure reason is one of {", ".join(ERASURE_REASONS)}';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM core.candidate WHERE id = for_candidate) THEN
            RAISE EXCEPTION 'no candidate %', for_candidate;
          END IF;
          PERFORM set_config('{ERASING}', 'on', true);

          -- Every capture this candidate's data sits in: the record itself, any CV uploaded, and
          -- the reader's answer to it.
          CREATE TEMP TABLE erasing_captures ON COMMIT DROP AS
            SELECT c.capture_id AS id FROM core.candidate c WHERE c.id = for_candidate
            UNION
            SELECT u.capture_id FROM intake.cv_upload u WHERE u.candidate_id = for_candidate
            UNION
            SELECT r.answer_capture_id FROM intake.cv_reading r
             WHERE r.answer_capture_id IS NOT NULL AND r.capture_id IN (
               SELECT u.capture_id FROM intake.cv_upload u WHERE u.candidate_id = for_candidate
             );

          SELECT coalesce(array_agg(k.blob_key), '{{}}') INTO keys
          FROM raw.capture k
          WHERE k.id IN (SELECT id FROM erasing_captures) AND k.blob_key IS NOT NULL;

          DELETE FROM core.candidate_field WHERE candidate_id = for_candidate;
          GET DIAGNOSTICS fields = ROW_COUNT;

          -- The file itself is deleted from the store by the caller. The row keeps its content
          -- hash, which says nothing about anyone once the bytes are gone, and erased_at says the
          -- file is no longer there: the capture is still the evidence that it once was (BR-107).
          UPDATE raw.capture SET erased_at = clock_timestamp()
           WHERE id IN (SELECT id FROM erasing_captures) AND erased_at IS NULL;
          GET DIAGNOSTICS files = ROW_COUNT;

          UPDATE intake.cv_reading SET answer_capture_id = NULL
           WHERE answer_capture_id IN (SELECT id FROM erasing_captures);
          GET DIAGNOSTICS readings = ROW_COUNT;

          -- A verdict keeps its score and tier; the words around it quote the person.
          UPDATE core.evaluation
             SET signals = '{{}}', flags = '{{}}', signals_text = NULL, flags_text = NULL
           WHERE candidate_id = for_candidate
             AND (signals <> '{{}}' OR flags <> '{{}}' OR signals_text IS NOT NULL
                  OR flags_text IS NOT NULL);
          GET DIAGNOSTICS evaluations = ROW_COUNT;

          UPDATE core.candidate
             SET archived_at = coalesce(archived_at, clock_timestamp()),
                 archived_reason = coalesce(archived_reason, 'erased: ' || why),
                 archived_by = coalesce(archived_by, by_whom)
           WHERE id = for_candidate;

          INSERT INTO core.erasure
            (candidate_id, reason, policy_version, due_at, fields_erased, files_erased,
             readings_erased, evaluations_cleared, erased_by)
          VALUES (for_candidate, why, under_policy, due_on, fields, files, readings, evaluations,
                  by_whom);

          PERFORM set_config('{ERASING}', 'off', true);
          RETURN QUERY SELECT fields, files, readings, evaluations, keys;
        END
        $$
        """
    )

    # Postgres gives EXECUTE on a new function to everyone. Not this one: erasure is the owner's.
    op.execute(
        "REVOKE EXECUTE ON FUNCTION core.erase_candidate(bigint, text, text, text, timestamptz) "
        "FROM PUBLIC"
    )

    # --- Whose time is up -----------------------------------------------------------------------
    op.execute(
        """
        CREATE VIEW core.candidate_retention AS
        WITH last_touch AS (
          SELECT c.id AS candidate_id,
                 greatest(
                   c.created_at,
                   coalesce((SELECT max(a.created_at) FROM pipeline.application a
                              WHERE a.candidate_id = c.id), c.created_at),
                   coalesce((SELECT max(m.moved_at) FROM pipeline.move m
                              JOIN pipeline.application a ON a.id = m.application_id
                             WHERE a.candidate_id = c.id), c.created_at),
                   coalesce((SELECT max(e.recorded_at) FROM core.evaluation e
                              WHERE e.candidate_id = c.id), c.created_at),
                   coalesce((SELECT max(n.received_at) FROM core.consent n
                              WHERE n.candidate_id = c.id), c.created_at)
                 ) AS last_activity_at,
                 (SELECT count(*) FROM pipeline.application a WHERE a.candidate_id = c.id) AS apps,
                 EXISTS (SELECT 1 FROM core.candidate_locked l WHERE l.candidate_id = c.id)
                   AS withdrawn,
                 EXISTS (
                   SELECT 1 FROM pipeline.application_state s
                    WHERE s.candidate_id = c.id AND s.outcome = 'hired'
                 ) AS hired,
                 EXISTS (
                   SELECT 1 FROM pipeline.application_state s
                    WHERE s.candidate_id = c.id AND s.outcome IS NOT NULL
                 ) AS finished
          FROM core.candidate c
        )
        SELECT t.candidate_id,
               t.last_activity_at,
               CASE
                 WHEN t.withdrawn THEN 'withdrawn'
                 WHEN t.hired THEN 'hired'
                 WHEN t.apps = 0 THEN 'no_application'
                 WHEN t.finished THEN 'rejected'
                 ELSE 'in_process'
               END AS applies_to,
               r.months,
               CASE WHEN r.months IS NULL THEN NULL
                    ELSE t.last_activity_at + make_interval(months => r.months)
               END AS due_at,
               EXISTS (SELECT 1 FROM core.erasure e WHERE e.candidate_id = t.candidate_id)
                 AS erased
        FROM last_touch t
        LEFT JOIN core.retention_rule r
          ON r.policy_version = core.retention_in_force()
         AND r.applies_to = CASE
               WHEN t.withdrawn THEN 'withdrawn'
               WHEN t.hired THEN 'hired'
               WHEN t.apps = 0 THEN 'no_application'
               WHEN t.finished THEN 'rejected'
               ELSE 'in_process'
             END
        """
    )
    op.execute("GRANT SELECT ON core.candidate_retention TO talent_rw")


def downgrade() -> None:
    raise RuntimeError("0014 is not reversible: an erasure cannot be taken back.")
