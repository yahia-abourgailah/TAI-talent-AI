"""The criteria versions the platform scores with. A rule change is a new module, registered here;
a registered module is never edited."""

from types import ModuleType

from scoring.rulesets import v2026_08_04

RULESETS: dict[str, ModuleType] = {"2026-08-04": v2026_08_04}
