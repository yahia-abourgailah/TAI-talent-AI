"""The pipeline's rules, enforced by the database (BR-401 to BR-406).

Every test runs in a transaction that is rolled back. Candidates are made up and hold no personal
data.
"""

import re
import uuid
from itertools import pairwise

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pipeline.access import Actor, NotFound, Refused
from pipeline.dev_candidate import create_demo_candidate
from pipeline.lists import load_step_list
from pipeline.store import (
    active_step_list,
    close_opening,
    confirm_proposed_rejection,
    create_application,
    create_opening,
    dismiss_proposed_rejection,
    get_application,
    move_application,
    move_history,
    propose_rejection,
    reverse_rejection,
)

PROVISIONAL = "provisional-brd-2026-09"
PROPOSED = "proposed-2026-09-15"
STEPS = (
    "new",
    "contacted",
    "replied",
    "phone_screen",
    "hr_interview",
    "aptitude_test",
    "technical_interview",
    "offer",
    "hired",
    "rejected",
)
FORWARD = STEPS[:9]
OPEN_STEPS = STEPS[:8]
RECRUITER_A = Actor("dev|recruiter-a", sees_all=False)
REASON = "no_response"


@pytest.fixture
def conn(app_engine):
    with app_engine.connect() as connection:
        yield connection


@pytest.fixture
def owner_conn(owner_engine):
    with owner_engine.connect() as connection:
        yield connection


def _sqlstate(error: pytest.ExceptionInfo[DBAPIError]) -> str:
    return error.value.orig.sqlstate  # type: ignore[union-attr]


def _opening(conn, actor: Actor = RECRUITER_A) -> dict:
    return create_opening(
        conn,
        actor,
        brand="Made-up Brand",
        department="Sales",
        track="A",
        headcount=2,
        team="team-a",
    )


def _application(conn, actor: Actor = RECRUITER_A, opening: dict | None = None) -> dict:
    candidate = create_demo_candidate(conn, "integration-test", source="test-pipeline")
    opening = opening or _opening(conn, actor)
    return create_application(conn, actor, opening["id"], candidate)


def _walk_to(conn, application: dict, step: str, actor: Actor = RECRUITER_A) -> None:
    current = get_application(conn, actor, application["id"])["current_step"]
    while current != step:
        following = FORWARD[FORWARD.index(current) + 1]
        move_application(conn, actor, application["id"], from_step=current, to_step=following)
        current = following


def _raw_move(conn, application_id: int, from_step, to_step, reason=None, actor_kind="person"):
    """A move written straight to the table, past every line of Python."""
    with conn.begin_nested():
        conn.execute(
            text(
                "INSERT INTO pipeline.move "
                "(application_id, from_step, to_step, reason_code, actor_kind, moved_by) "
                "VALUES (:a, :f, :t, :r, :k, 'integration-test')"
            ),
            {"a": application_id, "f": from_step, "t": to_step, "r": reason, "k": actor_kind},
        )


def _refused(conn, *args, **kwargs) -> str:
    with pytest.raises(DBAPIError) as error:
        _raw_move(conn, *args, **kwargs)
    return _sqlstate(error)


# --- B2: the step list is data, and provisional ---------------------------------------------------


def test_the_proposed_list_is_in_force_and_still_marked_provisional(conn):
    steps = active_step_list(conn)
    assert (steps["version"], steps["provisional"]) == (PROPOSED, True)  # migration 0009
    assert tuple(step["code"] for step in steps["steps"]) == STEPS
    assert {s["code"]: s["outcome"] for s in steps["steps"]}["hired"] == "hired"
    moves = {(m["from_step"], m["to_step"]) for m in steps["moves"]}
    assert moves == set(pairwise(FORWARD)) | {(step, "rejected") for step in OPEN_STEPS}
    assert steps["rejection_reasons"]


def test_a_new_list_loads_and_applies_without_a_code_change(conn):
    version = f"test-{uuid.uuid4().hex[:8]}"
    load_step_list(
        conn,
        {
            "version": version,
            "source": "made-up list for a test",
            "steps": [
                {"code": "new", "label": "New"},
                {"code": "screen", "label": "Screen"},
                {"code": "hired", "label": "Hired", "outcome": "hired"},
                {"code": "rejected", "label": "Rejected", "outcome": "rejected"},
            ],
            "moves": [["new", "screen"], ["screen", "hired"], ["new", "rejected"]],
            "rejection_reasons": [{"code": "made_up_reason", "label": "Made up"}],
        },
        "integration-test",
    )
    assert active_step_list(conn)["version"] == version
    application = _application(conn)
    assert _refused(conn, application["id"], "new", "hired") == "23514"
    move_application(conn, RECRUITER_A, application["id"], from_step="new", to_step="screen")
    assert get_application(conn, RECRUITER_A, application["id"])["current_step"] == "screen"


def test_a_list_with_a_move_out_of_a_final_step_is_refused(conn):
    with pytest.raises(Refused, match="final"):
        load_step_list(
            conn,
            {
                "version": f"test-{uuid.uuid4().hex[:8]}",
                "source": "made-up list for a test",
                "steps": [
                    {"code": "new", "label": "New"},
                    {"code": "hired", "label": "Hired", "outcome": "hired"},
                    {"code": "rejected", "label": "Rejected", "outcome": "rejected"},
                ],
                "moves": [["new", "hired"], ["hired", "new"]],
                "rejection_reasons": [{"code": "made_up_reason", "label": "Made up"}],
            },
            "integration-test",
        )


def test_a_loaded_list_cannot_be_changed(conn):
    for statement in (
        "UPDATE pipeline.allowed_move SET to_step = 'hired' WHERE list_version = :v",
        "DELETE FROM pipeline.allowed_move WHERE list_version = :v",
        "DELETE FROM pipeline.rejection_reason WHERE list_version = :v",
    ):
        with pytest.raises(DBAPIError) as error, conn.begin_nested():
            conn.execute(text(statement), {"v": PROVISIONAL})
        assert _sqlstate(error) == "42501"


# --- B2 and B3: every allowed move works, everything else is refused ---


def test_a_new_application_starts_with_a_recorded_move_to_new(conn):
    application = _application(conn)
    assert (application["current_step"], application["moves"]) == ("new", 1)
    (first,) = move_history(conn, RECRUITER_A, application["id"])
    assert (first["from_step"], first["to_step"], first["moved_by"]) == (
        None,
        "new",
        RECRUITER_A.subject,
    )
    assert first["moved_at"] is not None


def test_every_allowed_move_works_all_the_way_to_hired(conn):
    application = _application(conn)
    _walk_to(conn, application, "hired")
    history = move_history(conn, RECRUITER_A, application["id"])
    assert tuple(move["to_step"] for move in history) == FORWARD
    assert [move["sequence"] for move in history] == list(range(1, 10))
    assert all(move["moved_by"] == RECRUITER_A.subject for move in history)
    assert get_application(conn, RECRUITER_A, application["id"])["outcome"] == "hired"


@pytest.mark.parametrize("step", OPEN_STEPS)
def test_every_open_step_can_move_to_rejected_with_a_listed_reason(conn, step):
    application = _application(conn)
    _walk_to(conn, application, step)
    move = move_application(
        conn, RECRUITER_A, application["id"], from_step=step, to_step="rejected", reason_code=REASON
    )
    assert (move["from_step"], move["reason_code"]) == (step, REASON)


@pytest.mark.parametrize("step", OPEN_STEPS)
def test_every_move_not_on_the_list_is_refused_by_the_database(conn, step):
    application = _application(conn)
    _walk_to(conn, application, step)
    allowed = {(step, FORWARD[FORWARD.index(step) + 1]), (step, "rejected")}
    for target in STEPS:
        if (step, target) not in allowed:
            assert _refused(conn, application["id"], step, target, REASON) == "23514", target


@pytest.mark.parametrize("final", ["hired", "rejected"])
def test_nothing_moves_out_of_a_final_step(conn, final):
    application = _application(conn)
    if final == "hired":
        _walk_to(conn, application, "hired")
    else:
        move_application(
            conn,
            RECRUITER_A,
            application["id"],
            from_step="new",
            to_step="rejected",
            reason_code=REASON,
        )
    for target in STEPS:
        assert _refused(conn, application["id"], final, target, REASON) == "23514"


def test_a_step_that_is_not_on_the_list_is_refused(conn):
    application = _application(conn)
    assert _refused(conn, application["id"], "new", "Offer made by hand") == "23514"


def test_a_move_from_a_step_the_application_is_not_at_is_refused(conn):
    application = _application(conn)
    _walk_to(conn, application, "contacted")
    with pytest.raises(Refused, match="is at contacted"):
        move_application(conn, RECRUITER_A, application["id"], from_step="new", to_step="contacted")


# --- B3: history is append-only, and the step is never a column -----------------------------------


def test_there_is_no_step_column_to_edit(conn):
    columns = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'pipeline' AND table_name = 'application'"
        )
    ).scalars()
    assert not [c for c in columns if re.search("step|stage|status", c)]
    application = _application(conn)
    with pytest.raises(DBAPIError) as error, conn.begin_nested():
        conn.execute(
            text("UPDATE pipeline.application_state SET current_step = 'hired' WHERE id = :id"),
            {"id": application["id"]},
        )
    # Refused either way: a view over joins is not updatable (55000), and the app has no grant.
    assert _sqlstate(error) in {"55000", "42501"}


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE pipeline.move SET to_step = 'hired' WHERE application_id = :id",
        "UPDATE pipeline.move SET moved_by = 'someone else' WHERE application_id = :id",
        "DELETE FROM pipeline.move WHERE application_id = :id",
    ],
)
def test_the_app_cannot_change_or_delete_a_move(conn, statement):
    application = _application(conn)
    with pytest.raises(DBAPIError) as error, conn.begin_nested():
        conn.execute(text(statement), {"id": application["id"]})
    assert _sqlstate(error) == "42501"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE pipeline.move SET to_step = 'hired' WHERE application_id = :id",
        "DELETE FROM pipeline.move WHERE application_id = :id",
        "TRUNCATE pipeline.move CASCADE",
    ],
)
def test_even_the_owner_cannot_change_a_move(owner_conn, statement):
    application = _application(owner_conn)
    with pytest.raises(DBAPIError) as error, owner_conn.begin_nested():
        owner_conn.execute(text(statement), {"id": application["id"]})
    assert re.search("append-only|keeps every", str(error.value.orig))


# --- B1: openings and applications are never deleted ---


@pytest.mark.parametrize("table", ["opening", "application"])
def test_the_app_cannot_delete_an_opening_or_an_application(conn, table):
    application = _application(conn)
    row_id = application["opening_id"] if table == "opening" else application["id"]
    with pytest.raises(DBAPIError) as error, conn.begin_nested():
        conn.execute(text(f"DELETE FROM pipeline.{table} WHERE id = :id"), {"id": row_id})
    assert _sqlstate(error) == "42501"


@pytest.mark.parametrize("table", ["opening", "application"])
def test_even_the_owner_cannot_delete_an_opening_or_an_application(owner_conn, table):
    application = _application(owner_conn)
    row_id = application["opening_id"] if table == "opening" else application["id"]
    with pytest.raises(DBAPIError) as error, owner_conn.begin_nested():
        owner_conn.execute(text(f"DELETE FROM pipeline.{table} WHERE id = :id"), {"id": row_id})
    assert _sqlstate(error) == "42501"


def test_an_application_cannot_be_changed(conn, owner_conn):
    application = _application(conn)
    with pytest.raises(DBAPIError) as error, conn.begin_nested():
        conn.execute(
            text(
                "UPDATE pipeline.application SET owner_recruiter = 'dev|recruiter-b' WHERE id = :id"
            ),
            {"id": application["id"]},
        )
    assert _sqlstate(error) == "42501"
    owned = _application(owner_conn)
    with pytest.raises(DBAPIError) as error, owner_conn.begin_nested():
        owner_conn.execute(
            text("UPDATE pipeline.application SET team = 'other' WHERE id = :id"),
            {"id": owned["id"]},
        )
    assert "append-only" in str(error.value.orig)


def test_opening_details_cannot_change(conn):
    opening = _opening(conn)
    with pytest.raises(DBAPIError) as error, conn.begin_nested():
        conn.execute(
            text("UPDATE pipeline.opening SET headcount = 99 WHERE id = :id"), {"id": opening["id"]}
        )
    assert _sqlstate(error) == "42501"


def test_closing_an_opening_archives_it_with_a_reason_and_a_person_once(conn):
    opening = _opening(conn)
    with pytest.raises(Refused):
        close_opening(conn, RECRUITER_A, opening["id"], "   ")
    closed = close_opening(conn, RECRUITER_A, opening["id"], "Headcount filled")
    assert (closed["status"], closed["closed_reason"], closed["closed_by"]) == (
        "closed",
        "Headcount filled",
        RECRUITER_A.subject,
    )
    assert closed["closed_at"] is not None
    with pytest.raises(Refused, match="final"):
        close_opening(conn, RECRUITER_A, opening["id"], "Closed again")
    with pytest.raises(DBAPIError), conn.begin_nested():
        conn.execute(
            text(
                "UPDATE pipeline.opening SET status = 'open', closed_at = NULL, "
                "closed_reason = NULL, closed_by = NULL WHERE id = :id"
            ),
            {"id": opening["id"]},
        )
    candidate = create_demo_candidate(conn, "integration-test", source="test-pipeline")
    with pytest.raises(Refused, match="closed"):
        create_application(conn, RECRUITER_A, opening["id"], candidate)


def test_migrated_candidates_get_no_applications(conn):
    capture_id = conn.execute(
        text(
            "INSERT INTO raw.capture "
            "(source, external_id, content_sha256, blob_key, media_type, byte_size, received_by) "
            "VALUES ('tai_master', :ext, :sha, 'test', 'application/json', 1, 'integration-test') "
            "RETURNING id"
        ),
        {"ext": f"test:{uuid.uuid4().hex}", "sha": uuid.uuid4().bytes * 2},
    ).scalar_one()
    candidate = conn.execute(
        text(
            "INSERT INTO core.candidate (capture_id, source_key, created_by, pipeline_state) "
            "VALUES (:c, :k, 'integration-test', 'not_recorded') RETURNING id"
        ),
        {"c": capture_id, "k": f"test-migrated:{uuid.uuid4().hex}"},
    ).scalar_one()
    with pytest.raises(Refused, match="OPN-11"):
        create_application(conn, RECRUITER_A, _opening(conn)["id"], candidate)


# --- B4: rejections, the review queue, and reversals ---


def test_a_rejection_with_no_reason_or_an_unlisted_reason_is_refused(conn):
    application = _application(conn)
    for reason in (None, "because", "does not meet criteria"):
        with pytest.raises(Refused, match="reason"):
            move_application(
                conn,
                RECRUITER_A,
                application["id"],
                from_step="new",
                to_step="rejected",
                reason_code=reason,
            )
    assert _refused(conn, application["id"], "new", "rejected", None) == "23514"
    assert _refused(conn, application["id"], "new", "rejected", "made_up") == "23514"


def test_only_a_rejection_carries_a_reason(conn):
    application = _application(conn)
    assert _refused(conn, application["id"], "new", "contacted", REASON) == "23514"


def test_the_system_cannot_reject_anyone(conn):
    application = _application(conn)
    assert (
        _refused(conn, application["id"], "new", "rejected", REASON, actor_kind="system") == "23514"
    )
    assert get_application(conn, RECRUITER_A, application["id"])["current_step"] == "new"


def test_an_automated_reject_only_creates_a_review_item(conn):
    application = _application(conn)
    _walk_to(conn, application, "phone_screen")
    before = move_history(conn, RECRUITER_A, application["id"])
    item = propose_rejection(conn, application["id"], REASON, "scoring-gate")
    assert move_history(conn, RECRUITER_A, application["id"]) == before
    assert get_application(conn, RECRUITER_A, application["id"])["current_step"] == "phone_screen"
    at_step = conn.execute(
        text("SELECT at_step FROM pipeline.review_item WHERE id = :id"), {"id": item}
    ).scalar_one()
    assert at_step == "phone_screen"


def test_a_person_confirms_a_proposed_rejection_as_their_own_move(conn):
    application = _application(conn)
    item = propose_rejection(conn, application["id"], REASON, "scoring-gate")
    move = confirm_proposed_rejection(conn, RECRUITER_A, item)
    assert (move["to_step"], move["actor_kind"], move["moved_by"], move["reason_code"]) == (
        "rejected",
        "person",
        RECRUITER_A.subject,
        REASON,
    )
    with pytest.raises(Refused):
        dismiss_proposed_rejection(conn, RECRUITER_A, item, "Changed my mind")


def test_dismissing_a_proposed_rejection_needs_a_reason_and_is_labelled(conn):
    application = _application(conn)
    item = propose_rejection(conn, application["id"], REASON, "scoring-gate")
    with pytest.raises(Refused):
        dismiss_proposed_rejection(conn, RECRUITER_A, item, " ")
    dismiss_proposed_rejection(conn, RECRUITER_A, item, "Replied on another channel")
    signal = conn.execute(
        text(
            "SELECT label, reason, decided_by FROM pipeline.override_signal "
            "WHERE application_id = :id"
        ),
        {"id": application["id"]},
    ).one()
    assert tuple(signal) == (
        "proposed_rejection_dismissed",
        "Replied on another channel",
        RECRUITER_A.subject,
    )


def test_reversing_a_rejection_needs_a_reason_keeps_history_and_is_labelled(conn):
    application = _application(conn)
    move_application(
        conn,
        RECRUITER_A,
        application["id"],
        from_step="new",
        to_step="rejected",
        reason_code=REASON,
    )
    with pytest.raises(Refused):
        reverse_rejection(conn, RECRUITER_A, application["id"], "")
    reopened = reverse_rejection(conn, RECRUITER_A, application["id"], "Rejected in error")

    assert get_application(conn, RECRUITER_A, application["id"])["current_step"] == "rejected"
    assert (reopened["reopens_application_id"], reopened["current_step"]) == (
        application["id"],
        "new",
    )
    signal = conn.execute(
        text(
            "SELECT label, reason_code, reason, decided_by FROM pipeline.override_signal "
            "WHERE application_id = :id"
        ),
        {"id": application["id"]},
    ).one()
    assert tuple(signal) == ("rejection_reversed", REASON, "Rejected in error", RECRUITER_A.subject)


def test_only_a_rejection_can_be_reversed_and_reopening_needs_a_reversal(conn):
    application = _application(conn)
    with pytest.raises(Refused, match="rejection"):
        reverse_rejection(conn, RECRUITER_A, application["id"], "Not a rejection")
    with pytest.raises(DBAPIError) as error, conn.begin_nested():
        conn.execute(
            text(
                "INSERT INTO pipeline.application "
                "(opening_id, candidate_id, owner_recruiter, team, reopens_application_id, "
                "created_by) "
                "VALUES (:o, :c, 'dev|recruiter-a', 'team-a', :r, 'integration-test')"
            ),
            {
                "o": application["opening_id"],
                "c": application["candidate_id"],
                "r": application["id"],
            },
        )
    assert _sqlstate(error) == "23514"


# --- B5 in the query layer ---


def test_out_of_scope_is_not_found_for_reading_and_moving(conn):
    application = _application(conn)
    recruiter_b = Actor("dev|recruiter-b", sees_all=False)
    ta_lead = Actor("dev|ta-lead", sees_all=True)
    with pytest.raises(NotFound):
        get_application(conn, recruiter_b, application["id"])
    with pytest.raises(NotFound):
        move_application(conn, recruiter_b, application["id"], from_step="new", to_step="contacted")
    with pytest.raises(NotFound):
        move_history(conn, recruiter_b, application["id"])
    assert get_application(conn, ta_lead, application["id"])["id"] == application["id"]
