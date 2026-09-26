"""Reason layer (Phase 2): the LLM decides, the runner acts.

The model analyzes a page snapshot and returns the next actions as
structured JSON. It never touches the browser. The agent loop
(agent.py) validates every decision against hard local guards before
executing anything.

Gemini access: API key from the GEMINI_API_KEY environment variable at
runtime only. It is never written to disk, never logged, never echoed.
Model defaults to gemini-3.6-flash (same as Tailor_Agent) and can be
overridden with GEMINI_MODEL.
"""

from __future__ import annotations

import json
import os
import urllib.request

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
_API = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


class ReasonError(Exception):
    """The reasoner could not produce a decision (no key, API error, ...)."""


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise ReasonError(
            "GEMINI_API_KEY is not set. Export it in this shell before "
            "running the agent; it is never stored or logged."
        )
    return key


SYSTEM_TEMPLATE = """You are the reasoning brain of a job-application agent working for Sree (Lakshmisree Iyengar).
You NEVER touch the browser. You analyze a page snapshot and return the next actions as JSON.
A separate runner executes only the actions you return, then shows you the new snapshot.

{goal}

HARD RULES:
- Never invent facts. Every filled value must cite its evidence source: profile, standing (her explicit reusable answers), job_spec, or past_answer (answers she gave earlier on this application). No source -> park the question.
- Sensitive questions (work authorization, sponsorship, citizenship/visa, EEO/demographic, background checks, compensation, attestations, anything asking for another person's identity) may ONLY be filled from standing or past_answer. Otherwise park.
- Referrer / recruiter / employee-name fields: decision "blank". Do not fill, do not park.
- Password, SSN, bank-account, or other credential fields: park, never fill.
{submit_rule}
- If the page shows a login wall, CAPTCHA, or no form at all: set blocked with the reason and stop. Do not attempt to bypass.
- Prefer clicking visible actions (tabs, Next buttons) to reach the form over reporting blocked.
- For choice controls (radio/checkbox/button pills), name the exact option text to select.
- Keep "thinking" concise: one line of reasoning per field.

Return JSON only, exactly this schema:
{
  "thinking": "per-field reasoning, one line each",
  "fields": [
    {"control_id": "string", "decision": "fill|blank|park",
     "value": "string or null", "option": "exact option text or null",
     "source": "profile|standing|job_spec|past_answer|null",
     "reason": "one line"}
  ],
  "clicks": [{"control_id": "string", "label": "string", "purpose": "one line"}],
  "upload_resume": false,
  "blocked": "reason or null",
  "page_done": false,
  "notes": "anything the runner should log"
}
Only include fields that need a decision now; omit fields that are already correctly filled.
"""


def system_prompt(allow_submit: bool = False) -> str:
    """The system prompt, with the submit rule switched by the explicit
    --submit opt-in. Default (False) is the park-and-never-submit behavior."""
    if allow_submit:
        goal = ("GOAL: complete the job application form accurately, then submit it. "
                "The user explicitly authorized this run to submit.")
        submit_rule = ("- Return a click on the submit/apply/send control ONLY as the final action, "
                       "when the form is fully complete and every field is verified correct. "
                       "Never click it earlier, and never click it twice.")
    else:
        goal = ("GOAL: complete the job application form, then stop. "
                "You do not submit \u2014 submission is a separate human-approved step.")
        submit_rule = ("- Never return a click on anything labeled submit/apply/send. "
                       "If the form looks complete, set page_done=true and stop.")
    return (SYSTEM_TEMPLATE
            .replace("{goal}", goal)
            .replace("{submit_rule}", submit_rule))


# Backwards-compatible default prompt: park, never submit.
SYSTEM = system_prompt(False)


def build_prompt(snapshot: dict, profile: dict, standing: dict,
                 job: dict, past_answers: dict, step: int) -> str:
    parts = [
        f"STEP {step}.",
        f"JOB: {job.get('role')} at {job.get('company')}",
    ]
    if job.get("pay_range"):
        lo, hi = job["pay_range"]
        parts.append(f"POSTED PAY RANGE: ${lo:,} - ${hi:,}")
    parts += [
        f"PROFILE (safe contact facts): {json.dumps(profile)}",
        f"STANDING ANSWERS (her explicit reusable answers): {json.dumps(standing)}",
        f"PAST ANSWERS she gave on this application: {json.dumps(past_answers)}",
        f"PAGE SNAPSHOT: {json.dumps(snapshot)}",
        "Decide the next actions as JSON.",
    ]
    return "\n".join(parts)


def _call_gemini(system: str, prompt: str) -> dict:
    key = _api_key()  # local only; never logged or stored
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
            "maxOutputTokens": 4096,
        },
    }).encode()
    req = urllib.request.Request(
        _API, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        # Never include the key or body in the error surface.
        detail = exc.read().decode(errors="replace")[:300]
        raise ReasonError(f"Gemini API error {exc.code}: {detail}") from exc
    except Exception as exc:  # noqa: BLE001 - network failure
        raise ReasonError(f"Gemini request failed: {exc}") from exc
    try:
        text = "".join(
            p.get("text", "")
            for p in data["candidates"][0]["content"]["parts"]
        )
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise ReasonError(f"Gemini returned no usable JSON: {exc}") from exc


def decide(snapshot: dict, profile: dict, standing: dict, job: dict,
           past_answers: dict, step: int, allow_submit: bool = False) -> dict:
    """One batched reasoning call over the snapshot. Returns the decision
    dict (schema above); missing keys get safe defaults."""
    raw = _call_gemini(system_prompt(allow_submit), build_prompt(
        snapshot, profile, standing, job, past_answers, step))
    if not isinstance(raw, dict):
        raise ReasonError("Gemini decision was not a JSON object")
    return {
        "thinking": str(raw.get("thinking", "")),
        "fields": raw.get("fields") or [],
        "clicks": raw.get("clicks") or [],
        "upload_resume": bool(raw.get("upload_resume", False)),
        "blocked": raw.get("blocked"),
        "page_done": bool(raw.get("page_done", False)),
        "notes": str(raw.get("notes", "")),
    }
