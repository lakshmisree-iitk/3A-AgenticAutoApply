"""Profile and job-spec loading.

profile.yaml holds ONLY safe contact fields. It must never contain answers
to sensitive questions (visa, compensation, EEO, ...). It is git-ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Profile:
    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    country: str = ""
    location: str = ""
    linkedin: str = ""
    website: str = ""
    github: str = ""

    def value(self, key: str) -> str:
        return getattr(self, key, "") or ""


@dataclass
class JobSpec:
    id: str
    company: str
    role: str
    url: str
    resume_pdf: str
    ats: str = "generic"  # adapter hint: generic | greenhouse | lever | ashby ...
    pay_range: tuple[int, int] | None = None  # posted (min, max), for comp strategy

    @classmethod
    def load(cls, path: str | Path) -> "JobSpec":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for required in ("id", "company", "role", "url", "resume_pdf"):
            if not data.get(required):
                raise ValueError(f"job spec {path} is missing required key: {required}")
        resume = Path(data["resume_pdf"]).expanduser()
        if not resume.exists():
            raise ValueError(f"resume_pdf does not exist: {resume}")
        pr = data.get("pay_range")
        pay = (int(pr[0]), int(pr[1])) if pr else None
        return cls(
            id=str(data["id"]),
            company=str(data["company"]),
            role=str(data["role"]),
            url=str(data["url"]),
            resume_pdf=str(resume.resolve()),
            ats=str(data.get("ats", "generic")),
            pay_range=pay,
        )


def load_profile(path: str | Path = "profile.yaml") -> Profile:
    path = Path(path)
    if not path.exists():
        raise SystemExit(
            f"Profile not found: {path}\n"
            "Copy applybot/profile.example.yaml to profile.yaml and fill in "
            "your contact details. profile.yaml is git-ignored: never commit it."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    known = {f for f in Profile.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ValueError(
            f"profile.yaml contains unexpected keys {sorted(unknown)}; "
            "only safe contact fields are allowed here."
        )
    return Profile(**{k: str(v or "") for k, v in data.items() if k in known})


def load_standing(path: str | Path = "standing.yaml") -> dict:
    """Sree's explicit standing answers to recurring sensitive questions.

    Returns {} when the file is absent. Every value here was stated
    explicitly by her and is meant to be reused across applications.
    standing.yaml is git-ignored: never commit it.
    """
    path = Path(path)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {k: str(v or "") for k, v in data.items()}
