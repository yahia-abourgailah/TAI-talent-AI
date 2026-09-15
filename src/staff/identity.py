"""Keys for matching a candidate to an employee. Pure functions: nothing is stored or guessed.

phone  an Egyptian mobile in one form, 01XXXXXXXXX, whatever spacing or country code it was typed
       with; anything else is no key
email  trimmed and case-folded; no key unless it has text on both sides of one @
name   case-folded, Arabic marks removed and common letter variants unified; no key unless the
       name has at least two words, so a first name alone never matches anyone
"""

import re
import unicodedata

_EGYPT_MOBILE = re.compile(r"01[0125]\d{8}")
_ARABIC_MARKS = re.compile("[\u064b-\u065f\u0670\u0640]")  # harakat, superscript alef, tatweel
# alef with hamza or madda to alef, taa marbuta to haa, alef maqsura to yaa
_ARABIC_VARIANTS = str.maketrans(
    {
        "\u0623": "\u0627",
        "\u0625": "\u0627",
        "\u0622": "\u0627",
        "\u0629": "\u0647",
        "\u0649": "\u064a",
    }
)


def phone_key(raw: object) -> str | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)  # a sheet that stored the number as a number drops its leading zero
    digits = "".join(
        str(unicodedata.decimal(ch)) for ch in str(raw) if unicodedata.decimal(ch, None) is not None
    )
    if digits.startswith("0020"):
        digits = "0" + digits[4:]
    elif digits.startswith("20") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif digits.startswith("1") and len(digits) == 10:
        digits = "0" + digits
    return digits if _EGYPT_MOBILE.fullmatch(digits) else None


def email_key(raw: object) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().casefold()
    local, at, domain = value.partition("@")
    if not at or not local or not domain or "@" in domain or " " in value:
        return None
    return value


def name_key(raw: object) -> str | None:
    if raw is None:
        return None
    value = unicodedata.normalize("NFKC", str(raw)).casefold()
    value = _ARABIC_MARKS.sub("", value).translate(_ARABIC_VARIANTS)
    words = re.findall(r"[^\W\d_]+", value)
    return " ".join(words) if len(words) >= 2 else None
