"""Per-application state, kept outside the model in JSON files.

States: new -> filling -> needs_input -> ready_for_review -> approved
        -> submitting -> submitted
        blocked / failed are terminal without user action.

state/<job_id>.json holds the current state; runs/<job_id>/<ts>/ holds
the per-run log and screenshots.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STATES = (
    "new",
    "filling",
    "needs_input",
    "ready_for_review",
    "approved",
    "submitting",
    "submitted",
    "blocked",
    "failed",
)


class Store:
    def __init__(self, root: str | Path = "state"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def load(self, job_id: str) -> dict:
        p = self._path(job_id)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return {"job_id": job_id, "state": "new", "history": []}

    def save(self, job_id: str, data: dict) -> None:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._path(job_id).write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    def transition(self, job_id: str, to: str, note: str = "") -> dict:
        if to not in STATES:
            raise ValueError(f"unknown state: {to}")
        data = self.load(job_id)
        data["history"].append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "from": data.get("state"),
                "to": to,
                "note": note,
            }
        )
        data["state"] = to
        self.save(job_id, data)
        return data

    def run_dir(self, job_id: str) -> Path:
        d = (
            Path("runs")
            / job_id
            / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        )
        d.mkdir(parents=True, exist_ok=True)
        return d
