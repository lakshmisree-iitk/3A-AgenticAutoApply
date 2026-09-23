# Agentic Applybot — Architecture

_Saved 2026-09-23. Living doc: refine as the build progresses._

## 1. Goal

A job-application agent that works the way a personal agent would:
look at the page, understand each question, answer from what it knows,
park what it doesn't, and never submit without Sree's explicit word
for that run. Curated applications only — Sree names each job; the bot
never discovers or bulk-applies.

## 2. The agent loop

Runs per application, and per field inside it:

1. **Perceive.** Snapshot the form into a structured inventory: every
   field's question text, control type, options, required flag.
   Deterministic DOM extraction — no LLM, this is just eyes.
2. **Reason.** For each field the agent interrogates itself in writing
   before touching anything:
   - What is this question really asking?
   - What do I know? (profile → standing answers → this job's spec →
     how I answered it on past applications)
   - Is it sensitive? If yes, do I have an explicit standing answer?
     If no → park.
   - What's the risk of answering vs. parking?
   
   It acts only when the answers converge. Never one-shot
   classify-and-fill.
3. **Act.** Fill one field, read it back to confirm it stuck
   (React forms lie).
4. **Verify.** Screenshot plus a field-by-field report: filled, blanked,
   or parked — with the reasoning attached to each.
5. **Gate.** Park at review. Submission happens only on Sree's explicit
   approval for that specific run. Ever.

## 3. Two tiers

- **Tier 1 — deterministic** (the current scripted adapter): safe
  contact fields, resume upload, referrer blanking, conditional-field
  skips. Fast, free, no tokens.
- **Tier 2 — agent** (Gemini): takes everything Tier 1 parked or
  hesitated on, reasons over all of it in **one batched call** (not one
  call per field — this is the cost control), then acts field by field
  with read-back verification.

Tier 1 is the reliable 80%; Tier 2 is the judgment for the rest.

## 4. Perceive layer (Phase 1) — ATS-agnostic by construction

`applybot/perceive.py` → `snapshot_form(page)` returns a question
inventory:

```json
{"questions": [
  {"question": "How did you hear about EliseAI?",
   "required": true,
   "controls": [
     {"kind": "checkbox", "label": "LinkedIn", "options": ["LinkedIn", "X", ...],
      "control_id": "...", "visible": true}]}
]}
```

Rules:

- **DOM-driven, never ATS-driven.** It reads whatever page is in front
  of it: labels via `label[for]` / wrapping label / `aria-labelledby` /
  `aria-label` / placeholder, options from labels and selects, question
  text from the nearest block-sized ancestor (deep walk, stops at
  FORM/BODY). No per-ATS field maps.
- **One page = one snapshot.** Multi-step flows are snapshotted per
  step; the adapter owns step navigation.
- Shared extraction primitives live in `perceive.py`; adapters import
  them (no duplicated JS).

## 5. ATS coverage

### Ashby (jobs.ashbyhq.com) — working

- Lands on an Overview tab; the form lives behind the Application tab.
  Adapter clicks through and dismisses the cookie banner first.
- Name-less, deeply nested checkbox/radio groups; Yes/No rendered as
  `<button>` pills; conditional fields rendered twice. All handled in
  the perceive layer (deep question-text walk, pill-toggle detection).

### Amazon.jobs — next surface

Amazon's flow differs structurally, so it gets its own adapter; the
agent loop and perceive layer do not change:

- **Sign-in gate.** amazon.jobs requires an authenticated session
  before the application form. The bot never handles credentials
  itself: it reuses a saved browser session, or parks with
  "sign-in needed" for Sree. (Her 2026-09-22 application used Google
  sign-in, which she did herself.)
- **Stepped, multi-section flow** (not one long form). The Amazon
  adapter walks sections: snapshot current step → fill → Next →
  repeat. Each step gets its own inventory, so the reasoning stays
  per-question, never per-ATS.
- **Known question shapes** (from her completed 2026-09-22
  application): work authorization, sponsorship (H-1B transfer),
  I-140, citizenship, start date, EEO self-identification. Sponsorship
  and work-auth answers come from standing answers; EEO stays parked
  unless she says otherwise.
- DOM specifics to be captured from a live snapshot run and recorded
  here — no guessing.

### Adding a future ATS

New ATS = new adapter (navigation quirks only: tab clicks, step
walking, auth gates) + a fixture replicating its DOM shape +
regression tests. The loop, perceive, reason, memory, and rails are
untouched.

## 6. Reason layer (Phase 2) — the self-questioning agent

- Input: the question inventory from Phase 1 + profile + standing
  answers + job spec + relevant past answers.
- One batched Gemini call reasons over every uncertain field and
  returns, per field: `fill` (with value) | `blank` (referrer-type) |
  `park` (with the exact question for Sree) — each with its reasoning.
- The runner executes the decisions: fill + read-back verify, blank,
  or park. The LLM never touches the browser directly; it only
  decides, the runner acts. (Small blast radius, auditable.)
- **Gemini key**: env var at runtime only, never in the repo, never
  logged. "Use tokens as needed" authorized 2026-09-23; cost stays
  bounded via batching + caching (identical question text reuses past
  reasoning without a new call).

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
- [ ] Phase 1 — perceive layer: `snapshot_form` + `snapshot` CLI
  (ATS-agnostic; proven on Ashby DOM shape)
- [ ] Phase 2 — reason layer: batched Gemini self-questioning over
  the inventory; Amazon.jobs adapter (auth gate + stepped flow)
- [ ] Phase 3 — memory: answer log, standing-answer proposals
- [ ] Phase 4 — hardening for 24/7 operation
