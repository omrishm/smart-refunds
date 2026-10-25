"""Refund Ops Manager: change the rules without an engineer, and see what a change does before it goes live."""
from __future__ import annotations

import copy
import os

import streamlit as st

from smartrefunds.drafting import draft_rule, insert_rule, overridden_rules, suggest_position
from smartrefunds.preview import preview
from smartrefunds.facts import FACT_LABELS
from smartrefunds.rules import AnyOf, Condition, RuleSet, load_rules, rule_to_dict, validate

# (widget key, rule id, condition field, label)
EDITS = [
    ("edit_cap", "R10", "amount_usd", "Auto-approve cap ($)"),
    ("edit_tenure", "R9", "tenure_months", "Minimum account age (months)"),
    ("edit_prior", "R8", "prior_refunds_12m", "Prior refunds allowed in 12 months"),
    ("edit_window", "R4", "days_since_delivery", "Change-of-mind window (days)"),
    ("edit_photo", "R11", "amount_usd", "Damage photo needed above ($)"),
]
FIELD_LABELS = {
    "amount_usd": "amount ($)", "tenure_months": "account age (months)",
    "prior_refunds_12m": "refunds in last 12 months", "days_since_delivery": "days since delivery",
    "stated_reason": "reason", "payment_method": "payment method", "fraud_flag": "fraud flag",
    "is_valid": "data complete", "duplicate_id": "duplicate request ID", "notes_present": "has notes",
    "facts_available": "notes analysed",
}
STATE_KEYS = ["draft_result", "choice", "position", "rollback_from", "publish_reason", "change_text"] + [e[0] for e in EDITS]


def edit_hint(rule, live: RuleSet) -> str | None:
    """If a drafted rule tests the same thing as an existing rule with an editable value (same field, and that
    rule's other conditions also present), the manager most likely wants to change that value instead."""
    new_conds = [c for c in rule.when if isinstance(c, Condition)]
    for _, rule_id, field, label in EDITS:
        existing = next((r for r in live.rules if r.id == rule_id), None)
        cond = _condition(live, rule_id, field)
        if existing is None or cond is None or not any(c.field == field for c in new_conds):
            continue
        context = [c for c in existing.when if isinstance(c, Condition) and c.field != field]
        if all(any((n.field, n.op, n.value) == (c.field, c.op, c.value) for n in new_conds) for c in context):
            return (f"Related setting: {rule_id} “{existing.title}” ({_cond_text(cond)}). If you meant to change "
                    f"that, edit “{label}” on the left.")
    return None


def _cond_text(c) -> str:
    if isinstance(c, AnyOf):
        return "(" + " or ".join(_cond_text(x) for x in c.conditions) + ")"
    if c.field.startswith("facts."):
        label = FACT_LABELS.get(c.field[6:], c.field[6:]).lower()
        if c.op == "=" and c.value == "yes":
            return f"notes show: {label}"
        field = f"notes: {label}"
    else:
        field = FIELD_LABELS.get(c.field, c.field.replace("_", " "))
    value = "" if c.op in ("is_set", "is_unset") else f" {c.value}"
    return f"{field} {c.op}{value}"


def _then_text(rule) -> str:
    t = rule.then
    if t.kind == "approve":
        return "approve in full"
    lane = "the lane for its reason" if t.lane == "by_reason" else t.lane
    return f"send to {lane}" + (f" with a draft rejection ({t.policy})" if t.kind == "route_with_draft_rejection" else "")


def _condition(rs: RuleSet, rule_id: str, field: str) -> Condition | None:
    rule = next((r for r in rs.rules if r.id == rule_id), None)
    if rule is None:
        return None
    return next((c for c in rule.when if isinstance(c, Condition) and c.field == field), None)


def _clear() -> None:
    for k in STATE_KEYS:
        st.session_state.pop(k, None)


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    return anthropic.Anthropic()


def _base(ctx) -> RuleSet:
    v = st.session_state.get("rollback_from")
    if v is None:
        return copy.deepcopy(ctx.live)
    path = ctx.store.seed_rules if v == 1 else ctx.store.rules_dir / f"v{v}.yaml"
    return load_rules(path)


def render(ctx) -> None:
    msg = st.session_state.pop("flash", None)
    if msg:
        st.success(msg[1])
    left, right = st.columns([1, 1.6])
    base = _base(ctx)
    with left:
        candidate = _rules_column(ctx, base)
    with right:
        _change_column(ctx, candidate)


def _rules_column(ctx, base: RuleSet) -> RuleSet:
    if st.session_state.get("rollback_from") is not None:
        st.warning(f"Previewing a roll-back to v{st.session_state['rollback_from']}.")
    st.subheader(f"Live rules · v{ctx.live.version}")
    st.caption("Checked top to bottom; the first rule that matches decides. Locked safety rules can't be changed here.")
    candidate = copy.deepcopy(base)
    st.markdown("**Values you can change directly**")
    for key, rule_id, field, label in EDITS:
        cond = _condition(candidate, rule_id, field)
        if cond is not None:
            cond.value = st.number_input(label, min_value=0, value=int(cond.value), step=1, key=key)
    for r in ctx.live.rules:
        st.markdown(f"`{r.id}` {r.title}" + (" · _locked_" if r.locked else ""))

    st.markdown("**Version history**")
    for v, at, reason in reversed(ctx.store.versions()):
        cols = st.columns([4, 1])
        cols[0].caption(f"v{v} · {at} · {reason}" + (" · live" if v == ctx.live.version else ""))
        if v != ctx.live.version and cols[1].button("Roll back", key=f"rollback_{v}"):
            _clear()
            st.session_state["rollback_from"] = v
            st.rerun()
    return candidate


def _change_column(ctx, candidate: RuleSet) -> None:
    st.subheader("Change a rule")
    if "prefill_change" in st.session_state:
        st.session_state["change_text"] = st.session_state.pop("prefill_change")
    text = st.text_area("Describe the change in your own words", key="change_text",
                        placeholder="From now on, anything over $300 comes to us.")
    if st.button("Draft the rule", key="draft") and text.strip():
        st.session_state["draft_result"] = draft_rule(text, ctx.live, client=_client())
        st.session_state.pop("position", None)
        st.session_state.pop("choice", None)

    drafted = None
    d = st.session_state.get("draft_result")
    if d is not None:
        if d.error:
            st.warning(d.error)
        elif d.choices:
            with st.container(border=True):
                st.caption("Proposed rule · not live until published")
                labels = [c.label for c in d.choices]
                if d.question:
                    st.markdown(f"**{d.question}**")
                picked = st.radio("Meaning", labels, key="choice", label_visibility="collapsed") if len(labels) > 1 \
                    else labels[0]
                drafted = d.choices[labels.index(picked)].rule
                default = suggest_position(candidate, drafted)
                pos = st.session_state.setdefault("position", default)
                first_free = max((i for i, r in enumerate(candidate.rules) if r.locked), default=-1) + 1
                c1, c2, c3 = st.columns([3, 1, 1])
                if c2.button("Move up", key="move_up") and pos > first_free:
                    st.session_state["position"] = pos = pos - 1
                if c3.button("Move down", key="move_down") and pos < len(candidate.rules):
                    st.session_state["position"] = pos = pos + 1
                before = candidate.rules[pos - 1].id if pos > 0 else "the top"
                c1.markdown(f"**Position:** after `{before}` (rule {pos + 1} of {len(candidate.rules) + 1})")
                st.markdown(f"**When:** {' and '.join(_cond_text(c) for c in drafted.when)}")
                st.markdown(f"**Then:** {_then_text(drafted)}")
                candidate = insert_rule(candidate, drafted, pos)

    changed = [rule_to_dict(r) for r in candidate.rules] != [rule_to_dict(r) for r in ctx.live.rules]
    if not changed and st.session_state.get("rollback_from") is None:
        st.caption("Change a value on the left or describe a change above to see what it would do.")
        _publish_controls(ctx, candidate, errors=["nothing changed"], show_errors=False)
        return

    errors = validate(candidate, base=ctx.live)
    for e in errors:
        st.error(e)
    _preview(ctx, candidate, drafted)
    _publish_controls(ctx, candidate, errors)


def _preview(ctx, candidate: RuleSet, drafted) -> None:
    st.markdown("**What it would do** · replayed on history and today's queue")
    res = preview(candidate, ctx.live, ctx.history, ctx.pending, ctx.preview_provider)
    h = res.history
    gained = sum(1 for c in h.changed if c.after == "auto_approved")
    lost = sum(1 for c in h.changed if c.before == "auto_approved")
    m = st.columns(4)
    m[0].metric("Auto-approved in history", f"{h.auto_count - gained + lost} → {h.auto_count}",
                delta=gained - lost or None)
    m[1].metric("Money auto-approved", f"${h.auto_amount:,.0f}")
    m[2].metric("Reviewer minutes saved", f"{h.minutes_saved:g}")
    m[3].metric("Bad outcomes let through", len(h.bad_let_through))
    for w in res.warnings:
        (st.info if w.startswith("No effect") else st.warning)(w)
    hint = edit_hint(drafted, ctx.live) if drafted is not None else None
    if hint:
        st.info(hint)
    if drafted is not None:
        rows = [(r, ctx.preview_provider.get(r)) for r in ctx.history + ctx.pending]
        over = overridden_rules(ctx.live, candidate, rows)
        if over:
            st.caption("Overrides for the matching cases: " + ", ".join(over) + " · safety rules S1–S4 still apply")
    table = [{"Where": "today's queue", "Request": c.request_id, "$": c.amount, "Now": c.before, "After change": c.after}
             for c in res.pending.changed]
    table += [{"Where": "history", "Request": c.request_id, "$": c.amount, "Now": c.before, "After change": c.after}
              for c in h.changed]
    if table:
        st.dataframe(table, hide_index=True, use_container_width=True)


def _publish_controls(ctx, candidate: RuleSet, errors: list[str], show_errors: bool = True) -> None:
    reason = st.text_input("Why are you making this change? (required to publish)", key="publish_reason")
    c = st.columns(3)
    if c[0].button(f"Publish as v{ctx.live.version + 1}", key="publish", type="primary",
                   disabled=bool(errors) or not reason.strip()):
        version = ctx.store.publish(candidate, reason)
        _clear()
        st.session_state["flash"] = ("ok", f"Published v{version}. It's live now.")
        st.rerun()
    if c[1].button("Save draft", key="save_draft"):
        st.toast("Kept for this session (drafts aren't stored permanently in this demo).")
    if c[2].button("Discard", key="discard"):
        _clear()
        st.rerun()
