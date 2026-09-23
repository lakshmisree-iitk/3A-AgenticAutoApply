"""Generic heuristic adapter: label-matched filling for simple ATS forms.

Fills only text-like inputs, textareas and selects that classify SAFE.
Radio groups and checkboxes are never auto-filled; they become needs_input
items unless the user explicitly answered them. Never submits.
"""

from __future__ import annotations

from applybot.adapters.base import FillResult, Need
from applybot.sensitive import classify

LABEL_JS = """(el) => {
  const label = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
  const wrap = el.closest('label');
  const fieldset = el.closest('fieldset');
  const legend = fieldset ? fieldset.querySelector('legend') : null;
  return [
    el.getAttribute('aria-label') || '',
    el.getAttribute('placeholder') || '',
    label ? label.innerText : '',
    legend ? legend.innerText : '',
    wrap ? wrap.innerText : '',
    el.getAttribute('name') || '',
    el.id || ''
  ].join(' ').replace(/\\s+/g, ' ').trim();
}"""

OPTIONS_JS = """(el) => {
  if (el.tagName === 'SELECT')
    return Array.from(el.options).map(o => o.text.trim()).filter(Boolean);
  return [];
}"""

VISIBLE_JS = """(el) => {
  const r = el.getBoundingClientRect();
  const s = window.getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
}"""


class GenericAdapter:
    name = "generic"

    def detect(self, page) -> bool:
        return True  # fallback adapter

    def fill(self, page, profile, resume_pdf: str, answered: dict,
             standing: dict | None = None,
             pay_range: tuple[int, int] | None = None) -> FillResult:
        result = FillResult()
        seen_radio_groups: set[str] = set()
        standing = standing or {}

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
                    group = el.get_attribute("name") or field_id
                    if group in seen_radio_groups:
                        continue
                    seen_radio_groups.add(group)
                    verdict, detail = classify(label)
                    if self._apply_standing_group(
                            page, el, ctype, label, verdict, detail,
                            standing, result):
                        continue
                    kind = f"sensitive:{detail}" if verdict == "sensitive" else "unknown"
                    result.needs.append(
                        Need(
                            field_id=group,
                            label=label,
                            kind=kind,
                            control=ctype,
                            options=self._group_options(page, el),
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
                    # Standing answers for open-text questions (explicit only).
                    stood = self._standing_text(
                        label, ctype, verdict, detail, standing, pay_range)
                    if stood:
                        self._apply_answer(el, ctype, stood)
                        result.filled.append(f"{label} -> [standing answer]")
                        continue
                    kind = f"sensitive:{detail}" if verdict == "sensitive" else "unknown"
                    result.needs.append(
                        Need(field_id=field_id, label=label,
                             kind=kind, control=ctype,
                             options=el.evaluate(OPTIONS_JS))
                    )
            except Exception as exc:  # noqa: BLE001 - one bad field never kills the run
                result.notes.append(f"skipped a control ({exc})")

        return result

    def _apply_answer(self, el, ctype: str, value: str) -> None:
        tag = el.evaluate("e => e.tagName").lower()
        if tag == "select":
            el.select_option(label=value)
        elif ctype == "checkbox":
            want = str(value).strip().lower() in ("yes", "true", "1", "checked")
            if el.is_checked() != want:
                el.check() if want else el.uncheck()
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
    OPT_TEXT_JS = """(b) => {
      const w = b.closest('label');
      return ((w ? w.innerText : '') + ' ' + (b.value || '') + ' '
              + (b.getAttribute('aria-label') || '')).replace(/\\s+/g, ' ').trim();
    }"""

    def _apply_standing_group(self, page, el, ctype: str, label: str,
                              verdict: str, detail: str | None,
                              standing: dict, result) -> bool:
        """Apply a standing answer to a whole radio/checkbox group.

        Returns True when the group was resolved. Conservative: only the
        exact question shapes she approved. Anything else stays a need.
        """
        if not standing:
            return False
        low = label.lower()
        name = el.get_attribute("name") or ""
        boxes = (page.query_selector_all(f'input[type={ctype}][name="{name}"]')
                 if name else [el])

        def click_match(want: str) -> bool:
            want = want.strip().lower()
            if not want:
                return False
            for b in boxes:
                t = (b.evaluate(self.OPT_TEXT_JS) or "").lower()
                if want in t:
                    try:
                        b.check() if ctype == "checkbox" else b.click()
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

    def _group_options(self, page, el) -> list[str]:
        name = el.get_attribute("name") or ""
        if not name:
            return []
        return page.evaluate(
            """(n) => Array.from(document.querySelectorAll(`input[type=radio][name="${n}"]`))
                 .map(r => (r.labels[0]?.innerText || r.value || '').trim()).filter(Boolean)""",
            name,
        )
