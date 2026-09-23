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
# 1. Fill the form, upload the tailored resume, park at review
python -m applybot apply --job jobs/amazon-10518364.yaml

# 2. See what's waiting on you
python -m applybot status --job-id amazon-10518364

# 3. Answer the open questions explicitly, then re-run the fill
python -m applybot answer --job-id amazon-10518364 --answers '{"sponsorship": "Yes"}'
python -m applybot resume --job-id amazon-10518364 --job jobs/amazon-10518364.yaml

# 4. Approve, then submit (submit refuses without approval)
python -m applybot approve --job-id amazon-10518364 --by "Sree"
python -m applybot submit --job-id amazon-10518364 --job jobs/amazon-10518364.yaml
```

## Safety rules (enforced in code)

- Only fields classified **safe** are auto-filled, from `profile.yaml` only.
- **Sensitive** fields (work authorization, citizenship, compensation,
  EEO/demographics, background, attestations) are never auto-answered.
- **Unknown** fields are never guessed.
- Both become `needs_input` items for you to answer explicitly.
- Login walls and CAPTCHAs stop the run as `blocked`.
- Every run logs to `runs/<job>/<timestamp>/` with screenshots.
- State lives in `state/<job>.json`, outside the model.

## Never commit

`profile.yaml`, `state/`, `runs/` — personal data. They are git-ignored.
