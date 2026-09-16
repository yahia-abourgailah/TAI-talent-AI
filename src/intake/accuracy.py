"""How right CV reading is, field by field, on a labelled test set (A2: BR-311, BR-308).

    python -m intake.accuracy template --set DIR
    python -m intake.accuracy run --set DIR [--reader fake|api] [--out DIR] [--report-copy FILE]

The test set is a folder of real CVs outside the repository, with a labels file beside them
(labels.json) in which a person wrote down the right fields for each CV
(docs/intake/CV_TEST_SET.md):

    {"format": "cv-labels/1", "labelled_by": "...", "cvs": [
      {"file": "cv-001.pdf", "language": "ar", "hidden_content": false,
       "fields": {"full_name": "...", "phone": "...", "current_employer": null}}
    ]}

A field with a value must come out as that value; a field given as null must come out not
recorded; a field left out, or left as "", is not checked. `template` writes
labels.template.json listing every CV in the folder, for the person labelling to fill in.

`run` reads every labelled CV through the same adapter and mapping the platform uses, and reports
per field how many came out right, wrong, missed or invented, overall and per language, and how
well hidden content was caught. The report holds counts only, never a value, so --report-copy may
point into the repository. With the fake OCR the numbers mean nothing: the run proves the runner.

Comparison: a name must match exactly as written. Phones match on their digits (tidy_phone),
numbers on their value, emails ignoring case, other text ignoring case and spacing.
"""

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from config import Environment, OcrMode
from importer.paths import DataLocationError, check_outside_repository
from intake.answer import AnswerUnreadable, parse_answer
from intake.cv_fields import STORED_FIELDS, map_answer, tidy_digits, tidy_phone
from intake.files import sniff
from intake.ocr import OcrError, OcrReader

LABELS_FORMAT = "cv-labels/1"
LABELS_FILE = "labels.json"
TEMPLATE_FILE = "labels.template.json"
CV_SUFFIXES = frozenset({".pdf", ".docx", ".jpg", ".jpeg", ".png"})
HIDDEN = "hidden_content"
RETRY_PAUSE_SECONDS = 5.0

CORRECT = "correct"
WRONG = "wrong"
MISSED = "missed"
INVENTED = "invented"
OUTCOMES = (CORRECT, WRONG, MISSED, INVENTED)


class TestSetError(Exception):
    """The test set or its labels cannot be used. Names files and keys, never values."""


@dataclass(frozen=True, slots=True)
class Label:
    file: str
    language: str | None
    hidden_content: bool | None
    fields: dict[str, str | None]


def load_labels(folder: Path) -> list[Label]:
    path = folder / LABELS_FILE
    if not path.is_file():
        raise TestSetError(f"No {LABELS_FILE} in the test set. Start from: template --set DIR.")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TestSetError(f"{LABELS_FILE} is not UTF-8 JSON.") from None
    if not isinstance(document, dict) or document.get("format") != LABELS_FORMAT:
        raise TestSetError(f'{LABELS_FILE} must have "format": "{LABELS_FORMAT}".')
    labels: list[Label] = []
    seen: set[str] = set()
    for index, entry in enumerate(document.get("cvs") or []):
        where = f"cvs[{index}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise TestSetError(f"{where} needs a file name.")
        name = entry["file"]
        if name in seen or Path(name).name != name:
            raise TestSetError(f"{where}: a file is listed twice, or is not a plain file name.")
        seen.add(name)
        fields = entry.get("fields") or {}
        if not isinstance(fields, dict):
            raise TestSetError(f"{where}.fields must be an object.")
        unknown = sorted(set(fields) - set(STORED_FIELDS))
        if unknown:
            raise TestSetError(f"{where}.fields has unknown fields: {', '.join(unknown)}.")
        if any(
            value is not None and not isinstance(value, str | int | float)
            for value in fields.values()
        ):
            raise TestSetError(f"{where}.fields values must be text, numbers or null.")
        hidden = entry.get("hidden_content")
        if hidden is not None and not isinstance(hidden, bool):
            raise TestSetError(f"{where}.hidden_content must be true, false or left out.")
        labels.append(
            Label(
                file=name,
                language=entry.get("language"),
                hidden_content=hidden,
                fields={k: None if v is None else str(v) for k, v in fields.items() if v != ""},
            )
        )
    if not labels:
        raise TestSetError(f"{LABELS_FILE} lists no CVs.")
    return labels


def _collapse(text: str) -> str:
    return " ".join(text.split()).casefold()


def _number(text: str) -> float | None:
    try:
        return float(tidy_digits(text.strip()))
    except ValueError:
        return None


def same(field_name: str, expected: str, found: str) -> bool:
    if field_name == "full_name":
        return expected.strip() == found.strip()
    if field_name in {"phone", "whatsapp"}:
        return tidy_phone(expected) == tidy_phone(found) != ""
    if field_name in {"age", "years_experience", "graduation_year"}:
        wanted, got = _number(expected), _number(found)
        return wanted is not None and wanted == got
    if field_name == "email":
        return expected.strip().casefold() == found.strip().casefold()
    return _collapse(expected) == _collapse(found)


def judge(field_name: str, expected: str | None, found: str | None) -> str:
    if expected is None:
        return CORRECT if found is None else INVENTED
    if found is None:
        return MISSED
    return CORRECT if same(field_name, expected, found) else WRONG


@dataclass
class Tally:
    fields: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    by_language: dict[str, dict[str, Counter[str]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(Counter))
    )
    hidden: Counter[str] = field(default_factory=Counter)
    cvs: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)

    def add(self, language: str, field_name: str, outcome: str) -> None:
        self.fields[field_name][outcome] += 1
        self.by_language[language][field_name][outcome] += 1


def _rates(counts: Counter[str]) -> dict[str, Any]:
    checked = sum(counts[o] for o in OUTCOMES)
    row: dict[str, Any] = {o: counts[o] for o in OUTCOMES}
    row["checked"] = checked
    row["accuracy"] = round(counts[CORRECT] / checked, 4) if checked else None
    return row


def _read(reader: OcrReader, content: bytes, media_type: str) -> bytes:
    try:
        return reader.read(content, media_type).body
    except OcrError as exc:
        if not exc.retryable:
            raise
    time.sleep(RETRY_PAUSE_SECONDS)
    return reader.read(content, media_type).body


def run_test_set(folder: Path, reader: OcrReader, today: date) -> dict[str, Any]:
    labels = load_labels(folder)
    listed = {label.file for label in labels}
    on_disk = {p.name for p in folder.iterdir() if p.suffix.lower() in CV_SUFFIXES}
    tally = Tally()
    tally.cvs["in_folder_not_labelled"] = len(on_disk - listed)
    tally.cvs["labelled_not_in_folder"] = len(listed - on_disk)
    for label in labels:
        path = folder / label.file
        if not path.is_file():
            continue
        tally.cvs["labelled"] += 1
        language = label.language or "unknown"
        content = path.read_bytes()
        kind = sniff(content)
        if kind is None:
            tally.errors["unsupported_file"] += 1
            continue
        try:
            answer = parse_answer(_read(reader, content, kind.media_type))
        except OcrError as exc:
            tally.errors[exc.code] += 1
            continue
        except AnswerUnreadable:
            tally.errors["ocr_answer_unreadable"] += 1
            continue
        tally.cvs["read"] += 1
        found = {f.field: f.value for f in map_answer(answer, today).fields}
        for name, expected in label.fields.items():
            tally.add(language, name, judge(name, expected, found.get(name)))
        if label.hidden_content is not None:
            caught = answer.hidden_content.found
            outcome = {
                (True, True): "caught",
                (True, False): "missed",
                (False, True): "false_alarm",
                (False, False): "clean",
            }[(label.hidden_content, caught)]
            tally.hidden[outcome] += 1
            tally.add(language, HIDDEN, CORRECT if caught == label.hidden_content else WRONG)

    return {
        "reader": reader.name,
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "cvs": dict(sorted(tally.cvs.items())),
        "reading_errors": dict(sorted(tally.errors.items())),
        "fields": {
            name: _rates(tally.fields[name])
            for name in (*STORED_FIELDS, HIDDEN)
            if name in tally.fields
        },
        "by_language": {
            language: {name: _rates(counts) for name, counts in sorted(per_field.items())}
            for language, per_field in sorted(tally.by_language.items())
        },
        "hidden_content": dict(sorted(tally.hidden.items())),
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# CV reading accuracy",
        "",
        f"Reader `{report['reader']}`, run {report['run_at']}. Counts only; no CV values.",
        "",
        "| CVs | |",
        "|---|---|",
        *(f"| {k} | {v} |" for k, v in report["cvs"].items()),
        *(f"| error: {k} | {v} |" for k, v in report["reading_errors"].items()),
        "",
        "| Field | Checked | Correct | Wrong | Missed | Invented | Accuracy |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, row in report["fields"].items():
        accuracy = "-" if row["accuracy"] is None else f"{row['accuracy']:.1%}"
        lines.append(
            f"| {name} | {row['checked']} | {row['correct']} | {row['wrong']} | "
            f"{row['missed']} | {row['invented']} | {accuracy} |"
        )
    lines += [
        "",
        "Hidden content: "
        + (", ".join(f"{k} {v}" for k, v in report["hidden_content"].items()) or "not labelled"),
        "",
    ]
    for language, per_field in report["by_language"].items():
        summary = ", ".join(
            f"{name} {row['correct']}/{row['checked']}" for name, row in per_field.items()
        )
        lines.append(f"- **{language}**: {summary}")
    return "\n".join(lines) + "\n"


def write_template(folder: Path) -> Path:
    files = sorted(p.name for p in folder.iterdir() if p.suffix.lower() in CV_SUFFIXES)
    document = {
        "format": LABELS_FORMAT,
        "labelled_by": "",
        "cvs": [
            {
                "file": name,
                "language": "ar | en | mixed",
                "hidden_content": False,
                "fields": {f: "" for f in STORED_FIELDS},
            }
            for name in files
        ],
    }
    target = folder / TEMPLATE_FILE
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def reader_for(choice: str | None) -> OcrReader:
    """The reader, from the environment alone: the runner needs no database."""
    env = Environment(os.environ.get("TALENT_ENV", "dev"))
    mode = OcrMode(
        choice
        or os.environ.get("TALENT_OCR_MODE")
        or (OcrMode.FAKE if env is Environment.DEV else OcrMode.API)
    )
    if mode is OcrMode.FAKE:
        from intake.fake_ocr import FakeOcrReader

        return FakeOcrReader(env)
    from intake.ocr_http import HttpOcrReader

    base_url = os.environ.get("TALENT_OCR_BASE_URL", "")
    if not base_url:
        raise TestSetError("Set TALENT_OCR_BASE_URL to read the test set with the OCR API.")
    return HttpOcrReader(
        base_url,
        api_key=os.environ.get("TALENT_OCR_API_KEY", ""),
        timeout_seconds=float(os.environ.get("TALENT_OCR_TIMEOUT_SECONDS", "60")),
    )


def _folder(value: str | None) -> Path:
    raw = value or os.environ.get("TALENT_CV_TEST_SET", "")
    if not raw:
        raise TestSetError("Pass --set or set TALENT_CV_TEST_SET to the test set folder.")
    folder = Path(raw).expanduser()
    if not folder.is_dir():
        raise TestSetError("The test set folder does not exist.")
    check_outside_repository(folder, None, "The test set")
    return folder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m intake.accuracy", description=__doc__.splitlines()[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser("template", help="write labels.template.json for the folder")
    template.add_argument("--set", help="the test set folder (default: $TALENT_CV_TEST_SET)")
    run = commands.add_parser("run", help="read every labelled CV and report accuracy")
    run.add_argument("--set", help="the test set folder (default: $TALENT_CV_TEST_SET)")
    run.add_argument("--reader", choices=[m.value for m in OcrMode])
    run.add_argument("--out", help="folder for the full report, outside the repository")
    run.add_argument("--report-copy", help="also write the counts-only markdown report here")
    args = parser.parse_args(argv)

    try:
        folder = _folder(args.set)
        if args.command == "template":
            print(f"Wrote {write_template(folder).name}. Fill it in and save it as {LABELS_FILE}.")
            return 0
        out = Path(args.out).expanduser() if args.out else None
        if out is not None:
            check_outside_repository(out, None, "--out")
        report = run_test_set(folder, reader_for(args.reader), date.today())
    except (TestSetError, DataLocationError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    text = markdown(report)
    print(text)
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        stamp = report["run_at"].replace(":", "")
        (out / f"cv-accuracy-{stamp}.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
    if args.report_copy:
        Path(args.report_copy).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
