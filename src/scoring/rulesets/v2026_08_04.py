# Criteria version 2026-08-04 - Track A and Track B scoring, ported verbatim from Leila AI
# TAI/scorer.py. Immutable (docs/BRANCHING.md): a rule change is a new ruleset module,
# never an edit here. Excluded from ruff and mypy on purpose - parity with the legacy output
# comes before tidying. Removed from the original: the author's contact details and the
# __main__ self-test, now tests/unit/test_scoring_ruleset_v2026_08_04.py.
"""
scorer.py — Candidate Scoring Engine
Owned & built by Karim AlAkkad -- HRIS Lead, The Address Investments.
The Address Investments (TAI) Brokerage — Cairo, Egypt
Scores candidates 0-100 against real estate sales agent hiring criteria.
Track A base scale (CLAUDE.md, ratified 2026-08-04) sums to exactly 100:
  Location 30 · Sales fit 25 · Entry level 20 · Education 15 · Contact 10
Bonuses sit on top and the total is capped at 100: +10 competitor, +10 open-to-work,
-5 TikTok source, -10 remote-only.

Hiring constraints:
  - Cairo ONLY — candidates outside Greater Cairo are auto-disqualified
  - Preferred areas near New Cairo (Tier 1 location)
  - Giza, 6th of October, and 10th of Ramadan = Tier 2 (close but not ideal)
  - Age 21–32 only — under 21 or over 32 are auto-disqualified
  - Managerial titles (Director/GM/Manager etc.) are a hard disqualifier.
    "Senior" alone is NOT — seniority is experience, not management (Karim 2026-08-04).
  - Team Leaders / Supervisors leave Track A and are routed to profiling (Track B targets)
  - Competitor brokerage background = +10 bonus (preferred, not required)
  - TikTok source = −5 platform reliability adjustment (high quantity, lower quality)
"""

import re
from dataclasses import dataclass, field
from typing import Optional

CURRENT_YEAR = 2026   # Update annually

# ─────────────────────────────────────────────
# Location sets
# ─────────────────────────────────────────────

# Tier 1: New Cairo + closest neighbours (30 pts)
NEAR_NEW_CAIRO = {
    "new cairo", "القاهرة الجديدة",
    "fifth settlement", "التجمع الخامس", "تجمع خامس",
    "nasr city", "مدينة نصر", "نصر سيتي",
    "heliopolis", "مصر الجديدة", "masr el gedida", "masr el gdida", "misr el gedida",
    "maadi", "المعادي",
    "rehab", "الرحاب",
    "mostakbal", "المستقبل",
    "badr city", "بدر",
    "shorouk", "الشروق",
    "obour", "العبور",
}

# Tier 2: Other Cairo areas (15 pts — still acceptable, just farther)
OTHER_CAIRO = {
    "cairo", "القاهرة",
    "zamalek", "الزمالك",
    "dokki", "الدقي",
    "mohandessin", "المهندسين",
    "agouza", "العجوزة",
    "hadayek", "حدائق",
    "ain shams", "عين شمس",
    "shoubra", "شبرا", "shubra el kheima", "شبرا الخيمة",
    "6th of october", "السادس من أكتوبر",
    "giza", "الجيزة",
    "haram", "الهرم",
    "faisal", "فيصل",
    "omraneya", "العمرانية",
    "imbaba", "إمبابة",
    "rod el farag", "روض الفرج",
    "matareya", "المطرية",
    "abbassia", "العباسية",
    "abdin", "عابدين",
    "sayeda zeinab", "السيدة زينب",
    "10th ramadan", "10th of ramadan", "العاشر من رمضان",
    "mokattam", "المقطم",
}

# Outside Cairo — hard disqualifier (0 pts + flag + auto P4)
OUTSIDE_CAIRO = {
    "alexandria", "الإسكندرية", "اسكندرية",
    "sinai", "سيناء", "north sinai", "south sinai", "شمال سيناء", "جنوب سيناء",
    "aswan", "أسوان", "luxor", "الأقصر",
    "hurghada", "الغردقة", "sharm", "شرم",
    "mansoura", "المنصورة", "tanta", "طنطا",
    "el mahalla", "المحلة", "zagazig", "الزقازيق",
    "ismailia", "الإسماعيلية", "suez", "السويس",
    "port said", "بورسعيد", "damietta", "دمياط",
    "minya", "المنيا", "sohag", "سوهاج", "qena", "قنا",
    "beni suef", "بني سويف", "fayoum", "الفيوم",
}

# Non-Egypt countries — auto-DQ regardless of anything else
# We hire Egyptians in Cairo ONLY; no relocation offered.
NON_EGYPT_COUNTRIES = {
    # South Asia
    "india", "pakistan", "bangladesh", "sri lanka", "nepal",
    # Gulf / MENA (expats welcome to apply but must relocate on own — flag it)
    "uae", "dubai", "abu dhabi", "sharjah", "saudi arabia", "riyadh", "jeddah",
    "kuwait", "qatar", "doha", "bahrain", "oman", "muscat",
    "jordan", "amman", "lebanon", "beirut", "iraq", "baghdad",
    "libya", "morocco", "algeria", "tunisia",
    "sudan", "khartoum",
    # Europe / Americas / Asia-Pacific
    "united states", "usa", "canada", "uk", "united kingdom", "germany",
    "france", "australia", "china", "japan", "turkey",
    # Generic signals
    "abroad", "overseas", "outside egypt", "خارج مصر",
}

# ─────────────────────────────────────────────
# Sales keywords
# ─────────────────────────────────────────────

SALES_KEYWORDS_EN = {
    "sales", "sell", "selling", "seller", "salesperson",
    "customer service", "customer care",
    "communication", "communicating",
    "retail", "shop assistant", "store",
    "hospitality", "hotel", "waiter", "waitress",
    "account manager", "business development",
    "crm", "client relations",
    # Real-estate SELLING roles (Karim's ruling 2026-08-04). Deliberately phrases
    # that name a selling job, never bare "real estate" / "property" — otherwise an
    # HR Officer or Administrative Assistant at a real-estate company would score
    # as a salesperson. These are the titles closest to what TAI actually hires.
    "property consultant", "property advisor", "property adviser", "property specialist",
    "property sales", "property executive", "property broker",
    "real estate consultant", "real estate advisor", "real estate adviser",
    "real estate specialist", "real estate broker", "real estate agent",
    "real estate sales", "real estate cold caller",
    "realtor", "estate agent",
}

SALES_KEYWORDS_AR = {
    "مبيعات", "بيع", "مندوب مبيعات", "مبيع",
    "خدمة عملاء", "خدمة العملاء",
    "تواصل", "تسويق",
    "ضيافة", "فندق", "استقبال",
    "علاقات عملاء", "حسابات",
    "تجزئة", "محل",
    # Real-estate selling roles — Arabic (Karim's ruling 2026-08-04)
    "مستشار عقاري", "استشاري عقاري", "وسيط عقارات", "وسيط عقاري",
    "سمسار عقارات", "مبيعات عقارية", "مندوب عقارات",
}

# Hard-disqualify managerial titles (rule updated 2026-04-14)
# RULE: "Senior" prefix alone is NOT a disqualifier — seniors are acceptable (not preferred).
# Only true management/director level and above are auto-DQ.
SENIORITY_DISQUALIFIERS = {
    "manager", "director", "head of", "vp", "vice president",
    "chief", "ceo", "coo", "cfo", "president", "owner", "founder",
    "general manager", "branch manager", "regional manager", "area manager",
    "group manager", "department manager",
    "مدير", "مدير عام", "مدير فرع", "مدير منطقة", "رئيس قسم", "نائب رئيس",
}
# Explicitly safe prefixes — "senior X" by itself is not a DQ
SENIORITY_SAFE_PREFIXES = {"senior", "sr.", "sr "}  # e.g. "senior sales specialist" passes

# Track B territory — supervisors and team leaders are NOT entry-level hires.
# Karim's ruling 2026-08-04: "stop scoring supervisors and team leaders as P1s in
# entry level sourcing, only profile them." They are removed from Track A scoring
# and routed to the Profiling record instead. They remain POSITIVE targets in
# Track B (TENURED_TITLES) — this check runs in entry mode only.
TRACK_B_ROUTE_TITLES = {
    "team leader", "team lead", "teamleader", "team-leader",
    "supervisor",
    "قائد فريق", "مشرف",
}

# Current TAI staff — never a sourcing target (Karim's ruling 2026-08-04):
# "there's no benefit in sourcing our own current employees, so don't add them to
# master. however, profile them accordingly."
# Matched on CURRENT employer and CURRENT title only — never on summary/raw text,
# because TAI appearing in someone's HISTORY makes them a *rehire candidate*, which
# is a different and legitimate case (see the competitor-bonus exception).
OWN_COMPANY_SIGNALS = {
    "the address invest", "address investments", "address investment",
    "the address holding", "address holding",
    "the address real estate", "the address consultancy",
    # Bare "The Address" — two master rows use exactly this as the employer.
    # In a Cairo real-estate dataset this reads as TAI; the failure modes are not
    # symmetric (a wrong exclusion costs one candidate, a wrong inclusion means
    # messaging our own staff), so it is matched. Flagged to Karim 2026-08-04.
    "the address",
}

REMOTE_ONLY_SIGNALS = {"remote only", "work from home only", "wfh only", "fully remote"}

# ─────────────────────────────────────────────
# Track B — Tenured movers (headhunt mode)
# These titles are POSITIVE in headhunt mode but DQ in entry-level mode.
# ─────────────────────────────────────────────
TENURED_TITLES = {
    # Sales Manager family (highest)
    "sales manager": 25, "senior sales manager": 25, "associate sales manager": 22,
    "مدير مبيعات": 25, "مدير مبيعات أول": 25,
    # Team Leader family
    "team leader": 20, "team lead": 20, "senior team leader": 22, "associate team leader": 18,
    "قائد فريق": 20, "قائد فريق أول": 22,
    # Supervisor family
    "supervisor": 15, "senior supervisor": 18, "associate supervisor": 13,
    "مشرف": 15, "مشرف أول": 18,
}

# Titles that remain DQ even in headhunt mode (too senior to recruit as agents)
HEADHUNT_HARD_DQ_TITLES = {
    "director", "head of", "vp", "vice president", "chief", "ceo", "coo", "cfo",
    "president", "owner", "founder", "general manager", "branch manager",
    "regional manager", "area manager", "group manager", "department manager",
    "مدير عام", "مدير فرع", "مدير منطقة", "نائب رئيس",
}

# ─────────────────────────────────────────────
# Private universities — +5 bonus for graduates
# These institutions signal higher English proficiency, presentation quality,
# and professional network — relevant advantages for RE sales roles.
# ─────────────────────────────────────────────
PRIVATE_UNIVERSITIES = {
    "american university in cairo", "auc",
    "german university in cairo", "guc",
    "british university in egypt", "bue",
    "msa university", "modern sciences and arts", "msa",
    "future university in egypt", "fue",
    "modern academy", "modern academy in maadi",
    "misr international university", "miu",
    "canadian international college", "cic",
    "arab academy for science", "arab academy",
    "october university", "october 6 university",
}

# ─────────────────────────────────────────────
# Competitor brokerages — preferred background
# +10 bonus points if candidate mentions working at / coming from these
# ─────────────────────────────────────────────
COMPETITOR_BROKERAGES = {
    # ── Original list ───────────────────────────────────────────────
    "element", "element real estate", "element developments",
    "bold routes", "boldroutes",
    "views", "views real estate", "views egypt",
    "everscopes", "ever scopes", "ever scope",
    "red", "red real estate", "red developments",
    "nawy", "nawy real estate",
    # ── Added April 2026 ────────────────────────────────────────────
    "nod", "nod real estate", "nod brokerage",
    "b2b", "b2b real estate", "b2b brokerage",
    "y the brokers", "y brokers", "y-the-brokers",
    "white investments", "white investment",
    "august investments", "august investment",
    "legacy investments", "legacy investment",
    "the address consultancy", "address consultancy",
    "aqarpedia",
    "aqarmap", "aqar map",
    "coldwell banker", "coldwell",
    "connect homes", "connecthomes",
    "remax", "re/max", "re-max",
    "property finder", "propertyfinder",
    "taskeen",
    "bayut",
    # ── Arabic variants ─────────────────────────────────────────────
    "إيليمنت", "بولد روتس", "فيوز", "ريد", "ناوي",
    "نود", "واي ذا بروكرز", "وايت", "أوجست", "ليجاسي",
    "العنوان", "العنوان للاستشارات", "عقارماب", "عقاربيديا",
    "كولدويل بانكر", "ريماكس", "بروبرتي فايندر", "تسكين", "بيوت",
}


# ─────────────────────────────────────────────
# Track B — Expanded crossover employers (2026-04-30)
# Not brokerage firms, but produce sales-trained, presentable candidates.
# These are NOT hard-DQ in headhunt mode. Score under Employer dimension:
#   CROSSOVER_PREMIUM  → 12 pts (same as "Other RE company")
#   CROSSOVER_STANDARD → 8 pts
# Flag with ★ CROSSOVER in the sheet.
# ─────────────────────────────────────────────

CROSSOVER_PREMIUM = {
    # Tobacco multinationals — strong consultative/field sales training
    "jti", "japan tobacco", "japan tobacco international",
    "philip morris", "pmi", "philip morris international",
    "iqos",  # PMI brand — premium product, high-touch sale
    "bat", "british american tobacco",
    "imperial brands", "seita",
    # High-end & luxury retail — strict hiring bar means staff are already
    # well-spoken, presentable, and trained in client-facing consultative sales.
    # Physical interview still required to verify TAI fit.
    "gucci", "louis vuitton", "lv", "chanel", "dior", "christian dior",
    "prada", "burberry", "valentino", "givenchy", "bottega veneta",
    "michael kors", "coach", "guess", "calvin klein", "tommy hilfiger",
    "hugo boss", "ralph lauren", "lacoste",
    "massimo dutti", "pull&bear", "bershka", "stradivarius",  # Inditex group
    "mango", "marks & spencer", "marks and spencer",
    "victoria's secret", "victoria secret",
    "mac cosmetics", "mac makeup", "mac",
    "mazaya", "mazaya mall",
    "charlotte tilbury", "inglot", "l'oreal", "loreal", "estee lauder",
    "bath & body works", "bath and body works", "the body shop",
    "cartier", "rolex", "tag heuer", "omega", "folli follie",
    "mont blanc", "montblanc",
    # Reputable mainstream retail — presentable, client-facing
    "zara", "h&m", "lc waikiki", "aldo", "pandora", "sephora", "faces",
    "rivoli", "magrabi",
    "istyle", "virgin megastore",
    # Automotive showrooms (authorised dealers)
    "bmw", "mercedes", "mercedes-benz", "kia egypt", "toyota egypt",
    # Tier-1 banks
    "cib", "commercial international bank",
    "hsbc", "qnb", "banque misr", "national bank of egypt", "nbe",
    "allianz", "metlife", "axa",  # large insurers
    # Arabic variants
    "فيليب موريس", "فيليب موريز", "جي تي آي",
    "إيكوس", "آيكوس",
    "زارا", "إتش آند إم",
    "سيفورا", "رفايولي", "ماجرابي",
    "بي إم دبليو", "مرسيدس",
    "سي آي بي", "بنك مصر", "البنك الأهلي",
}

CROSSOVER_STANDARD = {
    # FMCG with strong Egypt field sales teams
    "juhayna", "جهينة",
    "edita", "إديتا",
    "pepsico", "pepsi", "بيبسي",
    "coca-cola", "cocacola", "كوكاكولا",
    # Telecom
    "vodafone", "vodafone egypt", "فودافون",
    "orange", "orange egypt", "أورنج",
    "etisalat", "we", "اتصالات مصر",
}

# ─────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────

@dataclass
class Candidate:
    full_name: str = ""
    first_name: str = ""
    phone_number: str = ""
    email: str = ""
    profile_url: str = ""
    current_title: str = ""
    location: str = ""
    years_experience: Optional[float] = None
    education_level: str = ""           # bachelor, undergraduate, diploma, highschool, unknown
    education_details: str = ""
    skills: str = ""                    # comma-separated or free text
    summary: str = ""                   # ≤500 chars
    source_platform: str = ""           # linkedin, facebook, tiktok, manual
    graduation_year: Optional[int] = None
    is_student: bool = False
    age: Optional[int] = None           # explicit age if known
    raw_text: str = ""                  # any extra text for keyword scanning
    current_employer: str = ""          # used in headhunt mode (Track B)
    years_at_current: Optional[float] = None  # tenure at current employer
    move_signal: str = ""               # "open_to_work", "engaged_post", "passive"
    last_active: str = ""               # ISO date OR relative ("3 days ago") — recency scoring
    has_profile_photo: bool = True      # False = no photo → fake/inactive profile signal
    connection_count: Optional[int] = None  # LinkedIn connections / Wuzzuf followers
    open_to_work: bool = False          # LinkedIn "Open to Work" badge (Track A intent signal)


@dataclass
class ScoreResult:
    overall_score: int = 0
    location_score: int = 0
    sales_fit_score: int = 0
    entry_level_score: int = 0
    education_score: int = 0
    contact_score: int = 0
    competitor_bonus: int = 0           # +10 if from a known Cairo brokerage
    platform_adjustment: int = 0        # −5 for TikTok (quantity over quality)
    red_flags: list = field(default_factory=list)
    key_signals: list = field(default_factory=list)
    recommendation: str = ""
    priority: str = ""
    disqualified: bool = False          # True = hard disqualify, score → 0
    disqualify_reason: str = ""
    track: str = "entry"                # "entry" (Track A) or "headhunt" (Track B)
    tenured_flag: bool = False          # ★ TENURED marker for Track B


# ─────────────────────────────────────────────
# Age inference
# ─────────────────────────────────────────────

def _infer_age(c: Candidate) -> Optional[int]:
    """
    Estimate candidate age from available signals.

    Egyptian university system:
      - Entry age for all programmes = 18
      - Standard bachelor   (4 yrs)  → graduates at 22
      - Engineering         (5 yrs)  → graduates at 23
      - Medicine/dentistry/pharmacy
                            (6 yrs)  → graduates at 24
      - Master's / MBA / MSc
                            (2 yrs after bachelor's) → graduates at ~24
      - PhD / Doctorate     (4-5 yrs after bachelor's) → graduates at ~27

    CRITICAL: Advanced degrees (Master's, MBA, PhD) MUST be detected
    BEFORE bachelor's specialisation checks. A "Master's in Engineering"
    is a 2-yr post-graduate programme — its graduation year must use
    grad_age 24, NOT the 5-yr engineering grad_age of 23. Using the
    wrong grad_age here makes candidates appear younger than they are
    and can silently bypass the age-32 hard disqualifier.

    age ≈ (CURRENT_YEAR − graduation_year) + graduation_age
    """
    if c.age is not None:
        return c.age

    # Age from graduation_year — TWO valid cases only:
    #
    #   1. Bachelor's graduate:
    #      graduation_year = the year they finished their bachelor's degree.
    #      age = CURRENT_YEAR − graduation_year + grad_age
    #      grad_age depends on programme length (all enter at 18):
    #        • Standard bachelor (4 yrs) → graduate at 22
    #        • Engineering       (5 yrs) → graduate at 23
    #        • Medicine/dentistry/pharmacy (6 yrs) → graduate at 24
    #
    #   2. Current undergrad (is_student = True):
    #      graduation_year = the year they ENTERED university (not expected graduation).
    #      age = CURRENT_YEAR − entry_year + 18
    #
    # Any other use of graduation_year is invalid:
    #   • Master's / MBA / PhD and all post-graduate degrees are IGNORED —
    #     they are unrelated to bachelor's duration and the gap between a
    #     bachelor's and a Master's varies too widely to assume anything.
    if c.graduation_year:
        edu_text = _normalise(" ".join([
            c.education_level, c.education_details, c.summary, c.raw_text
        ]))

        # ── Detect post-graduate / advanced academic studies — skip entirely ───
        is_postgrad = any(kw in edu_text for kw in [
            "master", "masters", "mba", "m.b.a", "msc", "m.sc", "m.a.",
            "postgraduate", "post-graduate", "post graduate",
            "pgdip", "pg dip", "advanced diploma",
            "phd", "ph.d", "ph. d", "doctorate", "doctor of philosophy",
            "dba", "llm", "med ", "m.ed",
            "ماجستير", "دكتوراه", "دراسات عليا", "دبلوم عالي",
        ])

        if not is_postgrad:
            if c.is_student:
                # graduation_year stores entry year — everyone enters at 18
                return CURRENT_YEAR - c.graduation_year + 18

            else:
                # graduation_year is actual bachelor's graduation year
                if any(kw in edu_text for kw in [
                    "medicine", "medical", "physician", "md ",
                    "dentistry", "dentist",
                    "pharmacy", "pharmacist", "pharm",
                    "طب", "أسنان", "صيدلة", "بشري", "طبيب",
                ]):
                    grad_age = 24   # 6-yr programme  (entry 18 → graduate 24)
                elif any(kw in edu_text for kw in [
                    "engineering", "engineer", "b.eng", "beng",
                    "هندسة", "مهندس",
                ]):
                    grad_age = 23   # 5-yr programme  (entry 18 → graduate 23)
                else:
                    grad_age = 22   # Standard 4-yr   (entry 18 → graduate 22)

                return CURRENT_YEAR - c.graduation_year + grad_age
        # is_postgrad → fall through to years_experience fallback below

    # From years of experience: assumes work started at ~22
    if c.years_experience is not None and c.years_experience >= 0:
        return int(22 + c.years_experience)

    # Scan raw text for explicit age mentions ("age: 25", "عمري 24", "24 years old")
    text = _normalise(
        " ".join([c.summary, c.raw_text, c.education_details])
    )
    m = re.search(r'\bage[:\s]+(\d{2})\b|\b(\d{2})\s+years?\s+old\b|عمري[:\s]+(\d{2})', text)
    if m:
        age_val = int(next(g for g in m.groups() if g))
        if 15 <= age_val <= 60:
            return age_val

    return None


# ─────────────────────────────────────────────
# Hard disqualifiers (checked before scoring)
# ─────────────────────────────────────────────

def _check_hard_disqualifiers(c: Candidate) -> tuple[bool, str]:
    """
    Returns (disqualified: bool, reason: str).
    Any True result forces overall_score = 0 and priority = P4.
    """
    title_norm = _normalise(c.current_title)
    loc_norm = _normalise(c.location)
    text = _normalise(" ".join([c.summary, c.raw_text, c.skills]))

    # ── 0a. Current TAI employee → never sourced, profile only ──────────────
    # Karim's ruling 2026-08-04. Highest precedence: we do not recruit our own staff.
    own = _normalise(f"{c.current_employer or ''} {c.current_title or ''}")
    for sig in OWN_COMPANY_SIGNALS:
        if sig in own:
            return True, ("Current TAI employee — not a sourcing target, profile only: "
                          f"'{c.current_employer or c.current_title}'")

    # ── 0. Non-Egypt country (hard rule: Egyptians in Cairo ONLY) ────────────
    # Matched on WORD BOUNDARIES, never as a bare substring.
    # Fixed 2026-08-03 (Karim's ruling): the old `country in loc_norm` check let
    # "uk" match inside "El Shorouk" / "Shorouk City", hard-DQ'ing every East
    # Cairo Shorouk candidate at score 0. El Shorouk is Egypt, East Cairo, and a
    # secondary preferred location — it is already Tier 1 (30 pts) in
    # NEAR_NEW_CAIRO, so removing the false DQ is the whole fix.
    for country in NON_EGYPT_COUNTRIES:
        if re.search(rf"(?<!\w){re.escape(country)}(?!\w)", loc_norm):
            return True, f"Non-Egypt location: {c.location}"

    # ── 1. Outside Cairo ──────────────────────────────────────────────────
    for place in OUTSIDE_CAIRO:
        if place in loc_norm:
            return True, f"Outside Cairo: {c.location}"

    # If location is non-empty and doesn't match any known Cairo area → disqualify
    if loc_norm:
        is_near_nc = any(p in loc_norm for p in NEAR_NEW_CAIRO)
        is_cairo = any(p in loc_norm for p in OTHER_CAIRO)
        is_outside = any(p in loc_norm for p in OUTSIDE_CAIRO)
        # Only disqualify if it clearly reads as outside — don't penalise blanks
        if is_outside and not is_near_nc and not is_cairo:
            return True, f"Outside Cairo: {c.location}"

    # ── 2. Managerial DQ (rule 2026-04-14): seniors OK, managers/directors DQ ──
    # Only trigger if the keyword is NOT preceded by nothing but a safe senior prefix.
    title_stripped = title_norm
    for sfx in SENIORITY_SAFE_PREFIXES:
        title_stripped = title_stripped.replace(sfx, "").strip()
    # Matched on WORD BOUNDARIES (optional plural), never as a bare substring.
    # Fixed 2026-08-03 (Karim's ruling): the old `kw in title_stripped` check let
    # "coo" match inside "Sales Coo|rdinator" and hard-DQ every Coordinator.
    # Sales Coordinator is NOT disqualified going forward. Real "COO" / "COOs"
    # still DQ, and plurals ("Sales Managers", "Directors") still DQ.
    for kw in SENIORITY_DISQUALIFIERS:
        if re.search(rf"(?<!\w){re.escape(kw)}s?(?!\w)", title_stripped):
            return True, f"Managerial/director title: '{c.current_title}'"

    # ── 2b. Supervisors and Team Leaders → Track B, profile only ─────────
    # Karim's ruling 2026-08-04. Not a rejection: these people are headhunt
    # targets and belong in the Profiling record, not the entry-level call list.
    for kw in TRACK_B_ROUTE_TITLES:
        if re.search(rf"(?<!\w){re.escape(kw)}s?(?!\w)", title_norm):
            return True, (f"Team Leader / Supervisor — Track B target, profile only: "
                          f"'{c.current_title}'")

    # ── 3. Age over 32 ────────────────────────────────────────────────────
    age = _infer_age(c)
    if age is not None and age > 32:
        return True, f"Over 32 (inferred age: {age})"

    # ── 4. Age under 21 (junior undergrad) ───────────────────────────────
    if age is not None and age < 21:
        return True, f"Under 21 (inferred age: {age}) — too young"

    # ── 5. Explicit 12+ years mentioned (likely over 32) ────────────────
    if re.search(r'(12|13|14|15|16|17|18|19|20)\s*\+?\s*(years|yrs|سنوات|سنة)', text):
        return True, "Mentions 12+ years experience (likely over 32)"

    if c.years_experience is not None and c.years_experience >= 11:
        return True, f"11+ years experience (likely over 32) — {c.years_experience:.0f} yrs"

    return False, ""


# ─────────────────────────────────────────────
# Scoring sub-functions
# ─────────────────────────────────────────────

def _normalise(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'[\u064B-\u065F\u0670]', '', text)  # strip Arabic diacritics
    return text


def _combined_text(c: Candidate) -> str:
    parts = [c.current_title, c.skills, c.summary, c.education_details, c.raw_text]
    return _normalise(" ".join(p for p in parts if p))


def _score_location(c: Candidate) -> tuple[int, list, list]:
    loc = _normalise(c.location)
    flags, signals = [], []

    for place in NEAR_NEW_CAIRO:
        if place in loc:
            signals.append(f"Near New Cairo: {c.location}")
            return 30, flags, signals

    for place in OTHER_CAIRO:
        if place in loc:
            signals.append(f"Cairo area (farther): {c.location}")
            return 15, flags, signals  # Other Cairo districts

    if loc:
        # Unknown location — small points, not disqualified (caught by hard check if outside)
        return 5, flags, signals

    return 0, flags, signals


def _score_sales_fit(c: Candidate) -> tuple[int, list, list]:
    text = _combined_text(c)
    flags, signals = [], []
    hits = 0

    for kw in SALES_KEYWORDS_EN:
        if kw in text:
            hits += 1
            signals.append(f"Sales keyword: '{kw}'")

    for kw in SALES_KEYWORDS_AR:
        if kw in text:
            hits += 1
            signals.append(f"Sales keyword (AR): '{kw}'")

    if hits >= 4:
        return 25, flags, signals
    elif hits >= 2:
        return 15, flags, signals
    elif hits == 1:
        return 8, flags, signals
    return 0, flags, signals


def _score_entry_level(c: Candidate) -> tuple[int, list, list]:
    """
    Age 21–30 is already confirmed by the hard-disqualifier gate.
    Score based on experience within that window.
    """
    flags, signals = [], []

    if c.graduation_year and c.graduation_year >= (CURRENT_YEAR - 3):
        signals.append(f"★ Recent grad ({c.graduation_year}) — priority")
        # Karim's ruling 2026-08-04: Entry Level is capped at 20 (CLAUDE.md scale).
        # The recent-grad boost survives as a ★ signal, not as extra points.
        return 20, flags, signals
    if c.graduation_year and c.graduation_year >= 2022:
        signals.append(f"Fresh graduate ({c.graduation_year})")
        return 20, flags, signals

    if c.is_student:
        signals.append("Final-year student (21+)")
        return 15, flags, signals

    yoe = c.years_experience
    if yoe is None:
        return 0, flags, signals

    if yoe <= 0:
        signals.append("No experience / fresh graduate")
        return 20, flags, signals
    elif yoe <= 3:
        signals.append(f"{yoe:.0f} year(s) — entry level")
        return 20, flags, signals
    elif yoe <= 6:
        signals.append(f"{yoe:.0f} years — mid entry level")
        return 12, flags, signals
    elif yoe <= 8:
        signals.append(f"{yoe:.0f} years — approaching limit")
        return 5, flags, signals

    # 9+ years → caught by hard disqualifier; if somehow here, penalise
    flags.append(f"Too much experience for entry level: {yoe:.0f} yrs")
    return 0, flags, signals


def _score_education(c: Candidate) -> tuple[int, list, list]:
    level = _normalise(c.education_level)
    flags, signals = [], []

    if any(kw in level for kw in ["bachelor", "بكالوريوس", "licence", "ليسانس", "b.sc", "bsc", "ba ", "b.a"]):
        signals.append("Bachelor's degree")
        return 15, flags, signals
    if any(kw in level for kw in ["undergraduate", "student", "طالب"]):
        signals.append("Undergraduate (21+)")
        return 12, flags, signals
    if any(kw in level for kw in ["diploma", "دبلوم"]):
        signals.append("Diploma")
        return 8, flags, signals
    if any(kw in level for kw in ["high school", "ثانوية", "secondary"]):
        signals.append("High school")
        return 5, flags, signals

    return 0, flags, signals


def _private_uni_bonus(c: Candidate) -> tuple[int, list, list]:
    """
    +5 bonus for graduates of private / international universities.
    These candidates tend to have stronger English and presentation skills —
    useful signals for real estate sales in premium project environments.
    Does not stack with or replace the education level score.
    """
    # REMOVED 2026-08-04 (Karim's ruling). The +5 private-university bonus rewarded
    # the ability to pay for university rather than anything about selling ability,
    # and there is no evidence it predicts performance in a commission-based sales
    # role. Kept as a recorded SIGNAL only — it awards no points.
    text = _normalise(" ".join([c.education_level, c.summary, c.raw_text]))
    flags, signals = [], []
    for uni in PRIVATE_UNIVERSITIES:
        if uni in text:
            signals.append(f"Private/international university: {uni} (no score effect)")
            return 0, flags, signals
    return 0, flags, signals


def _score_contact(c: Candidate) -> tuple[int, list, list]:
    flags, signals = [], []
    phone_pattern = re.compile(r'(01[0-9]{9}|\+2001[0-9]{9})')
    phone = c.phone_number.strip() if c.phone_number else ""

    if phone and phone_pattern.search(phone):
        signals.append("Egyptian phone number available")
        return 10, flags, signals
    elif phone or c.email:
        signals.append("Email or partial contact available")
        return 5, flags, signals
    elif c.profile_url:
        signals.append("Profile URL only")
        return 3, flags, signals

    flags.append("No contact info found")
    return 0, flags, signals


def _score_competitor_bonus(c: Candidate) -> tuple[int, list, list]:
    """
    +10 bonus if candidate has background from a known Cairo real estate brokerage.
    These candidates have higher conversion rates from The Address's experience.
    Preferred but NOT required — does not disqualify absence.
    Brokerages: Element, Bold Routes, Views, Ever Scopes, Red, Nawy.
    """
    text = _combined_text(c)
    flags, signals = [], []

    # Check for TAI/The Address Investments first — rehire, not a new-hire bonus
    tai_variants = ["the address investments", "address investments", "tai real estate",
                    "the address investment", "address investment"]
    for tai in tai_variants:
        if tai in text:
            flags.append("Rehire candidate — The Address Investments background noted (no bonus)")
            return 0, flags, signals

    matched = []
    for brokerage in COMPETITOR_BROKERAGES:
        if brokerage in text:
            matched.append(brokerage)

    if matched:
        # Deduplicate (e.g. "nawy" and "nawy real estate" both hit)
        canonical = sorted({m.split()[0] for m in matched})
        signals.append(f"Competitor brokerage background: {', '.join(canonical)}")
        return 10, flags, signals

    return 0, flags, signals


def _platform_adjustment(c: Candidate) -> tuple[int, list]:
    """
    TikTok sources tend to be high-volume, lower-quality candidates.
    Apply a small reliability penalty to surface them lower than equivalent
    LinkedIn/Facebook candidates, without fully disqualifying them.
    """
    if c.source_platform == "tiktok":
        return -5, ["TikTok source: quantity-over-quality platform (−5)"]
    return 0, []


def _score_recency(c: Candidate) -> tuple[int, list, list]:
    """
    Recency bonus/penalty based on last_active.
    Recently active candidates are far more likely to respond to outreach.

    Accepts:
      - ISO date string: "2026-04-28"
      - Relative English: "3 days ago", "1 week ago", "2 months ago"
      - Relative Arabic:  "منذ 3 أيام", "منذ أسبوع"

    Returns bonus (positive) or penalty (negative):
      ≤ 7 days  → +10 (high response likelihood)
      ≤ 30 days → +5
      ≤ 90 days →  0  (neutral)
      > 90 days → -5  (stale profile)
    """
    from datetime import date as _date
    flags, signals = [], []

    raw = (c.last_active or "").strip().lower()
    if not raw:
        return 0, flags, signals

    days_ago = None

    # Relative English: "3 days ago", "2 weeks ago", "1 month ago"
    m = re.search(r'(\d+)\s*(day|week|month|year)', raw)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit.startswith('day'):   days_ago = n
        elif unit.startswith('week'): days_ago = n * 7
        elif unit.startswith('month'): days_ago = n * 30
        elif unit.startswith('year'):  days_ago = n * 365

    # Relative Arabic: "منذ 3 أيام", "منذ أسبوع"
    if days_ago is None:
        ma = re.search(r'منذ\s*(\d+)?\s*(يوم|أيام|أسبوع|أسابيع|شهر|أشهر)', raw)
        if ma:
            n_raw, unit_ar = ma.group(1), ma.group(2)
            n = int(n_raw) if n_raw else 1
            if 'يوم' in unit_ar or 'أيام' in unit_ar: days_ago = n
            elif 'أسبوع' in unit_ar or 'أسابيع' in unit_ar: days_ago = n * 7
            elif 'شهر' in unit_ar or 'أشهر' in unit_ar: days_ago = n * 30

    # ISO date string: "2026-04-28" or "2026-04-28 14:30"
    if days_ago is None:
        try:
            active_date = _date.fromisoformat(raw[:10])
            days_ago = (_date.today() - active_date).days
        except (ValueError, TypeError):
            pass

    if days_ago is None:
        return 0, flags, signals

    if days_ago <= 7:
        signals.append(f"★ Active recently ({days_ago}d ago) — high response likelihood")
        return 10, flags, signals
    elif days_ago <= 30:
        signals.append(f"Active within a month ({days_ago}d ago)")
        return 5, flags, signals
    elif days_ago > 90:
        flags.append(f"Profile stale — last active {days_ago}d ago (lower response rate)")
        return -5, flags, signals

    return 0, flags, signals


def _score_profile_quality(c: Candidate) -> tuple[int, list, list]:
    """
    Penalise unverifiable or likely-fake profiles.
    Introduced after the Mariam/Omar wrong-number incident (May 2026).

    Rules:
      No photo + connections < 10 → −20 + ⚠ Unverifiable Profile flag
      No photo only               → −10 + flag
      Connections < 10 only       → −5  + flag
    """
    flags, signals = [], []
    penalty = 0

    no_photo = not c.has_profile_photo
    low_conn = (c.connection_count is not None and c.connection_count < 10)

    if no_photo and low_conn:
        penalty = -20
        flags.append(
            f"⚠ Unverifiable profile — no photo + {c.connection_count} connections "
            f"(possible fake/inactive — verify before outreach)"
        )
    elif no_photo:
        penalty = -10
        flags.append("No profile photo — possible anonymous or inactive account")
    elif low_conn:
        penalty = -5
        flags.append(f"Very low connections ({c.connection_count}) — possible new or inactive profile")

    return penalty, flags, signals


def _score_intent_signals(c: Candidate) -> tuple[int, list, list]:
    """
    Bonus for candidates actively signalling job-seeking intent (Track A).
    - LinkedIn Open to Work badge: +10
    These candidates are already looking — they convert at higher rates.
    """
    flags, signals = [], []

    if c.open_to_work:
        signals.append("★ Open to Work — active job seeker (LinkedIn badge)")
        return 10, flags, signals

    return 0, flags, signals


def _apply_soft_penalties(c: Candidate) -> tuple[int, list]:
    """Soft penalties (remote-only) applied on top of hard-disqualifier gate."""
    text = _combined_text(c)
    penalties = 0
    flags = []

    for kw in REMOTE_ONLY_SIGNALS:
        if kw in text:
            penalties -= 10
            flags.append("Remote-only preference mentioned")
            break

    return penalties, flags


def _recommend(score: int) -> tuple[str, str]:
    if score >= 75:
        return "Strong Match - Call Today", "P1"
    elif score >= 55:
        return "Good Match - Call This Week", "P2"
    elif score >= 35:
        return "Possible Match - Follow Up Later", "P3"
    else:
        return "Poor Match - Archive", "P4"


# ─────────────────────────────────────────────
# Track B — Headhunt scoring (tenured movers)
# ─────────────────────────────────────────────

def _title_fit_headhunt(c: Candidate) -> tuple[int, str]:
    """Return (score, matched_title) for headhunt mode. 0 means no tenured title."""
    title = _normalise(c.current_title)
    best = 0
    matched = ""
    for kw, pts in TENURED_TITLES.items():
        if kw in title and pts > best:
            best = pts
            matched = kw
    return best, matched


def _competitor_match(c: Candidate) -> tuple[int, str, bool]:
    """
    Track B employer scoring (updated 2026-04-30 — expanded crossover industries).
    Returns (points, matched_name, is_crossover).

    - Known competitor brokerage     → 25 pts
    - Any other RE company           → 12 pts
    - Premium crossover employer     → 12 pts  + ★ CROSSOVER flag
      (reputable retail, tobacco multinationals, tier-1 banks/insurers)
    - Standard crossover employer    → 8 pts   + ★ CROSSOVER flag
      (FMCG, telecom, standard insurance)
    - Not in any recognised category → 0 pts (DQ only if strict RE required)
    """
    text = _normalise(" ".join([c.current_employer, c.current_title, c.summary, c.raw_text]))

    for brokerage in COMPETITOR_BROKERAGES:
        if brokerage in text:
            return 25, brokerage, False

    re_keywords = ["real estate", "brokerage", "developments", "properties", "property",
                   "عقارات", "عقاري", "تطوير عقاري", "وسيط عقاري"]
    if any(kw in text for kw in re_keywords):
        return 12, "real estate (non-competitor)", False

    for employer in CROSSOVER_PREMIUM:
        if employer in text:
            return 12, f"★ CROSSOVER (premium): {employer}", True

    for employer in CROSSOVER_STANDARD:
        if employer in text:
            return 8, f"★ CROSSOVER (standard): {employer}", True

    return 0, "", False


def _tenure_score(c: Candidate) -> tuple[int, list, list]:
    """Track B tenure scoring. 3+ yrs = tenured ★. 0–20 pts."""
    flags, signals = [], []
    yrs = c.years_at_current
    if yrs is None:
        return 5, flags, ["Tenure unknown"]
    if yrs >= 4:
        signals.append(f"★ TENURED — {yrs:.0f} yrs at current employer")
        return 20, flags, signals
    if yrs >= 3:
        signals.append(f"★ TENURED — {yrs:.0f} yrs at current employer")
        return 16, flags, signals
    if yrs >= 2:
        signals.append(f"{yrs:.0f} yrs at current employer")
        return 10, flags, signals
    flags.append(f"Low tenure ({yrs:.0f} yrs) — possible job-hopper")
    return 5, flags, signals


def _move_signal_score(c: Candidate) -> tuple[int, list]:
    sig = _normalise(c.move_signal)
    if sig == "open_to_work":
        return 15, ["Open to work signal"]
    if sig == "engaged_post":
        return 10, ["Engaged with competitor job posts"]
    return 5, ["Passive — no explicit move signal"]


def _score_headhunt(c: Candidate) -> ScoreResult:
    result = ScoreResult()
    result.track = "headhunt"
    flags, signals = [], []

    # Hard disqualifiers for Track B
    title_norm = _normalise(c.current_title)
    loc_norm = _normalise(c.location)

    # Too senior
    for kw in HEADHUNT_HARD_DQ_TITLES:
        if kw in title_norm:
            result.disqualified = True
            result.disqualify_reason = f"Too senior for agent recruit: '{c.current_title}'"
            result.priority = "T4"
            result.recommendation = "Skip - too senior"
            return result

    # Outside Cairo
    for place in OUTSIDE_CAIRO:
        if place in loc_norm:
            result.disqualified = True
            result.disqualify_reason = f"Outside Cairo: {c.location}"
            result.priority = "T4"
            return result

    # Age 25–38
    age = _infer_age(c)
    if age is not None and age > 38:
        result.disqualified = True
        result.disqualify_reason = f"Over 38 (inferred age: {age})"
        result.priority = "T4"
        return result
    if age is not None and age < 25:
        result.disqualified = True
        result.disqualify_reason = f"Under 25 — route to entry-level Track A instead"
        result.priority = "T4"
        return result

    # Title fit (must be a tenured title)
    title_pts, title_matched = _title_fit_headhunt(c)
    if title_pts == 0:
        result.disqualified = True
        result.disqualify_reason = f"No tenured title match — '{c.current_title}'"
        result.priority = "T4"
        return result
    signals.append(f"Title: {title_matched} (+{title_pts})")

    # Competitor / employer match
    # Crossover employers (retail, tobacco, banking) are NOT disqualified — they score
    # 8–12 pts and receive a ★ CROSSOVER flag. Only truly unknown employers are DQ.
    comp_pts, comp_matched, is_crossover = _competitor_match(c)
    if comp_pts == 0:
        result.disqualified = True
        result.disqualify_reason = "Employer not recognised (not RE, brokerage, or crossover industry)"
        result.priority = "T4"
        return result
    if is_crossover:
        signals.append(f"★ CROSSOVER employer: {comp_matched} (+{comp_pts})")
        result.key_signals.append("★ CROSSOVER — non-RE background, strong sales profile")
    else:
        signals.append(f"Employer: {comp_matched} (+{comp_pts})")

    # Tenure
    tenure_pts, tf, ts = _tenure_score(c)
    flags.extend(tf); signals.extend(ts)
    if c.years_at_current is not None and c.years_at_current >= 3:
        result.tenured_flag = True

    # Location (Track B uses 0–15 scale)
    loc_pts = 0
    if any(p in loc_norm for p in NEAR_NEW_CAIRO):
        loc_pts = 15
        signals.append(f"Near New Cairo: {c.location}")
    elif any(p in loc_norm for p in OTHER_CAIRO):
        loc_pts = 10
        signals.append(f"Cairo area: {c.location}")
    elif loc_norm:
        loc_pts = 3

    # Move signal
    move_pts, ms = _move_signal_score(c)
    signals.extend(ms)

    raw = title_pts + comp_pts + tenure_pts + loc_pts + move_pts
    result.overall_score = max(0, min(100, raw))
    result.location_score = loc_pts
    result.sales_fit_score = title_pts  # reused field for title fit
    result.entry_level_score = tenure_pts
    result.education_score = 0
    result.contact_score = 0
    result.competitor_bonus = comp_pts
    result.platform_adjustment = move_pts
    result.red_flags = flags
    result.key_signals = signals

    # Track B tiers per CLAUDE.md (Karim's ruling 2026-08-04): T1 >= 80, T2 60-79, T3 40-59
    if result.overall_score >= 80:
        result.recommendation, result.priority = "Priority Outreach Today", "T1"
    elif result.overall_score >= 60:
        result.recommendation, result.priority = "Warm — Contact This Week", "T2"
    elif result.overall_score >= 40:
        result.recommendation, result.priority = "Watchlist", "T3"
    else:
        result.recommendation, result.priority = "Skip", "T4"

    if age:
        signals.insert(0, f"Inferred age: {age}")

    return result


# ─────────────────────────────────────────────
# Main scoring function
# ─────────────────────────────────────────────

def score_candidate(c: Candidate, mode: str = "entry") -> ScoreResult:
    """
    mode = "entry"    → Track A: inbound / fresh-grad / Wuzzuf / FB groups
    mode = "headhunt" → Track B: tenured movers via LinkedIn feed + Apollo
    """
    if mode == "headhunt":
        return _score_headhunt(c)
    result = ScoreResult()
    result.track = "entry"
    all_flags: list[str] = []
    all_signals: list[str] = []

    # ── Hard disqualifiers (age, location, seniority) ────────────────────
    disqualified, reason = _check_hard_disqualifiers(c)
    if disqualified:
        result.overall_score = 0
        result.disqualified = True
        result.disqualify_reason = reason
        result.red_flags = [f"DISQUALIFIED: {reason}"]
        result.key_signals = []
        result.recommendation = "Poor Match - Archive"
        result.priority = "P4"
        return result

    # ── Positive scoring ─────────────────────────────────────────────────
    loc_score, f, s = _score_location(c)
    result.location_score = max(0, loc_score)
    all_flags.extend(f); all_signals.extend(s)

    sales_score, f, s = _score_sales_fit(c)
    result.sales_fit_score = max(0, sales_score)
    all_flags.extend(f); all_signals.extend(s)

    entry_score, f, s = _score_entry_level(c)
    result.entry_level_score = max(0, entry_score)
    all_flags.extend(f); all_signals.extend(s)

    edu_score, f, s = _score_education(c)
    result.education_score = max(0, edu_score)
    all_flags.extend(f); all_signals.extend(s)

    contact_score, f, s = _score_contact(c)
    result.contact_score = max(0, contact_score)
    all_flags.extend(f); all_signals.extend(s)

    # ── Private university bonus ──────────────────────────────────────────
    priv_uni_bonus, f, s = _private_uni_bonus(c)
    all_flags.extend(f); all_signals.extend(s)

    # ── Competitor brokerage bonus ────────────────────────────────────────
    comp_bonus, f, s = _score_competitor_bonus(c)
    result.competitor_bonus = comp_bonus
    all_flags.extend(f); all_signals.extend(s)

    # ── Platform quality adjustment ───────────────────────────────────────
    plat_adj, f = _platform_adjustment(c)
    result.platform_adjustment = plat_adj
    all_flags.extend(f)

    # ── Soft penalties ────────────────────────────────────────────────────
    penalty, f = _apply_soft_penalties(c)
    all_flags.extend(f)

    # ── Recency bonus/penalty (last_active) ──────────────────────────────
    recency_adj, f, s = _score_recency(c)
    all_flags.extend(f); all_signals.extend(s)

    # ── Profile quality penalty (fake/inactive profile detection) ─────────
    quality_adj, f, s = _score_profile_quality(c)
    all_flags.extend(f); all_signals.extend(s)

    # ── Intent signals (Open to Work etc.) ───────────────────────────────
    intent_bonus, f, s = _score_intent_signals(c)
    all_flags.extend(f); all_signals.extend(s)

    raw = (
        result.location_score + result.sales_fit_score + result.entry_level_score
        + result.education_score + result.contact_score
        + result.competitor_bonus + priv_uni_bonus + result.platform_adjustment + penalty
        + recency_adj + quality_adj + intent_bonus
    )
    result.overall_score = max(0, min(100, raw))
    result.red_flags = all_flags
    result.key_signals = all_signals
    result.recommendation, result.priority = _recommend(result.overall_score)

    # Annotate inferred age in signals
    age = _infer_age(c)
    if age:
        all_signals.insert(0, f"Inferred age: {age}")

    return result
