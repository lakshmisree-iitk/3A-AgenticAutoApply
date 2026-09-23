"""Agent loop (Phase 2): perceive -> reason -> act -> verify.

One generic engine, no per-site cases. Each step: snapshot the page,
ask the reasoner (Gemini) for the next actions, validate every decision
against hard local guards, execute, re-snapshot, verify. Repeats until
the form is done, blocked, or parked.

Hard guards (in code, not in the prompt — the LLM cannot override):
- No click on anything labeled submit/apply/send. Ever. The executor
  refuses and logs it.
- A fill the local classifier rates sensitive/unknown only executes
  when its evidence source is a standing or past answer. Otherwise the
  decision is downgraded to park, even if the LLM said fill.
- Password/hidden inputs are never filled.
- A fill with no value or no evidence source becomes park.
- The loop always ends parked at review; submission is a separate
  human-approved step (the existing approve/submit commands).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from applybot import browser as B
from applybot.adapters import pick
from applybot.config import JobSpec, load_profile, load_standing
from applybot.perceive import (
    GROUP_CTX_JS,
    OPT_TEXT_JS,
    VISIBLE_JS,
    snapshot_form,
)
from applybot.reason import ReasonError, decide
from applybot.sensitive import classify
from applybot.state import Store

SUBMIT_RE = re.compile(r"submit|apply now|send application|complete application", re.I)
PASSWORD_TYPES = {"password", "hidden"}
SAFE_SOURCES_FOR_SENSITIVE = {"standing", "past_answer"}
MAX_STEPS = 12


class GuardRefusal(Exception):
    """A decision the executor refused to carry out."""


def _profile_dict(profile) -> dict:
    fields = ["first_name", "last_name", "full_name", "email", "phone",
              "address", "city", "state", "zip", "country", "location",
              "linkedin", "website", "github"]
    return {f: getattr(profile, f) for f in fields if getattr(profile, f)}


def _find_el(page, control_id: str):
    """Locate a control by the control_id the snapshot assigned."""
    if not control_id or control_id.startswith("field_"):
        return None
    try:
        el = page.query_selector(f'[name="{control_id}"]')
        if el:
            return el
        return page.query_selector(f'#{control_id}')
    except Exception:  # noqa: BLE001 - bad selector never kills it
        return None


def _find_option_el(page, question: dict, option: str):
    """Within a question's choice controls, the element whose option text
    matches. Matches the way the snapshot collected the options."""
    want = (option or "").lower()
    for c in question["controls"]:
        el = _find_el(page, c["control_id"])
        if not el:
            continue
        try:
            text = (el.evaluate(OPT_TEXT_JS) or "").lower()
            label = (c["label"] or "").lower()
            if want and (want in text or want in label or text in want):
                return el
        except Exception:  # noqa: BLE001
            continue
    return None


def _guard_fill(question_text: str, source: str | None,
                value: str | None) -> str | None:
    """Returns a park reason when the fill must not execute, else None."""
    if not value:
        return "no value supplied"
    if not source:
        return "no evidence source cited"
    verdict, _ = classify(question_text or "")
    if verdict != "safe" and source not in SAFE_SOURCES_FOR_SENSITIVE:
        return (f"classifier rates this {verdict} and the source is "
                f"'{source}', not a standing or past answer")
    return None


def _execute_fill(page, question: dict, control: dict,
                  decision: dict) -> str:
    """Fill one control per the decision. Returns a result string."""
    el = _find_el(page, control["control_id"])
    kind = control["kind"]
    if kind in ("radio", "checkbox", "button"):
        el = _find_option_el(page, question, decision.get("option"))
        if not el:
            raise GuardRefusal(
                f"option '{decision.get('option')}' not found on page")
    if not el:
        raise GuardRefusal(f"control '{control['control_id']}' not found")
    try:
        if (el.get_attribute("type") or "").lower() in PASSWORD_TYPES:
            raise GuardRefusal("password/credential field: never filled")
        if not el.evaluate(VISIBLE_JS):
            raise GuardRefusal("control not visible")
    except GuardRefusal:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GuardRefusal(f"control unreadable: {exc}") from exc

    value = decision["value"]
    if kind in ("text", "textarea"):
        el.fill(str(value))
        page.wait_for_timeout(300)
        if el.input_value() != str(value):
            return f"filled but read-back mismatch on '{control['control_id']}'"
        return f"filled '{control['control_id']}'"
    if kind == "select":
        el.select_option(label=str(value))
        page.wait_for_timeout(300)
        return f"selected '{value}'"
    if kind in ("radio", "checkbox", "button"):
        el.evaluate("(b) => { const t = (b.labels && b.labels[0]) || b; t.click(); }")
        page.wait_for_timeout(400)
        try:
            if kind in ("radio", "checkbox") and not el.is_checked():
                return f"clicked option '{decision.get('option')}' (unchecked read-back)"
        except Exception:  # noqa: BLE001
            pass
        return f"clicked option '{decision.get('option')}'"
    if kind == "file":
        return "file input: resume upload handled by Tier 1"
    return f"unsupported kind '{kind}'"


def _execute_click(page, action: dict) -> str:
    label = action.get("label", "")
    if SUBMIT_RE.search(label):
        raise GuardRefusal(f"refused click on '{label}': submit is never clicked")
    control_id = action.get("control_id") or ""
    el = None
    if control_id:
        try:
            el = page.query_selector(f"#{control_id}")
        except Exception:  # noqa: BLE001
            el = None
    if not el and label:
        try:
            if action.get("kind") == "link":
                el = page.get_by_role("link", name=label).first
            else:
                el = page.get_by_role("button", name=label).first
            if el.count() == 0:
                el = None
        except Exception:  # noqa: BLE001
            el = None
    if not el:
        raise GuardRefusal(f"action '{label}' not found")
    el.scroll_into_view_if_needed()
    el.click()
    page.wait_for_timeout(2000)
    return f"clicked '{label}'"


def _snapshot_sig(snapshot: dict) -> str:
    return json.dumps([
        (q["question"][:60], sorted(c["kind"] for c in q["controls"]))
        for q in snapshot["questions"]
    ] + [a["label"] for a in snapshot["actions"]], sort_keys=True)


def run_agent(job_path: str, headless: bool = True,
              reason_fn=None, max_steps: int = MAX_STEPS) -> dict:
    """Run the generic agent loop. Always ends parked (never submits).

    reason_fn is injectable for tests: fn(snapshot, profile, standing,
    job, past_answers, step) -> decision dict. Defaults to the Gemini
    reasoner.
    """
    job = JobSpec.load(job_path)
    profile = load_profile()
    standing = load_standing()
    store = Store()
    run_dir = store.run_dir(job.id)
    log = (run_dir / "agent.log").open("w", encoding="utf-8")
    reason_fn = reason_fn or decide

    def say(msg: str) -> None:
        print(msg)
        log.write(msg + "\n")
        log.flush()

    data = store.transition(job.id, "filling", f"agent run for {job.url}")
    data["job"] = {"company": job.company, "role": job.role,
                   "url": job.url, "ats": job.ats}
    past_answers = data.get("answers", {}) or {}
    parked: list[dict] = []
    parked_ids: set[str] = set()

    def park_field(control_id: str, question: str, reason: str) -> None:
        if control_id in parked_ids:
            return
        parked_ids.add(control_id)
        parked.append({"field_id": control_id, "label": question[:200],
                       "kind": f"parked: {reason[:120]}"})
        say(f"  PARKED [{control_id}]: {reason}")

    try:
        with B.launch(headless=headless) as page:
            page.goto(job.url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            blocker = B.detect_blockers(page)
            if blocker:
                say(f"BLOCKED: {blocker}")
                park_field("blocked", blocker, "page blocker detected")
                raise _Parked()

            adapter = pick(page, job.ats)
            adapter.prepare(page)

            # Tier 1 fast path: safe fields + standing answers + resume
            # upload, deterministic and free. The loop reasons over the rest.
            say("Tier 1: safe fills + resume upload...")
            t1 = adapter.fill(page, profile, job.resume_pdf, past_answers,
                              standing, job.pay_range)
            say(f"Tier 1: filled={len(t1.filled)} "
                f"resume_uploaded={t1.resume_uploaded}")

            last_sig = ""
            idle_steps = 0
            for step in range(1, max_steps + 1):
                say(f"\n--- step {step} ---")
                snapshot = snapshot_form(page)
                page.screenshot(path=str(run_dir / f"step{step}.png"))
                (run_dir / f"step{step}.json").write_text(
                    json.dumps(snapshot, indent=2), encoding="utf-8")
                say(f"snapshot: {len(snapshot['questions'])} questions, "
                    f"{len(snapshot['actions'])} actions")

                sig = _snapshot_sig(snapshot)
                if sig == last_sig:
                    idle_steps += 1
                else:
                    idle_steps = 0
                last_sig = sig
                if idle_steps >= 2:
                    say("no page change for 2 steps: stopping.")
                    break

                try:
                    decision = reason_fn(
                        snapshot, _profile_dict(profile), standing,
                        {"company": job.company, "role": job.role,
                         "pay_range": job.pay_range},
                        past_answers, step)
                except ReasonError as exc:
                    say(f"reasoner error: {exc}")
                    park_field("reasoner", "reasoner error", str(exc)[:200])
                    break

                thinking = (decision.get("thinking") or "").strip()
                if thinking:
                    say(f"thinking: {thinking[:600]}")
                if decision.get("notes"):
                    say(f"notes: {decision['notes'][:300]}")
                if decision.get("blocked"):
                    say(f"BLOCKED: {decision['blocked']}")
                    park_field("blocked", str(decision["blocked"])[:200],
                               "reasoner reported blocked")
                    break

                acted = False
                # -- fields --
                for f in decision.get("fields") or []:
                    cid = f.get("control_id") or ""
                    dec = f.get("decision")
                    question = next(
                        (q for q in snapshot["questions"]
                         if any(c["control_id"] == cid
                                for c in q["controls"])),
                        None)
                    qtext = question["question"] if question else ""
                    if dec == "blank":
                        say(f"  blank (referrer-type): {qtext[:80]}")
                        continue
                    if dec != "fill":
                        park_field(cid, qtext or cid,
                                   f.get("reason") or "parked by reasoner")
                        continue
                    if not question:
                        park_field(cid, cid, "control not in snapshot")
                        continue
                    control = next(c for c in question["controls"]
                                   if c["control_id"] == cid)
                    refusal = _guard_fill(qtext,
                                          f.get("source"), f.get("value"))
                    if refusal:
                        say(f"  GUARD override [{cid}]: {refusal}")
                        park_field(cid, qtext, refusal)
                        continue
                    try:
                        result = _execute_fill(page, question, control, f)
                        say(f"  {result}")
                        acted = True
                        if "mismatch" in result or "unchecked" in result:
                            park_field(cid, qtext,
                                       f"verification issue: {result}")
                    except GuardRefusal as exc:
                        say(f"  GUARD refusal [{cid}]: {exc}")
                        park_field(cid, qtext, str(exc))

                # -- resume upload on request --
                if decision.get("upload_resume"):
                    try:
                        file_els = page.query_selector_all(
                            'input[type="file"]')
                        if file_els and job.resume_pdf:
                            file_els[0].set_input_files(job.resume_pdf)
                            page.wait_for_timeout(2000)
                            say("  resume uploaded on reasoner request")
                            acted = True
                    except Exception as exc:  # noqa: BLE001
                        say(f"  resume upload failed: {exc}")

                # -- clicks (never submit) --
                page_changed = False
                for c in decision.get("clicks") or []:
                    try:
                        result = _execute_click(page, c)
                        say(f"  {result}")
                        acted = True
                        page_changed = True
                    except GuardRefusal as exc:
                        say(f"  GUARD refusal: {exc}")
                if page_changed:
                    page.wait_for_timeout(1500)

                if decision.get("page_done") and not acted:
                    say("reasoner reports the page is done.")
                    break
                if not acted and not decision.get("page_done"):
                    say("no actions taken this step.")
                    idle_steps += 1
                    if idle_steps >= 2:
                        say("nothing left to do: stopping.")
                        break

            # -- end of loop: always park, never submit --
            if parked:
                say(f"\nPARKED: {len(parked)} question(s) need input.")
                data["needs"] = parked
                store.save(job.id, data)
                store.transition(job.id, "needs_input",
                                 f"agent parked {len(parked)} question(s)")
            else:
                say("\nPARKED at review: form complete, awaiting your review.")
                store.transition(job.id, "ready_for_review",
                                 "agent loop complete; parked at review")
    except _Parked:
        data["needs"] = parked
        store.save(job.id, data)
        store.transition(job.id, "needs_input", "agent parked (blocked page)")
    except Exception as exc:  # noqa: BLE001
        say(f"FAILED: {exc}")
        store.transition(job.id, "failed", str(exc)[:500])
    finally:
        log.close()
    return store.load(job.id)


class _Parked(Exception):
    """Internal: stop the loop, record parked needs."""
