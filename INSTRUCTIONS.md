# Applybot Instructions (MacBook)

Supervised job-application automation: it fills what's safe, answers
recurring questions from your standing answers, parks anything it isn't
sure about, and submits only when the form is clean.

## 1. One-time setup

Unzip the bot folder, open Terminal inside it, and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Create `profile.yaml` in the bot folder (contact details only — this file
is git-ignored and never committed):

```yaml
first_name: "Lakshmisree"
last_name: "Iyengar"
full_name: "Lakshmisree Iyengar"
email: "lakshmisreeiyengar@gmail.com"
phone: "470-923-7530"
address: "330 Angelo Cifelli Dr Apt 488"
city: "Harrison"
state: "NJ"
zip: "07029"
country: "USA"
location: "Harrison, New Jersey, USA"
linkedin: "https://www.linkedin.com/in/lakshmisree-iyengar"
website: ""
github: "https://github.com/lakshmisree-iitk"
```

Create `standing.yaml` in the bot folder (your explicit reusable answers
to recurring questions — also git-ignored, never committed):

```yaml
how_heard: "LinkedIn"
us_work_authorized: "Yes"
requires_sponsorship: "Yes"
open_to_relocate: "Yes"
work_arrangement: "I am comfortable with in-office, hybrid, or remote work arrangements"
in_office_ok: "Yes"
compensation_strategy: "midpoint_of_posted_range"
```

What the bot does with these, automatically:
- Checks your "how did you hear about us" source (LinkedIn).
- Answers work-authorization Yes/No with Yes (sponsorship questions use
  your sponsorship answer, never confused with each other).
- Answers relocation Yes, in-office comfort Yes (or the text version).
- Fills compensation with the midpoint of the job's posted pay range
  (taken from the job file's `pay_range`).
- Leaves referrer/employee-name boxes blank (you have no referrer).

Anything else sensitive or unfamiliar parks as a question for you —
nothing is ever guessed.

## 2. Applying to one job

For each job Sree picks:

```bash
# a. Tailor the resume first with Tailor_Agent (always).
# b. Add a job file under jobs/, e.g. jobs/eliseai-junior-research-scientist.yaml:
```

```yaml
id: eliseai-junior-research-scientist
company: EliseAI
role: Junior Research Scientist
url: https://jobs.ashbyhq.com/EliseAI/54f71abf-9184-4b4a-8d41-43ebf1600eba
resume_pdf: ~/Downloads/EliseAI_Junior_Research_Scientist_Resume.pdf
ats: generic
pay_range: [150000, 230000]   # from the posting; bot fills the midpoint
```

```bash
# c. Test run: fills everything, STOPS at the review screen, never submits.
source .venv/bin/activate
python -m applybot apply --job jobs/eliseai-junior-research-scientist.yaml --park

# d. See what's waiting (if anything parked):
python -m applybot status --job-id eliseai-junior-research-scientist

# e. Answer open questions explicitly, then re-run:
python -m applybot answer --job-id eliseai-junior-research-scientist \
  --answers '{"field_id": "Your answer"}'
python -m applybot resume --job-id eliseai-junior-research-scientist \
  --job jobs/eliseai-junior-research-scientist.yaml --park

# f. Real run: when the form is clean it submits automatically and verifies.
python -m applybot apply --job jobs/eliseai-junior-research-scientist.yaml
```

Every run is logged with screenshots under `runs/<job-id>/<timestamp>/`.

## 4. Agent loop with explicit submit (Sree's call, 2026-09-26)

The generic agent loop (`agent` command) reasons over each page with the
LLM and acts. By default it always parks at review and never submits.
`--submit` is the explicit per-run opt-in that lets it click
submit/apply as its final action — the click is verified against
confirmation markers ("thank you for applying", etc.).

For sites with a login wall (e.g. Apple), sign in yourself first — the
bot never fills password fields:

```bash
source .venv/bin/activate
export GEMINI_API_KEY="..."   # runtime only; never stored or committed

# a. You sign in under a persistent profile (headed browser opens):
python -m applybot signin --job jobs/apple-aiml-data-scientist-evaluation.yaml \
  --profile ~/.applybot/apple
#    -> sign in with your own account in the window, then press Enter here.

# b. Dry run: fills everything, parks at review, never submits:
python -m applybot agent --job jobs/apple-aiml-data-scientist-evaluation.yaml \
  --headed --profile ~/.applybot/apple

# c. Real run: --submit lets the agent click submit as its final action:
python -m applybot agent --job jobs/apple-aiml-data-scientist-evaluation.yaml \
  --headed --profile ~/.applybot/apple --submit
```

Rules that always hold:
- Without `--submit`, the agent refuses every submit click (hard guard in
  code, not just the prompt).
- The `signin` profile dir keeps your login between runs on that machine.

## 5. Scaling to many jobs

```bash
# Add one YAML per job under jobs/, then loop:
source .venv/bin/activate
for j in jobs/*.yaml; do
  python -m applybot apply --job "$j" --park
done
# Review each with `status`, answer what's parked, then run without --park.
```

Rules that always hold:
- Only jobs Sree names get a YAML. No auto-discovery, no broad applying.
- Tailor the resume with Tailor_Agent before every application.
- Never invent experience, dates, metrics, skills, or credentials.
- The bot never answers a question it wasn't explicitly given — parked
  items wait for Sree's word via `answer`.
