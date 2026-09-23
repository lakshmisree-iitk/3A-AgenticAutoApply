"""Playwright browser wrapper + blocker detection."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

LOGIN_MARKERS = [
    "log in to apply",
    "sign in to apply",
    "create an account to apply",
    "login to apply",
]

CAPTCHA_MARKERS = ["recaptcha", "hcaptcha", "captcha", "turnstile"]


def detect_blockers(page) -> str | None:
    """Return a blocker reason, or None if the page looks fillable."""
    try:
        text = (page.content() or "").lower()
    except Exception:  # noqa: BLE001
        return "could not read page content"
    if any(m in text for m in LOGIN_MARKERS):
        return "login wall: site requires sign-in before applying"
    try:
        for frame in page.frames:
            url = (frame.url or "").lower()
            if any(m in url for m in CAPTCHA_MARKERS):
                return "CAPTCHA detected on the application page"
    except Exception:  # noqa: BLE001
        pass
    return None


@contextmanager
def launch(headless: bool = True) -> Iterator:
    """Yield a (playwright, browser, page) triple; cleans up on exit."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        # channel="chromium" runs the full Chromium build headless
        # (the separate headless-shell binary may not be downloaded).
        browser = pw.chromium.launch(headless=headless, channel="chromium")
        context = browser.new_context(
            viewport={"width": 1366, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()
