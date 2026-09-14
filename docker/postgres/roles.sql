-- Database roles. Run once per database, as the owner:
--   psql -v ON_ERROR_STOP=1 -v app_password=... -f roles.sql
--
-- talent_rw   holds the grants (set by migrations). Cannot log in.
-- talent_app  the login the API uses. Member of talent_rw, owns nothing,
--             so it cannot step around the grants.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'talent_rw') THEN
    CREATE ROLE talent_rw NOLOGIN;
  END IF;
END $$;

SELECT 'CREATE ROLE talent_app LOGIN IN ROLE talent_rw'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'talent_app') \gexec

ALTER ROLE talent_app PASSWORD :'app_password';
