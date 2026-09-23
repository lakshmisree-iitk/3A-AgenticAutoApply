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
  return [
    el.getAttribute('aria-label') || '',
    el.getAttribute('placeholder') || '',
    label ? label.innerText : '',
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

    def fill(self, page, profile, resume_pdf: str, answered: dict) -> FillResult:
        result = FillResult()
        seen_radio_groups: set[str] = set()

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

                # Radios / checkboxes: never auto-fill.
                if ctype in ("radio", "checkbox"):
                    group = el.get_attribute("name") or field_id
                    if group in seen_radio_groups:
                        continue
                    seen_radio_groups.add(group)
                    verdict, detail = classify(label)
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

    def _group_options(self, page, el) -> list[str]:
        name = el.get_attribute("name") or ""
        if not name:
            return []
        return page.evaluate(
            """(n) => Array.from(document.querySelectorAll(`input[type=radio][name="${n}"]`))
                 .map(r => (r.labels[0]?.innerText || r.value || '').trim()).filter(Boolean)""",
            name,
        )
