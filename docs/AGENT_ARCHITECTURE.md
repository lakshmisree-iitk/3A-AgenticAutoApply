# Agentic Applybot — Architecture

_Saved 2026-09-23. Living doc: refine as the build progresses._

## 1. Goal

A job-application agent that works the way a personal agent would:
look at the page, understand each question, answer from what it knows,
park what it doesn't, and never submit without Sree's explicit word
for that run. Curated applications only — Sree names each job; the bot
never discovers or bulk-applies.

## 2. The agent loop (one generic engine, no per-site cases)

There are no ATS adapters in this design — no Ashby case, no Amazon
case, no Workday case. The bot works the way a personal agent works:
it looks at whatever page is in front of it, figures out what it is,
acts, then looks again.

1. **Perceive.** Snapshot the current page into structured data: every
   question (text, control type, options, required flag) plus every
   visible action (tabs, Next/Back buttons, links). Deterministic DOM
   extraction — no LLM, this is just eyes. The snapshot carries no ATS
   label; the page is the page.
2. **Reason.** The LLM analyzes the snapshot and decides the next
   move: fill a field, click a tab, walk a Next button, upload the
   résumé, report blocked (login wall, CAPTCHA it can't pass), or park
   at review. For each field it interrogates itself in writing before
   touching anything:
   - What is this question really asking?
   - What do I know? (profile → standing answers → this job's spec →
     how I answered it on past applications)
   - Is it sensitive? If yes, do I have an explicit standing answer?
     If no → park.
   - What's the risk of answering vs. parking?

   It acts only when the answers converge. Never one-shot
   classify-and-fill.
3. **Act.** The runner executes the decided DOM actions only: fill,
   click, select, upload. The LLM never touches the browser directly;
   it decides, the runner acts. (Small blast radius, auditable.)
4. **Verify.** Re-snapshot, confirm the action stuck (React forms lie),
   attach a screenshot.
5. **Gate.** Park at review. Submission happens only on Sree's explicit
   approval for that specific run. Ever.

A stepped Workday flow, an Ashby Overview tab, an Amazon login wall —
these are all just things the snapshot shows and the reasoner handles.
Site-specific knowledge ("Ashby usually has an Application tab") may
inform reasoning; it is never a code branch.

## 3. Two tiers

- **Tier 1 — deterministic** (the current scripted adapter): safe
  contact fields, resume upload, referrer blanking, conditional-field
  skips. Fast, free, no tokens.
- **Tier 2 — agent** (Gemini): takes everything Tier 1 parked or
  hesitated on, reasons over all of it in **one batched call** (not one
  call per field — this is the cost control), then acts field by field
  with read-back verification.

Tier 1 is the reliable 80%; Tier 2 is the judgment for the rest.

## 4. Perceive layer (Phase 1) — the page is the page

`applybot/perceive.py` → `snapshot_form(page)` returns the current
page as structured data:

```json
{"questions": [
  {"question": "How did you hear about EliseAI?",
   "required": true,
   "controls": [
     {"kind": "checkbox", "label": "LinkedIn", "options": ["LinkedIn", "X", ...],
      "control_id": "..."}]}],
 "actions": [
  {"kind": "button", "label": "Submit Application", "control_id": ""}]}
```

Rules:

- **DOM-driven, never site-driven.** It reads whatever page is in front
  of it: labels via `label[for]` / wrapping label / `aria-labelledby` /
  `aria-label` / placeholder, options from labels and selects, question
  text from the nearest block-sized ancestor (deep walk, stops at
  FORM/BODY). Navigation controls (buttons, links) are inventoried as
  actions so the reasoner can click tabs and Next buttons. No per-site
  field maps, no per-site navigation code.
- **One page = one snapshot.** The reasoner decides what to do with
  the page it sees; after acting, it snapshots again.
- Shared extraction primitives live in `perceive.py`; the old
  scripted filler imports them (no duplicated JS).

## 5. Why there are no ATS adapters

Ashby, Amazon.jobs, Workday, and whatever comes next are all handled
by the same loop. What used to be "adapter logic" is now just
reasoning over the snapshot:

- Ashby opens on an Overview tab → the snapshot shows an "Application"
  action → the reasoner clicks it.
- Amazon.jobs shows a sign-in wall → the snapshot shows no form, only
  the wall → the reasoner reports blocked ("sign-in needed") and stops.
  The bot never handles credentials itself.
- Workday walks a stepped, multi-section flow → each step snapshots
  its questions and its Next button → the reasoner fills, clicks Next,
  repeats.
- A question the site asks in an unfamiliar shape → the reasoner reads
  the question text and options and decides fill / blank / park from
  Sree's rules, same as any other question.

The `adapters/` module and its registry are a temporary scaffold
around the old scripted filler; Phase 2 removes them in favor of the
single generic loop. The only site-independent special cases that
remain are **policy**, not site logic: never submit without explicit
per-run approval, never invent answers, park sensitive questions
without standing answers, leave referrer fields blank.

## 6. Reason layer (Phase 2) — the self-questioning agent

- Input: the page snapshot (questions + actions) + profile + standing
  answers + job spec + relevant past answers.
- The LLM reasons over the whole snapshot in **one batched call** and
  returns the next actions: fill fields (with values and their
  evidence source), click an action (a tab, a Next button), upload the
  résumé, report blocked, or park. Per field it returns `fill` |
  `blank` (referrer-type) | `park` (with the exact question for Sree) —
  each with its reasoning attached.
- The runner executes the decided actions, re-snapshots, and verifies.
  After any page-changing action (a click, a tab, a Next button), it
  re-snapshots and the reasoner re-decides before continuing the batch —
  never fill blindly after the page changed. The loop repeats until the
  form is done or parked. The LLM never touches the browser directly; it
  only decides, the runner acts. (Small blast radius, auditable.)
- **Gemini key**: env var at runtime only, never in the repo, never
  logged. "Use tokens as needed" authorized 2026-09-23; cost stays
  bounded via batching + caching (identical question text reuses past
  reasoning without a new call). The old Tier-1 scripted filler remains
  as a fast path for safe fields so routine applications don't burn
  tokens on the obvious.

## 7. Memory (Phase 3) — this is what makes it "curated"

- Every run logs: question → decision → value → Sree's corrections.
- A question she answers once becomes a **proposed** standing answer;
  it goes automatic only when she approves it.
- Parked questions shrink over time. The bot gets more autonomous
  strictly from her decisions, never from guessing.

## 8. Safety rails (the agent's constitution)

- `--park` is the default; submit only on explicit per-run approval.
- Never fabricate: no answer → park, never invent.
- Sensitive questions (work auth, sponsorship, citizenship, EEO,
  background, compensation without a range) park unless a standing
  answer exists.
- Referrer/recruiter/employee-name fields stay blank — never parked,
  never filled.
- Full reasoning log per run under `runs/<job>/<ts>/` (audit trail).
- Secrets (API keys, credentials) are runtime-only; never written to
  disk, logs, or the repo.

## 9. Build phases & status

- [x] Phase 0 — scripted adapter, Ashby live run, standing answers
- [x] Phase 1 — perceive layer: `snapshot_form` (questions + actions) +
  `snapshot` CLI. Deliberately site-agnostic.
- [x] Phase 2 — reason layer: the generic perceive→reason→act→verify
  loop (`reason.py` + `agent.py`). Gemini decides from each snapshot;
  the runner validates every decision against hard local guards (no
  submit clicks, sensitive fills need standing/past answers, no
  password fields), executes, re-snapshots, verifies. Tier 1 stays as
  the free fast path for safe fields + resume upload. `agent` CLI
  always parks at review; adapters/ registry is now a documented
  temporary scaffold.
- [ ] Phase 3 — memory: answer log, standing-answer proposals
- [ ] Phase 4 — hardening for 24/7 operation
