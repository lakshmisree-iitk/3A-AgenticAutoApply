"""Field classifier: safe / sensitive / unknown.

The bot NEVER auto-fills a field unless it is classified SAFE *and* the
profile provides a value for it. Everything else becomes a needs_input
item that only the user can answer explicitly.
"""

from __future__ import annotations

import re

# profile key -> regexes matched against the field's label text (lowercased)
SAFE_PATTERNS: dict[str, list[str]] = {
    "first_name": [r"\bfirst name\b", r"\bgiven name\b", r"\bforename\b"],
    "last_name": [r"\blast name\b", r"\bsurname\b", r"\bfamily name\b"],
    "full_name": [r"\bfull name\b", r"\byour name\b", r"\bapplicant name\b",
                  r"\bname\b"],
    "email": [r"\bemail\b", r"\be-mail\b"],
    "phone": [r"\bphone\b", r"\bmobile\b", r"\btelephone\b", r"\btel\b"],
    "address": [r"\bstreet address\b", r"\baddress line", r"\bmailing address\b"],
    "city": [r"\bcity\b", r"\btown\b"],
    "state": [r"\bstate\b", r"\bprovince\b"],
    "zip": [r"\bzip\b", r"\bpostal code\b", r"\bpostcode\b"],
    "country": [r"\bcountry\b"],
    "location": [r"\blocation\b"],
    "linkedin": [r"linkedin"],
    "website": [r"\bwebsite\b", r"\bportfolio\b", r"\bpersonal site\b"],
    "github": [r"github"],
}

# A bare "name" label must NOT match when the field asks for someone ELSE's
# name (referrer, recruiter, ...) or an organization's name. Those become
# needs_input instead of taking the applicant's name.
NAME_EXCLUSIONS = [
    r"referr", r"recruit", r"employee", r"friend", r"colleague",
    r"their name", r"manager", r"supervisor",
    r"company", r"employer", r"university", r"school", r"college",
]
_COMPILED_NAME_EXCL = [re.compile(p) for p in NAME_EXCLUSIONS]


def is_other_name(label: str) -> bool:
    """True when the label asks for someone ELSE's name (referrer, recruiter,
    employee, ...) or an organization's name. Such fields are left blank."""
    return any(p.search((label or "").lower()) for p in _COMPILED_NAME_EXCL)

# category -> regexes; a match means NEVER auto-answer
SENSITIVE_PATTERNS: dict[str, list[str]] = {
    "work_authorization": [
        r"authori[sz]ed to work",
        r"work authori[sz]ation",
        r"legally able to work",
        r"sponsorship",
        r"\bvisa\b",
        r"h-?1b",
        r"immigration",
        r"i-?140",
        r"employment eligibility",
        r"require sponsorship",
    ],
    "citizenship": [
        r"citizenship",
        r"citizen of",
        r"country of (birth|residence)",
        r"national origin",
        r"permanent resident",
        r"green card",
    ],
    "compensation": [
        r"\bsalary\b",
        r"compensation",
        r"pay expectation",
        r"desired pay",
        r"expected (pay|salary|compensation)",
        r"base pay",
    ],
    "eeo": [
        r"\bgender\b",
        r"\brace\b",
        r"ethnicity",
        r"veteran",
        r"disability",
        r"protected class",
    ],
    "background": [
        r"criminal",
        r"felony",
        r"misdemeanor",
        r"background check",
        r"conviction",
    ],
    "attestation": [
        r"\bcertify\b",
        r"\battest\b",
        r"acknowledge",
        r"\bagree to\b",
        r"\bconsent\b",
        r"i confirm",
        r"terms and conditions",
    ],
}

_COMPILED_SAFE = {
    key: [re.compile(p) for p in pats] for key, pats in SAFE_PATTERNS.items()
}
_COMPILED_SENSITIVE = {
    cat: [re.compile(p) for p in pats] for cat, pats in SENSITIVE_PATTERNS.items()
}


def classify(label: str) -> tuple[str, str | None]:
    """Return (verdict, detail).

    verdict is one of:
      "safe"      -> detail is the profile key to fill from
      "sensitive" -> detail is the category; never auto-answer
      "unknown"   -> detail is None; never guess
    Sensitive wins over safe when both match.
    """
    text = (label or "").lower()
    for category, patterns in _COMPILED_SENSITIVE.items():
        if any(p.search(text) for p in patterns):
            return "sensitive", category
    for key, patterns in _COMPILED_SAFE.items():
        if key == "full_name" and any(p.search(text) for p in _COMPILED_NAME_EXCL):
            continue  # someone else's name, or an organization's name
        if any(p.search(text) for p in patterns):
            return "safe", key
    return "unknown", None
