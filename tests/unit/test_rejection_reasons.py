"""The step list files obey the rules the database applies when one is loaded (BR-402, BR-404).

The checks mirror migration 0005 (pipeline.step_list, step, allowed_move, rejection_reason,
list_activation), so a broken edit to the file fails here before anyone tries to load it.
"who" and "steps" on a reason are guidance for TA; the loader ignores them.
"""

import json
import re
from pathlib import Path

import pytest

LISTS = sorted((Path(__file__).parents[2] / "docs" / "pipeline" / "lists").glob("*.json"))


@pytest.fixture(params=LISTS, ids=[path.name for path in LISTS])
def document(request):
    return json.loads(request.param.read_text(encoding="utf-8"))


def test_there_is_a_list():
    assert LISTS


def test_version_and_source(document):
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", document["version"])
    assert document["source"].strip()
    assert isinstance(document["provisional"], bool)


def test_steps(document):
    steps = document["steps"]
    codes = [step["code"] for step in steps]
    assert len(codes) == len(set(codes))
    for step in steps:
        assert re.fullmatch(r"[a-z][a-z_]{0,39}", step["code"])
        assert step["label"].strip()
        assert step.get("outcome") in (None, "hired", "rejected")
    outcomes = [step.get("outcome") for step in steps if step.get("outcome")]
    assert sorted(outcomes) == ["hired", "rejected"]


def test_moves(document):
    steps = {step["code"]: step.get("outcome") for step in document["steps"]}
    moves = [tuple(move) for move in document["moves"]]
    assert len(moves) == len(set(moves))
    for from_step, to_step in moves:
        assert from_step in steps and to_step in steps
        assert from_step != to_step
        assert steps[from_step] is None, f"{from_step} is final, so no move may leave it"
    open_steps = [code for code, outcome in steps.items() if outcome is None]
    for code in open_steps:
        assert (code, "rejected") in moves, f"{code} cannot be rejected"
    reachable = {document["steps"][0]["code"]}
    for from_step, to_step in moves * len(moves):
        if from_step in reachable:
            reachable.add(to_step)
    assert reachable == set(steps), "every step is reachable from the first"


def test_rejection_reasons(document):
    reasons = document["rejection_reasons"]
    assert reasons
    codes = [reason["code"] for reason in reasons]
    assert len(codes) == len(set(codes))
    labels = [reason["label"].strip().casefold() for reason in reasons]
    assert len(labels) == len(set(labels))
    open_steps = {step["code"] for step in document["steps"] if step.get("outcome") is None}
    for reason in reasons:
        assert re.fullmatch(r"[a-z][a-z_]{0,59}", reason["code"])
        assert reason["label"].strip()
        assert reason.get("who", "company") in ("company", "candidate")
        assert set(reason.get("steps", [])) <= open_steps, reason["code"]
