"""A stand-in for the CV service's matcher, for dev machines and tests (never outside dev).

It does not read a CV: it looks for each skill's name in the file's own bytes. That is enough to
exercise everything around it — what is written, what is refused, what a person is left with —
without sending anybody's CV anywhere, and it is the same switch the fake CV reader uses,
TALENT_OCR_MODE.
"""

import json

from assess.match import DEFAULT_CATEGORY, Line, MatchReport, MatchUnreadable


class FakeMatcher:
    name = "fake-matcher"

    def match(self, content: bytes, media_type: str, requirements: str) -> MatchReport:
        try:
            asked = json.loads(requirements)
        except ValueError:
            raise MatchUnreadable("The requirements are not JSON.") from None
        inside = content.decode("utf-8", "ignore").lower()
        lines = []
        for group in asked.get("skill_types", []):
            for skill in group.get("skills", []):
                name = str(skill.get("name", ""))
                shown = name.lower() in inside
                lines.append(
                    Line(
                        skill=name,
                        category=str(group.get("name") or DEFAULT_CATEGORY),
                        wanted=str(skill.get("required_level", "")),
                        found=str(skill.get("required_level", "")) if shown else None,
                        evidence=name if shown else None,
                        status="matched" if shown else "missing",
                    )
                )
        if not lines:
            raise MatchUnreadable("The requirements name no skill.")
        met = sum(1 for line in lines if line.status == "matched")
        return MatchReport(
            percentage=round(100.0 * met / len(lines), 2),
            lines=tuple(lines),
            job_title=str(asked.get("job_title") or ""),
            method="stand-in",
            requirements_hash="",
        )

    def close(self) -> None:
        return None
