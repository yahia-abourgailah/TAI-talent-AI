"""A2: the accuracy runner, driven through the fake OCR on made-up files (BR-311)."""

import json
from datetime import date

import pytest

from config import Environment
from intake import accuracy
from intake.fake_ocr import FakeOcrReader

TODAY = date(2026, 9, 16)


def _write_set(folder, labels: list[dict]) -> None:
    files = {
        "cv-en.pdf": b"%PDF-1.4 made-up FAKE-OCR:en",
        "cv-ar.pdf": b"%PDF-1.4 made-up FAKE-OCR:ar",
        "cv-hidden.pdf": b"%PDF-1.4 made-up FAKE-OCR:hidden",
        "cv-refused.pdf": b"%PDF-1.4 made-up FAKE-OCR:reject",
        "cv-unlabelled.png": b"\x89PNG\r\n\x1a\n made-up",
        "notes.txt": b"not a CV",
    }
    for name, content in files.items():
        (folder / name).write_bytes(content)
    document = {"format": accuracy.LABELS_FORMAT, "labelled_by": "test", "cvs": labels}
    (folder / accuracy.LABELS_FILE).write_text(json.dumps(document), encoding="utf-8")


LABELS = [
    {
        "file": "cv-en.pdf",
        "language": "en",
        "hidden_content": False,
        "fields": {
            "full_name": "Test Candidate Alpha",
            "phone": "0100 000 0000",
            "email": "TEST.ALPHA@example.com",
            "current_title": "sales  representative",
            "age": 26,
            "whatsapp": None,
            "profile_url": "https://example.com/in/someone",
            "location": "",
        },
    },
    {
        "file": "cv-ar.pdf",
        "language": "ar",
        "fields": {"full_name": "مرشح تجريبي الأول", "graduation_year": "٢٠٢٣", "email": None},
    },
    {"file": "cv-hidden.pdf", "language": "en", "hidden_content": True, "fields": {}},
    {"file": "cv-refused.pdf", "language": "ar", "fields": {"full_name": "x"}},
    {"file": "cv-gone.pdf", "language": "en", "fields": {}},
]


def test_the_runner_reports_counts_per_field_language_and_hidden_content(tmp_path):
    _write_set(tmp_path, LABELS)
    report = accuracy.run_test_set(tmp_path, FakeOcrReader(Environment.DEV), TODAY)

    assert report["reader"] == "fake-ocr"
    assert report["cvs"] == {
        "in_folder_not_labelled": 1,
        "labelled": 4,
        "labelled_not_in_folder": 1,
        "read": 3,
    }
    assert report["reading_errors"] == {"ocr_rejected": 1}
    fields = report["fields"]
    # Phones match on digits, emails ignoring case, text ignoring spacing.
    for name in ("phone", "email", "current_title", "whatsapp"):
        assert fields[name]["correct"] == fields[name]["checked"], name
    # The Arabic name differs by one space from the CV's text: names must match exactly.
    assert fields["full_name"] == {
        "correct": 1,
        "wrong": 1,
        "missed": 0,
        "invented": 0,
        "checked": 2,
        "accuracy": 0.5,
    }
    assert (fields["age"]["wrong"], fields["profile_url"]["missed"]) == (1, 1)
    assert fields["graduation_year"]["correct"] == 1
    assert "location" not in fields  # left as "": not checked
    assert report["hidden_content"] == {"caught": 1, "clean": 1}
    assert fields["hidden_content"] == {
        "correct": 2,
        "wrong": 0,
        "missed": 0,
        "invented": 0,
        "checked": 2,
        "accuracy": 1.0,
    }
    assert set(report["by_language"]) == {"ar", "en"}

    text = accuracy.markdown(report) + json.dumps(report, ensure_ascii=False)
    for value in ("Test Candidate", "مرشح", "example.com", "0100 000 0000"):
        assert value not in text


def test_an_answer_that_invents_a_value_is_counted(tmp_path):
    _write_set(tmp_path, [{"file": "cv-en.pdf", "fields": {"current_employer": None}}])
    report = accuracy.run_test_set(tmp_path, FakeOcrReader(Environment.DEV), TODAY)
    assert report["fields"]["current_employer"]["invented"] == 1
    assert report["by_language"]["unknown"]["current_employer"]["accuracy"] == 0.0


@pytest.mark.parametrize(
    ("labels", "message"),
    [
        ({"format": "other"}, "format"),
        ({"format": accuracy.LABELS_FORMAT, "cvs": []}, "no CVs"),
        ({"format": accuracy.LABELS_FORMAT, "cvs": [{"file": "../x.pdf"}]}, "plain file name"),
        (
            {"format": accuracy.LABELS_FORMAT, "cvs": [{"file": "a", "fields": {"salary": "1"}}]},
            "unknown fields: salary",
        ),
        (
            {"format": accuracy.LABELS_FORMAT, "cvs": [{"file": "a", "hidden_content": "yes"}]},
            "hidden_content",
        ),
    ],
)
def test_bad_labels_are_refused_by_key_never_by_value(tmp_path, labels, message):
    (tmp_path / accuracy.LABELS_FILE).write_text(json.dumps(labels), encoding="utf-8")
    with pytest.raises(accuracy.TestSetError, match=message):
        accuracy.load_labels(tmp_path)


def test_the_template_lists_every_cv_with_empty_fields(tmp_path):
    _write_set(tmp_path, LABELS)
    template = json.loads(accuracy.write_template(tmp_path).read_text(encoding="utf-8"))
    assert [cv["file"] for cv in template["cvs"]] == [
        "cv-ar.pdf",
        "cv-en.pdf",
        "cv-hidden.pdf",
        "cv-refused.pdf",
        "cv-unlabelled.png",
    ]
    (tmp_path / accuracy.LABELS_FILE).write_text(json.dumps(template), encoding="utf-8")
    labels = accuracy.load_labels(tmp_path)
    assert all(label.fields == {} for label in labels)  # nothing filled in, nothing checked


def test_the_command_refuses_a_missing_folder(tmp_path, capsys):
    assert accuracy.main(["run", "--set", str(tmp_path / "missing")]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_the_command_runs_on_the_fake_and_writes_a_counts_only_copy(tmp_path, capsys):
    folder = tmp_path / "set"
    folder.mkdir()
    _write_set(folder, LABELS)
    copy = tmp_path / "report.md"
    code = accuracy.main(
        [
            "run",
            "--set",
            str(folder),
            "--reader",
            "fake",
            "--out",
            str(tmp_path / "out"),
            "--report-copy",
            str(copy),
        ]
    )
    assert code == 0
    assert "| full_name | 2 | 1 | 1 |" in copy.read_text(encoding="utf-8")
    assert len(list((tmp_path / "out").glob("cv-accuracy-*.json"))) == 1
    assert "CV reading accuracy" in capsys.readouterr().out
