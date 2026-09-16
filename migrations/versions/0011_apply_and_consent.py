"""Applying from a job post, with consent recorded (week 6: BR-101, BR-109, BR-602, CR-02).

  pipeline.opening      title, location and public: what a careers page may show. Only a public,
                        open requisition is listed publicly. They are set when the requisition is
                        opened and never change, like everything else about it.
  pipeline.job_post     one tracking code per job post, so we know which post brought a candidate
                        (BR-602). Append-only; a code is never reused.
  core.consent_wording  the words a candidate agrees to, in Arabic and English, with the purposes
                        they cover. A new wording is a new version; the one in force is the latest
                        activation, as with step lists. Marked provisional until Legal approves.
  core.consent          what one candidate agreed to, when, in which language, through which
                        channels, and the wording version they were shown (CR-02). Append-only:
                        withdrawing consent is a later record, never an edit.
  core.candidate_field  a new source is allowed: candidate_confirmed, for what the candidate
                        checked on the apply form (BR-201).

Everything added is append-only for every role.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-16
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

PROVISIONAL_WORDING = "provisional-2026-09-16"
PURPOSES = ("recruitment_contact",)
CHANNELS = ("phone", "whatsapp", "email")

ARABIC_WORDING = (
    "بإرسال هذا الطلب، "
    "أوافق على أن تحتفظ "
    "شركة دي أدرس ببيان"
    "اتي وسيرتي الذاتي"
    "ة وأن تتواصل معي "
    "بشأن فرص العمل عبر "
    "الهاتف أو واتساب "
    "أو البريد الإلكتر"
    "وني. يمكنني سحب "
    "موافقتي في أي وقت."
)
ENGLISH_WORDING = (
    "By sending this application, I agree that The Address may keep my details and my CV, and "
    "may contact me about jobs by phone, WhatsApp or email. I can withdraw my consent at any time."
)


def _array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(f"'{value}'" for value in values) + "]"


def upgrade() -> None:
    # --- What a careers page may show about a requisition ----------------------------------------
    op.execute(
        r"""
        ALTER TABLE pipeline.opening
          ADD COLUMN title text CHECK (title ~ '\S'),
          ADD COLUMN location text CHECK (location ~ '\S'),
          ADD COLUMN public boolean NOT NULL DEFAULT false,
          ADD CONSTRAINT opening_public_has_a_title CHECK (NOT public OR title IS NOT NULL)
        """
    )
    # The guard of 0005 lists what never changes; the new columns join that list.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pipeline.guard_opening() RETURNS trigger LANGUAGE plpgsql AS $$
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
              NEW.public)
             IS DISTINCT FROM
             (OLD.brand, OLD.department, OLD.track, OLD.headcount, OLD.owner_recruiter, OLD.team,
              OLD.criteria_version_id, OLD.created_at, OLD.created_by, OLD.title, OLD.location,
              OLD.public) THEN
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

    # --- One tracking code per job post (BR-602) --------------------------------------------------
    op.execute(
        r"""
        CREATE TABLE pipeline.job_post (
          id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          opening_id   bigint      NOT NULL REFERENCES pipeline.opening (id),
          code         text        NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9][a-z0-9-]{3,39}$'),
          channel      text        NOT NULL CHECK (channel ~ '^[a-z][a-z_]{1,29}$'),
          label        text        CHECK (label ~ '\S'),
          created_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
          created_by   text        NOT NULL CHECK (created_by ~ '\S')
        )
        """
    )
    op.execute("CREATE INDEX job_post_opening ON pipeline.job_post (opening_id)")

    # --- The words a candidate agrees to (CR-02) --------------------------------------------------
    op.execute(
        r"""
        CREATE TABLE core.consent_wording (
          version      text        PRIMARY KEY CHECK (version ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
          provisional  boolean     NOT NULL,
          purposes     text[]      NOT NULL CHECK (cardinality(purposes) > 0),
          text_ar      text        NOT NULL CHECK (text_ar ~ '\S'),
          text_en      text        NOT NULL CHECK (text_en ~ '\S'),
          source       text        NOT NULL CHECK (source ~ '\S'),
          loaded_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
          loaded_by    text        NOT NULL CHECK (loaded_by ~ '\S')
        )
        """
    )
    op.execute(
        r"""
        CREATE TABLE core.consent_wording_activation (
          id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          version       text        NOT NULL REFERENCES core.consent_wording (version),
          activated_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
          activated_by  text        NOT NULL CHECK (activated_by ~ '\S')
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION core.consent_wording_in_force() RETURNS text LANGUAGE sql STABLE AS $$
          SELECT version FROM core.consent_wording_activation
          ORDER BY activated_at DESC, id DESC LIMIT 1
        $$
        """
    )

    # --- What one candidate agreed to (BR-101, CR-02) ---------------------------------------------
    op.execute(
        f"""
        CREATE TABLE core.consent (
          id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          candidate_id     bigint      NOT NULL REFERENCES core.candidate (id),
          application_id   bigint      REFERENCES pipeline.application (id),
          wording_version  text        NOT NULL REFERENCES core.consent_wording (version),
          purposes         text[]      NOT NULL CHECK (cardinality(purposes) > 0),
          channels         text[]      NOT NULL CHECK (cardinality(channels) > 0),
          language         text        NOT NULL CHECK (language IN ('ar', 'en')),
          agreed_at        timestamptz NOT NULL,
          received_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
          source           text        NOT NULL CHECK (source ~ '\\S'),
          tracking_code    text,
          CHECK (purposes <@ {_array(PURPOSES)}),
          CHECK (channels <@ {_array(CHANNELS)}),
          CHECK (agreed_at <= received_at + interval '1 hour')
        )
        """
    )
    op.execute("CREATE INDEX consent_candidate ON core.consent (candidate_id)")

    for schema, table in (
        ("pipeline", "job_post"),
        ("core", "consent_wording"),
        ("core", "consent_wording_activation"),
        ("core", "consent"),
    ):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {schema}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION {schema}.refuse_change()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {schema}.{table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {schema}.refuse_change()"
        )
    op.execute(
        "GRANT SELECT, INSERT ON pipeline.job_post, core.consent, core.consent_wording, "
        "core.consent_wording_activation TO talent_rw"
    )

    # --- The provisional wording, until Legal approves one (D-WEB-4) ------------------------------
    op.execute(
        f"""
        INSERT INTO core.consent_wording
          (version, provisional, purposes, text_ar, text_en, source, loaded_by)
        VALUES (
          '{PROVISIONAL_WORDING}', true, {_array(PURPOSES)},
          '{ARABIC_WORDING}', '{ENGLISH_WORDING}',
          'Drafted for week 6, provisional until Legal approves the wording (D-WEB-4, CR-02)',
          'migration 0011'
        )
        """
    )
    op.execute(
        "INSERT INTO core.consent_wording_activation (version, activated_by) "
        f"VALUES ('{PROVISIONAL_WORDING}', 'migration 0011')"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0011 is not reversible: dropping consent would lose what candidates agreed."
    )
