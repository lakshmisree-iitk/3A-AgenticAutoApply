"""applybot — supervised job-application automation.

Pipeline: curated job -> Tailor_Agent resume -> applybot fills the form.

Safety rules (baked in, not optional):
  * Only fields classified SAFE are ever auto-filled, and only from profile.yaml.
  * Sensitive fields (work authorization, compensation, EEO/demographics,
    background, attestations) are NEVER auto-answered. They become
    "needs_input" items for the user to answer explicitly.
  * Unknown fields are NEVER guessed. They also become needs_input items.
  * The bot parks at the final review screen and takes a screenshot.
  * Submission happens only after an explicit `approve` step that records
    who approved and when. No approval -> no submit, ever.
"""

__version__ = "0.1.0"
