# Criteria differences: code vs CRITERIA.md (criteria version 2026-08-04)

**Status:** Draft for Karim's ruling — 14 September 2026

> **CLAUDE.md has not been received.** The build plan asks for a three-way comparison of `CLAUDE.md`, `CRITERIA.md` and the code. Without `CLAUDE.md`, this document compares only the code and `CRITERIA.md`. Repeat the comparison when `CLAUDE.md` arrives (see the last section).

This document lists every place where the executable rules and the written spec disagree, or where the code does something the spec never mentions. Each item ends in a question for Karim AlAkkad, the criteria owner. **The code wins until Karim rules. Nothing here is fixed yet.** A ruling becomes a new criteria version. It is never an edit to v2026-08-04, which must keep reproducing the 4 August scoring exactly (BR-301, BR-303).

**Sources compared**

- Code (wins): `src/scoring/rulesets/v2026_08_04.py`. References such as `v2026_08_04.py:556` use this file's line numbers, which include its 5-line port header.
- Written spec: `docs/criteria/CRITERIA_2026-08-04.md` (called CRITERIA.md below).
- Characterisation tests: `tests/unit/test_scoring_ruleset_v2026_08_04.py`.
- Context: `docs/BRD.html`, sections 2.3, 6.1, 8, 9 and 13.

Every example below is made up. None of it comes from candidate data. Every example score was worked out by running the ruleset on a fabricated candidate.

---

## Summary

| ID | Topic | Code does | CRITERIA.md says | Question for Karim | Affects |
|---|---|---|---|---|---|
| D-01 | Track A age band | Rejects over 32 and under 21 | 19–35 | Is the Track A band 21–32, 19–35, or something else, pending legal review? | Track A |
| D-02 | Track B age band | Rejects over 38 and under 25 | 27–40 in the table; reject over 40 or under 22 in the list; "floor of 25" still open | Is the Track B band 25–38, 27–40 or 22–40? Where does a 21–24-year-old Team Leader go? | Track B (and A) |
| D-03 | Sales fit missing from Track A table | Sales fit is worth 25, in bands 25/15/8/0 by keyword count | Table has no Sales fit row; its rows add up to 75 | Confirm Sales fit 25 and its bands, and the keyword list | Track A |
| D-04 | Undocumented Track A bonuses and penalties | Adds six adjustments on top of the base score | Mentions none of them | Which of the six are ratified, and at what values? | Track A |
| D-05 | Track A hard gates not written down | Seven gates, including own-staff and Team Leader routing | Track A has no disqualifier section | Confirm the gate list and the P4 "Archive" label for routed people | Track A |
| D-06 | Track A experience limit | Rejects 11+ years, or text mentioning 12–20 years; 9–10 years scores 0 | 0–10 years | Is the limit 10 years? Should free text be allowed to reject someone? | Track A |
| D-07 | Track B employer rule and crossover lists | Crossover employers score 12 or 8; lists differ from the spec | Non-RE = reject in the table; relaxed later; lists differ | Is the crossover cohort approved (OPN-05), and which list is right? | Track B |
| D-08 | Track B title table | "Associate" titles score as the full title; bank and insurance roles are rejected | Associate rows score 22/18/13; bank and insurance roles are targets | Should Associate titles score lower? Which job titles count for crossover candidates? | Track B |
| D-09 | "The Address" in own-staff and competitor lists | Same firm is both "our staff" and "competitor +10" | Not covered | Is The Address Consultancy own staff or a competitor? Does bare "The Address" mean us? | Both |
| D-10 | Track B has no own-staff or foreign-country gate | Our own staff and people abroad can reach T2 | Track B rejects anyone outside Cairo | Should Track B have the same own-staff and country gates as Track A? | Track B |
| D-11 | Loose substring matching in keyword lists | Short words match inside other words | Not covered | Should every list match whole words only? | Both |
| D-12 | Track B title gate still has the "coo" bug | "Coordinator" counts as "COO" in Track B | Not covered | Confirm Track B should get the same whole-word fix as Track A | Track B |
| D-13 | Location vocabulary | "6 October City" scores 5; road names can reject a New Cairo address | 6 October = 15 | Confirm tiers for missing areas and the order of the location checks | Both |
| D-14 | Scores depend on the day they run | Recency uses today's date; the year 2026 is fixed in code | Not covered | Should recency be measured from a stored date, or dropped from the deterministic score? | Track A (age: both) |
| D-15 | Age inferred from graduation year or experience | Guesses age from free text; small words change the guess | Not covered | Is inferring age acceptable (OPN-02)? If yes, which inputs may be used? | Both |
| D-16 | Students' entry year read as graduation year | Some students score 20 as "recent grad" instead of 15 | Final-year student = 15 | Should a student always score 15? | Track A |
| D-17 | Phone format and contact score | A mobile number written with spaces scores 5, not 10 | Egyptian phone = 10 | Should the score depend on how the number is typed? | Track A |
| D-18 | Dead code and stale comments | An unreachable check, misleading comments, reused result fields | Not covered | Confirm these carry no rule meaning | Both |

---

## D-01 — Track A age band

**What the code does**

- Rejects anyone whose age, stated or guessed, is over 32: `v2026_08_04.py:554-557`.
- Rejects anyone under 21: `v2026_08_04.py:559-561`.
- The file header says "Age 21–32 only": `v2026_08_04.py:20`.
- The `_score_entry_level` docstring says "Age 21–30 is already confirmed by the hard-disqualifier gate": `v2026_08_04.py:635`. That is wrong. The gate allows up to 32.
- The test `under-21` pins the lower gate.

**What CRITERIA.md says**

"Two pipelines, never mixed" table, Age row: Track A **19–35**. The Track A section itself has no age rule.

**Why it matters**

A made-up 33-year-old "Sales Associate" in Nasr City scores 0 and goes to P4 under the code. Under CRITERIA.md they would be scored normally. A 19- or 20-year-old is rejected by the code but allowed by CRITERIA.md. The gap covers five ages in total.

**Question**

What is the Track A age band? Options: (a) 21–32, as the code does; (b) 19–35, as CRITERIA.md says; (c) another band; (d) no age gate, depending on the legal review in D-15.

**Related:** BR-301, CR-07, OPN-02, BRD 6.1 Layer 0 ("age outside band").

---

## D-02 — Track B age band

**What the code does**

- Rejects over 38: `v2026_08_04.py:1025-1029`.
- Rejects under 25 with the reason "Under 25 — route to entry-level Track A instead": `v2026_08_04.py:1030-1034`.
- The test `over-38` pins the upper gate.

**What CRITERIA.md says**

It gives three different answers:

- "Two pipelines" table: Track B age **27–40**.
- "Hard disqualifiers (Track B)": "Age **> 40 or < 22** (under-22 routes back to Track A instead)".
- "Open items still pending user input", item 3: "Track B age floor of 25" is still awaiting confirmation.

**Why it matters**

- A made-up 39-year-old "Sales Manager" at a competitor is rejected by the code but allowed by all three CRITERIA.md versions.
- A person can end up in neither track. Track A sends every Team Leader or Supervisor to Track B (`v2026_08_04.py:549-552`). Track B sends anyone under 25 back to Track A. A made-up 23-year-old "Team Leader" in Maadi is rejected by both tracks, and neither message says so. BRD 2.3 says Team Leaders are "routed, not rejected".

**Question**

1. What is the Track B age band? Options: (a) 25–38, as the code does; (b) 27–40; (c) 22–40; (d) no age gate, depending on the legal review.
2. Where should a Team Leader or Supervisor under the Track B floor go? Options: (a) Track B anyway; (b) the profiling record with no score; (c) Track A, with the routing rule relaxed for them.

**Related:** BR-301, CR-07, OPN-02, BRD 2.3 ("routed, not rejected").

---

## D-03 — Sales fit is missing from the Track A scoring table

**What the code does**

- Sales fit is one of five base components, worth up to 25: `v2026_08_04.py:609-630`, added at `v2026_08_04.py:1142-1144`.
- It counts keyword hits in title, skills, summary, education details and raw text. 4 or more hits = 25, 2–3 = 15, 1 = 8, none = 0: `v2026_08_04.py:624-630`.
- Keyword lists: English `v2026_08_04.py:113-131`, Arabic `v2026_08_04.py:133-143`.
- Overlapping keywords are counted separately. "Salesperson" hits both "sales" and "salesperson", so it counts as 2 and scores 15.
- The file header gives the full scale: "Location 30 · Sales fit 25 · Entry level 20 · Education 15 · Contact 10": `v2026_08_04.py:11-12`.

**What CRITERIA.md says**

"Track A — Scoring (max 100 — ratified 2026-08-04)". The note says "The five components below sum to exactly 100". The table has only four rows: Location 30, Entry level 20, Education 15, Contact 10. They add up to **75**. Sales fit, its bands and its keyword list are not written anywhere. "Open items", item 3, also lists "sales-fit weighting" as still unconfirmed.

**Why it matters**

Sales fit is a quarter of the score. The code and BRD 6.1 both use 25. But the written spec cannot be used to check the rule, the bands, or whether counting "sales" twice in "Salesperson" is intended. A made-up "Seller" gets 15 (hits "sell" and "seller"). A made-up "Sales Rep" gets 8.

**Question**

Confirm Sales fit = 25 with bands 4+/2–3/1/0 → 25/15/8/0. Should overlapping keywords count once or separately? Should the keyword lists be written into the next spec?

**Related:** BR-301, BRD 6.1 Layer 1, NFR-07 (bilingual lists).

---

## D-04 — Track A bonuses and penalties that CRITERIA.md never lists

**What the code does**

After the base score, `score_candidate` adds these, then limits the total to 0–100 (`v2026_08_04.py:1188-1194`):

| Adjustment | Values | Code | Written anywhere else? |
|---|---|---|---|
| Competitor brokerage background | +10 | `v2026_08_04.py:734-763` | File header only (`:13`, `:24`) |
| LinkedIn "Open to Work" badge | +10 | `v2026_08_04.py:877-889` | File header only (`:13`) |
| TikTok source | −5 | `v2026_08_04.py:766-774` | File header only (`:14`, `:25`) |
| Remote-only preference | −10 | `v2026_08_04.py:892-904` | File header only (`:14`) |
| Recency of last activity | +10 (≤7 days), +5 (≤30), 0 (31–90), −5 (>90) | `v2026_08_04.py:777-842` | Nowhere |
| Profile quality | −20 (no photo and under 10 connections), −10 (no photo), −5 (under 10 connections) | `v2026_08_04.py:845-874` | Nowhere |

- The header says the first four come from `CLAUDE.md`, ratified 2026-08-04. It does not mention recency or profile quality.
- BRD 6.1 says only "bonuses applied on top and capped at 100".
- The TikTok check compares the source to lowercase "tiktok" exactly (`v2026_08_04.py:772`). A source written "TikTok" gets no penalty.

**What CRITERIA.md says**

Nothing. The Track A section has the four-row table and the tiers only.

**Why it matters**

These adjustments move people across tiers. A made-up "Sales Associate" in Maadi with a bachelor's degree, 1 year of experience and a phone number scores 83 (P1). With no profile photo, the same person scores 73 (P2). A TikTok applicant scores 78 or 83, depending only on how the source name is capitalised. Recency also makes scores change from day to day (D-14).

**Question**

For each of the six adjustments, choose: (a) keep at the current value; (b) keep with a different value; (c) record as a signal only, with no points. Separately: should recency and profile quality have been in the 4 August ratification at all?

**Related:** BR-301, BRD 6.1 Layer 1, NFR-08, D-09, D-14.

---

## D-05 — Track A hard gates are not written down

**What the code does**

`_check_hard_disqualifiers` (`v2026_08_04.py:490-570`) rejects, in this order:

1. Current TAI staff, matched on current employer or title: `:501-505`.
2. A country outside Egypt, matched on whole words: `:514-516`.
3. A place outside Greater Cairo: `:519-521`.
4. A managerial title, whole words, where "Senior" alone is allowed: `:534-544`.
5. Team Leader or Supervisor, routed to Track B: `:549-552`.
6. Age over 32 or under 21: `:554-561`.
7. 12–20 years mentioned in text, or 11+ years of experience: `:564-568`.

Every rejection, including own staff and routed Team Leaders, is given score 0, P4 and "Poor Match - Archive": `v2026_08_04.py:1127-1135`. Tests pin gates 1, 3, 4, 5 and 6.

**What CRITERIA.md says**

Track B has a "Hard disqualifiers" list. Track A has none. Own staff, routing, the country list and the managerial title list are not mentioned. BRD 2.3 records several of these as rulings that must be preserved.

**Why it matters**

Engineers cannot check the gates against a written rule. A routed Team Leader and a current employee are both labelled "Poor Match - Archive". BRD 2.3 says routed people are "not rejected" and own staff are "profile only". The P4 label could be read as a rejection in reports and in the reasons given to a candidate (CR-04).

**Question**

1. Confirm the seven gates and their order as the Track A gate list.
2. Should routed Team Leaders and current staff keep the P4 "Archive" label? Or should they get a separate outcome, such as "Routed" or "Profile only"?

**Related:** BR-301, BR-302, CR-04, CR-05, BRD 2.3, BRD 6.1 Layer 0.

---

## D-06 — Track A experience limit

**What the code does**

- Rejects `years_experience >= 11`: `v2026_08_04.py:567-568`.
- Rejects any summary, raw text or skills mentioning 12 to 20 "years", "yrs", "سنوات" or "سنة": `v2026_08_04.py:564-565`. The pattern does not check what the number refers to.
- 9–10 years is not rejected. It scores 0 entry-level points and gets the flag "Too much experience for entry level": `v2026_08_04.py:670-672`. The comment there says "9+ years → caught by hard disqualifier", which is not true.
- When age is unknown, age is guessed as 22 + years of experience (`:470-471`). So 11 years usually hits "Over 32" first, and the 11-year gate only decides when an age or graduation year is present.
- The test `ten-years-experience` pins 10 years at 63 (P2).

**What CRITERIA.md says**

"Two pipelines" table: Track A experience **0–10 yrs**. "Track A — Scoring", Entry level row: bands up to "7–8 yrs = 5", with nothing for 9–10. "Sourcing playbook", Wuzzuf step: "0–5 yrs".

**Why it matters**

- The number limit matches (10 allowed, 11 rejected). The text rule does not. A made-up summary "I am 20 years old" is rejected as "Mentions 12+ years experience". So is "joined a firm with 15 years in the market".
- 9–10 years sits in a band CRITERIA.md does not define.

**Question**

1. Confirm the limit: 10 years allowed, 11 rejected.
2. Should free text be able to reject someone? Options: (a) keep; (b) only when "experience" follows the number; (c) remove and rely on the number field.
3. What does 9–10 years score: 0, 5, or a rejection?

**Related:** BR-301, BRD 6.1 Layer 0 ("eleven or more years").

---

## D-07 — Track B employer rule and crossover lists

**What the code does**

`_competitor_match` (`v2026_08_04.py:934-966`) checks in this order:

1. Competitor brokerage = 25 (`:949-951`).
2. Any real-estate word = 12 (`:953-956`).
3. Premium crossover = 12 (`:958-960`).
4. Standard crossover = 8 (`:962-964`).
5. Otherwise 0, and the candidate is rejected (`:1049-1053`).

It searches current employer, title, summary and raw text together (`:947`). It does not use current employer alone.

**What CRITERIA.md says**

- "Track B — Scoring" table: "Non-RE = **DQ**".
- "Hard disqualifiers (Track B)": "**Not** currently at a real estate / brokerage / property company".
- "Scoring note for Track B expanded industries" relaxes this: premium retail, tobacco and tier-1 banks = 12; "Standard FMCG / telecom / insurance → **8 pts**".
- The spec talks about "the 22 competitors" three times.
- BRD OPN-05 says the crossover cohort is still only proposed.

**Mismatches**

| Point | Code | CRITERIA.md |
|---|---|---|
| Insurers (AXA and two others) | Premium, 12 (`:307`) | Standard, 8. The code's own docstring also says standard (`:944`) |
| Mainstream retail (Zara, H&M, LC Waikiki) and car showrooms | Premium, 12 (`:299-303`) | Lists them, but only "premium retail" is given a points value |
| Kia and Toyota dealers | Only "kia egypt" and "toyota egypt" match (`:303`) | "Kia, Toyota authorised dealers" |
| Accented brand names such as "L'Oréal" | Only unaccented spellings; no accent folding, so "L'Oréal" gets 0 | Listed with accents |
| Tobacco (JTI, PMI, BAT, IQOS, Imperial Brands, Seita) | Premium, 12 (`:277-281`) | Named as a scoring category, but no tobacco firm is listed |
| Brands only in code | Bottega Veneta, Guess, Pull&Bear, Bershka, Stradivarius, Folli Follie, Omega (`:286-296`); Juhayna, Edita, PepsiCo, Coca-Cola, "we" (`:319-326`) | Not listed |
| Number of competitors | 21 distinct firms (`:234-263`); the Track A docstring names only 6 (`:739`) | "22 competitors" |
| Current vs past employer | Any mention in summary counts | "Confirmed competitor", "currently at" |
| Real-estate words | "developments", "property" and others anywhere in text = 12 | "Other RE company" |

**Why it matters**

- A made-up current "Vodafone" employee whose summary says "previously at Nawy" scores 25 as a competitor hire, not 8 as crossover.
- A made-up summary saying "business developments" scores 12 as a real-estate employer.
- A crossover candidate could be scored and contacted before OPN-05 is decided.

**Question**

1. Is the crossover cohort approved as a scored source (OPN-05)? Options: (a) yes, as coded; (b) yes, with the CRITERIA.md weights; (c) no — go back to "Non-RE = reject".
2. Which list is authoritative, and which firm is the 22nd competitor?
3. Should the employer score use the current employer only, or any mention in the text?

**Related:** BR-301, OPN-04, OPN-05, D-08, D-11.

---

## D-08 — Track B title table: Associate titles and crossover roles

**What the code does**

- `_title_fit_headhunt` keeps the highest-scoring title found anywhere in the job title (`v2026_08_04.py:922-931`). "Associate Team Leader" contains "team leader" (20), which beats "associate team leader" (18). The same happens for "Associate Supervisor" (15, not 13) and "Associate Sales Manager" (25, not 22). The Associate values at `v2026_08_04.py:194-201` can never be used.
- A title with no tenured match is rejected before the employer is looked at: `v2026_08_04.py:1036-1042`. Only Sales Manager, Team Leader and Supervisor families count.

**What CRITERIA.md says**

- "Tenured titles" table: Associate Sales Manager 22, Associate Team Leader 18, Associate Supervisor 13.
- "Expanded target industries": the targets include "Relationship managers, personal bankers, and bancassurance sales", "Direct sales agents and brokers" at insurers, and "B2B account managers" at telecoms.

**Why it matters**

- A made-up "Associate Supervisor" gets 2 points more than the spec says. Associate titles are never scored lower.
- A made-up "Relationship Manager" at a tier-1 bank is rejected with "No tenured title match", even though CRITERIA.md names that role as a target. The crossover scoring in D-07 only reaches crossover people who already hold a Sales Manager, Team Leader or Supervisor title.

**Question**

1. Should Associate titles score as in the table (22/18/13), or the same as the full title?
2. For crossover employers, which titles qualify? Options: (a) only the tenured families, as coded; (b) add named roles such as Relationship Manager and Account Manager, with points to be set.

**Related:** BR-301, OPN-05, D-07.

---

## D-09 — "The Address" in both the own-staff and competitor lists

**What the code does**

- "the address consultancy" is in `OWN_COMPANY_SIGNALS` (`v2026_08_04.py:178`) and in `COMPETITOR_BROKERAGES` (`v2026_08_04.py:249`), along with "address consultancy". Arabic "العنوان" ("the address") and "العنوان للاستشارات" are also competitors (`:261`).
- The own-staff check runs first and looks only at current employer and title (`:501-505`). A current Address Consultancy employee is excluded as staff. A *past* one gets **+10 competitor bonus**. The rehire list (`:745-746`) has no "consultancy" or "holding" entry, so the rehire check does not catch them.
- Bare "the address" is an own-staff signal (`:183`). The comment says it was flagged to Karim. Any employer name that starts with those words is treated as TAI staff.
- There is no Arabic own-staff signal. An Arabic current employer naming TAI is not excluded. It can pick up the competitor bonus through "العنوان" instead.
- The Track A competitor bonus reads title, skills, summary, education details and raw text (`_combined_text`, `:583-585`). It does **not** read `current_employer`. A made-up candidate whose only competitor evidence is current employer "Nawy" gets no bonus.

**What CRITERIA.md says**

Nothing about own staff, rehires or the Track A competitor bonus.

**Why it matters**

- A made-up summary "Previously at The Address Consultancy" earns +10 as competitor experience.
- A made-up current employer "The Address Tower Hotel" is excluded as TAI staff.
- A made-up Arabic "مستشار عقاري" whose current employer is written in Arabic as a TAI company is scored (38, P3) as a normal candidate.

**Question**

1. Is The Address Consultancy part of TAI (own staff, rehire) or a competitor (+10)?
2. Does bare "The Address" as an employer mean TAI? (OPN-03)
3. Should own-staff and rehire signals include Arabic names?
4. Should the Track A competitor bonus look at current employer?

**Answer to question 2 (15 September 2026):** yes. A current employer of exactly "The Address" means The Address Investments, so sheet rows 53 and 1478 stay excluded as own staff (OPN-03). Questions 1, 3 and 4 are still open.

**Related:** BR-301, OPN-03, NFR-07, BRD 2.3 ("Own staff are never sourced"), D-10.

---

## D-10 — Track B has no own-staff gate and no foreign-country gate

**What the code does**

`_score_headhunt` (`v2026_08_04.py:997-1106`) runs these gates only:

- Too senior (`:1006-1013`)
- Outside Cairo, using the Egyptian place list only (`:1015-1021`)
- Age (`:1023-1034`)
- No tenured title (`:1036-1042`)
- Employer not recognised (`:1049-1053`)

It never checks `OWN_COMPANY_SIGNALS` or `NON_EGYPT_COUNTRIES`.

**What CRITERIA.md says**

"Hard disqualifiers (Track B)": "Outside Cairo". Own staff is not mentioned. BR-301 lists "the own-staff exclusion" among the rules to reproduce for both tracks. BRD 2.3 says "Own staff are never sourced."

**Why it matters**

- A made-up "Sales Manager" at "The Address Real Estate" in New Cairo with 4 years' tenure scores **77 (T2)** as a headhunt target.
- A made-up "Sales Manager" at a competitor located in "Dubai, UAE" scores **78 (T2)**. The foreign location only lowers location points to 3.

**Question**

Should Track B use the Track A own-staff gate and the Track A country gate? Options: (a) yes, both; (b) own-staff only; (c) keep as coded.

**Related:** BR-301, BRD 2.3, OPN-03, D-09.

---

## D-11 — Loose substring matching in keyword lists

**What the code does**

Whole-word matching was added on 3 August 2026 in only three places: countries (`v2026_08_04.py:514-516`), managerial titles (`:542-544`) and Track B routing titles (`:549-552`). Every other list uses a plain "is this text inside that text" test, so a short word matches inside longer words:

| List | Code | Short entries | Made-up text that matches |
|---|---|---|---|
| Competitor brokerages (A: +10; B: 25) | `:234-263`, tested at `:753-755` and `:949-951` | "red", "views", "element", "nod", "b2b", "y brokers", "ريد", "نود", "العنوان", "بيوت" | "handled credit card sales"; "conducted customer interviews"; "achieved required targets"; "B2B sales"; "worked with many brokers"; "Graduated from elementary school"; "أريد العمل في المبيعات" ("I want to work in sales"); an address label "العنوان: مدينة نصر". Each gives a Track A candidate +10 |
| Premium crossover (B: 12) | `:275-315`, tested at `:958-960` | "bat", "lv", "mac", "coach", "guess", "omega", "pmi", "cib", "faces", "mango" | "involved in field sales" matches "lv"; job title "Sales Coach" matches "coach"; "Combat Gym" matches "bat"; "Machinery Supplies" matches "mac". Each scores 12 as crossover |
| Standard crossover (B: 8) | `:317-327`, tested at `:962-964` | "we", "orange" | "Answer phones" or "Lowest churn in the team" contain "we" and score 8. So "Employer not recognised" almost never applies when any English summary exists |
| Real-estate words (B: 12) | `:953-956` | "property", "developments" | "business developments" |
| Sales keywords (A: Sales fit) | `:113-143`, tested at `:614-622` | "بيع", "حسابات", "محل", "store" | "طبيعي" ("normal") contains "بيع"; an accountant's "حسابات" ("accounts") counts as sales |
| Track B too-senior titles | `:205-210`, tested at `:1007-1008` | "coo", "vp", "chief", "owner" | See D-12 |
| Tenured titles | `:192-202`, tested at `:927-928` | — | See D-08 |
| Own-staff signals | `:175-184`, tested at `:502-503` | "the address" | See D-09 |
| Outside Cairo and location tiers | `:39-89`, tested at `:519-526`, `:592-600`, `:1016-1017`, `:1068-1071` | "suez", "ismailia", "port said", "بدر", "المستقبل" | See D-13 |
| Postgraduate detection (age) | `:435-442` | "master", "med ", "mba", "dba" | See D-15 |

**Why it matters**

These matches decide competitor bonuses, Track B employer points and gates. So ordinary words in a CV change scores and tiers. The 3 August Shorouk and "coo" fixes (BRD 2.3) show the same class of bug has already cost real candidates.

**Question**

For the next criteria version, should all keyword lists match whole words only? Options: (a) all lists; (b) all lists except named exceptions; (c) keep substring matching and remove only the short, ambiguous entries. Any change here will move scores. It needs the difference report in BR-304 before activation.

**Related:** BR-301, BR-304, NFR-07, BRD 2.3, D-07 to D-10, D-12, D-13, D-15.

---

## D-12 — The Track B title gate still has the "coo" bug

**What the code does**

- Track A matches managerial titles on whole words. This was fixed on 3 August 2026 so that "Sales Coordinator" no longer matches "coo": `v2026_08_04.py:537-544`. It is pinned by `test_coordinator_is_not_a_coo`.
- Track B's "too senior" check still uses a plain substring test on the same kind of list, which includes "coo": `v2026_08_04.py:205-210`, `v2026_08_04.py:1007-1008`.

**What CRITERIA.md says**

"Hard disqualifiers (Track B)" lists "CEO/COO/CFO" as too senior. It does not mention Coordinator.

**Why it matters**

A made-up "Team Leader and Sales Coordinator" at a competitor is rejected as "Too senior for agent recruit" and scores 0 (T4). The same issue affects any title containing "coo" (such as "Coordination"), "vp" or "chief".

**Question**

Should the Track B too-senior gate use whole-word matching, as Track A does since the 3 August ruling? Options: (a) yes; (b) keep as coded.

**Related:** BR-301, BRD 2.3 ("The Shorouk bug"), D-11.

---

## D-13 — Location vocabulary and the order of location checks

**What the code does**

- Tier 2 matches "6th of october" and "السادس من أكتوبر" only (`v2026_08_04.py:62`). "6 October City" or "6 أكتوبر" does not match. It falls to "unknown location" and scores 5 (`:602-604`).
- Common areas such as Sheikh Zayed, Madinaty and the New Administrative Capital are in no list and score 5.
- The outside-Cairo check runs before any Cairo match (`:519-521`). A made-up location "Cairo-Suez Road, New Cairo" is rejected as "Outside Cairo" because it contains "suez". Track B does the same (`:1016-1017`).
- Arabic "بدر" in Tier 1 (`:47`) matches inside "البدرشين", a Giza district, which then scores 30 as "Near New Cairo".

**What CRITERIA.md says**

"Track A — Scoring", Location row: "Cairo / Giza / **6 October** / 10th of Ramadan / other Cairo districts = **15**". "Track B — Scoring", Location row: "Cairo / Giza / 6 Oct / 10th Ramadan = 10". "Open items", item 3: "Tier 2 location pts" is still unconfirmed.

**Why it matters**

A made-up applicant from "6 October City" gets 5 location points instead of 15. That is a 10-point gap, enough to change a tier. A New Cairo address on a Suez or Ismailia road name is rejected outright.

**Question**

1. Which tier should 6 October (any spelling), Sheikh Zayed, Madinaty and the New Administrative Capital get?
2. Should a Cairo area match take precedence over an outside-Cairo word in the same location text?
3. Confirm Tier 2 = 15 points (the open item).

**Related:** BR-301, NFR-07, D-11.

---

## D-14 — Scores depend on the day they are run

**What the code does**

- Recency compares an ISO date in `last_active` with `date.today()`: `v2026_08_04.py:823-825`. The bands are at `:832-842`.
- Relative text such as "3 days ago" is read as days before *scoring*, not before the profile was captured: `:803-819`.
- `CURRENT_YEAR = 2026` is fixed in code, with the comment "Update annually": `v2026_08_04.py:32`. It drives age inference (`:447`, `:466`) and "recent grad" (`:640`). "Fresh graduate" uses a fixed 2022 (`:645`).

**What CRITERIA.md says**

Nothing about recency. "Recent grad (≤ 3 yrs since graduation)" and "Fresh grad (2022+)" appear in the Entry level row.

**Why it matters**

A made-up candidate in Maadi with `last_active` "2026-09-10" scores 40 (P3) when run on 14 September. The same record scores 30 (P4) on 15 October and 25 on 20 December. Nothing about the candidate changed. On 1 January 2027, unless someone edits the code, every guessed age and "recent grad" check becomes a year out of date. Editing it would also break the rule that v2026-08-04 never changes. Either way, NFR-08 and the golden replay (BR-301) fail.

**Question**

1. Recency: options (a) measure it from a date stored with the evaluation, such as the capture date; (b) keep it as a signal only, with no points; (c) drop it.
2. Should the reference year be the evaluation date stored with the result, rather than a fixed constant?

**Related:** NFR-08, BR-301, BR-307, D-04.

**Evidence from the baseline (14 September 2026)**

Replayed with the workbook's last-active date, 1,880 of the 3,940 stored scores match exactly. Replayed without it, 3,938 match. The stored scores were produced without recency points, so the legacy output already follows CRITERIA.md on this point. The replay leaves the date out (`src/replay/mapping.py`). Whether recency belongs in a future criteria version is still a ruling for Karim.

---

## D-15 — Age inferred from graduation year and experience

**What the code does**

`_infer_age` (`v2026_08_04.py:385-483`) takes the first of these that applies:

1. Stated age (`:408-409`).
2. Graduation year plus an assumed graduation age: 22 by default, 23 if the text mentions engineering, 24 if it mentions medicine or pharmacy (`:451-466`). For students, graduation year is read as the entry year plus 18 (`:445-447`).
3. 22 + years of experience (`:470-471`).
4. An age written in the text (`:474-481`).

The keyword checks for steps 1–2 read summary and raw text as well as education (`:430-432`), with substring matching:

- "pharm" matches "pharmaceutical sales rep". A made-up commerce graduate from 2016 is then guessed at 34 instead of 32, and rejected as over 32.
- "sales engineer" in the summary adds one year.
- Postgraduate words switch off the graduation-year guess (`:435-442`). "mastered negotiation" or "named top seller of the quarter" ("med ") do this. A made-up 2010 graduate (normally guessed at 38, and rejected) then has no age and passes the age gate.

**What CRITERIA.md says**

Nothing about how age is found.

**Why it matters**

The result is a hard gate. BRD section 9 flags age bands, and guessing age from graduation year, for legal review before the first criteria version is activated (CR-07, OPN-02). The guess also moves with unrelated wording in the summary, which is hard to explain to a candidate (CR-04).

**Question**

1. Pending legal: may age be guessed at all? Options: (a) stated age only; (b) stated age and graduation year; (c) all four methods, as coded.
2. If guessing stays, should the degree keywords read only education fields, not summary and raw text?

**Ruling (14 September 2026):** Legal and Karim allow age limits as hard gates, and allow age to be guessed from graduation year (OPN-02). The question above narrows to how the guess is made: which words may change it, and whether a guess from years of experience is also allowed. Which bands apply is still D-01 and D-02. Record this ruling against each criteria version (CR-07).

**Related:** CR-07, OPN-02, CR-04, BRD section 9 "Flagged for legal review", D-01, D-02, D-11.

---

## D-16 — A student's entry year is read as a graduation year

**What the code does**

- For students, `graduation_year` holds the year they *entered* university (`v2026_08_04.py:421-423`, `:445-447`).
- `_score_entry_level` checks "recent grad" (`graduation_year >= CURRENT_YEAR - 3`, `:640-644`) and "fresh graduate" (`>= 2022`, `:645-647`) *before* checking `is_student` (`:649-651`).
- A student who entered in 2022 or later gets 20 points and a "Recent grad" or "Fresh graduate" signal. The 15-point student band applies only to students who entered in 2021 or earlier, or who have no year.

**What CRITERIA.md says**

"Track A — Scoring", Entry level row: "Final-year student = 15".

**Why it matters**

A made-up student in Maadi with job title "Sales" who entered university in 2023 scores 58 (P2), with the signal "★ Recent grad (2023)". Scored at 15 as a student, they would get 53 (P3).

**Question**

Should a current student always score 15, whatever year is stored? Options: (a) yes; (b) keep as coded.

**Related:** BR-301, D-15.

---

## D-17 — Phone format changes the contact score

**What the code does**

`_score_contact` gives 10 only when the phone field contains 11 digits in a row starting "01", optionally after "+20" (`v2026_08_04.py:717-722`). Any other non-empty phone, or an email, gives 5 (`:723-725`). A profile link gives 3 (`:726-728`).

**What CRITERIA.md says**

"Track A — Scoring", Contact row: "Egyptian phone (01XXXXXXXXX) = 10. Email/partial = 5. Profile URL only = 3".

**Why it matters**

A valid Egyptian mobile typed with spaces or dashes scores 5, not 10. The 5-point gap can change a tier. BRD RSK-10 already shows contactability is the pipeline's weakest point.

**Question**

Should a valid Egyptian mobile score 10 however it is formatted? Options: (a) yes — clean up formatting before scoring; (b) keep as coded. Either way, the replay must still reproduce v2026-08-04 as it is.

**Related:** BR-301, BR-109, RSK-10.

---

## D-18 — Dead code and stale comments

**What the code does**

- **Unreachable check.** `v2026_08_04.py:523-530` rejects a location only if it contains an outside-Cairo word. The loop just before (`:519-521`) has already returned for any such word. So `is_outside` is always false there, and the check never fires. The comment at `:523` says an unknown location is rejected, but unknown locations actually score 5 (`:602-604`).
- **Misleading comment.** `:670` says 9+ years is caught by the hard gate. Only 11+ is (D-06).
- **Private-university bonus.** The comments at `:212-216` and `:697-700` still describe "+5". The function returns 0 (`:702-712`), and that 0 is still added to the total (`:1191`). CRITERIA.md does not mention private universities, so this is not a disagreement with the spec, only stale text.
- **Lost signal.** `:1056` adds "★ CROSSOVER — non-RE background, strong sales profile" to `result.key_signals`. `:1091` then replaces that list, so the line is lost. The crossover signal at `:1055` survives.
- **Reused result fields.** In Track B, `sales_fit_score` holds title points, `entry_level_score` holds tenure, `competitor_bonus` holds employer points and `platform_adjustment` holds move-signal points: `:1083-1089`.
- **Missing recommendation.** Track B rejections other than "too senior" set no recommendation text: `:1018-1053`.

**What CRITERIA.md says**

Not covered.

**Why it matters**

None of these changes today's scores, and the port must keep them for parity. But the reused Track B fields will label reasons wrongly if they are shown to a reviewer or candidate as they are (CR-04, BR-307). The stale comments invite a wrong "fix".

**Question**

Confirm that none of these carries rule meaning. Options: (a) confirmed — the engineers map the Track B fields to correctly named components when storing evaluations, and a later version removes the dead code; (b) one of them was meant to be a rule — say which.

**Related:** CR-04, BR-307, BR-303.

---

## Question for TA leadership: answered

OBJ-01 says "≥ 95% of active applications" carry a current stage 30 days after go-live. Neither the BRD nor the criteria defined "active".

**Answer (14 September 2026): an active candidate is anyone still in the process, that is, not yet hired and not rejected.** Any stage other than `hired` or `rejected` counts as active, whatever its age or requisition.

**What this means for the migrated record.** The workbook holds no hires and no rejections, so all 5,140 migrated candidates count as active today, and 5,125 of them have no stage at all. To reach 95%, each one needs a recorded stage, or a recorded rejection with its stage and reason (BR-404). Stages are never back-filled by assumption (BR-703). Two decisions follow, so the target is not reached by guessing:

1. OPN-11: are candidates with no phone or email still in the process, or archive?
2. Are archived records (BR-205) outside the process, like hired and rejected?

---

## When CLAUDE.md arrives

Re-run this comparison three ways: code, `CRITERIA.md` and `CLAUDE.md`. The code header (`v2026_08_04.py:11-14`) and the CRITERIA.md corrections of 4 August both say `CLAUDE.md` was ratified as the single source of truth. So check every item above against it: it may already answer some questions (for example the Sales fit row in D-03 or the bonus values in D-04), and it may add new differences. Record, for each item, which of the three sources agree. Keep the rule that the code wins until Karim rules. Then update this file's status and date before sending it back to Karim.
