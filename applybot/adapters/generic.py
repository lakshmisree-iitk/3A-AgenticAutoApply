"""Generic heuristic adapter: label-matched filling for simple ATS forms.

Fills only text-like inputs, textareas and selects that classify SAFE.
Radio groups and checkboxes are never auto-filled; they become needs_input
items unless the user explicitly answered them. Never submits.
"""

from __future__ import annotations

import re

from applybot.adapters.base import FillResult, Need
from applybot.perceive import (
    GROUP_CTX_JS,
    JS_LABEL_CLICK,
    LABEL_JS,
    OPTIONS_JS,
    OPT_TEXT_JS,
    VISIBLE_JS,
    _group_options as _perceive_group_options,
)
from applybot.sensitive import classify


def _dismiss_cookie_banner(page) -> None:
    """Click a reject/necessary-only cookie button if one is showing."""
    try:
        btn = page.get_by_role(
            "button", name=re.compile(r"necessary only|reject( all)?|decline", re.I)
        )
        if btn.count():
            btn.first.click(timeout=3000)
            page.wait_for_timeout(500)
    except Exception:  # noqa: BLE001 - banner may not exist; never fatal
        pass


def _reveal_form(page) -> None:
    """Make sure the actual application form is visible before filling.

    Ashby-style pages land on an Overview tab; the form lives behind an
    'Application' tab (or an 'Apply for this Job' button). Without this
    step the adapter sees zero fields and parks on a page that was never
    the form. Returns early when form controls already exist.
    """
    try:
        if page.query_selector("form input, form textarea, form select"):
            return
        _dismiss_cookie_banner(page)
        clicked = False
        for role, pattern in [
            ("tab", r"^application$"),
            ("link", r"^application$"),
            ("button", r"apply for this job"),
            ("link", r"apply for this job"),
        ]:
            try:
                el = page.get_by_role(role, name=re.compile(pattern, re.I))
                if el.count():
                    el.first.click(timeout=5000)
                    clicked = True
                    break
            except Exception:  # noqa: BLE001 - try the next shape
                continue
        if clicked:
            page.wait_for_selector(
                "form input, form textarea, form select", timeout=15000
            )
    except Exception:  # noqa: BLE001 - form genuinely absent; fill reports it
        pass


# "If you selected 'Other' above, please specify..." — only relevant when
# the Other option is actually checked.
OTHER_COND_RE = re.compile(r'if you selected.*other', re.I)

# A field is "someone else's name / org name" (left blank, never parked)
# only when it ASKS FOR a name/identity of another party or org — not
# merely because the question mentions a company. ("EliseAI is an
# in-office company. Are you comfortable...?" is about HER comfort, not
# a company name.)
_OTHER_PARTY_RE = re.compile(
    r"referr|recruit|employee|colleague|friend|manager|supervisor", re.I)
_ORG_RE = re.compile(
    r"compan|employer|universit|school|college|organi[sz]ation", re.I)
_NAME_REQUEST_RE = re.compile(
    r"\bname\b|\bwho\b|specify|list their|contacted by", re.I)


def _is_referrer_field(label: str) -> bool:
    """True when the field asks for another person/org's name (referrer,
    recruiter, employee, company name, ...). Such fields stay blank."""
    low = (label or "").lower()
    if not _NAME_REQUEST_RE.search(low):
        return False
    return bool(_OTHER_PARTY_RE.search(low) or _ORG_RE.search(low))


class GenericAdapter:
    name = "generic"

    def detect(self, page) -> bool:
        return True  # fallback adapter

    def prepare(self, page) -> None:
        # Temporary scaffold: Ashby-style pages open on an Overview tab;
        # click through to the real application form (and clear any cookie
        # banner) first. Phase 2 replaces this with the generic loop, where
        # the reasoner clicks tabs / Next buttons from the snapshot.
        _reveal_form(page)

    def fill(self, page, profile, resume_pdf: str, answered: dict,
             standing: dict | None = None,
             pay_range: tuple[int, int] | None = None) -> FillResult:
        result = FillResult()
        seen_radio_groups: set[str] = set()
        # Normalized labels of conditional "Other, please specify" fields
        # already skipped: Ashby can render the same conditional field
        # twice (visible + hidden template); skip every copy.
        other_skipped: set[str] = set()
        standing = standing or {}

        self.prepare(page)

        controls = page.query_selector_all(
            "input, textarea, select"
        )
        for i, el in enumerate(controls):
            try:
                if not el.evaluate(VISIBLE_JS):
                    continue
                ctype = (el.get_attribute("type") or el.evaluate("e => e.tagName")).lower()
                label = el.evaluate(LABEL_JS)
                if not label:
                    continue

                # Conditional "if you selected Other..." box: skip unless the
                # Other option is actually checked. Every copy is skipped
                # (Ashby sometimes renders the conditional field twice).
                if ctype not in ("radio", "checkbox") and OTHER_COND_RE.search(label):
                    norm_label = re.sub(r"\s+", " ", label).strip().lower()
                    if norm_label in other_skipped or not self._other_option_checked(page):
                        other_skipped.add(norm_label)
                        result.notes.append(
                            f"skipped (Other not selected): {label[:60]}")
                        continue

                # Resume upload
                if ctype == "file":
                    el.set_input_files(resume_pdf)
                    result.resume_uploaded = True
                    result.filled.append(f"{label} [resume uploaded]")
                    continue

                field_id = (
                    el.get_attribute("name")
                    or el.get_attribute("id")
                    or f"field_{i}"
                )

                # Explicit user answers always win (she supplied them herself).
                if field_id in answered:
                    self._apply_answer(el, ctype, answered[field_id])
                    result.filled.append(f"{label} [user answer]")
                    continue

                # Radios / checkboxes: never auto-fill, except from her
                # explicit standing answers (matched conservatively).
                if ctype in ("radio", "checkbox"):
                    name = el.get_attribute("name") or ""
                    ctx = el.evaluate(GROUP_CTX_JS) or ""
                    group = name if name else (f"ctx:{ctx[:80]}" if ctx else field_id)
                    if group in seen_radio_groups:
                        continue
                    seen_radio_groups.add(group)
                    verdict, detail = classify(label)
                    if self._apply_standing_group(
                            page, el, ctype, label, ctx, verdict, detail,
                            standing, result):
                        continue
                    kind = f"sensitive:{detail}" if verdict == "sensitive" else "unknown"
                    result.needs.append(
                        Need(
                            field_id=group,
                            label=label,
                            kind=kind,
                            control=ctype,
                            options=_perceive_group_options(page, el, ctype, ctx),
                        )
                    )
                    continue

                verdict, detail = classify(label)
                if verdict == "safe":
                    value = profile.value(detail)
                    if value:
                        self._apply_answer(el, ctype, value)
                        result.filled.append(f"{label} -> {detail}")
                    else:
                        result.needs.append(
                            Need(field_id=field_id, label=label,
                                 kind="unknown", control=ctype,
                                 options=el.evaluate(OPTIONS_JS))
                        )
                else:
                    # Her explicit standing answers win over heuristics.
                    stood = self._standing_text(
                        label, ctype, verdict, detail, standing, pay_range)
                    if stood:
                        self._apply_answer(el, ctype, stood)
                        result.filled.append(f"{label} -> [standing answer]")
                        continue
                    # Someone else's name / org name: leave blank, never park.
                    # Narrow check: only fields that actually ASK FOR the
                    # name (referrer, recruiter, company name...), not
                    # ordinary questions that merely mention a company.
                    if verdict == "unknown" and _is_referrer_field(label):
                        result.notes.append(f"left blank (no referrer): {label}")
                        continue
                    kind = f"sensitive:{detail}" if verdict == "sensitive" else "unknown"
                    result.needs.append(
                        Need(field_id=field_id, label=label,
                             kind=kind, control=ctype,
                             options=el.evaluate(OPTIONS_JS))
                    )
            except Exception as exc:  # noqa: BLE001 - one bad field never kills the run
                result.notes.append(f"skipped a control ({exc})")

        # Ashby-style Yes/No pill toggles rendered as <button>s, not inputs.
        self._fill_button_toggles(page, standing, result)

        return result

    def _apply_answer(self, el, ctype: str, value: str) -> None:
        tag = el.evaluate("e => e.tagName").lower()
        if tag == "select":
            el.select_option(label=value)
        elif ctype == "checkbox":
            want = str(value).strip().lower() in ("yes", "true", "1", "checked")
            try:
                if el.is_checked() != want:
                    el.check() if want else el.uncheck()
            except Exception:  # noqa: BLE001 - hidden input: click via label
                el.evaluate(
                    "(el, v) => { if (el.checked !== v) {"
                    " const t = (el.labels && el.labels[0]) || el; t.click(); } }",
                    want,
                )
        elif ctype == "radio":
            # value matches the option label/value within its group
            el.evaluate(
                """(el, v) => {
                  const group = document.querySelectorAll(`input[type=radio][name="${el.name}"]`);
                  for (const r of group) {
                    const t = (r.value + ' ' + (r.labels[0]?.innerText || '')).toLowerCase();
                    if (t.includes(v.toLowerCase())) { r.click(); return; }
                  }
                }""",
                value,
            )
        else:
            el.fill(str(value))

    # -- standing answers (the user's explicit, reusable answers) ---------
    def _apply_standing_group(self, page, el, ctype: str, label: str, ctx: str,
                              verdict: str, detail: str | None,
                              standing: dict, result) -> bool:
        """Apply a standing answer to a whole radio/checkbox group.

        Returns True when the group was resolved. Conservative: only the
        exact question shapes she approved. Anything else stays a need.
        """
        if not standing:
            return False
        low = f"{label} {ctx}".lower()
        # Ashby-style controls often carry only the option text; classify
        # with the question text for the branch conditions below.
        verdict, detail = classify(low)
        name = el.get_attribute("name") or ""
        if name:
            boxes = page.query_selector_all(f'input[type={ctype}][name="{name}"]')
        elif ctx:
            # Ashby-style: options share no name; take same-type controls
            # from the same question container.
            key = ctx[:80]
            boxes = [b for b in page.query_selector_all(f'input[type={ctype}]')
                     if (b.evaluate(GROUP_CTX_JS) or "")[:80] == key]
        else:
            boxes = [el]

        def click_match(want: str) -> bool:
            want = want.strip().lower()
            if not want:
                return False
            for b in boxes:
                t = (b.evaluate(OPT_TEXT_JS) or "").lower()
                if want in t:
                    try:
                        try:
                            b.check() if ctype == "checkbox" else b.click()
                        except Exception:  # noqa: BLE001 - hidden input
                            b.evaluate(JS_LABEL_CLICK)
                    except Exception:  # noqa: BLE001
                        continue
                    return True
            return False

        # "How did you hear about us?" -> check her chosen source.
        if ctype == "checkbox" and "how did you hear" in low:
            want = standing.get("how_heard", "")
            if want and click_match(want):
                result.filled.append(f"{label} -> {want} [standing answer]")
                return True
            return False

        if ctype == "radio":
            # Work authorization (NOT sponsorship) -> her answer.
            if (verdict, detail) == ("sensitive", "work_authorization") \
                    and "sponsor" not in low:
                want = standing.get("us_work_authorized", "")
                if want and click_match(want):
                    result.filled.append(f"{label} -> {want} [standing answer]")
                    return True
                return False
            # Willing to relocate?
            if "relocat" in low:
                want = standing.get("open_to_relocate", "")
                if want and click_match(want):
                    result.filled.append(f"{label} -> {want} [standing answer]")
                    return True
                return False
            # Comfortable with in-office?
            if any(k in low for k in ("in-office", "in office", "on-site",
                                      "onsite")) and \
                    any(k in low for k in ("comfort", "okay", "willing",
                                           "open to")):
                want = standing.get("in_office_ok", "")
                if want and click_match(want):
                    result.filled.append(f"{label} -> {want} [standing answer]")
                    return True
                return False
        return False

    def _other_option_checked(self, page) -> bool:
        """True when an 'Other' checkbox option is currently checked."""
        try:
            for b in page.query_selector_all("input[type=checkbox]"):
                t = (b.evaluate(OPT_TEXT_JS) or "").lower()
                if "other" in t:
                    try:
                        if b.is_checked():
                            return True
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass
        return False

    def _fill_button_toggles(self, page, standing: dict, result) -> None:
        """Ashby renders some Yes/No questions as <button> pills, not inputs.

        Only the exact question shapes she approved are answered; anything
        else is left alone (never parked from here — the input pass owns
        parking).
        """
        if not standing:
            return
        seen: set[str] = set()
        for b in page.query_selector_all("button"):
            try:
                if not b.evaluate(VISIBLE_JS):
                    continue
                text = (b.inner_text() or "").strip().lower()
                if text not in ("yes", "no"):
                    continue
                ctx = b.evaluate(GROUP_CTX_JS) or ""
                key = ctx[:80]
                if key in seen:
                    continue
                seen.add(key)
                low = ctx.lower()
                verdict, detail = classify(low)
                want = None
                if verdict == "sensitive" and detail == "work_authorization" \
                        and "sponsor" not in low:
                    want = (standing.get("us_work_authorized") or "").strip().lower()
                elif "relocat" in low:
                    want = (standing.get("open_to_relocate") or "").strip().lower()
                if want in ("yes", "no"):
                    clicked = b.evaluate(
                        """(el, want) => {
                          let n = el.parentElement;
                          for (let i = 0; i < 5 && n; i++, n = n.parentElement) {
                            const btns = Array.from(n.querySelectorAll('button'));
                            const names = btns.map(x => (x.innerText || '').trim().toLowerCase());
                            if (names.includes('yes') && names.includes('no')) {
                              const t = btns[names.indexOf(want)];
                              if (t) { t.click(); return true; }
                              return false;
                            }
                          }
                          return false;
                        }""",
                        want,
                    )
                    if clicked:
                        result.filled.append(f"{ctx[:60]}... -> {want} [standing answer]")
            except Exception:  # noqa: BLE001 - one bad toggle never kills the run
                continue

    def _standing_text(self, label: str, ctype: str, verdict: str,
                       detail: str | None, standing: dict,
                       pay_range: tuple[int, int] | None) -> str | None:
        """Standing answer for an open-text question, or None to leave a need."""
        if not standing:
            return None
        low = label.lower()
        get = lambda k: (standing.get(k) or "").strip() or None  # noqa: E731

        # Sponsorship: only from her explicit standing answer (may be empty).
        if verdict == "sensitive" and detail == "work_authorization" \
                and "sponsor" in low:
            return get("requires_sponsorship")

        # Compensation: midpoint of the job's posted range.
        if verdict == "sensitive" and detail == "compensation":
            if get("compensation_strategy") == "midpoint_of_posted_range" \
                    and pay_range:
                lo, hi = int(pay_range[0]), int(pay_range[1])
                return f"${(lo + hi) // 2:,}"
            return None

        # In-office / arrangement comfort (open text).
        if any(k in low for k in ("in-office", "in office", "on-site", "onsite",
                                  "hybrid", "work arrangement")) and \
                any(k in low for k in ("comfort", "open to", "okay", "willing")):
            return get("work_arrangement")

        # Relocation (open text).
        if "relocat" in low:
            return get("open_to_relocate")
        return None
