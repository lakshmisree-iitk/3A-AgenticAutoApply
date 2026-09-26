"""Playwright browser wrapper + blocker detection."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

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


def _proxy_config() -> dict | None:
    """Proxy for the browser, if the environment requires one.

    Playwright does not read proxy env vars itself. Honor APPLYBOT_PROXY
    first, then the standard HTTPS_PROXY/https_proxy. Returns None when no
    proxy is configured, which keeps local runs proxy-free.
    """
    raw = os.environ.get("APPLYBOT_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not raw:
        return None
    u = urlparse(raw)
    if not u.hostname:
        return None
    cfg: dict = {"server": f"{u.scheme or 'http'}://{u.hostname}:{u.port or 8080}"}
    if u.username:
        cfg["username"] = u.username
    if u.password:
        cfg["password"] = u.password
    return cfg


@contextmanager
def launch(headless: bool = True,
           user_data_dir: str | None = None) -> Iterator:
    """Yield a page; cleans up on exit.

    user_data_dir enables a persistent browser profile: sign-ins and
    cookies survive between runs on the same machine. Use it for sites
    with a login wall (the user signs in herself via the `signin`
    command; the bot never touches passwords).
    """
    from playwright.sync_api import sync_playwright

    ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
    with sync_playwright() as pw:
        if user_data_dir:
            Path(user_data_dir).mkdir(parents=True, exist_ok=True)
            context = pw.chromium.launch_persistent_context(
                user_data_dir,
                headless=headless,
                channel="chromium",
                proxy=_proxy_config(),
                viewport={"width": 1366, "height": 900},
                user_agent=ua,
            )
            page = context.new_page()
            try:
                yield page
            finally:
                context.close()
            return
        # channel="chromium" runs the full Chromium build headless
        # (the separate headless-shell binary may not be downloaded).
        browser = pw.chromium.launch(
            headless=headless, channel="chromium", proxy=_proxy_config()
        )
        context = browser.new_context(
            viewport={"width": 1366, "height": 900},
            user_agent=ua,
        )
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()
