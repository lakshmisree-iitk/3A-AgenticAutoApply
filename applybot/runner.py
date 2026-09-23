"""Orchestration: the state machine that mirrors a supervised assistant.

  apply   -> fill safe fields, upload resume; auto-submits when the form is
             clean (Sree's explicit call, 2026-09-22). --park stops at review.
  answer  -> record the user's explicit answers to needs_input questions
  approve -> manual override: record explicit approval to submit (who + when)
  submit  -> manual override: only runs when approved

Sensitive/unknown fields still become needs_input items and are never
guessed. Every run writes a log + screenshots under runs/<job_id>/<ts>/.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from applybot import browser as B
from applybot.adapters import pick
from applybot.config import JobSpec, load_profile
from applybot.state import Store

SUBMIT_LABELS = ["submit application", "submit"]
SUCCESS_MARKERS = [
    "thank you for applying",
    "application submitted",
    "application received",
    "successfully submitted",
]


class Runner:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()

    # -- apply ----------------------------------------------------------
    def apply(self, job_path: str, headless: bool = True,
              auto_submit: bool = True) -> dict:
        """Fill the form; when clean, submit automatically (Sree's call).

        Sensitive/unknown fields still become needs_input items and are never
        guessed. Pass auto_submit=False (CLI: --park) to stop at review.
        """
        job = JobSpec.load(job_path)
        profile = load_profile()
        run_dir = self.store.run_dir(job.id)
        log = (run_dir / "run.log").open("w", encoding="utf-8")

        def say(msg: str) -> None:
            print(msg)
            log.write(msg + "\n")
            log.flush()

        data = self.store.transition(job.id, "filling", f"applying to {job.url}")
        data["job"] = {
            "company": job.company, "role": job.role,
            "url": job.url, "resume_pdf": job.resume_pdf, "ats": job.ats,
        }

        try:
            with B.launch(headless=headless) as page:
                say(f"opening {job.url}")
                page.goto(job.url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                blocker = B.detect_blockers(page)
                if blocker:
                    say(f"BLOCKED: {blocker}")
                    page.screenshot(path=str(run_dir / "blocked.png"))
                    self.store.transition(job.id, "blocked", blocker)
                    return self._finish(log, job.id, {"blocked": blocker})

                adapter = pick(page, job.ats)
                say(f"adapter: {adapter.name}")
                answered = data.get("answers", {})
                result = adapter.fill(page, profile, job.resume_pdf, answered)

                say(f"filled {len(result.filled)} fields; "
                    f"{len(result.needs)} need input; "
                    f"resume uploaded: {result.resume_uploaded}")
                for n in result.notes:
                    say(f"note: {n}")

                shot = run_dir / "review.png"
                page.screenshot(path=str(shot), full_page=True)

                data = self.store.load(job.id)
                data["filled"] = result.filled
                data["resume_uploaded"] = result.resume_uploaded
                # merge new needs with already-answered ones
                known = {n["field_id"] for n in data.get("needs", [])}
                for need in result.needs:
                    if need.field_id not in answered and need.field_id not in known:
                        data.setdefault("needs", []).append(asdict(need))
                # drop needs that are now answered
                data["needs"] = [
                    n for n in data.get("needs", []) if n["field_id"] not in answered
                ]
                self.store.save(job.id, data)

                if data["needs"]:
                    say(f"PARKED: {len(data['needs'])} questions need your answers.")
                    for n in data["needs"]:
                        say(f"  - [{n['kind']}] {n['label']}")
                    self.store.transition(job.id, "needs_input",
                                          f"{len(data['needs'])} questions open")
                elif auto_submit:
                    self._do_submit(page, job, run_dir, say)
                else:
                    say("PARKED at review (--park).")
                    self.store.transition(job.id, "ready_for_review",
                                          "all fields filled or uploaded")
        except Exception as exc:  # noqa: BLE001
            say(f"FAILED: {exc}")
            self.store.transition(job.id, "failed", str(exc)[:500])
        finally:
            log.close()
        return self.store.load(job.id)

    # -- answer ---------------------------------------------------------
    def answer(self, job_id: str, answers: dict[str, str]) -> dict:
        """Record the user's explicit answers, then re-run the fill."""
        data = self.store.load(job_id)
        if data.get("state") not in ("needs_input", "ready_for_review", "failed"):
            raise SystemExit(
                f"job {job_id} is in state {data.get('state')}; "
                "nothing to answer right now."
            )
        open_ids = {n["field_id"] for n in data.get("needs", [])}
        unknown = set(answers) - open_ids
        if unknown:
            raise SystemExit(f"answers for unknown questions: {sorted(unknown)}")
        data.setdefault("answers", {}).update(answers)
        data["needs"] = [n for n in data.get("needs", []) if n["field_id"] not in answers]
        self.store.save(job_id, data)
        print(f"recorded {len(answers)} answer(s) for {job_id}")
        return data

    # -- approve / submit ----------------------------------------------
    # NOTE: as of 2026-09-22 the default flow auto-submits once the form is
    # clean (Sree's explicit call). approve/submit remain as manual overrides.
    def approve(self, job_id: str, approver: str) -> dict:
        data = self.store.load(job_id)
        if data.get("state") not in ("ready_for_review", "needs_input"):
            raise SystemExit(
                f"cannot approve job {job_id} in state {data.get('state')}"
            )
        if data.get("needs"):
            raise SystemExit(
                f"job {job_id} still has {len(data['needs'])} open questions; "
                "answer them first."
            )
        data["approval"] = {"by": approver, "note": "explicit user approval"}
        self.store.save(job_id, data)
        self.store.transition(job_id, "approved", f"approved by {approver}")
        print(f"{job_id}: approved to submit by {approver}")
        return self.store.load(job_id)

    def submit(self, job_id: str, job_path: str, headless: bool = True) -> dict:
        """Manual submit override: still requires a recorded approval."""
        data = self.store.load(job_id)
        if data.get("state") != "approved" or not data.get("approval"):
            raise SystemExit(
                f"refusing to submit {job_id}: no explicit approval on record. "
                "Run `approve` first."
            )
        job = JobSpec.load(job_path)
        run_dir = self.store.run_dir(job.id)

        def say(msg: str) -> None:
            print(msg)

        try:
            with B.launch(headless=headless) as page:
                page.goto(job.url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                # re-fill to be safe (idempotent), then submit
                profile = load_profile()
                adapter = pick(page, job.ats)
                adapter.fill(page, profile, job.resume_pdf, data.get("answers", {}))
                self._do_submit(page, job, run_dir, say)
        except Exception as exc:  # noqa: BLE001
            self.store.transition(job.id, "failed", str(exc)[:500])
            print(f"{job_id}: submit failed: {exc}")
        return self.store.load(job_id)

    def _do_submit(self, page, job, run_dir, say) -> None:
        """Click submit in the live form and verify a confirmation marker."""
        self.store.transition(job.id, "submitting", "clicking submit (auto)")
        clicked = self._click_submit(page)
        if not clicked:
            raise RuntimeError("could not find a submit button")
        page.wait_for_timeout(5000)
        page.screenshot(path=str(run_dir / "submitted.png"), full_page=True)
        text = (page.content() or "").lower()
        if any(m in text for m in SUCCESS_MARKERS):
            self.store.transition(job.id, "submitted",
                                  "confirmation detected (auto-submit)")
            say(f"{job.id}: SUBMITTED and confirmed.")
        else:
            self.store.transition(
                job.id, "failed",
                "clicked submit but no confirmation marker found; "
                f"check {run_dir / 'submitted.png'}",
            )
            say(f"{job.id}: submit clicked but NOT confirmed - check screenshot.")

    def _click_submit(self, page) -> bool:
        for label in SUBMIT_LABELS:
            try:
                btn = page.get_by_role("button", name=label)
                if btn.count() > 0:
                    btn.first.scroll_into_view_if_needed()
                    btn.first.click()
                    return True
            except Exception:  # noqa: BLE001
                continue
        # fallback: any submit-type input
        try:
            btns = page.query_selector_all("input[type=submit], button[type=submit]")
            if btns:
                btns[0].scroll_into_view_if_needed()
                btns[0].click()
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _finish(self, log, job_id: str, extra: dict) -> dict:
        log.close()
        data = self.store.load(job_id)
        data.update(extra)
        return data
