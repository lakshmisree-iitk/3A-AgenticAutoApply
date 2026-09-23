"""Perceive layer (Phase 1): deterministic form snapshotting.

snapshot_form(page) reads whatever page is in front of it and returns a
question inventory — no LLM, no ATS-specific code. This is the agent's
eyes; the reason layer (Phase 2) decides what to do with each question.

Shared DOM extraction primitives live here; adapters import them instead
of duplicating the JS.
"""

from __future__ import annotations

# --- shared DOM extraction primitives ---------------------------------

LABEL_JS = """(el) => {
  const label = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
  const wrap = el.closest('label');
  const fieldset = el.closest('fieldset');
  const legend = fieldset ? fieldset.querySelector('legend') : null;
  const lb = el.getAttribute('aria-labelledby') || '';
  const lbText = lb.split(/\\s+/).map(id => {
    const n = id && document.getElementById(id);
    return n ? n.innerText : '';
  }).join(' ');
  return [
    el.getAttribute('aria-label') || '',
    lbText,
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

OPT_TEXT_JS = """(b) => {
  const w = b.closest('label');
  const lab = (b.labels && b.labels[0]) ? b.labels[0].innerText : '';
  return ((w ? w.innerText : '') + ' ' + lab + ' ' + (b.value || '') + ' '
          + (b.getAttribute('aria-label') || '')).replace(/\\s+/g, ' ').trim();
}"""

VISIBLE_JS = """(el) => {
  const vis = (n) => {
    const r = n.getBoundingClientRect();
    const s = window.getComputedStyle(n);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  if (vis(el)) return true;
  // Ashby-style: visually-hidden input driven by a visible label pill.
  const lab = el.labels && el.labels[0];
  return !!(lab && vis(lab));
}"""

GROUP_CTX_JS = """(el) => {
  // Question text near a control, for grouping options that share no
  // fieldset/name (e.g. Ashby checkbox lists) and for standing-answer
  // matching. A targeted known-question pattern wins at any reasonable
  // size; otherwise the nearest block-sized ancestor. An option's own
  // short wrapper is skipped so every option in one question resolves to
  // the same text. Walks deep: real Ashby markup nests options 5+ levels
  // below their question container, and stops at FORM/BODY so the whole
  // form is never grabbed as "the question".
  const norm = (n) => (n.innerText || '').replace(/\\s+/g, ' ').trim();
  let n = el.parentElement, fallback = '', qtext = '';
  for (let i = 0; i < 10 && n && n.tagName !== 'FORM' && n.tagName !== 'BODY';
       i++, n = n.parentElement) {
    const t = norm(n);
    if (!qtext && /how did you hear/i.test(t) && t.length < 5000) qtext = t;
    if (!fallback && t.length >= 25 && t.length <= 1200) fallback = t;
    if (qtext && fallback) break;
  }
  return qtext || fallback;
}"""

JS_LABEL_CLICK = "(el) => { const t = (el.labels && el.labels[0]) || el; t.click(); }"


# --- inventory ---------------------------------------------------------

def _kind(el) -> str | None:
    """Control kind for the inventory, or None to leave out."""
    tag = el.evaluate("e => e.tagName").lower()
    if tag == "button":
        # Only Yes/No pill toggles are questions; other buttons are chrome.
        text = (el.inner_text() or "").strip().lower()
        return "button" if text in ("yes", "no") else None
    if tag == "textarea":
        return "textarea"
    if tag == "select":
        return "select"
    ctype = (el.get_attribute("type") or "text").lower()
    if ctype in ("hidden", "submit"):
        return None
    if ctype in ("radio", "checkbox", "file"):
        return ctype
    return "text"  # text-like inputs


def _is_required(el, label: str, ctx: str) -> bool:
    if el.get_attribute("required") is not None:
        return True
    if (el.get_attribute("aria-required") or "").lower() == "true":
        return True
    return "*" in (label or "") or "*" in (ctx or "")


def _group_options(page, el, kind: str, ctx: str) -> list[str]:
    if kind == "select":
        return el.evaluate(OPTIONS_JS)
    if kind in ("radio", "checkbox"):
        name = el.get_attribute("name") or ""
        if name:
            return page.evaluate(
                """(n) => Array.from(
                     document.querySelectorAll(`input[type=radio][name="${n}"],`
                       + `input[type=checkbox][name="${n}"]`))
                   .map(r => (r.labels[0]?.innerText || r.value || '').trim())
                   .filter(Boolean)""",
                name,
            )
        if ctx:
            key = ctx[:80]
            opts: list[str] = []
            for b in page.query_selector_all(
                    "input[type=checkbox], input[type=radio]"):
                try:
                    if (b.evaluate(GROUP_CTX_JS) or "")[:80] != key:
                        continue
                    t = (b.evaluate(OPT_TEXT_JS) or "").strip()
                    if t and t not in opts:
                        opts.append(t)
                except Exception:  # noqa: BLE001 - one bad option never kills it
                    continue
            return opts
    if kind == "button":
        return ["Yes", "No"]
    return []


def snapshot_form(page) -> dict:
    """Question inventory of the current page.

    One page = one snapshot; multi-step flows snapshot per step and the
    reason layer decides the next action from what it sees (a tab strip,
    a Next button, a login wall, ...). Returns
    {"questions": [{"question": str, "required": bool, "controls": [...]}],
     "actions": [{"kind": "button"|"link", "label": str, "control_id": str}]}
    in page order. The snapshot deliberately carries no ATS label: the
    reason layer works from what is on the page, not from which site it
    thinks it is on.
    """
    groups: dict[str, dict] = {}
    order: list[str] = []
    controls = page.query_selector_all("input, textarea, select, button")
    for i, el in enumerate(controls):
        try:
            if not el.evaluate(VISIBLE_JS):
                continue
            kind = _kind(el)
            if not kind:
                continue
            label = el.evaluate(LABEL_JS)
            ctx = el.evaluate(GROUP_CTX_JS) or ""
            if not label and kind != "button":
                continue
            control_id = (
                el.get_attribute("name")
                or el.get_attribute("id")
                or f"field_{i}"
            )
            name = el.get_attribute("name") or ""
            if kind in ("radio", "checkbox", "button"):
                key = name or (f"ctx:{ctx[:80]}" if ctx else control_id)
            else:
                key = f"ctx:{ctx[:80]}" if ctx else control_id
            if key not in groups:
                groups[key] = {
                    "question": ctx or label,
                    "required": False,
                    "controls": [],
                }
                order.append(key)
            g = groups[key]
            g["required"] = g["required"] or _is_required(el, label, ctx)
            g["controls"].append({
                "kind": kind,
                "label": label,
                "options": _group_options(page, el, kind, ctx),
                "control_id": control_id,
            })
        except Exception:  # noqa: BLE001 - one bad control never kills it
            continue
    return {
        "questions": [groups[k] for k in order],
        "actions": snapshot_actions(page),
    }


def snapshot_actions(page) -> list[dict]:
    """Visible interactive elements that are not form questions: tabs,
    Next/Back buttons, Apply links, ... The reason layer uses these to
    navigate (click the Application tab, walk a stepped flow) instead of
    relying on per-site navigation code."""
    actions: list[dict] = []
    seen: set[str] = set()
    for el in page.query_selector_all("button, a[href]"):
        try:
            if not el.evaluate(VISIBLE_JS):
                continue
            text = " ".join((el.inner_text() or "").split())
            if not text:
                continue
            tag = el.evaluate("e => e.tagName").lower()
            key = f"{tag}:{text}"
            if key in seen:
                continue
            seen.add(key)
            actions.append({
                "kind": "link" if tag == "a" else "button",
                "label": text,
                "control_id": el.get_attribute("id") or "",
            })
        except Exception:  # noqa: BLE001 - one bad element never kills it
            continue
    return actions
