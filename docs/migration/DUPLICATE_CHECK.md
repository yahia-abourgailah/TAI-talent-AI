# Checking the pairs by hand

The system went through every candidate record and found records that look like the same person
twice: **the same mobile number, the same email, or the same profile link**. A shared name is never
enough on its own — too many people are called the same thing — so those pairs are not in this file
at all. Nothing has been joined. Before any record is read under another, one of us checks these by
hand; if the machine is wrong more than 5 times in a thousand, we change it and check again.

This page is for the person doing that check.

## The file

`duplicate-sample.csv`, **94 pairs**, opens in Excel. It holds candidate names, numbers and profile
links, so it stays on your work machine: not in email, not in the code repository, not in a chat.

These are every pair the machine found that a person can actually weigh. It also found 1,258 pairs
that are two records of one profile link where one of the two holds nothing but that link — the
link itself is the answer there, so we do not waste your time on them.

Each row is **one pair of records**, with the columns:

| Column | What it is |
|---|---|
| `pair` | the row number, 1 to 500 |
| `candidate_a`, `candidate_b` | the two record numbers |
| `a_name`, `a_phone`, `a_email`, `a_profile` | everything we hold on the first record |
| `b_name`, `b_phone`, `b_email`, `b_profile` | the same for the second |
| `matched_on` | what the two have in common: `phone`, `email` or `profile_url`, and whether the name matches too |
| `decision`, `checked_by`, `note` | **yours** |

## What to do

For each row, decide whether the two are the same person, and write in `decision` one of:

- **`same`** — one person, two records
- **`different`** — two people
- **`unclear`** — you cannot tell from what is there

Put your name in `checked_by`. `note` is for anything worth saying (for example "brothers, same
number"). Leave the other columns as they are, do not delete rows, and do not sort the file.

A blank `decision` means "not checked" and is fine — better a blank than a guess. Please try to
finish all 94, or tell us how far you got.

## How to decide

- The same **mobile number** or the same **email** is nearly always the same person. Watch for the
  exceptions: a family number, an old work address, a phone written on someone else's behalf.
- The same **profile link** is the same person.
- **A name never put a pair in this file**, so do not treat a matching name as the answer. Where
  `matched_on` also says `name_arabic` or `name_latin`, it only means the names agree as well —
  useful support, not the reason. Two people can share a name, and the reading that brings محمد,
  Mohamed, Mohammed and Muhammad together also brings Mahmoud and Mohamed together.
- If the number and the name agree but something else feels wrong — a different city, a different
  employer, a twenty-year gap — say `unclear` and put it in the note. That is exactly what we want
  to hear about.

## When you are done

Send the file back to the platform team (keep it off email if you can — hand over the file itself).
We run it through:

```
python -m candidates.duplicates check --labels duplicate-sample.csv
```

which reports how often the system found a match and you said `different`. Under 0.5% and we start
joining records; above it, we fix the matching first.

## What happens after a pair is joined

Both records stay, with everything in them. A join only says which record the other is read under,
and it can be undone at any time with a reason. Nothing is ever deleted (BR-204, BR-205).
