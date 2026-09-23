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
    # bare Name / Location labels (Ashby-style single-field forms)
    assert classify("Name") == ("safe", "full_name")
    assert classify("Name*") == ("safe", "full_name")
    assert classify("Location") == ("safe", "location")
    assert classify("Location*") == ("safe", "location")
    # someone else's name or an org's name must NOT take the applicant's name
    assert classify("If you were referred by an employee, list their name")[0] == "unknown"
    assert classify("Recruiter name")[0] == "unknown"
    assert classify("Current company name")[0] == "unknown"
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


def test_ashby_style_form():
    """EliseAI-shaped form: single Name field, Location, and a referrer box
    that must NOT receive the applicant's name."""
    from applybot import browser as B
    from applybot.adapters.generic import GenericAdapter
    from applybot.config import Profile

    profile = Profile(full_name="Lakshmisree Iyengar",
                      email="lakshmisree.iitk@gmail.com",
                      phone="470-923-7530",
                      location="Harrison, New Jersey, USA")
    fixture = Path(__file__).parent / "fixtures" / "ashby_form.html"
    resume = Path(__file__).parent / "fixtures" / "resume.txt"
    resume.write_text("fake resume", encoding="utf-8")

    with B.launch(headless=True) as page:
        page.goto(fixture.as_uri())
        result = GenericAdapter().fill(page, profile, str(resume), {})

        filled = " ".join(result.filled)
        assert "full_name" in filled, result.filled
        assert "email" in filled, result.filled
        assert "phone" in filled, result.filled
        assert "location" in filled, result.filled
        assert result.resume_uploaded, "resume was not uploaded"

        # the referrer-name box must stay empty: it asks for someone else
        assert page.locator("#ref").input_value() == "", \
            "referrer box wrongly filled with applicant name"

        kinds = {n.field_id: n.kind for n in result.needs}

    assert kinds.get("work_auth") == "sensitive:work_authorization", kinds
    assert kinds.get("sponsorship") == "sensitive:work_authorization", kinds
    assert kinds.get("compensation") == "sensitive:compensation", kinds
    assert kinds.get("office_comfort") == "unknown", kinds
    assert kinds.get("source") == "unknown", kinds
    assert kinds.get("referrer_name") == "unknown", kinds
    assert kinds.get("linkedin") == "unknown", kinds  # no value in profile
    print(f"ashby-style: OK (filled={len(result.filled)}, needs={len(result.needs)})")


if __name__ == "__main__":
    test_classifier()
    test_generic_adapter()
    test_ashby_style_form()
    print("ALL TESTS PASSED")
