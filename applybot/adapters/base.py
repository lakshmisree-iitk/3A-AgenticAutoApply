"""Adapter interface: one per ATS / site shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Need:
    """A question the bot refused to answer on its own."""

    field_id: str        # stable identifier for this field on the page
    label: str           # human-readable question text
    kind: str            # "sensitive:<category>" or "unknown"
    control: str         # input | textarea | select | radio | checkbox | file
    options: list[str] = field(default_factory=list)  # for radios/selects


@dataclass
class FillResult:
    filled: list[str] = field(default_factory=list)      # labels that were filled
    needs: list[Need] = field(default_factory=list)     # questions left for the user
    resume_uploaded: bool = False
    parked_at_review: bool = False
    notes: list[str] = field(default_factory=list)


class Adapter(Protocol):
    name: str

    def detect(self, page) -> bool:
        """True if this adapter handles the current page."""
        ...

    def fill(self, page, profile, resume_pdf: str, answered: dict) -> FillResult:
        """Fill safe fields, upload resume, collect needs. Never submits."""
        ...
