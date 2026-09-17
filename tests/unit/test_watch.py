"""What counts as trouble, and what an alert may say (NFR-05). No database here."""

import json

from ops.watch import CRITICAL, OK, WARN, Check, alert_text, backup_freshness, overall, report


def test_the_worst_check_decides_the_answer():
    checks = [Check("a", OK, "fine"), Check("b", WARN, "hmm"), Check("c", OK, "fine")]
    assert overall(checks) == WARN
    assert overall([*checks, Check("d", CRITICAL, "no")]) == CRITICAL
    assert overall([]) == OK


def test_no_backup_at_all_is_critical(tmp_path):
    check = backup_freshness(tmp_path)
    assert check.status == CRITICAL
    assert "no backup" in check.detail


def test_a_backup_nobody_restored_is_critical_however_fresh_it_is(tmp_path):
    (tmp_path / "talent-20260917T000000Z.dump").write_bytes(b"x")
    (tmp_path / "talent-20260917T000000Z.json").write_text(
        json.dumps({"restored": None}), encoding="utf-8"
    )
    check = backup_freshness(tmp_path)
    assert check.status == CRITICAL
    assert "NOT restored" in check.detail

    (tmp_path / "talent-20260917T000000Z.json").write_text(
        json.dumps({"restored": {"ok": True}}), encoding="utf-8"
    )
    assert backup_freshness(tmp_path).status == OK


def test_backups_that_are_not_watched_say_so(tmp_path):
    assert backup_freshness(None).status == WARN
    assert "TALENT_BACKUP_DIR" in backup_freshness(None).detail


def test_an_alert_carries_the_failing_checks_and_where_to_look():
    checks = [
        Check("queue_waiting", CRITICAL, "812 job(s) waiting, the oldest for 140.0 minutes"),
        Check("backup", OK, "newest backup is 3.0h old and was restored as a check"),
    ]
    message = alert_text(checks, "production")
    assert "CRITICAL on production" in message
    assert "812 job(s) waiting" in message
    assert "RUNBOOK" in message
    # A check that is fine is not worth waking anyone for.
    assert "newest backup" not in message


def test_the_report_is_plain_data_a_monitoring_agent_can_read():
    body = report([Check("queue_waiting", OK, "0 job(s) waiting", {"waiting": 0})])
    assert body["status"] == OK
    assert body["checks"][0] == {
        "check": "queue_waiting",
        "status": OK,
        "detail": "0 job(s) waiting",
        "numbers": {"waiting": 0},
    }
    assert json.dumps(body)  # nothing in it that cannot be sent
