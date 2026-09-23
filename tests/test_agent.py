"""Agent loop tests: run_agent with a mock reasoner (no Gemini calls)."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applybot.agent import run_agent

FIXTURE = Path(__file__).parent / "fixtures" / "ashby_realshape.html"

PROFILE_YAML = """\
first_name: "Test"
last_name: "Person"
full_name: "Test Person"
email: "test@example.com"
phone: "470-923-7530"
location: "Harrison, New Jersey, USA"
linkedin: "https://www.linkedin.com/in/test-person"
"""

JOB_YAML = """\
id: test-agent-job
company: TestCo
role: Test Role
url: {url}
resume_pdf: {resume}
ats: generic
"""


def _setup(tmp: str) -> str:
    Path(tmp, "profile.yaml").write_text(PROFILE_YAML, encoding="utf-8")
    Path(tmp, "standing.yaml").write_text("{}\n", encoding="utf-8")
    resume = Path(tmp, "dummy.pdf")
    resume.write_bytes(b"%PDF-1.4 dummy")
    job = Path(tmp, "job.yaml")
    job.write_text(JOB_YAML.format(url=FIXTURE.resolve().as_uri(),
                                   resume=resume), encoding="utf-8")
    return str(job)


def _run(job_yaml: str, reason_fn, tmp: str):
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        return run_agent(job_yaml, headless=True, reason_fn=reason_fn,
                         max_steps=4)
    finally:
        os.chdir(cwd)


def _find_question(snapshot: dict, needle: str) -> dict:
    return next(q for q in snapshot["questions"]
                if needle in q["question"].lower())


def test_agent_guards_and_parks():
    """Safe fill executes with read-back; sensitive fill without a standing
    answer is downgraded to park even though the mock said fill."""
    calls = []

    def mock(snapshot, profile, standing, job, past_answers, step):
        calls.append(step)
        spons = _find_question(snapshot, "sponsorship")
        comp = _find_question(snapshot, "compensation")
        loc = _find_question(snapshot, "location")
        return {
            "thinking": "test",
            "fields": [
                # safe field Tier 1 left empty (no value needed): fills fine
                {"control_id": loc["controls"][0]["control_id"],
                 "decision": "fill", "value": "Harrison, NJ",
                 "source": "profile", "reason": "safe contact field"},
                # sensitive field, source is NOT standing/past_answer:
                # the guard must override to park
                {"control_id": spons["controls"][0]["control_id"],
                 "decision": "fill", "value": "No",
                 "source": "profile", "reason": "mock tries to sneak it"},
                {"control_id": comp["controls"][0]["control_id"],
                 "decision": "park", "value": None,
                 "source": None, "reason": "needs her number"},
            ],
            "clicks": [], "upload_resume": False,
            "blocked": None, "page_done": True, "notes": "",
        }

    tmp = tempfile.mkdtemp(prefix="agent-test-")
    # profile WITHOUT location so Tier 1 leaves it for the mock
    Path(tmp, "profile.yaml").write_text(
        PROFILE_YAML.replace('location: "Harrison, New Jersey, USA"\n', ""),
        encoding="utf-8")
    Path(tmp, "standing.yaml").write_text("{}\n", encoding="utf-8")
    resume = Path(tmp, "dummy.pdf")
    resume.write_bytes(b"%PDF-1.4 dummy")
    job_yaml = str(Path(tmp, "job.yaml"))
    Path(job_yaml).write_text(
        JOB_YAML.format(url=FIXTURE.resolve().as_uri(), resume=resume),
        encoding="utf-8")

    result = _run(job_yaml, mock, tmp)
    log = Path(tmp) / "runs" / "test-agent-job"
    agent_log = next(log.rglob("agent.log")).read_text(encoding="utf-8")

    assert "GUARD override" in agent_log, "sensitive fill was not overridden"
    assert result["state"] == "needs_input", result["state"]
    labels = " ".join(n["label"] for n in result["needs"]).lower()
    assert "sponsorship" in labels and "compensation" in labels, labels
    print("agent guards+parks: OK")


def test_agent_refuses_submit_click():
    """A click on Submit Application is refused by the executor."""

    def mock(snapshot, profile, standing, job, past_answers, step):
        return {"thinking": "test", "fields": [], "clicks": [
                    {"control_id": "", "label": "Submit Application",
                     "purpose": "malicious test"}],
                "upload_resume": False, "blocked": None,
                "page_done": False, "notes": ""}

    tmp = tempfile.mkdtemp(prefix="agent-test-")
    result = _run(_setup(tmp), mock, tmp)
    log = Path(tmp) / "runs" / "test-agent-job"
    agent_log = next(log.rglob("agent.log")).read_text(encoding="utf-8")
    assert "refused click on 'Submit Application'" in agent_log
    assert "SUBMITTED" not in agent_log
    assert result["state"] in ("needs_input", "ready_for_review"), result["state"]
    print("agent refuses submit: OK")


def test_agent_blocked():
    """A login wall / blocked report parks the job."""

    def mock(snapshot, profile, standing, job, past_answers, step):
        return {"thinking": "test", "fields": [], "clicks": [],
                "upload_resume": False,
                "blocked": "login wall, no form visible",
                "page_done": False, "notes": ""}

    tmp = tempfile.mkdtemp(prefix="agent-test-")
    result = _run(_setup(tmp), mock, tmp)
    assert result["state"] == "needs_input", result["state"]
    assert any(n["field_id"] == "blocked" for n in result["needs"])
    print("agent blocked: OK")


if __name__ == "__main__":
    test_agent_guards_and_parks()
    test_agent_refuses_submit_click()
    test_agent_blocked()
    print("AGENT TESTS PASSED")
