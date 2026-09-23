"""Smoke tests: classifier + generic adapter on a local fixture form."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applybot.sensitive import classify


def test_classifier():
    assert classify("First name") == ("safe", "first_name")
    assert classify("Email address") == ("safe", "email")
    assert classify("Phone number") == ("safe", "phone")
    assert classify("Will you now or in the future require sponsorship") == (
        "sensitive", "work_authorization")
    assert classify("Desired salary") == ("sensitive", "compensation")
    assert classify("I certify that the information provided is true") == (
        "sensitive", "attestation")
    assert classify("Blorptitude index") == ("unknown", None)
    # sensitive wins over safe-looking text
    assert classify("Email for visa sponsorship contact")[0] == "sensitive"
    print("classifier: OK")


def test_generic_adapter():
    from applybot import browser as B
    from applybot.adapters.generic import GenericAdapter
    from applybot.config import Profile

    profile = Profile(first_name="Test", email="test@example.com",
                      phone="555-0100")
    fixture = Path(__file__).parent / "fixtures" / "sample_form.html"
    resume = Path(__file__).parent / "fixtures" / "resume.txt"
    resume.write_text("fake resume", encoding="utf-8")

    with B.launch(headless=True) as page:
        page.goto(fixture.as_uri())
        result = GenericAdapter().fill(page, profile, str(resume), {})

    filled = " ".join(result.filled)
    assert "first_name" in filled, result.filled
    assert "email" in filled, result.filled
    assert "phone" in filled, result.filled
    assert result.resume_uploaded, "resume was not uploaded"

    kinds = {n.field_id: n.kind for n in result.needs}
    assert kinds.get("sponsorship") == "sensitive:work_authorization", kinds
    assert kinds.get("salary") == "sensitive:compensation", kinds
    assert kinds.get("certify") == "sensitive:attestation", kinds
    assert kinds.get("blorptitude") == "unknown", kinds
    print(f"adapter: OK (filled={len(result.filled)}, needs={len(result.needs)})")


if __name__ == "__main__":
    test_classifier()
    test_generic_adapter()
    print("ALL TESTS PASSED")
