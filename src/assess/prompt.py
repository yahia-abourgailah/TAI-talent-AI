"""What the model is shown, and what it is told not to do (BR-305, CR-07).

It is shown the job's own description and the candidate's CV as the reader returned it. It is told
to answer only from those two things, to quote the CV for every reason it gives, and to say what
the job asks for that it cannot find.

It is told what not to weigh: age, sex, marital status, where someone is from, their photograph,
their name, their religion. Those are not hiring criteria here (CR-07), and a model asked for a
number will happily read them if nobody says otherwise. Whether it obeys is not left to trust —
every reason must quote the CV, and a person reads the assessment before anything happens (CR-05).
"""

from collections.abc import Mapping

MAX_DESCRIPTION = 4000
MAX_CV = 12000

SYSTEM = """You read one CV against one job description for a recruitment team in Egypt.

Answer with JSON only, in exactly this shape:
{"score": <0-100>,
 "summary": "<one sentence>",
 "reasons": [{"says": "<what the CV shows>", "quote": "<the CV's own words, copied exactly>"}],
 "missing": ["<something the job asks for that the CV does not show>"]}

Rules:
- Use only the job description and the CV below. Never assume anything else about the person.
- Every reason must quote the CV exactly, word for word, copied from the text you were given.
  If you cannot quote it, do not say it.
- The score is how well this CV meets what THIS job asks for. 100 means every requirement is
  evidenced in the CV; 0 means none is.
- Never weigh, mention or infer age, sex, marital status, nationality, religion, photograph or
  the person's name. They are not part of this job's requirements.
- If the CV is empty or unreadable, answer with a score of 0 and say so in the summary."""


def _cut(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n[…]"


def question(job_title: str, description: str, cv_text: str) -> str:
    return (
        f"JOB: {job_title.strip()}\n\n"
        f"WHAT THE JOB ASKS FOR:\n{_cut(description, MAX_DESCRIPTION)}\n\n"
        f"THE CANDIDATE'S CV:\n{_cut(cv_text, MAX_CV)}"
    )


# The fields a CV is shown as, in the order a person would read them. The name is not sent: the
# model is told not to weigh it, and the surest way is not to give it (CR-07).
SHOWN: tuple[tuple[str, str], ...] = (
    ("current_title", "Current title"),
    ("current_employer", "Current employer"),
    ("years_experience", "Years of experience"),
    ("education", "Education"),
    ("location", "Location"),
)


def cv_text(fields: Mapping[str, str | None], answer_text: str = "") -> str:
    """The CV as the model sees it: what the reader extracted, and the CV's own text when we have
    it. Quotes are checked against this, so it is exactly what was sent, never a tidied copy."""
    lines = [
        f"{label}: {str(fields.get(name) or '').strip()}"
        for name, label in SHOWN
        if str(fields.get(name) or "").strip()
    ]
    written = (answer_text or "").strip()
    if written:
        lines += ["", "THE CV AS WRITTEN:", written]
    return "\n".join(lines)
