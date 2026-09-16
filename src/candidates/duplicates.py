"""Finding the same person twice (A: BR-203, BR-206).

    python -m candidates.duplicates scan --out DIR [--report-copy docs/migration/DUPLICATES.md]
    python -m candidates.duplicates sample --out DIR [--pairs 500] [--include-link-only]
    python -m candidates.duplicates check --labels FILE [--report-copy FILE]

**A name is never a match.** Two records are one person only when they share something that
belongs to one person:

    phone        the last 10 digits, so +20 100..., 0020 100... and 0100... are one number
    email        trimmed and lower-cased
    profile_url  the profile itself: no scheme, no www, no query, no trailing slash

A cell can hold more than one of these — a number and a profile link together, as some of the
sourcing batches were written — so each piece is read as what it is, not as what its column says.

A name is read the same careful way (ال prefix, ة and ه, ى and ي, hamza forms, diacritics, and
Arabic against its English spelling: "محمد", "Mohamed", "Mohammed" and "Muhammad" are one key), but
only ever as **extra detail on a pair that already matched on a number, an address or a profile**.
Egypt has a great many people called Mohamed Ali, and the reading that brings spellings together
also brings Mahmoud and Mohamed together, so a shared name on its own says nothing and is not put
in front of anyone.

Nothing here joins anything: every match waits for a person (BR-206), and a person joins with
candidates.joins.

A name key needs at least two words, and a key shared by more candidates than MOST_PER_KEY is
reported as too common rather than turned into thousands of pairs.

Reports hold counts and candidate ids only, never a name, a number or an address. The per-pair
file goes outside the repository: which two records are one person is about people.
"""

import argparse
import csv
import io
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from importer.paths import DataLocationError, check_outside_repository
from jobs.queue import engine_from_environment

STRONG = "strong"
POSSIBLE = "possible"
EVIDENCE = ("phone", "email", "profile_url", "name_arabic", "name_latin")
STRONG_EVIDENCE = frozenset({"phone", "email", "profile_url"})
FIELDS = ("full_name", "phone", "whatsapp", "email", "profile_url")
MOST_PER_KEY = 25
FOUND_BY = "duplicate-matcher"
REVIEW_REASON = "possible_duplicate"

_ARABIC_MARKS = re.compile("[ً-ٰٟـ]")
_ARABIC_LETTERS = str.maketrans(
    {
        "أ": "ا",  # alef with hamza above
        "إ": "ا",  # alef with hamza below
        "آ": "ا",  # alef with madda
        "ى": "ي",  # alef maqsura -> yaa
        "ة": "ه",  # taa marbuta -> haa
        "ؤ": "ء",  # waw with hamza -> hamza
        "ئ": "ء",  # yaa with hamza -> hamza
    }
)
# Egyptian reading, consonants only. Arabic writes no short vowels, and its long vowels (ا و ي)
# are what English spellings turn into a, o, ou, e, y: both sides drop them, so محمد, Mohamed,
# Mohammed and Muhammad meet. A word-final ة or ه is the feminine ending: Fatma, Fatima, Fatimah.
_TO_LATIN = {
    "ا": "",
    "ء": "",
    "ع": "",
    "و": "",
    "ي": "",
    "ى": "",
    "ب": "b",
    "ت": "t",
    "ث": "th",
    "ج": "g",
    "ح": "h",
    "خ": "kh",
    "د": "d",
    "ذ": "z",
    "ر": "r",
    "ز": "z",
    "س": "s",
    "ش": "sh",
    "ص": "s",
    "ض": "d",
    "ط": "t",
    "ظ": "z",
    "غ": "gh",
    "ف": "f",
    "ق": "k",
    "ك": "k",
    "ل": "l",
    "م": "m",
    "ن": "n",
    "ه": "h",
}
_VOWELS = "aeiouy"
_DIGITS = str.maketrans(
    "".join(chr(0x0660 + i) for i in range(10)) + "".join(chr(0x06F0 + i) for i in range(10)),
    "0123456789" * 2,
)


@dataclass(frozen=True, slots=True)
class Pair:
    lower_id: int
    higher_id: int
    strength: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Matches:
    """What a scan found: the pairs to put in front of a person, and what it left alone.

    `name_only` counts the pairs that share nothing but a name. They are not matches, and are
    counted only so the report can say how many were left alone.
    """

    pairs: list[Pair]
    too_common: dict[str, int]
    name_only: int


# A cell sometimes holds more than one thing: "+20100...  |  linkedin.com/in/...". Each piece is
# read on its own, so a number is never taken from the digits inside a link.
_BETWEEN_PIECES = re.compile(r"[|;,\n\t]+|\s{2,}")


def pieces(value: str) -> list[str]:
    return [piece.strip() for piece in _BETWEEN_PIECES.split(value) if piece.strip()]


def kind_of(piece: str) -> str:
    """What a piece of a contact cell is: an address, a link, or a number."""
    if "@" in piece:
        return "email"
    if "/" in piece or re.search(r"[a-z]\.[a-z]{2,}", piece.lower()):
        return "profile_url"
    return "phone"


def phone_key(value: str) -> str | None:
    digits = "".join(ch for ch in value.translate(_DIGITS) if ch.isdigit())
    return digits[-10:] if len(digits) >= 10 else None


def email_key(value: str) -> str | None:
    cleaned = value.strip().lower()
    local, at, domain = cleaned.partition("@")
    return cleaned if at and local and domain and " " not in cleaned else None


def profile_key(value: str) -> str | None:
    cleaned = value.strip().lower()
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = re.sub(r"^www\.", "", cleaned)
    cleaned = cleaned.split("?")[0].split("#")[0].rstrip("/")
    return cleaned or None


def _words(value: str) -> list[str]:
    text_ = unicodedata.normalize("NFKC", value).strip().lower()
    text_ = _ARABIC_MARKS.sub("", text_).translate(_ARABIC_LETTERS)
    words = re.findall(r"[^\W\d_]+", text_)
    # "ال" is the Arabic "the": السيد and سيد are the same word.
    return [w[2:] if len(w) > 4 and w.startswith("ال") else w for w in words]


def _latin_skeleton(word: str) -> str:
    """An English spelling as its consonants: Mohamed, Mohammed and Muhammad are all mhmd."""
    without_ending = word[:-1] if len(word) > 2 and word.endswith("h") else word
    consonants = "".join(ch for ch in without_ending if ch not in _VOWELS)
    return re.sub(r"(.)\1+", r"\1", consonants)


def _as_latin(word: str) -> str:
    if not any(ch in _TO_LATIN for ch in word):
        return _latin_skeleton(word)
    # A word-final haa is the feminine ending, as in فاطمة.
    body = word[:-1] if len(word) > 2 and word.endswith("ه") else word
    return re.sub(r"(.)\1+", r"\1", "".join(_TO_LATIN.get(ch, "") for ch in body))


def name_keys(value: str) -> dict[str, str]:
    """The keys a name is matched on: its own spelling, and its consonants across scripts."""
    words = _words(value)
    if len(words) < 2:
        return {}
    keys = {"name_arabic": " ".join(words)}
    latin = " ".join(part for part in (_as_latin(word) for word in words) if part)
    if len(latin.split()) >= 2:
        keys["name_latin"] = latin
    return keys


_KEY_OF = {"phone": phone_key, "email": email_key, "profile_url": profile_key}


def contact_keys(field: str, value: str) -> dict[str, list[str]]:
    """The keys in a contact cell, each piece read as what it is rather than as what its heading
    says: a link written in the phone column is a link, not a number ending in its digits."""
    found: dict[str, list[str]] = {}
    for piece in pieces(value):
        kind = kind_of(piece)
        if field == "email" and kind == "phone":
            continue  # a number in the email column is not an address, and not a mobile either
        key = _KEY_OF[kind](piece)
        if key and key not in found.setdefault(kind, []):
            found[kind].append(key)
    return found


def keys_of(field: str, value: str) -> dict[str, list[str]]:
    """Every key this value is matched on, by kind. A cell can hold more than one."""
    if field in {"phone", "whatsapp", "email", "profile_url"}:
        return contact_keys(field, value)
    if field == "full_name":
        return {kind: [key] for kind, key in name_keys(value).items()}
    return {}


def _identities(conn: Connection) -> list[tuple[int, str, str]]:
    rows = conn.execute(
        text(
            "SELECT candidate_id, field, value FROM core.candidate_field_current f "
            "WHERE field = ANY(:fields) AND value IS NOT NULL AND btrim(value) <> '' "
            # A candidate who asked us to stop keeping their data is matched against no one.
            "AND NOT EXISTS ("
            "  SELECT 1 FROM core.candidate_locked lock WHERE lock.candidate_id = f.candidate_id"
            ")"
        ),
        {"fields": list(FIELDS)},
    )
    return [(int(row.candidate_id), row.field, row.value) for row in rows]


def find_pairs(identities: Iterable[tuple[int, str, str]]) -> Matches:
    """Every pair of candidates sharing a number, an address or a profile, with what they share.

    A pair that shares only a name is not a match and is left out (see the note at the top).
    """
    by_key: dict[tuple[str, str], set[int]] = {}
    for candidate_id, field, value in identities:
        for evidence, keys in keys_of(field, value).items():
            for key in keys:
                by_key.setdefault((evidence, key), set()).add(candidate_id)

    evidence_of: dict[tuple[int, int], set[str]] = {}
    too_common: dict[str, int] = {}
    for (evidence, _key), candidates in by_key.items():
        if len(candidates) < 2:
            continue
        if len(candidates) > MOST_PER_KEY:
            too_common[evidence] = too_common.get(evidence, 0) + 1
            continue
        for lower, higher in combinations(sorted(candidates), 2):
            evidence_of.setdefault((lower, higher), set()).add(evidence)

    pairs = [
        Pair(lower, higher, STRONG, tuple(sorted(found)))
        for (lower, higher), found in sorted(evidence_of.items())
        if found & STRONG_EVIDENCE
    ]
    name_only = sum(1 for found in evidence_of.values() if not found & STRONG_EVIDENCE)
    return Matches(pairs, too_common, name_only)


def record(conn: Connection, pairs: Sequence[Pair], found_by: str = FOUND_BY) -> dict[str, int]:
    """Keeps each match and opens a review item for it. Nothing is joined (BR-206)."""
    counts = {"matches_new": 0, "matches_known": 0, "review_items_opened": 0}
    for pair in pairs:
        match_id = conn.execute(
            text(
                """
                INSERT INTO core.candidate_match (lower_id, higher_id, strength, evidence, found_by)
                VALUES (:lower, :higher, :strength, :evidence, :by)
                ON CONFLICT (lower_id, higher_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "lower": pair.lower_id,
                "higher": pair.higher_id,
                "strength": pair.strength,
                "evidence": list(pair.evidence),
                "by": found_by,
            },
        ).scalar_one_or_none()
        if match_id is None:
            counts["matches_known"] += 1
            continue
        counts["matches_new"] += 1
        opened = conn.execute(
            text(
                """
                INSERT INTO pipeline.review_item
                  (kind, candidate_id, match_id, reason_code, proposed_by)
                VALUES ('possible_duplicate', :candidate, :match, :reason, :by)
                ON CONFLICT (match_id) WHERE kind = 'possible_duplicate' DO NOTHING
                RETURNING id
                """
            ),
            {
                "candidate": pair.lower_id,
                "match": match_id,
                "reason": REVIEW_REASON,
                "by": found_by,
            },
        ).scalar_one_or_none()
        counts["review_items_opened"] += opened is not None
    return counts


def groups(pairs: Sequence[Pair]) -> list[list[int]]:
    """Candidates joined by matches, as groups: what a person would see before deciding."""
    parent: dict[int, int] = {}

    def root(value: int) -> int:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    for pair in pairs:
        left, right = root(pair.lower_id), root(pair.higher_id)
        if left != right:
            parent[left] = right
    found: dict[int, list[int]] = {}
    for candidate in parent:
        found.setdefault(root(candidate), []).append(candidate)
    return sorted(
        (sorted(group) for group in found.values() if len(group) > 1), key=len, reverse=True
    )


def summarise(matches: Matches, candidates: int) -> dict[str, Any]:
    pairs, too_common = matches.pairs, matches.too_common
    by_evidence: dict[str, int] = {}
    for pair in pairs:
        for evidence in pair.evidence:
            by_evidence[evidence] = by_evidence.get(evidence, 0) + 1
    found = groups(pairs)
    sizes: dict[int, int] = {}
    for group in found:
        sizes[len(group)] = sizes.get(len(group), 0) + 1
    return {
        "candidates_with_something_to_match_on": candidates,
        "pairs": len(pairs),
        "name_only_not_a_match": matches.name_only,
        "pairs_by_evidence": dict(sorted(by_evidence.items())),
        "groups": len(found),
        "records_in_a_group": sum(len(group) for group in found),
        "group_sizes": {str(size): count for size, count in sorted(sizes.items())},
        "keys_too_common_to_use": dict(sorted(too_common.items())),
        "most_per_key": MOST_PER_KEY,
    }


def render_report(summary: Mapping[str, Any], recorded: Mapping[str, int] | None) -> str:
    lines = [
        "# The same person, found twice (BR-203)",
        "",
        "Counts and candidate ids only; no names, numbers or addresses. Nothing here is joined: "
        "every match waits for a person (BR-206).",
        "",
        "| What | Count |",
        "|---|---:|",
        "| Candidates with a phone, email, profile or name | "
        f"{summary['candidates_with_something_to_match_on']:,} |",
        f"| Pairs found (same phone, email or profile) | {summary['pairs']:,} |",
        f"| Pairs sharing only a name, left alone | {summary['name_only_not_a_match']:,} |",
        f"| Groups | {summary['groups']:,} |",
        f"| Records inside a group | {summary['records_in_a_group']:,} |",
        "",
        "## What pairs were matched on",
        "",
        "| Evidence | Pairs |",
        "|---|---:|",
    ]
    lines += [f"| {name} | {count:,} |" for name, count in summary["pairs_by_evidence"].items()]
    lines += ["", "## Group sizes", "", "| Records in the group | Groups |", "|---:|---:|"]
    lines += [f"| {size} | {count:,} |" for size, count in summary["group_sizes"].items()]
    if summary["keys_too_common_to_use"]:
        lines += [
            "",
            f"**Keys shared by more than {summary['most_per_key']} records** are left out rather "
            "than turned into thousands of pairs: "
            + ", ".join(
                f"{name} {count:,}" for name, count in summary["keys_too_common_to_use"].items()
            )
            + ".",
        ]
    if recorded is not None:
        lines += [
            "",
            "## Recorded",
            "",
            f"New matches {recorded['matches_new']:,}, "
            f"already known {recorded['matches_known']:,}, "
            f"review items opened {recorded['review_items_opened']:,}.",
        ]
    lines += [
        "",
        "## Before anything is joined",
        "",
        "A person checks a sample by hand (`python -m candidates.duplicates sample`), and the "
        "wrong-join rate must be under 0.5% before any joining starts.",
        "",
    ]
    return "\n".join(lines)


def _write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def _pairs_csv(pairs: Sequence[Pair]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["candidate_a", "candidate_b", "strength", "evidence"])
    for pair in pairs:
        writer.writerow([pair.lower_id, pair.higher_id, pair.strength, " ".join(pair.evidence)])
    return buffer.getvalue()


SHOWN_FIELDS = ("full_name", "phone", "whatsapp", "email", "profile_url")


def pick_sample(
    pairs: Sequence[Pair], wanted: int, worth_asking: Callable[[Pair], bool] | None = None
) -> list[Pair]:
    """The pairs for a person to check by hand.

    Where two records were matched on a profile link and one of them holds nothing but that link,
    there is nothing for a person to weigh, so those go to the back: a sample is worth someone's
    afternoon only if the pairs in it can be got wrong.
    """
    order = (lambda pair: not worth_asking(pair)) if worth_asking else (lambda pair: False)
    return sorted(pairs, key=order)[:wanted]


def shown_values(conn: Connection, candidates: Sequence[int]) -> dict[int, dict[str, str]]:
    """What a person needs to tell two records apart: the name, a way to reach them, the profile.

    Only ever written into the file the person checks, which lives outside the repository.
    """
    if not candidates:
        return {}
    found: dict[int, dict[str, str]] = {}
    rows = conn.execute(
        text(
            "SELECT candidate_id, field, value FROM core.candidate_field_current "
            "WHERE candidate_id = ANY(:ids) AND field = ANY(:fields) AND value IS NOT NULL"
        ),
        {"ids": list(candidates), "fields": list(SHOWN_FIELDS)},
    )
    for row in rows:
        found.setdefault(int(row.candidate_id), {})[row.field] = str(row.value)
    return found


def _side(found: Mapping[str, str]) -> list[str]:
    """Name, number, email and profile as a person should read them.

    Some rows arrived with a number and a profile in one cell, separated by a bar; the pieces are
    put under the right heading here so the person checking sees a number where a number belongs.
    """
    columns: dict[str, list[str]] = {"phone": [], "email": [], "profile_url": []}
    for field in ("phone", "whatsapp", "email", "profile_url"):
        for piece in pieces(str(found.get(field, ""))):
            kind = kind_of(piece)
            if piece not in columns[kind]:
                columns[kind].append(piece)
    return [found.get("full_name", "")] + [
        " ".join(columns[kind]) for kind in ("phone", "email", "profile_url")
    ]


def _worth_asking(values: Mapping[int, Mapping[str, str]]) -> Callable[[Pair], bool]:
    """A pair is worth a person's time when both records hold more than the key they matched on."""

    def told_apart(candidate: int) -> bool:
        found = values.get(candidate, {})
        return bool(found.get("full_name", "").strip()) and any(
            found.get(field, "").strip() for field in ("phone", "whatsapp", "email", "profile_url")
        )

    return lambda pair: told_apart(pair.lower_id) and told_apart(pair.higher_id)


def _sample_csv(
    pairs: Sequence[Pair],
    wanted: int,
    values: Mapping[int, Mapping[str, str]] | None = None,
    picked: Sequence[Pair] | None = None,
) -> str:
    picked = pick_sample(pairs, wanted) if picked is None else picked
    columns = ("name", "phone", "email", "profile") if values is not None else ()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["pair", "candidate_a", "candidate_b"]
        + [f"{side}_{column}" for side in ("a", "b") for column in columns]
        + ["matched_on", "strength", "decision", "checked_by", "note"]
    )
    for number, pair in enumerate(picked, start=1):
        row: list[Any] = [number, pair.lower_id, pair.higher_id]
        if values is not None:
            row += _side(values.get(pair.lower_id, {})) + _side(values.get(pair.higher_id, {}))
        row += [" ".join(pair.evidence), pair.strength, "", "", ""]
        writer.writerow(row)
    return buffer.getvalue()


DECISIONS = ("same", "different", "unclear")


def read_decisions(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    problems = []
    for number, row in enumerate(rows, start=2):
        decision = (row.get("decision") or "").strip().lower()
        if decision and decision not in DECISIONS:
            problems.append(f"line {number}: decision is one of {', '.join(DECISIONS)}")
    if problems:
        raise ValueError("; ".join(problems))
    return rows


def check_report(rows: Sequence[Mapping[str, str]]) -> dict[str, Any]:
    """What the hand check says: of the pairs checked, how many are not one person after all."""
    checked = [row for row in rows if (row.get("decision") or "").strip()]
    wrong = [row for row in checked if row["decision"].strip().lower() == "different"]
    return {
        "pairs_in_the_sample": len(rows),
        "checked": len(checked),
        "wrong": len(wrong),
        "wrong_join_rate": None if not checked else round(len(wrong) / len(checked), 5),
        "unclear": sum(row["decision"].strip().lower() == "unclear" for row in checked),
        "wrong_pairs": [(row["candidate_a"], row["candidate_b"]) for row in wrong],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m candidates.duplicates", description=__doc__.splitlines()[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="find matches, keep them, and open review items")
    scan.add_argument("--out", type=Path, required=True, help="outside any git repository")
    scan.add_argument("--report-copy", type=Path, help="counts only, so it may be committed")
    scan.add_argument("--dry-run", action="store_true", help="find and report, record nothing")
    sample = commands.add_parser("sample", help="pairs for a person to check by hand")
    sample.add_argument("--out", type=Path, required=True)
    sample.add_argument("--pairs", type=int, default=500)
    sample.add_argument(
        "--ids-only",
        action="store_true",
        help="leave out the names, numbers and profiles the person needs to judge a pair",
    )
    sample.add_argument(
        "--include-link-only",
        action="store_true",
        help="also include pairs on one profile link where a record holds nothing else",
    )
    check = commands.add_parser("check", help="read the checked sample and report the rate")
    check.add_argument("--labels", type=Path, required=True)
    check.add_argument("--report-copy", type=Path)
    args = parser.parse_args(argv)

    if args.command == "check":
        try:
            rows = read_decisions(args.labels.expanduser())
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        checked = check_report(rows)
        print(
            f"Checked {checked['checked']} of {checked['pairs_in_the_sample']} pairs. "
            f"Not one person after all: {checked['wrong']} "
            f"(rate {checked['wrong_join_rate']}). Unclear {checked['unclear']}."
        )
        return 0 if (checked["wrong_join_rate"] or 0) < 0.005 else 1

    out = args.out.expanduser()
    try:
        check_outside_repository(out, None, "--out")
    except DataLocationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out.mkdir(mode=0o700, parents=True, exist_ok=True)

    engine = engine_from_environment()
    with engine.begin() as conn:
        identities = _identities(conn)
        matches = find_pairs(identities)
        pairs = matches.pairs
        summary = summarise(matches, len({row[0] for row in identities}))
        if args.command == "sample":
            if args.ids_only:
                picked, values = pick_sample(pairs, args.pairs), None
            else:
                sides = sorted({side for pair in pairs for side in (pair.lower_id, pair.higher_id)})
                values = shown_values(conn, sides)
                worth_asking = _worth_asking(values)
                # Two records on one profile link where one of them holds nothing but that link
                # are the same person by the link itself. There is nothing for a person to weigh,
                # so they are left out unless they are asked for.
                judgeable = [pair for pair in pairs if worth_asking(pair)]
                picked = pick_sample(
                    pairs if args.include_link_only else judgeable, args.pairs, worth_asking
                )
                print(
                    f"{len(pairs) - len(judgeable)} of {len(pairs)} pairs are one profile link "
                    "where a record holds nothing else"
                    + (
                        "; they are in the file too."
                        if args.include_link_only
                        else "; left out (--include-link-only keeps them)."
                    )
                )
            _write_private(
                out / "duplicate-sample.csv", _sample_csv(pairs, args.pairs, values, picked)
            )
            print(f"{len(picked)} pairs for checking: {out / 'duplicate-sample.csv'}")
            if values is not None:
                print("  It holds candidate details for the person checking. Never commit it.")
            return 0
        recorded = None if args.dry_run else record(conn, pairs)
    _write_private(out / "duplicate-pairs.csv", _pairs_csv(pairs))
    report = render_report(summary, recorded)
    _write_private(out / "DUPLICATES.md", report)
    if args.report_copy is not None:
        args.report_copy.parent.mkdir(parents=True, exist_ok=True)
        args.report_copy.write_text(report, encoding="utf-8")
    print(
        f"Pairs {summary['pairs']} in {summary['groups']} groups. "
        f"Pairs sharing only a name, left alone: {summary['name_only_not_a_match']}."
        + ("" if recorded is None else f" Recorded: {recorded}.")
    )
    print(f"  pairs  {out / 'duplicate-pairs.csv'}  (candidate ids, never commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
