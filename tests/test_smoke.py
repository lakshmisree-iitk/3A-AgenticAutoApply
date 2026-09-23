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
    """EliseAI-shaped form: single Name field, Location, standing answers,
    and a referrer box that is left blank (never parked, never filled)."""
    from applybot import browser as B
    from applybot.adapters.generic import GenericAdapter
    from applybot.config import Profile

    profile = Profile(full_name="Lakshmisree Iyengar",
                      email="lakshmisreeiyengar@gmail.com",
                      phone="470-923-7530",
                      location="Harrison, New Jersey, USA",
                      linkedin="https://www.linkedin.com/in/lakshmisree-iyengar")
    standing = {
        "how_heard": "LinkedIn",
        "us_work_authorized": "Yes",
        "requires_sponsorship": "Yes",
        "open_to_relocate": "Yes",
        "work_arrangement": "I am comfortable with in-office, hybrid, or remote work",
        "in_office_ok": "Yes",
        "compensation_strategy": "midpoint_of_posted_range",
    }
    fixture = Path(__file__).parent / "fixtures" / "ashby_form.html"
    resume = Path(__file__).parent / "fixtures" / "resume.txt"
    resume.write_text("fake resume", encoding="utf-8")

    with B.launch(headless=True) as page:
        page.goto(fixture.as_uri())
        result = GenericAdapter().fill(page, profile, str(resume), {},
                                       standing, (150000, 230000))

        filled = " ".join(result.filled)
        for key in ("full_name", "email", "phone", "location", "linkedin"):
            assert key in filled, result.filled
        assert "[standing answer]" in filled, result.filled
        assert result.resume_uploaded, "resume was not uploaded"

        # the referrer-name box must stay empty: it asks for someone else
        assert page.locator("#ref").input_value() == "", \
            "referrer box wrongly filled with applicant name"
        # compensation got the midpoint of the posted range
        assert page.locator("#comp").input_value() == "$190,000", \
            page.locator("#comp").input_value()

        kinds = {n.field_id: n.kind for n in result.needs}

    # referrer box is skipped silently; standing answers resolved the rest
    assert "referrer_name" not in kinds, kinds
    assert "sponsorship" not in kinds, kinds
    assert "work_auth" not in kinds, kinds
    assert "compensation" not in kinds, kinds
    assert "office_comfort" not in kinds, kinds
    assert "source" not in kinds, kinds
    assert not kinds, kinds  # fully clean: nothing left to ask
    print(f"ashby-style: OK (filled={len(result.filled)}, needs={len(result.needs)})")


if __name__ == "__main__":
    test_classifier()
    test_generic_adapter()
    test_ashby_style_form()
    print("ALL TESTS PASSED")
