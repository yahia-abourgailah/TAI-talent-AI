"""Week 8: the start-up check names what is missing or dangerous, and never prints a value."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ops.preflight import ERROR, WARN, check, exit_code, main, read_env_file
from reports.arrivals import channel_of, week_of

ROOT = Path(__file__).resolve().parents[2]
SECRET = "Sentinel-Secret-Value-" + "x" * 30

GOOD = {
    "TALENT_ENV": "prod",
    "TALENT_DB_DSN": "postgresql+psycopg://talent_app:pw@postgres:5432/talent",
    "TALENT_REDIS_URL": "redis://redis:6379/0",
    "TALENT_BLOB_ENDPOINT": "http://storage.internal:9000",
    "TALENT_BLOB_ACCESS_KEY": "made-up-access-key-1",
    "TALENT_BLOB_SECRET_KEY": SECRET,
    "TALENT_DB_OWNER_PASSWORD": SECRET,
    "TALENT_DB_APP_PASSWORD": SECRET,
    "TALENT_JWT_SECRET": SECRET,
    "TALENT_AUTH_MODE": "oidc",
    "TALENT_OIDC_ISSUER": "https://login.example.com/tenant/v2.0",
    "TALENT_OIDC_AUDIENCE": "talent-platform",
    "TALENT_OCR_MODE": "api",
    "TALENT_OCR_BASE_URL": "https://ocr.internal",
    "TALENT_OCR_API_KEY": SECRET,
    "TALENT_CORS_ORIGINS": "https://careers.example.com",
    "TALENT_TRUSTED_PROXY_HOPS": "1",
    "TALENT_BACKUP_DIR": "/var/backups/talent",
    "TALENT_CRM_WEBHOOK_URL": "https://crm.example.com/hook",
    "TALENT_CRM_WEBHOOK_SECRET": SECRET,
    "TALENT_ALERT_WEBHOOK_URL": "https://chat.internal/hook",
    "TALENT_VLLM_BASE_URL": "https://vllm.internal/v1",
    "TALENT_VLLM_API_KEY": SECRET,
}


def _errors(env, **kwargs) -> dict[str, str]:
    return {f.setting: f.message for f in check(env, **kwargs) if f.level == ERROR}


def test_a_complete_production_configuration_passes():
    findings = check(GOOD, api_bind="127.0.0.1")
    assert exit_code(findings, strict=True) == 0, findings


@pytest.mark.parametrize(
    ("change", "setting"),
    [
        ({"TALENT_AUTH_MODE": "dev"}, "TALENT_AUTH_MODE"),
        ({"TALENT_OIDC_ISSUER": ""}, "TALENT_OIDC_ISSUER"),
        ({"TALENT_OCR_MODE": "fake"}, "TALENT_OCR_MODE"),
        # A CV to a model over plain http is a CV on the wire (CR-01).
        ({"TALENT_VLLM_BASE_URL": "http://vllm.internal/v1"}, "TALENT_VLLM_BASE_URL"),
        ({"TALENT_OCR_BASE_URL": ""}, "TALENT_OCR_BASE_URL"),
        ({"TALENT_CORS_ORIGINS": ""}, "TALENT_CORS_ORIGINS"),
        ({"TALENT_CORS_ORIGINS": "*"}, "TALENT_CORS_ORIGINS"),
        ({"TALENT_CORS_ORIGINS": "http://careers.example.com"}, "TALENT_CORS_ORIGINS"),
        ({"TALENT_JWT_SECRET": "changeme"}, "TALENT_JWT_SECRET"),
        ({"TALENT_BLOB_SECRET_KEY": "minioadmin"}, "TALENT_BLOB_SECRET_KEY"),
        ({"TALENT_DB_APP_PASSWORD": "short"}, "TALENT_DB_APP_PASSWORD"),
        ({"TALENT_REDIS_URL": ""}, "TALENT_REDIS_URL"),
        ({"TALENT_OCR_MODE": ""}, "TALENT_OCR_MODE"),
    ],
)
def test_each_dangerous_setting_is_named(change, setting):
    errors = _errors({**GOOD, **change}, api_bind="127.0.0.1")
    assert setting in errors, errors
    assert exit_code(check({**GOOD, **change})) == 2


def test_a_missing_setting_is_named():
    env = {k: v for k, v in GOOD.items() if k != "TALENT_DB_DSN"}
    assert _errors(env)["TALENT_DB_DSN"] == "is not set"


def test_the_proxy_count_must_match_how_the_api_is_published():
    assert "TALENT_TRUSTED_PROXY_HOPS" in _errors(
        {**GOOD, "TALENT_TRUSTED_PROXY_HOPS": "0"}, api_bind="127.0.0.1"
    )
    assert "TALENT_TRUSTED_PROXY_HOPS" in _errors(GOOD, api_bind="0.0.0.0")
    assert "TALENT_TRUSTED_PROXY_HOPS" not in _errors(
        {**GOOD, "TALENT_TRUSTED_PROXY_HOPS": "0"}, api_bind="0.0.0.0"
    )


def test_unused_and_model_settings_are_warned_about():
    env = {**GOOD, "TALENT_EVAL_PATH": "", "TALENT_LLM_BASE_URL": "http://localhost:8061/v1"}
    warnings = {f.setting for f in check(env) if f.level == WARN}
    assert {"TALENT_EVAL_PATH", "TALENT_LLM_BASE_URL"} <= warnings
    assert exit_code(check(env)) == 0 and exit_code(check(env), strict=True) == 1


def test_on_a_dev_machine_gaps_are_warnings_not_errors():
    env = {**GOOD, "TALENT_ENV": "dev", "TALENT_AUTH_MODE": "dev", "TALENT_CORS_ORIGINS": ""}
    assert exit_code(check(env)) == 0


def test_the_output_never_holds_a_value(tmp_path, capsys):
    env_file = tmp_path / "talent.env"
    bad = {**GOOD, "TALENT_JWT_SECRET": "tiny", "TALENT_CORS_ORIGINS": "*"}
    env_file.write_text(
        "# a comment\n" + "\n".join(f"{k}={v}" for k, v in bad.items()) + "\n", encoding="utf-8"
    )
    assert read_env_file(env_file)["TALENT_OCR_BASE_URL"] == "https://ocr.internal"
    assert main(["--env-file", str(env_file)]) == 2
    printed = capsys.readouterr().out
    assert "TALENT_JWT_SECRET" in printed
    assert SECRET not in printed and "tiny" not in printed and "pw@" not in printed


def test_the_example_settings_file_does_not_stop_the_api_by_itself():
    findings = check(read_env_file(ROOT / ".env.example"))
    assert "TALENT_OCR_MODE" not in {f.setting for f in findings if f.level == ERROR}


@pytest.mark.parametrize(
    ("source", "post", "applied", "channel"),
    [
        ("public_apply", None, True, "careers_page"),
        ("cv_upload", None, True, "careers_page"),
        ("cv_upload", None, False, "cv_only"),
        ("cv_upload", "tiktok", True, "job_post:tiktok"),
        ("manual_entry", None, False, "recruiter_typed"),
        ("tai_master", None, False, "scraped_import"),
        ("something_new", None, False, "other"),
    ],
)
def test_each_arrival_has_one_channel(source, post, applied, channel):
    assert channel_of(source, post, applied) == channel


def test_weeks_start_on_monday():
    from datetime import date

    assert week_of(date(2026, 9, 17)) == "2026-09-14"
    assert week_of(date(2026, 9, 14)) == "2026-09-14"


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None, reason="needs a POSIX bash (CI runs it)"
)
@pytest.mark.parametrize("script", ["deploy.sh", "rollback.sh", "deploy-lib.sh"])
def test_the_deploy_scripts_parse(script):
    finished = subprocess.run(
        ["bash", "-n", str(ROOT / "scripts" / script)], capture_output=True, check=False
    )
    assert finished.returncode == 0, finished.stderr.decode()


def test_the_database_questions_are_not_asked_by_default():
    """The deploy runs the check before the database is even started, so a configuration check
    that needed one could never run first."""
    from ops.preflight import check

    settings = {
        "TALENT_ENV": "prod",
        "TALENT_DB_DSN": "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
    }
    # Nothing here can reach a database; the check still answers.
    assert [finding.setting for finding in check(settings) if finding.setting == "database"] == []


def test_a_database_nobody_can_reach_is_an_error_when_it_is_asked_about():
    from ops.preflight import ERROR, database_findings

    findings = database_findings(
        {"TALENT_DB_DSN": "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"}
    )
    assert findings[0].level == ERROR
    assert "cannot read it" in findings[0].message


def test_no_dsn_at_all_says_which_setting_is_missing():
    from ops.preflight import ERROR, database_findings

    (finding,) = database_findings({})
    assert (finding.level, finding.setting) == (ERROR, "TALENT_DB_DSN")


def test_no_model_is_a_warning_not_a_failure():
    """A job that is not sales then waits for a person, which is a working state, not a broken
    one (BR-305)."""
    findings = check(
        {**GOOD, "TALENT_VLLM_BASE_URL": "", "TALENT_VLLM_API_KEY": ""}, api_bind="127.0.0.1"
    )
    assert "TALENT_VLLM_BASE_URL" not in _errors({**GOOD, "TALENT_VLLM_BASE_URL": ""})
    assert any(f.setting == "TALENT_VLLM_BASE_URL" and f.level == WARN for f in findings)
