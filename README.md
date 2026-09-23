# 3A-AgenticAutoApply

Supervised job-application automation. It does what a careful personal
assistant would do: fill what's safe, stop at what's sensitive, park at
review, and submit only on your explicit word.

## Pipeline

```
curated job -> Tailor_Agent resume -> applybot apply -> you answer -> approve -> submit
```

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
python -m applybot init        # creates profile.yaml (git-ignored, never commit it)
```

Fill in `profile.yaml` with contact details only. Sensitive answers
(visa, compensation, EEO, attestations) are never stored there.

## Usage

```bash
# 1. Fill the form and upload the tailored resume.
#    Sensitive/unknown questions park as needs_input (never guessed).
#    Once the form is clean, it submits automatically and verifies.
python -m applybot apply --job jobs/amazon-10518364.yaml

# 2. See what's waiting on you
python -m applybot status --job-id amazon-10518364

# 3. Answer the open questions explicitly, then re-run (auto-submits when clean)
python -m applybot answer --job-id amazon-10518364 --answers '{"sponsorship": "Yes"}'
python -m applybot resume --job-id amazon-10518364 --job jobs/amazon-10518364.yaml

# Stop at the review screen instead of auto-submitting:
python -m applybot apply --job jobs/x.yaml --park
```

## Safety rules (enforced in code)

- Only fields classified **safe** are auto-filled, from `profile.yaml` only.
- **Sensitive** fields (work authorization, citizenship, compensation,
  EEO/demographics, background, attestations) are never auto-answered.
- **Unknown** fields are never guessed.
- Both become `needs_input` items for you to answer explicitly; submission
  waits until they are answered.
- Submission is verified against confirmation markers and screenshotted;
  every run is logged under `runs/<job>/<timestamp>/` for audit.
- Login walls and CAPTCHAs stop the run as `blocked`.
- State lives in `state/<job>.json`, outside the model.

## Never commit

`profile.yaml`, `state/`, `runs/` — personal data. They are git-ignored.
