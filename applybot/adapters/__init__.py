"""Adapter registry."""

from __future__ import annotations

from applybot.adapters.generic import GenericAdapter

ADAPTERS = {
    "generic": GenericAdapter(),
}


def pick(page, hint: str = "generic"):
    """Pick the adapter for a page: explicit hint wins, else first that detects."""
    if hint in ADAPTERS:
        return ADAPTERS[hint]
    for adapter in ADAPTERS.values():
        try:
            if adapter.detect(page):
                return adapter
        except Exception:  # noqa: BLE001
            continue
    return ADAPTERS["generic"]
